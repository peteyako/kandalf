"""The agent seam for the GitHub Copilot CLI (`copilot`): the same plain functions as
claude_code.py (see its docstring), and the only module that runs `copilot`.

Copilot has no background sessions of its own, so each one here is a detached
`copilot -p <text> --session-id <uuid> --output-format json` process. Its short id is the first 8 hex digits of
a fresh uuid. Its state lives in STATE_DIR (KANDALF_COPILOT_DIR overrides it), one
`<short>.json` (pid, full session id, folder, whether it was stopped), `<short>.log` (the
events it printed, one JSON object a line, which transcript() makes readable)
and `<short>.exit` (its exit code, written when it ends). A running process is "busy"; one
that has ended is "idle": its conversation can be resumed, by the board or by you
(`kandalf attach`, which follows a running session and opens a finished one). Copilot has no --settings, but it
reads Claude Code hooks from a repo's .claude/settings.local.json: the board's hooks file
(pickup's --settings) is linked into the session's folder there (link_hooks).
"""

import json
import os
import sys
import time
import re
import shutil
import signal
import subprocess
import uuid
from pathlib import Path

CLI = "copilot"
LABEL = "Copilot CLI"
NAME = "Copilot"
UPDATE = "`copilot update`"
MODEL = "auto"  # Copilot picks the model
MODELS = ["auto", "claude-opus-5.5", "claude-sonnet-5.5", "gpt-6.2-sol", "gemini-4-carbon"]
TRUST = "Before the first story: run `copilot` once in {app} and trust the folder, or sessions may not start there."
STATE_DIR = Path(os.environ.get("KANDALF_COPILOT_DIR") or Path.home() / ".kandalf" / "copilot")
WRITES = {"auto", "acceptEdits", "bypassPermissions"}  # the modes that let a session run its tools unasked


def installed() -> bool:
    """Whether `copilot` is on PATH."""
    return bool(shutil.which(CLI))


# ---------- what the installed CLI can do ----------
def help_text(*args) -> str:
    """What `copilot <args> --help` prints; "" when it can't be run."""
    try:
        run = subprocess.run([CLI, *args, "--help"], capture_output=True, text=True, timeout=30)
        return run.stdout + run.stderr
    except (OSError, subprocess.SubprocessError):
        return ""


def cli_lacks(help=help_text) -> list:
    """What pickup needs that the installed copilot lacks, of its options -p, --session-id,
    --resume, --add-dir, --name, --allow-all-tools and --allow-all-paths, read from its help."""
    top = help()
    needs = ("--prompt", "--session-id", "--resume", "--add-dir", "--name", "--allow-all-tools", "--allow-all-paths")
    return [f"copilot {o}" for o in needs if not re.search(rf"^\s+(?:-\w, )?{o}(?![\w-])", top, re.M)]


# ---------- the state of a session ----------
_children = {}  # short id -> the Popen this process started, so an ended one is reaped


def _file(short: str, ext: str) -> Path:
    return STATE_DIR / f"{short}.{ext}"


def _read(short: str):
    try:
        return json.loads(_file(short, "json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _shorts() -> list:
    return sorted(p.stem for p in STATE_DIR.glob("*.json")) if STATE_DIR.is_dir() else []


def _running(short: str, meta: dict) -> bool:
    """Whether the session's process is still there."""
    child = _children.get(short)
    if child is not None:
        return child.poll() is None
    if _file(short, "exit").exists():
        return False
    try:
        os.kill(meta["pid"], 0)
        return True
    except (OSError, KeyError, TypeError):
        return False


# ---------- starting a session ----------
LOCAL = Path(".claude") / "settings.local.json"  # where Copilot reads a repo's own hooks


def link_hooks(cwd, hooks):
    """Link the hooks file into cwd as .claude/settings.local.json, unless something is there,
    and keep the link out of git through the repo's info/exclude (shared by its worktrees)."""
    mine = Path(cwd) / LOCAL
    if mine.exists() or mine.is_symlink():
        return
    mine.parent.mkdir(parents=True, exist_ok=True)
    mine.symlink_to(Path(hooks).resolve())
    found = subprocess.run(["git", "rev-parse", "--git-path", "info/exclude"], cwd=cwd, capture_output=True, text=True)
    if found.returncode != 0:
        return
    exclude = Path(cwd) / found.stdout.strip()
    have = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
    if f"/{LOCAL.as_posix()}" not in have.split("\n"):
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text(have + ("" if not have or have.endswith("\n") else "\n") + f"/{LOCAL.as_posix()}\n", encoding="utf-8")

def _options(args) -> dict:
    """pickup's args ("--model m", "-n name", "--resume <full id>", "--settings f") as options."""
    got, rest = {}, list(args)
    while rest:
        flag = rest.pop(0)
        value = rest.pop(0) if rest else None
        got[flag] = value
    return got


def start(args, cwd, permission: str, text: str, dirs=()) -> dict:
    """Run `copilot -p <text>` in cwd as a detached process, on the model, name and conversation
    args give, with dirs added to what it can work in: {"id": the session's short id}, or
    {"error": why it didn't start}."""
    opts = _options(args)
    session = opts.get("--resume") or str(uuid.uuid4())
    cmd = ["--output-format", "json", "--no-ask-user", "-p", text]
    cmd += [f"--resume={session}"] if opts.get("--resume") else ["--session-id", session]
    if opts.get("--model"):
        cmd += ["--model", opts["--model"]]
    if opts.get("-n"):
        cmd += ["--name", opts["-n"]]
    reach = [arg for d in dirs for arg in ("--add-dir", str(d))]  # what attach opens it with too
    if permission == "plan":
        reach += ["--mode", "plan"]
    elif permission == "bypassPermissions":
        reach += ["--allow-all"]
    elif permission == "auto":  # like Claude's auto mode, it may read and run things outside its folders
        reach += ["--allow-all-tools", "--allow-all-paths"]
    elif permission in WRITES:
        reach += ["--allow-all-tools"]
    cmd += reach
    short = uuid.uuid4().hex[:8]
    try:
        if opts.get("--settings"):
            link_hooks(cwd, opts["--settings"])
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(_file(short, "log"), "wb") as log:
            child = subprocess.Popen(["sh", "-c", '"$@"; echo $? > "$0"', str(_file(short, "exit")), CLI, *cmd],
                                     cwd=cwd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                     start_new_session=True)
        _file(short, "json").write_text(json.dumps({"pid": child.pid, "session": session, "cwd": str(cwd), "reach": reach, "stopped": False}), encoding="utf-8")
    except (OSError, subprocess.SubprocessError) as e:
        return {"error": str(e)}
    _children[short] = child
    return {"id": short}


# ---------- the sessions there are ----------
def sessions():
    """{id: "busy" or "idle"} for the sessions that are still there: busy while the process
    runs, idle once it has ended. A stopped one isn't in it."""
    out = {}
    for short in _shorts():
        meta = _read(short)
        if meta and not meta.get("stopped"):
            out[short] = "busy" if _running(short, meta) else "idle"
    return out


def known():
    """{short id: full session id} for every session listed, stopped ones too."""
    return {short: meta.get("session") for short in _shorts() if (meta := _read(short))}


# ---------- a session's output ----------
def _brief(args, limit: int = 80) -> str:
    """A tool call's arguments in a few words: its command, path, pattern or first value."""
    if not isinstance(args, dict):
        return ""
    for key in ("command", "path", "pattern", "query", "url", "description", "prompt"):
        if isinstance(args.get(key), str):
            text = " ".join(args[key].split())
            return text if len(text) <= limit else text[:limit - 1] + "…"
    return ""


def event_lines(event: dict) -> list:
    """The readable lines of one event of a session's JSON output: what it said, the tools it
    ran (and those that failed), warnings, and how it ended."""
    kind, data = event.get("type"), event.get("data") or {}
    if kind == "assistant.message" and (data.get("content") or "").strip():
        return [data["content"].strip()]
    if kind == "tool.execution_start":
        what = data.get("toolTitle") or data.get("toolName") or "a tool"
        brief = _brief(data.get("arguments"))
        return [f"● {what}" + (f": {brief}" if brief else "")]
    if kind == "tool.execution_complete" and data.get("success") is False:
        error = data.get("error") or {}
        return [f"✗ failed: {(error.get('message') if isinstance(error, dict) else error) or 'no reason given'}"]
    if kind in ("session.warning", "session.error") and data.get("message"):
        return [f"! {data['message']}"]
    if kind == "result":
        code = event.get("exitCode")
        return ["— finished" if code == 0 else f"— ended with exit code {code}"]
    return []


def transcript(raw: str) -> str:
    """A session's JSON output as readable lines; a line that isn't JSON is kept as it is."""
    out = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            out.append(line)
            continue
        if isinstance(event, dict):
            out += event_lines(event)
    return "\n".join(out)


def logs(sid: str):
    """What the session did so far, as a transcript; None when it can't be read."""
    try:
        raw = _file(sid, "log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    text = transcript(raw)
    code = _exit_code(sid)
    if code not in (None, 0) and "— ended with exit code" not in text:
        text += f"\n— ended with exit code {code}"
    return text


def _exit_code(sid: str):
    try:
        return int(_file(sid, "exit").read_text().strip())
    except (OSError, ValueError):
        return None


def waiting_for(output: str):
    """Why a finished session waits for you, read from its transcript (logs): "error" when it
    ended on an error, "question" when its last words ask one, or None."""
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines:
        return None
    if lines[-1].startswith("— ended with exit code"):
        return "error"
    said = [line for line in lines if not line.startswith(("● ", "✗ ", "! ", "— "))]
    return "question" if said and said[-1].rstrip().endswith("?") else None


def screen_tail(output: str, limit: int = 220) -> str:
    """The last words of a session's output, formatting marks dropped, cut to the last
    `limit` characters at a word."""
    words = re.sub(r"[*`\[\]]", "", re.sub(r"\s+", " ", output)).replace('"', "'").strip()
    if len(words) <= limit:
        return words
    tail = words[-limit:]
    if words[-limit - 1] != " ":
        tail = tail.split(" ", 1)[-1]
    return "… " + tail.strip()


# ---------- attaching to a session ----------
def has(sid: str) -> bool:
    """Whether sid is a session this module started."""
    return _read(sid) is not None


def attach(sid: str, out=sys.stdout, poll: float = 0.5) -> int:
    """kandalf attach for a Copilot session: print its transcript, follow it while it runs
    (Ctrl-C stops following and leaves it at work), then open the finished conversation in
    `copilot --resume` in its folder, to read it and carry on."""
    meta = _read(sid)
    if not meta:
        print(f"no Copilot session {sid}", file=sys.stderr)
        return 1
    seen = 0
    try:
        while True:
            raw = _file(sid, "log").read_text(encoding="utf-8", errors="replace") if _file(sid, "log").is_file() else ""
            done = raw[:raw.rfind("\n") + 1]  # whole lines only: the last may still be being written
            text = transcript(done[seen:])
            if text:
                print(text, file=out, flush=True)
            seen = len(done)
            if not _running(sid, meta):
                break
            time.sleep(poll)
    except KeyboardInterrupt:
        print(f"\nStopped following; session {sid} is still at work. `kandalf attach {sid}` again to follow it.", file=out)
        return 0
    if meta.get("stopped"):
        print(f"Session {sid} was stopped.", file=out)
    print(f"Opening the conversation: {CLI} --resume={meta['session']}. While it is open here the board"
          " can't resume it: quit it (/exit) before Continue on the tile.", file=out, flush=True)
    os.chdir(meta.get("cwd") or ".")
    os.execvp(CLI, [CLI, f"--resume={meta['session']}", *meta.get("reach", [])])


# ---------- stopping a session ----------
def stop(sid: str):
    """End the session's process (and what it started); keep it listed as stopped."""
    meta = _read(sid)
    if not meta:
        return
    if _running(sid, meta):
        try:
            os.killpg(meta["pid"], signal.SIGTERM)
        except OSError:
            pass
        child = _children.get(sid)
        if child is not None:
            try:
                child.wait(timeout=10)
            except subprocess.SubprocessError:
                pass
    meta["stopped"] = True
    _file(sid, "json").write_text(json.dumps(meta), encoding="utf-8")


def remove(sid: str):
    """Take a stopped session off the list, and delete what it left."""
    _children.pop(sid, None)
    for ext in ("json", "log", "exit"):
        _file(sid, ext).unlink(missing_ok=True)
