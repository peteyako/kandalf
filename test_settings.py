import http.client
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from kandalf import agent, drafts, jobs, pickup, serve, settings
from kandalf.store import Store

REAL_TICK = pickup.tick  # the Watch tests fake it; one look needs the real one
REAL_LOOK = drafts.look  # the same for the drafts' look


class Reload(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.file = Path(self.tmp.name) / "board.json"

    def write(self, text):
        self.file.write_text(text if isinstance(text, str) else json.dumps(text), encoding="utf-8")

    def test_a_store_reads_board_json_again_in_place(self):
        self.write({"parallel": 1})
        s = Store(self.tmp.name)
        self.write({"parallel": 3, "fullSuiteOnMerge": True})
        self.assertTrue(s.reload())
        self.assertEqual(s.parallel, 3)
        self.assertIs(s.full_suite_on_merge, True)

    def test_a_board_json_that_does_not_parse_keeps_the_settings_in_force(self):
        self.write({"parallel": 2})
        s = Store(self.tmp.name)
        self.write('{"parallel": ')
        self.assertFalse(s.reload())
        self.assertEqual(s.parallel, 2)
        self.assertTrue(s.config_problem.startswith("board.json doesn't parse"))
        self.write({"parallel": 4})
        self.assertTrue(s.reload())
        self.assertIsNone(s.config_problem)
        self.assertEqual(s.parallel, 4)

    def test_a_board_json_with_a_bad_value_keeps_the_settings_in_force(self):
        self.write({"key": "AAA", "parallel": 2})
        s = Store(self.tmp.name)
        self.write({"name": "b", "key": "BBB", "parallel": "x"})
        self.assertFalse(s.reload())
        self.assertEqual((s.key, s.parallel), ("AAA", 2))
        self.assertTrue(s.config_problem.startswith("board.json"))
        self.write({"key": "BBB", "parallel": 3})
        self.assertTrue(s.reload())
        self.assertEqual((s.key, s.parallel, s.config_problem), ("BBB", 3, None))

    def test_a_board_json_that_is_not_an_object_keeps_the_settings_in_force(self):
        self.write({"key": "AAA"})
        s = Store(self.tmp.name)
        self.write("[]")
        self.assertFalse(s.reload())
        self.assertEqual(s.key, "AAA")
        self.assertTrue(s.config_problem.startswith("board.json"))

    def test_reload_if_changed_reloads_only_after_a_write(self):
        self.write({"parallel": 1})
        s = Store(self.tmp.name)
        self.assertFalse(s.reload_if_changed())
        self.write({"parallel": 5})
        st = self.file.stat()
        os.utime(self.file, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        self.assertTrue(s.reload_if_changed())
        self.assertEqual(s.parallel, 5)
        self.assertFalse(s.reload_if_changed())


def git(path, *args):
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


class Settings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name) / "plans"
        self.dir.mkdir()
        self.file = self.dir / "board.json"

    def store(self, cfg):
        self.file.write_text(json.dumps(cfg), encoding="utf-8")
        return Store(self.dir)

    def on_disk(self):
        return json.loads(self.file.read_text(encoding="utf-8"))

    def test_every_setting_and_store_fact_says_what_it_means(self):
        v = settings.view(self.store({}))
        for k, f in v["fields"].items():
            self.assertGreater(len(f.get("help", "")), 20, k)
        self.assertEqual(set(v["readonlyHelp"]), set(v["readonly"]))
        self.assertTrue(all(len(h) > 10 for h in v["readonlyHelp"].values()))

    def test_the_view_says_what_is_set_what_is_in_force_and_the_defaults(self):
        v = settings.view(self.store({"parallel": 3, "name": "X"}))
        self.assertEqual(v["set"], {"parallel": 3})
        self.assertEqual(v["effective"]["parallel"], 3)
        self.assertEqual(v["defaults"]["parallel"], 1)
        self.assertIs(v["defaults"]["fullSuiteOnMerge"], False)
        self.assertEqual(v["readonly"]["name"], "X")
        self.assertEqual(list(v["fields"])[0], "pickup")

    def test_a_change_is_written_with_every_other_key_kept(self):
        s = self.store({"name": "X", "custom": {"a": 1}, "parallel": 1})
        lines = settings.apply(s, {"parallel": 2, "fullSuiteOnMerge": True})
        self.assertEqual(self.on_disk(), {"name": "X", "custom": {"a": 1}, "parallel": 2, "fullSuiteOnMerge": True})
        self.assertEqual(s.parallel, 2)
        self.assertEqual(len(lines), 2)

    def test_null_resets_a_setting_to_its_default(self):
        s = self.store({"parallel": 4})
        settings.apply(s, {"parallel": None})
        self.assertNotIn("parallel", self.on_disk())
        self.assertEqual(s.parallel, 1)

    def test_a_bad_change_writes_nothing(self):
        s = self.store({"parallel": 1})
        before = self.file.read_text(encoding="utf-8")
        for bad in ({"parallel": 0}, {"parallel": "2"}, {"parallel": True}, {"pickup": "false"},
                    {"permission": "yolo"}, {"port": 9}, {"model": "a b"}, {"model": ""},
                    {"parallel": 2, "bogus": 1}):
            with self.subTest(bad=bad):
                with self.assertRaises(settings.SettingsError):
                    settings.apply(s, bad)
                self.assertEqual(self.file.read_text(encoding="utf-8"), before)

    def test_the_trunk_must_be_a_branch_of_the_app(self):
        app = Path(self.tmp.name) / "app"
        app.mkdir()
        git(app, "init", "-q", "-b", "dev")
        (app / "f").write_text("x")
        git(app, "add", "f")
        git(app, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        s = self.store({"app": "../app"})
        with self.assertRaises(settings.SettingsError):
            settings.apply(s, {"trunk": "nope"})
        with self.assertRaises(settings.SettingsError):
            settings.apply(s, {"trunk": "-x"})
        self.assertNotIn("trunk", self.on_disk())
        settings.apply(s, {"trunk": "dev"})
        self.assertEqual(self.on_disk()["trunk"], "dev")

    def test_where_worktrees_go_is_a_setting_that_defaults_to_inside_the_app(self):
        v = settings.view(self.store({}))
        self.assertEqual((v["defaults"]["worktree"], v["effective"]["worktree"]), ("{app}/.worktrees/{key}",) * 2)
        self.assertNotIn("worktree", v["readonly"])
        s = self.store({})
        settings.apply(s, {"worktree": "{app}-{key}"})
        self.assertEqual((self.on_disk()["worktree"], s.worktree_pattern), ("{app}-{key}", "{app}-{key}"))
        settings.apply(s, {"worktree": None})
        self.assertNotIn("worktree", self.on_disk())
        self.assertEqual(s.worktree_pattern, "{app}/.worktrees/{key}")

    def test_a_worktree_place_pickup_could_not_use_is_refused(self):
        s = self.store({})
        for bad in ("{app}-x", "/tmp/{key}", "../../{key}", "~/{key}", "", 3):
            with self.subTest(bad=bad):
                with self.assertRaises(settings.SettingsError):
                    settings.apply(s, {"worktree": bad})
                self.assertNotIn("worktree", self.on_disk())

    def test_switching_the_agent_is_refused_while_a_session_is_live(self):
        s = self.store({"pickup": True})
        pickup.save(s, {"inflight": [], "jobs": [{"key": "PLN-01", "state": "started", "id": "s1"}]})
        with self.assertRaises(settings.SettingsError) as c:
            settings.apply(s, {"agent": "copilot", "parallel": 2})
        self.assertIn("has a claude session", str(c.exception))
        self.assertNotIn("parallel", self.on_disk())

    def test_switching_the_agent_keeps_the_other_changes(self):
        self.addCleanup(agent.use, "claude")
        s = self.store({"model": "opus"})
        settings.apply(s, {"agent": "copilot", "parallel": 2})
        cfg = self.on_disk()
        self.assertEqual((cfg["agent"], cfg["parallel"]), ("copilot", 2))
        self.assertNotIn("model", cfg)
        self.assertEqual(s.agent, "copilot")

    def test_an_agent_switch_says_what_kandalf_agent_says(self):
        self.addCleanup(agent.use, "claude")
        s = self.store({"model": "opus"})
        lines = settings.apply(s, {"agent": "copilot"})
        self.assertTrue(any("model opus (for claude) dropped" in line for line in lines))
        self.assertTrue(any("Model: lines" in line for line in lines))
        self.assertFalse(any("restart" in line for line in lines))

    def test_turning_pickup_off_says_that_sessions_at_work_carry_on(self):
        s = self.store({"pickup": True})
        lines = settings.apply(s, {"pickup": False})
        self.assertIn(settings.PICKUP_OFF, lines)
        self.assertIn("sessions at work carry on", settings.PICKUP_OFF)
        self.assertNotIn(settings.PICKUP_OFF, settings.apply(s, {"parallel": 2}))
        self.assertNotIn(settings.PICKUP_OFF, settings.apply(s, {"pickup": False}))

    def test_default_is_a_permission_mode_the_panel_accepts(self):
        s = self.store({})
        settings.apply(s, {"permission": "default"})
        self.assertEqual(self.on_disk()["permission"], "default")

    def test_a_board_json_that_does_not_parse_is_reported_not_crashed_on(self):
        s = self.store({"parallel": 2, "agent": "claude"})
        self.file.write_text('{"parallel": ', encoding="utf-8")
        v = settings.view(s)
        self.assertEqual(v["set"], {})
        self.assertTrue(v["problem"].startswith("board.json"))
        before = self.file.read_text(encoding="utf-8")
        for change in ({"parallel": 3}, {"agent": "copilot"}):
            with self.assertRaises(settings.SettingsError) as c:
                settings.apply(s, change)
            self.assertIn("doesn't parse", str(c.exception))
        self.assertEqual(self.file.read_text(encoding="utf-8"), before)

    def test_a_write_that_does_not_load_is_not_reported_as_done(self):
        s = self.store({"parallel": 1})
        self.file.write_text(json.dumps({"parallel": 1, "port": "abc"}), encoding="utf-8")
        with self.assertRaises(settings.SettingsError) as c:
            settings.apply(s, {"parallel": 2})
        self.assertIn("not in force", str(c.exception))
        self.assertEqual(s.parallel, 1)

    def test_the_problem_is_shown_only_when_pickup_is_on_and_cannot_run(self):
        self.assertIsNone(settings.view(self.store({"pickup": False}))["problem"])
        self.assertTrue(settings.view(self.store({"pickup": True}))["problem"])


def aged(path, seconds=10):
    """Put a file's modification time back, so the next write is seen as a change even where
    the clock is coarse."""
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns - seconds * 1_000_000_000))


class Route(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "board.json").write_text(json.dumps({"name": "X"}), encoding="utf-8")
        aged(self.dir / "board.json")
        self.handler = type("Handler", (serve.Handler,), {"store": Store(self.dir)})
        self.srv = serve.ThreadingHTTPServer(("127.0.0.1", 0), self.handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        p = mock.patch.object(pickup, "log")  # the console line each save gets
        p.start()
        self.addCleanup(p.stop)

    def ask(self, method, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=10)
        c.request(method, "/api/settings", body=json.dumps(body).encode() if body is not None else None,
                  headers={"Content-Type": "application/json"} if body is not None else {})
        r = c.getresponse()
        out = (r.status, json.loads(r.read()))
        c.close()
        return out

    def test_the_settings_route_saves_a_change_and_the_server_uses_it(self):
        status, got = self.ask("GET")
        self.assertEqual(status, 200)
        self.assertEqual(got["effective"]["parallel"], 1)
        status, saved = self.ask("POST", {"mtime": got["mtime"], "changes": {"parallel": 2}})
        self.assertEqual(status, 200, saved)
        self.assertEqual(self.handler.store.parallel, 2)
        self.assertEqual(saved["effective"]["parallel"], 2)
        self.assertEqual(saved["said"], ["parallel set to 2"])

    def test_a_save_against_an_old_copy_is_refused(self):
        _, got = self.ask("GET")
        self.assertEqual(self.ask("POST", {"mtime": got["mtime"], "changes": {"parallel": 2}})[0], 200)
        status, refused = self.ask("POST", {"mtime": got["mtime"], "changes": {"parallel": 3}})
        self.assertEqual(status, 409)
        self.assertEqual(refused["error"], "changed on disk")
        self.assertEqual(refused["set"], {"parallel": 2})

    def test_a_bad_value_is_refused_with_why(self):
        _, got = self.ask("GET")
        status, refused = self.ask("POST", {"mtime": got["mtime"], "changes": {"parallel": 0}})
        self.assertEqual(status, 400)
        self.assertIn(settings.EDITABLE["parallel"]["label"], refused["error"])  # parallel's name on the page
        self.assertEqual(refused["effective"]["parallel"], 1)

    def test_switching_the_agent_through_the_route_changes_the_ledger_it_holds(self):
        self.addCleanup(agent.use, "claude")
        st = self.handler.store
        pickup.save(st, {"inflight": ["PLN-01"], "jobs": [{"key": "PLN-01", "state": "lost", "id": "s1", "session": "s1-full"}]})
        _, got = self.ask("GET")
        status, saved = self.ask("POST", {"mtime": got["mtime"], "changes": {"agent": "copilot"}})  # set_agent's change() inside the route's hold
        self.assertEqual(status, 200, saved)
        self.assertEqual(st.agent, "copilot")
        self.assertEqual(pickup.load(st)["jobs"], [{"key": "PLN-01", "state": "lost", "id": "s1"}])

    def test_the_route_reads_board_json_again_when_it_was_changed_by_hand(self):
        (self.dir / "board.json").write_text(json.dumps({"name": "X", "parallel": 4}), encoding="utf-8")
        status, got = self.ask("GET")
        self.assertEqual(got["effective"]["parallel"], 4)
        self.assertEqual(self.handler.store.parallel, 4)


class Watch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "build.md").write_text("# Plans\n\n## In flight\n\n- [ ] PLN-80 **On its own.**\n", encoding="utf-8")
        self.ctx = {"seen": None, "last": 0.0, "told": set(), "before": None, "was_on": False}
        self.ticks, self.adopted, self.stopped = [], [], []
        for name, fake in (("why_off", lambda store, **kw: None),
                           ("tick", lambda store, tiles, **kw: self.ticks.append([t["key"] for t in tiles])),
                           ("adopt", lambda store, tiles: self.adopted.append([t["key"] for t in tiles]) or [])):
            p = mock.patch.object(pickup, name, fake)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(agent, "stop", lambda *a, **kw: self.stopped.append(a))
        p.start()
        self.addCleanup(p.stop)

    def store(self, cfg):
        (self.dir / "board.json").write_text(json.dumps(cfg), encoding="utf-8")
        aged(self.dir / "board.json")
        return Store(self.dir)

    def turns(self, store, n=2):  # the first look waits for build.md to settle
        for _ in range(n):
            serve.watch_turn(store, self.ctx)

    def test_pickup_turned_on_picks_up_tiles_already_in_flight(self):
        st = self.store({"pickup": False})
        self.turns(st)
        self.assertEqual((self.adopted, self.ticks), ([], []))
        (self.dir / "board.json").write_text(json.dumps({"pickup": True}), encoding="utf-8")
        self.turns(st)
        self.assertIs(st.pickup, True)
        self.assertEqual(self.adopted, [["PLN-80"]])
        self.assertEqual(self.ticks, [["PLN-80"]])

    def test_pickup_turned_off_leaves_sessions_alone(self):
        st = self.store({"pickup": True})
        self.ctx["was_on"] = True
        self.turns(st)
        self.assertEqual(len(self.ticks), 1)
        (self.dir / "board.json").write_text(json.dumps({"pickup": False}), encoding="utf-8")
        (self.dir / "build.md").write_text("# Plans\n\n## In flight\n\n", encoding="utf-8")
        self.turns(st, 3)
        self.assertIs(st.pickup, False)
        self.assertEqual(len(self.ticks), 1)
        self.assertEqual(self.stopped, [])

    def test_pickup_on_that_cannot_run_says_why_once(self):
        st = self.store({"pickup": False})
        (self.dir / "board.json").write_text(json.dumps({"pickup": True}), encoding="utf-8")
        with mock.patch.object(pickup, "why_off", lambda store, **kw: "Pickup is off: no app."), \
                mock.patch.object(pickup, "log") as log:
            self.turns(st, 4)
        self.assertEqual([c.args[0] for c in log.call_args_list], ["Pickup is off: no app."])
        self.assertEqual((self.adopted, self.ticks), ([], []))

    def test_pickup_that_could_not_run_starts_once_a_saved_setting_fixes_it(self):
        st = self.store({"pickup": True})
        self.ctx.update(was_on=True, why="Pickup is off: no app.", mtime=st.config_mtime)
        self.turns(st)
        self.assertEqual((self.adopted, self.ticks), ([], []))
        settings.apply(st, {"parallel": 2})  # as the settings route does: the store is reloaded there
        self.turns(st)
        self.assertEqual(self.adopted, [["PLN-80"]])
        self.assertEqual(self.ticks, [["PLN-80"]])

    def test_a_blocker_filed_in_the_archive_log_starts_the_tile_it_blocked_though_plans_md_is_unchanged(self):
        from test_pickup import Fake
        (self.dir / "build.md").write_text("# Plans\n\n## In flight\n\n- [ ] PLN-41 **Nav.** Blocked by PLN-30.\n", encoding="utf-8")
        (self.dir / "plan.md").write_text("# Backlog\n\n## Design decision\n\n- [ ] PLN-30 **Auth.**\n", encoding="utf-8")
        for name in ("build.md", "plan.md"):
            aged(self.dir / name)
        st = self.store({"pickup": True})
        self.ctx["was_on"] = True
        fake = Fake(announce=lambda store, job: None)
        with mock.patch.object(pickup, "tick", REAL_TICK), mock.patch.object(serve, "SESSIONS", fake), \
                mock.patch.object(pickup, "log"):
            self.turns(st, 3)
            self.assertEqual(fake.started, [])
            (self.dir / "plan.md").write_text("# Backlog\n\n## Design decision\n", encoding="utf-8")
            (self.dir / "archive").mkdir()
            (self.dir / "archive" / "archived.md").write_text(
                "# Archive\n\n## Archived\n\n- [x] PLN-30 **Auth.**\n  **Decision 2026-11-23:** retire it.\n", encoding="utf-8")
            self.turns(st)
        self.assertEqual(fake.started, ["PLN-41"])
        self.assertEqual(pickup.load(st)["blocked"], {})

    def test_a_board_json_that_cannot_be_read_does_not_end_the_watch(self):
        st = self.store({"pickup": True})
        self.ctx["was_on"] = True
        (self.dir / "board.json").write_bytes(b'{"pickup": "\xff"}')
        with mock.patch.object(pickup, "log"):
            self.turns(st)
        self.assertIs(st.pickup, True)
        self.assertIn("could not be read", st.config_problem)
        self.assertEqual(len(self.ticks), 1)

    def drafted(self, st, state, doc="active/2026-11-23-pln-80-options.md"):
        with jobs.Ledger(st).change() as held:
            held.setdefault("drafts", []).append({"key": "PLN-80", "state": state, "title": "On its own", "note": None,
                                                  "doc": doc, "asked_at": 1})

    def draft_turns(self, st, n=1, said=(), real=False):
        """n drafts' turns with drafts.look recorded (self.looks: whether each look's boards had
        PLN-80) and the console recorded (self.logged). The look gives the lines said, or is the
        real one (real). The number of looks."""
        self.looks = []
        def look(store, snap, **kw):
            self.looks.append(snap.tile("PLN-80") is not None)
            return REAL_LOOK(store, snap, **kw) if real else list(said)
        self.addCleanup(serve.DRAFTS_ASKED.clear)
        with mock.patch.object(drafts, "look", look), mock.patch.object(pickup, "log") as log:
            for _ in range(n):
                serve.watch_drafts_turn(st, self.ctx)
        self.logged = [c.args[0] for c in log.call_args_list]
        return len(self.looks)

    def test_a_draft_asked_for_is_looked_at_on_the_next_turn_with_pickup_off(self):
        st = self.store({"pickup": False})
        self.assertEqual(self.draft_turns(st, 2), 0)
        self.ctx["d_last"] = time.time()  # a look just now: the recheck isn't due
        self.drafted(st, "queued")
        serve.DRAFTS_ASKED.set()  # as /api/draft does
        self.assertEqual(self.draft_turns(st), 1)
        self.assertFalse(serve.DRAFTS_ASKED.is_set())
        self.assertEqual(self.draft_turns(st, 3), 0)

    def test_a_working_draft_is_looked_at_again_only_after_the_recheck_interval(self):
        st = self.store({"pickup": False})
        self.drafted(st, "working")
        self.assertEqual(self.draft_turns(st, 3), 1)
        self.ctx["d_last"] -= serve.PICKUP_RECHECK + 1
        self.assertEqual(self.draft_turns(st, 3), 1)

    def test_a_changed_doc_is_looked_at_once_it_has_settled(self):
        st = self.store({"pickup": False})
        doc = self.dir / "active" / "2026-11-23-pln-80-options.md"
        doc.parent.mkdir()
        doc.write_text("# PLN-80 options\n", encoding="utf-8")
        self.drafted(st, "ready")
        self.assertEqual(self.draft_turns(st, 4), 1)  # the first sight of it, once it held for a turn
        doc.write_text("# PLN-80 options\n\n## A. Do it\n", encoding="utf-8")
        os.utime(doc, ns=(doc.stat().st_atime_ns, doc.stat().st_mtime_ns + 10**9))
        self.assertEqual(self.draft_turns(st), 0)
        self.assertEqual(self.draft_turns(st), 1)
        self.assertEqual(self.draft_turns(st, 3), 0)

    def test_no_draft_no_look(self):
        st = self.store({"pickup": True})
        self.assertEqual(self.draft_turns(st, 4), 0)

    def test_a_look_reads_the_boards_as_they_are_then(self):
        st = self.store({"pickup": False})
        self.drafted(st, "queued")
        serve.DRAFTS_ASKED.set()
        self.draft_turns(st)
        self.assertEqual(self.looks, [True])
        (self.dir / "build.md").write_text("# Plans\n\n## Decisions\n\n", encoding="utf-8")
        serve.DRAFTS_ASKED.set()
        self.draft_turns(st)
        self.assertEqual(self.looks, [False])

    def test_a_line_a_look_keeps_giving_is_said_once(self):
        st = self.store({"pickup": False})
        self.drafted(st, "working")
        self.draft_turns(st, said=["PLN-80: the options could not be written: no board"])
        self.assertEqual(self.logged, ["PLN-80: the options could not be written: no board"])
        self.ctx["d_last"] -= serve.PICKUP_RECHECK + 1
        self.draft_turns(st, said=["PLN-80: the options could not be written: no board"])
        self.assertEqual(self.logged, [])
        self.ctx["d_last"] -= serve.PICKUP_RECHECK + 1
        self.draft_turns(st, said=[])
        self.ctx["d_last"] -= serve.PICKUP_RECHECK + 1
        self.draft_turns(st, said=["PLN-80: the options could not be written: no board"])
        self.assertEqual(self.logged, ["PLN-80: the options could not be written: no board"])

    def test_a_line_two_looks_in_a_row_give_for_two_events_is_said_twice(self):
        st = self.store({"pickup": False})
        self.drafted(st, "working")
        self.draft_turns(st, said=["PLN-80: options read again from its research doc"])
        self.ctx["d_last"] -= serve.PICKUP_RECHECK + 1
        self.draft_turns(st, said=["PLN-80: options read again from its research doc"])
        self.assertEqual(self.logged, ["PLN-80: options read again from its research doc"])

    def decision(self, column="Decisions", tile=True):
        board = self.dir / "build.md"
        board.write_text(f"# Plans\n\n## {column}\n\n" + ("- [ ] PLN-80 **On its own.**\n" if tile else ""), encoding="utf-8")
        if board.stat().st_mtime_ns == getattr(self, "board_at", None):
            os.utime(board, ns=(board.stat().st_atime_ns, board.stat().st_mtime_ns + 10**9))
        self.board_at = board.stat().st_mtime_ns

    def test_a_ready_draft_whose_tile_is_dragged_out_of_its_column_is_ended_once_the_board_settles(self):
        self.decision()
        st = self.store({"pickup": False})
        self.drafted(st, "ready")
        self.assertEqual(self.draft_turns(st, 3, real=True), 1)  # the first sight of it
        self.assertEqual([d["state"] for d in drafts.live(st)], ["ready"])
        self.decision("To do")
        self.assertEqual(self.draft_turns(st, real=True), 0)  # not settled yet
        self.assertEqual(self.draft_turns(st, real=True), 1)
        self.assertEqual(drafts.live(st), [])
        self.assertEqual(self.logged, ["PLN-80: draft ended, its tile is no longer a decision"])
        self.assertEqual(self.draft_turns(st, 3, real=True), 0)

    def test_a_ready_draft_whose_tile_is_deleted_is_ended_once_the_board_settles(self):
        self.decision()
        st = self.store({"pickup": False})
        self.drafted(st, "ready")
        self.draft_turns(st, 3, real=True)
        self.decision(tile=False)
        self.assertEqual(self.draft_turns(st, 2, real=True), 1)
        self.assertEqual(drafts.live(st), [])
        self.assertEqual(self.logged, ["PLN-80: draft ended, its tile is gone"])

    def test_with_no_draft_a_turn_reads_the_ledger_once_and_looks_at_no_board(self):
        st = self.store({"pickup": False})
        with mock.patch.object(serve, "board_mtimes", side_effect=AssertionError("a board was looked at")), \
                mock.patch.object(drafts, "live", wraps=drafts.live) as live, \
                mock.patch.object(serve.reader.Snapshot, "read", side_effect=AssertionError("the boards were read")):
            self.assertEqual(self.draft_turns(st, 3), 0)
        self.assertEqual(live.call_count, 3)

    def test_a_draft_left_ending_brings_a_look_after_the_recheck_interval_and_not_before(self):
        st = self.store({"pickup": False})
        self.drafted(st, "ending")
        self.ctx["d_last"] = time.time()
        self.assertEqual(self.draft_turns(st, 3), 0)
        self.ctx["d_last"] -= serve.PICKUP_RECHECK + 1
        self.assertEqual(self.draft_turns(st, 3), 1)

    def test_a_ready_draft_is_not_looked_at_again_while_nothing_changes(self):
        st = self.store({"pickup": False})
        self.drafted(st, "ready")
        self.assertEqual(self.draft_turns(st, 3), 1)
        self.ctx["d_last"] -= serve.PICKUP_RECHECK + 1
        self.assertEqual(self.draft_turns(st, 3), 0)

    def test_a_doc_changed_while_its_session_was_busy_is_read_once_the_session_is_idle(self):
        self.decision()
        st = self.store({"pickup": False})
        doc = self.dir / "active" / "2026-11-23-pln-80-options.md"
        doc.parent.mkdir()
        doc.write_text("# PLN-80 options\n\n## A. Do it\n", encoding="utf-8")
        aged(doc)
        self.drafted(st, "ready")
        with jobs.Ledger(st).change() as held:
            held["drafts"][0].update(id="s1", read=doc.stat().st_mtime_ns)
        listing, wrote = {"s1": "busy"}, []
        fake = pickup.Sessions(sessions=lambda: dict(listing), now=time.time)
        with mock.patch.object(serve, "SESSIONS", fake), \
                mock.patch.object(serve, "write_options", lambda store, key, block, subject: wrote.append(key)):
            self.assertEqual(self.draft_turns(st, 3, real=True), 1)  # the first sight of it
            doc.write_text("# PLN-80 options\n\n## A. Do it\n\n## B. Leave it\n", encoding="utf-8")
            self.assertEqual(self.draft_turns(st, 2, real=True), 1)  # settled, but its session is busy
            self.assertEqual(wrote, [])
            listing["s1"] = "idle"
            self.assertEqual(self.draft_turns(st, 3, real=True), 0)  # not before the recheck interval
            self.ctx["d_last"] -= serve.PICKUP_RECHECK + 1
            self.assertEqual(self.draft_turns(st, real=True), 1)
            self.assertEqual(wrote, ["PLN-80"])
            self.ctx["d_last"] -= serve.PICKUP_RECHECK + 1
            self.assertEqual(self.draft_turns(st, 3, real=True), 0)  # read now: nothing more to look at

    def test_a_block_that_could_not_be_written_is_tried_again_after_the_recheck_interval(self):
        st = self.store({"pickup": False})
        self.drafted(st, "ready")
        failed = ["PLN-80: the options could not be written: no board"]
        self.assertEqual(self.draft_turns(st, 3, said=failed), 1)
        self.assertEqual(self.draft_turns(st, 3, said=failed), 0)  # not before the recheck interval
        self.ctx["d_last"] -= serve.PICKUP_RECHECK + 1
        self.assertEqual(self.draft_turns(st, 3), 1)  # this one writes it
        self.ctx["d_last"] -= serve.PICKUP_RECHECK + 1
        self.assertEqual(self.draft_turns(st, 3), 0)

    def move_turns(self, st, n=1):
        for _ in range(n):
            serve.watch_move_turn(st, self.ctx)

    def test_a_due_store_is_moved_at_the_top_of_a_turn(self):
        st = self.store({"pickup": False})
        jobs.save(st, {"inflight": [], "jobs": [{"key": "PLN-80", "state": "started", "id": "s1", "started": 1}]})
        listing, order = {"s1": "busy"}, []

        class Stop(Exception):
            pass

        def sleep(seconds):
            if listing["s1"] == "idle":
                raise Stop
            listing["s1"] = "idle"  # its turn ends during the first sleep

        real = serve.watch_move_turn
        with mock.patch.object(serve, "SESSIONS", pickup.Sessions(sessions=lambda: dict(listing))), \
                mock.patch.object(serve, "PICKUP_RECHECK", 0), \
                mock.patch.object(serve, "watch_move_turn", lambda store, ctx: order.append(("move", store.build)) or real(store, ctx)), \
                mock.patch.object(serve, "watch_drafts_turn", lambda store, ctx: order.append(("drafts", store.build))), \
                mock.patch.object(serve, "watch_turn", lambda store, ctx: order.append(("turn", store.build))), \
                mock.patch.object(serve, "report_in_flight", lambda store: None), \
                mock.patch.object(serve.time, "sleep", sleep), mock.patch.object(pickup, "log"):
            with self.assertRaises(Stop):
                serve.watch_pickups(st)
        self.assertEqual(order, [("move", "build.md"),  # at the start: its session is busy
                                 ("move", "build.md"), ("drafts", "build.md"), ("turn", "build.md"),
                                 ("move", "build.md"), ("drafts", "build.md"), ("turn", "build.md")])
        self.assertEqual(st.boards, ("build.md", "plan.md"))

    def test_a_store_whose_board_changed_since_the_previous_turn_waits_a_turn(self):
        st = self.store({"pickup": False})
        aged(self.dir / "build.md")
        self.ctx["m_before"] = serve.old_board_mtimes(st)  # as the watch starts
        (self.dir / "build.md").write_text("# Plans\n\n## In flight\n\n- [ ] PLN-80 **On its own.** Edited.\n", encoding="utf-8")
        with mock.patch.object(pickup, "log"):
            self.move_turns(st)
            self.assertEqual(st.build, "build.md")
            self.move_turns(st)
        self.assertEqual(st.build, "build.md")
        self.assertIn("Edited.", (self.dir / "build.md").read_text(encoding="utf-8"))

    def test_a_store_not_due_costs_one_check_and_no_listing(self):
        st = self.store({"pickup": False})
        with mock.patch.object(pickup, "log"):
            serve.renaming.move(st, sessions=pickup.Sessions(sessions=lambda: {}), record=mock.Mock())
        with mock.patch.object(serve.renaming, "due", wraps=serve.renaming.due) as due, \
                mock.patch.object(serve.renaming, "move", side_effect=AssertionError("moved again")), \
                mock.patch.object(serve, "SESSIONS", pickup.Sessions(sessions=mock.Mock(side_effect=AssertionError("listed")))):
            self.move_turns(st, 3)
        self.assertEqual(due.call_count, 3)

    def test_a_move_that_fails_is_said_once_and_does_not_end_the_watch(self):
        st = self.store({"pickup": False})
        self.ctx["m_before"] = serve.old_board_mtimes(st)
        with mock.patch.object(serve.renaming, "move", side_effect=OSError("the disk is full")), \
                mock.patch.object(serve, "PICKUP_RECHECK", 0), mock.patch.object(pickup, "log") as log:
            self.move_turns(st, 3)
        self.assertEqual([c.args[0] for c in log.call_args_list], ["the move to Plan and Build failed: the disk is full"])

if __name__ == "__main__":
    unittest.main()
