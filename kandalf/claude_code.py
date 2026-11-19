"""The agent seam: every call pickup makes to the Claude Code CLI (`claude`), and the reading
of what the CLI prints. pickup.py holds the policy (jobs, states, prompts) and does
`from . import claude_code as agent`; nothing else in the package runs `claude`.

Another agent's module would provide the same plain functions:

    installed()                     whether its CLI is on PATH
    cli_lacks()                     what pickup needs that the installed CLI lacks, as names
    start(args, cwd, permission, text, dirs=())
                                    start a background session in cwd, given the folders in dirs
                                    to work in too: {"id": short id}, or {"error"}
    sessions()                      {short id: "busy" or "idle"} of the live background sessions, or None
    known()                         {short id: full id or None} of every session listed, finished too, or None
    logs(sid)                       a session's recent output, or None when it can't be read
    waiting_for(output), screen_tail(output)
                                    what that output says: why it waits for input, its last words
    stop(sid), remove(sid)          stop a session; take it off the list

start's args are pickup's: "--model m", "-n name" and "--resume <full id>".
"""

import json
import os
import re
import shutil
import subprocess

CLI = "claude"
NAME = "Claude"
MODEL = "opus"  # the model a session runs on unless the store or tile says
MODELS = ["opus", "sonnet", "haiku", "fable"]  # the page's Model dropdown
TRUST = "Before the first story: run `claude` once in {app} and accept its trust prompt, or sessions can't start there."


def installed() -> bool:
    """Whether `claude` is on PATH."""
    return bool(shutil.which(CLI))


# ---------- what the installed CLI can do ----------
OPTION_RE = r"^  (?:-[\w-]+, )*{}(?![\w-])"  # an option's own line in a --help listing
COMMANDS_RE = re.compile(r"^  ([a-z][\w-]*(?:\|[a-z][\w-]*)*)", re.M)  # "  stop|kill <id>  ..."


def help_text(*args) -> str:
    """What `claude <args> --help` prints; "" when it can't be run."""
    try:
        run = subprocess.run([CLI, *args, "--help"], capture_output=True, text=True, timeout=30)
        return run.stdout + run.stderr
    except (OSError, subprocess.SubprocessError):
        return ""


def cli_lacks(help=help_text) -> list:
    """What pickup needs that the installed claude lacks, of `claude --bg`, `claude agents
    --json`, `claude logs`, `claude stop` and `claude rm`, read from its help.
    help(*args): the text of `claude <args> --help`."""
    top = help()
    listed = top.split("\nCommands:", 1)[1] if "\nCommands:" in top else ""
    commands = {name for m in COMMANDS_RE.finditer(listed) for name in m.group(1).split("|")}
    has = {"claude --bg": bool(re.search(OPTION_RE.format("--bg"), top, re.M)),
           "claude agents --json": "agents" in commands and bool(re.search(OPTION_RE.format("--json"), help("agents"), re.M))}
    has.update({f"claude {c}": c in commands for c in ("logs", "stop", "rm")})
    return [need for need, there in has.items() if not there]


# ---------- starting a session ----------
SESSION_RE = re.compile(r"backgrounded\s+\S+\s+([0-9a-f]{8})\b")  # what `claude --bg` says it started


def start(args, cwd, permission: str, text: str, dirs=()) -> dict:
    """Run `claude --bg <args> --add-dir <dirs> --permission-mode <permission> <text>` in cwd:
    {"id": the session's short id}, or {"error": why it didn't start}. --add-dir takes any
    number of folders, so the permission mode after it is what ends the list."""
    more = ["--add-dir", *map(str, dirs)] if dirs else []
    try:
        run = subprocess.run([CLI, "--bg", *args, *more, "--permission-mode", permission, text],
                             cwd=cwd, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as e:
        return {"error": str(e)}
    found = SESSION_RE.search(run.stdout)
    if run.returncode != 0 or not found:
        return {"error": (run.stderr or run.stdout).strip()[-300:] or "claude --bg gave no session id"}
    return {"id": found.group(1)}


# ---------- the sessions there are ----------
def _agents(*flags):
    """`claude agents --json`, parsed; None when the sessions can't be listed."""
    try:
        run = subprocess.run([CLI, "agents", "--json", *flags], capture_output=True, text=True, timeout=60)
        return json.loads(run.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def sessions():
    """{id: "busy" or "idle"} for the background sessions that are still there, or None when
    they can't be listed. Idle: it finished its turn or its whole task (it can still be
    attached to), was interrupted, or waits for you. A session that was stopped, closed or
    removed has no status and isn't in it."""
    listed = _agents()
    if listed is None:
        return None
    return {s["id"]: "idle" if s.get("status") == "idle" else "busy" for s in listed
            if s.get("kind") == "background" and s.get("id") and s.get("status")}


def known():
    """{short id: full id} for every session listed, finished ones too (the full id, which
    --resume needs, is None when the CLI doesn't give it); None when they can't be listed."""
    listed = _agents("--all")
    if listed is None:
        return None
    out = {}
    for s in listed:
        if s.get("id"):
            out.setdefault(s["id"], s.get("sessionId"))
    return out


# ---------- a session's screen ----------
def logs(sid: str):
    """A session's recent output (`claude logs`, a redrawn terminal screen); None when it
    can't be read."""
    try:
        return subprocess.run([CLI, "logs", sid], capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return None


# What an idle session's screen ends with says whether it waits for input. Each marker, and
# what it means; the one that comes last in the session's recent output counts.
SCREEN_MARKS = [
    (re.compile(r"Interrupted\s*·\s*What should Claude do instead"), "interrupted"),
    (re.compile(r"Do you want to |Would you like to |❯\s*1\.\s|Enter to select|Esc to cancel"), "question"),
    (re.compile(r"Worked for [^\n]{0,24}·\s*done"), None),  # it finished its turn
]
ANSI_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-9;?]*[A-Za-z]|\x1b[78=>]")


def waiting_for(output: str):
    """Why a session waits for input, read from its recent output (logs):
    "interrupted", "question", or None when it isn't waiting."""
    text = ANSI_RE.sub("\n", output)
    last, reason = -1, None
    for mark, means in SCREEN_MARKS:
        found = [m.start() for m in mark.finditer(text)]
        if found and found[-1] > last:
            last, reason = found[-1], means
    return reason


# The terminal's own furniture in a session's output: spinners, counters, the mode line, key hints.
SCREEN_NOISE = re.compile(
    r"…$|^\(?\d+s\b|tokens\)?$|^[↓↑←]|shift\+tab|⏵⏵|for agents|esc to interrupt|Checking for updates|Press Ctrl-C"
    r"|^[─━═\-_ ]+$|^❯|^\d+ shells?\b|still running|^\w+ for \d+\w*( \d+\w*)* ·|^done \d"
    r"|[─━═]{3}|to manage$|^[·•↓↑←→ ]+$", re.I)  # a rule with the session's title in it, the footer's hints


def screen_tail(output: str, limit: int = 220) -> str:
    """The last words on a session's screen, from its recent output (logs): the terminal's
    furniture and formatting marks dropped, cut to the last `limit` characters at a word.
    Rough: the output is a redrawn screen, not a transcript."""
    pieces = [re.sub(r"\s+", " ", piece).strip() for piece in ANSI_RE.sub("\n", output).split("\n")]
    words = " ".join(p for p in pieces if len(p) > 1 and not SCREEN_NOISE.search(p))
    words = re.sub(r"[*`\[\]]", "", words).replace('"', "'").strip()  # nothing the tile would read as markdown
    if len(words) <= limit:
        return words
    tail = words[-limit:]
    if words[-limit - 1] != " ":  # the cut fell inside a word: start at the next one
        tail = tail.split(" ", 1)[-1]
    return "… " + tail.strip()


# ---------- attaching to a session ----------
def attach(sid: str) -> int:
    """kandalf attach for a Claude session: `claude attach <id>`, in place of this process."""
    os.execvp(CLI, [CLI, "attach", sid])


# ---------- stopping a session ----------
def stop(sid: str):
    """`claude stop <id>`. Raises OSError or SubprocessError when it can't be run."""
    subprocess.run([CLI, "stop", sid], capture_output=True, text=True, timeout=60)


def remove(sid: str):
    """`claude rm <id>`: take a stopped session off the list. Raises as stop does."""
    subprocess.run([CLI, "rm", sid], capture_output=True, text=True, timeout=60)
