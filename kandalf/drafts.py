"""Drafts: a session that researches the options for a decision tile and writes them up in a
research doc, which the server reads and puts on the tile as an options block. This module holds
a draft's readings and its lifecycle in the ledger.

  - STATES, and view: what the page shows of a draft in each state
  - read_doc: the options and the recommendation in a research doc
  - doc_path, block: where the doc lives in the store, and the tile's options block for a reading
  - prompt: what the draft's session is told
  - why_not: why Draft options can't run, in pickup's words
  - request, of, live: a draft asked for and queued in the ledger, and looked up there
  - start, end: a draft's throwaway worktrees and session made, and retired and removed again
  - look, busy, unread, docs, summary: the server's one pass over the drafts, and what it and the page
    are told of them
"""

import re
import time
from pathlib import Path

from . import jobs as ledger
from . import pickup
from . import tiles

# A draft's states: queued waits for the draft before it, working has a session at it, ready has a
# research doc read onto the tile, empty has a session that finished with no option in its doc,
# need has a session waiting on a question or ended on an error, lost has a session gone from the
# session list, and ending is being cleaned up (session retired, worktrees removed).
STATES = ("queued", "working", "ready", "empty", "need", "lost", "ending")
MAX_OPTIONS = 4  # how many options of a doc the tile keeps
SUMMARY_CUT = 240  # the longest a summary runs before it is cut on a word
UNWRITTEN = "the options could not be written"  # in a look's line for a block whose write failed

_RECOMMENDS = re.compile(r"^\**recommends\**\s*:\s*\**\s*([A-Ha-h])\b", re.I)
_OPTION = re.compile(r"^##\s+(?:option\s+)?([A-Ha-h])[.:)]\s+(.+?)(?:\s+#+)?\s*$", re.I)
_HEADING = re.compile(r"^#{1,6}(\s|$)")
_FENCE = ("```", "~~~")


def _title(text: str) -> str:
    """An option's title: without emphasis stars and one trailing full stop."""
    text = text.replace("*", "").strip()
    return text[:-1].rstrip() if text.endswith(".") else text


def _cut(text: str) -> str:
    """The text, or its first SUMMARY_CUT characters ending on a whole word, with an ellipsis."""
    if len(text) <= SUMMARY_CUT:
        return text
    head = text[:SUMMARY_CUT]
    if text[SUMMARY_CUT] != " ":
        head = head.rsplit(" ", 1)[0] if " " in head else head
    return head.rstrip() + "…"


def _summary(lines: list) -> str:
    """The first prose paragraph of the lines under a heading, folded to one line and cut on a
    word. A paragraph ends at a blank line, a heading or a code fence; a fenced block is skipped."""
    para = []
    fenced = False
    for raw in lines:
        line = raw.strip()
        if line.startswith(_FENCE):
            if para:
                break
            fenced = not fenced
        elif fenced:
            continue
        elif _HEADING.match(line):
            break
        elif line:
            para.append(line)
        elif para:
            break
    return _cut(" ".join(" ".join(para).split()))


def read_doc(text: str) -> dict:
    """What the server reads of a research doc: {"recommends": a letter or None, "items": [{"letter",
    "title", "summary"}]}. An option is a heading "## A. Title" (letters A to H, a little drift
    allowed); its summary is the first prose paragraph under it. "Recommends: B" counts before the
    first option, and only when that option is there. Lines in a code fence are never headings or
    a recommendation; a letter's first option is the one kept, and at most MAX_OPTIONS of them. A
    doc with no option reads as empty. Everything else is ignored.
    Python only: the page never reads a doc."""
    lines = text.splitlines()
    recommends = None
    items = []
    fenced = False
    for i, raw in enumerate(lines):
        line = raw.strip()
        if line.startswith(_FENCE):
            fenced = not fenced
            continue
        if fenced:
            continue
        m = _OPTION.match(line)
        if m:
            letter = m.group(1).upper()
            if all(it["letter"] != letter for it in items):
                items.append({"letter": letter, "title": _title(m.group(2)), "summary": _summary(lines[i + 1:])})
        elif not items and recommends is None:
            r = _RECOMMENDS.match(line)
            if r:
                recommends = r.group(1).upper()
    items = items[:MAX_OPTIONS]
    if recommends not in [it["letter"] for it in items]:
        recommends = None
    return {"recommends": recommends, "items": items}


def doc_path(key: str, date: str) -> str:
    """Where a draft's research doc lives, relative to the store: active/<date>-<key>-options.md."""
    return f"active/{date}-{key.lower()}-options.md"


def block(reading: dict, date: str, doc, asked) -> list:
    """The tile's options block (tiles.options_block) for a reading of a doc. doc is the doc's
    store-relative path or None; asked is the note given to Draft again."""
    return tiles.options_block(date, reading["items"], reading.get("recommends"), doc, asked)


RULES = """- This is research for a decision a person will make. Don't decide it.
- Read the code and docs you need. You may run read-only commands and tests to check a claim.
- Change no code. This worktree is thrown away when the draft ends; nothing in it is kept.
- Write exactly one file, `{path}`, in the format shown (the fixed opening above). Two to four options that really differ, each with a one-paragraph summary a person can choose from, a rough cost, and what it rules out. Recommend one and say why. Say what you could not find out.
- Don't edit the boards. Don't commit. Stop when the file is written."""

FORMAT = """# {key} options: {title}

Recommends: B

## A. Restyle it

Sidebar card, profile box, tab bar and More sheet in the pro palette. ~2d, for a theme
nothing can switch on.

What it touches, risks, what was read...

## B. Retire it

Delete the app-shell rules in app-neo-pro.css. ~1h; the spectate pages keep their own pro look.

...

## What I read

..."""


def prompt(store, draft: dict, tile: str, context: str, blocks: list, again: bool) -> str:
    """What a draft's session is told: the tile, its master's tile and the tiles it blocks for
    context, the person's note when there is one, the rules, and the research doc's path as the
    session reaches it. again: a redraft, which points at the last draft."""
    path = f"docs/{store.link}/{draft['doc']}"
    out = [f"You are researching options for {draft['key']} \"{draft['title']}\" on the {store.name} Plan board. "
           f"A person has to decide it, and your research doc is what they decide from. The tile reads:", "", tile]
    if context:
        out += ["", "It is part of a larger story, whose tile reads:", "", context]
    if blocks:
        out += ["", "These tiles wait for its decision:"] + [f"- {b['key']}: {b['title']}" for b in blocks]
    if again:
        out += ["", "This is a redraft."]
    if draft.get("note"):
        out += ["", f"The person asked: {draft['note']}"]
    if again:
        out += [f"The last draft is at `{path}`: revise it."]
    out += ["", "The research doc has this shape. Its opening is fixed; the rest is free:", "",
            "```markdown", FORMAT.format(key=draft["key"], title=draft["title"]), "```", "",
            f"docs/{store.link} is the plans store linked into this worktree, so the research doc written there is kept when the worktree is thrown away.",
            "", "Rules:", RULES.format(path=path)]
    return "\n".join(out) + "\n"


_VIEWS = {
    "queued": ("queued to draft", ["stop"]),
    "working": ("drafting options", ["stop"]),
    "ready": ("options ready", ["again"]),
    "empty": ("draft gave no options", ["again"]),
    "need": ("draft needs you", ["again", "stop"]),
    "lost": ("draft lost", ["again"]),
    "ending": ("", []),
}


def view(draft: dict) -> dict:
    """What the page shows of a draft: {"label": a few words for its state, "actions": the buttons
    open to it: again (Draft again), stop}.

    Same in board.html: draftView. view_cases.json ("drafts") holds both to one table."""
    label, actions = _VIEWS.get(draft.get("state"), ("", []))
    return {"label": label, "actions": list(actions)}


# ---------- why Draft options can't run ----------
def why_not(store, **kw):
    """None when Draft options can run for the store; else one sentence saying why not. It needs
    what pickup needs (pickup.cannot_run), but not "pickup": true, and a store whose permission
    lets a session write the research doc. kw: installed and lacks, as pickup.why_off takes."""
    why = pickup.cannot_run(store, **kw)
    if why:
        return "Draft options is off: " + why
    if store.permission == "plan":
        return "Draft options is off: the store's permission is plan, so a session can't write the research doc."
    return None


# ---------- the ledger's drafts ----------
def live(store) -> list:
    """Every draft in the ledger, in the order they were asked for."""
    return ledger.Ledger(store).read().get("drafts", [])


def of(store, key: str):
    """The draft a tile has in the ledger (the latest, if two linger); None when it has none."""
    return next((d for d in reversed(live(store)) if d["key"] == key), None)


def request(store, key: str, tile: str, note, *, sessions) -> dict:
    """Ask for a draft on a tile. In one transaction, the drafts the key has are marked ending and
    a queued one is added, so a key never has two that aren't ending; then the earlier drafts'
    sessions and worktrees are cleaned up (end), outside the lock. The new draft's doc is the
    earlier one's when there was one, else a new path dated today. tile: the tile's markdown."""
    now = sessions.now()
    with ledger.Ledger(store).change() as state:
        held = state.setdefault("drafts", [])
        earlier = [d for d in held if d["key"] == key]
        doc = earlier[-1]["doc"] if earlier else doc_path(key, time.strftime("%Y-%m-%d", time.localtime(now)))
        for d in earlier:
            d["state"] = "ending"
        draft = {"key": key, "state": "queued", "title": tiles._title(store, tile), "note": note, "doc": doc, "asked_at": now}
        held.append(draft)
        earlier = [dict(d) for d in earlier]
    for d in earlier:
        _finish(store, d, sessions)
    return draft


# ---------- worktrees ----------
def _registered(repo: Path, path: Path) -> bool:
    """Whether git lists path as one of the repo's worktrees."""
    listed = pickup._git(repo, "worktree", "list", "--porcelain").stdout.splitlines()
    return any(line.startswith("worktree ") and Path(line[9:]).resolve() == path.resolve() for line in listed)


def _drop(repo: Path, path: Path) -> str:
    """Remove a worktree and what it changed. "" when it is gone (one that was gone already is);
    else why not, in git's words."""
    failed = ""
    if path.exists():
        failed = pickup._git(repo, "worktree", "remove", "--force", str(path)).stderr.strip()
    pickup._git(repo, "worktree", "prune")
    return "" if not path.exists() else failed or f"{path} would not go"


def _worktree(store, key: str, repo, tile: str) -> dict:
    """A detached worktree of the repo's trunk for the draft: {"name", "worktree", "trunk"}.
    A worktree git still lists at the path is removed and made again; any other trouble raises
    RuntimeError with git's words, or a plain sentence naming the path. The store is not linked
    in yet (pickup.link_store)."""
    path = pickup.worktree(store, {"key": "draft-" + key, "title": ""}, repo)
    trunk = pickup.story_trunk(store, repo, None, tile)
    if path.exists():
        if not _registered(repo.path, path):
            raise RuntimeError(f"{path} is already there and isn't a worktree of {repo.name}, so move it away.")
        why = _drop(repo.path, path)
        if why:
            raise RuntimeError(why)
    pickup._git(repo.path, "worktree", "prune")
    pickup.keep_out(repo.path, path)
    made = pickup._git(repo.path, "worktree", "add", "--detach", str(path), trunk)
    if made.returncode != 0:
        raise RuntimeError(made.stderr.strip() or "git worktree add failed")
    return {"name": repo.name, "worktree": str(path), "trunk": trunk}


def _undo(store, cuts: list) -> list:
    """Remove the worktrees a start made, when it goes no further; the ones that won't go."""
    stayed = []
    for cut in cuts:
        repo = store.repo(cut["name"])
        if repo and _drop(repo.path, Path(cut["worktree"])):
            stayed.append(cut)
    return stayed


def _need(store, cuts: list, why: str) -> dict:
    """The fields of a draft that needs you: why, and the worktrees of cuts that can't be undone."""
    stayed = _undo(store, cuts)
    return {"state": "need", "why": why, **({"repos": stayed} if stayed else {})}


def start(store, draft: dict, tile: str, context: str, blocks: list, *, sessions) -> dict:
    """Start a draft: a detached worktree of each repo the tile names (its Repos line, else the
    main repo), then one background session in the first. The fields to set on the draft:
    working, with the session id, repos and start time; or need, with why (and as repos any
    worktree that couldn't be removed again). Anything raised once worktrees are being cut
    undoes them the same way, so no worktree is left without a record. A doc the draft already
    has makes it a redraft."""
    key = draft["key"]
    repos, error = pickup.resolve_repos(store, tiles.repos(tile))
    if error:
        return {"state": "need", "why": error}
    cuts = []
    try:
        for repo in repos:
            cuts.append(_worktree(store, key, repo, tile))
            pickup.link_store(store, repo.path, Path(cuts[-1]["worktree"]))
        again = (store.dir / draft["doc"]).is_file()
        args = pickup.session_args({"key": key, "title": "options", "model": pickup.model_for(store, tile)})
        launched = pickup._launch(store, draft, args, Path(cuts[0]["worktree"]), prompt(store, draft, tile, context, blocks, again),
                                  {}, [c["worktree"] for c in cuts[1:]])
    except Exception as e:  # git's trouble, or anything else: what was cut is undone
        return _need(store, cuts, str(e))
    if launched["state"] != "started":
        return _need(store, cuts, launched.get("error") or "the session didn't start")
    return {"state": "working", "id": launched["id"], "session": launched["session"], "repos": cuts, "started": sessions.now()}


def _listed(repo: Path):
    """{resolved path: its lines} for each worktree git lists in the repo (git worktree list
    --porcelain); None when git can't list them (an unreadable list is not an empty one)."""
    out, here = {}, None
    ran = pickup._git(repo, "worktree", "list", "--porcelain")
    if ran.returncode != 0:
        return None
    for line in ran.stdout.splitlines():
        if line.startswith("worktree "):
            here = out[Path(line[9:]).resolve()] = []
        elif here is not None:
            here.append(line)
    return out


def _finish(store, draft: dict, sessions) -> bool:
    """Clean up a draft marked ending: its session retired (and gone from the session list), each
    worktree of its own removed with what it changed, then its record taken out of the ledger.
    A recorded path is the draft's own when git lists it as detached or it is where the draft's
    worktree goes now; one listed on a branch, or on disk but not a worktree, is not: it is left
    alone, said on pickup's log, and forgotten. False, with the record left ending, when the
    session is still there or a list can't be read, or a worktree won't go. A draft with no
    session or worktree (queued) has nothing to do."""
    sid = draft.get("id")
    if sid:
        sessions.retire(sid)
        listing = sessions.sessions()  # the session list says whether it is gone, not retire's answer
        if listing is None or sid in listing:
            return False
    left = []

    def forget():
        if left:
            with ledger.Ledger(store).change() as state:
                for d in state.get("drafts", []):
                    if _same(d, draft) and d["state"] == "ending":
                        d["repos"] = [c for c in d.get("repos") or [] if c not in left]
    done = False
    try:
        for cut in draft.get("repos") or []:
            repo, path = store.repo(cut["name"]), Path(cut["worktree"])
            if not repo:
                continue
            listing = _listed(repo.path)
            if listing is None:
                return False
            block = listing.get(path.resolve())
            own = pickup.worktree(store, {"key": "draft-" + draft["key"], "title": ""}, repo)
            if block is None and not path.exists():
                continue
            if block is not None and ("detached" in block or path == own):
                if _drop(repo.path, path):
                    return False
                continue
            pickup.log(f"{draft['key']} draft: left {path} alone, it is not a worktree this draft cut")
            left.append(cut)
        done = True
    finally:
        forget()
    if done:
        with ledger.Ledger(store).change() as state:
            state["drafts"] = [d for d in state.get("drafts", []) if not (_same(d, draft) and d["state"] == "ending")]
    return done


def end(store, key: str, *, sessions) -> bool:
    """End a tile's draft: marked ending first (so a crash part-way leaves it findable), then
    cleaned up (_finish) and taken out of the ledger. True when it is gone; a step that doesn't
    finish leaves it ending and gives False. A key with no draft is ended already."""
    with ledger.Ledger(store).change() as state:
        mine = [d for d in state.get("drafts", []) if d["key"] == key]
        for d in mine:
            d["state"] = "ending"
        mine = [dict(d) for d in mine]
    return all([_finish(store, d, sessions) for d in mine])


# ---------- the look ----------
def busy(store, held=None) -> bool:
    """Whether a draft is queued or at work, so the drafts' turn looks again soon. held: the
    drafts (live), when the caller has read them already."""
    return any(d["state"] in ("queued", "working") for d in (live(store) if held is None else held))


def _mtime(store, draft):
    """The research doc's mtime in nanoseconds, or None when it isn't there."""
    try:
        return (store.dir / draft["doc"]).stat().st_mtime_ns
    except OSError:
        return None


def unread(store, held=None) -> bool:
    """Whether a ready, empty or need draft's research doc changed since it was last read (its
    record's read), so the drafts' turn looks again soon: the look leaves such a doc while its
    session is busy, and nothing else may change once the session goes idle. These are the
    drafts look reads again; a queued or working one is busy instead. held: the drafts (live),
    when the caller has read them already."""
    return any(d["state"] in ("ready", "empty", "need") and _mtime(store, d) != d.get("read")
               for d in (live(store) if held is None else held))


def docs(store, held=None) -> dict:
    """{key: the research doc's mtime, or None} for every draft that isn't ending, for the
    drafts' turn to see a doc change. held: the drafts (live), when the caller has read them
    already."""
    return {d["key"]: _mtime(store, d) for d in (live(store) if held is None else held) if d["state"] != "ending"}


def summary(store) -> list:
    """What the page is told of each draft that isn't ending: [{"key", "state", "why", "id",
    "started", "asked_at", "view"}], a field the draft lacks given as None."""
    return [{"key": d["key"], "state": d["state"], "why": d.get("why"), "id": d.get("id"), "started": d.get("started"),
             "asked_at": d.get("asked_at"), "view": view(d)} for d in live(store) if d["state"] != "ending"]


def _same(d: dict, draft: dict) -> bool:
    """Whether a ledger record is this draft: its key, asked for at the same time, and an orphan
    (what a start made for a record gone by then) only when the draft is that orphan."""
    return d["key"] == draft["key"] and d.get("asked_at") == draft.get("asked_at") and bool(d.get("orphan")) == bool(draft.get("orphan"))


def _set(store, draft: dict, was: str, fields: dict) -> bool:
    """Set fields on the draft's record if it is still in state was; a record that is no longer
    in need loses its why. False, with nothing changed, when it was stopped, replaced or ended
    meanwhile."""
    with ledger.Ledger(store).change() as state:
        for d in state.get("drafts", []):
            if _same(d, draft) and d["state"] == was:
                d.update(fields)
                if d["state"] != "need":
                    d.pop("why", None)
                return True
    return False


def _gone(snap, at: dict, key: str):
    """Why a draft's tile is no longer a decision to draft for (gone from the build and plan
    boards, archived, done, or out of the decision column), or None while it is one."""
    tile = snap.tile(key)
    if tile is None or at.get(key, {}).get("file") not in (snap.build, snap.plan):
        return "its tile is archived" if at.get(key, {}).get("file") == tiles.ARCHIVE_LOG else "its tile is gone"
    if tile.done:
        return "its tile is done"
    if not tiles.is_decision_column(tile.column):
        return "its tile is no longer a decision"
    return None


def _take(store, draft: dict, tile: str, *, sessions, write) -> list:
    """Read a draft's research doc onto its tile: the draft becomes ready (or empty, for no doc
    or one with no option), its read the doc's mtime, and write is called when the block
    differs from the one the tile has. write runs outside the ledger's lock; one that raises is
    said and leaves the record as it was. The lines to log."""
    key, was = draft["key"], draft["state"]
    mtime = _mtime(store, draft)
    try:
        text = (store.dir / draft["doc"]).read_text(encoding="utf-8", errors="replace") if mtime is not None else ""
    except OSError:
        mtime, text = None, ""
    reading = read_doc(text)
    date = time.strftime("%Y-%m-%d", time.localtime(sessions.now()))
    lines = block(reading, date, draft["doc"] if mtime is not None else None, draft.get("note"))
    state = "ready" if reading["items"] else "empty"
    wrote = tiles.options("\n".join(lines)) != tiles.options(tile)
    if wrote:
        try:
            write(key, lines, f"board: {key} options drafted" if state == "ready" else f"board: {key} draft gave no options")
        except Exception as e:  # the record stays as it was, so the next look tries again
            return [f"{key}: {UNWRITTEN}: {e}"]
    if not _set(store, draft, was, {"state": state, "read": mtime}):
        return []
    if state != was:
        return [f"{key}: options ready" if state == "ready" else f"{key}: the draft gave no options"]
    return [f"{key}: options read again from its research doc"] if wrote else []


def _begin(store, snap, at: dict, draft: dict, *, sessions) -> list:
    """Start a queued draft (start), outside the lock, then write what started onto its record,
    if the record is still there and still queued. One that was stopped, replaced or ended
    meanwhile is not written over: what started is recorded as a draft ending of its own (an
    orphan, which ending the stopped record never drops) and ended. The record's read is the doc's mtime before the session, so a draft again isn't made
    ready by the last draft's doc. The lines to log."""
    key = draft["key"]
    tile = snap.tile(key)
    context = "\n".join(t.text for t in snap.lineage(key))
    blocks = [{"key": k, "title": t["title"]} for k, t in at.items() if key in t["blockers"] and not t["done"]]
    read = _mtime(store, draft)
    got = {**start(store, draft, tile.text, context, blocks, sessions=sessions), "read": read}
    orphan = None
    with ledger.Ledger(store).change() as state:
        mine = next((d for d in state.get("drafts", []) if _same(d, draft) and d["state"] == "queued"), None)
        if mine is not None:
            mine.update(got)
        elif got.get("id") or got.get("repos"):
            orphan = {**draft, **got, "state": "ending", "orphan": True}
            state.setdefault("drafts", []).append(orphan)
            orphan = dict(orphan)
    if mine is None:
        if orphan:
            _finish(store, orphan, sessions)
        if not orphan:
            return [f"{key}: the draft was stopped while it started"]
        return [f"{key}: the draft was stopped while it started, so its session and worktrees are ended"]
    if got["state"] == "working":
        return [f"{key}: drafting options (session {got['id']})"]
    return [f"{key}: the draft needs you: {got['why']}"]


def look(store, snap, *, sessions, write) -> list:
    """One pass over the drafts, in this order: a draft whose tile is no longer a decision is
    ended (but one just asked for, whose tile snap lacks, is left until SETTLE has passed); a
    draft left ending is ended again; a draft at work moves on by what its session is doing
    (busy: left alone; idle with nothing it waits for: its doc read onto the tile, or need when
    a redraft left its doc as it was; waiting: need, with why, and its doc's mtime then noted as
    read; gone from the list: lost; a list that can't be read: nothing changes); a ready, empty
    or need draft whose doc changed since is read again while its session isn't busy; and when
    no draft is at work, the oldest queued one whose key has none ending is started.
    snap: the boards (tiles.Snapshot). write(key, block, subject) puts a block on a tile and
    commits it. The ledger's lock is never held across git, the agent or write. The lines to
    log, as plain sentences."""
    said = []
    if not live(store):
        return said
    at = snap.index()
    ended = set()
    held = [d for d in live(store) if d["state"] != "ending"]
    for key in dict.fromkeys(d["key"] for d in held):
        why = _gone(snap, at, key)
        if why and key not in at and all(d["state"] == "queued" and sessions.now() - d.get("asked_at", 0) < ledger.SETTLE
                                         for d in held if d["key"] == key):
            continue  # just asked for: snap may have been read before its tile was keyed
        if why:
            ended.add(key)
            done = end(store, key, sessions=sessions)
            said.append(f"{key}: draft ended, {why}" if done else f"{key}: draft ending, {why}; a later look finishes it")
    for d in live(store):
        if d["state"] == "ending" and d["key"] not in ended and _finish(store, d, sessions):
            said.append(f"{d['key']}: draft ended")

    listing, asked = None, False

    def listed():
        nonlocal listing, asked
        if not asked:
            listing, asked = sessions.sessions(), True
        return listing

    def deciding(key):  # a record asked for after snap was read may have no tile in it yet
        return _gone(snap, at, key) is None

    for d in live(store):
        if d["state"] != "working" or not deciding(d["key"]) or listed() is None:
            continue
        key, sid = d["key"], d.get("id")
        if sessions.now() - d.get("started", 0) < ledger.SETTLE:  # just launched: it may not show yet
            continue
        seen = listing.get(sid)
        if seen == "busy":
            continue
        if seen is None:
            if _set(store, d, "working", {"state": "lost"}):
                said.append(f"{key}: the draft's session is gone")
            continue
        waits = sessions.why(sid)
        if seen == "waiting" and not waits:
            waits = "question"  # the CLI says it is blocked on input, as jobs.saw reads it
        mtime = _mtime(store, d)
        if waits:
            why = ledger.WAITING.get(waits, ledger.WAITING["question"])
        elif mtime is not None and mtime == d.get("read"):  # a redraft that left the last draft's doc as it was
            why = "it finished without changing the research doc"
        else:
            said += _take(store, d, snap.tile(key).text, sessions=sessions, write=write)
            continue
        # read: the doc as it is now, so only a later change of it makes the draft ready
        if _set(store, d, "working", {"state": "need", "why": why, "read": mtime}):
            said.append(f"{key}: the draft needs you: {why}")

    for d in live(store):
        if d["state"] not in ("ready", "empty", "need") or not deciding(d["key"]) or _mtime(store, d) == d.get("read"):
            continue
        if listed() is None or listing.get(d.get("id")) == "busy":
            continue
        said += _take(store, d, snap.tile(d["key"]).text, sessions=sessions, write=write)

    held = live(store)
    if any(d["state"] == "working" for d in held):
        return said
    ending = {d["key"] for d in held if d["state"] == "ending"}
    queued = next((d for d in held if d["state"] == "queued" and d["key"] not in ending and deciding(d["key"])), None)
    if queued:
        said += _begin(store, snap, at, queued, sessions=sessions)
    return said
