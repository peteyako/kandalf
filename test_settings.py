import http.client
import json
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from kandalf import agent, pickup, serve, settings
from kandalf.store import Store


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
        (self.dir / "plans.md").write_text("# Plans\n\n## In flight\n\n- [ ] PLN-80 **On its own.**\n", encoding="utf-8")
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

    def turns(self, store, n=2):  # the first look waits for plans.md to settle
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
        (self.dir / "plans.md").write_text("# Plans\n\n## In flight\n\n", encoding="utf-8")
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

    def test_a_board_json_that_cannot_be_read_does_not_end_the_watch(self):
        st = self.store({"pickup": True})
        self.ctx["was_on"] = True
        (self.dir / "board.json").write_bytes(b'{"pickup": "\xff"}')
        with mock.patch.object(pickup, "log"):
            self.turns(st)
        self.assertIs(st.pickup, True)
        self.assertIn("could not be read", st.config_problem)
        self.assertEqual(len(self.ticks), 1)


if __name__ == "__main__":
    unittest.main()
