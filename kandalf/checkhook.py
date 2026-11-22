"""Claude Code PostToolUse hook: after an Edit/Write to a board file, check it still opens on the board.

    kandalf hook check          # reads the tool call on stdin

The board opens a file view-only when it doesn't round-trip through its own
parser (see check.js). The autocommit hook stays silent by design; this one speaks
up: when the edited file is a store's board (plans.md, backlog.md or
archive/archived.md) and check.js finds a difference, it exits 2 with check.js's
message on stderr, which Claude Code hands back to the session that made the
edit, so it fixes the file at once. Anything else (not a board file, no node,
bad input) exits 0.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

from . import store as stores

HERE = Path(__file__).resolve().parent
BOARDS = {"plans.md", "backlog.md", "archive/archived.md"}


def _file_path(call: dict):
    """The edited file's path from a Claude/Copilot (tool_input) or Antigravity (toolCall) hook."""
    path = (call.get("tool_input") or {}).get("file_path")
    if path:
        return path
    args = (call.get("toolCall") or {}).get("args") or {}
    for key in ("Path", "TargetFile", "file_path", "path"):
        if args.get(key):
            return args[key]
    return None


def main(stdin=sys.stdin, stdout=sys.stdout) -> int:
    is_agy = False
    try:
        call = json.load(stdin)
        is_agy = "toolCall" in call
        path = _file_path(call)
        node = shutil.which("node")
        if not path or not node:
            if is_agy:
                stdout.write("{}\n")
            return 0
        f = Path(path).resolve()  # resolves the docs/plans symlink
        st = stores.find(f.parent)
        if not st or not f.is_file() or f.relative_to(st.dir).as_posix() not in BOARDS:
            if is_agy:
                stdout.write("{}\n")
            return 0
        r = subprocess.run([node, str(HERE / "check.js"), str(f)], capture_output=True, text=True, timeout=20)
        if r.returncode == 1:
            sys.stderr.write(r.stderr)
            return 2
    except Exception:
        pass
    if is_agy:
        stdout.write("{}\n")
    return 0
