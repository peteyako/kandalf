"""Organize: the map asks the store's agent to sort stories into domains. One session, in a
throwaway worktree of the main repo, reads the stories' tiles and plan docs and writes what it
proposes to ORGANIZE.md; the server reads that file into a proposal the person reviews on the
map, and nothing is filed until they apply it (through /api/domain, as a drop files). This
module holds the run's readings and its lifecycle in the ledger, as state["organize"]: one run
per store at a time.

  - stories, domains: what is sorted, and the domains there are now with their counts
  - prompt: what the session is told
  - parse: the moves in an ORGANIZE.md
  - why_not: why Organize can't run, in pickup's words
  - of, start, end: the run in the ledger, started (worktree and session) and ended again

The session can't reach the store: the store isn't linked into its worktree (the plan docs it
reads are copies) and it runs without the store's hooks, so nothing it writes is committed.
  - look, busy, summary: the server's one pass over the run, and what it and the page are told

The run's lifecycle (of, end, busy, _launch_run, _look) takes the ledger slot and the worktree's
key, so a card's Split (split.py) runs the same way under its own slot.
"""

import re
from pathlib import Path

from . import agent
from . import drafts
from . import jobs as ledger
from . import pickup
from . import tiles

# A run's states: working has a session at it, ready has a proposal to review, need stopped
# without one (why says what happened), and ending is being cleaned up (session retired,
# worktree removed). No record is no run.
STATES = ("working", "ready", "need", "ending")
OUTPUT = "ORGANIZE.md"  # the file the session writes at its worktree's top
DESCRIPTION_CUT = 300  # how much of a tile's description the session is given
KEY = "kandalf-organize"  # the run's worktree goes where a story with this key would have its own: a name nobody types


class Refused(ValueError):
    """A run that can't start, with one sentence saying why."""


STILL_ENDING = "The last sort is still being cleaned up; try again in a moment."


# ---------- what is sorted ----------
_DOC_RE = re.compile(r"\]\((?:\./)?((?:active|archive)/[^)\s]+\.md)\)")
_FIRST_RE = re.compile(r"^[-*] (?:\[[ xX]\] )?")


def _cut(text: str) -> str:
    """The text, or its first DESCRIPTION_CUT characters ending on a whole word, with an ellipsis."""
    if len(text) <= DESCRIPTION_CUT:
        return text
    head = text[:DESCRIPTION_CUT]
    if text[DESCRIPTION_CUT] != " " and " " in head:
        head = head.rsplit(" ", 1)[0]
    return head.rstrip() + "…"


def _description(store, md: str) -> str:
    """A tile's description on one line: its first line after the key and the bold title, then
    the lines under it up to its sub-tasks, session, status, ready, options or decision lines,
    without its Domain line."""
    lines = md.split("\n")
    first = tiles.TILE_HEAD_RE.match(tiles._patterns(store.key)[2].sub("", _FIRST_RE.sub("", lines[0]))).group(1)
    first = re.sub(r"^\*\*.+?\*\*\s*", "", first)
    body = []
    for line in lines[1:]:
        if tiles.AFTER_DESCRIPTION_RE.match(line) or tiles.SESSION_LINE_RE.match(line):
            break
        if not tiles.DOMAIN_RE.match(line):
            body.append(line.strip())
    return _cut(" ".join(" ".join([first, *body]).split()))


def _doc(store, md: str):
    """The first plan doc a tile links that is in the store, as its path in the store; else None."""
    for found in _DOC_RE.finditer(md):
        path = found.group(1)
        at = (store.dir / path).resolve()
        if path != tiles.ARCHIVE_LOG and at.is_file() and at.is_relative_to(store.dir.resolve()):
            return path
    return None


def stories(store, snap, refile: bool) -> list:
    """The stories a run sorts: the keyed tiles on the Build and Plan boards (never the archive)
    with no master (a sub-story follows its master's domain), and without refile only those
    with no Domain line of their own. [{"key", "title", "domain": its own Domain line or None,
    "description", "doc"}], the Build board's first."""
    out = []
    for name in (snap.build, snap.plan):
        for t in snap.tiles(name):
            if not t.key or snap.lineage(t.key) or snap.tile(t.key) is not t:
                continue
            own = tiles.domain(t.text)
            if own and not refile:
                continue
            out.append({"key": t.key, "title": t.title, "domain": own, "description": _description(store, t.text), "doc": _doc(store, t.text)})
    return out


def domains(snap) -> list:
    """The domains the stories are in now, as the map draws them (tiles.tile_domains: a Domain
    line, a master's, or a title prefix several stories share), [(name, how many stories)],
    most first; each keyed tile on the boards and the archive counts once."""
    spelt, count = {}, {}
    for found in tiles.tile_domains(snap.index()).values():
        if found:
            low = found.lower()
            spelt.setdefault(low, found)
            count[low] = count.get(low, 0) + 1
    return sorted(((spelt[k], n) for k, n in count.items()), key=lambda d: (-d[1], d[0].lower()))


def _known(snap) -> dict:
    """{lower-cased name: the name as spelt} for every domain the map draws: each Domain line on
    the store's tiles as the first spells it (what /api/domain writes), then each title prefix
    that is a domain (tiles.tile_domains)."""
    out = {}
    for name in snap.files:
        for t in snap.tiles(name):
            own = tiles.domain(t.text)
            if own:
                out.setdefault(own.lower(), own)
    for found in tiles.tile_domains(snap.index()).values():
        if found:
            out.setdefault(found.lower(), found)
    return out


# ---------- the session's prompt ----------
RULES = """- Reuse a domain that is there when a story fits it. Prefer a handful of domains over many.
- A new domain's name starts with a letter or a digit, is at most 39 characters, has no backtick or line break, and doesn't end in a full stop.
- A story may be left out if no domain fits it.
- Read a story's plan doc when its title and description aren't enough. Don't read the app's code: the tiles and plan docs are all you need.
- Write one file, `{output}`, at the top of this folder, with one line per story you sort, in this form, and nothing else is needed:

- KEY: Domain name

- Don't edit any board or plan doc. Change no code and don't commit. Stop when the file is written."""


def prompt(store, chosen: list, existing: list, note) -> str:
    """What the run's session is told: what domains are for, the domains there are with their
    counts, the stories to sort (each one's key and title, the start of its description, its
    plan doc's path under docs/<link>/ when it links one, and its domain now when it has one or
    any story given has one, as when re-sorting), the person's note when there is one, the
    rules and the file to write."""
    resort = any(s.get("domain") for s in chosen)
    out = [f"Sort the stories on the {store.name} boards into domains. A domain is a region of the board's map that "
           "groups the stories of one part of the product, so a person sees each part at a glance.", ""]
    if existing:
        out.append("The domains there are now, with how many stories each has:")
        out += [f"- {name} ({n} stor{'y' if n == 1 else 'ies'})" for name, n in existing]
    else:
        out.append("There are no domains yet.")
    out += ["", "The stories to sort. Only stories with no master are listed: a master's sub-stories follow it into its domain. "
            f"A plan doc named here is a copy, in docs/{store.link}/ in this folder.", ""]
    for s in chosen:
        out.append(f"- {s['key']} {s['title']}")
        if resort:
            out.append(f"  Domain now: {s.get('domain') or 'none'}")
        if s.get("doc"):
            out.append(f"  Plan doc: docs/{store.link}/{s['doc']}")
        if s.get("description"):
            out.append(f"  {s['description']}")
    if note:
        out += ["", f"The person asked: {note}"]
    out += ["", "Rules:", RULES.format(output=OUTPUT)]
    return "\n".join(out) + "\n"


# ---------- reading ORGANIZE.md ----------
_BULLET = re.compile(r"^\s*[-*+]\s+(.*?)\s*$")
_MOVE = re.compile(r"^`?([A-Za-z][A-Za-z0-9]*-\d+)`?\s*:\s*(.+)$")


def _name(raw: str):
    """A proposed domain's name, without one trailing full stop and backticks round it all; None
    when tiles.domain_name refuses it."""
    name = raw.strip()
    for _ in range(2):
        if name.endswith("."):
            name = name[:-1].rstrip()
        if len(name) > 1 and name.startswith("`") and name.endswith("`"):
            name = name[1:-1].strip()
    try:
        return tiles.domain_name(name)
    except ValueError:
        return None


def parse(text: str, given: dict) -> dict:
    """The moves an ORGANIZE.md proposes, {KEY: name} in the order written. A move is a bullet
    line "- KEY: Name" ("*" bullets, bold, backticks round the key or the name, a trailing full
    stop and extra spaces allowed). Only keys in given ({KEY: its domain now or None}) count,
    with a name tiles.domain_name takes, and only the first such line for a key; a story
    proposed for the domain it is in already (ignoring case) is no move. Every other line is
    ignored."""
    out, seen = {}, set()
    for line in text.splitlines():
        bullet = _BULLET.match(line)
        found = _MOVE.match(bullet.group(1).replace("*", "")) if bullet else None
        if not found:
            continue
        key, name = found.group(1).upper(), _name(found.group(2))
        if key not in given or key in seen or name is None:
            continue
        seen.add(key)
        if (given[key] or "").lower() != name.lower():
            out[key] = name
    return out


# ---------- whether Organize can run ----------
def why_not(store, **kw):
    """None when Organize can run for the store; else one sentence saying why not. It needs what
    pickup needs (pickup.cannot_run), but not "pickup": true, and a store whose permission lets
    a session write its file. kw: installed and lacks, as pickup.why_off takes."""
    why = pickup.cannot_run(store, **kw)
    if why:
        return "Organize is off: " + why
    if store.permission == "plan":
        return f"Organize is off: the store's permission is plan, so a session can't write {OUTPUT}."
    return None


# ---------- the run in the ledger ----------
SLOT = "organize"  # the run's key in the ledger's state


def of(store, slot=SLOT):
    """The run in the ledger (under slot), or None."""
    return ledger.Ledger(store).read().get(slot)


def _same(rec, run) -> bool:
    """Whether a ledger record is this run (started at the same moment)."""
    return bool(rec) and rec.get("started") == run.get("started")


def _set(store, run: dict, was: str, fields: dict, slot=SLOT) -> bool:
    """Set fields on the run's record if it is still in state was; a record that is no longer
    in need loses its why. False, with nothing changed, when it was ended meanwhile."""
    with ledger.Ledger(store).change() as state:
        rec = state.get(slot)
        if not _same(rec, run) or rec["state"] != was:
            return False
        rec.update(fields)
        if rec["state"] != "need":
            rec.pop("why", None)
        return True


def _path(store, key=KEY) -> Path:
    """Where the run's worktree goes in the main repo: where a story with this key would have its own."""
    return pickup.worktree(store, {"key": key, "title": ""}, store.main_repo)


def _cut_worktree(store, key=KEY, who="a sort") -> Path:
    """A detached worktree of the main repo's trunk at the run's path. What is there already is
    cleared only when it is a detached worktree (drafts.clear_for); anything else, or other
    trouble, raises RuntimeError with git's words or a plain sentence naming the path. who: what
    cuts it, as the refusal says it."""
    repo, path = store.main_repo, _path(store, key)
    drafts.clear_for(repo.path, path, who)
    pickup._git(repo.path, "worktree", "prune")
    pickup.keep_out(repo.path, path)
    made = pickup._git(repo.path, "worktree", "add", "--detach", str(path), repo.trunk)
    if made.returncode != 0:
        raise RuntimeError(made.stderr.strip() or "git worktree add failed")
    return path


def _copy_docs(store, cut: Path, chosen: list):
    """Give the worktree its own copies of the stories' plan docs, at docs/<link>/<their path>,
    where the prompt points. A docs/<link> link the app's checkout brought along is taken out
    first, so nothing written there reaches the store."""
    home = cut / "docs" / store.link
    if home.is_symlink():
        home.unlink()
    for s in chosen:
        if s.get("doc"):
            to = home / s["doc"]
            to.parent.mkdir(parents=True, exist_ok=True)
            to.write_bytes((store.dir / s["doc"]).read_bytes())


def _launch(store, args, cwd: Path, text: str) -> dict:
    """Start the run's background session in cwd, in the store's permission mode, with no
    hooks and no folder added (agent.start; not pickup._launch, which gives a session the
    store's hooks and, for Copilot and Antigravity, the store's folder): {"state": "started",
    "id", "session"} or {"state": "failed", "error"}."""
    began = agent.start(args, cwd, store.permission, text)
    if "error" in began:
        return {"state": "failed", "error": began["error"]}
    return {"state": "started", "id": began["id"], "session": pickup.full_id(began["id"])}


def start(store, note, refile: bool, *, sessions) -> dict:
    """Start a run: refused (Refused, with why) while one is in the ledger, when Organize can't
    run (why_not) or when there is nothing to sort. The run is recorded working first, so a
    second start is refused at once; then a detached worktree of the main repo is cut, the
    stories' plan docs copied in (_copy_docs), and one background session started there
    (_launch). The record as it then is: working with
    the session's id, or need with why when the worktree or the session didn't come (any
    worktree cut is removed again)."""
    why = why_not(store)
    if why:
        raise Refused(why)
    held = of(store)
    if held and held["state"] == "ending" and not end(store, sessions=sessions):
        raise Refused(STILL_ENDING)
    snap = tiles.Snapshot.read(store)
    chosen = stories(store, snap, refile)
    note = (note or "").strip() or None
    run = {"state": "working", "started": sessions.now(), "note": note, "refile": bool(refile),
           "stories": [{"key": s["key"], "title": s["title"], "domain": s["domain"]} for s in chosen],
           "repo": store.main_repo.name, "worktree": str(_path(store))}
    with ledger.Ledger(store).change() as state:
        if state.get(SLOT):
            raise Refused(STILL_ENDING if state[SLOT]["state"] == "ending" else "A sort is already under way: finish it or cancel it first.")
        if not chosen:
            raise Refused("There are no stories to sort." if refile else
                          "Every story is in a domain already; tick Re-sort filed stories too to sort them again.")
        state[SLOT] = dict(run)
    args = pickup.session_args({"key": "Organize", "title": "domains", "model": store.model})
    return _launch_run(store, run, sessions=sessions, docs=chosen, args=args,
                       text=lambda: prompt(store, chosen, domains(snap), note))


def _launch_run(store, run: dict, *, sessions, docs, args, text, slot=SLOT, key=KEY, who="a sort") -> dict:
    """The rest of a start, once the run is recorded working under slot: a detached worktree of
    the main repo cut at the key's path (_cut_worktree), the plan docs of docs ([{"doc"}])
    copied in (_copy_docs), and one background session started there (_launch) with text()
    for its prompt. The record as it then is: working with the session's id, or need with why
    when the worktree or the session didn't come (any worktree cut is removed again); one ended
    while it started is ending, and what started goes too."""
    cut = None
    try:
        cut = _cut_worktree(store, key, who)
        _copy_docs(store, cut, docs)
        launched = _launch(store, args, cut, text())
        failed = None if launched["state"] == "started" else f"The session didn't start: {launched.get('error') or 'no reason given'}"
    except Exception as e:  # git's trouble, or anything else
        launched, failed = {}, f"The worktree couldn't be made: {e}" if cut is None else f"The session didn't start: {e}"
    if failed:
        stayed = cut is not None and drafts._drop(store.main_repo.path, cut)
        _set(store, run, "working", {"state": "need", "why": failed, **({} if stayed else {"worktree": None, "tidied": True})}, slot)
        return of(store, slot) or {**run, "state": "need", "why": failed}
    fields = {"id": launched["id"], "session": launched.get("session")}
    if not _set(store, run, "working", fields, slot):  # cancelled while it started: what started goes too
        _tidy(store, {**run, **fields}, sessions, slot)
        return {**run, **fields, "state": "ending"}
    return of(store, slot)


def _tidy(store, run: dict, sessions, slot=SLOT) -> bool:
    """Retire the run's session (it must then be gone from the session list) and remove its
    worktree with what it changed. A path git doesn't list as a detached worktree of the main
    repo is not the run's: it is left alone, and said on pickup's log. False when the session is
    still listed, a list can't be read or the worktree won't go."""
    sid = run.get("id")
    if sid:
        sessions.retire(sid)
        listing = sessions.sessions()
        if listing is None or sid in listing:
            return False
    if not run.get("worktree"):
        return True
    repo = store.repo(run.get("repo") or "") or store.main_repo
    path = Path(run["worktree"])
    listing = drafts._listed(repo.path)
    if listing is None:
        return False
    block = listing.get(path.resolve())
    if block is None and not path.exists():
        return True
    if block is not None and "detached" in block:
        return not drafts._drop(repo.path, path)
    pickup.log(f"{slot}: left {path} alone, it is not a worktree this run cut")
    return True


def end(store, *, sessions, slot=SLOT) -> bool:
    """End the run, in any state: marked ending first (so a crash part-way leaves it findable),
    its session retired and worktree removed (_tidy), then its record taken out. True when it is
    gone, as it is when there was none; a step that doesn't finish leaves it ending and gives
    False, for a later look to finish."""
    with ledger.Ledger(store).change() as state:
        rec = state.get(slot)
        if not rec:
            return True
        rec["state"] = "ending"
        run = dict(rec)
    if not run.get("tidied") and not _tidy(store, run, sessions, slot):
        return False
    with ledger.Ledger(store).change() as state:
        if _same(state.get(slot), run):
            del state[slot]
    return True


# ---------- the look ----------
def busy(store, rec=None, slot=SLOT) -> bool:
    """Whether the run needs looking at again soon: at work, being ended, or done with but not
    yet tidied up. rec: the record (of), when the caller has read it already."""
    rec = of(store, slot) if rec is None else rec
    return bool(rec) and (rec["state"] in ("working", "ending") or not rec.get("tidied"))


def _output(run: dict, name=OUTPUT):
    """The text of the file the run's session writes (name, at its worktree's top), or None when there is none."""
    try:
        return (Path(run["worktree"]) / name).read_text(encoding="utf-8", errors="replace") if run.get("worktree") else None
    except OSError:
        return None


def _proposal(store, run: dict, text: str) -> dict:
    """The fields of a run whose session wrote text: ready with its moves ([{key, title, domain,
    new, from}], a name a tile already carries spelt as the board spells it, new when none does,
    from the story's own Domain line when it was given, None for none; Apply sends it as
    /api/domain's expect) and
    the keys it left as they are; or need when there is no move."""
    known = _known(tiles.Snapshot.read(store))
    given = {s["key"]: s.get("domain") for s in run.get("stories") or []}
    titles = {s["key"]: s.get("title") or "" for s in run.get("stories") or []}
    moves, spelt = [], dict(known)
    for key, name in parse(text, given).items():
        new = name.lower() not in known
        name = spelt.setdefault(name.lower(), name)  # a new name the proposal spells twice is spelt as it first did
        if (given[key] or "").lower() != name.lower():
            moves.append({"key": key, "title": titles[key], "domain": name, "new": new, "from": given[key]})
    if not moves:
        return {"state": "need", "why": "The agent proposed no moves."}
    moved = {m["key"] for m in moves}
    return {"state": "ready", "moves": moves, "left": [k for k in given if k not in moved]}


def look(store, *, sessions) -> list:
    """One pass over the run. An ending run is ended again. A working one moves on by what its
    session is doing, once SETTLE has passed since it started: busy is left alone; otherwise its
    ORGANIZE.md is read (parse), and a file with some move makes it ready, whatever the session
    is doing; without one, waiting (a question, a permission prompt, an error) needs you with
    that said, and idle or gone from the list needs you (no file, or no move); a list that
    can't be read changes nothing. A run that leaves working has its session
    retired and worktree removed, then or on a later look. The lines to log."""
    return _look(store, sessions=sessions, read=lambda rec, text: _proposal(store, rec, text),
                 ready=lambda fields: f"{len(fields['moves'])} moves ready to review")


def _look(store, *, sessions, read, ready, slot=SLOT, output=OUTPUT) -> list:
    """look, for the run under slot whose session writes output: read(rec, text) gives the
    fields of a run whose file says text (state ready, or need with why), and ready(fields) the
    words logged when it is ready."""
    rec = of(store, slot)
    if not rec:
        return []
    if rec["state"] == "ending":
        return [f"{slot}: the run is ended"] if end(store, sessions=sessions, slot=slot) else []
    said = []
    if rec["state"] == "working":
        if sessions.now() - rec.get("started", 0) < ledger.SETTLE:
            return said
        listing = sessions.sessions()
        if listing is None:
            return said
        sid = rec.get("id")
        seen = listing.get(sid) if sid else None
        if seen == "busy":
            return said
        text = _output(rec, output)
        fields = read(rec, text) if text is not None else None
        waits = None
        if not (fields and fields["state"] == "ready") and seen:
            waits = sessions.why(sid)
            if seen == "waiting" and not waits:
                waits = "question"
        if fields and fields["state"] == "ready":
            pass  # a proposal written is kept, whatever the session did after it
        elif waits:
            fields = {"state": "need", "why": f"{agent.label()} stopped before it finished: {ledger.WAITING.get(waits, ledger.WAITING['question'])}."}
        elif fields is None:
            fields = {"state": "need", "why": f"The session is gone and wrote no {output}." if seen is None
                      else f"The session finished without writing {output}."}
        if not _set(store, rec, "working", fields, slot):
            return said
        said.append(f"{slot}: {ready(fields)}" if fields["state"] == "ready"
                    else f"{slot}: the run needs you: {fields['why']}")
        rec = of(store, slot)
    if rec and rec["state"] in ("ready", "need") and not rec.get("tidied") and _tidy(store, rec, sessions, slot):
        _set(store, rec, rec["state"], {"tidied": True, "worktree": None}, slot)
    return said


def summary(store):
    """What the page is told of the run: None when there is none (or it is ending), else
    {"state", "started", "note", "refile"} with "why" when it needs you and "moves" and "left"
    when it is ready."""
    rec = of(store)
    if not rec or rec["state"] == "ending":
        return None
    out = {"state": rec["state"], "started": rec.get("started"), "note": rec.get("note"), "refile": bool(rec.get("refile"))}
    if rec.get("why"):
        out["why"] = rec["why"]
    if rec["state"] == "ready":
        out.update(moves=rec.get("moves") or [], left=rec.get("left") or [])
    return out
