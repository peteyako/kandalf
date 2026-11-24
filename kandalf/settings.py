"""The store's editable settings: the one place the settings panel's rules live. EDITABLE says
which board.json keys the panel may change and how each is checked, view() describes them for
the page, and apply() checks a set of changes and writes them."""
import re

from . import agent, init, pickup, store as stores
from .store import CONFIG

# Same rule as BRANCH_NAME_RE in board.html.
BRANCH_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._/-]*$")

PICKUP_OFF = ("Pickup is off: no new stories are picked up; sessions at work carry on, and their tile "
              "buttons work again once pickup is back on.")

EDITABLE = {
    "pickup": {"kind": "bool", "section": "Pickup", "label": "Pickup",
               "help": "Start an agent session for each story that lands in In flight. Off: no new stories are picked up; "
                       "sessions already at work carry on."},
    "agent": {"kind": "choice", "choices": list(agent.MODULES), "section": "Pickup", "label": "Agent",
              "help": "Which coding agent the sessions run in: claude (Claude Code), copilot (GitHub Copilot CLI) or agy (Antigravity CLI). "
                      "It can't be switched while a session is at work."},
    "model": {"kind": "text", "section": "Pickup", "label": "Model",
              "help": "The model sessions run on: an alias such as opus or sonnet, or a full model id. "
                      "A tile's own Model: line overrides it for that story."},
    "permission": {"kind": "choice", "choices": ["auto", "default", "acceptEdits", "manual", "dontAsk", "plan", "bypassPermissions"],
                   "section": "Pickup", "label": "Permission mode",
                   "help": "The permission mode a session starts in (claude --permission-mode): what it may do without asking. "
                           "bypassPermissions lets it do anything."},
    "parallel": {"kind": "int", "min": 1, "section": "Pickup", "label": "Stories at once",
                 "help": "How many stories on their own (not sub-stories) have a session at work at the same time. "
                         "The rest wait in the queue, each with a Start now button."},
    "sessions": {"kind": "int", "min": 1, "section": "Pickup", "label": "Sessions",
                 "help": "How many of a master story's sub-stories have a session at work at the same time. "
                         "A master's own Sessions: line overrides it."},
    "fullSuiteOnMerge": {"kind": "bool", "section": "Tests", "label": "Full suite on merge",
                         "help": "Run every test tier on the trunk after a story is merged from the board (after the last "
                                 "sub-story, for a master). Off: sessions run only the tests for what they change. "
                                 "A tile's Tests: line overrides it."},
    "trunk": {"kind": "text", "section": "Git and commits", "label": "Trunk",
              "help": "The app branch stories are cut from and merged into. A tile's Base: line picks another branch "
                      "for its story."},
    "worktree": {"kind": "text", "section": "Git and commits", "label": "Worktrees",
                 "help": "Where a story's worktree goes: \"worktree\" in the store's board.json, a path from the folder "
                         "the app is in. {app} is the app folder's name, {key} the tile's key (pln-12; {KEY} for PLN-12). "
                         "{app}/.worktrees/{key} (the default) keeps them inside the app; {app}-{key} puts them beside it; "
                         "../wt/{key} goes one folder up. It needs {key} and may climb one folder at most. "
                         "Stories already picked up keep theirs."},
    "tidy": {"kind": "bool", "section": "Git and commits", "label": "Tidy",
             "help": "When a story is archived, remove its worktree, feature branch and session, but only if nothing "
                     "would be lost (the branch is merged and nothing is uncommitted)."},
    "push": {"kind": "bool", "section": "Git and commits", "label": "Push",
             "help": "Push each commit of this board's files to the store's own upstream, when it has one. "
                     "It doesn't push the app."},
    "commitDelay": {"kind": "int", "min": 0, "section": "Git and commits", "label": "Commit delay (seconds)",
                    "help": "How long the board waits after the last change before committing everything changed "
                            "since as one commit."},
}

# What each fact in the panel's About this store means; they are set in board.json.
READONLY_HELP = {
    "name": "The project's name, shown in the page title and header.",
    "key": "The prefix of tile keys, as in PLN-12.",
    "port": "Where kandalf serve listens. A change takes effect when the server starts again.",
    "folder": "The store: the git repo holding the boards, plan docs and board.json.",
    "app": "The main code repo sessions work in, relative to the store in board.json.",
    "repos": "Every repo a story can work in, each with the branch it is merged into.",
    "link": "The folder name the store is linked into each repo by (docs/<link>).",
    "pickupColumn": "The column whose tiles are picked up. Unset, any column named like In flight.",
    "branch": "The pattern for a story's feature branch: {key} is the tile's key, {slug} its title.",
    "hooks": "Whether kandalf serve adds the board's Claude Code hooks to the store.",
}


class SettingsError(Exception):
    """A change the panel may not make; its message is one sentence for the page."""


def _effective(store) -> dict:
    return {"pickup": store.pickup, "agent": store.agent, "model": store.model, "permission": store.permission,
            "parallel": store.parallel, "sessions": store.sessions, "fullSuiteOnMerge": store.full_suite_on_merge,
            "trunk": store.trunk, "worktree": store.worktree_pattern, "tidy": store.tidy, "push": store.push, "commitDelay": store.commit_delay()}


def _defaults(store) -> dict:
    return {"pickup": False, "agent": "claude", "model": agent.default_model(), "permission": "auto",
            "parallel": 1, "sessions": 2, "fullSuiteOnMerge": False, "trunk": stores.default_trunk(store.app),
            "worktree": stores.WORKTREE, "tidy": True, "push": True, "commitDelay": 300}


def _read(store) -> dict:
    """board.json as parsed; SettingsError (one sentence) when it doesn't parse or isn't an object."""
    try:
        cfg = init.read_config(store)
    except ValueError as e:
        raise SettingsError(f"board.json doesn't parse: {e}") from None
    if not isinstance(cfg, dict):
        raise SettingsError("board.json is not a JSON object.")
    return cfg


def _reloaded(store):
    """Reload the store; SettingsError when the file as written won't load."""
    if not store.reload():
        raise SettingsError(f"board.json was written but is not in force until this is fixed: {store.config_problem}")


def view(store) -> dict:
    """What the panel shows: the raw values board.json sets, the ones in force, the defaults,
    the field rules, the read-only settings, and a sentence on why pickup or board.json is off."""
    from .serve import mtime
    f = store.dir / CONFIG
    unread = None
    try:
        cfg = _read(store) if f.is_file() else {}
    except SettingsError as e:
        cfg, unread = {}, str(e)
    return {
        "mtime": mtime(f) if f.is_file() else "",
        "set": {k: cfg[k] for k in EDITABLE if k in cfg},
        "effective": _effective(store),
        "defaults": _defaults(store),
        "fields": EDITABLE,
        "readonly": {"name": store.name, "key": store.key, "port": store.port, "folder": str(store.dir),
                     "app": str(store.app) if store.app else None,
                     "repos": [{"name": r.name, "trunk": r.trunk} for r in store.repos],
                     "link": store.link, "pickupColumn": store.pickup_column, "branch": store.branch_pattern,
                     "hooks": store.hooks},
        "readonlyHelp": READONLY_HELP,
        "problem": store.config_problem or unread or (pickup.why_off(store) if store.pickup else None),
    }


def _check(store, key: str, value):
    """Raise SettingsError unless value is allowed for key (None, a reset, always is)."""
    spec = EDITABLE.get(key)
    if spec is None:
        raise SettingsError(f'"{key}" is not a setting the panel can change.')
    if value is None:
        return
    kind = spec["kind"]
    if kind == "bool" and not isinstance(value, bool):
        raise SettingsError(f"{spec['label']} must be on or off.")
    if kind == "int":
        if not isinstance(value, int) or isinstance(value, bool):
            raise SettingsError(f"{spec['label']} must be a whole number.")
        if value < spec["min"]:
            raise SettingsError(f"{spec['label']} must be at least {spec['min']}.")
    if kind == "choice" and value not in spec["choices"]:
        raise SettingsError(f"{spec['label']} must be one of {', '.join(spec['choices'])}.")
    if kind == "text":
        if not isinstance(value, str):
            raise SettingsError(f"{spec['label']} must be text.")
        if key == "model" and (not value or re.search(r"\s", value)):
            raise SettingsError("Model must be a name with no spaces.")
        if key == "trunk":
            if not BRANCH_NAME_RE.match(value):
                raise SettingsError("Trunk must be a branch name: letters, digits and . _ / -")
            if store.app and not stores.has_branch(store.app, value):
                raise SettingsError(f'Trunk "{value}" is not a branch of {store.app.name}.')
        if key == "worktree":
            why = pickup.pattern_problem("worktree", value)
            if why:
                raise SettingsError(f"Worktrees ({value}) {why}")


def apply(store, changes: dict) -> list:
    """Check every change, then write them to board.json (None removes a key, every other key
    stays) and reload the store. Returns one line per change. Nothing is written when any
    change is refused."""
    for key, value in changes.items():
        _check(store, key, value)
    if (store.dir / CONFIG).is_file():
        _read(store)
    rest = dict(changes)
    said = {}
    pickup_was = store.pickup
    if "agent" in rest:
        want = rest.pop("agent") or "claude"
        try:
            said["agent"] = [line for line in init.set_agent(store, want) if line != init.RESTART_SERVE]
        except SystemExit as e:
            raise SettingsError(str(e)) from None
        _reloaded(store)
        if changes["agent"] is None:
            rest["agent"] = None
    cfg = _read(store)
    for key, value in rest.items():
        if value is None:
            cfg.pop(key, None)
        else:
            cfg[key] = value
    if rest:
        init.write_config(store, cfg, "settings: " + ", ".join(sorted(rest)))
    _reloaded(store)
    defaults = _defaults(store)
    lines = []
    for key, value in changes.items():
        if key in said:
            lines += said[key]
        else:
            lines.append(f"{key} reset to its default ({defaults[key]})" if value is None else f"{key} set to {value}")
    if pickup_was and not store.pickup:
        lines.append(PICKUP_OFF)
    return lines
