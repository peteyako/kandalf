"""The move of a store from the old board names to the new: plans.md becomes build.md (the Build
board) and backlog.md becomes plan.md (the Plan board).

`kandalf serve` moves a store by itself (serve.watch_move_turn), at its start and on each turn
of its watch, when the store is due (due) and nothing blocks it (blocked): no agent session is
in the middle of a turn, and no file of the store's own is in the way. The move (move) renames
the two files, brings the filing rules up to date as `kandalf notes --update` does, gives a
board whose header is still the old template's the new one, points the links between the
store's files at the new names (what those two steps wrote included), queues one commit of all
of it, and last records the move in the ledger.

Every step looks at what is already true rather than what an earlier step did, so a move that
stopped part way (a crash, a full disk) is finished by the next one, and a move run again
changes nothing. It renames, never copies, and never writes over a file that is there: an old
file that comes back beside its new one (a stray) is left alone, and the page (notice) and the
edit hook (checkhook) say so.

A session started before the move was told the old names: what pickup sends it next begins
with one line saying they changed (told).
"""

import os
import re
import stat
import subprocess
import tempfile
import time
from pathlib import Path

from . import gitlog
from . import jobs
from .store import ARCHIVE_LOG, BUILD, OLD_BUILD, OLD_PLAN, PLAN, forget_moved

NEW = {OLD_BUILD: BUILD, OLD_PLAN: PLAN}  # each old board file's new name

IN_THE_WAY = "{name} is in the way: these boards can't be renamed Plan and Build until it is moved or renamed."
UNLISTED = "These boards will be renamed Plan and Build once the agent's sessions can be listed."
UNREADABLE = "These boards will be renamed Plan and Build once board.json can be read."
BUSY = "These boards will be renamed Plan and Build once {key}'s session finishes its turn."
STRAY = "{old} is back in this store, but the board is {new} now. Move anything new into {new} and delete {old}."
TOLD = f"The boards were renamed since you started: {OLD_BUILD} is now {BUILD} and {OLD_PLAN} is now {PLAN}."
RENAMED = f"board: {OLD_BUILD} → {BUILD}, {OLD_PLAN} → {PLAN}"  # the move's commit subject
FINISHED = "board: the move to Plan and Build finished"  # a later run's, finishing one that stopped part way


# ---------- links ----------
_OLD = "|".join(re.escape(name) for name in NEW)
# A link's target that is an old board file: after ./ or any number of ../, with any #fragment.
_TARGET_RE = re.compile(rf"^(\./|(?:\.\./)*)({_OLD})(#\S*)?$")
_LINK_RE = re.compile(r'\[([^\[\]]*)\]\(([^()\s]+)((?:\s+"[^"]*")?)\)')  # [text](target) or [text](target "title")
_REF_RE = re.compile(r"^( {0,3}\[[^\]]+\]:[ \t]*)(\S+)(.*)$")  # a reference definition, [x]: target
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")


def _target(target: str):
    """The new target for a link to an old board file, written bare or in <angle brackets>, else None."""
    bracketed = len(target) > 2 and target[0] == "<" and target[-1] == ">"
    found = _TARGET_RE.match(target[1:-1] if bracketed else target)
    if not found:
        return None
    new = found.group(1) + NEW[found.group(2)] + (found.group(3) or "")
    return f"<{new}>" if bracketed else new


def _link(found) -> str:
    """A [text](target) link with an old board as its target pointed at the new one, and its text
    renamed too when it is the old file's name (in backticks or not)."""
    text, target, title = found.groups()
    new = _target(target)
    if new is None:
        return found.group(0)
    bare = text[1:-1] if len(text) > 2 and text[0] == text[-1] == "`" else text
    if bare in NEW:
        text = text.replace(bare, NEW[bare])
    return f"[{text}]({new}{title})"


def _relink_line(line: str) -> str:
    """One line outside a fenced block with its links relinked; code spans are skipped whole."""
    ref = _REF_RE.match(line)
    if ref:
        new = _target(ref.group(2))
        return line if new is None else ref.group(1) + new + ref.group(3)
    out, i = [], 0
    while i < len(line):
        if line[i] == "[":
            found = _LINK_RE.match(line, i)
            if found:
                out.append(_link(found))
                i = found.end()
                continue
        elif line[i] == "`":
            run = len(line[i:]) - len(line[i:].lstrip("`"))
            close = re.compile(rf"(?<!`)`{{{run}}}(?!`)").search(line, i + run)
            end = close.end() if close else i + run
            out.append(line[i:end])
            i = end
            continue
        out.append(line[i])
        i += 1
    return "".join(out)


def relink(text: str) -> str:
    """The text with each markdown link (and reference definition) whose target is plans.md or
    backlog.md, after an optional ./ or any number of ../ and with any #fragment, pointed at
    build.md or plan.md, and a link's text that is exactly the old file's name renamed with it.
    Prose, code spans and fenced blocks are left as they are. The same text, unchanged, when
    there is nothing to change."""
    lines, fence = text.split("\n"), None
    for n, line in enumerate(lines):
        found = _FENCE_RE.match(line)
        if fence:
            if found and found.group(1)[0] == fence[0] and len(found.group(1)) >= len(fence) and not line.strip().strip(fence[0]):
                fence = None
            continue
        if found:
            fence = found.group(1)
            continue
        lines[n] = _relink_line(line)
    out = "\n".join(lines)
    return text if out == text else out


# ---------- when ----------
def _renamed(store):
    """The ledger's record of the move ({"at": unix seconds}), or None."""
    return jobs.Ledger(store).read().get("renamed")


def _due(store) -> bool:
    """due, whether or not board.json reads."""
    if not store.is_store:
        return False
    build, plan = store.boards
    if build == OLD_BUILD or plan == OLD_PLAN:
        return True
    if not ((store.dir / BUILD).is_file() or (store.dir / PLAN).is_file()):
        return False
    return _renamed(store) is None


def due(store) -> bool:
    """True when the store is to be moved: it has a board.json, and it is on the old names, or half
    moved (build.md beside backlog.md), or on the new names with a board file and no record of
    the move in the ledger (a move that stopped before its last step, or one made by hand). Never
    while board.json doesn't read (store.config_problem): the filing rules would be written from
    the settings before it, or the defaults."""
    return not store.config_problem and _due(store)


def held(store) -> list:
    """(key, session id) of each job with a session (jobs.TRACKED) and each draft at work, jobs
    first: the sessions blocked asks about. [] means the move needs no listing of sessions."""
    from . import drafts
    found = [(j["key"], j["id"]) for j in jobs.Ledger(store).read()["jobs"] if j["state"] in jobs.TRACKED and j.get("id")]
    return found + [(d["key"], d["id"]) for d in drafts.live(store) if d.get("state") == "working" and d.get("id")]


def blocked(store, *, sessions) -> str | None:
    """Why the store can't be moved now, as a sentence for the page, or None. In this order: a
    store on the old names with a build.md or a plan.md of its own (build.md named when both are);
    then, when the ledger holds a job with a
    session (jobs.TRACKED) or a draft at work, the sessions as listed (sessions.sessions(), asked
    once): none listed blocks, and so does one listed as busy (the first such job's key, jobs
    before drafts). An idle session, or one waiting for a person, writes nothing until it is
    spoken to, so it doesn't block. With no such job or draft the sessions aren't listed."""
    if store.boards == (OLD_BUILD, OLD_PLAN):
        mine = next((name for name in (BUILD, PLAN) if (store.dir / name).exists()), None)
        if mine:
            return IN_THE_WAY.format(name=mine)
    asked = held(store)
    if not asked:
        return None
    listed = sessions.sessions()
    if listed is None:
        return UNLISTED
    busy = next((key for key, sid in asked if listed.get(sid) == "busy"), None)
    return BUSY.format(key=busy) if busy else None


# ---------- the move ----------
def _tracked(store, path: Path) -> bool:
    return subprocess.run(["git", "-C", str(store.root), "ls-files", "--error-unmatch", str(path)],
                          capture_output=True).returncode == 0


def _rename(store, old: str, new: str):
    """Rename a store file: `git mv` when the store is in a git repo and the file is tracked, else
    (or when git mv fails) a plain rename. Never onto a file that is there."""
    src, dst = store.dir / old, store.dir / new
    if store.root and _tracked(store, src):
        moved = subprocess.run(["git", "-C", str(store.root), "mv", str(src), str(dst)], capture_output=True)
        if moved.returncode == 0 or (not src.exists() and dst.is_file()):
            return
    if dst.exists():
        raise FileExistsError(f"{new} is there already")
    os.replace(src, dst)


def _write(store, path: Path, text: str):
    """Write a file whole, by a rename, so a reader never sees half of it; its mode is kept. A file
    that holds the text already is left alone. One that can't be written raises OSError naming it
    (as a path in the store), which stops the move before its commit."""
    if path.read_text(encoding="utf-8") == text:
        return
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".board-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, stat.S_IMODE(path.stat().st_mode))
        os.replace(tmp, path)
    except OSError as e:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)
        raise OSError(f"{path.relative_to(store.dir).as_posix()} could not be written: {e}") from e


def _linked_files(store, build: str, plan: str) -> list:
    """What the links are rewritten in: the two boards, the archive log and the plan docs in
    active/ and archive/, as paths in the store."""
    names = [build, plan, ARCHIVE_LOG]
    for folder in ("active", "archive"):
        names += sorted(f"{folder}/{p.name}" for p in (store.dir / folder).glob("*.md") if p.is_file())
    return list(dict.fromkeys(names))


def _uncommitted(store, docs) -> list:
    """What a move that stopped part way left uncommitted, for the run that finishes it to commit:
    a board file, old name or new, whose state git hasn't committed (an old board gone from disk,
    a new one changed or new), and a file among docs (store paths: the archive log and the plan
    docs) changed only by relinking (its committed text, relinked, is what it holds now). A doc
    with other changes too is left for the commit queue. [] for a store that isn't in a git repo."""
    if not store.root:
        return []
    boards = [store.dir / name for name in (*NEW, *NEW.values())]
    others = [store.dir / name for name in docs]
    out = subprocess.run(["git", "-C", str(store.root), "status", "--porcelain", "-z", "--untracked-files=all", "--",
                          *map(str, boards + others)], capture_output=True, text=True).stdout.split("\0")
    dirty, i = set(), 0
    while i < len(out):
        entry = out[i]
        i += 1
        if len(entry) < 4:
            continue
        dirty.add(entry[3:])
        if entry[0] in "RC" and i < len(out):  # -z puts a rename's old path in the next field
            dirty.add(out[i])
            i += 1
    root = store.root.resolve()
    rel = lambda path: path.relative_to(root).as_posix()  # noqa: E731
    found = [p for p in boards if rel(p) in dirty and (not p.exists() if p.name in NEW else p.is_file())]
    for path in others:
        if rel(path) in dirty and path.is_file():
            was = gitlog.head_text(store.root, rel(path))
            if was and relink(was) is not was and relink(was) == path.read_text(encoding="utf-8"):
                found.append(path)
    return found


def move(store, *, sessions, record=gitlog.record) -> list:
    """Move the store onto the new names when it is due and nothing blocks it, under the ledger's
    lock, so no session is started or spoken to meanwhile. First what the commit queue holds is
    committed, on its own, when nothing is renamed yet. Then, in order, each step skipped where it
    is done already: plans.md renamed build.md, backlog.md renamed plan.md (a rename that fails
    stops the move there); the filing rules and the agents' notes brought up to date as `kandalf
    notes --update` does; a board whose header is still the old template's given the new one; the
    links relinked in the two boards, the archive log and every plan doc (a file that can't be
    written stops the move there, naming it); one commit queued through
    record of every path touched, the renamed files' old paths too (in a store that is a git
    repo); then, last, the move recorded in the ledger. The lines to log; [] when it did nothing."""
    if not due(store):  # a folder that isn't a store gets nothing, not even the ledger's lock file
        return []
    with jobs.Ledger(store).locked():
        if not due(store) or blocked(store, sessions=sessions):
            return []
        return _move(store, record)


def _move(store, record) -> list:
    from . import init
    folder, touched, said = store.dir, [], []
    # A store not yet renamed at all first commits what is queued, on its own, so that no commit of
    # an old board's queued edit lands between the renames and the move's (it would delete the
    # board with no file to take its place). A store part moved keeps its queue: what it holds
    # (gitlog.resume's finds after a restart) is the move's own, and joins its commit.
    if store.root and store.boards == (OLD_BUILD, OLD_PLAN):
        with gitlog.queue(store.root) as q:
            gitlog.flush(store, q)
    renamed = []
    for old, new in NEW.items():
        if (folder / old).is_file() and not (folder / new).exists():
            _rename(store, old, new)
            renamed.append(f"{old} is now {new}")
            touched += [folder / old, folder / new]
    if renamed:
        said.append(" and ".join(renamed) + ": the boards are Plan and Build")
    build, plan = store.boards
    board = folder / build
    before = board.read_text(encoding="utf-8") if board.is_file() else None
    # Of what the header's refresh says, only the rules it added: its "left as you have them" is
    # about rules whose links the relinking below rewrites, in a header the next step may replace.
    said += [line for line in init.refresh_header(store, True, commit=False) if " added to the header" in line]
    for repo in store.repos:
        if repo.path.is_dir():
            said += init.refresh_notes(repo.path, store.link, True)
    if before is not None and board.read_text(encoding="utf-8") != before:
        touched.append(board)
    headers = init.board_headers(store)
    for kind, name in (("build", build), ("plan", plan)):
        f = folder / name
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8")
        head, (old, new) = init.header_of(text), headers[kind]
        if head != new and relink(head) == relink(old):
            _write(store, f, new + text[len(head):])
            said.append(f"the header of {name} now says Plan and Build")
            touched.append(f)
    # After the notes update and the headers, so what they wrote is relinked too.
    linked, relinked = _linked_files(store, build, plan), []
    for name in linked:
        f = folder / name
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8")
        new_text = relink(text)
        if new_text is not text:
            _write(store, f, new_text)
            relinked.append(name)
            touched.append(f)
    if relinked:
        said.append("links to the boards now point at their new names in " + ", ".join(relinked))
    docs = [name for name in linked if name not in (build, plan)]
    paths = list(dict.fromkeys([*touched, *_uncommitted(store, docs)]))
    if paths and store.root:
        subject = RENAMED if any(p.name in NEW and p.parent == folder for p in paths) else FINISHED
        record(store, paths, subject, subject=True)
        said.append(f'the move to Plan and Build is done: "{subject}" is queued to commit')
    else:
        said.append("the move to Plan and Build is done")
    # Last of all: a move that stops before this is due again, and the run that finishes it
    # finds everything done, queues what is still uncommitted (nothing, once committed) and records it.
    if store.boards == (BUILD, PLAN):
        with jobs.Ledger(store).change() as state:
            state.setdefault("renamed", {"at": int(time.time())})
        forget_moved()
    return said


# ---------- what is said ----------
def notice(store, *, sessions) -> dict | None:
    """What the page says about the move: {"stray": [names], "text": sentence} while an old board
    file is back beside its new one (store.strays), else {"waiting": sentence} while a move would
    be due but board.json doesn't read, or is due and blocked; None otherwise."""
    strays = store.strays
    if strays:
        return {"stray": strays, "text": " ".join(STRAY.format(old=name, new=NEW[name]) for name in strays)}
    if store.config_problem:
        return {"waiting": UNREADABLE} if _due(store) else None
    if due(store):
        why = blocked(store, sessions=sessions)
        if why:
            return {"waiting": why}
    return None


def told(store, job: dict) -> str:
    """The line for a session whose job started before the store was moved (TOLD), else "". A job
    never started is told nothing. Both times are whole seconds, so a session started in the
    second the move was made is told too: an extra line, never a missing one."""
    renamed = _renamed(store)
    if not renamed or not job.get("started"):
        return ""
    return TOLD if job["started"] <= renamed["at"] else ""
