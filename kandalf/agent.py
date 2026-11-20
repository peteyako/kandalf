"""The agent the board hands stories to: Claude Code (claude_code.py) or the GitHub Copilot CLI
(copilot_cli.py), as board.json's "agent" says. Store sets it with use(); pickup does
`from . import agent` and calls the plain functions here, which go to the one in use.
Both modules provide the functions their docstrings list; this adds label, attach and
update_hint, the words the board shows the user.
"""

from . import claude_code, copilot_cli

MODULES = {"claude": claude_code, "copilot": copilot_cli}
_current = {"name": "claude"}


def use(name: str):
    """Make name ("claude" or "copilot") the agent in use. Raises ValueError for another."""
    if name not in MODULES:
        raise ValueError(f'"agent" must be one of {", ".join(MODULES)}, not {name!r}')
    _current["name"] = name


def name() -> str:
    return _current["name"]


def _module():
    return MODULES[_current["name"]]


def label() -> str:
    """The agent's name as the board says it: "Claude Code" or "Copilot CLI"."""
    return "Claude Code" if _current["name"] == "claude" else copilot_cli.LABEL


def cli() -> str:
    """The command that runs the agent."""
    return _module().CLI


def update_hint() -> str:
    """How to update the agent's CLI, in backticks."""
    return "`claude update`" if _current["name"] == "claude" else copilot_cli.UPDATE


def short() -> str:
    """The agent's name in a word, for the board's labels: "Claude" or "Copilot"."""
    return _module().NAME


def attach(sid: str) -> str:
    """The command that opens a session, by its short id: `kandalf attach`, whichever agent runs
    it (open_session). For Claude it hands over to `claude attach`; for Copilot it follows the
    session while it runs and then opens its conversation."""
    return f"kandalf attach {sid}"


def transcript(sid: str, session: str = None, limit: int = 400):
    """A session's log for the board, whichever agent ran it: by short id for Copilot, by full
    id (session) for Claude. Its last `limit` lines, or None when it can't be read."""
    if copilot_cli.has(sid):
        text = copilot_cli.logs(sid)
        return None if text is None else "\n".join(text.splitlines()[-limit:])
    return claude_code.transcript(session, limit)


def open_session(sid: str) -> int:
    """kandalf attach: open the session sid, whichever agent started it."""
    return (copilot_cli if copilot_cli.has(sid) else claude_code).attach(sid)


def default_model() -> str:
    """The model a session runs on when neither board.json nor the tile names one."""
    return _module().MODEL


def models() -> list:
    """The models the page's Model dropdown offers."""
    return list(_module().MODELS)


def trust(app) -> str:
    """What to do once in a repo before the agent's first session there."""
    return _module().TRUST.format(app=app)


def default_name(which=None) -> str:
    """The agent a new store picks: claude when it is on PATH, else copilot when that is, else claude."""
    import shutil
    which = which or shutil.which
    return "claude" if which(claude_code.CLI) or not which(copilot_cli.CLI) else "copilot"


def installed() -> bool:
    return _module().installed()


def cli_lacks() -> list:
    return _module().cli_lacks()


def start(args, cwd, permission, text, dirs=(), store_dir=None):
    """Start a session. store_dir: the store's folder, which a Copilot session is given too, as
    it can only reach the folders it is given (Claude reaches the store through docs/plans)."""
    if _current["name"] == "copilot" and store_dir is not None:
        dirs = [*dirs, store_dir]
    return _module().start(args, cwd, permission, text, dirs)


def sessions():
    return _module().sessions()


def known():
    return _module().known()


def logs(sid):
    return _module().logs(sid)


def waiting_for(output):
    return _module().waiting_for(output)


def screen_tail(output, limit: int = 220):
    return _module().screen_tail(output, limit)


def stop(sid):
    return _module().stop(sid)


def remove(sid):
    return _module().remove(sid)
