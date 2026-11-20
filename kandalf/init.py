"""kandalf init: make a new plans store and, run inside a git repo, wire it to that repo (the app).

    kandalf init [DIR] [--name NAME] [--key KEY] [--port N] [--app PATH | --no-app]
                 [--pickup | --no-pickup] [--agent claude|copilot] [--hooks | --no-hooks] [--example] [--yes]

The store goes in DIR, or beside the git repo you're in in a folder named for the store's name
(<repo>-plans when the name is left at its default). It gets board.json,
plans.md and backlog.md (from templates/), active/, archive/archived.md, a git repo of its own
and a first commit. With an app, the app gets a gitignored docs/plans symlink to the store and,
the board's hooks (unless --no-hooks): they go in the store's .claude/settings.json (committed with it),
which pickup sessions load with --settings and the app links in as a gitignored
.claude/settings.local.json for your own sessions there; nothing is committed in the app.

Values not given as flags are asked for, with the default shown, when run in a terminal;
--yes (or no terminal) takes the defaults: pickup and hooks off, and the agent claude when
`claude` is on PATH, else copilot when `copilot` is.

Also here: `kandalf agent` (which agent pickup hands stories to, and switching it),
`kandalf repo add|list|rm` (the store's other repos), the hooks block (`kandalf hooks` prints it, `kandalf hooks --add` adds it to a store made
without it) and `kandalf doctor` (checks).
"""

import argparse
import datetime
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

from . import agent, pickup
from . import store as stores

TEMPLATES = Path(__file__).resolve().parent / "templates"
KEY_RE = re.compile(r"^[A-Z][A-Z0-9]*$")
FIRST_PORT = 7788
LINK = "plans"

# The PostToolUse block the store's .claude/settings.json carries under "hooks" (README: Hooks).
HOOKS = {"PostToolUse": [{"matcher": "Edit|Write", "hooks": [
    {"type": "command", "command": "command -v kandalf >/dev/null && kandalf hook autocommit 2>/dev/null || true"},
    {"type": "command", "command": "command -v kandalf >/dev/null || exit 0; kandalf hook check"},
]}]}
HOOK_MARK = "kandalf hook autocommit"  # a settings file that has this has the hooks already
# What the hooks ran before the command was called kandalf: `board hook check`, or `"$b" hook check`
# with $b found by `command -v board`. That command is gone, so those hooks quietly do nothing.
OLD_HOOK_RE = re.compile(r"\bboard hook |command -v board\b")
LOCAL = ".claude/settings.local.json"  # where an app links the store's hooks in

PICKUP_ASK = """Pickup: when it is on, a story dragged to In flight starts a background agent session
(Claude Code or the GitHub Copilot CLI) in a new git worktree under the app's .worktrees/, in the store's permission mode
(auto by default, which edits and runs commands without asking). Archiving a merged story
removes its worktree, branch and session."""
# A new store keeps its sessions' worktrees inside the app, out of the folder the app sits in.
# (Unset, the store's default is beside the app.) An agent starts a session in a worktree
# only when the worktree's main repo is trusted: hence agent.trust.
WORKTREE = "{app}/.worktrees/{key}"
WORKTREES_IGNORE = ".worktrees"


def hooks_text() -> str:
    """The hooks block as JSON, ready to paste under "hooks" in a .claude settings file."""
    return json.dumps(HOOKS, indent=2)


def git(path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True)


# ---------- defaults ----------
def default_trunk(app: Path) -> str:
    """The app's default branch: origin's HEAD, else its current branch, else main."""
    r = git(app, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip().removeprefix("origin/")
    r = git(app, "symbolic-ref", "--short", "HEAD")
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else "main"


def default_name(app, folder: Path) -> str:
    """The app repo's folder name, or the store folder's name without a trailing -plans."""
    return app.name if app else folder.name.removesuffix("-plans") or folder.name


def default_key(name: str) -> str:
    """The name's first three letters or digits, upper-cased; PLN when that isn't a key."""
    key = "".join(ch for ch in name if ch.isascii() and ch.isalnum())[:3].upper()
    return key if KEY_RE.match(key) else "PLN"


def port_free(port: int) -> bool:
    """Can a server bind this port on 127.0.0.1 right now?"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def sibling_ports(folder: Path) -> set:
    """The ports the stores beside a folder already name in their board.json."""
    taken = set()
    for d in folder.parent.iterdir() if folder.parent.is_dir() else []:
        f = d / stores.CONFIG
        if d != folder and f.is_file():
            try:
                taken.add(int(json.loads(f.read_text(encoding="utf-8")).get("port", FIRST_PORT)))
            except (ValueError, TypeError, AttributeError):
                pass
    return taken


def free_port(folder: Path, bindable=port_free) -> int:
    """The first port from 7788 up that binds and that no sibling store names."""
    taken, port = sibling_ports(folder), FIRST_PORT
    while port in taken or not bindable(port):
        port += 1
    return port


# ---------- asking ----------
def asker(interactive: bool, input=input):
    """ask(question, default, parse): the answer parsed, re-asked while parse raises ValueError;
    the default as it is, without asking, when not interactive. parse(text) checks a value.
    A yes/no question (a bool default) says its own default, as [y/N]."""
    def ask(question, default, parse=str):
        if not interactive:
            return default
        for _ in range(5):
            text = input(f"{question}{'' if isinstance(default, bool) else f' [{default}]'}: ").strip()
            if not text:
                return default
            try:
                return parse(text)
            except ValueError as e:
                print(f"  {e}")
        raise SystemExit(f"kandalf init: no valid answer to {question!r}")
    return ask


def yes_no(text: str) -> bool:
    if text.lower() in ("y", "yes"):
        return True
    if text.lower() in ("n", "no"):
        return False
    raise ValueError("answer y or n")


def check_agent(name: str) -> str:
    name = name.strip().lower()
    if name not in agent.MODULES:
        raise ValueError(f"the agent is one of {', '.join(agent.MODULES)}")
    return name


def check_key(key: str) -> str:
    if not KEY_RE.match(key):
        raise ValueError(f"{key!r} isn't a key prefix: capital letters and digits, starting with a letter (PLN)")
    return key


def check_port(text) -> int:
    port = int(text)
    if not 0 < port < 65536:
        raise ValueError(f"{port} isn't a port")
    return port


# ---------- writing ----------
def from_template(name: str, values: dict) -> str:
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    for k, v in values.items():
        text = text.replace("{" + k + "}", v)
    return text


def put(board: str, column: str, tiles: str) -> str:
    """A board with tiles (back to back) as the cards of an empty column."""
    head = f"## {column}\n"
    assert head in board, column
    return board.replace(head, f"{head}\n{tiles.strip()}\n", 1)


def example(key: str, today: datetime.date) -> dict:
    """The sample story files, {path in the store: text}, and what plans.md and backlog.md
    gain: {"plans.md": {column: tiles}, "backlog.md": {column: tiles}}."""
    k = lambda n: f"{key}-{n:02d}"  # noqa: E731
    doc = f"active/{today.isoformat()}-dark-mode.md"
    plans = {
        "Bugs": f"""
- [ ] {k(4)} P2 **Settings forgets the open tab.**
  Leaving Settings and coming back always opens the General tab. Fix it before {k(3)} puts the theme switch on the Appearance tab. (A key in a tile is a link: this tile is related to {k(3)}.)""",
        "To do": f"""
- [ ] {k(1)} ~1d **Dark mode.**
  A dark theme for the whole app: it follows the system setting, and a switch in Settings overrides it. Plan: [{doc[7:]}]({doc}). This is a master story: its sub-stories fold into it, and dragging it to In flight takes them along.
- [ ] {k(2)} ~2h **Colour tokens.**
  Part of {k(1)}. Every colour the app uses gets a name, so a theme is one list of values.
  - [ ] List the colours the screens use
  - [ ] Give each one a token name
  - [ ] Replace the literal colours with the tokens
- [ ] {k(3)} ~3h **Theme switch in Settings.**
  Part of {k(1)}. Light, dark, or follow the system; the choice is remembered.
  - [ ] Add the switch to the Appearance tab
  - [ ] Remember the choice between visits""",
        "Done": f"""
- [x] {k(5)} ~1h **Project set up.**
  The repo, the build and a first page. Clear, in this column's header, archives what is done.""",
    }
    backlog = {
        "Feature": f"""
- [ ] {k(6)} **Keyboard shortcuts.**
  The common actions get keys, listed on a help page. Drag it onto Promote to schedule it.""",
        "Change": f"""
- [ ] {k(7)} **Faster start-up.**
  Load the settings once at start, not on every page.""",
    }
    docs = {doc: f"""# {k(1)}: Dark mode

A dark theme for the whole app. It follows the system setting, and a switch in Settings overrides it.

## Steps

1. {k(2)} Colour tokens: name every colour, so a theme is one list of values.
2. {k(3)} Theme switch: light, dark or system, remembered between visits.

## Done when

Every screen reads well in both themes, and the choice survives a restart.
"""}
    return {"plans.md": plans, "backlog.md": backlog, "docs": docs}


def write_store(folder: Path, cfg: dict, with_example: bool, today: datetime.date):
    """board.json, the boards, active/ and archive/archived.md, and the example if asked for."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / stores.CONFIG).write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    values = {"name": cfg["name"], "key": cfg["key"], "link": cfg["link"], "trunk": cfg.get("trunk", "main")}
    boards = {b: from_template(b, values) for b in ("plans.md", "backlog.md")}
    (folder / "active").mkdir(exist_ok=True)
    (folder / "active" / ".gitkeep").write_text("", encoding="utf-8")
    (folder / "archive").mkdir(exist_ok=True)
    (folder / "archive" / "archived.md").write_text(from_template("archived.md", values), encoding="utf-8")
    if with_example:
        sample = example(cfg["key"], today)
        for b in boards:
            for column, tiles in sample[b].items():
                boards[b] = put(boards[b], column, tiles)
        for path, text in sample["docs"].items():
            (folder / path).write_text(text, encoding="utf-8")
    for b, text in boards.items():
        (folder / b).write_text(text, encoding="utf-8")


def has_identity(repo: Path) -> bool:
    """Has git a name and email to commit with here (env or config)? Never set here."""
    def val(env, key, *more):
        return any(os.environ.get(e) for e in (env, *more)) or git(repo, "config", key).stdout.strip()
    return all(val(*v) for v in (("GIT_AUTHOR_NAME", "user.name"), ("GIT_AUTHOR_EMAIL", "user.email", "EMAIL"),
                                 ("GIT_COMMITTER_NAME", "user.name"), ("GIT_COMMITTER_EMAIL", "user.email", "EMAIL")))


def commit_store(folder: Path, identity=has_identity) -> str:
    """git init the store (when it isn't in a repo) and make its first commit; what was done."""
    root = stores.git_root(folder)
    if root:
        return f"git: it is inside the repo at {root}, so nothing was committed; commit it there yourself"
    if git(folder, "init", "-q").returncode:
        return "git: `git init` failed, so the store isn't a repo yet"
    if not identity(folder):
        return "git: a new repo, not committed yet: git has no user name or email (git config user.name/user.email)"
    git(folder, "add", "-A")
    r = git(folder, "commit", "-q", "-m", "board: a new store")
    if r.returncode:
        return f"git: a new repo, not committed: {((r.stderr or r.stdout).strip().splitlines() or ['git commit failed'])[-1]}"
    return 'git: a new repo, first commit "board: a new store"'


def covers(gitignore: str, path: str) -> bool:
    """Does a line of this .gitignore already ignore path (docs/plans): itself, or docs/?"""
    parts = path.split("/")
    wanted = {"/".join(parts[:i]) for i in range(1, len(parts) + 1)} | {parts[0] + "/*"}
    return any(line.strip().strip("/") in wanted for line in gitignore.splitlines()
               if line.strip() and not line.startswith(("#", "!")))


def link_app(app: Path, folder: Path, link: str) -> list:
    """The app's docs/<link> symlink to the store, and its .gitignore lines (the link, and
    .worktrees/ where the sessions' worktrees go); what was done."""
    said, at = [], app / "docs" / link
    if os.path.lexists(at):
        same = at.resolve() == folder.resolve()
        said.append(f"{at} {'already links the store' if same else 'is already there, so it was left as it is'}")
    else:
        at.parent.mkdir(exist_ok=True)
        at.symlink_to(os.path.relpath(folder, at.parent))
        said.append(f"linked it into the app at {at} -> {os.path.relpath(folder, at.parent)}")
    said += ignore(app, (f"docs/{link}", f"docs/{link}"), (WORKTREES_IGNORE, WORKTREES_IGNORE + "/"))
    return said + agent_notes(app, link)


AGENT_NOTES_MARK = "<!-- kandalf -->"


def agent_notes_text(link: str) -> str:
    return f"""{AGENT_NOTES_MARK}
## Kandalf board

This repo's stories live on a kandalf board, linked in at `docs/{link}`. When the user says
"add a story to kandalf", "kandalf new story", "new kandalf story", "add to board" or
"add to the board", file a story: read the header of `docs/{link}/plans.md` for the filing
rules, run `kandalf key next` for the key, write the plan doc in `docs/{link}/active/` and add
the tile at the top of its column on `docs/{link}/backlog.md`. Commit with
`git -C docs/{link} commit -- <paths>`, never in this repo.
"""


def agent_notes(app: Path, link: str) -> list:
    """Tell the agents working in the app how to file a story when asked: a kandalf section in its
    AGENTS.md and CLAUDE.md (made when neither exists, CLAUDE.md importing AGENTS.md); what was done."""
    notes, said = agent_notes_text(link), []
    files = [app / n for n in ("AGENTS.md", "CLAUDE.md") if (app / n).is_file()]
    if not files:
        (app / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        files = [app / "AGENTS.md"]
        said.append(f"made {app / 'CLAUDE.md'} importing AGENTS.md")
    for f in files:
        text = f.read_text(encoding="utf-8") if f.exists() else ""
        if AGENT_NOTES_MARK in text or text.strip() == "@AGENTS.md":
            continue
        f.write_text(text + ("\n" if text and not text.endswith("\n") else "") + ("\n" if text else "") + notes, encoding="utf-8")
        said.append(f"told agents in {f} how to add a story to the board")
    return said


def ignore(app: Path, *entries) -> list:
    """Add each (path, line) the app's .gitignore doesn't cover yet; what was done."""
    said, f = [], app / ".gitignore"
    text = f.read_text(encoding="utf-8") if f.is_file() else ""
    for entry, line in entries:
        if not covers(text, entry):
            text += ("\n" if text and not text.endswith("\n") else "") + line + "\n"
            f.write_text(text, encoding="utf-8")
            said.append(f"added {line} to {f}")
    return said


def merge_hooks(f: Path) -> str:
    """Add the hooks block to a settings file (the store's .claude/settings.json), keeping all
    else in it; what was done."""
    text = f.read_text(encoding="utf-8") if f.is_file() else ""
    if HOOK_MARK in text:
        return f"hooks: {f} has them already"
    try:
        settings = json.loads(text) if text.strip() else {}
        hooks = settings.setdefault("hooks", {})
        post = hooks.setdefault("PostToolUse", [])
        if not isinstance(post, list):
            raise ValueError("its hooks.PostToolUse isn't a list")
    except (ValueError, AttributeError) as e:
        return (f"hooks: {f} was left alone, as it isn't valid settings JSON ({e}). "
                f"Add this under \"hooks\" yourself:\n{hooks_text()}")
    replaced = drop_old_hooks(post)
    post.extend(HOOKS["PostToolUse"])
    f.parent.mkdir(exist_ok=True)
    f.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return f"hooks: replaced the old `board hook` hooks in {f}" if replaced else f"hooks: added to {f}"


def drop_old_hooks(post: list) -> bool:
    """Take the old `board hook ...` commands out of a PostToolUse list, and any group they
    leave empty; were there any?"""
    def old(hook):
        return isinstance(hook, dict) and bool(OLD_HOOK_RE.search(str(hook.get("command", ""))))
    dropped = False
    for group in list(post):
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            continue
        keep = [h for h in group["hooks"] if not old(h)]
        if len(keep) < len(group["hooks"]):
            dropped = True
            if keep:
                group["hooks"] = keep
            else:
                post.remove(group)
    return dropped


def mentions(f: Path, text: str) -> bool:
    """Is f a file with text in it?"""
    return f.is_file() and text in f.read_text(encoding="utf-8", errors="replace")


def has_old_hooks(f: Path) -> bool:
    """Does this settings file still carry the old `board` hooks?"""
    return f.is_file() and bool(OLD_HOOK_RE.search(f.read_text(encoding="utf-8", errors="replace")))


def has_hooks(f: Path) -> bool:
    """Does this settings file carry the board's hooks?"""
    return mentions(f, HOOK_MARK)


def links_hooks(app: Path, hooks: Path) -> bool:
    """Does the app's .claude/settings.local.json lead to the store's hooks file?"""
    at = app / LOCAL
    return at.is_symlink() and at.resolve() == hooks.resolve()


def link_hooks(app: Path, hooks: Path) -> list:
    """Link the store's hooks file into the app as its .claude/settings.local.json (gitignored),
    for the sessions you run there; pickup sessions load it with --settings. When the app has a
    settings.local.json of its own, the hooks are merged into it, keeping the rest. What was done."""
    at = app / LOCAL
    if links_hooks(app, hooks):
        said = [f"hooks: {at} already links them"]
    elif os.path.lexists(at):  # the app's own: the hooks go in beside what it has
        if has_hooks(at) and not has_old_hooks(at):
            return [f"hooks: {at} has them already"]
        return [merge_hooks(at)]
    else:
        at.parent.mkdir(exist_ok=True)
        at.symlink_to(os.path.relpath(hooks, at.parent))
        said = [f"hooks: linked them into the app at {at} -> {os.path.relpath(hooks, at.parent)}"]
    return said + ignore(app, (LOCAL, LOCAL))


def add_hooks(st) -> list:
    """kandalf hooks --add: the hooks in the store's .claude/settings.json, linked into its app."""
    had = has_hooks(st.hooks_file)
    said = [merge_hooks(st.hooks_file)]
    if st.root and not had and has_hooks(st.hooks_file):
        from . import gitlog
        done = gitlog.commit(st.root, [st.hooks_file], "board: the Claude Code hooks")
        said.append(f"git: committed {stores.HOOKS_FILE} in the store" if done else
                    f"git: {stores.HOOKS_FILE} isn't committed yet: commit it in the store yourself")
    if st.app and st.app.is_dir():
        said += link_hooks(st.app, st.hooks_file)
    return said


# ---------- kandalf repo ----------
def write_config(st, cfg: dict, message: str):
    """Save board.json (the other keys as they were) and queue it for the store's commit."""
    f = st.dir / stores.CONFIG
    f.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    from . import gitlog
    gitlog.record(st, [f], message)


def read_config(st) -> dict:
    f = st.dir / stores.CONFIG
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}


RESTART_SERVE = "restart `kandalf serve` if it is running, so it sees the change"


def add_repo(st, path: Path, name: str = None, trunk: str = None) -> list:
    """kandalf repo add: another repo stories can work in. Its git root is used. board.json gains
    repos[name] (a path relative to the store; {path, trunk} when the trunk isn't the store's),
    and the repo gets the docs/<link> symlink and, when the store has hooks, their link."""
    repo = stores.git_root(Path(path).expanduser().resolve())
    if not repo:
        raise SystemExit(f"kandalf repo: {path} isn't in a git repo")
    if repo in (st.dir, st.root):
        raise SystemExit(f"kandalf repo: {repo} is the plans store itself, not a repo to work in")
    same = next((r for r in st.repos if r.path == repo.resolve()), None)
    if same:
        raise SystemExit(f"kandalf repo: {repo} is already in the store as {same.name}")
    name = name or repo.name
    if not stores.REPO_NAME_RE.match(name):
        raise SystemExit(f'kandalf repo: "{name}" isn\'t a repo name: letters, digits, ".", "_" or "-", starting with a letter or digit')
    cfg = read_config(st)
    taken = {r.name.lower() for r in st.repos} | {k.lower() for k in (cfg.get("repos") or {})}
    if name.lower() in taken:
        main = st.main_repo
        raise SystemExit(f'kandalf repo: "{name}" is already a repo' + (
            f" (the main repo's name)" if main and main.name.lower() == name.lower() else "") + ". Pick another with --name.")
    trunk = trunk or default_trunk(repo)
    rel = os.path.relpath(repo, st.dir)
    default = cfg.get("trunk", "main")
    cfg.setdefault("repos", {})[name] = rel if trunk == default else {"path": rel, "trunk": trunk}
    write_config(st, cfg, f"board: repo {name} added")
    said = [f"board.json: repo {name} {rel} @ {trunk}"]
    said += link_app(repo, st.dir, st.link)
    if has_hooks(st.hooks_file):
        said += link_hooks(repo, st.hooks_file)
    said.append(agent.trust(repo))
    said.append(RESTART_SERVE)
    return said


def remove_repo(st, name) -> list:
    """kandalf repo rm: take a repo out of board.json. Its links in the repo stay."""
    main = st.main_repo
    if main and main.name.lower() == str(name).lower():
        raise SystemExit(f"kandalf repo: {main.name} is the main repo (\"app\" in board.json), so it can't be removed")
    cfg = read_config(st)
    repos = cfg.get("repos") or {}
    key = next((k for k in repos if k.lower() == str(name).lower()), None)
    if key is None:
        raise SystemExit(f'kandalf repo: no repo called "{name}" (kandalf repo list)')
    del repos[key]
    if not repos:
        del cfg["repos"]
    write_config(st, cfg, f"board: repo {key} removed")
    return [f"board.json: repo {key} removed; its docs/{st.link} link and .gitignore lines in the repo were left", RESTART_SERVE]


def uninstall(st) -> list:
    """kandalf uninstall: take the docs/<link> symlink out of every repo of the store, the main one
    included, and the docs folder it leaves empty. Only a symlink to this store is removed; the
    store, board.json, .gitignore lines and agent notes stay. What was done."""
    said = []
    for r in st.repos:
        at = r.path / "docs" / st.link
        if not at.is_symlink():
            said.append(f"{r.name}: no {at} link" if not at.exists() else f"{r.name}: {at} isn't a link, so it was left")
        elif at.resolve() != st.dir.resolve():
            said.append(f"{r.name}: {at} links somewhere else, so it was left")
        else:
            at.unlink()
            said.append(f"{r.name}: removed {at}")
            try:
                at.parent.rmdir()
            except OSError:
                pass
    return said + [RESTART_SERVE]


def set_agent(st, name: str, force: bool = False, stop=None) -> list:
    """kandalf agent NAME: switch the agent pickup hands stories to. board.json's "agent" is set
    and its "model" (the old agent's) dropped. A story whose session the old agent started is
    left lost, without that session's id, so Continue starts the new agent in the same worktree.
    One still running is refused unless force, which stops it first. stop(old, id): stops a session."""
    try:
        name = check_agent(name)
    except ValueError as e:
        raise SystemExit(f"kandalf agent: {e}")
    old = st.agent
    if name == old:
        return [f"the agent is {name} already"]
    stop = stop or (lambda which, sid: agent.MODULES[which].stop(sid))
    state = pickup.load(st)
    tracked = [j for j in state["jobs"] if j["state"] in pickup.TRACKED]
    live = [j for j in tracked if j["state"] in ("started", "idle")]
    if live and not force:
        keys = ", ".join(j["key"] for j in live)
        raise SystemExit(f"kandalf agent: {keys} {'has' if len(live) == 1 else 'have'} a {old} session. Let "
                         f"{'it' if len(live) == 1 else 'them'} finish, or pass --force to stop {'it' if len(live) == 1 else 'them'}: "
                         f"each story keeps its worktree, and Continue on its tile hands it to {name}.")
    said = []
    for j in tracked:
        if j["state"] in ("started", "idle") and j.get("id"):
            try:
                stop(old, j["id"])
            except (OSError, subprocess.SubprocessError):
                pass
            said.append(f"{j['key']}: {old} session {j['id']} stopped")
        j["state"] = "lost"
        j.pop("session", None)
    if tracked:
        pickup.save(st, state)
        said.append(f"{', '.join(j['key'] for j in tracked)}: Continue on the tile starts {name} in the story's worktree")
    cfg = read_config(st)
    cfg["agent"] = name
    model = cfg.pop("model", None)
    write_config(st, cfg, f"board: agent {name}")
    agent.use(name)
    said.insert(0, f"board.json: agent {name}" + (f"; model {model} (for {old}) dropped, so sessions run on {agent.default_model()}" if model else ""))
    said += ["tiles' own Model: lines are left as they are, so check they name a model " + name + " has",
             agent.trust(st.app or "the app"), RESTART_SERVE]
    return said


def agent_command(st, args) -> int:
    """kandalf agent [claude|copilot] [--force]"""
    ap = argparse.ArgumentParser(prog="kandalf agent", description="Show or switch the agent pickup hands stories to.")
    ap.add_argument("name", nargs="?", choices=sorted(agent.MODULES))
    ap.add_argument("--force", action="store_true", help="stop the old agent's running sessions")
    a = ap.parse_args(args)
    if not a.name:
        print(f"{st.agent} ({agent.label()}), model {st.model}")
        return 0
    for line in set_agent(st, a.name, a.force):
        print(line)
    return 0


def repo_lines(st) -> list:
    """One line per repo, the main one first: name, path from the store, trunk."""
    return [f"{r.name}{' (main)' if r.main else ''} {os.path.relpath(r.path, st.dir)} @ {r.trunk}" for r in st.repos]


def repo_command(st, args) -> int:
    """kandalf repo add PATH [--name NAME] [--trunk BRANCH] | list | rm NAME"""
    ap = argparse.ArgumentParser(prog="kandalf repo")
    sub = ap.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add", help="another repo stories can work in")
    add.add_argument("path")
    add.add_argument("--name", help="default: the repo's folder name")
    add.add_argument("--trunk", help="default: the repo's default branch")
    sub.add_parser("list", help="the store's repos")
    sub.add_parser("rm", help="take a repo out of board.json").add_argument("name")
    a = ap.parse_args(args)
    if a.cmd == "add":
        lines = add_repo(st, Path(os.getcwd()) / a.path, a.name, a.trunk)
    elif a.cmd == "rm":
        lines = remove_repo(st, a.name)
    else:
        lines = repo_lines(st) or ["no repos: set \"app\" in board.json, or kandalf repo add PATH"]
    for line in lines:
        print(line)
    return 0


# ---------- the command ----------
def parser():
    ap = argparse.ArgumentParser(prog="kandalf init", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir", nargs="?", help="where the store goes (default: beside this git repo, in a folder named for the store's name, <repo>-plans if that is left as is)")
    ap.add_argument("--name", help="shown in the page title (default: the app's folder name)")
    ap.add_argument("--key", help="tile key prefix, e.g. PLN (default: the name's first three letters)")
    ap.add_argument("--port", type=check_port, help="where `kandalf serve` listens (default: the first free from 7788)")
    ap.add_argument("--app", help="the app repo (default: the git repo you're in)")
    ap.add_argument("--no-app", action="store_true", help="no app: a store on its own")
    ap.add_argument("--pickup", dest="pickup", action="store_true", default=None, help="let agents pick up stories")
    ap.add_argument("--no-pickup", dest="pickup", action="store_false")
    ap.add_argument("--agent", choices=sorted(agent.MODULES), help="the agent pickup hands stories to (default: claude if on PATH, else copilot)")
    ap.add_argument("--hooks", dest="hooks", action="store_true", default=None,
                    help="the board's hooks, in the store's .claude/settings.json and linked into the app (the default)")
    ap.add_argument("--no-hooks", dest="hooks", action="store_false", help="leave the hooks out, and don't add them later")
    ap.add_argument("--example", action="store_true", help="fill the store with a sample story")
    ap.add_argument("--yes", "-y", action="store_true", help="take the defaults, never ask")
    return ap


def run(args=None, *, cwd=None, input=input, isatty=None, bindable=port_free, why_off=pickup.why_off,
        identity=has_identity, today=datetime.date.today, out=print, which=shutil.which) -> int:
    """kandalf init. input/isatty: the terminal (tests pass their own); the rest are probes."""
    a = parser().parse_args(args)
    cwd = Path(cwd or os.getcwd()).resolve()
    isatty = isatty or sys.stdin.isatty
    ask = asker(isatty() and not a.yes, input)
    here = stores.git_root(cwd)
    if a.app and a.no_app:
        raise SystemExit("kandalf init: --app and --no-app don't go together")
    app = None
    if a.app:
        app = stores.git_root((cwd / a.app).expanduser().resolve())
        if not app:
            raise SystemExit(f"kandalf init: --app {a.app} isn't a git repo")
    elif here and not a.no_app:
        app = here
    base = app or here
    if a.dir:
        folder = (cwd / a.dir).expanduser().resolve()
    elif base:
        folder = base.parent / f"{base.name}-plans"
    else:
        raise SystemExit("kandalf init: not in a git repo, so there is no app to put the store beside. "
                         "Pass the folder to make it in: kandalf init DIR")
    name = a.name or ask("Name", default_name(app, folder))
    if not a.dir and base and name != default_name(app, folder):
        if not name or name in (".", "..") or "/" in name or "\\" in name:
            raise SystemExit(f'kandalf init: "{name}" can\'t be a folder name. Pass the folder to make it in: kandalf init DIR')
        folder = base.parent / name
    if folder.exists() and not folder.is_dir():
        raise SystemExit(f"kandalf init: {folder} is a file")
    for f in (stores.CONFIG, "plans.md"):
        if (folder / f).exists():
            raise SystemExit(f"kandalf init: {folder} already has {f}, so it is a store already. Nothing was written.")
    if app and (folder == app or app in folder.parents):
        raise SystemExit(f"kandalf init: {folder} is inside the app repo {app}. Put the store beside it, or pass --no-app.")

    try:
        key = check_key(a.key) if a.key else ask("Key prefix", default_key(name), check_key)
    except ValueError as e:
        raise SystemExit(f"kandalf init: {e}")
    port = a.port or ask("Port", free_port(folder, bindable), check_port)
    cfg = {"name": name, "key": key, "port": port, "link": LINK}
    if app:
        cfg.update(app=os.path.relpath(app, folder), trunk=default_trunk(app), worktree=WORKTREE)
        on = a.pickup
        if on is None:
            if isatty() and not a.yes:
                out(PICKUP_ASK)
            on = ask("Let agents pick up stories? [y/N]", False, yes_no)
        cfg["pickup"] = on
        if on or a.agent:
            try:
                cfg["agent"] = a.agent or ask("Agent: claude (Claude Code) or copilot (GitHub Copilot CLI)",
                                              agent.default_name(which), check_agent)
            except ValueError as e:
                raise SystemExit(f"kandalf init: {e}")
            agent.use(cfg["agent"])
    # The board's hooks are part of it, not a question: --no-hooks leaves them out, and says so in
    # board.json ("hooks": false), so kandalf serve doesn't add them later (serve.ensure_hooks).
    hooks = bool(app) and a.hooks is not False
    if app and a.hooks is False:
        cfg["hooks"] = False

    write_store(folder, cfg, a.example, today())
    hooks_file = folder / stores.HOOKS_FILE
    merged = merge_hooks(hooks_file) if hooks else None
    out(f"Made the store {folder}:")
    out(f"  board.json: name {name}, keys {key}-NN, port {port}" + (f", app {cfg['app']} @ {cfg['trunk']}, pickup {'on' if cfg['pickup'] else 'off'}" if app else "")
        + (f", agent {cfg['agent']}" if cfg.get("agent") else ""))
    out("  plans.md, backlog.md, active/, archive/archived.md" + (", and an example story (Dark mode)" if a.example else ""))
    if merged:
        out("  " + merged)
    out("  " + commit_store(folder, identity))
    linked = False
    if app:
        for line in link_app(app, folder, LINK):
            out("  " + line)
        linked = (app / "docs" / LINK).resolve() == folder
        if hooks:
            for line in link_hooks(app, hooks_file):
                out("  " + line)
    elif a.hooks:
        out("  hooks: no app, so no hooks were added (`kandalf hooks` prints them)")
    out(f"Next: kandalf serve{f'   (from {app})' if linked else f' --dir {folder}'}")
    if app and cfg["pickup"]:
        why = why_off(stores.Store(folder))
        out(why or agent.trust(app))
    return 0


def main(args=None) -> int:
    return run(args)


# ---------- kandalf doctor ----------
OK, BAD, INFO = "ok", "!!", "--"


def checks(st, *, which=shutil.which, version=sys.version_info, why_off=pickup.why_off, bindable=port_free) -> list:
    """What works and what doesn't, as [(mark, text)]: ok, !! (a problem) or -- (to know)."""
    out = [(OK if version >= (3, 10) else BAD, f"Python {version[0]}.{version[1]}" + ("" if version >= (3, 10) else ": 3.10 or later is needed"))]
    has_git = bool(which("git"))
    out.append((OK, "git found") if has_git else (BAD, "git not found: install git"))
    out.append((OK, "node found") if which("node") else (INFO, "node not found: only `kandalf check` and the check hook need it"))
    settings = f"name {st.name}, keys {st.key}-NN, port {st.port}, link docs/{st.link}"
    if not st.dir.is_dir():
        out.append((BAD, f"store {st.dir} isn't there"))
        return out
    if not (st.dir / "plans.md").is_file():
        out.append((BAD, f"store {st.dir} has no plans.md ({settings})"))
    elif not (st.dir / stores.CONFIG).is_file():
        out.append((INFO, f"store {st.dir} has no board.json, so it runs on defaults: {settings}"))
    else:
        out.append((OK, f"store {st.dir}: {settings}"))
    if not KEY_RE.match(st.key):
        out.append((BAD, f'key "{st.key}" isn\'t a key prefix: capital letters and digits, starting with a letter'))
    if not st.root:
        out.append((BAD, "store isn't a git repo, so board changes aren't committed: git init it"))
    elif not st.push:
        out.append((INFO, 'store commits without pushing ("push": false)'))
    else:
        up = git(st.dir, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
        out.append((OK, f"store pushes to {up.stdout.strip()}") if up.returncode == 0 else
                   (INFO, "store has no upstream, so commits stay local (git push -u to set one)"))
    if st.repo_problem:
        out.append((BAD, st.repo_problem))
    if st.app:
        out.extend(app_checks(st))
    for repo in st.repos:
        if not repo.main:
            out.extend(repo_checks(st, repo))
    why = why_off(st)
    if why is None:
        out.append((OK, f"pickup on: In flight tiles go to {agent.label()} ({st.permission} mode)"))
    else:
        out.append((INFO if not st.pickup or not st.app else BAD, why))
    out.append((OK, f"port {st.port} is free") if bindable(st.port) else
               (INFO, f"port {st.port} is in use: maybe this board is already running"))
    return out


def app_checks(st) -> list:
    """The app's checks: there, a repo, its trunk, the docs/<link> symlink, the hooks."""
    app = st.app
    if not app.is_dir():
        return [(BAD, f'app {app} isn\'t there: fix "app" in board.json')]
    if not stores.git_root(app):
        return [(BAD, f"app {app} isn't a git repo")]
    out = [(OK, f"app {app}")]
    has = any(git(app, "rev-parse", "--verify", "--quiet", ref).returncode == 0
              for ref in (f"refs/heads/{st.trunk}", f"refs/remotes/origin/{st.trunk}"))
    out.append((OK, f"trunk {st.trunk} is there") if has else
               (BAD, f'trunk {st.trunk} isn\'t a branch of the app: fix "trunk" in board.json'))
    at = app / "docs" / st.link
    if not os.path.lexists(at):
        out.append((BAD, f"{at} isn't there: ln -s {os.path.relpath(st.dir, at.parent)} {at}"))
    elif at.resolve() != st.dir:
        out.append((BAD, f"{at} leads to {at.resolve()}, not to this store"))
    else:
        out.append((OK, f"{at} links the store"))
    out.append(hook_check(st))
    return out


def repo_checks(st, repo) -> list:
    """What app_checks does for one other repo: there, a repo, its trunk, the docs/<link> symlink,
    and the hooks (INFO when not linked)."""
    label = f"repo {repo.name} {repo.path}"
    if not repo.path.is_dir():
        return [(BAD, f'{label} isn\'t there: fix "repos" in board.json')]
    if not stores.git_root(repo.path):
        return [(BAD, f"{label} isn't a git repo")]
    out = [(OK, label)]
    has = any(git(repo.path, "rev-parse", "--verify", "--quiet", ref).returncode == 0
              for ref in (f"refs/heads/{repo.trunk}", f"refs/remotes/origin/{repo.trunk}"))
    out.append((OK, f"repo {repo.name}: trunk {repo.trunk} is there") if has else
               (BAD, f'repo {repo.name}: trunk {repo.trunk} isn\'t a branch: fix its "trunk" in board.json'))
    at = repo.path / "docs" / st.link
    if not os.path.lexists(at):
        out.append((BAD, f"{at} isn't there: ln -s {os.path.relpath(st.dir, at.parent)} {at}"))
    elif at.resolve() != st.dir:
        out.append((BAD, f"{at} leads to {at.resolve()}, not to this store"))
    else:
        out.append((OK, f"{at} links the store"))
    out.append((INFO, agent.trust(repo.path)))
    if has_hooks(st.hooks_file) and not links_hooks(repo.path, st.hooks_file):
        out.append((INFO, f"repo {repo.name}: {repo.path / LOCAL} doesn't link the store's hooks (kandalf repo add links them)"))
    return out


def hook_check(st):
    """Where the board's hooks are: in the store and linked into the app (the way kandalf init
    puts them), in the store only, in the app's own settings, or nowhere."""
    local = st.app / LOCAL
    for f in (st.hooks_file, st.app / ".claude" / "settings.json", local):
        if has_old_hooks(f):
            return (INFO, f"{f} has the old hooks, which run `board`, a command that is gone, so they do nothing: "
                          f"`kandalf hooks --add` replaces them in the store and the app's {LOCAL}; take them out of "
                          f"the app's committed settings by hand")
    if has_hooks(st.hooks_file):
        if links_hooks(st.app, st.hooks_file):
            return OK, f"hooks in {st.hooks_file}, linked into the app at {local}"
        if has_hooks(local):
            return OK, f"hooks in {st.hooks_file}, and in the app's own {local}"
        return (INFO, f"hooks in {st.hooks_file}: pickup sessions get them, but your own sessions in the app "
                      f"don't, as {local} doesn't link them (`kandalf hooks --add` links it when that file isn't there)")
    for f in (st.app / ".claude" / "settings.json", local):
        if has_hooks(f):
            return OK, f"hooks in {f} (pickup sessions get them only if it is committed; `kandalf hooks --add` keeps them in the store)"
    return INFO, "no board hooks: `kandalf hooks --add` puts them in the store and links them into the app"


def doctor(st, out=print, **probes) -> int:
    """kandalf doctor: one line per check; 1 when any is a problem. probes: as checks takes them."""
    found = checks(st, **probes)
    for mark, text in found:
        out(f"{mark}  {text}")
    return 1 if any(mark == BAD for mark, _ in found) else 0
