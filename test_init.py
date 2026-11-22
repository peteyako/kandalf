#!/usr/bin/env python3
"""Setup (init.py, serve.should_open): kandalf init makes a store and wires it to the app repo it
is run in; kandalf doctor says what works; kandalf hooks prints the hooks block; kandalf serve opens
the browser only when it should. All in temp dirs: no browser, no claude, no network, and git
with a test identity and none of the machine's config.

    python3 -m unittest test_init
"""

import contextlib
import datetime
import io
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kandalf import agent
from kandalf import cli
from kandalf import init
from kandalf import jobs as jobs_module
from kandalf import pickup
from kandalf import serve
from kandalf import store as stores

HERE = Path(__file__).resolve().parent
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "BOARD_NO_OPEN": "1"}
TODAY = datetime.date(2026, 11, 18)
OFF = 'Pickup is off for this store: set "pickup": true in its board.json to turn it on.'


def git(path, *args) -> str:
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout.strip()


def round_trips(path: Path) -> bool:
    return subprocess.run(["node", str(HERE / "kandalf" / "check.js"), str(path)], capture_output=True).returncode == 0


def no_input(prompt):
    raise AssertionError(f"asked {prompt!r} with nothing to answer it")


class Setup(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, GIT_ENV)
        env.start()
        self.addCleanup(env.stop)
        self.top = Path(tempfile.mkdtemp()).resolve()
        self.said = []

    def app(self, name="shop", branch="dev") -> Path:
        """A git repo with one commit, on branch."""
        app = self.top / name
        app.mkdir()
        git(app, "init", "-q", "-b", branch)
        (app / "README.md").write_text("an app\n")
        git(app, "add", "-A")
        git(app, "commit", "-qm", "first")
        return app

    def init(self, *args, cwd=None, input=no_input, tty=False, **probes):
        probes.setdefault("bindable", lambda port: True)
        probes.setdefault("why_off", lambda st: OFF)
        probes.setdefault("today", lambda: TODAY)
        probes.setdefault("which", lambda name: "/bin/claude" if name == "claude" else None)
        return init.run(list(args), cwd=cwd or self.top, input=input, isatty=lambda: tty, out=self.said.append, **probes)

    def config(self, folder: Path) -> dict:
        return json.loads((folder / "board.json").read_text())


class Init(Setup):
    def test_inside_an_app_repo_the_store_goes_beside_it_and_is_wired_to_it(self):
        app = self.app()
        self.assertEqual(self.init("--yes", cwd=app), 0)
        store = self.top / "shop-plans"
        self.assertEqual(self.config(store), {"name": "shop", "key": "SHO", "port": 7788, "link": "plans",
                                              "app": "../shop", "trunk": "dev", "pickup": False,
                                              "worktree": "{app}/.worktrees/{key}"})
        for f in ("plans.md", "backlog.md", "active/.gitkeep", "archive/archived.md"):
            self.assertTrue((store / f).is_file(), f)
        link = app / "docs" / "plans"
        self.assertEqual(os.readlink(link), "../../shop-plans")
        self.assertEqual(link.resolve(), store)
        self.assertEqual((app / ".gitignore").read_text(), "docs/plans\n.worktrees/\n.claude/settings.local.json\n")
        self.assertEqual(git(app, "rev-list", "--count", "HEAD"), "1")  # nothing committed in the app
        self.assertEqual(git(store, "log", "--format=%s"), "board: a new store")
        self.assertEqual(git(store, "status", "--porcelain"), "")
        self.assertEqual(stores.default(app).dir, store)  # board commands find it from the app

    def test_the_boards_come_from_the_templates_with_every_placeholder_filled(self):
        app = self.app()
        self.init("--yes", "--name", "The Shop", "--key", "TS", cwd=app)
        plans = (self.top / "The Shop" / "plans.md").read_text()
        backlog = (self.top / "The Shop" / "backlog.md").read_text()
        self.assertNotRegex(plans + backlog, r"[{}]")
        self.assertIn("every plan for The Shop", plans)
        self.assertIn("`TS-NN`", backlog)
        self.assertIn("merged into `dev`", plans)
        for b in ("plans.md", "backlog.md"):
            self.assertTrue(round_trips(self.top / "The Shop" / b), b)

    def test_a_folder_that_is_a_store_already_is_refused_and_nothing_is_written(self):
        app = self.app()
        self.init("--yes", cwd=app)
        before = (self.top / "shop-plans" / "board.json").read_text()
        with self.assertRaisesRegex(SystemExit, "already has board.json"):
            self.init("--yes", "--key", "NEW", cwd=app)
        self.assertEqual((self.top / "shop-plans" / "board.json").read_text(), before)
        bare = self.top / "old"
        bare.mkdir()
        (bare / "plans.md").write_text("# Plans\n")
        with self.assertRaisesRegex(SystemExit, "already has plans.md"):
            self.init("--yes", str(bare))
        self.assertEqual(sorted(p.name for p in bare.iterdir()), ["plans.md"])

    def test_outside_a_git_repo_init_asks_for_the_folder(self):
        with self.assertRaisesRegex(SystemExit, r"kandalf init DIR"):
            self.init("--yes")
        self.assertEqual(list(self.top.iterdir()), [])

    def test_a_store_made_outside_any_repo_stands_alone_in_a_repo_of_its_own(self):
        self.init("--yes", "notes-plans")
        store = self.top / "notes-plans"
        self.assertEqual(self.config(store), {"name": "notes", "key": "NOT", "port": 7788, "link": "plans"})
        self.assertEqual(stores.git_root(store), store)
        self.assertTrue(any("kandalf serve --dir " + str(store) in line for line in self.said))

    def test_a_store_inside_another_repo_is_not_committed_there(self):
        app = self.app()
        self.init("--yes", "--no-app", str(app / "plans"), cwd=app)
        self.assertEqual(git(app, "rev-list", "--count", "HEAD"), "1")
        self.assertNotIn("app", self.config(app / "plans"))
        self.assertTrue(any("nothing was committed" in line for line in self.said))

    def test_a_store_inside_its_own_app_is_refused(self):
        app = self.app()
        with self.assertRaisesRegex(SystemExit, "inside the app repo"):
            self.init("--yes", "plans", cwd=app)

    def test_an_app_named_by_flag_is_used_from_anywhere(self):
        app = self.app("web", branch="main")
        self.init("--yes", "--app", str(app))
        self.assertEqual(self.config(self.top / "web-plans")["app"], "../web")
        self.assertEqual(self.config(self.top / "web-plans")["trunk"], "main")
        with self.assertRaisesRegex(SystemExit, "isn't a git repo"):
            self.init("--yes", "--app", str(self.top), "x")

    def test_the_key_is_the_names_first_three_letters_or_digits_else_pln(self):
        self.assertEqual(init.default_key("myapp"), "MYA")
        self.assertEqual(init.default_key("my app"), "MYA")
        self.assertEqual(init.default_key("x1"), "X1")
        self.assertEqual(init.default_key("2fa-tool"), "PLN")
        self.assertEqual(init.default_key("..."), "PLN")

    def test_a_bad_key_flag_is_refused_and_a_bad_answer_is_asked_again(self):
        with self.assertRaisesRegex(SystemExit, "isn't a key prefix"):
            self.init("--yes", "--key", "pln", "a-plans")
        self.assertFalse((self.top / "a-plans").exists())
        answers = iter(["", "pln", "AB", ""])
        with contextlib.redirect_stdout(io.StringIO()) as told:
            self.init("b-plans", input=lambda q: next(answers), tty=True)
        self.assertEqual(self.config(self.top / "b-plans")["key"], "AB")
        self.assertIn("isn't a key prefix", told.getvalue())

    def test_the_port_skips_ports_in_use_and_ports_the_stores_beside_it_name(self):
        (self.top / "one").mkdir()
        (self.top / "one" / "board.json").write_text('{"port": 7788}')
        (self.top / "two").mkdir()
        (self.top / "two" / "board.json").write_text("{}")  # no port: 7788 too
        (self.top / "three").mkdir()
        (self.top / "three" / "board.json").write_text('{"port": 7790}')
        self.init("--yes", "new", bindable=lambda port: port != 7789)
        self.assertEqual(self.config(self.top / "new")["port"], 7791)

    def test_the_trunk_is_origins_default_branch_else_the_current_one(self):
        origin = self.app("origin", branch="dev")
        clone = self.top / "clone"
        git(self.top, "clone", "-q", str(origin), str(clone))
        git(clone, "switch", "-qc", "feature")
        self.assertEqual(init.default_trunk(clone), "dev")
        self.assertEqual(init.default_trunk(self.app("solo", branch="work")), "work")

    def test_the_store_folder_is_named_for_the_name_given(self):
        app = self.app()
        self.init("--yes", "--name", "tracker", "--key", "TRK", cwd=app)
        self.assertEqual(self.config(self.top / "tracker")["name"], "tracker")
        self.assertFalse((self.top / "shop-plans").exists())
        with self.assertRaises(SystemExit):
            self.init("--yes", "--name", "a/b", cwd=app)

    def test_questions_show_their_defaults_and_take_the_answers(self):
        app = self.app()
        asked, answers = [], iter(["Tracker", "", "7799", "y", "copilot", "y"])
        with contextlib.redirect_stdout(io.StringIO()):
            self.init(cwd=app, input=lambda q: asked.append(q) or next(answers), tty=True)
        self.assertEqual(asked[:3], ["Name [shop]: ", "Key prefix [TRA]: ", "Port [7788]: "])
        self.assertEqual(asked[3], "Let agents pick up stories? [y/N]: ")
        self.assertRegex(asked[4], r"^Agent: claude .* copilot .* or agy .*: $")
        self.assertEqual(len(asked), 5)  # the hooks aren't asked about: they are part of the board
        self.assertTrue(any("new git worktree under the app's .worktrees/" in line and "auto by default" in line for line in self.said))
        cfg = self.config(self.top / "Tracker")
        self.assertEqual((cfg["name"], cfg["key"], cfg["port"], cfg["pickup"], cfg["agent"]), ("Tracker", "TRA", 7799, True, "copilot"))
        store = self.top / "Tracker"
        self.assertIn(init.HOOK_MARK, (store / ".claude" / "settings.json").read_text())
        local = app / ".claude" / "settings.local.json"
        self.assertTrue(local.is_symlink())
        self.assertEqual(local.resolve(), store / ".claude" / "settings.json")
        self.assertFalse((app / ".claude" / "settings.json").exists())
        self.assertIn(".claude/settings.local.json\n", (app / ".gitignore").read_text())
        self.assertEqual(git(store, "status", "--porcelain"), "")  # the hooks are committed with the store
        self.assertEqual(git(app, "status", "--porcelain"), "?? .gitignore\n?? AGENTS.md\n?? CLAUDE.md")  # the link is ignored

    def test_nothing_is_asked_without_a_terminal_or_with_yes(self):
        app = self.app()
        self.init(cwd=app)  # no_input fails the test if anything is asked
        self.init("--yes", str(self.top / "other"), cwd=app, tty=True)
        self.assertFalse(self.config(self.top / "shop-plans")["pickup"])
        self.assertTrue(init.has_hooks(self.top / "shop-plans" / ".claude" / "settings.json"))  # not asked: part of the board

    def test_flags_are_not_asked_again(self):
        app = self.app()
        self.init("--name", "n", "--key", "K", "--port", "7001", "--pickup", "--agent", "copilot", "--no-hooks", cwd=app, tty=True)
        self.assertEqual((self.config(self.top / "n")["pickup"], self.config(self.top / "n")["agent"]), (True, "copilot"))

    def test_the_agent_is_claude_unless_only_copilot_is_on_path(self):
        app = self.app()
        self.init("--yes", "--pickup", cwd=app, which=lambda name: "/bin/copilot" if name == "copilot" else None, why_off=lambda st: None)
        self.assertEqual(self.config(self.top / "shop-plans")["agent"], "copilot")
        self.assertTrue(any("run `copilot` once in" in line for line in self.said))
        agent.use("claude")

    def test_the_agent_is_agy_when_only_agy_is_on_path(self):
        app = self.app()
        self.init("--yes", "--pickup", cwd=app, which=lambda name: "/bin/agy" if name == "agy" else None, why_off=lambda st: None)
        self.assertEqual(self.config(self.top / "shop-plans")["agent"], "agy")
        self.assertTrue(any("run `agy` once in" in line for line in self.said))
        agent.use("claude")

    def test_init_says_why_pickup_cannot_run_when_it_is_switched_on(self):
        app = self.app()
        self.init("--yes", "--pickup", cwd=app, why_off=lambda st: "Pickup is off: `claude` isn't on your PATH.")
        self.assertEqual(self.said[-1], "Pickup is off: `claude` isn't on your PATH.")
        self.assertTrue(any(line.startswith("Next: kandalf serve") and str(app) in line for line in self.said))

    def test_something_already_at_docs_plans_is_left_where_it_is(self):
        app = self.app()
        (app / "docs" / "plans").mkdir(parents=True)
        (app / ".gitignore").write_text("/docs/\n")
        self.init("--yes", cwd=app)
        self.assertTrue((app / "docs" / "plans").is_dir() and not (app / "docs" / "plans").is_symlink())
        self.assertTrue(any("left as it is" in line for line in self.said))
        self.assertEqual((app / ".gitignore").read_text(), "/docs/\n.worktrees/\n.claude/settings.local.json\n")  # docs/ covers the link already
        self.assertTrue(any(line.startswith("Next: kandalf serve --dir") for line in self.said))

    def test_a_gitignore_line_covers_docs_plans_when_it_names_it_or_docs(self):
        for text in ("docs/plans", "/docs/plans/", "docs/", "docs", "docs/*", "x\n  docs/plans  \n"):
            self.assertTrue(init.covers(text, "docs/plans"), text)
        for text in ("", "# docs/plans", "!docs/plans", "docs/plans-old", "plans", "other/docs/plans"):
            self.assertFalse(init.covers(text, "docs/plans"), text)

    def test_without_a_git_identity_the_store_is_made_but_not_committed(self):
        self.init("--yes", "lone", identity=lambda repo: False)
        self.assertEqual(stores.git_root(self.top / "lone"), self.top / "lone")
        self.assertNotEqual(subprocess.run(["git", "-C", str(self.top / "lone"), "rev-parse", "HEAD"],
                                           capture_output=True).returncode, 0)
        self.assertTrue(any("not committed yet" in line for line in self.said))

    def test_the_example_is_a_master_with_sub_stories_a_related_tile_and_a_done_one_and_passes_the_check(self):
        app = self.app()
        self.init("--yes", "--example", "--key", "EX", cwd=app)
        store = self.top / "shop-plans"
        for b in ("plans.md", "backlog.md"):
            self.assertTrue(round_trips(store / b), b)
        plans, backlog = (store / "plans.md").read_text(), (store / "backlog.md").read_text()
        columns = dict(re.findall(r"^## (.+)\n((?:(?!^## ).*\n?)*)", plans, re.M))
        self.assertNotIn("- [", columns["In flight"])
        self.assertEqual(columns["Done"].count("- [x] EX-"), 1)
        todo = columns["To do"]
        self.assertEqual(len(re.findall(r"^  Part of EX-01\.", todo, re.M)), 2)
        self.assertGreaterEqual(len(re.findall(r"^  - \[ \] ", todo, re.M)), 4)
        self.assertRegex(todo, re.compile(r"^- \[ \] EX-02 ~2h ", re.M))
        doc = f"active/{TODAY.isoformat()}-dark-mode.md"
        self.assertTrue((store / doc).is_file())
        self.assertRegex(todo, r"EX-01 ~1d \*\*Dark mode\.\*\*\n  .*\]\(" + re.escape(doc) + r"\)")  # linked from the master tile
        self.assertRegex(columns["Bugs"], r"EX-0[1-3]\b")  # a related tile
        self.assertEqual(len(re.findall(r"^- \[ \] EX-", backlog, re.M)), 2)
        self.assertRegex(backlog, r"## Feature\n\n- \[ \] EX-06")
        self.assertRegex(backlog, r"## Change\n\n- \[ \] EX-07")
        self.assertNotIn("PLN-", plans + backlog + (store / doc).read_text())
        self.assertEqual(git(store, "status", "--porcelain"), "")

    def test_the_hooks_block_is_one_source_for_init_and_board_hooks(self):
        self.assertEqual(json.loads(init.hooks_text()), init.HOOKS)
        with contextlib.redirect_stdout(io.StringIO()) as printed, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["hooks"]), 0)
        self.assertEqual(json.loads(printed.getvalue()), init.HOOKS)


class Hooks(Setup):
    def settings(self, text=None) -> Path:
        f = self.top / ".claude" / "settings.json"
        if text is not None:
            f.parent.mkdir(exist_ok=True)
            f.write_text(text)
        return f

    def test_the_hooks_are_merged_into_a_settings_file_keeping_everything_else(self):
        mine = {"type": "command", "command": "echo mine"}
        f = self.settings(json.dumps({"model": "opus", "hooks": {"Stop": [], "PostToolUse": [{"matcher": "Bash", "hooks": [mine]}]}}))
        self.assertIn("added", init.merge_hooks(f))
        got = json.loads(f.read_text())
        self.assertEqual(got["model"], "opus")
        self.assertEqual(got["hooks"]["Stop"], [])
        self.assertEqual(got["hooks"]["PostToolUse"], [{"matcher": "Bash", "hooks": [mine]}] + init.HOOKS["PostToolUse"])
        before = f.read_text()
        self.assertIn("already", init.merge_hooks(f))
        self.assertEqual(f.read_text(), before)

    def test_a_missing_settings_file_is_made_with_just_the_hooks(self):
        init.merge_hooks(self.settings())
        self.assertEqual(json.loads(self.settings().read_text()), {"hooks": init.HOOKS})

    def test_settings_that_are_not_json_are_left_alone_and_the_block_is_printed(self):
        f = self.settings("{ not json")
        said = init.merge_hooks(f)
        self.assertEqual(f.read_text(), "{ not json")
        self.assertIn("left alone", said)
        self.assertIn(init.hooks_text(), said)

    def test_the_old_board_hooks_in_their_other_form_are_spotted_and_replaced(self):
        old = [{"type": "command", "command": 'b="$(command -v board || echo "$HOME/.local/bin/board")"; [ -x "$b" ] && "$b" hook autocommit 2>/dev/null || true'},
               {"type": "command", "command": 'b="$(command -v board || echo "$HOME/.local/bin/board")"; [ -x "$b" ] || exit 0; "$b" hook check'}]
        f = self.settings(json.dumps({"hooks": {"PostToolUse": [{"matcher": "Edit|Write|MultiEdit", "hooks": old}]}}))
        self.assertTrue(init.has_old_hooks(f))
        self.assertIn("replaced", init.merge_hooks(f))
        self.assertFalse(init.has_old_hooks(f))
        self.assertEqual(json.loads(f.read_text())["hooks"]["PostToolUse"], init.HOOKS["PostToolUse"])

    def test_the_old_board_hooks_are_replaced_and_the_rest_kept(self):
        mine = {"type": "command", "command": "echo mine"}
        old = [{"type": "command", "command": "command -v board >/dev/null && board hook autocommit 2>/dev/null || true"},
               {"type": "command", "command": "command -v board >/dev/null || exit 0; board hook check"}]
        f = self.settings(json.dumps({"hooks": {"PostToolUse": [{"matcher": "Edit|Write", "hooks": old},
                                                                 {"matcher": "Bash", "hooks": [mine, old[0]]}]}}))
        self.assertIn("replaced", init.merge_hooks(f))
        self.assertEqual(json.loads(f.read_text())["hooks"]["PostToolUse"],
                         [{"matcher": "Bash", "hooks": [mine]}] + init.HOOKS["PostToolUse"])

    def test_an_apps_own_settings_local_json_gets_the_hooks_merged_in(self):
        app, hooks = self.app(), self.settings(json.dumps({"hooks": init.HOOKS}))
        local = app / ".claude" / "settings.local.json"
        local.parent.mkdir()
        local.write_text('{"permissions": {}}')
        said = init.link_hooks(app, hooks)
        merged = json.loads(local.read_text())
        self.assertEqual(merged["permissions"], {})  # the rest kept
        self.assertEqual(merged["hooks"], init.HOOKS)
        self.assertIn("added", said[0])
        self.assertEqual(init.link_hooks(app, hooks), [f"hooks: {local} has them already"])

    def test_linking_twice_changes_nothing(self):
        app, hooks = self.app(), self.settings(json.dumps({"hooks": init.HOOKS}))
        self.assertIn("linked", init.link_hooks(app, hooks)[0])
        ignored = (app / ".gitignore").read_text()
        self.assertEqual(init.link_hooks(app, hooks), [f"hooks: {app / '.claude' / 'settings.local.json'} already links them"])
        self.assertEqual((app / ".gitignore").read_text(), ignored)

    def test_a_store_gets_the_hooks_without_being_asked_and_keeps_a_no_hooks_choice(self):
        self.init("--yes", cwd=self.app())
        st = stores.Store(self.top / "shop-plans")
        self.assertTrue(init.has_hooks(st.hooks_file))
        self.assertTrue(st.hooks)
        self.assertTrue(init.links_hooks(st.app, st.hooks_file))
        self.init("--yes", "--no-hooks", cwd=self.app("lone"))
        st = stores.Store(self.top / "lone-plans")
        self.assertFalse(st.hooks_file.exists())
        self.assertIs(self.config(st.dir)["hooks"], False)
        self.assertFalse(st.hooks)

    def test_serve_adds_the_hooks_to_a_store_without_them_unless_it_said_no(self):
        app = self.app()
        self.init("--yes", "--no-hooks", cwd=app)
        st = stores.Store(self.top / "shop-plans")
        self.assertEqual(serve.ensure_hooks(st), [])  # it said no
        cfg = self.config(st.dir)
        del cfg["hooks"]
        (st.dir / "board.json").write_text(json.dumps(cfg))
        st = stores.Store(st.dir)  # a store from before the hooks were standard
        said = serve.ensure_hooks(st)
        self.assertTrue(init.has_hooks(st.hooks_file))
        self.assertTrue(any("committed" in line for line in said))
        self.assertEqual(serve.ensure_hooks(st), [])  # there already

    def test_board_hooks_add_puts_them_in_a_store_made_without_them_commits_and_links_them(self):
        app = self.app()
        self.init("--yes", "--no-hooks", cwd=app)
        st = stores.Store(self.top / "shop-plans")
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(cli.main(["hooks", "--add", "--dir", str(st.dir)]), 0)
        self.assertIn("committed", printed.getvalue())
        self.assertEqual(json.loads(st.hooks_file.read_text()), {"hooks": init.HOOKS})
        self.assertEqual(git(st.dir, "status", "--porcelain"), "")
        self.assertEqual((app / ".claude" / "settings.local.json").resolve(), st.hooks_file)


class Doctor(Setup):
    def made(self, *args) -> stores.Store:
        app = self.app()
        self.init("--yes", *args, cwd=app)
        return stores.Store(self.top / "shop-plans")

    def checks(self, st, **probes):
        probes.setdefault("which", lambda name: "/bin/" + name)
        probes.setdefault("version", (3, 12, 0))
        probes.setdefault("why_off", lambda st: OFF)
        probes.setdefault("bindable", lambda port: True)
        return init.checks(st, **probes)

    def marks(self, found) -> dict:
        return {text: mark for mark, text in found}

    def test_a_new_store_beside_its_app_has_no_problem_and_says_what_is_merely_off(self):
        found = self.checks(self.made())
        self.assertEqual([t for m, t in found if m == init.BAD], [])
        info = " | ".join(t for m, t in found if m == init.INFO)
        self.assertIn("no upstream", info)
        self.assertIn(OFF, info)
        self.assertTrue(any(m == init.OK and "hooks in" in t and "linked into the app" in t for m, t in found))
        self.assertTrue(any(m == init.OK and "links the store" in t for m, t in found))
        self.assertTrue(any(m == init.OK and "trunk dev" in t for m, t in found))
        said = []
        self.assertEqual(init.doctor(self.made_again(), out=said.append, which=lambda n: "/x", version=(3, 12),
                                     why_off=lambda st: OFF, bindable=lambda p: True), 0)
        self.assertTrue(all(line[:4] in ("ok  ", "!!  ", "--  ") for line in said), said)

    def test_doctor_says_sub_story_tests_is_retired(self):
        st = self.made()
        cfg = self.config(st.dir)
        (st.dir / "board.json").write_text(json.dumps({**cfg, "subStoryTests": "all"}))
        said = " | ".join(t for m, t in self.checks(stores.Store(st.dir)))
        self.assertIn("subStoryTests is retired", said)
        self.assertIn("fullSuiteOnMerge", said)

    def made_again(self):
        return stores.Store(self.top / "shop-plans")

    def test_a_wrong_app_a_missing_trunk_and_a_broken_link_are_problems(self):
        st = self.made("--hooks")
        self.assertTrue(any(m == init.OK and "hooks in" in t and "linked into the app" in t for m, t in self.checks(st)))
        (self.top / "shop" / ".claude" / "settings.local.json").unlink()
        self.assertTrue(any(m == init.INFO and "pickup sessions get them" in t for m, t in self.checks(st)))
        (self.top / "shop" / ".claude" / "settings.json").parent.mkdir(exist_ok=True)
        (self.top / "shop" / ".claude" / "settings.json").write_text('{"x": "command -v board || exit 0; board hook check"}')
        self.assertTrue(any(m == init.INFO and "old hooks" in t for m, t in self.checks(st)))
        (self.top / "shop" / ".claude" / "settings.json").unlink()
        cfg = self.config(st.dir)
        (st.dir / "board.json").write_text(json.dumps({**cfg, "trunk": "nope"}))
        (self.top / "shop" / "docs" / "plans").unlink()
        bad = [t for m, t in self.checks(stores.Store(st.dir)) if m == init.BAD]
        self.assertEqual(len(bad), 2, bad)
        self.assertRegex(bad[0], "trunk nope")
        self.assertRegex(bad[1], "isn't there: ln -s")
        (st.dir / "board.json").write_text(json.dumps({**cfg, "app": "../gone"}))
        self.assertEqual(init.doctor(stores.Store(st.dir), out=lambda s: None, which=lambda n: "/x",
                                     version=(3, 12), why_off=lambda st: OFF, bindable=lambda p: True), 1)

    def test_pickup_is_a_problem_only_when_it_is_switched_on_and_cannot_run(self):
        st = self.made("--pickup")
        self.assertEqual(self.marks(self.checks(st, why_off=lambda s: "Pickup is off: no claude."))["Pickup is off: no claude."], init.BAD)
        self.assertTrue(any(m == init.OK and t.startswith("pickup on") for m, t in self.checks(st, why_off=lambda s: None)))
        alone = stores.Store(self.top / "shop-plans")
        alone.app = None
        self.assertEqual(self.marks(self.checks(alone, why_off=lambda s: "no app."))["no app."], init.INFO)

    def test_old_python_and_no_git_are_problems_and_no_node_is_not(self):
        st = self.made()
        found = self.checks(st, version=(3, 9, 1), which=lambda name: None if name in ("git", "node") else "/bin/" + name)
        marks = {t.split(":")[0].split(" ")[0]: m for m, t in found}
        self.assertEqual((marks["Python"], marks["git"], marks["node"]), (init.BAD, init.BAD, init.INFO))

    def test_a_port_in_use_is_noted_as_maybe_this_board(self):
        found = self.marks(self.checks(self.made(), bindable=lambda port: False))
        self.assertEqual(found["port 7788 is in use: maybe this board is already running"], init.INFO)

    def test_a_store_that_does_not_push_or_is_no_repo_says_so(self):
        loose = self.top / "loose"
        loose.mkdir()
        (loose / "plans.md").write_text("# Plans\n")
        (loose / "board.json").write_text('{"push": false}')
        found = self.checks(stores.Store(loose))
        self.assertTrue(any(m == init.BAD and "isn't a git repo" in t for m, t in found))
        git(loose, "init", "-q")
        found = self.checks(stores.Store(loose))
        self.assertTrue(any(m == init.INFO and '"push": false' in t for m, t in found))


class AgentCommand(Setup):
    def made(self):
        self.init("--yes", "--pickup", cwd=self.app())
        st = stores.Store(self.top / "shop-plans")
        self.addCleanup(agent.use, "claude")
        return st

    def jobs(self, st, *jobs):
        pickup.save(st, {"inflight": [j["key"] for j in jobs], "jobs": list(jobs)})

    def switch(self, st, name, **kw):
        with mock.patch("kandalf.gitlog.record"):
            return init.set_agent(st, name, **kw)

    def test_switching_sets_the_agent_and_drops_the_old_model(self):
        st = self.made()
        cfg = self.config(st.dir)
        cfg["model"] = "sonnet"
        (st.dir / "board.json").write_text(json.dumps(cfg))
        said = self.switch(stores.Store(st.dir), "copilot")
        self.assertIn("model sonnet (for claude) dropped", said[0])
        again = stores.Store(st.dir)
        self.assertEqual((again.agent, again.model), ("copilot", "auto"))
        self.assertEqual(self.switch(again, "copilot"), ["the agent is copilot already"])

    def test_an_agent_switch_leaves_a_pausing_job_lost_and_not_pausing(self):
        st = self.made()
        self.jobs(st, {"key": "SHO-1", "state": "started", "id": "ab12cd34", "session": "full-id", "pausing": True})
        self.switch(st, "copilot", force=True, stop=lambda which, sid: None)
        job = pickup.load(st)["jobs"][0]
        self.assertEqual(job["state"], "lost")
        self.assertNotIn("pausing", job)

    def test_an_agent_switch_writes_board_json_while_the_ledger_is_held(self):
        st = self.made()
        held = []
        real = init.write_config

        def write(*a, **kw):
            held.append(str(jobs_module.Ledger(st).path.with_suffix(".lock").resolve()) in jobs_module._flocks)
            return real(*a, **kw)
        with mock.patch.object(init, "write_config", write):
            self.switch(st, "copilot")
        self.assertEqual(held, [True])

    def test_a_running_session_stops_a_switch_unless_forced_and_restart_then_starts_afresh(self):
        st = self.made()
        self.jobs(st, {"key": "SHO-1", "state": "started", "id": "ab12cd34", "session": "full-id"},
                  {"key": "SHO-2", "state": "lost", "id": "cd34ef56", "session": "other-id"})
        with self.assertRaises(SystemExit) as no:
            self.switch(st, "copilot")
        self.assertIn("SHO-1 has a claude session", str(no.exception))
        self.assertEqual(self.config(st.dir)["agent"], "claude")
        stopped = []
        self.switch(st, "copilot", force=True, stop=lambda which, sid: stopped.append((which, sid)))
        self.assertEqual(stopped, [("claude", "ab12cd34")])
        jobs = pickup.load(st)["jobs"]
        self.assertEqual([(j["state"], "session" in j) for j in jobs], [("lost", False), ("lost", False)])


class RepoCommands(Setup):
    def setUp(self):
        super().setUp()
        rec = mock.patch("kandalf.gitlog.record")  # no committer process for a temp store
        self.record = rec.start()
        self.addCleanup(rec.stop)

    def made(self, *args):
        app = self.app()
        self.init("--yes", *args, cwd=app)
        return stores.Store(self.top / "shop-plans")

    def add(self, st, *args):
        with contextlib.redirect_stdout(io.StringIO()):
            return init.add_repo(st, *args)

    def test_repo_add_names_it_links_the_store_and_ignores_the_links(self):
        st, api = self.made(), self.app("api", "dev")
        said = self.add(st, api)
        self.assertEqual(self.config(st.dir)["repos"], {"api": "../api"})
        self.assertEqual((api / "docs" / "plans").resolve(), st.dir)
        ignored = (api / ".gitignore").read_text()
        self.assertIn("docs/plans", ignored)
        self.assertIn(".worktrees/", ignored)
        self.assertIn("trust prompt", said[-1])
        self.assertEqual(self.record.call_args.args[1], [st.dir / "board.json"])
        self.assertIn(str(api), said[-1])
        self.assertNotIn("restart", said[-1])

    def test_repo_add_tells_agents_how_to_file_a_story_once(self):
        st, api, web = self.made(), self.app("api", "dev"), self.app("web", "dev")
        (web / "CLAUDE.md").write_text("# Web\n")
        self.add(st, api)
        self.add(st, web)
        self.assertEqual((api / "CLAUDE.md").read_text(), "@AGENTS.md\n")
        for f in (api / "AGENTS.md", web / "CLAUDE.md"):
            text = f.read_text()
            self.assertIn("add a story to kandalf", text)
            self.assertIn("add to board", text)
            self.assertIn("docs/plans/backlog.md", text)
        self.assertFalse((web / "AGENTS.md").exists())
        init.agent_notes(api, "plans")
        self.assertEqual((api / "AGENTS.md").read_text().count(init.AGENT_NOTES_MARK), 1)

    def test_uninstall_removes_the_link_from_every_repo_and_leaves_the_store(self):
        st, api = self.made(), self.app("api", "dev")
        self.add(st, api)
        (api / "docs" / "notes").mkdir()
        st = init.stores.Store(st.dir)
        said = init.uninstall(st)
        for r in st.repos:
            self.assertFalse(os.path.lexists(r.path / "docs" / "plans"))
        self.assertTrue((api / "docs" / "notes").is_dir())
        self.assertTrue((st.dir / "board.json").is_file())
        self.assertEqual(len([s for s in said if "removed" in s]), len(st.repos))
        self.assertTrue(init.uninstall(st))  # again: nothing to remove, no error

    def test_repo_add_uses_the_repo_root_of_a_path_inside_it(self):
        st, api = self.made(), self.app("api", "dev")
        (api / "src").mkdir()
        self.add(st, api / "src")
        self.assertEqual(self.config(st.dir)["repos"], {"api": "../api"})

    def test_repo_add_records_a_trunk_that_differs_from_the_store_s(self):
        st, web = self.made(), self.app("web", "main")
        self.add(st, web, "site")
        self.assertEqual(self.config(st.dir)["repos"], {"site": {"path": "../web", "trunk": "main"}})
        self.assertEqual(stores.Store(st.dir).repo("site").trunk, "main")
        self.add(st, self.app("api", "dev"), None, "dev")
        self.assertEqual(self.config(st.dir)["repos"]["api"], "../api")

    def test_repo_add_links_the_hooks_when_the_store_has_them(self):
        st, api = self.made("--hooks"), self.app("api", "dev")
        self.add(st, api)
        self.assertEqual((api / ".claude" / "settings.local.json").resolve(), st.hooks_file)
        self.init("--yes", "--no-hooks", cwd=self.app("lone"))
        st2 = stores.Store(self.top / "lone-plans")
        other = self.app("other", "dev")
        self.add(st2, other)
        self.assertFalse((other / ".claude").exists())

    def test_repo_add_refuses_a_clash_or_a_folder_that_is_not_a_repo(self):
        st, api = self.made(), self.app("api", "dev")
        self.add(st, api)
        before = (st.dir / "board.json").read_text()
        with self.assertRaises(SystemExit) as e:
            self.add(stores.Store(st.dir), api)
        self.assertIn("already", str(e.exception))
        with self.assertRaises(SystemExit):
            self.add(stores.Store(st.dir), self.app("x", "dev"), "API")
        with self.assertRaises(SystemExit) as e:
            self.add(stores.Store(st.dir), self.app("y", "dev"), "SHOP")
        self.assertIn("main", str(e.exception))
        with self.assertRaises(SystemExit):
            self.add(stores.Store(st.dir), self.app("z", "dev"), "bad name")
        bare = self.top / "bare"
        bare.mkdir()
        with self.assertRaises(SystemExit) as e:
            self.add(stores.Store(st.dir), bare)
        self.assertIn("isn't in a git repo", str(e.exception))
        self.assertEqual((st.dir / "board.json").read_text(), before)

    def test_repo_add_refuses_the_store_itself_and_a_repo_already_in_it(self):
        st, api = self.made(), self.app("api", "dev")
        self.add(st, api)
        before = (st.dir / "board.json").read_text()
        for path, name, said in ((st.dir, None, "plans store itself"), (self.top / "shop", "again", "already in the store as shop"),
                                 (api, "other", "already in the store as api")):
            with self.assertRaises(SystemExit) as e:
                self.add(stores.Store(st.dir), path, name)
            self.assertIn(said, str(e.exception))
        self.assertEqual((st.dir / "board.json").read_text(), before)
        self.assertFalse((st.dir / "docs").exists())
        self.assertFalse((st.dir / ".gitignore").exists())

    def test_repo_add_keeps_board_json_s_other_keys_in_order(self):
        st = self.made()
        cfg = self.config(st.dir)
        (st.dir / "board.json").write_text(json.dumps({**cfg, "repos": {"old": "../old"}, "tidy": False}, indent=2) + "\n")
        self.add(stores.Store(st.dir), self.app("api", "dev"))
        after = self.config(st.dir)
        self.assertEqual(list(after), [*cfg, "repos", "tidy"])
        self.assertEqual(list(after["repos"]), ["old", "api"])
        self.assertEqual((st.dir / "board.json").read_text(), json.dumps(after, indent=2) + "\n")

    def test_repo_rm_takes_it_out_and_leaves_its_links(self):
        st, api = self.made(), self.app("api", "dev")
        self.add(st, api)
        said = init.remove_repo(stores.Store(st.dir), "API")
        self.assertIn("API", said[0].upper())
        self.assertNotIn("repos", self.config(st.dir))
        self.assertTrue((api / "docs" / "plans").is_symlink())
        with self.assertRaises(SystemExit):
            init.remove_repo(stores.Store(st.dir), "api")

    def test_repo_rm_refuses_the_main_repo(self):
        st = self.made()
        before = (st.dir / "board.json").read_text()
        with self.assertRaises(SystemExit) as e:
            init.remove_repo(st, "shop")
        self.assertIn("main", str(e.exception))
        self.assertEqual((st.dir / "board.json").read_text(), before)

    def test_doctor_checks_each_repo_and_reports_a_repo_problem(self):
        st, api = self.made(), self.app("api", "dev")
        self.add(st, api)
        probes = dict(which=lambda n: "/x", version=(3, 12), why_off=lambda s: OFF, bindable=lambda p: True)
        found = init.checks(stores.Store(st.dir), **probes)
        self.assertTrue(any(m == init.OK and f"repo api {api}" in t for m, t in found), found)
        self.assertEqual([t for m, t in found if m == init.BAD], [])
        self.assertTrue(any(m == init.INFO and agent.trust(api) in t for m, t in found), found)
        (api / "docs" / "plans").unlink()
        cfg = self.config(st.dir)
        (st.dir / "board.json").write_text(json.dumps({**cfg, "repos": {**cfg["repos"], "web": "../gone", "bad name": "../x"}}))
        found = init.checks(stores.Store(st.dir), **probes)
        bad = " | ".join(t for m, t in found if m == init.BAD)
        self.assertIn("isn't there: ln -s", bad)
        self.assertIn("repo web", bad)
        self.assertIn('"bad name"', bad)

    def test_where_lists_the_repos(self):
        st, api = self.made(), self.app("api", "dev")
        self.add(st, api)
        self.assertEqual(init.repo_lines(stores.Store(st.dir)), ["shop (main) ../shop @ dev", "api ../api @ dev"])
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(cli.main(["where", "--dir", str(st.dir)]), 0)
        lines = printed.getvalue().splitlines()
        self.assertEqual(lines[3:5], ["  repo shop (main) ../shop @ dev", "  repo api ../api @ dev"])

    def test_the_repo_command_adds_lists_and_removes(self):
        st, api = self.made(), self.app("api", "dev")
        run = lambda *a: cli.main(["repo", *a, "--dir", str(st.dir)])  # noqa: E731
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(run("add", str(api), "--name", "backend", "--trunk", "main"), 0)
            self.assertEqual(run("list"), 0)
            self.assertEqual(run("rm", "backend"), 0)
        self.assertIn("backend ../api @ main", printed.getvalue())
        self.assertEqual(stores.Store(st.dir).repo("backend"), None)


class Opening(unittest.TestCase):
    def test_the_browser_opens_only_in_a_terminal_without_no_open_or_board_no_open(self):
        tty = lambda: True  # noqa: E731
        self.assertTrue(serve.should_open(False, env={}, isatty=tty))
        self.assertFalse(serve.should_open(True, env={}, isatty=tty))
        self.assertFalse(serve.should_open(False, env={"BOARD_NO_OPEN": "1"}, isatty=tty))
        self.assertFalse(serve.should_open(False, env={}, isatty=lambda: False))

    def test_serve_opens_the_board_once_it_is_listening(self):
        folder = Path(tempfile.mkdtemp())
        (folder / "plans.md").write_text("# Plans\n")
        (folder / "board.json").write_text('{"port": 7801}')
        steps = []

        class Server:
            def __init__(self, address, handler):
                steps.append(("listen", address))

            def serve_forever(self):
                steps.append(("serve",))

        class Terminal(io.StringIO):
            def isatty(self):
                return True

        env = {k: v for k, v in os.environ.items() if k != "BOARD_NO_OPEN"}
        with mock.patch.object(serve, "ThreadingHTTPServer", Server), mock.patch.object(serve.threading, "Thread"), \
                mock.patch.object(serve.webbrowser, "open", lambda url: steps.append(("open", url))), \
                mock.patch.object(serve.Handler, "store", None, create=True), \
                mock.patch.dict(os.environ, env, clear=True), mock.patch("sys.stdout", Terminal()):
            serve.main(["--dir", str(folder)])
            serve.main(["--dir", str(folder), "--no-open"])
        self.assertEqual(steps, [("listen", ("127.0.0.1", 7801)), ("open", "http://127.0.0.1:7801/"), ("serve",),
                                 ("listen", ("127.0.0.1", 7801)), ("serve",)])


if __name__ == "__main__":
    unittest.main()
