"""Claude Code PostToolUse hook: queue an Edit/Write that landed in a plans store for commit.

    kandalf hook autocommit     # reads the tool call on stdin

An app links its store in at docs/plans, so a session editing the board or a plan
doc edits the store. The store's .claude/settings.json (loaded by pickup sessions
with --settings, and linked into the app as .claude/settings.local.json) runs this after every
Edit/Write: if the file (symlinks resolved) is a .md file inside a store that is a
git repo, just that file is queued for the store's next batched commit
(gitlog.record). Anything else is ignored. It never fails the tool call: every
error exits 0 quietly. The batched commit's subject is worked out when it is
made, from the net change (gitlog.summarize); this only notes the step for its body.
"""

import json
import sys
from pathlib import Path

from . import gitlog
from . import store as stores


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
        if not path:
            if is_agy:
                stdout.write("{}\n")
            return 0
        f = Path(path).resolve()  # resolves the docs/plans symlink
        st = stores.find(f.parent)
        if not st or not st.root or not f.is_file() or f.suffix != ".md":
            if is_agy:
                stdout.write("{}\n")
            return 0
        rel = f.relative_to(st.root).as_posix()
        who = "agy" if is_agy else "claude"
        gitlog.record(st, [rel], f"{who}: {'edit' if gitlog.head_text(st.root, rel) else 'add'} {rel}")
    except Exception:  # a hook must never break the session's edit
        pass
    if is_agy:
        stdout.write("{}\n")
    return 0
