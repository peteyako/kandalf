#!/usr/bin/env python3
"""Local drag-and-drop board over the markdown files in a plans store (see store.py).

Each `## ` heading is a column and each top-level `- ` item is a tile.
The page parses and writes the markdown itself; this server lists, reads
and writes files, refusing a write if the file changed on disk since the
page loaded it (another session or an editor touched it).

Plan docs live in active/ beside the board; finished ones in archive/.
Every write is queued for a commit (gitlog.record): once the board has been
quiet for 5 minutes, everything queued is committed as one commit and pushed.
Dropping a tile on the page's Archive column calls /api/archive: the
plan docs the tile links move from active/ into archive/ (git mv inside a repo),
links to them are rewritten, and the tile leaves the board for the top
of archive/archived.md.

Clear archives a whole Done column at once (/api/clear), and the page can hand the reply
back to undo it (/api/unclear).

Tiles that say "Moves with KEY-NN" are linked: the page moves them together, and asks
this server for the ones on the other board (/api/move takes them along, /api/pull
fetches them).

A tile that says "Part of KEY-NN" is a sub-story of that tile, its master. The page gets an
index of every keyed tile (tile_index): it folds a master's sub-stories into its tile, shows
how far along they are, moves them with it, and makes the keys a tile mentions links.

It answers only its own page (refused): a request must name the board's own address as its
Host, and a change (PUT, POST) sent from a page must come from that address too.

    kandalf serve                        # the store you're in (or linked at docs/plans), on its port,
                                         # opened in the browser (not with --no-open or BOARD_NO_OPEN=1)
    kandalf serve --dir ~/.claude/plans --port 7789
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.dont_write_bytecode = True  # no __pycache__ in the repo from importing pln
from . import pln  # noqa: E402  tile keys
from . import gitlog  # noqa: E402  one commit per board change
from . import agent, pickup  # noqa: E402  a tile that lands in In flight goes to Claude Code
from . import store as stores  # noqa: E402  which store, and its settings

HERE = Path(__file__).resolve().parent
NAME_RE = re.compile(r"^((archive|active)/)?[A-Za-z0-9][A-Za-z0-9._-]*\.md$")
FONT_RE = re.compile(r"^[a-z0-9-]+\.woff2$")  # a bundled font under kandalf/fonts
DOC_RE = re.compile(r"^(active/)?[A-Za-z0-9][A-Za-z0-9._-]*\.md$")  # a plan doc a board tile can archive
ARCHIVE_LOG = "archive/archived.md"
ARCHIVE_HEAD = """# Archived plans

Tiles dragged to the Archive column on the plans board, newest first. Their plan docs are in this folder.

## Archived
"""

def mtime(p: Path) -> str:
    return str(p.stat().st_mtime_ns)


def write_atomic(p: Path, text: str):
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".board-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, p)


def archive_docs(store, names):
    """Move each plan doc (active/x.md, or x.md beside the board) into
    archive/ and point every reference in the store's repo at its new path.
    Returns the names actually moved, as given, and every file it changed."""
    plans, root = store.dir, store.root
    (plans / "archive").mkdir(exist_ok=True)
    todo = [n for n in names if DOC_RE.match(n) and (plans / n).is_file()]
    # Check every destination first, so a clash moves nothing rather than half the docs.
    for name in todo:
        if (plans / "archive" / Path(name).name).exists():
            raise FileExistsError(f"archive/{Path(name).name} already exists")
    moved, touched = [], []
    for name in todo:
        src, dst = plans / name, plans / "archive" / Path(name).name
        tracked = root and subprocess.run(["git", "-C", str(root), "ls-files", "--error-unmatch", str(src)],
                                          capture_output=True).returncode == 0
        if tracked:
            subprocess.run(["git", "-C", str(root), "mv", str(src), str(dst)], check=True, capture_output=True)
        else:
            os.rename(src, dst)
        moved.append(name)
        touched += [src, dst]
        if root:
            # "plans/active/x.md" catches docs/plans/active/x.md and ../plans/active/x.md alike.
            old, new = f"{store.link}/{name}", f"{store.link}/archive/{dst.name}"
            hits = subprocess.run(["git", "-C", str(root), "grep", "-lIF", old, "--", str(plans)],
                                  capture_output=True, text=True)
            for rel in hits.stdout.splitlines():  # one path per line; paths may hold spaces
                f = root / rel
                text = f.read_text(encoding="utf-8")
                f.write_text(text.replace(old, new), encoding="utf-8")
                touched.append(f)
    return moved, touched


def relink(text: str, names, folder: str) -> str:
    """Point a board's links (and repo paths) at the archived copies."""
    for name in names:
        base = Path(name).name
        text = text.replace(f"]({name})", f"](archive/{base})")
        text = text.replace(f"{folder}/{name}", f"{folder}/archive/{base}")
    return text


LINK_RE = re.compile(r"\]\(([^)\s]+)\)")


def rebase_into_archive(tile: str, board_name: str) -> str:
    """Rewrite a tile's relative links so they work from inside archive/."""
    def fix(m):
        href = m.group(1)
        if href.startswith("archive/"):
            return f"]({href[len('archive/'):]})"
        if DOC_RE.match(href) or href == board_name:
            return f"](../{href})"
        return m.group(0)
    return LINK_RE.sub(fix, tile)


def unarchive_docs(store, names):
    """archive_docs, backwards: move each plan doc from archive/ back to where it was and
    point the references in the store's repo back at it. Returns every file it changed."""
    plans, root = store.dir, store.root
    pairs = [(plans / "archive" / Path(n).name, plans / n) for n in names]
    for src, dst in pairs:  # check everything first, so a clash moves nothing
        if not src.is_file():
            raise LookupError(f"archive/{src.name} is no longer there")
        if dst.exists():
            raise FileExistsError(f"{dst.relative_to(plans)} already exists")
    touched = []
    for (src, dst), name in zip(pairs, names):
        dst.parent.mkdir(exist_ok=True)
        tracked = root and subprocess.run(["git", "-C", str(root), "ls-files", "--error-unmatch", str(src)],
                                          capture_output=True).returncode == 0
        if tracked:
            subprocess.run(["git", "-C", str(root), "mv", str(src), str(dst)], check=True, capture_output=True)
        else:
            os.rename(src, dst)
        touched += [src, dst]
        if root:
            old, new = f"{store.link}/archive/{src.name}", f"{store.link}/{name}"
            hits = subprocess.run(["git", "-C", str(root), "grep", "-lIF", old, "--", str(plans)],
                                  capture_output=True, text=True)
            for rel in hits.stdout.splitlines():
                f = root / rel
                f.write_text(f.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
                touched.append(f)
    return touched


def unlink_archived(text: str, names, folder: str) -> str:
    """relink, backwards: point a board's links (and repo paths) at the docs' old places."""
    for name in names:
        base = Path(name).name
        text = text.replace(f"](archive/{base})", f"]({name})")
        text = text.replace(f"{folder}/archive/{base}", f"{folder}/{name}")
    return text


TILE_DOC_RE = re.compile(r"\]\(((?:active/)?[\w.-]+\.md)\)")  # a plan doc a tile links (the page's archiveCard)


def clear_column(store, board: Path, text: str, column: str, keep=()) -> tuple:
    """Archive every tile in a column of this board text at once, but the ones with a key in
    keep. Moves their plan docs and files them in the archive log, as archiving each would.
    Returns (the board text to write, the record undo_clear needs, the files changed)."""
    if not any(line == f"## {column}" for line in text.split("\n")):
        raise KeyError(f'no "## {column}" column')
    tiles = [t for t in board_tiles(store, text) if t["column"] == column and t["key"] not in keep]
    if not tiles:
        raise ValueError(f"nothing to clear in {column}")
    names = {m for t in tiles for m in TILE_DOC_RE.findall(t["text"])} - {board.name}
    docs, touched = archive_docs(store, sorted(names))
    log = store.dir / ARCHIVE_LOG
    for t in reversed(tiles):  # each goes on top, so the last one first keeps the column's order
        file_in_archive_log(log, rebase_into_archive(relink(t["text"], docs, store.link), board.name))
    record = {"board": board.name, "column": column, "count": len(tiles), "docs": docs,
              "tiles": [{"key": t["key"], "text": t["text"]} for t in tiles]}
    return relink(without_tiles(text, tiles), docs, store.link), record, [log, *touched]


def undo_clear(store, board: Path, text: str, record: dict) -> tuple:
    """Take a clear back, on the board as it reads now: its tiles leave the archive log and
    return to the top of their column as they were, and their plan docs move back.
    Returns (the board text to write, the files changed). Raises, changing nothing, if a
    cleared tile or doc is no longer where the clear left it."""
    log = store.dir / ARCHIVE_LOG
    logged = log.read_text(encoding="utf-8") if log.is_file() else ""
    filed, taken = board_tiles(store, logged), []
    def is_it(entry, tile, first_line):
        """A keyed tile is found by its key; an unkeyed one by its first line as it was filed."""
        if tile["key"]:
            return entry["key"] == tile["key"]
        return entry["text"].split("\n", 1)[0] == first_line

    for t in record["tiles"]:
        first = rebase_into_archive(relink(t["text"], record["docs"], store.link), board.name).split("\n", 1)[0]
        match = next((f for f in filed if f not in taken and is_it(f, t, first)), None)
        if match is None:
            raise LookupError(f"{t['key'] or 'a cleared tile'} is no longer in the archive")
        taken.append(match)
    new = unlink_archived(text, record["docs"], store.link)
    for t in reversed(record["tiles"]):
        new = insert_at_top(new, record["column"], t["text"])  # KeyError if the column is gone
    touched = unarchive_docs(store, record["docs"])
    rest = without_tiles(logged, taken)
    for name in record["docs"]:  # other archived tiles that link a doc that moved back
        rest = rest.replace(f"]({Path(name).name})", f"](../{name})")
    write_atomic(log, rest)
    return new, [log, *touched]


def file_in_archive_log(log: Path, tile: str):
    """Put a tile at the top of the archive log's "## Archived" column, with today's date."""
    text = log.read_text(encoding="utf-8") if log.is_file() else ARCHIVE_HEAD
    marker = "## Archived\n"
    at = text.index(marker) + len(marker)
    entry = f"{tile}\n  Archived {datetime.date.today().isoformat()}.\n"
    write_atomic(log, text[:at] + "\n" + entry + text[at:].lstrip("\n"))


# Tiles move between the two boards by dropping them on the other board's columns, shown
# beside Archive: a backlog.md tile is promoted to the top of plans.md's To do, and a
# plans.md tile goes back to the top of any backlog.md column. {from: (to, columns or None for all)}
MOVES = {"backlog.md": ("plans.md", ["To do"]), "plans.md": ("backlog.md", None)}


def move_targets(plans: Path, name: str) -> dict:
    """{"file", "columns"}: where a tile on this board can be sent, or {} for nowhere."""
    if name not in MOVES or not (plans / MOVES[name][0]).is_file():
        return {}
    to, only = MOVES[name]
    columns = [line[3:].strip() for line in (plans / to).read_text(encoding="utf-8").split("\n") if line.startswith("## ")]
    return {"file": to, "columns": [c for c in columns if only is None or c in only]}


def keyed_tile(store, tile: str, next_key: str) -> tuple:
    """(tile, key): the tile as a plans.md story, with a "- [ ] " checkbox and its own key,
    or next_key when it has none."""
    first, nl, rest = tile.partition("\n")
    m = re.match(r"^- (?:\[[ xX]\] )?(.*)$", first)
    head = m.group(1) if m else first
    own = store.keyed_re.match(head)
    key = own.group(1) if own else next_key
    if not own:
        head = f"{key} {head}"
    return f"- [ ] {head}{nl}{rest}", key


def insert_at_top(text: str, column: str, tile: str) -> str:
    """Put a tile first in a board column, keeping the board's own layout (heading, blank,
    intro, blank, cards back to back), so the file still round-trips through board.html."""
    lines = text.split("\n")
    try:
        at = lines.index(f"## {column}")
    except ValueError:
        raise KeyError(f'no "## {column}" column')
    end = next((i for i in range(at + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    first = next((i for i in range(at + 1, end) if re.match(r"^[-*] ", lines[i])), None)
    new = tile.split("\n")
    if first is not None:
        lines[first:first] = new
    else:  # no cards yet: after the heading (and any intro), with a blank line either side
        last = max((i for i in range(at, end) if lines[i].strip()), default=at)
        # A blank line follows the tile unless the column ends a file that has no final newline.
        keep_trailing = end < len(lines) or end > last + 1
        lines[last + 1:end] = [""] + new + ([""] if keep_trailing else [])
    return "\n".join(lines)


# Linked tiles: a tile whose text says "Moves with PLN-74" (or "PLN-74, PLN-75 and PLN-76")
# moves with those tiles. A link counts in both directions and chains, so only one tile of a
# group has to say it. board.html reads the same wording (LINK_RE there).
def link_re(store) -> re.Pattern:
    k = re.escape(store.key)
    return re.compile(rf"\bmoves with\s+({k}-\d+(?:(?:\s*,\s*(?:and\s+)?|\s+and\s+){k}-\d+)*)", re.I)


def board_tiles(store, text: str) -> list:
    """Every tile of a board file: {"key" (or None), "column", "start", "end", "text"},
    start and end being its line range, without the blank lines after it."""
    lines, out, column, i = text.split("\n"), [], None, 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("## "):
            column = line[3:].strip()
        elif column is not None and re.match(r"^[-*] ", line):
            end = i + 1
            while end < len(lines) and (not lines[end].strip() or lines[end][0] in " \t"):
                end += 1
            while not lines[end - 1].strip():
                end -= 1
            m = store.tile_head_re.match(line)
            out.append({"key": m.group(1) if m else None, "column": column, "start": i, "end": end,
                        "text": "\n".join(lines[i:end])})
            i = end
            continue
        i += 1
    return out


def linked(store, *texts) -> dict:
    """{"KEY-NN": [the keys it moves with]} over the tiles of these board files, for every
    tile in a group of two or more."""
    pat, edges = link_re(store), {}
    for text in texts:
        for t in board_tiles(store, text):
            if not t["key"]:
                continue
            for m in pat.finditer(t["text"]):
                for other in store.key_re.finditer(m.group(1)):
                    if other.group(0) != t["key"]:
                        edges.setdefault(t["key"], set()).add(other.group(0))
                        edges.setdefault(other.group(0), set()).add(t["key"])
    out = {}
    for key in edges:
        seen, todo = {key}, [key]
        while todo:
            for other in edges[todo.pop()]:
                if other not in seen:
                    seen.add(other)
                    todo.append(other)
        out[key] = sorted(seen - {key})
    return out


def take_tiles(store, text: str, keys) -> tuple:
    """(the board without the tiles that have these keys, those tiles' markdown in board
    order). The board keeps its layout, so it still round-trips through board.html."""
    tiles = [t for t in board_tiles(store, text) if t["key"] in keys]
    return without_tiles(text, tiles), [t["text"] for t in tiles]


def without_tiles(text: str, tiles) -> str:
    """The board without these tiles (board_tiles entries of this text), layout kept."""
    lines = text.split("\n")
    for t in sorted(tiles, key=lambda t: t["start"], reverse=True):
        del lines[t["start"]:t["end"]]
        at = t["start"]  # the column's last tile leaves two blank lines behind: keep one
        if 0 < at < len(lines) and not lines[at - 1].strip() and not lines[at].strip():
            del lines[at]
    return "\n".join(lines)


def insert_after(store, text: str, key: str, tiles) -> str:
    """Put tiles straight after the tile with this key, in order."""
    at = next((t for t in board_tiles(store, text) if t["key"] == key), None)
    if at is None:
        raise KeyError(f"no {key} tile")
    lines = text.split("\n")
    lines[at["end"]:at["end"]] = [line for tile in tiles for line in tile.split("\n")]
    return "\n".join(lines)


# Master tiles: a tile whose text says "Part of PLN-70" is a sub-story of PLN-70. Only the
# first one counts. board.html reads the same wording (PART_RE there) and the same Done
# columns (isDoneCol there).
def part_re(store) -> re.Pattern:
    return re.compile(rf"\bpart of\s+({re.escape(store.key)}-\d+)\b", re.I)


DONE_COL_RE = re.compile(r"^(?!.*\bnot\b).*\b(?:done|shipped|completed?|finished|closed|merged|released|deployed|delivered|resolved)\b", re.I)  # same in board.html: Done, Shipped, Merged...; never "Not done"
TILE_HEAD_RE = re.compile(r"^(?:P[1-4] )?(?:~\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)?[hdw] )?(.*)$")


def tile_title(store, tile: str) -> str:
    """A tile's title, plain: its first line without the checkbox, key, priority, estimate
    and bold (the page's splitCard)."""
    line = re.sub(r"^[-*] (?:\[[ xX]\] )?", "", tile.split("\n", 1)[0])
    rest = TILE_HEAD_RE.match(store.keyed_re.sub("", line)).group(1)
    bold = re.match(r"^\*\*(.+?)\*\*", rest)
    title = re.sub(r"[.:]$", "", bold.group(1)) if bold else rest
    return re.sub(r"\*\*|`", "", title)


def tile_index(store, files: dict) -> dict:
    """{"KEY-NN": {"title", "file", "column", "done", "parent", "mentions"}} for every keyed
    tile of these board files ({name: text}; a key on two of them is the first one's tile).
    done: in a Done column, or in the archive log. parent: the master it says it is part of,
    or None. mentions: the other keys its text names, sorted."""
    part, out = part_re(store), {}
    for name, text in files.items():
        for t in board_tiles(store, text):
            key = t["key"]
            if not key or key in out:
                continue
            others = {m.group(0) for m in store.key_re.finditer(t["text"])} - {key}
            parent = next((m.group(1) for m in part.finditer(t["text"]) if m.group(1) != key), None)
            out[key] = {"title": tile_title(store, t["text"]), "file": name, "column": t["column"],
                        "done": name == ARCHIVE_LOG or bool(DONE_COL_RE.search(t["column"])),
                        "parent": parent, "mentions": sorted(others, key=store.num)}
    return out


def in_flight_tiles(store, text: str) -> list:
    """The keyed tiles in the plans board's In flight column (pickup.is_pickup_column), for
    pickup.py: [{"key", "title", "text", "parent"}]."""
    part, out = part_re(store), []
    for t in board_tiles(store, text):
        if t["key"] and pickup.is_pickup_column(store, t["column"]):
            parent = next((m.group(1) for m in part.finditer(t["text"]) if m.group(1) != t["key"]), None)
            out.append({"key": t["key"], "title": tile_title(store, t["text"]), "text": t["text"], "parent": parent})
    return out


PICKUP_BOARD = "plans.md"
SESSION_LINE_RE = re.compile(r"^\s+(?:Claude|Copilot) session `[0-9a-f]+`: ")  # board.html reads it too (SESSION_RE there)


def with_session(store, text: str, job: dict) -> str:
    """The board with the job's session written into its tile: one indented line naming the
    session, its branch and worktree, after the description and before the first sub-task.
    It replaces the line an earlier session left."""
    tile = next((t for t in board_tiles(store, text) if t["key"] == job["key"]), None)
    if tile is None:
        return text
    lines = text.split("\n")
    body = [line for line in lines[tile["start"] + 1:tile["end"]] if not SESSION_LINE_RE.match(line)]
    at = next((i for i, line in enumerate(body) if re.match(r"^  [-*] ", line)), len(body))
    cuts = job.get("repos") or []
    main = store.main_repo
    if len(cuts) > 1 or (cuts and main and cuts[0]["name"] != main.name):
        # a repo board.json no longer has is named by its worktree's folder
        named = ", ".join(f"{c['name']} `{pickup.worktree_label(store, c['worktree'], store.repo(c['name'])) if store.repo(c['name']) else Path(c['worktree']).name}`" for c in cuts)
        where = f" Branch `{job['branch']}`, worktrees {named}."
    else:
        where = f" Branch `{job['branch']}`, worktree `{pickup.worktree_label(store, job['worktree'])}`." if job.get("worktree") else ""
    where += f" Model `{job['model']}`." if job.get("model") else ""
    day = time.strftime("%Y-%m-%d", time.localtime(job.get("started") or time.time()))
    body.insert(at, f"  {agent.short()} session `{job['id']}`: `{agent.attach(job['id'])}`.{where} Started {day}.")
    lines[tile["start"] + 1:tile["end"]] = body
    return "\n".join(lines)


STATUS_LINE_RE = re.compile(r"^\s+\*\*Status \d{4}-\d\d-\d\d \d\d:\d\d:\*\* ")  # board.html reads it too (STATUS_RE there)


def with_status(store, text: str, key: str, line: str) -> str:
    """The board with a tile's status (pickup.status, "Status <date> <time>: ...") written in
    right under its session line (as its last line, if it has none), its stamp in bold,
    replacing the status it had."""
    tile = next((t for t in board_tiles(store, text) if t["key"] == key), None)
    if tile is None:
        return text
    lines = text.split("\n")
    body = [x for x in lines[tile["start"] + 1:tile["end"]] if not STATUS_LINE_RE.match(x)]
    session = next((i for i, x in enumerate(body) if SESSION_LINE_RE.match(x)), None)
    body.insert(len(body) if session is None else session + 1, "  " + re.sub(r"^(Status \S+ \S+:)", r"**\1**", line))
    lines[tile["start"] + 1:tile["end"]] = body
    return "\n".join(lines)


def note_session(store, job: dict):
    """Write a started job's session id into its tile on the plans board, and queue the commit."""
    board = store.dir / PICKUP_BOARD
    old = board.read_text(encoding="utf-8")
    new = with_session(store, old, job)
    if new != old:
        write_atomic(board, new)
        gitlog.record(store, [board], f"board: {job['key']} picked up by {agent.short()} session {job['id']}")
PICKUP_RECHECK = 20  # seconds between looks at the sessions while a job waits or runs


def report_in_flight(store):
    """On the console, once at startup: what is in flight, and the Claude session on each."""
    board = store.dir / PICKUP_BOARD
    if not board.is_file():
        return
    try:
        for line in pickup.startup_report(store, in_flight_tiles(store, board.read_text(encoding="utf-8"))):
            pickup.log(line)
    except Exception as e:  # the listing is a courtesy: never stop the server for it
        pickup.log(f"in flight could not be listed: {e}")


def watch_pickups(store, every: float = 2.0):
    """Forever: look at plans.md when it changes (and now and then while a job waits or runs),
    and hand what landed in In flight to pickup.tick."""
    board, seen, last, told, before = store.dir / PICKUP_BOARD, None, 0.0, set(), None
    report_in_flight(store)
    try:
        if board.is_file():
            for key in pickup.adopt(store, in_flight_tiles(store, board.read_text(encoding="utf-8"))):
                pickup.log(f"{key} is in flight with no session: picking it up")
    except Exception as e:  # a courtesy too: the watch below still runs
        pickup.log(f"in flight tiles could not be adopted: {e}")
    while True:
        try:
            at = board.stat().st_mtime_ns if board.is_file() else None
            # A file still being written is left for the next look: half a board reads as
            # tiles that left In flight, and then as the same tiles arriving.
            settled, before = at == before, at
            busy = any(j["state"] == "waiting" or j["state"] in pickup.TRACKED for j in pickup.load(store)["jobs"])
            if at is not None and settled and (at != seen or (busy and time.time() - last > PICKUP_RECHECK)):
                seen, last = at, time.time()
                pickup.tick(store, in_flight_tiles(store, board.read_text(encoding="utf-8")), on_start=note_session)
                tidy_stragglers(store, told)
        except Exception as e:  # a bad look must not end the watch
            pickup.log(f"the look at {PICKUP_BOARD} failed: {e}")
        time.sleep(every)


# A linked tile still on the backlog follows its partner on the plans board: {board: where from}.
# Never the other way, which would unschedule a tile because its partner changed kind.
PULLS = {"plans.md": "backlog.md"}

# The boards whose tiles can be each other's masters, sub-stories and mentions.
FAMILY = ["plans.md", "backlog.md", ARCHIVE_LOG]


# Optional, in the store: {"first": {"KEY-NN": unix seconds}} for tiles filed before the
# store's own history began (myapp-plans: tiles filed while the board lived in the app).
FIRST_SEED = "first-committed.json"
_first_cache = {}


# The app repo whose feature branches the Done tiles name: the store's "app" and "trunk"
# (store.py); PLANS_APP_REPO and PLANS_APP_TRUNK override them.
BRANCH_TOKEN = re.compile(r"`([A-Za-z0-9._-]+/[A-Za-z0-9._/-]+)`")


def _app_git(store, *args, repo=None) -> subprocess.CompletedProcess:
    """git in a repo of the store (default the main one)."""
    return subprocess.run(["git", "-C", str(repo.path if repo else store.app), *args], capture_output=True, text=True, timeout=10)


def _branch_merged(store, branch: str, repo=None) -> bool | None:
    """True when branch is in the trunk, False when it isn't, None when it isn't a
    local branch of the repo (the main one by default; a path like docs/plans, or a branch deleted after
    its merge). Merged means an ancestor of the trunk, or, for a squash or a
    rebase, merging it would change nothing: git merge-tree's result is the
    trunk's own tree. (git cherry can't see a squash: one commit stands in for
    many, so no patch id matches.)"""
    trunk = repo.trunk if repo else store.trunk
    if _app_git(store, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", repo=repo).returncode != 0:
        return None
    if _app_git(store, "merge-base", "--is-ancestor", branch, trunk, repo=repo).returncode == 0:
        return True
    merged = _app_git(store, "merge-tree", "--write-tree", trunk, branch, repo=repo)
    tree = _app_git(store, "rev-parse", f"{trunk}^{{tree}}", repo=repo)
    if merged.returncode == 0 and merged.stdout.split()[:1] == tree.stdout.split()[:1]:
        return True
    # Once the trunk changes the same files after a squash, merge-tree no
    # longer comes out equal, so fall back to the squash or merge commit
    # naming the branch ("Squash of <branch>", "Merge branch '<branch>'").
    for marker in (f"Squash of {branch}", f"Merge branch '{branch}'"):
        found = _app_git(store, "log", trunk, "-1", "--format=%h", "--fixed-strings", f"--grep={marker}", repo=repo)
        if found.returncode == 0 and found.stdout.strip():
            return True
    return False


def tidy_archived(store, tiles, told=None) -> list:
    """Clear away the Claude session, worktree and feature branch of each archived tile
    (pickup.tidy), saying on the console what went and what was kept. Returns those lines,
    each with its key. A line already in told isn't said again; the new ones are added to it."""
    said = []
    for tile in tiles:
        found = store.key_re.search(tile.split("\n", 1)[0])
        key = found.group(0) if found else None
        if key:
            pickup.archived(store, key)
        try:
            lines = pickup.tidy(store, tile, merged=lambda repo, b: _branch_merged(store, b, repo),
                                job=pickup.job_for(store, key) if key else None)
        except (OSError, subprocess.SubprocessError) as e:
            lines = [f"not tidied: {e}"]
        said += [f"{key or 'a tile'} archived: {line}" for line in lines]
    for line in said:
        if told is None or line not in told:
            pickup.log(line)
    if told is not None:
        told.update(said)
    return said


def tidy_stragglers(store, told: set):
    """Tidy the stories that reached the archive without the server archiving them (a session
    or an editor moved the tile): each picked-up story whose tile is now in the archive log
    and on no board. What is kept is retried on the next look, and said once (told remembers
    what was said and which stories are finished with)."""
    log = store.dir / ARCHIVE_LOG
    keys = {j["key"] for j in pickup.load(store)["jobs"] if j.get("branch")}
    if not keys or not log.is_file():
        return
    live = {t["key"] for name in ("plans.md", "backlog.md") if (store.dir / name).is_file()
            for t in board_tiles(store, (store.dir / name).read_text(encoding="utf-8"))}
    for t in board_tiles(store, log.read_text(encoding="utf-8")):
        key = t["key"]
        if key not in keys or key in live or ("tidied", key) in told:
            continue
        said = tidy_archived(store, [t["text"]], told)
        if not any(": kept " in line or ": not tidied" in line for line in said):
            told.add(("tidied", key))  # nothing of it is left: don't look again


def unmerged_done(store, text: str) -> dict:
    """{"KEY-NN": [branch, ...]} for each tile in the Done column that names a feature
    branch not yet merged into its repo's trunk: one in backticks with a slash in it
    (`feat/x`), or the branch on its Claude session line. A branch is looked for in the repos
    the tile names (its session line's, else its Repos line's, else the main one), and counts
    as unmerged when it is in any of them. The board outlines those tiles red. {} when the
    main repo isn't there."""
    if not store.app or not (store.app / ".git").exists():
        return {}
    tiles, key, in_done = {}, None, False  # each Done tile's lines: a Repos line can follow its branch
    for line in text.splitlines():
        if line.startswith("## "):
            in_done, key = bool(DONE_COL_RE.search(line[3:].strip())), None
            continue
        if not in_done:
            continue
        m = re.match(rf"^- \[[ xX]\] ({re.escape(store.key)}-\d+)\b", line)
        if m:
            key = m.group(1)
        elif not line.startswith(" "):
            key = None
        if key:
            tiles.setdefault(key, []).append(line)
    out = {}
    for key, lines in tiles.items():
        tile = "\n".join(lines)
        found = pickup.tile_cuts(store, tile)
        if found:
            repos = [repo for _, repo, _ in found[1] if repo]
        else:
            names = pickup.repo_names(tile)
            repos = [r for r in map(store.repo, names) if r] if names is not None else [store.main_repo]
        for line in lines:
            noted = pickup.TILE_BRANCH_RE.search(line) or pickup.TILE_REPOS_RE.search(line)  # the branch pickup cut, whatever its name
            for b in BRANCH_TOKEN.findall(line) + ([noted.group(1)] if noted else []):
                if any(_branch_merged(store, b, repo) is False for repo in repos) and b not in out.get(key, []):
                    out.setdefault(key, []).append(b)
    return out


def first_committed(store, board: Path) -> dict:
    """{"KEY-NN": Unix seconds} for each tile key: the author time of the first commit
    that added a line for it to this board. Author time, because a rebase rewrites the
    commit time. Cached per HEAD, so it's one git log per new commit."""
    root = store.root
    if not root:
        return {}
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if (board, head) in _first_cache:
        return _first_cache[(board, head)]
    log = subprocess.run(["git", "-C", str(root), "log", "--reverse", "--format=@@C %at", "-p",
                          "--unified=0", "--", str(board)], capture_output=True, text=True).stdout
    added_re = re.compile(rf"^\+- (?:\[[ xX]\] )?({re.escape(store.key)}-\d+) ")
    first, t = {}, None
    for line in log.splitlines():
        if line.startswith("@@C "):
            t = int(line[4:])
        elif (m := added_re.match(line)):
            first.setdefault(m.group(1), t)
    seed = store.dir / FIRST_SEED
    if board == store.dir / "plans.md" and seed.is_file():
        for key, at in json.loads(seed.read_text(encoding="utf-8"))["first"].items():
            first[key] = min(at, first.get(key, at))
    _first_cache[(board, head)] = first
    return first


def refused(method: str, host, origin, port: int):
    """Why the server won't answer a request, or None when it will. Host must be the board's
    own address, 127.0.0.1:<port> or localhost:<port>, so a page of another site can't reach
    it under a name of its own; and a change (anything but GET and HEAD) that carries an
    Origin must come from that address, so a page of another site can't make one. A request
    with no Origin (curl, a script) isn't from a page, and is answered."""
    ours = {f"127.0.0.1:{port}", f"localhost:{port}"}
    if (host or "").lower() not in ours:
        return f"this board answers only to {' or '.join(sorted(ours))}"
    if method not in ("GET", "HEAD") and origin is not None and origin.lower() not in {f"http://{h}" for h in ours}:
        return "this board takes changes only from its own page"
    return None


class Handler(BaseHTTPRequestHandler):
    store: stores.Store

    @property
    def plans(self) -> Path:
        return self.store.dir

    def parse_request(self):
        """Read the request line and headers, then refuse a request from another site (refused)
        with a 403 before any method sees it."""
        if not super().parse_request():
            return False
        why = refused(self.command, self.headers.get("Host"), self.headers.get("Origin"), self.server.server_address[1])
        if why:
            self.close_connection = True
            self.send_json(403, {"error": why})
            return False
        return True

    def log_message(self, fmt, *args):  # keep the terminal quiet
        pass

    def send_json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path, content_type: str):
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def target(self, qs):
        name = (qs.get("file") or [""])[0]
        if not NAME_RE.match(name):
            return None
        return self.plans / name

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        if u.path == "/marked.min.js":  # vendored marked 15.0.12 (MIT), renders plan docs
            self.send_file(HERE / "marked.min.js", "text/javascript; charset=utf-8")
        elif u.path in ("/", "/index.html"):
            self.send_file(HERE / "board.html", "text/html; charset=utf-8")
        elif u.path.startswith("/fonts/"):  # the themes' bundled fonts (OFL, see THIRD_PARTY)
            font = HERE / "fonts" / u.path[len("/fonts/"):]
            if FONT_RE.match(font.name) and font.parent == HERE / "fonts" and font.is_file():
                self.send_file(font, "font/woff2")
            else:
                self.send_json(404, {"error": "not found"})
        elif u.path == "/favicon.svg":
            self.send_file(HERE / "favicon.svg", "image/svg+xml")
        elif u.path == "/favicon.ico":
            ico = HERE / "favicon.ico"
            if ico.is_file():
                self.send_file(ico, "image/x-icon")
            else:
                self.send_file(HERE / "favicon.svg", "image/svg+xml")
        elif u.path == "/api/files":
            # Only boards go in the picker; plan docs open from the links on their tiles.
            # Not the README, nor, where pickup is on, the sessions' rules (pickup.md).
            hidden = {"readme.md", pickup.RULES_FILE} if self.store.pickup else {"readme.md"}
            files = sorted(p.name for p in self.plans.glob("*.md") if NAME_RE.match(p.name) and p.name.lower() not in hidden)
            if (self.plans / ARCHIVE_LOG).is_file():
                files.append(ARCHIVE_LOG)
            self.send_json(200, {"dir": str(self.plans), "files": files,
                                 "name": self.store.name, "key": self.store.key, "model": self.store.model, "attach": agent.attach("{id}"), "models": agent.models(), "agent": agent.short(),
                                 "sessions": self.store.sessions, "pickupColumn": self.store.pickup_column,
                                 "trunk": self.store.trunk,
                                 "repos": [{"name": r.name, "main": r.main} for r in self.store.repos]})
        elif u.path == "/api/pickup":
            self.send_json(200, pickup.summary(self.store))
        elif u.path == "/api/board":
            p = self.target(qs)
            if p is None or not p.is_file():
                return self.send_json(404, {"error": "no such file"})
            self.send_json(200, {"text": p.read_text(encoding="utf-8"), "mtime": mtime(p),
                                 "maxKey": self.max_key(),
                                 "firstCommitted": first_committed(self.store, p) if p.parent == self.plans else {},
                                 "unmerged": unmerged_done(self.store, p.read_text(encoding="utf-8"))
                                 if p.name == "plans.md" else {},
                                 "moveTo": move_targets(self.plans, p.name) if p.parent == self.plans else {},
                                 "linked": self.linked(p), "tiles": self.tiles(p)})
        else:
            self.send_json(404, {"error": "not found"})

    def read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_PUT(self):
        u = urlparse(self.path)
        if u.path != "/api/board":
            return self.send_json(404, {"error": "not found"})
        p = self.target(parse_qs(u.query))
        if p is None or not p.is_file():
            return self.send_json(404, {"error": "no such file"})
        data = self.read_body()
        if data.get("mtime") != mtime(p):
            return self.send_json(409, {"error": "changed on disk",
                                        "text": p.read_text(encoding="utf-8"), "mtime": mtime(p)})
        old = p.read_text(encoding="utf-8")
        write_atomic(p, data["text"])
        self.commit([p], gitlog.describe(self.store, p.name, old, data["text"]))
        self.send_json(200, {"mtime": mtime(p), "linked": self.linked(p), "tiles": self.tiles(p)})

    def max_key(self) -> int:
        """The highest key in use, across every file that holds keyed tiles."""
        return pln.highest(self.store, *pln.keyed_files(self.store))

    def linked(self, board: Path) -> dict:
        """The linked tiles a board's page should know: plans.md and backlog.md share their
        links, since a tile's partner may be on the other one."""
        if board.parent == self.plans and board.name in MOVES:
            boards = [self.plans / n for n in MOVES if (self.plans / n).is_file()]
        else:
            boards = [board]
        return linked(self.store, *(b.read_text(encoding="utf-8") for b in boards))

    def tiles(self, board: Path) -> dict:
        """The tile index a board's page should know (tile_index): plans.md, backlog.md and the
        archive log share one, since a tile's master or sub-stories may be on another of them."""
        name = board.relative_to(self.plans).as_posix()
        names = FAMILY if name in FAMILY else [name]
        return tile_index(self.store, {n: (self.plans / n).read_text(encoding="utf-8")
                                       for n in names if (self.plans / n).is_file()})

    def commit(self, paths, message):
        gitlog.record(self.store, paths, message)

    def archive(self, board: Path):
        """Body: {mtime, text: the board without the tile, tile: its markdown, docs: [names]}."""
        data = self.read_body()
        if data.get("mtime") != mtime(board):
            return self.send_json(409, {"error": "changed on disk",
                                        "text": board.read_text(encoding="utf-8"), "mtime": mtime(board)})
        try:
            moved, touched = archive_docs(self.store, data.get("docs") or [])
        except (FileExistsError, subprocess.CalledProcessError) as e:
            return self.send_json(409, {"error": str(e)})
        # The board on disk may now carry rewritten paths too, so relink the page's text and write it.
        write_atomic(board, relink(data["text"], moved, self.store.link))
        tile = relink(data["tile"].rstrip("\n"), moved, self.store.link)
        file_in_archive_log(self.plans / ARCHIVE_LOG, rebase_into_archive(tile, board.name))
        key = self.store.key_re.search(tile.split("\n", 1)[0])
        self.commit([board, self.plans / ARCHIVE_LOG, *touched],
                    f"board: archive {key.group(0) if key else 'a tile'}" + (f" ({len(moved)} docs)" if moved else ""))
        self.send_json(200, {"mtime": mtime(board), "text": board.read_text(encoding="utf-8"), "moved": moved,
                             "tidied": tidy_archived(self.store, [tile]),
                             "maxKey": self.max_key(), "linked": self.linked(board),
                             "tiles": self.tiles(board)})

    def refresh_status(self, key: str):
        board = self.plans / PICKUP_BOARD
        old = board.read_text(encoding="utf-8") if board.is_file() else ""
        tile = next((t for t in board_tiles(self.store, old) if t["key"] == key), None)
        line = pickup.status(self.store, key, tile["text"]) if tile else None
        if line is None:
            return self.send_json(409, {"error": f"{key} has no session to report on"})
        write_atomic(board, with_status(self.store, old, key, line))
        self.commit([board], f"board: {key} status refreshed")
        self.send_json(200, {**pickup.summary(self.store), "status": line})

    def clear(self, board: Path):
        """Body: {mtime, text: the board as the page has it, column}. Archives every tile in
        that column but the Done tiles whose branch isn't merged yet. The reply carries
        "cleared", the record the page hands back to /api/unclear to undo it."""
        data = self.read_body()
        if data.get("mtime") != mtime(board):
            return self.send_json(409, {"error": "changed on disk",
                                        "text": board.read_text(encoding="utf-8"), "mtime": mtime(board)})
        kept = sorted(unmerged_done(self.store, data["text"])) if board.name == "plans.md" else []
        try:
            new, record, touched = clear_column(self.store, board, data["text"], data.get("column"), kept)
        except (KeyError, ValueError, FileExistsError, subprocess.CalledProcessError) as e:
            return self.send_json(409, {"error": str(e).strip("'\"")})
        write_atomic(board, new)
        keys = [t["key"] for t in record["tiles"] if t["key"]]
        self.commit([board, *touched], f"board: clear {record['column']}: archive {', '.join(keys) or str(record['count']) + ' tiles'}")
        tidied = tidy_archived(self.store, [t["text"] for t in record["tiles"]])
        self.send_json(200, {**self.board_reply(board), "cleared": record, "kept": kept, "tidied": tidied})

    def unclear(self, board: Path):
        """Body: {mtime, text: the board as the page has it, cleared: the record a clear
        replied with}. Puts those tiles back at the top of their column, out of the archive."""
        data = self.read_body()
        if data.get("mtime") != mtime(board):
            return self.send_json(409, {"error": "changed on disk",
                                        "text": board.read_text(encoding="utf-8"), "mtime": mtime(board)})
        record = data.get("cleared") or {}
        try:
            new, touched = undo_clear(self.store, board, data["text"], record)
        except (KeyError, LookupError, FileExistsError, subprocess.CalledProcessError) as e:
            return self.send_json(409, {"error": str(e).strip("'\"")})
        write_atomic(board, new)
        self.commit([board, *touched], f"board: undo clear of {record['column']} ({record['count']} tiles back)")
        self.send_json(200, self.board_reply(board))

    def board_reply(self, board: Path) -> dict:
        """What the page adopts after the server changed a board."""
        text = board.read_text(encoding="utf-8")
        return {"mtime": mtime(board), "text": text, "maxKey": self.max_key(),
                "unmerged": unmerged_done(self.store, text) if board.name == "plans.md" else {},
                "moveTo": move_targets(self.plans, board.name),
                "linked": self.linked(board), "tiles": self.tiles(board)}

    def move(self, board: Path):
        """Body: {mtime, text: the board without the tiles, tiles: their markdown (or tile, for
        one), column}. The tiles go to the top of that column on the other board (MOVES), in
        order, each keyed if it has no key. The page sends a tile's linked tiles with it."""
        data = self.read_body()
        if data.get("mtime") != mtime(board):
            return self.send_json(409, {"error": "changed on disk",
                                        "text": board.read_text(encoding="utf-8"), "mtime": mtime(board)})
        targets, column = move_targets(self.plans, board.name), data.get("column")
        if column not in targets.get("columns", []):
            return self.send_json(409, {"error": f"{board.name} tiles can't go to {column!r}"})
        dest = self.plans / targets["file"]
        tiles, keys, free = [], [], self.max_key() + 1
        for raw in data.get("tiles") or [data["tile"]]:
            tile, key = keyed_tile(self.store, raw.rstrip("\n"), self.store.fmt(free))
            if key == self.store.fmt(free):  # an unkeyed tile took the free key
                free += 1
            tiles.append(tile)
            keys.append(key)
        try:
            new = dest.read_text(encoding="utf-8")
            for tile in reversed(tiles):
                new = insert_at_top(new, column, tile)
        except KeyError as e:
            return self.send_json(409, {"error": str(e)})
        write_atomic(dest, new)  # the destination first: a tile is never lost between the two writes
        write_atomic(board, data["text"])
        self.commit([board, dest], f"board: move {', '.join(keys)} from {board.name} to {dest.name} {column}")
        self.send_json(200, {"mtime": mtime(board), "text": board.read_text(encoding="utf-8"),
                             "key": keys[0], "keys": keys,
                             "moveTo": move_targets(self.plans, board.name),
                             "maxKey": self.max_key(), "linked": self.linked(board), "tiles": self.tiles(board)})

    def pull(self, board: Path):
        """Body: {mtime, text: the board after a tile changed column, after: that tile's key,
        keys: the tiles linked to it that aren't on this board}. Saves the board, with any of
        those tiles found on the other board (PULLS) moved in straight after that tile."""
        data = self.read_body()
        if data.get("mtime") != mtime(board):
            return self.send_json(409, {"error": "changed on disk",
                                        "text": board.read_text(encoding="utf-8"), "mtime": mtime(board)})
        src = self.plans / PULLS[board.name]
        old, new, paths, keys = board.read_text(encoding="utf-8"), data["text"], [board], []
        src_text = src.read_text(encoding="utf-8") if src.is_file() else ""
        rest, found = take_tiles(self.store, src_text, set(data.get("keys") or []))
        if found:
            tiles = [keyed_tile(self.store, t, "") for t in found]  # found by key, so each has one
            keys = [key for _, key in tiles]
            try:
                new = insert_after(self.store, new, data.get("after"), [tile for tile, _ in tiles])
            except KeyError as e:
                return self.send_json(409, {"error": str(e)})
        write_atomic(board, new)  # this board first: a tile is never lost between the two writes
        if found:
            write_atomic(src, rest)
            paths.append(src)
        message = gitlog.describe(self.store, board.name, old, new)
        if keys:
            message += f"; {', '.join(keys)} from {src.name} with {data.get('after')}"
        self.commit(paths, message)
        self.send_json(200, {"mtime": mtime(board), "text": new, "pulled": keys,
                             "moveTo": move_targets(self.plans, board.name),
                             "maxKey": self.max_key(), "linked": self.linked(board), "tiles": self.tiles(board)})

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/api/move":
            p = self.target(parse_qs(u.query))
            if p is None or not p.is_file() or p.parent != self.plans or p.name not in MOVES:
                return self.send_json(404, {"error": "only plans.md and backlog.md tiles move between boards"})
            return self.move(p)
        if u.path == "/api/pull":
            p = self.target(parse_qs(u.query))
            if p is None or not p.is_file() or p.parent != self.plans or p.name not in PULLS:
                return self.send_json(404, {"error": "only plans.md pulls linked tiles in"})
            return self.pull(p)
        if u.path == "/api/pickup":  # {key}: start a waiting job now, in parallel; {key, restart: true}: the Restart button; {key, continue: true}: the Continue button; {key, pause: true}: the Pause button
            data = self.read_body()
            if data.get("status"):  # {key, status: true}: write where the story's session and branch stand into its tile
                return self.refresh_status(data.get("key", ""))
            if data.get("discard"):  # {key, discard: true, tile}: the tile is being deleted; throw its work away
                key = data.get("key", "")
                try:
                    done = pickup.discard(self.store, key, data.get("tile") or "")
                except (OSError, subprocess.SubprocessError) as e:
                    done = [f"not discarded: {e}"]
                for line in done:
                    pickup.log(f"{key} deleted: {line}")
                return self.send_json(200, {**pickup.summary(self.store), "discarded": done})
            why = pickup.why_off(self.store)
            if why:
                return self.send_json(409, {"error": why})
            if "reply" in data:  # {key, reply: text, steps: [squash, merge, push]}: the reply box
                why = pickup.reply(self.store, data.get("key", ""), data.get("reply") or "", data.get("steps") or [], on_start=note_session)
                if why:
                    return self.send_json(409, {"error": why, **pickup.summary(self.store)})
                return self.send_json(200, pickup.summary(self.store))
            if data.get("pause"):
                if not pickup.pause(self.store, data.get("key", ""), on_start=note_session):
                    return self.send_json(409, {"error": "that tile has no session at work to pause, or it couldn't be stopped", **pickup.summary(self.store)})
                return self.send_json(200, pickup.summary(self.store))
            if data.get("continue"):
                if not pickup.restart(self.store, data.get("key", ""), on_start=note_session, nudge=True):
                    return self.send_json(409, {"error": "that tile's session couldn't be stopped to continue it, or the board didn't start one", **pickup.summary(self.store)})
                return self.send_json(200, pickup.summary(self.store))
            act = pickup.restart if data.get("restart") else pickup.start_now
            if not act(self.store, data.get("key", ""), on_start=note_session):
                return self.send_json(409, {"error": "that tile has no session to start", **pickup.summary(self.store)})
            return self.send_json(200, pickup.summary(self.store))
        if u.path in ("/api/clear", "/api/unclear"):
            p = self.target(parse_qs(u.query))
            if p is None or not p.is_file() or p.parent != self.plans:
                return self.send_json(404, {"error": "no such board"})
            return self.clear(p) if u.path == "/api/clear" else self.unclear(p)
        if u.path == "/api/archive":
            p = self.target(parse_qs(u.query))
            if p is None or not p.is_file() or p.parent != self.plans:
                return self.send_json(404, {"error": "no such board"})
            return self.archive(p)
        self.send_json(404, {"error": "not found"})


def should_open(no_open: bool, env=None, isatty=None) -> bool:
    """Open the board in the browser on start? Not with --no-open, BOARD_NO_OPEN, or when
    stdout isn't a terminal. env, isatty: os.environ and sys.stdout.isatty, as they are now."""
    env = os.environ if env is None else env
    return not no_open and not env.get("BOARD_NO_OPEN") and (isatty or sys.stdout.isatty)()


def ensure_hooks(st) -> list:
    """The board's hooks are part of it: a store with an app that lacks them gets them when it
    is served (init.add_hooks: in the store, committed, and in the app), unless its board.json
    says "hooks": false. What was done, as lines for the console; [] when nothing was."""
    from . import init
    if not st.hooks or not st.app or not st.app.is_dir() or init.has_hooks(st.hooks_file):
        return []
    return ["The board's hooks were missing; added them:"] + ["  " + line for line in init.add_hooks(st)]


def main(args=None):
    ap = argparse.ArgumentParser(prog="kandalf serve", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", help="the store to serve (default: the one you're in, or docs/plans)")
    ap.add_argument("--port", type=int, help="default: the store's port (board.json), else 7788")
    ap.add_argument("--no-open", action="store_true", help="don't open the board in the browser (nor does BOARD_NO_OPEN=1)")
    args = ap.parse_args(args)
    st = stores.Store(args.dir) if args.dir else stores.default()
    if st is None:
        raise SystemExit("no store here: pass --dir, or run from a store or an app repo that links one in")
    if not st.dir.is_dir():
        raise SystemExit(f"no such folder: {st.dir}")
    Handler.store = st
    if st.root:
        gitlog.resume(st)  # changes queued before a restart still get committed
    for line in ensure_hooks(st):
        print(line, flush=True)
    why = pickup.why_off(st)  # None: pickup runs
    # Both list what is in flight first. Off the main thread: asking the CLI for its sessions takes a second.
    threading.Thread(target=report_in_flight if why else watch_pickups, args=(st,), daemon=True).start()
    port = args.port or st.port
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"{st.name} board on http://127.0.0.1:{port}  (files in {st.dir}, keys {st.key}-NN)")
    if why:
        print(why, flush=True)
    else:
        print(f"Tiles that land in In flight go to {agent.label()} ({st.permission} mode), in worktrees beside {st.app}", flush=True)
    if should_open(args.no_open):
        webbrowser.open(f"http://127.0.0.1:{port}/")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
