"""A plans store: one project's boards and plan docs, which the board tool serves.

A store is a folder (usually a git repo of its own) holding:

    board.json      the store's settings (below); optional, every field has a default
    plans.md        the board: one "## " column per status, one "- " tile per plan
    backlog.md      the backlog board
    active/         plan docs for open plans
    archive/        plan docs for archived plans, plus archived.md, the log of archived tiles
    .claude/settings.json   the board's Claude Code hooks (kandalf init --hooks, kandalf hooks --add):
                    pickup sessions load it with --settings, and the app links it in as its
                    .claude/settings.local.json for your own sessions there

board.json:

    {
      "name":  "My App",               shown in the page title (default: the folder name)
      "key":   "PLN",                tile key prefix, PLN-01, PLN-02, ... (default PLN)
      "port":  7788,                 where `kandalf serve` listens (default 7788)
      "link":  "plans",              the folder name app repos link the store in by, so
                                     "plans/active/x.md" finds docs/plans/active/x.md (default plans)
      "app":   "../myapp",           the app repo, relative to the store, whose feature branches
                                     Done tiles name (default: none)
      "trunk": "dev",                the app branch stories are cut from and merged into (default:
                                     dev when the app has a dev branch, else main); a tile's own
                                     "Base: x" line picks another for its story
      "repos": {"api": "../api",     other repos stories can work in, by name: a path relative to
                "web": {"path": "../web", "trunk": "next"}},
                                     the store, or {"path", "trunk"} (its trunk defaults to the trunk
                                     setting, and without one to dev when that repo has it, else main)
      "pickup": true,                start a Claude Code session for a tile that lands in
                                     In flight (pickup.py; default false, needs "app")
      "agent": "claude",             the agent those sessions run in: claude (Claude Code) or
                                     copilot (the GitHub Copilot CLI; default claude)
      "pickupColumn": "Building",    the column whose tiles are picked up, by name (any case);
                                     default: any column named like In flight (in progress, doing, WIP...)
      "permission": "auto",          the permission mode those sessions start in (claude
                                     --permission-mode): auto, acceptEdits, manual, dontAsk,
                                     plan or bypassPermissions (default auto)
      "model": "opus",               the model those sessions run on, an alias or a full id
                                     (default opus for claude, auto for copilot); a tile's own
                                     "Model: x" line overrides it
      "parallel": 1,                 how many tiles on their own have a session at work at once
                                     (default 1); the rest are queued, each with Start now
      "sessions": 2,                 how many of a master's sub-stories have a session at work
                                     at once (default 2); the master's "Sessions: n" line overrides it
      "fullSuiteOnMerge": false,     true: a merge from the board runs the full suite on the trunk
                                     (a sub-story's only when it is its master's last); false (the
                                     default): only when the tile's "Tests: full" line asks
      "branch": "{key}-{slug}",      a session's feature branch (the default): {key} is the tile's
                                     key in lower case, {KEY} as written, {slug} its title,
                                     {app} the app folder's name; e.g. "feat/{key}-{slug}"
      "worktree": "{app}-{key}",     its worktree, relative to the folder the app is in (the
                                     default), with the same placeholders; e.g. "wt/{app}-{key}".
                                     Both need {key}; the worktree climbs at most one folder (../)
      "hooks": true,                 the board's hooks: kandalf serve adds them to a store without them
                                     (serve.ensure_hooks); false (kandalf init --no-hooks) leaves them out
      "tidy": true,                  archiving a story removes its worktree, feature branch and
                                     session, when the branch is in the trunk and nothing would
                                     be lost (pickup.tidy; default true)
      "push": true,                  the commit queue pushes each commit to the store's
                                     upstream, when it has one (gitlog.py; default true)
      "commitDelay": 300             seconds of quiet before the queue commits (default 300);
                                     BOARD_COMMIT_DELAY in the environment overrides it
    }

pickup.md, at the top of the store, optional: the working rules every Claude session is
given, in place of the default (templates/pickup.md: commit subjects carry the key, and the
Gates sub-task, unticked again for a later run of tests). {key} in it is the tile's key. They are told right after "Tick each
sub-task with its commit as it lands.": a first line that is a sentence carries on that
rule, and each "- " line is a rule of its own. An empty pickup.md gives no rules of the
store's own. What the board reads back (the session line, ticked sub-tasks, the Handoff:
and Ready for review lines) is asked for in pickup.py whatever the file says.

Stores share nothing: each has its own keys, its own commit queue (in its own .git) and
its own server.
"""

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import agent

CONFIG = "board.json"
HOOKS_FILE = ".claude/settings.json"  # the board's hooks, in the store
REPO_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class Repo:
    """A repo a story can work in: its name, folder and trunk branch. main marks the app."""
    name: str
    path: Path
    trunk: str
    main: bool = False


def has_branch(repo: Path, name: str) -> bool:
    """True when the git repo at repo has a local branch called name."""
    try:
        r = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"refs/heads/{name}"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def default_trunk(repo) -> str:
    """The trunk of a repo whose board.json names none: dev when it has a dev branch, else main."""
    return "dev" if repo and Path(repo).is_dir() and has_branch(repo, "dev") else "main"


def git_root(path: Path):
    r = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return Path(r.stdout.strip()) if r.returncode == 0 else None


class Store:
    def __init__(self, folder):
        self.dir = Path(folder).expanduser().resolve()
        self.config_problem = None
        self.config_mtime = None
        self._read(self._load_config())

    def _load_config(self) -> dict:
        """board.json parsed (an empty dict when there is none), and config_mtime noted.
        Raises ValueError when the file doesn't parse."""
        f = self.dir / CONFIG
        if not f.is_file():
            self.config_mtime = None
            return {}
        self.config_mtime = f.stat().st_mtime_ns
        return json.loads(f.read_text(encoding="utf-8"))

    def reload(self) -> bool:
        """Read board.json again into this same object and return True. When it doesn't
        parse, every setting stays as it was, config_problem says why, and this returns False."""
        saved = dict(self.__dict__)
        try:
            cfg = self._load_config()
        except json.JSONDecodeError as e:
            self.config_problem = f"board.json doesn't parse: {e}"
            return False
        try:
            if not isinstance(cfg, dict):
                raise ValueError("it is not a JSON object")
            self._read(cfg)
        except (ValueError, TypeError, AttributeError) as e:
            mtime = self.config_mtime
            self.__dict__.clear()
            self.__dict__.update(saved)
            self.config_mtime = mtime
            agent.use(self.agent if self.agent_problem is None else "claude")
            self.config_problem = f"board.json has a bad value: {e}"
            return False
        self.config_problem = None
        return True

    def reload_if_changed(self) -> bool:
        """Reload when board.json's modification time differs from the one last read."""
        f = self.dir / CONFIG
        mtime = f.stat().st_mtime_ns if f.is_file() else None
        return mtime != self.config_mtime and self.reload()

    def _read(self, cfg: dict):
        """Set every setting from the parsed board.json (and the environment's overrides)."""
        self.name = cfg.get("name") or self.dir.name
        self.key = cfg.get("key", "PLN")
        self.port = int(cfg.get("port", 7788))
        self.link = cfg.get("link", "plans")
        app = os.environ.get("PLANS_APP_REPO") or cfg.get("app")
        self.app = (self.dir / app).resolve() if app else None
        self.trunk = os.environ.get("PLANS_APP_TRUNK") or cfg.get("trunk") or default_trunk(self.app)
        self.repos, self.repo_problem = self._read_repos(cfg.get("repos"), cfg.get("trunk"))
        self.pickup = bool(cfg.get("pickup", False))
        self.agent = cfg.get("agent") or "claude"
        try:
            agent.use(self.agent)
            self.agent_problem = None
        except ValueError as e:
            agent.use("claude")
            self.agent_problem = str(e)
        self.permission = cfg.get("permission") or "auto"
        self.model = cfg.get("model") or agent.default_model()
        self.sessions = max(1, int(cfg.get("sessions") or 2))
        self.full_suite_on_merge = bool(cfg.get("fullSuiteOnMerge", False))
        self.parallel = max(1, int(cfg.get("parallel") or 1))  # tiles on their own with a session at work at once
        self.pickup_column = (cfg.get("pickupColumn") or "").strip()  # "": any In flight column
        self.branch_pattern = cfg.get("branch") or "{key}-{slug}"
        self.worktree_pattern = cfg.get("worktree") or "{app}-{key}"
        self._commit_delay = 300 if cfg.get("commitDelay") is None else int(cfg["commitDelay"])
        self.tidy = bool(cfg.get("tidy", True))
        self.hooks = bool(cfg.get("hooks", True))  # false: kandalf init --no-hooks, and serve leaves them out
        self.push = bool(cfg.get("push", True))
        self.root = git_root(self.dir) if self.dir.is_dir() else None
        self.hooks_file = self.dir / HOOKS_FILE
        k = re.escape(self.key)
        self.key_re = re.compile(rf"\b{k}-(\d+)\b")  # a key anywhere in a line

    def _read_repos(self, entries, store_trunk):
        """The main repo, then the valid "repos" entries in file order, and one sentence
        naming the first configuration error (or None)."""
        repos, problem = [], None
        if self.app:
            repos.append(Repo(self.app.name, self.app, self.trunk, True))
        seen = {r.name.lower() for r in repos}
        for name, v in (entries or {}).items():
            path, trunk = (v.get("path"), v.get("trunk")) if isinstance(v, dict) else (v, None)
            if not REPO_NAME_RE.match(name):
                why = f'the repo name "{name}" must be letters, digits, ".", "_" or "-"'
            elif name.lower() in seen:
                why = f'the repo name "{name}" clashes with another repo\'s name'
            elif not path:
                why = f'the repo "{name}" has no path'
            else:
                seen.add(name.lower())
                folder = (self.dir / path).resolve()
                repos.append(Repo(name, folder, trunk or store_trunk or default_trunk(folder)))
                continue
            problem = problem or why
        return repos, problem

    @property
    def main_repo(self):
        return self.repos[0] if self.repos and self.repos[0].main else None

    def repo(self, name: str):
        """The repo called name, in any case, or None."""
        return next((r for r in self.repos if r.name.lower() == str(name).lower()), None)

    def commit_delay(self) -> int:
        """Seconds of quiet before the commit queue commits: BOARD_COMMIT_DELAY when it is
        set, else "commitDelay" (default 300)."""
        env = os.environ.get("BOARD_COMMIT_DELAY")
        return int(env) if env is not None else self._commit_delay

    def fmt(self, n: int) -> str:
        return f"{self.key}-{n:02d}"

    def num(self, key: str) -> int:
        return int(key.rsplit("-", 1)[1])

    def rel(self, root_rel: str):
        """A path relative to the git root, as a path relative to the store, or None if it is outside."""
        p = (self.root / root_rel).resolve() if self.root else Path(root_rel)
        try:
            return p.relative_to(self.dir).as_posix()
        except ValueError:
            return None

    def __repr__(self):
        return f"Store({self.name!r}, {self.key}, {self.dir})"


def find(start: Path):
    """The store a path is in: the nearest folder at or above it holding board.json."""
    p = Path(start).expanduser().resolve()
    for d in (p, *p.parents):
        if (d / CONFIG).is_file():
            return Store(d)
    return None


def default(cwd: Path = None):
    """The store to use when none is named: the one cwd is in, else the one an app repo links
    in at docs/<link> (docs/plans), from anywhere in that app repo."""
    cwd = Path(cwd or os.getcwd())
    s = find(cwd)
    if s:
        return s
    root = git_root(cwd)
    if root:
        for d in sorted((root / "docs").glob("*")) if (root / "docs").is_dir() else []:
            if (d / CONFIG).is_file():
                return Store(d)
    return None
