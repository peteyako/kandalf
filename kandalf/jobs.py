"""The job ledger: board-pickup.json, the board's record of each pickup job, kept in the store's
.git beside the commit queue. Every change to it is one transaction (Ledger.change), locked in
this process and, where fcntl is there, across processes, so `kandalf agent` and a running
server never lose each other's writes. Reads (Ledger.read and the queries) take no lock: a save
replaces the file whole.

The state file:
{"inflight": [keys] or None before the first look,
 "umbrellas": [the in-flight masters with sub-stories in flight, as last seen],
 "jobs": [{"key", "title", "with": [sub-story keys], "text": the tiles' markdown, "at",
           "state": "waiting" | "started" (at work) | "idle" | "lost" | "paused" | "ended" | "failed",
           "behind": the key it waits for, "model", "id", "session": the full session id,
           "branch", "worktree", "started", "error",
           "closing": told to merge, so it may move its own tile to Done,
           "master", "limit": a master's sub-story, and how many of them work at once}]}

A job changes state only through the transitions below (queue, requeue, unqueue, begin, saw,
pausing, paused, lose, end, close, mark_ready). Each holds its rule, raising ValueError on a job
it isn't for, and returns the line to say on the console, or None when there is nothing to say.
"""

import copy
import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows: the ledger is locked in this process only
    fcntl = None

from . import agent
from . import gitlog

STATE = "board-pickup.json"
KEEP = 30  # jobs remembered once they are done with

TRACKED = ("started", "idle", "lost")  # a session was started and its tile is still in flight
STOPPED = ("idle", "lost", "failed")  # a job Restart can get going again
KEPT = ("waiting", "paused", *TRACKED)  # a tile with one of these keeps it: it isn't picked up again

# Seconds a session just started counts as at work, before it has shown itself busy.
SETTLE = int(os.environ.get("BOARD_PICKUP_SETTLE", "90"))
WAITING = {"interrupted": "it was interrupted", "question": "it asked a question", "error": "it ended on an error"}

# One lock for the whole process, whichever module built the Ledger. Reentrant: a change made
# while the lock is held (the settings route switching the agent) joins what holds it.
_lock = threading.RLock()
_flocks = {}  # lock file path: [depth, open file], while this process holds it
_open = {}  # state file path: the state of the outermost change in progress on it


def state_path(store) -> Path:
    """In the store's .git, beside the commit queue; a store that isn't a repo keeps it as a dotfile."""
    if store.root:
        return gitlog.git_dir(store.root) / STATE
    return store.dir / ("." + STATE)


def _loaded(path: Path) -> dict:
    """The state in the file at path, as any kandalf wrote it: a file from before "inflight"
    was kept reads as not yet looked at."""
    state = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    state.setdefault("inflight", None)
    state.setdefault("jobs", [])
    return state


def _saved(path: Path, state: dict):
    """Write the state, remembering the last KEEP jobs that are done with, and every job a tile
    still holds (KEPT: queued, paused or with a session), however old."""
    jobs = state["jobs"]
    done = [i for i, j in enumerate(jobs) if j["state"] not in KEPT]
    forgot = set(done[:-KEEP]) if len(done) > KEEP else set()
    state["jobs"] = [j for i, j in enumerate(jobs) if i not in forgot]
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def load(store) -> dict:
    """The store's state, unlocked: for tests that look at it. Code reads through a Ledger."""
    return _loaded(state_path(store))


def save(store, state: dict):
    """Write the store's state, unlocked: for tests that set one up. Code changes it through
    Ledger.change."""
    _saved(state_path(store), state)


class Ledger:
    """A store's board-pickup.json: one locked transaction to change it, and the queries made of it."""

    def __init__(self, store):
        self.store = store
        self.path = state_path(store)

    @contextmanager
    def locked(self):
        """Hold the ledger's lock without changing anything: a look, or a change in another
        process, waits until it is let go. Only the outermost hold in this process takes the
        file lock (board-pickup.lock, beside the state file)."""
        with _lock:
            where = str(self.path.with_suffix(".lock").resolve())
            held = _flocks.get(where)
            if held is None:
                f = None
                if fcntl is not None:
                    try:
                        f = open(where, "a")
                    except OSError:  # nowhere to put it: nor can the state be saved there
                        f = None
                    if f is not None:
                        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                held = _flocks[where] = [0, f]
            held[0] += 1
            try:
                yield
            finally:
                held[0] -= 1
                if held[0] == 0:
                    del _flocks[where]
                    if held[1] is not None:
                        fcntl.flock(held[1].fileno(), fcntl.LOCK_UN)
                        held[1].close()

    @contextmanager
    def change(self):
        """The one transaction: `with ledger.change() as state:` locks the ledger, loads the
        state, and saves it when the block ends, if it changed (pruned to KEEP jobs done with).
        A block that raises saves nothing. A change inside a change on the same store is part
        of it: it is given the same state, and the outermost saves."""
        with self.locked():
            where = str(self.path.resolve())
            if where in _open:
                yield _open[where]
                return
            state = _loaded(self.path)
            before = copy.deepcopy(state)
            _open[where] = state
            try:
                yield state
            finally:
                del _open[where]
            if state != before:
                _saved(self.path, state)

    def read(self) -> dict:
        """The state as it is on disk now, without the lock."""
        return _loaded(self.path)

    def _jobs(self) -> list:
        return self.read()["jobs"]

    def latest(self, key: str, *fields):
        """The last job on a tile, or the last one that has each of fields (say "id": the last
        one a session was started for). None when there is none."""
        return next((j for j in reversed(self._jobs()) if j["key"] == key and all(j.get(f) for f in fields)), None)

    def live(self) -> list:
        """The jobs a tile still holds (KEPT): queued, paused or with a session."""
        return [j for j in self._jobs() if j["state"] in KEPT]

    def busy(self) -> bool:
        """True while a job waits its turn or has a session on a tile in flight (TRACKED)."""
        return any(j["state"] == "waiting" or j["state"] in TRACKED for j in self._jobs())

    def with_branch(self) -> list:
        """The jobs that cut a feature branch, oldest first."""
        return [j for j in self._jobs() if j.get("branch")]


# ---------- the transitions ----------
def _not_for(job, what: str):
    raise ValueError(f"{job['key']} is {job['state']}: {what}")


def _drop(state: dict, job: dict):
    state["jobs"][:] = [j for j in state["jobs"] if j is not job]


def queue(state: dict, fields: dict, now: float):
    """Put a tile that arrived in In flight at the back of the queue, waiting its turn. One tile,
    one session: a tile that already has a live job (KEPT: queued, paused or with a session)
    isn't queued again, however it came to look newly arrived, and the line says so."""
    key = fields["key"]
    if any(j["key"] == key and j["state"] in KEPT for j in state["jobs"]):
        return f"{key} is in flight again and keeps the session it has"
    state["jobs"].append({**fields, "state": "waiting", "at": int(now)})
    return None


def requeue(state: dict, job: dict, now: float, tile: dict = None) -> str:
    """Put a paused job at the back of the queue, to be taken over from its handoff by a new
    session in turn (the Queue button). tile: the job as its tile stands now (entered), when it
    came back to In flight: a sub-story or not, as it is now."""
    if job["state"] != "paused":
        _not_for(job, "only a paused job is queued again")
    if tile is not None:
        for k in ("master", "limit", "of", "context", "base"):
            job.pop(k, None)
        job.update(tile)
    job.update(state="waiting", again="paused", at=int(now))
    _drop(state, job)
    state["jobs"].append(job)
    if tile is not None:
        return f"{job['key']} is in flight again: a new session takes it over from its handoff in turn"
    return f"{job['key']} is queued again: a new session takes it over from its handoff in turn"


def unqueue(state: dict, job: dict) -> str:
    """A waiting job whose tile left In flight before its turn: one that was paused before it
    was queued (again) is paused again; any other is dropped from the queue."""
    if job["state"] != "waiting":
        _not_for(job, "only a waiting job leaves the queue")
    if job.pop("again", None):
        job["state"] = "paused"
        return f"{job['key']} left In flight before its turn: paused again"
    _drop(state, job)
    return f"{job['key']} left In flight before its turn: dropped from the queue"


def begin(job: dict, launched: dict, now: float, how: str = "picked up") -> str:
    """A session was launched for the job: launched is what start or resume gave (started, with
    the session id, or failed, with the error), set on the job, stamped with when. how: what was
    done, for the console. An ended job is done with, and never begun again."""
    if job["state"] == "ended":
        _not_for(job, "an ended job is done with")
    job.update(launched)
    job.pop("behind", None)
    job["started"] = int(now)
    name = f"{job['key']} \"{job['title']}\""
    if job["state"] != "started":
        return f"{name} could not be started: {job.get('error')}"
    where = f", branch {job['branch']} in {job['worktree']}" if job.get("worktree") else ""
    return f"{name} {how}: session {job['id']} ({agent.attach(job['id'])}){where}"


def pauses_on_leaving(job: dict, seen, in_flight: bool, now: float) -> bool:
    """True when a session at work whose tile left In flight is to be paused, as the Pause button
    does: one that is busy, or just started (SETTLE). Not one already pausing, nor one told to
    merge (closing): that one may move its own tile to Done, and is left to finish."""
    gone = not in_flight and job["state"] == "started" and not job.get("pausing") and not job.get("closing")
    return gone and (seen == "busy" or now - job.get("started", 0) < SETTLE)


def saw(job: dict, seen, in_flight: bool, now: float, why):
    """Set a tracked job's state from what its session is doing now: seen is "busy", "idle",
    "waiting" (blocked mid-turn on your input: idle, waiting on a question), or None when the
    session isn't there any more. An idle one is asked (why, by id) whether it waits for
    input: job["waiting"] is then "interrupted" or "question". A pausing one that stops is
    paused, in or out of In flight."""
    if now - job.get("started", 0) < SETTLE:  # just launched: it may not show as busy yet
        return None
    was, waited = job["state"], job.get("waiting")
    if job.get("pausing"):  # writing its handoff; once it stops it is paused, in or out of In flight
        if seen == "busy":
            job["state"] = "started"
            return None
        return paused(job, ": Continue on its tile, or moving it back to In flight, has a new session take over from its handoff")
    if seen == "busy":
        job["state"] = "started"
    elif not in_flight:  # its tile moved on, and so has the session
        job["state"] = "ended"
    else:
        job["state"] = "idle" if seen in ("idle", "waiting") else "lost"
        job.pop("closing", None)  # it stopped with its tile still in flight: the merge is behind it
    waiting = why(job.get("id")) if job["state"] == "idle" else None
    if seen == "waiting" and job["state"] == "idle" and not waiting:
        waiting = "question"  # the CLI says it is blocked on input, whatever its screen shows
    if waiting:
        job["waiting"] = waiting
    else:
        job.pop("waiting", None)
    name = f"{job['key']} session {job.get('id')}"
    if waiting and waiting != waited:
        return f"{name} is waiting for your input ({WAITING[waiting]})"
    if job["state"] != was:
        said = {"started": "is at work again", "ended": "is finished with: its tile left In flight",
                "idle": "is idle: it finished its turn",
                "lost": "is gone (stopped, closed or removed): Continue is on its tile"}[job["state"]]
        return f"{name} {said}"
    return None


def pausing(job: dict):
    """A session at work, resumed told to write its handoff and stop: it is pausing until it
    stops (saw). Only a session at work is paused this way."""
    if job["state"] != "started":
        _not_for(job, "only a session at work writes a handoff to pause")
    job["pausing"] = True


def paused(job: dict, said: str) -> str:
    """Put a story aside: its tile gives up its place until Continue, or the Queue button, has
    a new session take over from its handoff. For a session at work, or one writing its handoff
    (pausing). said: what follows "is paused" on the console."""
    if job["state"] != "started" and not job.get("pausing"):
        _not_for(job, "only a session at work, or one writing its handoff, is paused")
    job.pop("pausing", None)
    job.pop("waiting", None)
    job["state"] = "paused"
    return f"{job['key']} session {job.get('id')} is paused{said}"


def lose(job: dict):
    """A session that was started is gone: Continue on its tile resumes it."""
    if job["state"] not in TRACKED:
        _not_for(job, "only a session that was started is lost")
    job["state"] = "lost"
    job.pop("waiting", None)


def end(job: dict):
    """A job is done with: its story was archived or deleted. Open to a job in any state, and
    final (begin refuses it)."""
    job["state"] = "ended"


def close(job: dict, closing: bool = True):
    """A session at work told to merge (closing): it may move its own tile to Done, and its tile
    leaving In flight doesn't pause it (pauses_on_leaving). closing False: told something else,
    and the mark goes."""
    if job["state"] != "started":
        _not_for(job, "only a session at work is told to merge")
    if closing:
        job["closing"] = True
    else:
        job.pop("closing", None)


def mark_ready(job: dict, note):
    """Keep job["ready"] in step with its tile's "Ready for review" line (note: what follows it,
    None when the tile has none), and say when it appears."""
    if note is None:
        job.pop("ready", None)
    elif not job.get("ready"):
        job["ready"] = True
        return f"{job['key']} is ready for review" + (f": {note}" if note else "")
    return None


# ---------- what the page shows ----------
SHOWN = {"idle": "idle", "lost": "gone", "paused": "paused"}  # a session that isn't at work, as its tile says it


def view(job: dict) -> dict:
    """What the page shows of a job: {"label": a word or two for its session's state ("" when the
    pill beside the key says it), "actions": the buttons open to it, in the order shown}. The
    actions are among reply (the message box: a session at work, idle or gone, not one writing its
    handoff), pause (at work; Pause now while it writes its handoff), startnow (queued), continue
    and queue (paused), continue (idle or gone), restart (never started), and close (a story ready
    for review with nothing to merge: job["empty"], set by pickup.summary). Each is one the
    server's guards allow (pickup.reply, pause, start_now, restart with nudge, requeue).

    Same in board.html: jobView, which the page falls back to for a server that sends no view.
    view_cases.json holds both to one table."""
    state = job["state"]
    if state == "started":
        label = "pausing" if job.get("pausing") else "working"
    elif state == "idle" and job.get("waiting"):
        label = "waiting for you"
    else:
        label = SHOWN.get(state, "")
    actions = []
    if state in TRACKED and not job.get("pausing"):
        actions.append("reply")
    if state == "started":
        actions.append("pause")
    if state == "waiting":
        actions.append("startnow")
    if state in ("paused", "idle", "lost"):
        actions.append("continue")
    if state == "paused":
        actions.append("queue")
    if state == "failed":
        actions.append("restart")
    if "reply" in actions and state != "started" and job.get("ready") and job.get("empty"):
        actions.append("close")
    return {"label": label, "actions": actions}
