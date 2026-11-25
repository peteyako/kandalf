"""The agent seam for the Antigravity CLI (`agy`): the same plain functions as
claude_code.py (see its docstring), and the only module that runs `agy`.

Antigravity has no background sessions of its own, so each one here is a detached
`agy --output-format stream-json --print=<text>` process. Its short id is the first 8 hex digits
of a fresh uuid. Its state lives in STATE_DIR (KANDALF_AGY_DIR overrides it), one
`<short>.json` (pid, full conversation id once emitted, folder, reach flags, whether it was stopped),
`<short>.log` (the NDJSON events it printed, which transcript() makes readable) and `<short>.exit`
(its exit code, written when it ends). A running process is "busy"; one that has ended is "idle":
its conversation can be resumed with `--conversation`, by the board or by you (`kandalf attach`,
which follows a running session and opens a finished one). Antigravity reads lifecycle hooks from
`.agents/hooks.json`: when pickup passes `--settings`, link_hooks() writes the board's PostToolUse
hooks there and keeps the file out of git through the repo's info/exclude.
"""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

CLI = "agy"
LABEL = "Antigravity CLI"
NAME = "Antigravity"
UPDATE = "`agy update`"
MODEL = "auto"  # Antigravity uses its configured default model
MODELS = [
    "auto",
    "gemini-4-neon-high",
    "gemini-4-neon-medium",
    "gemini-4-argon-high",
    "gemini-4-argon-medium",
    "gemini-4-hydrogen-high",
    "gemini-3.8-flash-medium",
    "claude-opus-5-5-high",
    "claude-sonnet-5-5-medium",
]
ALIASES = {
    "neon": "gemini-4-neon-medium",
    "argon": "gemini-4-argon-medium",
    "hydrogen": "gemini-4-hydrogen-medium",
    "flash": "gemini-3.8-flash-medium",
    "opus": "claude-opus-5-5-medium",
    "sonnet": "claude-sonnet-5-5-medium",
}
TRUST = "Before the first story: run `agy` once in {app} and sign in, or sessions may not start there."
STATE_DIR = Path(os.environ.get("KANDALF_AGY_DIR") or Path.home() / ".kandalf" / "agy")


def installed() -> bool:
    """Whether `agy` is on PATH."""
    return bool(shutil.which(CLI))


# ---------- what the installed CLI can do ----------
def help_text(*args) -> str:
    """What `agy <args> --help` prints; "" when it can't be run."""
    try:
        run = subprocess.run([CLI, *args, "--help"], capture_output=True, text=True, timeout=30)
        return run.stdout + run.stderr
    except (OSError, subprocess.SubprocessError):
        return ""


def cli_lacks(help=help_text) -> list:
    """What pickup needs that the installed agy lacks, of its options --print, --output-format,
    --conversation, --add-dir, --mode and --dangerously-skip-permissions, read from its help."""
    top = help()
    needs = ("--print", "--output-format", "--conversation", "--add-dir", "--mode", "--dangerously-skip-permissions")
    return [f"agy {o}" for o in needs if not re.search(rf"^\s+(?:-\w, )?{o}(?![\w-])", top, re.M)]


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


def _session_id(short: str, meta: dict):
    """The full Antigravity conversation id: stored in meta, or read from the session's log."""
    if meta.get("session"):
        return meta["session"]
    try:
        raw = _file(short, "log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        cid = (ev.get("conversation_id")
               or (ev.get("step_update") or {}).get("conversation_id")
               or (ev.get("result") or {}).get("conversation_id"))
        if cid:
            return cid
    return None


# ---------- starting a session ----------
LOCAL = Path(".agents") / "hooks.json"  # where Antigravity reads a workspace's lifecycle hooks
AGY_HOOKS = {
    "kandalf": {
        "PostToolUse": [{
            "matcher": "edit_file|write_file|write_to_file|replace_file_content|multi_replace_file_content|sed_file",
            "hooks": [
                {"type": "command", "command": "command -v kandalf >/dev/null && kandalf hook autocommit 2>/dev/null || echo '{}'"},
                {"type": "command", "command": "command -v kandalf >/dev/null || { echo '{}'; exit 0; }; kandalf hook check"},
            ],
        }],
    },
}


def link_hooks(cwd):
    """Write the board's PostToolUse hooks into cwd as .agents/hooks.json, unless something is
    there, and keep the file out of git through the repo's info/exclude (shared by its worktrees)."""
    mine = Path(cwd) / LOCAL
    if mine.exists() or mine.is_symlink():
        return
    mine.parent.mkdir(parents=True, exist_ok=True)
    mine.write_text(json.dumps(AGY_HOOKS, indent=2) + "\n", encoding="utf-8")
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
    """Run `agy --output-format stream-json --print=<text>` in cwd as a detached process, on the
    model and conversation args give, with dirs added to what it can work in:
    {"id": the session's short id}, or {"error": why it didn't start}."""
    opts = _options(args)
    session = opts.get("--resume") or None
    cmd = ["--output-format", "stream-json"]
    if session:
        cmd += ["--conversation", session]
    if opts.get("--model") and opts["--model"] != "auto":
        cmd += ["--model", ALIASES.get(opts["--model"], opts["--model"])]
    reach = [arg for d in dirs for arg in ("--add-dir", str(d))]  # what attach opens it with too
    if permission == "plan":
        reach += ["--mode", "plan"]
    elif permission in ("auto", "bypassPermissions"):
        reach += ["--dangerously-skip-permissions"]
    elif permission == "acceptEdits":
        reach += ["--mode", "accept-edits"]
    cmd += reach
    cmd.append(f"--print={text}")
    short = uuid.uuid4().hex[:8]
    try:
        if opts.get("--settings"):
            link_hooks(cwd)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(_file(short, "log"), "wb") as log:
            child = subprocess.Popen(["sh", "-c", '"$@"; echo $? > "$0"', str(_file(short, "exit")), CLI, *cmd],
                                     cwd=cwd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                     start_new_session=True)
        _file(short, "json").write_text(
            json.dumps({"pid": child.pid, "session": session, "cwd": str(cwd), "reach": reach, "stopped": False}),
            encoding="utf-8")
    except (OSError, subprocess.SubprocessError) as e:
        return {"error": str(e)}
    _children[short] = child
    return {"id": short}


# ---------- a one-shot question ----------
def ask(text: str, cwd, timeout: int = 45) -> None:
    """Antigravity has no one-shot mode this module knows to give no tools, so it makes no
    suggestion: always None, and nothing is run."""
    return None


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
    """{short id: full conversation id or None} for every session listed, stopped ones too."""
    return {short: _session_id(short, meta) for short in _shorts() if (meta := _read(short))}


# ---------- a session's output ----------
def _brief(args, limit: int = 80) -> str:
    """A tool call's arguments in a few words: its command, path, pattern or query."""
    if not isinstance(args, dict):
        return ""
    for key in ("CommandLine", "command", "Path", "TargetFile", "path", "Pattern", "pattern",
                "Query", "query", "SearchPath", "Url", "url", "Description", "description", "Prompt", "prompt"):
        if isinstance(args.get(key), str):
            text = " ".join(args[key].split())
            return text if len(text) <= limit else text[:limit - 1] + "…"
    return ""


def transcript(raw: str) -> str:
    """A session's stream-json output as readable lines; a line that isn't JSON is kept as it is."""
    out, buf = [], []

    def flush():
        if buf:
            text = "".join(buf).strip()
            buf.clear()
            if text:
                out.append(text)

    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            flush()
            out.append(line)
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("event")
        if kind == "step_update":
            su = event.get("step_update") or {}
            stype, state = su.get("step_type"), su.get("state")
            if stype == "agent_response":
                delta = su.get("text_delta") or ""
                if delta:
                    buf.append(delta)
                if state == "DONE":
                    flush()
            elif stype == "tool":
                flush()
                info = su.get("tool_info") or {}
                what = su.get("tool_name") or info.get("name") or "a tool"
                if state == "ACTIVE":
                    brief = _brief(info.get("parameters"))
                    out.append(f"● {what}" + (f": {brief}" if brief else ""))
                elif state == "ERROR" or (state == "DONE" and info.get("error")):
                    err = info.get("error") or su.get("error") or "no reason given"
                    out.append(f"✗ failed: {err}")
        elif kind == "result":
            flush()
            res = event.get("result") or {}
            status = res.get("status")
            if status == "SUCCESS":
                out.append("— finished")
            elif status:
                out.append(f"— ended with status {status}")
        elif kind == "error":
            flush()
            msg = event.get("message") or event.get("error") or "error"
            out.append(f"! {msg}")
    flush()
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
        text = (text + "\n" if text else "") + f"— ended with exit code {code}"
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
    if lines[-1].startswith(("— ended with exit code", "— ended with status")):
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
    """kandalf attach for an Antigravity session: print its transcript, follow it while it runs
    (Ctrl-C stops following and leaves it at work), then open the finished conversation in
    `agy --conversation` in its folder, to read it and carry on."""
    meta = _read(sid)
    if not meta:
        print(f"no Antigravity session {sid}", file=sys.stderr)
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
    conv = _session_id(sid, meta)
    if not conv:
        print(f"Session {sid} has no conversation id to resume.", file=sys.stderr)
        return 1
    print(f"Opening the conversation: {CLI} --conversation {conv}. While it is open here the board"
          " can't resume it: quit it (/exit) before Continue on the tile.", file=out, flush=True)
    os.chdir(meta.get("cwd") or ".")
    os.execvp(CLI, [CLI, "--conversation", conv, *meta.get("reach", [])])


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
