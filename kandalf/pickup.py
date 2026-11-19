"""Pickup: a tile that lands in In flight is handed to Claude Code for development.

The board server watches the store's plans.md (serve.py: watch_pickups). When a keyed tile
appears in the In flight column, however it got there (the board, an editor, an agent),
this starts a background Claude Code session for it:

    claude --bg --model opus -n "PLN-74 Title" --permission-mode auto "<the tile and the rules>"

in a git worktree of the store's app repo, beside it (myapp-pln-74), on a branch cut
from the trunk (pln-74-title); "worktree" and "branch" in board.json name them otherwise
(branch, worktree). A tile with a "Repos: api, web" line gets a worktree in each of those
repos on that one branch; the session starts in the first and is given the rest (--add-dir).
The In flight column is any named like one (in flight, in progress, doing, WIP...: IN_FLIGHT_RE), or
the one "pickupColumn" names (is_pickup_column). The session is told the tile, what the
board reads back from it, and the store's working rules (its pickup.md, else the default in
templates/). Every call to the claude CLI goes through claude_code.py (the agent seam).
`claude agents` lists the session; `claude attach <id>`
opens it, and the id is written into the tile ("Claude session `ab12cd34`"). It runs in the
store's permission mode ("permission" in board.json, default auto): in auto mode it works
without stopping to ask, and Claude Code's own auto-mode checks decide what it may run.

The model is opus unless the store's board.json sets "model", or the tile has a line of its
own reading "Model: sonnet" (model_for). Change a tile's model while it is in flight and its
session is stopped and resumed on the new one (switch): a running session can't be told to
change model any other way.

One session at a time: while one is at work, the next tile waits its turn, and the board
page asks whether to start it in parallel anyway (start_now). A master and the sub-stories
that arrive with it are one job. A tile that says "No pickup" is left alone, as are the
tiles already in flight the first time the board looks.

A session is at work while `claude agents` shows it busy. Once it finishes its turn or is
interrupted it is idle, and the next tile starts; if it is stopped, closed or removed it is
lost, and its tile flashes on the board. An idle session that waits for input (it was
interrupted, or it asked a question: read from `claude logs`, idle_reason) is marked so. Either way the tile gets a Restart button
(restart): that resumes the same conversation, or, when it can't be resumed, starts a new
session in the same worktree. The session is told to keep a handoff note on the tile as it
works, so a new session can take over from the tile and the branch alone.

On only with "pickup": true in board.json, for a store with an "app", and with a claude CLI
that has what pickup needs (why_off says what is in the way).
"""

import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

from . import agent
from . import gitlog

# The columns that mean under way, however they are spelt: In flight, in-flight, In progress (or a slip
# of it, "In progrees"), Doing, WIP, Working, Underway, Ongoing, Active; never one that says "not".
IN_FLIGHT_RE = re.compile(r"^(?!.*\bnot\b).*\b(?:in[\s_-]*flight|progre\w*|doing|wip|working|under[\s_-]*way|ongoing|active)\b", re.I)  # same in board.html
OPT_OUT_RE = re.compile(r"\bno pickup\b", re.I)
# A tile picks its own model with a line of its own, "Model: sonnet" (an alias or a full model id).
# A tile says its story is finished and waiting for review with a line of its own that
# starts "Ready for review" (the session writes "**Ready for review:** <gate results>").
# board.html reads the same wording (READY_RE there) and outlines the tile.
READY_RE = re.compile(r"^\s+(?:\*\*)?Ready for review\b[:.]?(?:\*\*)?[:.]?[ \t]*(.*)$", re.I | re.M)
REPOS_RE = re.compile(r"^[ \t]+repos?:[ \t]*(\S.*?)\.?[ \t]*$", re.I | re.M)  # same in board.html
MODEL_RE = re.compile(r"^\s+model:\s*`?([A-Za-z0-9][A-Za-z0-9._\[\]-]*)`?\.?\s*$", re.I | re.M)
STATE = "board-pickup.json"
KEEP = 30  # jobs remembered
# Seconds a session just started counts as at work, before it has shown itself busy.
SETTLE = int(os.environ.get("BOARD_PICKUP_SETTLE", "90"))
_lock = threading.Lock()


def is_pickup_column(store, name: str) -> bool:
    """Whether a column's tiles are picked up: the one the store's "pickupColumn" names (any
    case), else any In flight column (IN_FLIGHT_RE). board.html asks the same (isPickupColumn)."""
    if store.pickup_column:
        return name.strip().lower() == store.pickup_column.lower()
    return bool(IN_FLIGHT_RE.search(name))


# ---------- whether pickup can run ----------
_lacking = {}  # the installed claude's answer, worked out once


def _lacks() -> list:
    if "lacks" not in _lacking:
        _lacking["lacks"] = agent.cli_lacks()
    return _lacking["lacks"]


def why_off(store, *, installed=agent.installed, lacks=_lacks):
    """None when pickup can run for the store; else one sentence saying why not, and how to
    fix it. The claude CLI is asked only once pickup is on and the app is there."""
    if not store.pickup:
        return 'Pickup is off for this store: set "pickup": true in its board.json to turn it on.'
    if not store.app:
        return 'Pickup is off: board.json names no "app" repo for sessions to work in, so set "app" to its path.'
    if not store.app.is_dir():
        return f'Pickup is off: the app folder isn\'t there at {store.app}, so fix "app" in board.json.'
    naming = naming_problem(store)
    if naming:
        return naming
    if store.repo_problem:
        return f'Pickup is off: {store.repo_problem}, so fix "repos" in board.json.'
    if store.agent_problem:
        return f'Pickup is off: {store.agent_problem}, so fix "agent" in board.json.'
    if not installed():
        return f"Pickup is off: `{agent.cli()}` isn't on your PATH, so install {agent.label()} or put it there and restart the board."
    lacking = lacks()
    if lacking:
        named = ", ".join(lacking[:-1]) + " and " + lacking[-1] if len(lacking) > 1 else lacking[0]
        return f"Pickup is off: the installed {agent.label()} lacks {named}, so update it ({agent.update_hint()}) and restart the board."
    return None


def enabled(store) -> bool:
    return why_off(store) is None


# ---------- the queue's state ----------
# {"inflight": [keys] or None before the first look,
#  "jobs": [{"key", "title", "with": [sub-story keys], "text": the tiles' markdown, "at",
#            "state": "waiting" | "started" (at work) | "idle" | "lost" | "ended" | "failed",
#            "behind": the key it waits for, "model", "id", "session": the full session id,
#            "branch", "worktree", "started", "error",
#            "master", "limit": a master's sub-story, and how many of them work at once}]}
def state_path(store) -> Path:
    """In the store's .git, beside the commit queue; a store that isn't a repo keeps it as a dotfile."""
    if store.root:
        return gitlog.git_dir(store.root) / STATE
    return store.dir / ("." + STATE)


def load(store) -> dict:
    p = state_path(store)
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {"inflight": None, "jobs": []}


def save(store, state: dict):
    state["jobs"] = state["jobs"][-KEEP:]
    p = state_path(store)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    os.replace(tmp, p)


SESSIONS_RE = re.compile(r"^\s+sessions:\s*`?(\d+)`?\.?\s*$", re.I | re.M)  # a master's "Sessions: 3" line


def lineage(by_key: dict, key: str) -> list:
    """The in-flight tiles a tile is part of, nearest first: its master, its master's master."""
    out, seen, up = [], {key}, by_key[key]["parent"]
    while up in by_key and up not in seen:
        out.append(by_key[up])
        seen.add(up)
        up = by_key[up]["parent"]
    return out


def family_text(by_key: dict, key: str) -> str:
    """A tile's markdown, then that of the masters above it: what its session is given, and
    where its model and its family's session limit are read from (the nearest line counts)."""
    return "\n".join(t["text"] for t in [by_key[key], *lineage(by_key, key)])


def entered(prev, tiles, sessions: int = 2) -> list:
    """The jobs for the tiles that are in flight now and weren't before. tiles: the In flight
    tiles, [{"key", "title", "text", "parent"}] (serve.in_flight_tiles). A master that arrives
    with sub-stories gets no session of its own: each sub-story gets one, given the master's
    tile to read, and "limit" of them are at work at once (the master's "Sessions: n" line,
    else sessions). A sub-story that joins a master already in flight is one of them too."""
    by_key = {t["key"]: t for t in tiles}
    new = {t["key"] for t in tiles if t["key"] not in prev}
    masters = {by_key[k]["parent"] for k in new} & new  # arriving with a sub-story: the umbrella, not a job
    jobs = []
    for key in (t["key"] for t in tiles if t["key"] in new):
        above, text = lineage(by_key, key), family_text(by_key, key)
        if key in masters or OPT_OUT_RE.search(text):
            continue
        job = {"key": key, "title": by_key[key]["title"], "with": [], "text": text}
        if above:
            limit = SESSIONS_RE.search(text)
            job.update(master=above[-1]["key"], limit=max(1, int(limit.group(1))) if limit else sessions)
        jobs.append(job)
    return jobs


# ---------- starting a session ----------
def ready_note(text: str):
    """What a tile's "Ready for review" line says, "" if it says nothing more, None if the
    tile has no such line."""
    found = READY_RE.search(text)
    return found.group(1).strip() if found else None


def model_for(store, text: str) -> str:
    """The model a tile's session runs on: the tile's own "Model: x" line, else the store's
    "model" (board.json; opus unless set)."""
    own = MODEL_RE.search(text)
    return own.group(1) if own else store.model


def repo_names(text: str):
    """The repos named on the first "Repos: a, b" line in text (a tile's family_text, so the
    nearest line counts), in order, any case, no duplicates. None if there is no such line."""
    found = REPOS_RE.search(text)
    if not found:
        return None
    names = []
    for name in re.split(r"[,\s]+", found.group(1).replace("`", "")):
        if name and name.lower() not in [n.lower() for n in names]:
            names.append(name)
    return names


def resolve_repos(store, names):
    """([Repo], None) for the repos a tile wants (None: the main repo only), or ([], why) if one
    isn't in board.json, isn't there or isn't a git repo."""
    if names is None:
        return [store.main_repo], None
    if not names:
        return [], "the Repos line names no repo"
    repos = []
    for name in names:
        repo = store.repo(name)
        if repo is None:
            return [], f"no repo named `{name}` in board.json"
        if not repo.path.is_dir():
            return [], f"repo `{repo.name}` isn't there at {repo.path}"
        if not (repo.path / ".git").exists():
            return [], f"repo `{repo.name}` isn't a git repo"
        repos.append(repo)
    return repos, None


def session_args(job) -> list:
    """The `claude --bg` arguments that say which model and what the session is called."""
    model = ["--model", job["model"]] if job.get("model") else []
    return [*model, "-n", f"{job['key']} {job['title']}"[:60]]


def slug(title: str, limit: int = 40) -> str:
    words = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return words if len(words) <= limit else words[:limit].rsplit("-", 1)[0]


def fill(pattern: str, **values) -> str:
    """pattern with each {name} of values put in; any other brace is left as it is."""
    return re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), m.group(0)), pattern)


def names(store, job, repo=None) -> dict:
    """The placeholders of the store's "branch" and "worktree" patterns, for a job in a repo
    (default: the main repo; {app} is the repo's folder name)."""
    folder = repo.path if repo else store.app
    return {"key": job["key"].lower(), "KEY": job["key"], "slug": slug(job.get("title") or ""),
            "app": folder.name if folder else ""}


def branch(store, job, repo=None) -> str:
    """The job's feature branch: the store's "branch" pattern (default {key}-{slug}); {app}
    is the repo's, the one the session starts in."""
    return fill(store.branch_pattern, **names(store, job, repo)).rstrip("-")


def worktree(store, job, repo=None) -> Path:
    """The job's worktree in a repo (default: the main one): the store's "worktree" pattern
    (default {app}-{key}), under the repo's parent folder."""
    folder = repo.path if repo else store.app
    return Path(os.path.normpath(folder.parent / fill(store.worktree_pattern, **names(store, job, repo))))


def naming_problem(store):
    """None when the store's "branch" and "worktree" patterns can be used; else one sentence
    saying what is wrong with them. A worktree stays within the folder above the app's parent."""
    for name, pattern in (("branch", store.branch_pattern), ("worktree", store.worktree_pattern)):
        if "{key}" not in pattern and "{KEY}" not in pattern:
            return f'Pickup is off: "{name}" in board.json ({pattern}) has no {{key}}, so every story would share one; put {{key}} in it.'
    where = os.path.normpath(store.worktree_pattern)
    if os.path.isabs(where) or where.startswith("~") or where in (".", "..") or where.startswith("../.."):
        return (f'Pickup is off: "worktree" in board.json ({store.worktree_pattern}) must be a path relative to the folder the app is in, '
                "climbing at most one folder (../), so fix it.")
    return None


def worktree_label(store, path, repo=None) -> str:
    """How a tile names a worktree: its path from the repo's parent folder (the main repo's by
    default), just the folder's name for one beside the repo."""
    base = repo.path.parent if repo else store.app.parent if store.app else None
    rel = os.path.relpath(path, base) if base else ""
    return rel if rel and not rel.startswith("../..") and not os.path.isabs(rel) else Path(path).name


def tile_cuts(store, tile_text: str):
    """(branch, [(repo name, Repo or None, worktree path or None)]) from a tile's session line:
    one cut for the old form (the main repo's), one per repo for the worktrees form. A repo
    board.json no longer has reads as (name, None, None). None when the tile has no session line."""
    found = TILE_REPOS_RE.search(tile_text)
    if found:
        cuts = []
        for name, label in re.findall(r"([^`,]+?) `([^`]+)`", found.group(2)):
            name = name.strip()
            repo = store.repo(name)
            cuts.append((name, repo, Path(os.path.normpath(repo.path.parent / label))) if repo else (name, None, None))
        return found.group(1), cuts
    found = TILE_BRANCH_RE.search(tile_text)
    if not found or not store.app:
        return None
    main = store.main_repo
    return found.group(1), [(main.name, main, Path(os.path.normpath(store.app.parent / found.group(2))))]


def tile_worktree(store, tile_text: str):
    """The worktree a tile's session line names, as a path (the first repo's when it names
    several); None when it names none."""
    found = tile_cuts(store, tile_text)
    return found[1][0][2] if found and found[1] else None


# The working rules every session is given: the store's pickup.md, else this default ({key}
# in it is the tile's key). They follow "Tick each sub-task with its commit as it lands." in
# HANDOFF: a first line that is a sentence carries on that rule, one that starts "- " is a
# rule of its own.
RULES = Path(__file__).resolve().parent / "templates" / "pickup.md"
RULES_FILE = "pickup.md"


def house_rules(store, key: str) -> str:
    """The store's working rules for a session on the tile key: its pickup.md (an empty one:
    none), else the default."""
    own = store.dir / RULES_FILE
    return (own if own.is_file() else RULES).read_text(encoding="utf-8").strip().replace("{key}", key)


HANDOFF = """Keep the tile able to hand the work over. Your session can be closed at any moment, and a new one will have only the tile and this branch to go on:
- Commit as you go, so no finished work lives only in this session.
- Tick each sub-task with its commit as it lands."""
HANDOFF_NOTE = """
- Keep one indented "Handoff:" line on the tile, rewritten after each step: what is done, what you are doing now, what comes next, and anything not yet committed or decided. The board has already noted this session, the branch and the worktree on the tile ("Claude session ..."); leave that line as it is."""


def handoff(store, key: str) -> str:
    """What a session is told about keeping the tile able to hand the work over, with the
    store's working rules in it."""
    rules = house_rules(store, key)
    joined = ("\n" if rules.startswith("- ") else " ") + rules if rules else ""
    return HANDOFF + joined + HANDOFF_NOTE


def wanted(store, job):
    """The repos a job works in, in order: the main repo alone when its tile has no Repos line,
    [] when they can't be resolved (resolve_repos)."""
    return resolve_repos(store, job.get("want"))[0]


def repos_paragraph(store, job, branch_name: str, path: Path) -> str:
    """What a session working in more than the main repo is told about its repos, or "" for one
    that works in the main repo alone."""
    repos = wanted(store, job)
    if job.get("want") is None or [r.main for r in repos] == [True]:
        return ""
    cut = {c["name"]: c["worktree"] for c in job.get("repos") or []}
    lines = [f"- {r.name}: {cut.get(r.name, '(no worktree)')} (cut from {r.trunk})" for r in repos]
    return f"""

Repos: this story works in {len(repos)} repo{'s' if len(repos) > 1 else ''}, each in a git worktree of its own, all on the branch {branch_name}:
""" + "\n".join(lines) + f"""

You are in {path}; the others were added to your session, so read and edit them by their paths. The branch is shared: the same name in every repo, and each repo's commits are its own. Commit in each repo's own worktree, never in a repo's own checkout. "Don't merge, don't squash, don't push" below covers every repo."""


def prompt(store, job, branch_name: str, path: Path, again: bool = False) -> str:
    """What a new session is told. again: an earlier session on this tile can't be resumed,
    and this one takes over from what it left on the tile and the branch."""
    repos = wanted(store, job) if job.get("want") is not None else []
    start = repos[0] if repos and not repos[0].main else None  # a session that starts outside the main repo
    where, trunk = (f"the {start.name} repo", start.trunk) if start else ("the app", store.trunk)
    linked = f" The plans store is linked in at docs/{store.link}." if not start or (
        path / "docs" / store.link).is_symlink() else ""
    tiles = "this tile" if not job.get("with") else f"these tiles ({job['key']} and its sub-stories {', '.join(job['with'])})"
    if job.get("master"):
        tiles = (f"this tile, a sub-story of {job['master']}, whose tile follows it for you to read. Do only {job['key']}: "
                 f"the other sub-stories of {job['master']} have sessions of their own, some at work beside you now")
    if again:
        opening = f"""You are taking over {job['key']} "{job['title']}" on the {store.name} plans board. An earlier Claude session was developing it and has closed; it can't be resumed, so you carry on from what it left.

When it was picked up, {tiles} read as below. The board has moved on since: read the tile as it is now in {store.dir / 'plans.md'}, above all its "Handoff:" line and which sub-tasks are ticked.

{job.get('text', '')}

You are in that session's git worktree of {where} at {path}, on branch {branch_name} (cut from {trunk}).{linked}

First find where it stopped: `git log {trunk}..HEAD`, `git status` and the tile. Uncommitted changes in the worktree are its unfinished work: read them before you keep or drop them. Then carry on from there; don't redo what is done."""
    else:
        opening = f"""You are picking up {job['key']} "{job['title']}" from the {store.name} plans board: it was just moved to In flight, which is the signal to start developing it.

From {store.dir / 'plans.md'}, {tiles}:

{job.get('text', '')}

You are in a fresh git worktree of {where} at {path}, on branch {branch_name}, cut from {trunk}.{linked}

Before anything else, check nobody else has this: if the tile already notes a different branch or worktree, or another session is plainly working on it, stop and say so."""
    return f"""{opening}{repos_paragraph(store, job, branch_name, path)}

Follow this repo's CLAUDE.md and the filing rules in the header of plans.md. Read the plan doc the tile links, if it links one.

{handoff(store, job['key'])}

When every sub-task is ticked and every gate this repo asks for has passed, mark the story ready: add one indented line to the tile, starting `**Ready for review:**`, with the gate results, how many commits are on the branch, and what the reviewer should look at first. That line is what makes the board outline the tile for review; take it off again if you go back to work on the story. Then stop. Don't merge, don't squash, don't push, and don't move the tile to Done: those are the reviewer's to do."""


RESUME = """Your session was stopped or interrupted, and has been restarted from the plans board. Carry on with {key} from where you left off: check `git status` and the tile first, since either may have changed while you were away. Keep the tile's "Handoff:" line current as before."""


def _git(repo: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60)


def cut_of(store, job, repo):
    """The worktree the job already has in a repo, as a path; None when it has none. A job
    from before several repos has its one worktree in the main repo."""
    for cut in job.get("repos") or []:
        if cut["name"] == repo.name:
            return Path(cut["worktree"])
    return Path(job["worktree"]) if job.get("worktree") and not job.get("repos") and repo.main else None


def prepare(store, job, repo=None, name: str = None) -> tuple:
    """(worktree path, branch): the job's worktree in a repo (default the main one) beside it
    (the one it has already, when that is still there), made if it isn't there yet on the
    branch name (default branch(); cut from the repo's trunk), with the store linked in at
    docs/<link> as the repo's own checkout has it."""
    repo = repo or store.main_repo
    own = cut_of(store, job, repo)
    path = own if own and own.is_dir() else worktree(store, job, repo)
    name = name or branch(store, job, repo)
    if not path.exists():
        exists = _git(repo.path, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}").returncode == 0
        made = _git(repo.path, "worktree", "add", *([str(path), name] if exists else ["-b", name, str(path), repo.trunk]))
        if made.returncode != 0:
            raise RuntimeError(made.stderr.strip() or "git worktree add failed")
    else:  # picked up before: carry on where its branch is
        name = _git(path, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or name
    link, mine = repo.path / "docs" / store.link, path / "docs" / store.link
    if link.is_symlink() and not mine.exists() and not mine.is_symlink():
        mine.parent.mkdir(parents=True, exist_ok=True)
        mine.symlink_to(os.path.relpath(store.dir, mine.parent))
    return path, name


def prepare_all(store, job, repos) -> tuple:
    """([{"name", "worktree", "trunk"}], branch): a worktree in each of repos, all on one
    branch, the first's (its existing branch, when it has one). A repo that can't be cut
    raises RuntimeError("<repo>: <git's error>"); the worktrees already cut stay."""
    cuts, name = [], job.get("branch")
    if not name:  # a story cut before keeps the branch its worktrees are on
        for cut in job.get("repos") or []:
            if Path(cut["worktree"]).is_dir():
                name = _git(cut["worktree"], "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or None
                break
    for repo in repos:
        try:
            path, got = prepare(store, job, repo, name)
            name = name or got
        except RuntimeError as e:
            raise RuntimeError(f"{repo.name}: {e}") from None
        cuts.append({"name": repo.name, "worktree": str(path), "trunk": repo.trunk})
    return cuts, name


def extra_dirs(store, job) -> list:
    """The worktrees of the repos a job works in now, other than the one its session starts in:
    what the session is given besides its own folder. [] when its repos can't be resolved."""
    cut = {c["name"]: c["worktree"] for c in job.get("repos") or []}
    return [cut[r.name] for r in wanted(store, job) if r.name in cut and cut[r.name] != job.get("worktree")]


def full_id(short: str):
    """A background session's full id, which --resume needs to continue it under its own id."""
    return (agent.known() or {}).get(short)


def _launch(store, job, args, cwd, text: str, extra: dict, dirs=()) -> dict:
    """Start a background session in the store's permission mode, with the board's hooks
    from the store's .claude/settings.json when it has them, and the folders in dirs added
    to what it can work in: the fields to set on the job."""
    hooks = ["--settings", str(store.hooks_file)] if store.hooks_file.is_file() else []
    began = agent.start([*hooks, *args], cwd, store.permission, text, dirs, store_dir=store.dir)
    if "error" in began:
        return {"state": "failed", "error": began["error"], **extra}
    return {"state": "started", "id": began["id"], "session": full_id(began["id"]), **extra}


def _cuts_of(store, job) -> list:
    """The cuts a job has: its repos, else (from before several repos) its one worktree as the main repo's."""
    if job.get("repos"):
        return job["repos"]
    main = store.main_repo
    return [{"name": main.name, "worktree": job["worktree"], "trunk": main.trunk}] if job.get("worktree") and main else []


def start(store, job, again: bool = False) -> dict:
    """Start a new background session for the job. The fields to set on the job: started,
    with the session id, branch and worktree; or failed, with the error. again: it takes over
    from a session that can't be resumed (prompt)."""
    repos, error = resolve_repos(store, job.get("want"))
    if error:
        return {"state": "failed", "error": error}
    try:
        cuts, name = prepare_all(store, job, repos)
    except (OSError, RuntimeError, subprocess.SubprocessError) as e:
        return {"state": "failed", "error": str(e)}
    path = Path(cuts[0]["worktree"])
    wanted_now = {c["name"] for c in cuts}  # this story's cuts first, then earlier ones it no longer names
    before = _cuts_of(store, job)
    every = cuts + [c for c in before if c["name"] not in wanted_now]
    here = {**job, "worktree": str(path), "repos": every}
    return _launch(store, job, session_args(job), path, prompt(store, here, name, path, again),
                   {"branch": name, "worktree": str(path), "repos": every}, extra_dirs(store, here))


def resume(store, job, text: str = None) -> dict:
    """Continue the job's own conversation in the background, under the same session id (a
    copy, with a new id, if the CLI says the old one is still running), on the job's model.
    text: what it is told (default: that it was restarted). Fields as start."""
    path = Path(job.get("worktree") or "")
    if not job.get("session") or not path.is_dir():
        return {"state": "failed", "error": "no session or worktree to resume"}
    model = ["--model", job["model"]] if job.get("model") else []
    return _launch(store, job, ["--resume", job["session"], *model], path, text or RESUME.format(key=job["key"]), {},
                   extra_dirs(store, job))


SWITCH = """The plans board changed this story's model to {model}, so your session was stopped and restarted on it; a running session can't be told to change model any other way. Carry on with {key} from where you left off: check `git status` and the tile first, in case you were stopped mid-step. Keep the tile's "Handoff:" line current as before."""


def switch(store, job, model: str) -> dict:
    """Move a job's session to another model: stop it if it is at work, then resume the same
    conversation on that model. Fields as start; failed leaves a stopped session to Restart."""
    session = job.get("session") or full_id(job.get("id") or "")
    if not session:
        return {"state": "failed", "error": "its full session id isn't known"}
    try:
        if job["state"] == "started":
            agent.stop(job["id"])
    except (OSError, subprocess.SubprocessError) as e:
        return {"state": "failed", "error": str(e)}
    return resume(store, {**job, "session": session, "model": model}, SWITCH.format(model=model, key=job["key"]))


REPOS_CHANGED = """The plans board changed this story's repos to {repos}; your session was restarted with them. Your worktrees, all on the branch {branch}:
{lines}

Carry on with {key} from where you left off: check `git status` and the tile first, in case you were stopped mid-step. Commit in each repo's own worktree, never in a repo's own checkout. Keep the tile's "Handoff:" line current as before."""


def switch_repos(store, job, names) -> dict:
    """Move a job's session to the repos its tile names now: stop it if it is at work, cut a
    worktree in each new repo, then resume the same conversation with them added. When the
    starting repo changes, a new session takes over there instead. Every worktree ever cut
    stays on job["repos"]. Fields as start; failed leaves a stopped session to Restart."""
    repos, error = resolve_repos(store, names)
    if error:
        return {"state": "failed", "error": error}
    job = {**job, "repos": _cuts_of(store, job)}
    here = next((c["name"] for c in job.get("repos") or [] if c["worktree"] == job.get("worktree")), None)
    session = job.get("session") or full_id(job.get("id") or "")
    moved_start = here is not None and here.lower() != repos[0].name.lower()
    if not moved_start and not session:
        return {"state": "failed", "error": "its full session id isn't known"}
    try:
        if job["state"] == "started":
            agent.stop(job["id"])
    except (OSError, subprocess.SubprocessError) as e:
        return {"state": "failed", "error": str(e)}
    wants = {**job, "want": names}
    if moved_start:  # the old session can't work from another folder: a new one takes over
        return start(store, wants, again=True)
    return _add_repos(store, wants, repos, session)


def _add_repos(store, job, repos, session) -> dict:
    """Cut a worktree in each of repos the job hasn't one in, and resume its conversation with
    them added (a stopped session: the caller stops a running one). Fields as start."""
    try:
        cuts, name = prepare_all(store, job, repos)
    except (OSError, RuntimeError, subprocess.SubprocessError) as e:
        return {"state": "failed", "error": str(e)}
    cut_now = {c["name"] for c in cuts}
    every = cuts + [c for c in job["repos"] if c["name"] not in cut_now]
    lines = "\n".join(f"- {c['name']}: {c['worktree']}" for c in cuts)
    text = REPOS_CHANGED.format(repos=", ".join(c["name"] for c in cuts), branch=name, lines=lines, key=job["key"])
    if job.get("model_was") and job.get("model") and job["model_was"] != job["model"]:
        text += f" Your model is now {job['model']} too."
    return {**resume(store, {**job, "session": session, "repos": every}, text), "repos": every, "branch": name}


WAITING = {"interrupted": "it was interrupted", "question": "it asked a question", "error": "it ended on an error"}


def session_screen(sid: str) -> str:
    """The last words on a session's screen (agent.screen_tail), by id; "" when its output
    can't be read."""
    output = agent.logs(sid)
    return "" if output is None else agent.screen_tail(output)


def idle_reason(sid: str):
    """Why an idle session waits for input (agent.waiting_for), by id; None when its output
    can't be read."""
    output = agent.logs(sid)
    return None if output is None else agent.waiting_for(output)


# ---------- the queue ----------
def log(message: str):
    """A line on the board server's console."""
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} pickup: {message}", flush=True)


def _report(store, job, at, on_start=None, how="picked up"):
    """Stamp a job a session was just launched for, and say on the console how it went.
    on_start(store, job) is told of a session that did start (the server writes it into the tile)."""
    job.pop("behind", None)
    job["started"] = int(at)
    name = f"{job['key']} \"{job['title']}\""
    if job["state"] != "started":
        log(f"{name} could not be started: {job.get('error')}")
        return
    where = f", branch {job['branch']} in {job['worktree']}" if job.get("worktree") else ""
    log(f"{name} {how}: session {job['id']} (claude attach {job['id']}){where}")
    if on_start:
        try:
            on_start(store, job)
        except Exception as e:  # the session is running either way
            log(f"{job['key']}: its session could not be written to the tile: {e}")


def _begin(store, job, start, at, on_start=None):
    """Start a waiting job."""
    job.update(start(store, job))
    _report(store, job, at, on_start)


TRACKED = ("started", "idle", "lost")  # a session was started and its tile is still in flight


def tick(store, tiles, *, start=start, sessions=agent.sessions, switch=switch, reroute=switch_repos, why=idle_reason, now=time.time, on_start=None) -> dict:
    """One look at the board. tiles: the In flight tiles now. Queues the ones that arrived,
    drops a waiting one that left again, notes each session as at work, idle or lost, moves a
    session whose tile changed model onto that model or repos onto those repos, and starts the oldest waiting job if
    no session is at work. Returns the state."""
    with _lock:
        state = load(store)
        jobs = state["jobs"]
        by_key = {t["key"]: t for t in tiles}
        # A tile that says No pickup isn't counted as seen, so it is picked up once the words go.
        live = {j["key"] for j in jobs if j["state"] == "waiting" or j["state"] in TRACKED}
        held = [k for k in by_key if k not in live and OPT_OUT_RE.search(family_text(by_key, k))]
        for k in held:
            if k not in state.get("held", []):
                log(f"{k} is in flight but says No pickup: left alone until those words go")
        state["held"] = held
        keys = [k for k in by_key if k not in held]
        if state["inflight"] is None:  # the first look: what is in flight already is someone's
            state["inflight"] = keys
            save(store, state)
            return state
        # One tile, one session: a tile that already has a session (at work, idle or lost) or
        # a place in the queue isn't picked up again, however it came to look newly arrived.
        taken = {j["key"] for j in jobs if j["state"] == "waiting" or j["state"] in TRACKED}
        for job in entered(state["inflight"], tiles, store.sessions):
            if job["key"] in taken:
                log(f"{job['key']} is in flight again and keeps the session it has")
            else:
                want = repo_names(job["text"])
                jobs.append({**job, "model": model_for(store, job["text"]), "state": "waiting", "at": int(now()),
                             **({"want": want} if want is not None else {})})
        for j in jobs:
            if j["state"] == "waiting" and j["key"] not in by_key:
                log(f"{j['key']} left In flight before its turn: dropped from the queue")
        jobs[:] = [j for j in jobs if j["state"] != "waiting" or j["key"] in by_key]
        state["inflight"] = keys
        waiting = [j for j in jobs if j["state"] == "waiting"]
        latest = {j["key"]: j for j in jobs}
        texts = {t["key"]: t["text"] for t in tiles}
        for j in jobs:  # a tile whose session wrote "Ready for review" on it
            if j["state"] in TRACKED and j["key"] in texts:
                _mark_ready(j, ready_note(texts[j["key"]]))

        def closed_early(j):
            """ "ended" on a tile still in flight: how the first version of pickup left a stopped session."""
            return j["state"] == "ended" and j["key"] in by_key and latest[j["key"]] is j and bool(j.get("id"))

        tracked = [j for j in jobs if j["state"] in TRACKED or closed_early(j)]
        by_key = {t["key"]: t for t in tiles}
        for j in jobs:  # a failed start takes the tile's repos as they are now, so Retry tries those
            if j["state"] == "failed" and j["key"] in by_key and latest[j["key"]] is j:
                _rerepo(store, j, repo_names(family_text(by_key, j["key"])), reroute, now(), on_start)
        if waiting or tracked:
            there = sessions()
            if there is not None:
                for j in tracked:
                    _look(j, there.get(j.get("id")), j["key"] in by_key, now(), why)
                for j in [*tracked, *waiting]:
                    if j["key"] in by_key and j["state"] in ("waiting", *TRACKED):
                        text, was = family_text(by_key, j["key"]), j.get("model")
                        names = repo_names(text)
                        # a session that is moved onto other repos is restarted once, on the new model too
                        _remodel(store, j, model_for(store, text), switch, now(), on_start, defer=_moves(store, j, names))
                        _rerepo(store, j, names, reroute, now(), on_start, was)
                live = [j for j in tracked if j["state"] == "started"]
                # In turn: a tile on its own starts when no session is at work, a master's
                # sub-story while fewer than its limit are. A start that fails lets the next one go.
                while waiting and len(live) < waiting[0].get("limit", 1):
                    job = waiting.pop(0)
                    _begin(store, job, start, now(), on_start)
                    live += [job] if job["state"] == "started" else []
                for j in waiting:
                    if j.get("behind") != live[0]["key"]:
                        j["behind"] = live[0]["key"]
                        log(f"{j['key']} \"{j['title']}\" queued behind {j['behind']}")
        save(store, state)
        return state


def _remodel(store, job, model: str, switch, at, on_start=None, defer=False):
    """Bring a job in line with the model its tile asks for now. A session at work or idle is
    stopped and resumed on it; a queued or lost one just starts on it when it next starts."""
    was = job.get("model")
    if was == model:
        return
    job["model"] = model
    if was is None:  # a job from before the board chose models: nothing changed on the tile
        return
    if job["state"] not in ("started", "idle"):
        log(f"{job['key']} will run on {model}, not {was}, when its session starts")
        return
    if defer:  # the repos change too: that restart resumes on the new model
        return
    moved = switch(store, job, model)
    if moved["state"] == "started":
        job.update(moved)
        _report(store, job, at, on_start, f"switched from {was} to {model}")
    else:
        log(f"{job['key']} session {job.get('id')} could not be switched to {model}: {moved.get('error')}. Restart it from its tile")


def _folded(store, names):
    """The names of the repos a Repos line means, case-folded, in order: None and the main repo
    alone are the same. The line as written when it doesn't resolve."""
    repos, error = resolve_repos(store, names)
    return [n.lower() for n in names or []] if error else [r.name.lower() for r in repos if r]


def _moves(store, job, names) -> bool:
    """True when a session at work or idle has to be moved onto the repos names (_rerepo)."""
    return (job["state"] in ("started", "idle") and names != job.get("want")
            and _folded(store, names) != _folded(store, job.get("want")) and not resolve_repos(store, names)[1])


def _rerepo(store, job, names, reroute, at, on_start=None, model_was=None):
    """Bring a job in line with the repos its tile names now (names: repo_names). A session at
    work or idle is moved onto them (reroute); a queued, lost or failed one just takes them
    and starts with them. A line that doesn't resolve moves no session: it is said once."""
    if names == job.get("want") or _folded(store, names) == _folded(store, job.get("want")):
        job.pop("repos_error", None)
        return
    moving = job["state"] in ("started", "idle")
    repos, error = resolve_repos(store, names)
    if moving and error:
        if job.get("repos_error") != error:
            job["repos_error"] = error
            log(f"{job['key']} keeps its repos: its tile's Repos line doesn't work: {error}")
        return
    old = job.get("want")
    job.pop("repos_error", None)
    if names is None:
        job.pop("want", None)
    else:
        job["want"] = names
    if not moving:
        log(f"{job['key']} will work in {', '.join(names or ['the main repo'])} when its session starts")
        return
    moved = reroute(store, {**job, "want": old, "model_was": model_was}, names)
    if moved["state"] == "started":
        job.update(moved)
        _report(store, job, at, on_start, f"repos changed to {', '.join(r.name for r in repos)}")
    else:
        log(f"{job['key']} session {job.get('id')} could not be moved to {', '.join(names or ['the main repo'])}: {moved.get('error')}. Restart it from its tile")


def _mark_ready(job, note):
    """Keep job["ready"] in step with its tile's "Ready for review" line, and say when it appears."""
    if note is None:
        job.pop("ready", None)
    elif not job.get("ready"):
        job["ready"] = True
        log(f"{job['key']} is ready for review" + (f": {note}" if note else ""))


def _look(job, seen, in_flight: bool, at, why=idle_reason):
    """Set a tracked job's state from what its session is doing now: seen is "busy", "idle",
    or None when the session isn't there any more. An idle one is asked (why) whether it
    waits for input: job["waiting"] is then "interrupted" or "question"."""
    if at - job.get("started", 0) < SETTLE:  # just launched: it may not show as busy yet
        return
    was, waited = job["state"], job.get("waiting")
    if seen == "busy":
        job["state"] = "started"
    elif not in_flight:  # its tile moved on, and so has the session
        job["state"] = "ended"
    else:
        job["state"] = "idle" if seen == "idle" else "lost"
    waiting = why(job.get("id")) if job["state"] == "idle" else None
    if waiting:
        job["waiting"] = waiting
    else:
        job.pop("waiting", None)
    name = f"{job['key']} session {job.get('id')}"
    if waiting and waiting != waited:
        log(f"{name} is waiting for your input ({WAITING[waiting]})")
    elif job["state"] != was:
        said = {"started": "is at work again", "ended": "is finished with: its tile left In flight",
                "idle": "is idle: it finished its turn",
                "lost": "is gone (stopped, closed or removed): Restart is on its tile"}[job["state"]]
        log(f"{name} {said}")


def start_now(store, key: str, *, start=start, on_start=None) -> bool:
    """Start a waiting job although a session is at work: the page asked, and was told yes."""
    with _lock:
        state = load(store)
        job = next((j for j in state["jobs"] if j["key"] == key and j["state"] == "waiting"), None)
        if job is None:
            return False
        _begin(store, job, start, time.time(), on_start)
        save(store, state)
        return True


def _start_moved(store, job) -> bool:
    """True when the repos its tile names now start in another repo than the job's worktree
    is in: its session can't be resumed there, a new one has to take over."""
    here = next((c["name"] for c in _cuts_of(store, job) if c["worktree"] == job.get("worktree")), None)
    repos = resolve_repos(store, job.get("want"))[0] if job.get("want") is not None else []
    return bool(here and repos and repos[0].name.lower() != here.lower())


def restart(store, key: str, *, start=start, resume=resume, lookup=full_id, on_start=None) -> bool:
    """Get a tile's session going again after it went idle, was lost, or never started: the
    Restart button. Resumes the same conversation; when that can't be done, starts a new
    session in the same worktree to take over. False if the tile has no stopped session."""
    with _lock:
        state = load(store)
        job = next((j for j in reversed(state["jobs"]) if j["key"] == key), None)
        if job is None or job["state"] not in ("idle", "lost", "failed"):
            return False
        if job.get("id") and not job.get("session"):  # started before the board kept the full id
            job["session"] = lookup(job["id"])
        moved = _start_moved(store, job)  # its first repo changed: no resume, a new session takes over
        repos = resolve_repos(store, job.get("want"))[0] if job.get("want") is not None and _cuts_of(store, job) else []
        if job.get("session") and not moved and any(cut_of(store, job, r) is None for r in repos):
            again = _add_repos(store, {**job, "repos": _cuts_of(store, job)}, repos, job["session"])  # repos added to a stopped story's line
        else:
            again = resume(store, job) if job.get("session") and not moved else {"state": "failed"}
        if again["state"] == "started":
            job.update(again)
            _report(store, job, time.time(), on_start, "resumed")
        else:
            took_over = bool(job.get("id"))  # there was a session before: the new one takes over from it
            job.pop("error", None)
            job.update(start(store, job, took_over))
            _report(store, job, time.time(), on_start, "restarted with a new session" if took_over else "picked up")
        save(store, state)
        return True


# ---------- tidying up after an archived story ----------
TILE_BRANCH_RE = re.compile(r"(?:Claude|Copilot) session `[0-9a-f]+`.*?Branch `([^`]+)`, worktree `([^`]+)`")
TILE_REPOS_RE = re.compile(r"(?:Claude|Copilot) session `[0-9a-f]+`.*?Branch `([^`]+)`, worktrees ([^`,]+? `[^`]+`(?:, [^`,]+? `[^`]+`)*)\.")


def retire(session: str) -> bool:
    """Stop a background session and take it off the list (`claude stop`, `claude rm`).
    True when it was there and is gone now."""
    def listed():
        return session in (agent.known() or {})
    if not listed():
        return False
    agent.stop(session)
    agent.remove(session)
    return not listed()


def job_for(store, key: str):
    """The last session started on a tile, as the board remembers it (its branch, worktree and id)."""
    return next((j for j in reversed(load(store)["jobs"]) if j["key"] == key and j.get("branch") and j.get("worktree")), None)


def _tidy_cuts(store, tile_text: str, job):
    """(branch, session, [(name, Repo or None, worktree path or None)]) for a story to tidy: the
    tile's session line, else the board's record of the pickup (job); None when it has neither."""
    found = tile_cuts(store, tile_text)
    if found:
        return found[0], TILE_SESSION_RE.search(tile_text).group(1), found[1]
    if not job:
        return None
    raw = [(c["name"], c["worktree"]) for c in _cuts_of(store, job)]
    cuts = [(name, store.repo(name), Path(path)) for name, path in raw]
    return job["branch"], job.get("id") or "", [(n, r, p if r else None) for n, r, p in cuts]


def tidy(store, tile_text: str, *, merged, job=None, sessions=agent.sessions, retire=retire) -> list:
    """An archived story's worktrees, feature branch and Claude session are done with: remove
    the worktree in each repo the story worked in, delete the branch there, and stop and remove
    the session. Only what is safe goes, all or nothing across the repos. The branch must be in
    the trunk already in every repo that has it (merged(repo, branch) is True: merged, rebased
    or squashed), every worktree must hold nothing uncommitted, its session must not be at work,
    and every repo must still be in board.json; otherwise everything stays. Returns what was
    done, or why not, as lines for the console; [] for a tile no session was started on. The
    cuts are the ones on the tile's session line, or, when the tile has lost that line, the
    ones of job (the board's own record of the pickup). A store with "tidy": false keeps all,
    and gets []."""
    app = store.app
    if not store.tidy or not app or not (app / ".git").exists():
        return []
    got = _tidy_cuts(store, tile_text, job)
    if not got:
        return []
    name, session, cuts = got
    if name in {r.trunk for _, r, _ in cuts if r} | {store.trunk} or any(r and p and p.resolve() == r.path for _, r, p in cuts):
        return []
    many = len(cuts) > 1 or bool(cuts and cuts[0][1] != store.main_repo)  # the several-repo form, as with_session writes it
    link = f"docs/{store.link}"  # the store, linked in by prepare
    have, there = [], []  # repos that have the branch; (repo, path) of the worktrees that are there
    for _, repo, path in cuts:
        if not repo:
            continue
        if _git(repo.path, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}").returncode == 0:
            have.append(repo)
        listed = _git(repo.path, "worktree", "list", "--porcelain").stdout.split("\n")
        if path.is_dir() and f"worktree {path.resolve()}" in listed:
            there.append((repo, path))
    if many:
        held = ", ".join([f"branch {name} in {' and '.join(r.name for r in have)}"] * bool(have)
                         + [f"worktrees {' and '.join(f'{r.name}/{p.name}' for r, p in there)}"] * bool(there)
                         + [f"session {session}"] * bool(session))
    else:
        held = " and ".join([f"branch {name}"] * bool(have) + [f"worktree {p.name}" for _, p in there] + [f"session {session}"] * bool(session))
    held = held or f"branch {name}"
    unknown = [n for n, repo, _ in cuts if not repo]
    if unknown:
        return [f"kept {held}: repo `{unknown[0]}` isn't in board.json any more"]
    if any(path is None for _, _, path in cuts):
        return [f"kept {held}: a worktree's path isn't known"]
    behind = [r for r in have if merged(r, name) is not True]
    if behind:
        where = " or ".join(f"{r.name}'s {r.trunk}" for r in behind) if many else store.trunk
        return [f"kept {held}: the branch isn't in {where} yet"]
    if session and (sessions() or {}).get(session) == "busy":
        return [f"kept {held}: the session is still at work"]
    dirty = []
    for repo, path in there:
        files = [l for l in _git(path, "status", "--porcelain", "--untracked-files=all").stdout.splitlines()
                 if l != f"?? {link}"]
        if files:
            whose = f"{repo.name}'s" if many else "the"
            dirty.append(f"{len(files)} uncommitted file{'s' if len(files) != 1 else ''} in {whose} worktree")
    if dirty:
        return [f"kept {held}: {' and '.join(dirty)}"]
    done = [f"closed session {session}"] if session and retire(session) else []  # first: it sits in the worktree
    for _, repo, path in cuts:
        at = f" in {repo.name}" if many else ""
        if (repo, path) in there:
            if (path / link).is_symlink() and _git(path, "ls-files", "--error-unmatch", link).returncode != 0:
                (path / link).unlink()
            gone = _git(repo.path, "worktree", "remove", str(path))
            if gone.returncode != 0:
                return done + [f"kept worktree {path.name} and branch {name}{at}: {gone.stderr.strip() or 'git worktree remove failed'}"]
            done.append(f"removed worktree {path.name}{at}")
        _git(repo.path, "worktree", "prune")
        if repo in have:
            tip = _git(repo.path, "rev-parse", "--short", name).stdout.strip()
            cut = _git(repo.path, "branch", "-D", name)
            done.append(f"deleted branch {name}{at} (was {tip})" if cut.returncode == 0
                        else f"kept branch {name}{at}: {cut.stderr.strip() or 'git branch -D failed'}")
    return done


def discard(store, key: str, tile_text: str = "", *, retire=retire) -> list:
    """A deleted story's work is thrown away: its session stopped and removed, its worktree in
    each repo removed with whatever is uncommitted in it, and its feature branch deleted where
    it has no commits of its own (one that has is kept, so no commit is lost). Its jobs end, so
    nothing restarts or queues it. Returns what was done, as lines for the console."""
    with _lock:
        state = load(store)
        jobs = [j for j in state["jobs"] if j["key"] == key]
        for j in jobs:
            j["state"] = "ended"
        save(store, state)
    if not jobs and not TILE_SESSION_RE.search(tile_text):
        return []
    got = _tidy_cuts(store, tile_text, job_for(store, key))
    sessions = {j["id"] for j in jobs if j.get("id")} | ({got[1]} if got and got[1] else set())
    done = [f"closed session {sid}" for sid in sorted(sessions) if retire(sid)]
    if not got:
        return done
    name, _, cuts = got
    link = f"docs/{store.link}"
    for _, repo, path in cuts:
        if not repo or not path or path.resolve() == repo.path:
            continue
        at = f" in {repo.name}" if len(cuts) > 1 else ""
        listed = _git(repo.path, "worktree", "list", "--porcelain").stdout.split("\n")
        if path.is_dir() and f"worktree {path.resolve()}" in listed:
            if (path / link).is_symlink() and _git(path, "ls-files", "--error-unmatch", link).returncode != 0:
                (path / link).unlink()
            gone = _git(repo.path, "worktree", "remove", "--force", str(path))
            done.append(f"removed worktree {path.name}{at}" if gone.returncode == 0
                        else f"kept worktree {path.name}{at}: {gone.stderr.strip() or 'git worktree remove failed'}")
        _git(repo.path, "worktree", "prune")
        if name == repo.trunk or _git(repo.path, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}").returncode != 0:
            continue
        ahead = _git(repo.path, "rev-list", "--count", f"{repo.trunk}..{name}").stdout.strip()
        if ahead not in ("", "0"):
            done.append(f"kept branch {name}{at}: {ahead} commit{'s' if ahead != '1' else ''} not in {repo.trunk}")
            continue
        cut = _git(repo.path, "branch", "-D", name)
        done.append(f"deleted branch {name}{at}" if cut.returncode == 0
                    else f"kept branch {name}{at}: {cut.stderr.strip() or 'git branch -D failed'}")
    return done


TILE_SESSION_RE = re.compile(r"(?:Claude|Copilot) session `([0-9a-f]+)`")  # the line the server writes into a tile


def adopt(store, tiles) -> list:
    """At the server's start: the In flight tiles that never had a session (no job, no session
    line on the tile, no sub-story in flight) are taken out of what the board saw last, so its
    next look picks them up as if they had just landed. Returns their keys."""
    with _lock:
        state = load(store)
        had = {j["key"] for j in state["jobs"]}
        masters = {t["parent"] for t in tiles if t.get("parent")}
        fresh = [t["key"] for t in tiles if t["key"] not in had and t["key"] not in masters
                 and not TILE_SESSION_RE.search(t["text"]) and not OPT_OUT_RE.search(t["text"])]
        seen = state["inflight"] if state["inflight"] is not None else [t["key"] for t in tiles]
        state["inflight"] = [k for k in seen if k not in fresh]
        save(store, state)
        return fresh


def startup_report(store, tiles, *, sessions=agent.sessions) -> list:
    """The lines the server prints when it starts: each In flight tile with its Claude
    session and what that is doing, then any other session the board started that is still
    going. tiles: the In flight tiles now (serve.in_flight_tiles)."""
    there = sessions()
    jobs = {j["key"]: j for j in load(store)["jobs"]}  # the latest job for each key

    def doing(sid):
        if there is None:
            return f"session {sid} (the {agent.short()} sessions could not be listed)"
        if sid not in there:
            return f"session {sid} gone (Restart is on its tile)"
        if there[sid] != "idle":
            return f"session {sid} at work"
        waiting = next((j.get("waiting") for j in jobs.values() if j.get("id") == sid), None)
        return f"session {sid} " + (f"waiting for your input ({WAITING[waiting]})" if waiting else "idle")

    lines = [f"{len(tiles)} in flight:"] if tiles else ["Nothing in flight."]
    for t in tiles:
        job, noted = jobs.get(t["key"]), TILE_SESSION_RE.search(t["text"])
        sid = noted.group(1) if noted else (job or {}).get("id")
        if job and job["state"] == "waiting":
            what = f"queued behind {job['behind']}" if job.get("behind") else "queued"
        elif job and job["state"] == "failed":
            what = f"not started: {job.get('error')}"
        elif sid:
            what = doing(sid)
        else:
            what = "no session: its tile says No pickup" if OPT_OUT_RE.search(t["text"]) else "no session"
        if ready_note(t["text"]) is not None:
            what += ", ready for review"
        lines.append(f"  {t['key']} \"{t['title']}\": {what}")
    here = {t["key"] for t in tiles}
    others = [j for j in jobs.values() if j["key"] not in here and j.get("id") in (there or {})]
    if others:
        lines.append(f"{len(others)} other session{'s' if len(others) > 1 else ''} the board started:")
        lines += [f"  {j['key']} \"{j['title']}\": {doing(j['id'])}" for j in others]
    return lines


# ---------- a story's status, on request ----------
def age(seconds: float) -> str:
    """How long ago, in the largest unit that fits: 12 min, 3 h, 2 d."""
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))} min"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h"
    return f"{int(seconds // 86400)} d"


def git_facts(path: Path, trunk: str):
    """Where a worktree's branch stands: {"ahead": commits past the trunk, "last_at" and
    "last": the newest commit's time and subject, "dirty": files not committed}. None when
    the worktree isn't there."""
    if not Path(path).is_dir():
        return None
    ahead = _git(path, "rev-list", "--count", f"{trunk}..HEAD").stdout.strip()
    at, _, subject = _git(path, "log", "-1", "--format=%ct%x09%s").stdout.strip().partition("\t")
    dirty = [line for line in _git(path, "status", "--porcelain").stdout.splitlines() if line.strip()]
    return {"ahead": int(ahead or 0), "last_at": int(at or 0), "last": subject, "dirty": len(dirty)}


def _doing(seen) -> str:
    return {"busy": "at work", "idle": "idle", None: "gone",
            **{k: f"waiting for your input ({v})" for k, v in WAITING.items()}}.get(seen, "could not be checked")


def repo_status_text(name: str, facts, trunk: str, now: float) -> str:
    """One repo's part of a several-repo story's status: "api: 2 commits ahead of dev, last
    5 min ago: "subject"; nothing uncommitted." facts: git_facts, None when the worktree isn't there."""
    if facts is None:
        return f"{name}: worktree isn't there."
    if facts["ahead"]:
        commits = f"{facts['ahead']} commit{'s' if facts['ahead'] > 1 else ''}"
        said = f"{name}: {commits} ahead of {trunk}, last {age(now - facts['last_at'])} ago: \"{facts['last']}\""
    else:
        said = f"{name}: no commits yet"
    if facts["dirty"]:
        return said + f"; {facts['dirty']} file{'s' if facts['dirty'] > 1 else ''} not committed."
    return said + "; nothing uncommitted."


def status_text(sid: str, seen, facts, trunk: str, now: float) -> str:
    """One line on a story's session and branch. seen: "busy", "idle", "interrupted" or
    "question" for an idle one that waits for input, None when the session is gone, or
    "unknown" when the sessions couldn't be listed. facts: git_facts."""
    said = f"session `{sid}` {_doing(seen)}."
    if facts is None:
        return said + " Its worktree isn't there."
    if facts["ahead"]:
        commits = f"{facts['ahead']} commit{'s' if facts['ahead'] > 1 else ''}"
        said += f" Branch {commits} ahead of {trunk}; last commit {age(now - facts['last_at'])} ago: \"{facts['last']}\"."
    else:
        said += " Branch has no commits yet."
    if facts["dirty"]:
        return said + f" {facts['dirty']} file{'s' if facts['dirty'] > 1 else ''} not committed."
    return said + " Nothing uncommitted."


SUB_TASK_RE = re.compile(r"^  [-*] \[( |x|X)\] ", re.M)
ESTIMATE_RE = re.compile(r"~((\d+(?:\.\d+)?)(?:-(\d+(?:\.\d+)?))?([hdw]))(?= )")  # ~4h, ~1.5d, ~12-18h, in the tile's first line
HOURS = {"h": 1, "d": 24, "w": 168}  # estimates are wall-clock time (the board's estHours)


def eta_text(tile_text: str, started, now: float) -> str:
    """When a story should be done, from its tile: by the pace of its ticked sub-tasks since
    it started, else by what is left of its estimate. started: when it was picked up, if known."""
    ticks = [m.group(1) != " " for m in SUB_TASK_RE.finditer(tile_text)]
    done, total = sum(ticks), len(ticks)
    est = ESTIMATE_RE.search(tile_text.split("\n", 1)[0])
    when = lambda seconds: time.strftime("%Y-%m-%d %H:%M", time.localtime(now + seconds))
    if total and done == total:  # the work is written; whether it is done is what the Ready for review line says
        if ready_note(tile_text) is None:
            return f"All {total} sub-tasks ticked, but not done yet: its tests and review are still to finish (no Ready for review line)."
        return f"All {total} sub-tasks ticked, and it is ready for review."
    progress = f"{done} of {total} sub-tasks ticked" if total else ""
    if started is None:
        return (progress + ". " if progress else "") + "No ETA: when it started isn't known."
    spent = now - started
    if done:
        left = spent * (total - done) / done
        return f"{progress} in {age(spent)}; at that pace about {age(left)} to go (ETA {when(left)})."
    lead = f"{progress} after {age(spent)}" if progress else ""
    if not est:
        return (lead + ". " if lead else "") + "No ETA: no sub-tasks ticked and no estimate to go by."
    low, high = float(est.group(2)), float(est.group(3) or est.group(2))
    left = (low + high) / 2 * HOURS[est.group(4)] * 3600 - spent
    if left <= 0:
        return (lead + ". " if lead else "") + f"It is {age(-left)} past its ~{est.group(1)} estimate."
    by = f"its ~{est.group(1)} estimate about {age(left)} to go (ETA {when(left)})."
    return f"{lead}; by {by}" if lead else f"By {by}"


def status(store, key: str, tile_text: str, *, sessions=agent.sessions, why=idle_reason, screen=session_screen, now=time.time):
    """The status line for a tile, as of now: "Status 2026-11-18 14:58: session ... at work.
    Branch ... 2 of 5 sub-tasks ticked ... (ETA ...) On its screen (rough): ...". None if the
    tile has no Claude session."""
    job = next((j for j in reversed(load(store)["jobs"]) if j["key"] == key), None) or {}
    noted = TILE_SESSION_RE.search(tile_text)
    sid = noted.group(1) if noted else job.get("id")
    if not sid:
        return None
    found = tile_cuts(store, tile_text)
    cuts = found[1] if found else [(c["name"], store.repo(c["name"]), Path(c["worktree"]) if store.repo(c["name"]) else None)
                                   for c in job.get("repos") or []]
    if len(cuts) > 1 or (cuts and store.main_repo and cuts[0][1] != store.main_repo):  # several repos: a part for each
        there = sessions()
        seen = "unknown" if there is None else there.get(sid)
        if seen == "idle":
            seen = why(sid) or "idle"
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(now()))
        said = f"session `{sid}` {_doing(seen)}. " + " ".join(
            repo_status_text(c[0], git_facts(c[2], c[1].trunk) if c[1] and c[2] else None, c[1].trunk if c[1] else "", now())
            for c in cuts)
        said += " " + eta_text(tile_text, job.get("at") or job.get("started"), now())
        showing = screen(sid) if seen not in (None, "unknown") else ""
        return f"Status {stamp}: {said}" + (f' On its screen (rough): "{showing}".' if showing else "")
    path = job.get("worktree") or (tile_worktree(store, tile_text) or worktree(store, {"key": key}) if store.app else "")
    there = sessions()
    seen = "unknown" if there is None else there.get(sid)
    if seen == "idle":
        seen = why(sid) or "idle"
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(now()))
    said = status_text(sid, seen, git_facts(path, store.trunk) if path else None, store.trunk, now())
    said += " " + eta_text(tile_text, job.get("at") or job.get("started"), now())
    showing = screen(sid) if seen not in (None, "unknown") else ""  # a session that is there has a screen to read
    return f"Status {stamp}: {said}" + (f' On its screen (rough): "{showing}".' if showing else "")


def summary(store) -> dict:
    """What the page shows: the jobs, without the tiles' text, and whether pickup is on (why:
    why_off's sentence when it is off)."""
    jobs = [{k: v for k, v in j.items() if k != "text"} for j in load(store)["jobs"]]
    why = why_off(store)
    return {"enabled": why is None, "why": why, "jobs": jobs}
