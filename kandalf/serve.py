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

A tile in a decision column can have its options drafted (/api/draft): a session researches
them in a research doc, and the drafts' turn of the watch puts them on the tile (drafts.py).

The map can ask the agent to sort stories into domains (/api/organize): a session proposes,
the organize turn of the watch reads the proposal (organize.py), and the page files what the
person ticks through /api/domain. /api/organize/end ends the run.

Two or more stories can be combined into one (/api/combine): the others go to the archive log.

A story's card can ask the agent to split it into sub-stories (/api/split): a session proposes,
the split turn of the watch reads the proposal (split.py), and the page's Create writes the
sub-stories the person ticks (/api/split/create). /api/split/end ends the run.

Tiles that say "Moves with KEY-NN" are linked: the page moves them together, and asks
this server for the ones on the other board (/api/move takes them along, /api/pull
fetches them).

A tile that says "Part of KEY-NN" is a sub-story of that tile, its master. The page gets an
index of every keyed tile (tiles.Snapshot.index): it folds a master's sub-stories into its tile, shows
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
from . import drafts  # noqa: E402  a decision's options, drafted by a session
from . import organize  # noqa: E402  the map's stories sorted into domains by a session
from . import split  # noqa: E402  a story split into sub-stories proposed by a session
from . import jobs as ledger  # noqa: E402  board-pickup.json, the record of each pickup job
from . import renaming  # noqa: E402  the move from plans.md and backlog.md to build.md and plan.md
from . import store as stores  # noqa: E402  which store, and its settings
from . import tiles as reader  # noqa: E402  what the board's markdown says
from . import stamp, __version__  # noqa: E402  which kandalf this is, for the page's corner
from . import settings  # noqa: E402  the settings panel's rules

HERE = Path(__file__).resolve().parent
BUILD = stamp.info(HERE, __version__)  # once, at the server's start: a checkout's build time is when it started
NAME_RE = re.compile(r"^((archive|active)/)?[A-Za-z0-9][A-Za-z0-9._-]*\.md$")
FONT_RE = re.compile(r"^[a-z0-9-]+\.woff2$")  # a bundled font under kandalf/fonts
DOC_RE = re.compile(r"^(active/)?[A-Za-z0-9][A-Za-z0-9._-]*\.md$")  # a plan doc a board tile can archive
ARCHIVE_LOG = reader.ARCHIVE_LOG
ARCHIVE_HEAD = """# Archived plans

Tiles dragged to the Archive column on the Build board, newest first. Their plan docs are in this folder.

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
    tiles = [t for t in reader.parse(store, text) if t.column == column and t.key not in keep]
    if not tiles:
        raise ValueError(f"nothing to clear in {column}")
    names = {m for t in tiles for m in TILE_DOC_RE.findall(t.text)} - {board.name}
    docs, touched = archive_docs(store, sorted(names))
    log = store.dir / ARCHIVE_LOG
    for t in reversed(tiles):  # each goes on top, so the last one first keeps the column's order
        file_in_archive_log(log, rebase_into_archive(relink(t.text, docs, store.link), board.name))
    record = {"board": board.name, "column": column, "count": len(tiles), "docs": docs,
              "tiles": [{"key": t.key, "text": t.text} for t in tiles]}
    return relink(reader.without(text, tiles), docs, store.link), record, [log, *touched]


def undo_clear(store, board: Path, text: str, record: dict) -> tuple:
    """Take a clear back, on the board as it reads now: its tiles leave the archive log and
    return to the top of their column as they were, and their plan docs move back.
    Returns (the board text to write, the files changed). Raises, changing nothing, if a
    cleared tile or doc is no longer where the clear left it."""
    log = store.dir / ARCHIVE_LOG
    logged = log.read_text(encoding="utf-8") if log.is_file() else ""
    filed, taken = reader.parse(store, logged, file=ARCHIVE_LOG), []
    def is_it(entry, tile, first_line):
        """A keyed tile is found by its key; an unkeyed one by its first line as it was filed."""
        if tile["key"]:
            return entry.key == tile["key"]
        return entry.text.split("\n", 1)[0] == first_line

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
    rest = reader.without(logged, taken)
    for name in record["docs"]:  # other archived tiles that link a doc that moved back
        rest = rest.replace(f"]({Path(name).name})", f"](../{name})")
    write_atomic(log, rest)
    return new, [log, *touched]


def filed_in_archive(text: str, tile: str) -> str:
    """The archive log's text with a tile at the top of its "## Archived" column, with today's date."""
    marker = "## Archived\n"
    at = text.index(marker) + len(marker)
    entry = f"{tile}\n  Archived {datetime.date.today().isoformat()}.\n"
    return text[:at] + "\n" + entry + text[at:].lstrip("\n")


def file_in_archive_log(log: Path, tile: str):
    """Put a tile at the top of the archive log's "## Archived" column, with today's date."""
    write_atomic(log, filed_in_archive(log.read_text(encoding="utf-8") if log.is_file() else ARCHIVE_HEAD, tile))


def moves(store) -> dict:
    """Where tiles move between the two boards, by dropping them on the other board's columns,
    shown beside Archive: a plan board tile is promoted to the top of the build board's To do,
    and a build board tile goes back to the top of any plan board column. {from: (to, columns or
    None for all)}, in the store's names."""
    build, plan = store.boards
    return {plan: (build, ["To do"]), build: (plan, None)}


def move_targets(store, name: str) -> dict:
    """{"file", "columns"}: where a tile on this board can be sent, or {} for nowhere."""
    board_moves = moves(store)
    if name not in board_moves or not (store.dir / board_moves[name][0]).is_file():
        return {}
    to, only = board_moves[name]
    columns = [line[3:].strip() for line in (store.dir / to).read_text(encoding="utf-8").split("\n") if line.startswith("## ")]
    return {"file": to, "columns": [c for c in columns if only is None or c in only]}


def keyed_tile(store, tile: str, next_key: str) -> tuple:
    """(tile, key): the tile as a build board story, with a "- [ ] " checkbox and its own key,
    or next_key when it has none."""
    first, nl, rest = tile.partition("\n")
    m = re.match(r"^[-*] (?:\[[ xX]\] )?(.*)$", first)
    head = m.group(1) if m else first
    own = reader.opening_key(store, first)
    key = own or next_key
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


def pickup_board(store) -> str:
    """The board whose In flight column is picked up: the build board."""
    return store.build


def with_session(store, text: str, job: dict) -> str:
    """The board with the job's session written into its tile (reader.with_session): one
    indented line naming the session, its branch and worktree, after the description and
    before the first sub-task. It replaces the line an earlier session left."""
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
    return reader.with_session(store, text, job["key"],
                               f"  {agent.short()} session `{job['id']}`: `{agent.attach(job['id'])}`.{where} Started {day}.")


def note_session(store, job: dict):
    """Write a started job's session id into its tile on the build board, and queue the commit."""
    board = store.dir / pickup_board(store)
    old = board.read_text(encoding="utf-8")
    new = with_session(store, old, job)
    if new != old:
        write_atomic(board, new)
        gitlog.record(store, [board], f"board: {job['key']} picked up by {agent.short()} session {job['id']}")


# The server's sessions adapter: the real one, writing each session it starts into its tile.
SESSIONS = pickup.Sessions(announce=note_session)

PICKUP_RECHECK = 20  # seconds between looks at the sessions while a job waits or runs

# Set by /api/draft (and a draft that didn't finish ending) so the drafts' turn looks at once;
# the turn clears it. An Event, as the handler threads set it and the watch's thread reads it.
DRAFTS_ASKED = threading.Event()
# Set by /api/organize and /api/organize/end so the organize turn looks at once; the turn clears it.
ORGANIZE_ASKED = threading.Event()
# Set by /api/split and /api/split/end so the split turn looks at once; the turn clears it.
SPLIT_ASKED = threading.Event()


_listed = {"by": None, "at": 0.0, "sessions": None}  # the last listing recent_sessions made, by which adapter


def recent_sessions():
    """The sessions as SESSIONS lists them, listed again only when the last listing is more than
    PICKUP_RECHECK seconds old: what the page's notice about the move goes by, so the page's polls
    don't run the agent's CLI each time. The move itself lists them afresh (watch_move_turn)."""
    if _listed["by"] is not SESSIONS or time.time() - _listed["at"] > PICKUP_RECHECK:
        _listed.update(by=SESSIONS, at=time.time(), sessions=SESSIONS.sessions())
    return _listed["sessions"]


def pickup_reply(store) -> dict:
    """What /api/pickup, /api/draft, /api/organize and /api/split answer: pickup.summary (its
    notice about the move going by recent_sessions), with the drafts (drafts.summary), why Draft
    options can't run (drafts.why_not, or None), the map's sorting run (organize.summary, or
    None) and a card's split run (split.summary, or None)."""
    return {**pickup.summary(store, sessions=pickup.Sessions(sessions=recent_sessions)),
            "drafts": drafts.summary(store), "draftsOff": drafts.why_not(store), "organize": organize.summary(store),
            "split": split.summary(store)}


def write_options(store, key: str, block: list, subject: str):
    """Put a draft's options block on its tile (reader.with_options), on the build board or else
    the plan board, and queue the commit with subject. A board that reads so already isn't written.
    A key on neither board raises KeyError."""
    for name in store.boards:
        board = store.dir / name
        if not board.is_file():
            continue
        old = board.read_text(encoding="utf-8")
        if not any(t.key == key for t in reader.parse(store, old, file=name)):
            continue
        new = reader.with_options(store, old, key, block)
        if new != old:
            write_atomic(board, new)
            gitlog.record(store, [board], subject)
        return
    raise KeyError(key)


def set_domains(store, changes, expect=None) -> tuple:
    """File stories into domains: changes is {KEY: name | None}. Each key's top master gets its
    Domain line written, replaced or (None) removed on whichever of the build board, the plan
    board and the archive log holds it, and the lot is one commit. A name some tile's own line already carries is
    written as it is; one that matches such a line only ignoring case is written as the first of
    them is spelt. expect, when given, is {KEY: name | None}: what the own Domain line of each
    key's top master should say right now (Undo sends what its drop wrote); when one says
    otherwise, ignoring case, or the key has no tile, nothing is written. Returns (200, {filed:
    {KEY: {from, to}}, unchanged: [KEY], skipped: [KEY]}), (400, {error}) when nothing is asked or a
    name is refused, or (409, {error: "changed since", changed: [the masters that differ, in
    expect's order]}), with nothing written."""
    if not isinstance(changes, dict) or not changes:
        return 400, {"error": "nothing to file"}
    names = {}
    for key, raw in changes.items():
        if raw is None:
            names[key] = None
        elif isinstance(raw, str):
            try:
                names[key] = reader.domain_name(raw)
            except ValueError as e:
                return 400, {"error": str(e)}
        else:
            return 400, {"error": "A domain needs a name."}
    snap = reader.Snapshot.read(store)
    spelt, carried = {}, set()
    for file in snap.files:
        for t in snap.tiles(file):
            own = reader.domain(t.text)
            if own:
                spelt.setdefault(own.lower(), own)
                carried.add(own)

    def top_of(key):
        masters = snap.lineage(key)
        return masters[-1].key if masters else key

    target, skipped = {}, []
    for key, name in names.items():
        tile = snap.tile(key)
        if tile is None:
            skipped.append(key)
            continue
        top = top_of(key)
        if top not in target:
            target[top] = None if name is None else name if name in carried else spelt.get(name.lower(), name)
    changed = []
    for key, want in (expect or {}).items():
        if snap.tile(key) is None:  # gone, so it has changed
            top, same = key, False
        else:
            top = top_of(key)
            own = reader.domain(snap.tile(top).text) or ""
            same = (want is None or isinstance(want, str)) and own.lower() == (want or "").strip().lower()
        if not same and top not in changed:
            changed.append(top)
    if changed:
        return 409, {"error": "changed since", "changed": changed}
    files, filed, unchanged = dict(snap.files), {}, []
    for top, name in target.items():
        file = snap.index()[top]["file"]
        before = reader.domain(snap.tile(top).text)
        new = reader.with_domain(store, files[file], top, name)
        if new == files[file]:
            unchanged.append(top)
        else:
            files[file] = new
            filed[top] = {"from": before, "to": name}
    paths = [store.dir / f for f in files if files[f] != snap.files[f]]
    for path in paths:
        write_atomic(path, files[path.relative_to(store.dir).as_posix()])
    if filed:
        tos, n = {v["to"] for v in filed.values()}, len(filed)
        many = f"{n} stor" + ("y" if n == 1 else "ies")
        subject = (f"board: {many} re-filed" if len(tos) > 1
                   else f"board: {many} → {tos.pop() or 'no domain'}")
        gitlog.record(store, paths, subject)
    return 200, {"filed": filed, "unchanged": unchanged, "skipped": skipped}


def combine_stories(store, keys, into, expect=None) -> tuple:
    """Combine stories into one: keys is the list of keys to combine and into the one that
    survives (tiles.combine says what its text becomes). The others go to the top of the archive log
    with "Combined into KEY." on them, their plan docs stay where they are, and any other tile that
    says Blocked by, Moves with or Part of one of them says the survivor instead. One commit.
    expect, when given, is {KEY: rev}, what Snapshot.index said of each key's tile when the page
    read it. Returns (200, {combined: [the absorbed keys], into}), (400, {error}) when the request
    isn't one, (409, {error}) when a story can't be combined (archived, in flight with a session
    or job, or a master of another of them), or (409, {error: "changed since", changed: [KEY]})
    when a tile differs from expect, with nothing written."""
    if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys) or not isinstance(into, str):
        return 400, {"error": "Say which stories to combine, and which one stays."}
    keys = list(dict.fromkeys(keys))
    if len(keys) < 2:
        return 400, {"error": "Combining needs at least two stories."}
    if into not in keys:
        return 400, {"error": "The story that stays has to be one of those combined."}
    snap = reader.Snapshot.read(store)
    for key in keys:
        if snap.tile(key) is None:
            return 409, {"error": f"{key} isn't on the boards any more."}
    for key in keys:
        if snap.index()[key]["file"] == ARCHIVE_LOG:
            return 409, {"error": f"{key} is archived; an archived story can't be combined."}
    held = {j["key"] for j in ledger.Ledger(store).live()}
    for key in keys:
        if reader.session(snap.tile(key).text) or key in held:
            return 409, {"error": f"{key} has a session (or a place in the pickup queue); end it before combining it."}
    for key in keys:
        up = [m.key for m in snap.lineage(key) if m.key in keys]
        if up:
            return 409, {"error": f"{key} is part of {up[0]}, so they can't be combined: a master isn't combined with its own sub-story."}
    if isinstance(expect, dict):
        index = snap.index()
        changed = [k for k, rev in expect.items() if k not in index or index[k]["rev"] != rev]
        if changed:
            return 409, {"error": "changed since", "changed": changed}
    others = sorted((k for k in keys if k != into), key=store.num)
    files, index = dict(snap.files), snap.index()
    main = [store.main_repo.name] if store.main_repo else []

    def works_in(key):  # what pickup resolves: the nearest Repos line up the family, else the main repo
        return snap.nearest(key, reader.repos) or main
    union = list(dict.fromkeys(n for k in [into, *others] for n in works_in(k)))
    same = {n.lower() for n in union} == {n.lower() for n in works_in(into)}
    merged = reader.combine(store, snap.tile(into).text, [snap.tile(k).text for k in others],
                            repos=None if same else union, has_master=bool(snap.parent(into)))
    for name in files:
        files[name] = reader.repointed(store, files[name], others, into, skip={into, *others})
    survivor_file = index[into]["file"]
    files[survivor_file] = reader.with_tile(store, files[survivor_file], into, merged)
    notes = []
    for key in others:
        name = index[key]["file"]
        tile = reader.parse(store, files[name], file=name)
        tile = next(t for t in tile if t.key == key)
        notes.append(rebase_into_archive(reader.combined_note(store, tile.text, into), name))
        files[name] = reader.without(files[name], [tile])
    log = files.get(ARCHIVE_LOG, ARCHIVE_HEAD)
    for note in reversed(notes):
        log = filed_in_archive(log, note)
    files[ARCHIVE_LOG] = log
    paths = []
    for name, text in sorted(files.items(), key=lambda f: f[0] != ARCHIVE_LOG):  # the archive first: a failed write never loses a story
        if text != snap.files.get(name):
            (store.dir / name).parent.mkdir(exist_ok=True)
            write_atomic(store.dir / name, text)
            paths.append(store.dir / name)
    gitlog.record(store, paths, f"board: {', '.join(others)} combined into {into}", subject=True)
    end_drafts(store, others)
    return 200, {"combined": others, "into": into}


def split_story(store, key, stories, expect=None) -> tuple:
    """Split a story: stories is the list of sub-stories to write, [{title, description, tasks:
    [{text, done}]}], in order. Each gets the next free key (one past the highest key on a tile
    line of the boards and the archive log, so keys are never reused) and a Part of line, and
    goes in the story's board and column, directly below it (tiles.sub_story). A task that
    matches one of the story's own sub-tasks (tiles.task_key) is moved: it leaves the story and
    goes over as the story has it, its tick and the lines under it kept; any other is new. The
    story's other sub-tasks stay as they are (of two alike, the first moves). One commit,
    "board: PLN-72 split into PLN-90, PLN-91". It needs the ledger's split run ready for this
    key, and takes it (split.claim) before it writes, then ends it, so it is created once.
    expect, when given, is the story's rev as the run read it (Snapshot.index), as the page was
    told it. Returns (200, {into, created: [keys]}), (400, {error}) when the request isn't one,
    (409, {error}) when the story can't be split (split.refusal) or there is no ready run of
    it, or (409, {error: "changed since", changed: [key]}) when the story or the proposal
    differs, with nothing written."""
    if not isinstance(key, str) or not isinstance(stories, list) or not all(isinstance(s, dict) for s in stories):
        return 400, {"error": "Say which story to split, and into what."}
    if not stories:
        return 400, {"error": "Tick a sub-story to create, or press Discard."}
    for s in stories:
        if not isinstance(s.get("title"), str) or not reader.story_title(s["title"]):
            return 400, {"error": "Every sub-story needs a title."}
        if not isinstance(s.get("description") or "", str) or not isinstance(s.get("tasks") or [], list):
            return 400, {"error": "Say which story to split, and into what."}
    snap = reader.Snapshot.read(store)
    why = split.refusal(store, snap, key)
    if why:
        return 409, {"error": why}
    refused = split.claim(store, key, snap.index()[key]["rev"], expect)
    if refused:
        return refused
    name, master = snap.index()[key]["file"], snap.tile(key).text
    own = reader.subtask_lines(master)
    n = max((x for text in snap.files.values() for x in reader.numbers(store, text)), default=0)
    keys, mds, moved = [], [], set()
    for s in stories:
        n += 1
        keys.append(store.fmt(n))
        tasks = []
        for t in s.get("tasks") or []:
            if not isinstance(t, dict) or not str(t.get("text") or "").strip():
                continue
            k = reader.task_key(str(t["text"]))
            if k not in own:  # a new sub-task
                tasks.append(t)
            elif k not in moved:  # the story's own, moved with its tick and the lines under it; only once
                moved.add(k)
                tasks.append(own[k])
        mds.append(reader.sub_story(keys[-1], key, s["title"], s.get("description") or "", tasks))
    text = reader.with_tile(store, snap.files[name], key, reader.without_subtasks(master, moved))
    text = reader.insert_after(store, text, key, mds)
    write_atomic(store.dir / name, text)
    gitlog.record(store, [store.dir / name], f"board: {key} split into {', '.join(keys)}", subject=True)
    split_end(store)  # claimed (ending) before the write: now its record goes
    return 200, {"into": key, "created": keys}


def split_request(store, data: dict) -> tuple:
    """The status and reply for /api/split. Body: {key, note} starts a run (split.start); one
    refused says why (409). A start that fails part-way is recorded as a run that needs you, so
    the reply is pickup_reply's either way, and the split turn looks on its next turn."""
    try:
        split.start(store, str(data.get("key") or ""), str(data.get("note") or ""), sessions=SESSIONS)
    except split.Refused as e:
        return 409, {"error": str(e)}
    SPLIT_ASKED.set()
    return 200, pickup_reply(store)


def split_end(store) -> tuple:
    """The status and reply for /api/split/end (Cancel, Discard, Close): the run ended in any
    state (split.end), and with none there is nothing to do. One whose ending stops part-way is
    left ending for the split turn; the page is told no run either way."""
    try:
        if not split.end(store, sessions=SESSIONS):
            pickup.log("split: the run is ending; a later look finishes it")
    except Exception as e:  # marked ending first, so the look finishes it
        pickup.log(f"split: the run could not be ended yet, so a later look ends it: {e}")
    SPLIT_ASKED.set()
    return 200, pickup_reply(store)


def end_drafts(store, keys):
    """End the drafts of tiles a route took off the board (drafts.end), after its write. One
    that doesn't finish is left ending for the next look, and said; it never fails the route."""
    for key in keys:
        try:
            had = any(d["key"] == key for d in drafts.live(store))
            if not had:
                continue
            if drafts.end(store, key, sessions=SESSIONS):
                pickup.log(f"{key}: draft ended")
            else:
                pickup.log(f"{key}: draft ending; a later look finishes it")
                DRAFTS_ASKED.set()
        except Exception as e:  # the route's own change is made: a draft left over is the look's
            pickup.log(f"{key}: the draft could not be ended: {e}")
            DRAFTS_ASKED.set()


def draft_request(store, board: Path, data: dict) -> tuple:
    """The status and reply for /api/draft. Body: {key, note} asks for a draft (drafts.request:
    a draft the key has is ended and a new one queued): the key must be an open tile in a
    decision column of this board, the build or the plan board (404), and Draft options able to run
    (409, drafts.why_not). {key, stop: true} ends the key's draft (drafts.end), wherever its tile
    is now: it needs only a draft in the ledger, in any state (404). A draft that didn't get
    queued is a 500. The reply is pickup_reply's, and the drafts' turn looks on its next turn."""
    key = str(data.get("key") or "")
    if data.get("stop"):
        if not any(d["key"] == key for d in drafts.live(store)):
            return 404, {"error": f"{key} has no draft"}
        try:  # it is marked ending first, so one whose ending stops part-way is left for the look
            drafts.end(store, key, sessions=SESSIONS)
        except Exception as e:
            pickup.log(f"{key}: the draft could not be ended yet, so a later look ends it: {e}")
        DRAFTS_ASKED.set()
        return 200, pickup_reply(store)
    tile = None
    if key and board.parent == store.dir and board.name in store.boards:
        tile = next((t for t in reader.parse(store, board.read_text(encoding="utf-8"), file=board.name) if t.key == key), None)
    if tile is None or tile.done or not reader.is_decision_column(tile.column):
        return 404, {"error": f"{key} is not a decision on this board"}
    why = drafts.why_not(store)
    if why:
        return 409, {"error": why}
    since = SESSIONS.now()
    try:
        drafts.request(store, key, tile.text, str(data.get("note") or "") or None, sessions=SESSIONS)
    except Exception as e:
        mine = drafts.of(store, key)
        if not (mine and mine["state"] == "queued" and mine.get("asked_at", 0) >= since):
            pickup.log(f"{key}: the draft could not be asked for: {e}")
            return 500, {"error": str(e)}
        # queued: only ending the draft before it stopped part-way, which the look finishes
        pickup.log(f"{key}: the draft before it could not be ended yet, so a later look ends it: {e}")
    DRAFTS_ASKED.set()
    return 200, pickup_reply(store)


def organize_request(store, data: dict) -> tuple:
    """The status and reply for /api/organize. Body: {note, refile} starts a run (organize.start);
    one refused says why (409). A start that fails part-way is recorded as a run that needs you,
    so the reply is pickup_reply's either way, and the organize turn looks on its next turn."""
    try:
        organize.start(store, str(data.get("note") or ""), bool(data.get("refile")), sessions=SESSIONS)
    except organize.Refused as e:
        return 409, {"error": str(e)}
    ORGANIZE_ASKED.set()
    return 200, pickup_reply(store)


def organize_end(store) -> tuple:
    """The status and reply for /api/organize/end (Cancel, Discard, Close, and after Apply): the
    run ended in any state (organize.end), and with none there is nothing to do. One whose
    ending stops part-way is left ending for the organize turn; the page is told no run either way."""
    try:
        if not organize.end(store, sessions=SESSIONS):
            pickup.log("organize: the run is ending; a later look finishes it")
    except Exception as e:  # marked ending first, so the look finishes it
        pickup.log(f"organize: the run could not be ended yet, so a later look ends it: {e}")
    ORGANIZE_ASKED.set()
    return 200, pickup_reply(store)


def report_in_flight(store):
    """On the console, once at startup: what is in flight, and the Claude session on each."""
    board = store.dir / pickup_board(store)
    if not board.is_file():
        return
    try:
        for line in pickup.startup_report(store, reader.Snapshot.read(store).in_flight(), sessions=SESSIONS):
            pickup.log(line)
    except Exception as e:  # the listing is a courtesy: never stop the server for it
        pickup.log(f"in flight could not be listed: {e}")


def reload_settings(store) -> bool:
    """Read board.json again when it changed since the last read, under the job ledger's lock so a look
    never runs on half the settings. True when new settings are in force. A file that can't be
    read (not UTF-8, or gone between the look and the read) keeps the old ones, and the store's
    config_problem says why."""
    try:
        with ledger.Ledger(store).locked():
            return store.reload_if_changed()
    except (OSError, ValueError) as e:
        store.config_problem = f"board.json could not be read: {e}"
        return False


def adopt_in_flight(store):
    """Hand the In flight tiles that never had a session to the next look (pickup.adopt), and
    say which: at the start, and whenever pickup comes on."""
    board = store.dir / pickup_board(store)
    try:
        if board.is_file():
            for key in pickup.adopt(store, reader.Snapshot.read(store).in_flight()):
                pickup.log(f"{key} is in flight with no session: picking it up")
    except Exception as e:  # a courtesy too: the watch still runs
        pickup.log(f"in flight tiles could not be adopted: {e}")


def watch_turn(store, ctx: dict):
    """One turn of the pickup watch: read board.json again if it changed, and while pickup is
    on and can run, look at the build board when it, the plan board or the archive log changes (a blocker
    done on those frees a tile in flight), and now and then while a job waits or runs, and hand
    what landed in In flight to pickup.tick. Pickup coming on adopts what is in flight first; on
    but unable to run says why once. ctx carries the watch from turn to turn:
    seen, last, told and before for the looks, was_on, mtime (board.json's, as last read), and
    why and problem, what was last said."""
    ctx.setdefault("why", None)
    ctx.setdefault("problem", None)
    reload_settings(store)
    # Read again here or by the settings route (settings.apply reloads the store itself).
    changed, ctx["mtime"] = store.config_mtime != ctx.get("mtime"), store.config_mtime
    if store.config_problem != ctx["problem"]:
        ctx["problem"] = store.config_problem
        if store.config_problem:
            pickup.log(f"{store.config_problem}; the settings before it stay in force")
    if not store.pickup:
        ctx["was_on"], ctx["why"] = False, None
        return
    if changed or not ctx["was_on"]:
        why = pickup.why_off(store)
        if why and why != ctx["why"]:
            pickup.log(why)
        elif not why and (not ctx["was_on"] or ctx["why"]):
            adopt_in_flight(store)
            ctx["seen"] = None  # look at once
        ctx["was_on"], ctx["why"] = True, why
    if ctx["why"]:
        return
    board = store.dir / pickup_board(store)
    try:
        # What a look reads: the build board, and the plan board and archive log its tiles'
        # blockers may be on (a decision archived from the plan board writes no build board). A
        # file not there counts as None.
        at = board_mtimes(store) if board.is_file() else None
        # A file still being written is left for the next look: half a board reads as
        # tiles that left In flight, and then as the same tiles arriving.
        settled, ctx["before"] = at == ctx["before"], at
        busy = ledger.Ledger(store).busy()
        if at is not None and settled and (at != ctx["seen"] or (busy and time.time() - ctx["last"] > PICKUP_RECHECK)):
            ctx["seen"], ctx["last"] = at, time.time()
            pickup.tick(store, reader.Snapshot.read(store).in_flight(), sessions=SESSIONS)
            tidy_stragglers(store, ctx["told"])
    except Exception as e:  # a bad look must not end the watch
        pickup.log(f"the look at {board.name} failed: {e}")


def board_mtimes(store) -> tuple:
    """The mtimes of the boards a draft's tile can be on or leave for (the store's family), None
    for a file that isn't there."""
    return tuple((store.dir / n).stat().st_mtime_ns if (store.dir / n).is_file() else None for n in store.family)


def drafts_seen(store, held: list) -> tuple:
    """What the drafts' turn compares from turn to turn: the research docs (drafts.docs) and,
    while a draft that isn't ending is live, the boards (board_mtimes), as a tile that is moved
    or deleted ends its draft. ({}, None) with no draft; nothing is stat'ed then."""
    docs = drafts.docs(store, held)
    return docs, board_mtimes(store) if docs else None


def look_drafts(store, said: set) -> set:
    """One drafts.look, on the boards read just before it, its lines on the console. A line
    saying a block could not be written (drafts.UNWRITTEN) that the look before gave too (in
    said) isn't said again: it repeats while the write keeps failing. Every other line is said.
    The lines of this look that say a write failed."""
    def write(key, block, subject):  # the look's write(key, block, subject), on this store
        write_options(store, key, block, subject)
    lines = drafts.look(store, reader.Snapshot.read(store), sessions=SESSIONS, write=write)
    failed = {line for line in lines if f": {drafts.UNWRITTEN}: " in line}
    for line in lines:
        if line not in failed or line not in said:
            pickup.log(line)
    return failed


def watch_drafts_turn(store, ctx: dict):
    """One turn of the drafts' watch, whether or not pickup is on, reading the ledger once. With
    a draft in the ledger, a look (look_drafts) when:
      - a draft was just asked for or stopped (DRAFTS_ASKED);
      - PICKUP_RECHECK seconds have passed since the last look, and a draft is queued, at work
        or ending (one whose ending stopped part-way is tried again), or has a research doc
        changed since it was read (drafts.unread: the look left it while its session was busy),
        or the last look could not write a block;
      - the research docs or the boards (drafts_seen) differ from what the last look saw and
        are as they were a turn ago (settled).
    ctx carries d_last (when it last looked), d_seen (what it looked at), d_before (what was
    seen a turn ago) and d_said (the lines of the last look that said a write failed)."""
    ctx.setdefault("d_last", 0.0)
    ctx.setdefault("d_seen", ({}, None))
    ctx.setdefault("d_before", ({}, None))
    ctx.setdefault("d_said", set())
    asked = DRAFTS_ASKED.is_set()
    if asked:
        DRAFTS_ASKED.clear()  # before the ledger is read, so a draft asked for after it is looked at next turn
    held = drafts.live(store)
    at = drafts_seen(store, held)
    settled, ctx["d_before"] = at == ctx["d_before"], at
    if not held:
        return
    due = time.time() - ctx["d_last"] > PICKUP_RECHECK and (
        bool(ctx["d_said"]) or drafts.busy(store, held) or any(d["state"] == "ending" for d in held)
        or drafts.unread(store, held))
    if asked or due or (settled and at != ctx["d_seen"]):
        ctx["d_seen"], ctx["d_last"] = at, time.time()
        ctx["d_said"] = look_drafts(store, ctx["d_said"])


def look_organize(store):
    """One organize.look, its lines on the console."""
    for line in organize.look(store, sessions=SESSIONS):
        pickup.log(line)


def watch_run_turn(run, asked: threading.Event, look, store, ctx: dict, tag: str):
    """One turn of a session run's watch (run: organize or split), whether or not pickup is on:
    with a run that is busy (run.busy), a look (look(store)) when one was just asked for or ended
    (asked), or PICKUP_RECHECK seconds have passed since the last look, or the file its session
    writes (run.OUTPUT) changed since the last look and is as it was a turn ago (settled). ctx
    carries <tag>last (when it last looked), <tag>seen (the file's mtime then) and <tag>before
    (a turn ago)."""
    last, seen, before = tag + "last", tag + "seen", tag + "before"
    ctx.setdefault(last, 0.0)
    was_asked = asked.is_set()
    if was_asked:
        asked.clear()
    rec = run.of(store)
    if not rec or not run.busy(store, rec):
        return
    try:
        at = (Path(rec["worktree"]) / run.OUTPUT).stat().st_mtime_ns if rec.get("worktree") else None
    except OSError:
        at = None
    settled, ctx[before] = at == ctx.get(before), at
    if was_asked or time.time() - ctx[last] > PICKUP_RECHECK or (settled and at != ctx.get(seen)):
        ctx[last], ctx[seen] = time.time(), at
        look(store)


def watch_organize_turn(store, ctx: dict):
    """One turn of the organize watch (watch_run_turn, asked by ORGANIZE_ASKED; ctx's o_ keys)."""
    watch_run_turn(organize, ORGANIZE_ASKED, look_organize, store, ctx, "o_")


def look_split(store):
    """One split.look, its lines on the console."""
    for line in split.look(store, sessions=SESSIONS):
        pickup.log(line)


def watch_split_turn(store, ctx: dict):
    """One turn of the split watch (watch_run_turn, asked by SPLIT_ASKED; ctx's s_ keys)."""
    watch_run_turn(split, SPLIT_ASKED, look_split, store, ctx, "s_")


def old_board_mtimes(store) -> tuple:
    """The mtimes of the old names' two board files (plans.md, backlog.md), None for one not there."""
    def mtime_of(path):
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return None
    return tuple(mtime_of(store.dir / n) for n in renaming.NEW)


def watch_move_turn(store, ctx: dict):
    """One turn of the move to Plan and Build: when the store is due (renaming.due, the turn's one
    check while it isn't), and neither old board changed since the turn before (the settle rule: a
    file being written is left to finish), renaming.move, its lines on the console. A move that
    waits (blocked) or fails is tried again PICKUP_RECHECK seconds later, so a session at work
    isn't listed every turn; a failure is said once until it changes. The server's own writes take
    no lock to wait for: the settle rule covers them. ctx carries m_before (the mtimes a turn ago),
    m_next (when to try again) and m_error (the failure last said)."""
    try:
        if not renaming.due(store):
            return
        at = old_board_mtimes(store)
        settled, ctx["m_before"] = at == ctx.get("m_before"), at
        if not settled or time.time() < ctx.get("m_next", 0.0):
            return
        for line in renaming.move(store, sessions=SESSIONS):
            pickup.log(line)
        ctx["m_error"] = None
        if renaming.due(store):  # it waits
            ctx["m_next"] = time.time() + PICKUP_RECHECK
    except Exception as e:  # a failed move must not end the watch
        if str(e) != ctx.get("m_error"):
            pickup.log(f"the move to Plan and Build failed: {e}")
        ctx["m_error"], ctx["m_next"] = str(e), time.time() + PICKUP_RECHECK


def watch_pickups(store, every: float = 2.0):
    """Forever, from the server's start: move the store to Plan and Build if it is due and free
    to, say what is in flight, adopt what has no session when pickup can run, look at the drafts
    and the organize and split runs once, then a watch_move_turn, a watch_drafts_turn, a
    watch_organize_turn, a watch_split_turn and a watch_turn every few seconds, so pickup
    follows board.json."""
    moving = {"m_before": old_board_mtimes(store)}  # at the start nothing of the server's is being written
    watch_move_turn(store, moving)
    report_in_flight(store)
    why = pickup.why_off(store) if store.pickup else None  # main has said it
    if store.pickup and not why:
        adopt_in_flight(store)
    ctx = {"seen": None, "last": 0.0, "told": set(), "before": None, "was_on": store.pickup, "why": why,
           "mtime": store.config_mtime, "problem": store.config_problem, **moving}
    try:  # the drafts' look at the start: drafts left ending are finished, and those whose tiles went are ended
        held = drafts.live(store)
        if held:
            seen = drafts_seen(store, held)
            said = look_drafts(store, set())
            ctx.update(d_seen=seen, d_before=seen, d_said=said, d_last=time.time())
    except Exception as e:  # never stop the server for it
        pickup.log(f"the drafts' look at the start failed: {e}")
    try:  # a run whose session went with the server is lost, and one left ending is finished
        if organize.of(store):
            look_organize(store)
    except Exception as e:
        pickup.log(f"the organize look at the start failed: {e}")
    try:  # the same for a split
        if split.of(store):
            look_split(store)
    except Exception as e:
        pickup.log(f"the split look at the start failed: {e}")
    while True:
        watch_move_turn(store, ctx)  # first, so the turns after it read the store as it is now
        try:
            watch_drafts_turn(store, ctx)
        except Exception as e:  # a bad drafts' turn must not end the watch, nor keep pickup's from running
            pickup.log(f"the drafts' turn failed: {e}")
        try:
            watch_organize_turn(store, ctx)
        except Exception as e:  # nor a bad organize turn
            pickup.log(f"the organize turn failed: {e}")
        try:
            watch_split_turn(store, ctx)
        except Exception as e:  # nor a bad split turn
            pickup.log(f"the split turn failed: {e}")
        try:
            watch_turn(store, ctx)
        except Exception as e:  # nor a bad turn
            pickup.log(f"the pickup watch's turn failed: {e}")
        time.sleep(every)


def pulls(store) -> dict:
    """{board: where from}: a linked tile still on the plan board follows its partner on the
    build board. Never the other way, which would unschedule a tile because its partner changed
    kind."""
    build, plan = store.boards
    return {build: plan}

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


def local_branches(store, limit: int = 200) -> list:
    """The main repo's local branches, most recently committed first, for the Base field of the
    tile dialog; [] when there is no main repo."""
    if not store.app or not (store.app / ".git").exists():
        return []
    try:
        got = _app_git(store, "for-each-ref", "--sort=-committerdate", "--format=%(refname:short)", "refs/heads")
    except (OSError, subprocess.SubprocessError):
        return []
    return got.stdout.split()[:limit] if got.returncode == 0 else []


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
                                job=pickup.job_for(store, key) if key else None, sessions=SESSIONS)
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
    keys = {j["key"] for j in ledger.Ledger(store).with_branch()}
    if not keys or not log.is_file():
        return
    boards = reader.Snapshot.read(store, store.boards)
    live = {t.key for name in boards.files for t in boards.tiles(name)}
    for t in reader.parse(store, log.read_text(encoding="utf-8"), file=ARCHIVE_LOG):
        key = t.key
        if key not in keys or key in live or ("tidied", key) in told:
            continue
        said = tidy_archived(store, [t.text], told)
        if not any(": kept " in line or ": not tidied" in line for line in said):
            told.add(("tidied", key))  # nothing of it is left: don't look again


def unmerged_done(store, text: str) -> dict:
    """{"KEY-NN": [branch, ...]} for each tile in the Done column that names a feature
    branch not yet merged into its story's trunk (pickup.story_trunk): one in backticks with a slash in it
    (`feat/x`), or the branch on its Claude session line. A branch is looked for in the repos
    the tile names (its session line's, else its Repos line's, else the main one), and counts
    as unmerged when it is in any of them. The board outlines those tiles red. {} when the
    main repo isn't there."""
    if not store.app or not (store.app / ".git").exists():
        return {}
    tiles = {t.key: t.text.split("\n") for t in reader.parse(store, text) if t.key and t.done}  # each Done tile's lines: a Repos line can follow its branch
    out, jobs = {}, {j["key"]: j for j in ledger.Ledger(store).with_branch() if j.get("worktree")}  # the latest of each
    for key, lines in tiles.items():
        tile = "\n".join(lines)
        found = pickup.tile_cuts(store, tile)
        if found:
            repos = [repo for _, repo, _ in found[1] if repo]
        else:
            names = reader.repos(tile)
            repos = [r for r in map(store.repo, names) if r] if names is not None else [store.main_repo]
        repos = [pickup.as_story(store, r, jobs.get(key), tile) for r in repos]  # merged into the story's own trunk
        for line in lines:
            on_line = reader.session(line)
            noted = on_line.branch if on_line else None  # the branch pickup cut, whatever its name
            for b in BRANCH_TOKEN.findall(line) + ([noted] if noted else []):
                if any(_branch_merged(store, b, repo) is False for repo in repos) and b not in out.get(key, []):
                    out.setdefault(key, []).append(b)
    return out


# A board's name on Plan and Build and the name it had before the move: whatever reads a
# board's history reads both, so a tile keeps its dates across the move.
WAS = {stores.BUILD: stores.OLD_BUILD, stores.PLAN: stores.OLD_PLAN}


def history_paths(store, board: Path) -> list:
    """The paths a board's git history is read by: the board, and, for a store's Build or Plan
    board on the new names, the old name it had before the move (build.md and plans.md, plan.md
    and backlog.md)."""
    was = WAS.get(board.name) if board.parent == store.dir and board.name in store.boards else None
    return [str(board)] + ([str(board.parent / was)] if was else [])


def first_committed(store, board: Path) -> dict:
    """{"KEY-NN": Unix seconds} for each tile key: the author time of the first commit
    that added a line for it to this board, or to the board under its old name (history_paths),
    the earliest kept. Author time, because a rebase rewrites the commit time. Cached per HEAD,
    so it's one git log per new commit."""
    root = store.root
    if not root:
        return {}
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if (board, head) in _first_cache:
        return _first_cache[(board, head)]
    log = subprocess.run(["git", "-C", str(root), "log", "--reverse", "--format=@@C %at", "-p",
                          "--unified=0", "--", *history_paths(store, board)], capture_output=True, text=True).stdout
    opens = reader.head_re(store)
    first, t = {}, None
    for line in log.splitlines():
        if line.startswith("@@C "):
            t = int(line[4:])
        elif line.startswith("+") and (m := opens.match(line[1:])):
            key = m.group(1)
            first[key] = min(t, first.get(key, t))
    seed = store.dir / FIRST_SEED
    if board == store.dir / store.build and seed.is_file():
        for key, at in json.loads(seed.read_text(encoding="utf-8"))["first"].items():
            first[key] = min(at, first.get(key, at))
    _first_cache[(board, head)] = first
    return first


def archive_tile(store, board: Path, text: str, tile: str, docs: list, subject: str | None = None) -> dict:
    """File a tile in the archive log and move its plan docs: write the board as the page has it
    (text, without the tile), relinked, then commit. subject replaces the commit's default
    subject. The reply carries what the page needs afterwards. Raises what archive_docs raises."""
    moved, touched = archive_docs(store, docs)
    # The board on disk may now carry rewritten paths too, so relink the page's text and write it.
    write_atomic(board, relink(text, moved, store.link))
    tile = relink(tile.rstrip("\n"), moved, store.link)
    file_in_archive_log(store.dir / ARCHIVE_LOG, rebase_into_archive(tile, board.name))
    key = store.key_re.search(tile.split("\n", 1)[0])
    gitlog.record(store, [board, store.dir / ARCHIVE_LOG, *touched],
                  subject or f"board: archive {key.group(0) if key else 'a tile'}" + (f" ({len(moved)} docs)" if moved else ""))
    end_drafts(store, [key.group(0)] if key else [])
    name = board.relative_to(store.dir).as_posix()
    snap = reader.Snapshot.read(store, store.family if name in store.family else [name])
    links = list(moves(store)) if board.parent == store.dir and board.name in store.boards else [name]
    return {"mtime": mtime(board), "text": board.read_text(encoding="utf-8"), "moved": moved,
            "tidied": tidy_archived(store, [tile]),
            "maxKey": pln.highest(store, *pln.keyed_files(store)),
            "linked": reader.Snapshot.read(store, links).linked(),
            "tiles": snap.index()}


def unblocked_by(store, key: str) -> list:
    """The keys of the open tiles whose own or inherited blockers name this key and that
    nothing blocks any longer (Snapshot.blocking is empty), in key order."""
    snap = reader.Snapshot.read(store)
    found = [k for k, t in snap.texts().items()
             if k != key and not snap.tile(k).done and key in snap._blockers_of(k) and not snap.blocking(k)]
    return sorted(found, key=lambda k: (k.rpartition("-")[0], int(k.rpartition("-")[2]) if k.rpartition("-")[2].isdigit() else 0))


def decide_tile(store, board: Path, data: dict, today: str) -> tuple:
    """The status and reply for an answered decision. Body: {mtime, text: the board without the
    tile, tile: its markdown, docs, answer}. The board's mtime is checked first (409, nothing
    written); an empty answer and an unkeyed tile are refused (400). The tile gets its decision
    line and is filed as Archive files it; the reply is Archive's and "unblocked", the keys that
    nothing blocks any longer."""
    if data.get("mtime") != mtime(board):
        return 409, {"error": "changed on disk", "text": board.read_text(encoding="utf-8"), "mtime": mtime(board)}
    answer = " ".join(str(data.get("answer") or "").split())  # any value is taken as its text, on one line
    if not answer:
        return 400, {"error": "an answer is needed"}
    key = store.key_re.search(data["tile"].split("\n", 1)[0])
    if not key:
        return 400, {"error": "a decision needs a key"}
    tile = reader.with_decision(data["tile"], answer, today)
    try:
        reply = archive_tile(store, board, data["text"], tile, data.get("docs") or [], f"board: {key.group(0)} decided")
    except (FileExistsError, subprocess.CalledProcessError) as e:
        return 409, {"error": str(e)}
    return 200, {**reply, "unblocked": unblocked_by(store, key.group(0))}


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
        """The file a request's ?file= names, under the store's present name for a board named
        by its old or new name (store.board), or None for a name that isn't a store file's."""
        name = (qs.get("file") or [""])[0]
        if not NAME_RE.match(name):
            return None
        return self.plans / self.store.board(name)

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
            boards = self.store.boards  # the two names from one look, before the files are listed
            files = sorted(p.name for p in self.plans.glob("*.md") if NAME_RE.match(p.name) and p.name.lower() not in hidden)
            if (self.plans / ARCHIVE_LOG).is_file():
                files.append(ARCHIVE_LOG)
            self.send_json(200, {"dir": str(self.plans), "files": files,
                                 "name": self.store.name, "key": self.store.key, "model": self.store.model, "attach": agent.attach("{id}"), "models": agent.models(), "agent": agent.short(), "agentLabel": agent.label(),
                                 "sessions": self.store.sessions, "pickupColumn": self.store.pickup_column,
                                 "trunk": self.store.trunk, "fullSuiteOnMerge": self.store.full_suite_on_merge, "branches": local_branches(self.store),
                                 "repos": [{"name": r.name, "main": r.main} for r in self.store.repos], "build": BUILD,
                                 "boards": dict(zip(("build", "plan"), boards))})
        elif u.path == "/api/log":  # ?key=PLN-01: the last lines of that tile's session log
            view = log_view(self.store, (qs.get("key") or [""])[0])
            self.send_json(200, view) if view else self.send_json(404, {"error": "no session on that tile"})
        elif u.path == "/log":
            body = LOG_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif u.path == "/api/pickup":
            self.send_json(200, pickup_reply(self.store))
        elif u.path == "/api/settings":
            reload_settings(self.store)
            self.send_json(200, settings.view(self.store))
        elif u.path == "/api/board":
            p = self.target(qs)
            if p is None or not p.is_file():
                return self.send_json(404, {"error": "no such file"})
            self.send_json(200, {"text": p.read_text(encoding="utf-8"), "mtime": mtime(p),
                                 "maxKey": self.max_key(),
                                 "firstCommitted": first_committed(self.store, p) if p.parent == self.plans else {},
                                 "unmerged": unmerged_done(self.store, p.read_text(encoding="utf-8"))
                                 if p.name == self.store.build else {},
                                 "moveTo": move_targets(self.store, p.name) if p.parent == self.plans else {},
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

    def snapshot(self, board: Path) -> reader.Snapshot:
        """The tiles a board's page should know: the two boards and the archive log share one
        snapshot, since a tile's master or sub-stories may be on another of them."""
        name, family = board.relative_to(self.plans).as_posix(), self.store.family
        return reader.Snapshot.read(self.store, family if name in family else [name])

    def linked(self, board: Path) -> dict:
        """The linked tiles a board's page should know: the two boards share their links, since
        a tile's partner may be on the other one."""
        names = list(moves(self.store)) if board.parent == self.plans and board.name in self.store.boards else [board.relative_to(self.plans).as_posix()]
        return reader.Snapshot.read(self.store, names).linked()

    def tiles(self, board: Path) -> dict:
        """The tile index a board's page should know (Snapshot.index)."""
        return self.snapshot(board).index()

    def commit(self, paths, message):
        gitlog.record(self.store, paths, message)

    def archive(self, board: Path):
        """Body: {mtime, text: the board without the tile, tile: its markdown, docs: [names]}."""
        data = self.read_body()
        if data.get("mtime") != mtime(board):
            return self.send_json(409, {"error": "changed on disk",
                                        "text": board.read_text(encoding="utf-8"), "mtime": mtime(board)})
        try:
            reply = archive_tile(self.store, board, data["text"], data["tile"], data.get("docs") or [])
        except (FileExistsError, subprocess.CalledProcessError) as e:
            return self.send_json(409, {"error": str(e)})
        self.send_json(200, reply)

    def decide(self, board: Path):
        """Body: {mtime, text, tile, docs, answer}: see decide_tile."""
        status, reply = decide_tile(self.store, board, self.read_body(), datetime.date.today().isoformat())
        self.send_json(status, reply)

    def refresh_status(self, key: str):
        board = self.plans / pickup_board(self.store)
        old = board.read_text(encoding="utf-8") if board.is_file() else ""
        tile = next((t for t in reader.parse(self.store, old) if t.key == key), None)
        line = pickup.status(self.store, key, tile.text, sessions=SESSIONS) if tile else None
        if line is None:
            return self.send_json(409, {"error": f"{key} has no session to report on"})
        write_atomic(board, reader.with_status(self.store, old, key, line))
        self.commit([board], f"board: {key} status refreshed")
        self.send_json(200, {**pickup_reply(self.store), "status": line})

    def clear(self, board: Path):
        """Body: {mtime, text: the board as the page has it, column}. Archives every tile in
        that column but the Done tiles whose branch isn't merged yet. The reply carries
        "cleared", the record the page hands back to /api/unclear to undo it."""
        data = self.read_body()
        if data.get("mtime") != mtime(board):
            return self.send_json(409, {"error": "changed on disk",
                                        "text": board.read_text(encoding="utf-8"), "mtime": mtime(board)})
        kept = sorted(unmerged_done(self.store, data["text"])) if board.name == self.store.build else []
        try:
            new, record, touched = clear_column(self.store, board, data["text"], data.get("column"), kept)
        except (KeyError, ValueError, FileExistsError, subprocess.CalledProcessError) as e:
            return self.send_json(409, {"error": str(e).strip("'\"")})
        write_atomic(board, new)
        keys = [t["key"] for t in record["tiles"] if t["key"]]
        self.commit([board, *touched], f"board: clear {record['column']}: archive {', '.join(keys) or str(record['count']) + ' tiles'}")
        end_drafts(self.store, keys)
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
                "unmerged": unmerged_done(self.store, text) if board.name == self.store.build else {},
                "moveTo": move_targets(self.store, board.name),
                "linked": self.linked(board), "tiles": self.tiles(board)}

    def move(self, board: Path):
        """Body: {mtime, text: the board without the tiles, tiles: their markdown (or tile, for
        one), column}. The tiles go to the top of that column on the other board (moves), in
        order, each keyed if it has no key. The page sends a tile's linked tiles with it."""
        data = self.read_body()
        if data.get("mtime") != mtime(board):
            return self.send_json(409, {"error": "changed on disk",
                                        "text": board.read_text(encoding="utf-8"), "mtime": mtime(board)})
        targets, column = move_targets(self.store, board.name), data.get("column")
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
                             "moveTo": move_targets(self.store, board.name),
                             "maxKey": self.max_key(), "linked": self.linked(board), "tiles": self.tiles(board)})

    def pull(self, board: Path):
        """Body: {mtime, text: the board after a tile changed column, after: that tile's key,
        keys: the tiles linked to it that aren't on this board}. Saves the board, with any of
        those tiles found on the other board (pulls) moved in straight after that tile."""
        data = self.read_body()
        if data.get("mtime") != mtime(board):
            return self.send_json(409, {"error": "changed on disk",
                                        "text": board.read_text(encoding="utf-8"), "mtime": mtime(board)})
        src = self.plans / pulls(self.store)[board.name]
        old, new, paths, keys = board.read_text(encoding="utf-8"), data["text"], [board], []
        src_text = src.read_text(encoding="utf-8") if src.is_file() else ""
        rest, found = reader.take(self.store, src_text, set(data.get("keys") or []))
        if found:
            tiles = [keyed_tile(self.store, t, "") for t in found]  # found by key, so each has one
            keys = [key for _, key in tiles]
            try:
                new = reader.insert_after(self.store, new, data.get("after"), [tile for tile, _ in tiles])
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
                             "moveTo": move_targets(self.store, board.name),
                             "maxKey": self.max_key(), "linked": self.linked(board), "tiles": self.tiles(board)})

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/api/move":
            p = self.target(parse_qs(u.query))
            if p is None or not p.is_file() or p.parent != self.plans or p.name not in moves(self.store):
                return self.send_json(404, {"error": "only the Build and Plan boards' tiles move between boards"})
            return self.move(p)
        if u.path == "/api/pull":
            p = self.target(parse_qs(u.query))
            if p is None or not p.is_file() or p.parent != self.plans or p.name not in pulls(self.store):
                return self.send_json(404, {"error": "only the Build board pulls linked tiles in"})
            return self.pull(p)
        if u.path == "/api/pickup":  # {key}: start a waiting job now, in parallel; {key, restart: true}: the Restart button; {key, continue: true}: the Continue button; {key, pause: true}: the Pause button
            data = self.read_body()
            if data.get("status"):  # {key, status: true}: write where the story's session and branch stand into its tile
                return self.refresh_status(data.get("key", ""))
            if data.get("discard"):  # {key, discard: true, tile}: the tile is being deleted; throw its work away
                key = data.get("key", "")
                try:
                    done = pickup.discard(self.store, key, data.get("tile") or "", sessions=SESSIONS)
                except (OSError, subprocess.SubprocessError) as e:
                    done = [f"not discarded: {e}"]
                for line in done:
                    pickup.log(f"{key} deleted: {line}")
                end_drafts(self.store, [key] if key else [])
                return self.send_json(200, {**pickup_reply(self.store), "discarded": done})
            why = pickup.why_off(self.store)
            if why:
                return self.send_json(409, {"error": why})
            if "reply" in data:  # {key, reply: text, steps: [squash, merge, push]}: the reply box
                why = pickup.reply(self.store, data.get("key", ""), data.get("reply") or "", data.get("steps") or [], sessions=SESSIONS,
                                   snap=self.snapshot(self.plans / self.store.build))
                if why:
                    return self.send_json(409, {"error": why, **pickup_reply(self.store)})
                return self.send_json(200, pickup_reply(self.store))
            if data.get("queue"):  # {key, queue: true}: the Queue button on a paused story
                if not pickup.requeue(self.store, data.get("key", ""), sessions=SESSIONS):
                    return self.send_json(409, {"error": "that tile has no paused session to queue", **pickup_reply(self.store)})
                return self.send_json(200, pickup_reply(self.store))
            if data.get("pause"):
                if not pickup.pause(self.store, data.get("key", ""), sessions=SESSIONS):
                    return self.send_json(409, {"error": "that tile has no session at work to pause, or it couldn't be stopped", **pickup_reply(self.store)})
                return self.send_json(200, pickup_reply(self.store))
            if data.get("continue"):
                if not pickup.restart(self.store, data.get("key", ""), sessions=SESSIONS, nudge=True):
                    return self.send_json(409, {"error": "that tile's session couldn't be stopped to continue it, or the board didn't start one", **pickup_reply(self.store)})
                return self.send_json(200, pickup_reply(self.store))
            act = pickup.restart if data.get("restart") else pickup.start_now
            if not act(self.store, data.get("key", ""), sessions=SESSIONS):
                return self.send_json(409, {"error": "that tile has no session to start", **pickup_reply(self.store)})
            return self.send_json(200, pickup_reply(self.store))
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
        if u.path == "/api/domain":  # {set: {KEY: name | null}}: file stories into a domain
            p = self.target(parse_qs(u.query))
            if p is None or not p.is_file() or p.parent != self.plans:
                return self.send_json(404, {"error": "no such board"})
            data = self.read_body()
            expect = data.get("expect")  # Undo's {KEY: name | null}: what each tile should say now; anything but a map is ignored
            status, reply = set_domains(self.store, data.get("set"), expect if isinstance(expect, dict) else None)
            return self.send_json(status, {**self.board_reply(p), **reply} if status == 200 else reply)
        if u.path == "/api/combine":  # {keys, into, expect: {KEY: rev}}: combine stories into one
            p = self.target(parse_qs(u.query))
            if p is None or not p.is_file() or p.parent != self.plans:
                return self.send_json(404, {"error": "no such board"})
            data = self.read_body()
            status, reply = combine_stories(self.store, data.get("keys"), data.get("into"), data.get("expect"))
            return self.send_json(status, {**self.board_reply(p), **reply} if status == 200 else reply)
        if u.path == "/api/decide":
            p = self.target(parse_qs(u.query))
            if p is None or not p.is_file() or p.parent != self.plans:
                return self.send_json(404, {"error": "no such board"})
            return self.decide(p)
        if u.path == "/api/draft":  # {key, note}: Draft options or Draft again; {key, stop: true}: Stop
            p = self.target(parse_qs(u.query))
            if p is None or not p.is_file() or p.parent != self.plans:
                return self.send_json(404, {"error": "no such board"})
            status, reply = draft_request(self.store, p, self.read_body())
            return self.send_json(status, reply)
        if u.path == "/api/organize":  # {note, refile}: the map's Ask button
            status, reply = organize_request(self.store, self.read_body())
            return self.send_json(status, reply)
        if u.path == "/api/organize/end":  # Cancel, Discard, Close, and after Apply
            status, reply = organize_end(self.store)
            return self.send_json(status, reply)
        if u.path == "/api/split":  # {key, note}: a card's Ask button under Split
            status, reply = split_request(self.store, self.read_body())
            return self.send_json(status, reply)
        if u.path == "/api/split/end":  # Cancel, Discard, Close
            status, reply = split_end(self.store)
            return self.send_json(status, reply)
        if u.path == "/api/split/create":  # {key, expect: rev, stories: [{title, description, tasks}]}: Create
            p = self.target(parse_qs(u.query))
            if p is None or not p.is_file() or p.parent != self.plans:
                return self.send_json(404, {"error": "no such board"})
            data = self.read_body()
            status, reply = split_story(self.store, data.get("key"), data.get("stories"), data.get("expect"))
            if status != 200:
                return self.send_json(status, reply)
            return self.send_json(status, {**self.board_reply(p), **reply, "split": split.summary(self.store)})
        if u.path == "/api/settings":  # {mtime, changes}: the settings panel's Save
            return self.save_settings(self.read_body())
        self.send_json(404, {"error": "not found"})

    def save_settings(self, data):
        """Write the settings panel's changes to board.json (settings.apply) and answer with
        the settings now: 409 when board.json changed since the page read it, 400 when a change
        is refused."""
        reload_settings(self.store)
        f = self.store.dir / stores.CONFIG
        if data.get("mtime") != (mtime(f) if f.is_file() else ""):
            return self.send_json(409, {"error": "changed on disk", **settings.view(self.store)})
        changes = data.get("changes")
        if not isinstance(changes, dict):
            return self.send_json(400, {"error": "changes must be an object", **settings.view(self.store)})
        try:
            with ledger.Ledger(self.store).locked():  # a look waits for the new settings
                said = settings.apply(self.store, changes)
        except settings.SettingsError as e:
            return self.send_json(400, {"error": str(e), **settings.view(self.store)})
        for line in said:
            pickup.log(f"settings: {line}")
        self.send_json(200, {"said": said, **settings.view(self.store)})


def should_open(no_open: bool, env=None, isatty=None) -> bool:
    """Open the board in the browser on start? Not with --no-open, BOARD_NO_OPEN, or when
    stdout isn't a terminal. env, isatty: os.environ and sys.stdout.isatty, as they are now."""
    env = os.environ if env is None else env
    return not no_open and not env.get("BOARD_NO_OPEN") and (isatty or sys.stdout.isatty)()


def log_view(store, key: str):
    """What the board's log pane shows for a tile: its session's id and state and the log's last
    lines (agent.transcript). None for a tile no session was started on."""
    job = ledger.Ledger(store).latest(key, "id")
    if job is None:
        return None
    text = agent.transcript(job["id"], job.get("session") or pickup.full_id(job["id"]))
    return {"key": key, "id": job["id"], "state": job["state"], "pausing": bool(job.get("pausing")),
            "text": text or "", "found": text is not None}


# The log in a window of its own (/log?key=PLN-01): the same lines, refreshed as the pane is.
LOG_PAGE = """<!doctype html><meta charset="utf-8"><title>Session log</title>
<style>body{margin:0;background:#16181c;color:#d8dee9;font:13px/1.5 ui-monospace,Menlo,monospace}
header{position:sticky;top:0;background:#22262d;padding:8px 14px;font-family:system-ui,sans-serif}
pre{margin:0;padding:12px 14px;white-space:pre-wrap;overflow-wrap:anywhere}</style>
<header id="head">Session log</header><pre id="log"></pre>
<script>
const key = new URLSearchParams(location.search).get("key") || "";
async function load() {
  const r = await fetch("/api/log?key=" + encodeURIComponent(key)), j = await r.json();
  if (!r.ok) { document.getElementById("head").textContent = key + ": " + (j.error || r.status); return; }
  const pinned = innerHeight + scrollY >= document.body.scrollHeight - 40;
  document.title = key + " log";
  document.getElementById("head").textContent = key + " · session " + j.id + " · " + (j.pausing ? "pausing" : j.state);
  document.getElementById("log").textContent = j.found ? j.text : "No transcript found for this session.";
  if (pinned) scrollTo(0, document.body.scrollHeight);
}
load(); setInterval(() => { if (!document.hidden) load().catch(() => {}); }, 3000);
</script>"""


def resume_store(st):
    """What the server does with a store before it serves it: the move to Plan and Build's step
    first (watch_move_turn), so a move that stopped part way commits its renames under its own
    subject, then the commit queue picked up again (gitlog.resume), so changes queued before a
    restart still get committed. The move is made here only when it needs no listing of the
    agent's sessions (renaming.held), which can take a while before the port is bound; else it
    is the watch's first turn's, and resume leaves it the renames of a store part moved."""
    if not renaming.held(st):
        watch_move_turn(st, {"m_before": old_board_mtimes(st)})
    if st.root:
        gitlog.resume(st)


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
    resume_store(st)
    for line in ensure_hooks(st):
        print(line, flush=True)
    why = pickup.why_off(st)  # None: pickup runs
    # The watch runs whether or not pickup does, so turning it on in board.json needs no restart.
    # It lists what is in flight first. Off the main thread: asking the CLI for its sessions takes a second.
    threading.Thread(target=watch_pickups, args=(st,), daemon=True).start()
    port = args.port or st.port
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"{st.name} board on http://127.0.0.1:{port}  (files in {st.dir}, keys {st.key}-NN)")
    if why:
        print(why, flush=True)
    else:
        print(f"Tiles that land in In flight go to {agent.label()} ({st.permission} mode), in worktrees at {pickup.worktrees_at(st)}", flush=True)
    if should_open(args.no_open):
        webbrowser.open(f"http://127.0.0.1:{port}/")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
