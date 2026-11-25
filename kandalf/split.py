"""Split: a story's card asks the store's agent to split the story into sub-stories. One session,
in a throwaway worktree of the main repo, reads the story's tile and its plan doc and writes what
it proposes to SPLIT.md; the server reads that file into a proposal the person reviews on the
card, and nothing is written until they press Create (serve.split_story). This module holds the
run's readings and its lifecycle in the ledger, as state["split"]: one run per store at a time.
A sort (organize.py) may run beside it: they have their own worktrees and ledger slots.

  - refusal, story: whether a story can be split, and what the session is given of it
  - prompt: what the session is told
  - parse: the sub-stories in a SPLIT.md
  - why_not: why Split can't run, in pickup's words
  - of, start, end, look, busy, summary: the run, as organize's (its lifecycle helpers, by slot)

The session can't reach the store: the store isn't linked into its worktree (the plan doc it
reads is a copy) and it runs without the store's hooks, so nothing it writes is committed.
"""

import re

from . import jobs as ledger
from . import organize
from . import pickup
from . import tiles

SLOT = "split"  # the run's key in the ledger's state
OUTPUT = "SPLIT.md"  # the file the session writes at its worktree's top
KEY = "kandalf-split"  # the run's worktree goes where a story with this key would have its own: a name nobody types
STILL_ENDING = "The last split is still being cleaned up; try again in a moment."


class Refused(ValueError):
    """A run (or a Create) that can't go ahead, with one sentence saying why."""


# ---------- the story ----------
def refusal(store, snap, key: str):
    """None when the story with this key can be split; else one sentence saying why not: it has
    no tile, it is archived, it is a sub-story (its sub-stories would need a master of their own,
    so split its master, or take its Part of line off first), or it has a session or a place in
    the pickup queue."""
    tile = snap.tile(key) if key else None
    if tile is None:
        return f"{key or 'That story'} isn't on the boards any more." if key else "Say which story to split."
    if snap.index()[key]["file"] == tiles.ARCHIVE_LOG:
        return f"{key} is archived; an archived story can't be split."
    up = snap.parent(key)
    if up:
        return f"{key} is part of {up}, and a sub-story can't be split: split {up}, or take {key}'s Part of line off first."
    held = {j["key"] for j in ledger.Ledger(store).live()}
    if tiles.session(tile.text) or key in held:
        return f"{key} has a session (or a place in the pickup queue); end it before splitting it."
    return None


def story(store, snap, key: str) -> dict:
    """What a run is given of a story: {"key", "title", "text" (its tile's markdown), "doc" (the
    first plan doc it links that is in the store, or None), "tasks" (its own sub-tasks, [{"text",
    "done"}]), "rev" (what Snapshot.index says of it)}."""
    tile = snap.tile(key)
    return {"key": key, "title": tile.title, "text": tile.text, "doc": organize._doc(store, tile.text),
            "tasks": tiles.subtasks(tile.text), "rev": snap.index()[key]["rev"]}


# ---------- the session's prompt ----------
RULES = """- Propose two or more sub-stories, in the order they should be built, each small enough to build and review on its own.
- Give each a short title and a description of one short paragraph: what it does, and why.
- Put each of the story's own sub-tasks that belongs to a sub-story under it, word for word; a sub-task you leave out stays on the story. Add new sub-tasks where a sub-story needs them.
- Read the plan doc when the tile isn't enough. Don't read the app's code: the tile and its plan doc are all you need.
- Write one file, `{output}`, at the top of this folder, in this form, and nothing else is needed:

## Title of the first sub-story
What it does, in one short paragraph.

- [ ] A sub-task
- [ ] Another

## Title of the next sub-story
...

- Don't edit any board or plan doc. Change no code and don't commit. Stop when the file is written."""


def prompt(store, given: dict, note) -> str:
    """What the run's session is told: what splitting is for, the story's tile as it is, its plan
    doc's path under docs/<link>/ when it links one, its own sub-tasks when it has some, the
    person's note when there is one, the rules and the file to write."""
    out = [f"Split one story on the {store.name} boards into smaller sub-stories. Each sub-story becomes a tile of its "
           "own, part of this story, which stays as their master.", "",
           f"The story, {given['key']} {given['title']}, as its tile says it:", "", "~~~", given["text"], "~~~"]
    if given.get("doc"):
        out += ["", f"Its plan doc is a copy, at docs/{store.link}/{given['doc']} in this folder."]
    if given.get("tasks"):
        out += ["", "Its sub-tasks, which you may move into the sub-stories:"]
        out += [f"- [{'x' if t['done'] else ' '}] {t['text']}" for t in given["tasks"]]
    if note:
        out += ["", f"The person asked: {note}"]
    out += ["", "Rules:", RULES.format(output=OUTPUT)]
    return "\n".join(out) + "\n"


# ---------- reading SPLIT.md ----------
_HEADING = re.compile(r"^##(?:[ \t]+(.*?))?[ \t#]*$")
_TASK = re.compile(r"^\s*[-*+]\s+\[([ xX])\]\s+(.*?)\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


def _title(raw: str) -> str:
    """A proposed title without numbering ("1." or "2)"), bold, backticks and a full stop or colon at the end."""
    title = re.sub(r"^\d+[.)]\s+", "", raw.replace("*", "").replace("`", "")).strip()
    return " ".join(title.split()).rstrip(".:").strip()


def parse(text: str) -> list:
    """The sub-stories a SPLIT.md proposes, in order: [{"title", "description", "tasks": [{"text",
    "done"}]}]. Each "## " heading with a title is one; its description is the first paragraph
    under it (its lines joined), before its first sub-task, and its sub-tasks the "- [ ]" and
    "- [x]" lines under it (any bullet, any indent). Everything else is ignored: what comes before
    the first heading, other headings, plain bullets, later paragraphs, a heading with no title
    and its lines, and whatever is inside a code fence."""
    out, here, para, fenced = [], None, None, False
    for line in text.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        head = _HEADING.match(line) if line.startswith("## ") or line.rstrip() == "##" else None
        if head:
            title = _title(head.group(1) or "")
            here = {"title": title, "description": "", "tasks": []} if title else None
            if here:
                out.append(here)
            para = [] if here else None
            continue
        if here is None or line.startswith("#"):
            continue
        task = _TASK.match(line)
        if task:
            if task.group(2):
                here["tasks"].append({"text": task.group(2), "done": task.group(1) != " "})
            para = None
        elif not line.strip():
            if para:
                para = None
        elif para is not None and not re.match(r"^\s*[-*+]\s", line) and not here["tasks"]:
            para.append(line.strip())
            here["description"] = " ".join(para)
    return out


# ---------- whether Split can run ----------
def why_not(store, **kw):
    """None when Split can run for the store; else one sentence saying why not. It needs what
    pickup needs (pickup.cannot_run), but not "pickup": true, and a store whose permission lets
    a session write its file. kw: installed and lacks, as pickup.why_off takes."""
    why = pickup.cannot_run(store, **kw)
    if why:
        return "Split is off: " + why
    if store.permission == "plan":
        return f"Split is off: the store's permission is plan, so a session can't write {OUTPUT}."
    return None


# ---------- the run ----------
def of(store):
    """The run in the ledger, or None."""
    return organize.of(store, SLOT)


def start(store, key: str, note, *, sessions) -> dict:
    """Start a run for the story with this key: refused (Refused, with why) when Split can't run
    (why_not), while a run is in the ledger, or when the story can't be split (refusal). The run
    is recorded working first, with what the story was when it was read (its rev, which Create
    expects, and its sub-tasks); then a detached worktree of the main repo is cut, the plan doc
    copied in and one background session started there (organize._launch_run). The record as it
    then is: working with the session's id, or need with why."""
    why = why_not(store)
    if why:
        raise Refused(why)
    held = of(store)
    if held and held["state"] == "ending" and not end(store, sessions=sessions):
        raise Refused(STILL_ENDING)
    note = (note or "").strip() or None
    with ledger.Ledger(store).change() as state:
        rec = state.get(SLOT)
        if rec:
            raise Refused(STILL_ENDING if rec["state"] == "ending" else
                          f"A split of {rec.get('key')} is already under way: finish it or cancel it first.")
        snap = tiles.Snapshot.read(store)
        why = refusal(store, snap, key)
        if why:
            raise Refused(why)
        given = story(store, snap, key)
        run = {"state": "working", "started": sessions.now(), "key": key, "title": given["title"], "note": note,
               "rev": given["rev"], "tasks": given["tasks"], "repo": store.main_repo.name,
               "worktree": str(organize._path(store, KEY))}
        state[SLOT] = dict(run)
    args = pickup.session_args({"key": "Split", "title": key, "model": store.model})
    return organize._launch_run(store, run, sessions=sessions, docs=[given], args=args,
                                text=lambda: prompt(store, given, note), slot=SLOT, key=KEY, who="a split")


def end(store, *, sessions) -> bool:
    """End the run, in any state (organize.end, under this run's slot)."""
    return organize.end(store, sessions=sessions, slot=SLOT)


def busy(store, rec=None) -> bool:
    """Whether the run needs looking at again soon (organize.busy, under this run's slot)."""
    return organize.busy(store, rec, SLOT)


def _proposal(run: dict, text: str) -> dict:
    """The fields of a run whose session wrote text: ready with its stories ([{title, description,
    tasks: [{text, done, moved}]}]), where a task that matches one of the story's own sub-tasks
    (tiles.task_key) is moved, spelt and ticked as the story has it, and goes only to the first
    sub-story that lists it (of two of the story's alike, the first, as Create takes it), and
    stays, the story's own sub-tasks none takes; or need when there is no sub-story."""
    own = {}
    for t in run.get("tasks") or []:
        own.setdefault(tiles.task_key(t["text"]), t)
    taken, stories = set(), []
    for s in parse(text):
        tasks = []
        for t in s["tasks"]:
            k = tiles.task_key(t["text"])
            if k in own:
                if k in taken:
                    continue
                taken.add(k)
                tasks.append({"text": own[k]["text"], "done": own[k]["done"], "moved": True})
            else:
                tasks.append({**t, "moved": False})
        stories.append({**s, "tasks": tasks})
    if not stories:
        return {"state": "need", "why": "The agent proposed no sub-stories."}
    left, stays = set(taken), []
    for t in run.get("tasks") or []:  # each sub-task taken goes once; one alike after it stays
        k = tiles.task_key(t["text"])
        if k in left:
            left.discard(k)
        else:
            stays.append(t["text"])
    return {"state": "ready", "stories": stories, "stays": stays}


def claim(store, key: str, rev: str, expect):
    """Take the run's proposal for Create: when the ledger's run is ready for this key, its rev
    (what it read of the story) is the story's now (rev) and the one the page reviewed (expect),
    it is marked ending at once, in one transaction, so a second Create (a retry, another tab)
    finds no proposal to create. None when it is taken; else (409, {error}) with nothing
    changed: ("changed since", changed: [key]) when the story or the proposal moved on."""
    with ledger.Ledger(store).change() as state:
        rec = state.get(SLOT)
        if not rec or rec.get("key") != key or rec["state"] == "ending":
            return 409, {"error": f"There is no split of {key} ready to create (it was created or discarded); ask again for a new one."}
        if rec["state"] != "ready":
            return 409, {"error": f"The split of {key} is still at work; create it once it is ready."}
        if rec.get("rev") != rev or (expect is not None and expect != rec.get("rev")):
            return 409, {"error": "changed since", "changed": [key]}
        rec["state"] = "ending"
    return None


def look(store, *, sessions) -> list:
    """One pass over the run, as organize.look makes over its own: its SPLIT.md read (_proposal)
    once the session has stopped, a run that leaves working tidied up. The lines to log."""
    key = (of(store) or {}).get("key")

    def ready(fields):
        n = len(fields["stories"])
        return f"{n} sub-stor{'y' if n == 1 else 'ies'} ready to review for {key}"
    return organize._look(store, sessions=sessions, read=_proposal, ready=ready, slot=SLOT, output=OUTPUT)


def summary(store):
    """What the page is told of the run: None when there is none (or it is ending), else
    {"state", "started", "key", "title", "note", "rev"} with "why" when it needs you and "stories"
    and "stays" when it is ready."""
    rec = of(store)
    if not rec or rec["state"] == "ending":
        return None
    out = {k: rec.get(k) for k in ("state", "started", "key", "title", "note", "rev")}
    if rec.get("why"):
        out["why"] = rec["why"]
    if rec["state"] == "ready":
        out.update(stories=rec.get("stories") or [], stays=rec.get("stays") or [])
    return out

