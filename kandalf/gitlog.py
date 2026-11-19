"""Commit board changes to the plans repo, batched, and push each commit.

A change is saved to disk at once but committed later: the board server and the
Claude Code hook (`kandalf hook autocommit`) call record(), which queues the paths it
touched in the store's .git/board-pending.json and makes sure one background committer is
running. The committer waits until the store's commit_delay() seconds have passed
with no new change ("commitDelay" in board.json, or BOARD_COMMIT_DELAY; 300 by default),
then commits everything queued as one commit and pushes it (unless the store's
board.json says "push": false). So a burst of tile
moves or edits lands as one commit, not a dozen tiny ones.

The commit subject describes the net change since the last commit (a tile moved
there and back again names nothing); the body lists each recorded step. Commits
take only the queued paths, so one session never sweeps up another session's
half-done edit that it didn't record.

    kandalf commit now        # commit and push whatever is queued, right away
    kandalf commit status     # what is queued, and when it will commit
"""

import fcntl
import json
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from . import store as stores

BOARDS = ("plans.md", "backlog.md", "archive/archived.md")  # relative to the store
SUBJECT_MAX = 72


def git_dir(root: Path) -> Path:
    return Path(subprocess.run(["git", "-C", str(root), "rev-parse", "--absolute-git-dir"],
                               capture_output=True, text=True).stdout.strip())


def tiles(store, text: str) -> dict:
    """{key: [column, block]} for each keyed top-level tile; block is its line plus indented lines."""
    out, col, key = {}, None, None
    for line in text.split("\n"):
        if line.startswith("## "):
            col, key = line[3:].strip(), None
        elif m := store.tile_head_re.match(line):
            key = m.group(1)
            out[key] = [col, line]
        elif line.startswith("- "):
            key = None
        elif key and line.startswith((" ", "\t")):
            out[key][1] += "\n" + line
        elif line.strip():
            key = None
    return out


def tile_changes(store, name: str, old: str, new: str) -> list:
    """The tiles that differ between two versions of a board file, as short phrases."""
    a, b = tiles(store, old), tiles(store, new)
    parts = []
    for k in sorted(set(a) | set(b), key=store.num):
        if k not in a:
            parts.append(f"{k} added to {b[k][0]}")
        elif k not in b:
            parts.append(f"{k} off {name}")
        elif a[k][0] != b[k][0]:
            parts.append(f"{k} {a[k][0]} → {b[k][0]}")
        elif a[k][1] != b[k][1]:
            parts.append(f"{k} edited")
    return parts


def describe(store, name: str, old: str, new: str) -> str:
    """A one-line note for a board file going from old to new, naming the tiles that changed."""
    parts = tile_changes(store, name, old, new)
    return "board: " + (", ".join(parts) if parts else f"edit {name}")


def head_text(root: Path, rel: str) -> str:
    """The file as last committed, or "" if it is new."""
    r = subprocess.run(["git", "-C", str(root), "show", f"HEAD:{rel}"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def summarize(store, rels) -> str:
    """A commit subject for the net change in these paths (relative to the git root) since HEAD."""
    root = store.root
    parts, docs = [], []
    srels = {store.rel(r) for r in rels}
    for rel in rels:
        f, srel = root / rel, store.rel(rel)
        now = f.read_text(encoding="utf-8") if f.is_file() else ""
        if srel in BOARDS:
            parts += tile_changes(store, Path(rel).name, head_text(root, rel), now)
        elif f.is_file() and not (srel and srel.startswith("archive/") and f"active/{Path(rel).name}" in srels):
            docs.append(rel)  # a doc moved by an archive is covered by "PLN-NN archived"
    # A tile archived shows as "off plans.md" plus "added to Archived": say it once.
    archived = {p.split()[0] for p in parts if p.endswith("added to Archived")}
    parts = [p for p in parts if not (p.split()[0] in archived and " off " in p)]
    parts = [f"{p.split()[0]} archived" if p.split()[0] in archived else p for p in parts]
    # A tile moved between boards (promoted from backlog.md) shows as "off <file>" plus
    # "added to <column>": say it once, as "PLN-NN <file> → <column>".
    offs = {p.split()[0]: p.split(" off ", 1)[1] for p in parts if " off " in p}
    adds = {p.split()[0]: p.split(" added to ", 1)[1] for p in parts if " added to " in p}
    moved = offs.keys() & adds.keys()
    merged = []
    for p in parts:
        key = p.split()[0]
        if key in moved and " added to " in p:
            merged.append(f"{key} {offs[key]} → {adds[key]}")
        elif not (key in moved and " off " in p):  # the arrow above already names the file
            merged.append(p)
    parts = merged
    parts += [f"{'edit' if head_text(root, d) else 'add'} {d}" for d in docs]
    if not parts:
        return "board: edit " + ", ".join(rels)
    subject = "board: " + ", ".join(parts)
    if len(subject) > SUBJECT_MAX:
        shown = []
        for p in parts:
            if len("board: " + ", ".join(shown + [p])) + 12 > SUBJECT_MAX:
                break
            shown.append(p)
        subject = "board: " + ", ".join(shown or parts[:1]) + f" and {len(parts) - len(shown or parts[:1])} more"
    return subject


def push(root: Path):
    """Push the current branch to its upstream in the background, so a slow or absent
    network never holds anything up. A failed push is logged to .git/board-push.log;
    the next commit's push carries everything still unpushed, so nothing is lost."""
    up = subprocess.run(["git", "-C", str(root), "rev-parse", "--abbrev-ref", "@{upstream}"],
                        capture_output=True, text=True)
    if up.returncode != 0:  # no remote or no upstream: nothing to push to
        return
    log = open(git_dir(root) / "board-push.log", "a")
    log.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} push\n")
    log.flush()
    subprocess.Popen(["git", "-C", str(root), "push", "-q"], stdin=subprocess.DEVNULL,
                     stdout=log, stderr=log, start_new_session=True)


def commit(root: Path, paths, message: str) -> bool:
    """Stage and commit just these paths (repo-relative or absolute). Retries briefly on
    a held index lock, since several sessions share this repo. Returns True if a commit
    was made; nothing to commit is not an error."""
    rels = sorted({str(Path(p).resolve().relative_to(root.resolve())) if Path(p).is_absolute() else str(p)
                   for p in paths})
    if not rels:
        return False
    # A path already gone from disk (deleted, or moved by `git mv`) can't be `git add`ed:
    # stage its removal instead, which is a no-op when git mv already did.
    present = [r for r in rels if (root / r).exists()]
    gone = [r for r in rels if not (root / r).exists()]
    stage = ([["add", "-A", "--", *present]] if present else []) + \
            ([["rm", "-q", "--cached", "--ignore-unmatch", "--", *gone]] if gone else [])
    for args in stage:
        for attempt in range(5):
            r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
            if r.returncode == 0:
                break
            if "index.lock" not in r.stderr:
                return False
            time.sleep(0.2 * (attempt + 1))
        else:
            return False
    # Commit only paths git knows (in HEAD or the index), or the pathspec fails.
    known = subprocess.run(["git", "-C", str(root), "ls-files", "--with-tree=HEAD", "--", *rels],
                           capture_output=True, text=True).stdout.split("\n")
    rels = [r for r in rels if r in known]
    if not rels:
        return False
    diff = subprocess.run(["git", "-C", str(root), "diff", "--cached", "--quiet", "--", *rels])
    if diff.returncode == 0:
        return False
    for attempt in range(5):
        c = subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", message, "--", *rels],
                           capture_output=True, text=True)
        if c.returncode == 0:
            return True
        if "index.lock" not in c.stderr:
            return False
        time.sleep(0.2 * (attempt + 1))
    return False


# --- the queue and its committer --------------------------------------------------

@contextmanager
def queue(root: Path):
    """The pending queue, read and written under an exclusive lock:
    {"paths": [...], "steps": [...], "last": unix time of the latest change, "pid": committer}."""
    gd = git_dir(root)
    with open(gd / "board-pending.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        f = gd / "board-pending.json"
        try:
            q = json.loads(f.read_text())
        except (OSError, ValueError):
            q = {}
        q.setdefault("paths", [])
        q.setdefault("steps", [])
        yield q
        f.write_text(json.dumps(q, indent=1))


def alive(pid) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def ensure_committer(store, q: dict):
    """Start the detached committer unless one is already running. Call with the queue locked."""
    if q["paths"] and not alive(q.get("pid")):
        # python -m kandalf.gitlog, run from the folder that holds the package, so it imports this copy
        top = str(Path(__file__).resolve().parent.parent)
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [top, os.environ.get("PYTHONPATH")]))}
        p = subprocess.Popen([sys.executable, "-m", f"{__package__}.gitlog", "wait", str(store.dir)],
                             cwd=top, env=env,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        q["pid"] = p.pid


def record(store, paths, message: str):
    """Queue a change for the next commit and make sure a committer is waiting for it."""
    root = store.root
    if not root:
        return
    rels = [str(Path(p).resolve().relative_to(root.resolve())) if Path(p).is_absolute() else str(p) for p in paths]
    with queue(root) as q:
        q["paths"] = sorted(set(q["paths"]) | set(rels))
        q["steps"].append(f"{time.strftime('%H:%M:%S')} {message}")
        q["last"] = time.time()
        ensure_committer(store, q)


def dirty_board_files(store) -> list:
    """Uncommitted .md files in the board's places: the boards, active/ and archive/.
    A staged rename (an archive's git mv) gives both its paths."""
    root = store.root
    out = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "-z", "--untracked-files=all"],
                         capture_output=True, text=True).stdout.split("\0")
    rels, i = [], 0
    while i < len(out):
        e = out[i]
        i += 1
        if len(e) < 4:
            continue
        rels.append(e[3:])
        if e[0] in "RC" and i < len(out):  # -z puts a rename's old path in the next field
            rels.append(out[i])
            i += 1
    def ours(r):
        s = store.rel(r)
        return s is not None and r.endswith(".md") and \
            (s in BOARDS or (s.startswith(("active/", "archive/")) and s.count("/") == 1))
    return [r for r in rels if ours(r)]


def resume(store):
    """On board server start: pick up changes queued before a shutdown, crash or reboot,
    and any board files changed on disk while nothing was watching. The timer runs from
    the latest change (a file's last write, for the ones found on disk), so a change older
    than the store's commit_delay() commits and pushes at once and a newer one waits out the rest."""
    root = store.root
    found = dirty_board_files(store)
    with queue(root) as q:
        new = [r for r in found if r not in q["paths"]]
        if new:
            q["paths"] = sorted(set(q["paths"]) | set(new))
            q["steps"].append(f"{time.strftime('%H:%M:%S')} found on start: {', '.join(new)}")
            mtimes = [(root / r).stat().st_mtime for r in new if (root / r).exists()]
            q["last"] = max([q.get("last", 0), *mtimes]) if mtimes else q.get("last", time.time())
        ensure_committer(store, q)


def flush(store, q: dict) -> bool:
    """Commit everything queued in q, then empty it, and push the commit unless the store
    says "push": false. Call with the queue locked."""
    root = store.root
    if not q["paths"]:
        return False
    subject = summarize(store, q["paths"])
    body = "\n".join(q["steps"])
    made = commit(root, q["paths"], f"{subject}\n\n{body}" if len(q["steps"]) > 1 else subject)
    q["paths"], q["steps"] = [], []
    if made and store.push:
        push(root)
    return made


def wait(store):
    """The committer: sleep until the store's commit_delay() seconds after the latest change,
    commit, and exit once the queue is empty. Runs detached; record() starts it when none is
    alive, as its own process, so it reads the store's board.json afresh."""
    delay = store.commit_delay()
    while True:
        with queue(store.root) as q:
            if not q["paths"]:
                if q.get("pid") == os.getpid():
                    q["pid"] = None
                return
            due = q.get("last", 0) + delay - time.time()
            if due <= 0:
                flush(store, q)
                continue
        time.sleep(min(due, delay))


def main(store, args):
    cmd = args[0] if args else "status"
    root = store.root
    if not root:
        raise SystemExit(f"{store.dir} isn't in a git repo: nothing to commit")
    if cmd == "now":
        with queue(root) as q:
            n = len(q["paths"])
            made = flush(store, q)
            print(("committed and pushing" if store.push else "committed") if made else "nothing to commit",
                  f"({n} queued paths)")
    elif cmd == "status":
        with queue(root) as q:
            if not q["paths"]:
                print("nothing queued")
            else:
                left = max(0, int(q.get("last", 0) + store.commit_delay() - time.time()))
                print(f"{len(q['paths'])} paths queued, committing in {left // 60}m{left % 60:02d}s"
                      f" (committer {'running' if alive(q.get('pid')) else 'NOT running'}):")
                print("\n".join("  " + s for s in q["steps"]))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":  # the detached committer: python -m kandalf.gitlog wait <store dir>
    sys.dont_write_bytecode = True
    if len(sys.argv) == 3 and sys.argv[1] == "wait":
        wait(stores.Store(sys.argv[2]))
    else:
        raise SystemExit(__doc__)
