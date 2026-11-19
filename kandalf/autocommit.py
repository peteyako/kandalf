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


def main(stdin=sys.stdin) -> int:
    try:
        call = json.load(stdin)
        path = (call.get("tool_input") or {}).get("file_path")
        if not path:
            return 0
        f = Path(path).resolve()  # resolves the docs/plans symlink
        st = stores.find(f.parent)
        if not st or not st.root or not f.is_file() or f.suffix != ".md":
            return 0
        rel = f.relative_to(st.root).as_posix()
        gitlog.record(st, [rel], f"claude: {'edit' if gitlog.head_text(st.root, rel) else 'add'} {rel}")
    except Exception:  # a hook must never break the session's edit
        pass
    return 0
