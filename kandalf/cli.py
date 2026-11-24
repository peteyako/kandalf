"""kandalf: the board tool for Plan and Build boards, for any store (see store.py).

    kandalf init [DIR] [--example] [--yes]   make a store (beside this git repo) and link it in
    kandalf serve [--dir STORE] [--port N]   serve a store's boards and open them (--no-open: don't)
    kandalf key [next|assign] [--dir STORE]  the next free tile key; or key every unkeyed tile
    kandalf commit [status|now] [--dir STORE] the store's commit queue; or commit and push it now
    kandalf check FILE...                    does a board file round-trip through the board's parser?
    kandalf hook autocommit|check            Claude Code PostToolUse hooks (read the call on stdin)
    kandalf attach ID|KEY [--dir STORE]      open a story's session: follow it, then carry on in it
    kandalf status KEY [--dir STORE]         what a story's session is doing: the tile's Status line
    kandalf where [--dir STORE]              which store, its key, port, app and repos
    kandalf doctor [--dir STORE]             what works and what doesn't, one line per check
    kandalf agent [claude|copilot|agy] [--force] [--dir STORE]
                                             the agent pickup hands stories to; or switch it
    kandalf repo add PATH [--name NAME] [--trunk BRANCH] [--dir STORE]
                                             another repo stories can work in
    kandalf repo list [--dir STORE]          the store's repos
    kandalf repo rm NAME [--dir STORE]       take a repo out of board.json
    kandalf uninstall [--dir STORE]          remove the docs/plans link from each of the store's repos
    kandalf notes [--update] [--dir STORE]   whether the filing rules agents read are today's: the Build
                                             board's header and the note in each repo; --update brings them up to date
    kandalf hooks [--add] [--dir STORE]      the board's PostToolUse hooks block; --add puts it in the
                                             store's .claude/settings.json and links that into its app

With no --dir, the store is the one you're in, or the one the current git repo links
in under docs/ (docs/plans in an app). Install: symlink this file onto your PATH,
e.g. ln -s "$PWD/bin/kandalf" ~/.local/bin/kandalf
"""

import os
import subprocess
import sys
from pathlib import Path

from . import store as stores

HERE = Path(__file__).resolve().parent


def pick_store(args):
    """(store, remaining args): --dir STORE if given, else the default store."""
    rest, folder = [], None
    it = iter(args)
    for a in it:
        if a == "--dir":
            folder = next(it, None)
        elif a.startswith("--dir="):
            folder = a.split("=", 1)[1]
        else:
            rest.append(a)
    st = stores.Store(folder) if folder else stores.default()
    if st is None:
        raise SystemExit("board: no store here. Pass --dir, or run it from a store or an app repo "
                         "that links one in (docs/plans).")
    return st, rest


def present_board(arg: str) -> str:
    """The file `kandalf check` is given, or, when there is no such file and its name is another
    spelling of one of its store's boards (docs/plans/plans.md in a store that was moved), the
    store's present board, said on one line."""
    path = Path(arg)
    if path.exists() or path.name not in (stores.BUILD, stores.PLAN, stores.OLD_BUILD, stores.OLD_PLAN):
        return arg
    folder = stores.Store(path.parent)
    if not folder.is_store:
        return arg
    now = folder.board(path.name)
    if now == path.name:
        return arg
    old = path.name in (stores.OLD_BUILD, stores.OLD_PLAN) and folder.build == stores.BUILD  # an old name, on a moved store
    print(f"{path.name} is now {now}: checking that." if old else f"{path.name} is {now} in this store: checking that.")
    return str(path.parent / now)


def session_of(args):
    """The session id kandalf attach is given: an id as it is, or a tile key (CAS-01) looked
    up in the store's pickup jobs, else its draft's (one not being ended). None, said on stderr,
    when a key has neither."""
    want = args[0]
    if all(c in "0123456789abcdef" for c in want):
        return want
    from . import drafts, jobs
    st, rest = pick_store(args)
    key = rest[0].upper()
    job = jobs.Ledger(st).latest(key, "id")
    if job is not None:
        return job["id"]
    draft = next((d for d in drafts.summary(st) if d["key"] == key and d["id"]), None)
    if draft is not None:
        return draft["id"]
    print(f"{key} has no session", file=sys.stderr)
    return None


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0
    cmd, args = argv[0], argv[1:]
    if cmd == "init":
        from . import init
        return init.main(args)
    if cmd == "serve":
        from . import serve
        serve.main(args)
    elif cmd == "key":
        from . import pln
        st, rest = pick_store(args)
        pln.main(st, rest)
    elif cmd == "commit":
        from . import gitlog
        st, rest = pick_store(args)
        gitlog.main(st, rest)
    elif cmd == "check":
        return subprocess.run(["node", str(HERE / "check.js"), *[present_board(a) for a in args]]).returncode
    elif cmd == "hook" and args[:1] == ["autocommit"]:
        from . import autocommit
        return autocommit.main()
    elif cmd == "hook" and args[:1] == ["check"]:
        from . import checkhook
        return checkhook.main()
    elif cmd == "doctor":
        from . import init
        st, _ = pick_store(args)
        return init.doctor(st)
    elif cmd == "agent":
        from . import init
        st, rest = pick_store(args)
        return init.agent_command(st, rest)
    elif cmd == "repo":
        from . import init
        st, rest = pick_store(args)
        return init.repo_command(st, rest)
    elif cmd == "uninstall":
        from . import init
        st, _ = pick_store(args)
        for line in init.uninstall(st):
            print(line)
        return 0
    elif cmd == "hooks":
        from . import init
        if "--add" in args:
            st, _ = pick_store([a for a in args if a != "--add"])
            for line in init.add_hooks(st):
                print(line)
            return 0
        print(init.hooks_text())
        print('Paste it under "hooks" in a .claude settings file, or let `kandalf hooks --add` keep it in the store.', file=sys.stderr)
    elif cmd == "notes":
        from . import init
        st, rest = pick_store(args)
        for line in init.notes(st, "--update" in rest):
            print(line)
        return 0
    elif cmd == "attach" and args:
        from . import agent
        sid = session_of(args)
        if sid is None:
            return 1
        return agent.open_session(sid)
    elif cmd == "status" and args:
        from . import pickup
        from . import tiles as reader
        st, rest = pick_store(args)
        key = rest[0].upper()
        tile = reader.Snapshot.read(st, [st.build]).tile(key)
        line = pickup.status(st, key, tile.text) if tile else None
        if line is None:
            print(f"{key} has no session to report on", file=sys.stderr)
            return 1
        print(line)
    elif cmd == "where":
        from . import init
        st, _ = pick_store(args)
        print(f"{st.name}: {st.dir}\n  keys {st.key}-NN, port {st.port}, link docs/{st.link}"
              f"\n  app {st.app or '(none)'} @ {st.trunk}, agent {st.agent}, model {st.model}"
              + "".join(f"\n  repo {line}" for line in init.repo_lines(st)) + f"\n  git {st.root or '(not a repo)'}")
    else:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    return 0
