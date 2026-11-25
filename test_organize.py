#!/usr/bin/env python3
"""Organize (organize.py): the map asks the store's agent to sort stories into domains, and the
person reviews what it proposes before anything is filed.

    python3 -m unittest test_organize
"""

import http.client
import json
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from kandalf import agent, jobs, organize, pickup, serve, tiles
from kandalf import store as stores

WHY_NOT = organize.why_not  # the real rule, before the fixture stubs it

BUILD = """# Plans

## To do

- [ ] PLN-70 **Dark mode.** The master story.
  Domain: Theming
- [ ] PLN-71 **Theme auth.** Part of PLN-70.
- [ ] PLN-72 **Pay by card.** Take card payments at checkout. See [the plan](active/2026-11-01-pay.md).

## Done

- [x] PLN-73 **Club pages.** A page per club.
  Domain: Clubs
"""

PLAN = """# Backlog

## Feature

- [ ] PLN-80 **Refunds.** Give money back.
- [ ] PLN-81 **Club invites.**
"""

ARCHIVE = """# Archived plans

## Archived

- [x] PLN-40 **Old thing.** Gone.
"""


def git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout


class Store(unittest.TestCase):
    """A temp app repo (one commit, branch dev) and a store beside it with a Build board, a Plan
    board and an archive, and a sessions adapter that records what it retires."""

    def setUp(self):
        self.top = top = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(top)]))
        self.app, self.plans = top / "app", top / "plans"
        (self.app / "docs").mkdir(parents=True)
        (self.plans / "archive").mkdir(parents=True)
        (self.plans / "active").mkdir()
        (self.plans / "board.json").write_text(json.dumps({"app": "../app", "trunk": "dev"}))
        (self.plans / "build.md").write_text(BUILD, encoding="utf-8")
        (self.plans / "plan.md").write_text(PLAN, encoding="utf-8")
        (self.plans / "archive" / "archived.md").write_text(ARCHIVE, encoding="utf-8")
        (self.plans / "active" / "2026-11-01-pay.md").write_text("# Pay\n", encoding="utf-8")
        (self.app / "docs" / "plans").symlink_to("../../plans")
        (self.app / "README").write_text("app\n")
        git(self.app, "init", "-q", "-b", "dev")
        git(self.app, "add", "-A")
        git(self.app, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        self.store = stores.Store(self.plans)
        self.retired, self.live = [], {}
        self.clock = [time.mktime((2026, 11, 24, 12, 0, 0, 0, 0, -1))]
        self.sessions = pickup.Sessions(retire=self.retire, sessions=lambda: dict(self.live), why=lambda sid: self.waits,
                                        now=lambda: self.clock[0])
        self.waits = None
        p = mock.patch.object(organize, "why_not", lambda store: None)
        p.start()
        self.addCleanup(p.stop)

    def retire(self, sid):
        self.retired.append(sid)
        self.live.pop(sid, None)
        return True

    def snap(self):
        return tiles.Snapshot.read(self.store)

    def start(self, note=None, refile=False, launched=None, effect=None):
        launched = launched or {"state": "started", "id": "s1", "session": "s1-full"}
        def launch_(store, args, cwd, text):  # the session, stubbed: effect(cwd) is what it does there
            if effect:
                effect(cwd)
            return launched
        with mock.patch.object(organize, "_launch", side_effect=launch_) as launch:
            self.launch = launch
            got = organize.start(self.store, note, refile, sessions=self.sessions)
        if launched.get("id"):
            self.live[launched["id"]] = "busy"
        return got

    def path(self):
        return pickup.worktree(self.store, {"key": "kandalf-organize", "title": ""}, self.store.main_repo)

    def store_files(self):
        """Every file of the store but the ledger and git's, with its bytes."""
        return {str(f.relative_to(self.plans)): f.read_bytes() for f in sorted(self.plans.rglob("*"))
                if f.is_file() and not f.name.startswith(".") and ".git" not in f.parts}

    def later(self, seconds=jobs.SETTLE + 1):
        self.clock[0] += seconds

    def write(self, text):
        (self.path() / organize.OUTPUT).write_text(text, encoding="utf-8")


class Stories(Store):
    def test_only_roots_with_no_domain_are_sorted_by_default(self):
        got = organize.stories(self.store, self.snap(), False)
        self.assertEqual([s["key"] for s in got], ["PLN-72", "PLN-80", "PLN-81"])
        self.assertEqual(got[0]["title"], "Pay by card")
        self.assertEqual(got[0]["doc"], "active/2026-11-01-pay.md")
        self.assertIn("Take card payments at checkout", got[0]["description"])
        self.assertIsNone(got[0]["domain"])

    def test_re_sorting_takes_every_root_on_the_plan_and_build_boards(self):
        got = organize.stories(self.store, self.snap(), True)
        self.assertEqual([s["key"] for s in got], ["PLN-70", "PLN-72", "PLN-73", "PLN-80", "PLN-81"])
        self.assertEqual(got[0]["domain"], "Theming")

    def test_sub_stories_and_archived_stories_are_never_sorted(self):
        for refile in (False, True):
            keys = [s["key"] for s in organize.stories(self.store, self.snap(), refile)]
            self.assertNotIn("PLN-71", keys)
            self.assertNotIn("PLN-40", keys)

    def test_a_plan_doc_link_that_leaves_the_store_is_not_a_plan_doc(self):
        (self.top / "secret.md").write_text("outside\n")
        (self.plans / "plan.md").write_text("# Plan\n\n## Feature\n\n- [ ] PLN-80 **Refunds.** See [doc](active/../../secret.md).\n", encoding="utf-8")
        got = organize.stories(self.store, self.snap(), False)
        self.assertIsNone(next(s for s in got if s["key"] == "PLN-80")["doc"])

    def test_a_description_is_cut_to_about_300_characters(self):
        long = " ".join(["word"] * 200)
        (self.plans / "plan.md").write_text(f"# Backlog\n\n## Feature\n\n- [ ] PLN-80 **Refunds.** {long}\n", encoding="utf-8")
        got = organize.stories(self.store, self.snap(), False)
        self.assertLessEqual(len(got[-1]["description"]), 301)

    def test_the_domains_there_are_count_their_stories_with_sub_stories_following_their_master(self):
        self.assertEqual(organize.domains(self.snap()), [("Theming", 2), ("Clubs", 1)])

    def test_a_title_prefix_the_map_draws_as_a_domain_counts_as_one(self):
        (self.plans / "archive" / "archived.md").write_text(ARCHIVE + "".join(f"- [x] PLN-4{i} **Admin: part {i}.**\n" for i in range(1, 4)))
        self.assertIn(("Admin", 3), organize.domains(self.snap()))


class Prompt(Store):
    def prompt(self, note=None, refile=False):
        snap = self.snap()
        return organize.prompt(self.store, organize.stories(self.store, snap, refile), organize.domains(snap), note)

    def test_it_names_the_stories_the_domains_the_output_file_and_the_name_rule(self):
        p = self.prompt()
        for phrase in ("PLN-72 Pay by card", "Take card payments", "docs/plans/active/2026-11-01-pay.md",
                       "PLN-80 Refunds", "PLN-81 Club invites", "- Theming (2 stories)", "- Clubs (1 story)",
                       "ORGANIZE.md", "- KEY: Domain name", "starts with a letter or a digit", "at most 39 characters",
                       "no backtick", "Reuse", "handful", "left out", "Don't edit any board or plan doc",
                       "sub-stories follow"):
            self.assertIn(phrase, p)
        self.assertNotIn("PLN-71", p)
        self.assertNotIn("The person asked", p)
        self.assertNotIn("Domain now", p)

    def test_the_note_is_passed_on(self):
        self.assertIn("The person asked: keep payments separate from clubs", self.prompt("keep payments separate from clubs"))

    def test_re_sorting_says_each_story_s_domain_now(self):
        p = self.prompt(refile=True)
        self.assertIn("Domain now: Theming", p)
        self.assertIn("Domain now: none", p)

    def test_a_store_with_no_domains_says_so(self):
        for name in ("build.md", "plan.md"):
            path = self.plans / name
            path.write_text("\n".join(x for x in path.read_text().splitlines() if "Domain:" not in x) + "\n")
        self.assertIn("There are no domains yet.", self.prompt())


GIVEN = {"PLN-72": None, "PLN-80": None, "PLN-81": "Clubs"}


class Parse(unittest.TestCase):
    def test_plain_lines_are_read(self):
        self.assertEqual(organize.parse("- PLN-72: Payments\n- PLN-80: Payments\n", GIVEN), {"PLN-72": "Payments", "PLN-80": "Payments"})

    def test_bullets_backticks_bold_full_stops_and_spaces_are_tolerated(self):
        text = "* `PLN-72`: Payments.\n-   **PLN-80**:   Billing and refunds  \n- **PLN-81: Club life**\n"
        self.assertEqual(organize.parse(text, GIVEN), {"PLN-72": "Payments", "PLN-80": "Billing and refunds", "PLN-81": "Club life"})

    def test_unknown_keys_bad_names_and_other_lines_are_dropped(self):
        text = "# Domains\n\nSome words.\n- PLN-99: Payments\n- PLN-72: -bad\n- PLN-80: " + "x" * 40 + "\n- PLN-81: a`b\n"
        self.assertEqual(organize.parse(text, GIVEN), {})

    def test_the_first_line_for_a_key_wins(self):
        self.assertEqual(organize.parse("- PLN-72: Payments\n- PLN-72: Billing\n", GIVEN), {"PLN-72": "Payments"})

    def test_a_story_proposed_for_the_domain_it_is_in_is_no_move(self):
        self.assertEqual(organize.parse("- PLN-81: clubs\n- PLN-72: Payments\n", GIVEN), {"PLN-72": "Payments"})


class Start(Store):
    def test_it_cuts_a_detached_worktree_with_copies_of_the_plan_docs_and_records_working(self):
        got = self.start("keep payments apart")
        path = self.path()
        self.assertEqual((got["state"], got["id"], got["session"]), ("working", "s1", "s1-full"))
        self.assertEqual(git(path, "branch", "--show-current").strip(), "")
        self.assertEqual(git(path, "rev-parse", "HEAD"), git(self.app, "rev-parse", "dev"))
        self.assertFalse((path / "docs" / "plans").is_symlink())
        self.assertEqual((path / "docs" / "plans" / "active" / "2026-11-01-pay.md").read_text(), "# Pay\n")
        self.assertFalse((path / "docs" / "plans" / "build.md").exists())
        rec = organize.of(self.store)
        self.assertEqual((rec["state"], rec["worktree"], rec["note"], rec["refile"], rec["started"]),
                         ("working", str(path), "keep payments apart", False, self.clock[0]))
        self.assertEqual([s["key"] for s in rec["stories"]], ["PLN-72", "PLN-80", "PLN-81"])
        text, cwd = self.launch.call_args.args[3], self.launch.call_args.args[2]
        self.assertEqual(cwd, path)
        self.assertIn("keep payments apart", text)
        self.assertEqual(git(self.app, "status", "--porcelain"), "")

    def test_a_branch_worktree_with_work_in_it_at_the_path_is_left_alone(self):
        git(self.app, "worktree", "add", "-q", "-b", "mine", str(self.path()))
        (self.path() / "unsaved").write_text("work\n")
        got = self.start()
        self.assertEqual(got["state"], "need")
        self.assertIn(str(self.path()), got["why"])
        self.assertEqual((self.path() / "unsaved").read_text(), "work\n")
        self.launch.assert_not_called()
        organize.end(self.store, sessions=self.sessions)
        self.assertEqual((self.path() / "unsaved").read_text(), "work\n")

    def test_nothing_the_session_writes_reaches_the_store(self):
        before = self.store_files()
        def scribble(cwd):
            (cwd / "docs" / "plans" / "build.md").write_text("scribbled\n")
            (cwd / "docs" / "plans" / "active" / "2026-11-01-pay.md").write_text("scribbled\n")
        self.start(effect=scribble)
        self.write("- PLN-72: Payments\n")
        self.live["s1"] = "idle"
        self.later()
        organize.look(self.store, sessions=self.sessions)
        organize.end(self.store, sessions=self.sessions)
        self.assertEqual(self.store_files(), before)

    def test_the_session_is_started_without_the_store_s_hooks_or_its_folder(self):
        (self.plans / ".claude").mkdir()
        (self.plans / ".claude" / "settings.json").write_text("{}")
        self.assertTrue(self.store.hooks_file.is_file())
        with mock.patch.object(agent, "start", return_value={"id": "s9"}) as began, mock.patch.object(pickup, "full_id", return_value="s9-full"):
            got = organize._launch(self.store, ["-n", "Organize domains"], self.app, "text")
        self.assertEqual(got, {"state": "started", "id": "s9", "session": "s9-full"})
        self.assertNotIn("--settings", began.call_args.args[0])
        self.assertEqual((began.call_args.args[1:], began.call_args.kwargs), ((self.app, self.store.permission, "text"), {}))  # no folders added, the store's least of all

    def test_a_run_still_being_cleaned_up_refuses_a_new_one_in_those_words(self):
        self.start()
        self.sessions.retire = lambda sid: False
        organize.end(self.store, sessions=self.sessions)
        with self.assertRaises(organize.Refused) as e:
            self.start()
        self.assertEqual(str(e.exception), "The last sort is still being cleaned up; try again in a moment.")

    def test_a_second_run_is_refused(self):
        self.start()
        with self.assertRaises(organize.Refused) as e:
            self.start()
        self.assertIn("already", str(e.exception))

    def test_nothing_to_sort_is_refused_and_records_nothing(self):
        (self.plans / "plan.md").write_text("# Backlog\n\n## Feature\n", encoding="utf-8")
        (self.plans / "build.md").write_text(BUILD.replace("(active/2026-11-01-pay.md).\n", "(active/2026-11-01-pay.md).\n  Domain: Payments\n"), encoding="utf-8")
        with self.assertRaises(organize.Refused) as e:
            self.start()
        self.assertIn("Re-sort", str(e.exception))
        self.assertIsNone(organize.of(self.store))
        self.assertFalse(self.path().exists())

    def test_an_agent_that_cannot_run_is_refused_in_its_words(self):
        with mock.patch.object(organize, "why_not", lambda store: "Organize is off: no app."):
            with self.assertRaises(organize.Refused) as e:
                self.start()
        self.assertEqual(str(e.exception), "Organize is off: no app.")

    def test_a_session_that_does_not_start_needs_you_and_leaves_no_worktree(self):
        got = self.start(launched={"state": "failed", "error": "no claude"})
        self.assertEqual((got["state"], got["why"]), ("need", "The session didn't start: no claude"))
        self.assertFalse(self.path().exists())

    def test_why_not_needs_what_pickup_needs_and_a_permission_that_writes(self):
        real = WHY_NOT  # the fixture stubs organize.why_not
        with mock.patch.object(pickup, "cannot_run", lambda store, **kw: None):
            self.assertIsNone(real(self.store))
        with mock.patch.object(pickup, "cannot_run", lambda store, **kw: "the app folder isn't there."):
            self.assertEqual(real(self.store), "Organize is off: the app folder isn't there.")
        (self.plans / "board.json").write_text(json.dumps({"app": "../app", "permission": "plan"}))
        with mock.patch.object(pickup, "cannot_run", lambda store, **kw: None):
            self.assertIn("permission is plan", real(stores.Store(self.plans)))


class Look(Store):
    def look(self):
        return organize.look(self.store, sessions=self.sessions)

    def test_a_finished_session_s_file_makes_the_run_ready_and_its_worktree_goes(self):
        self.start()
        self.write("- PLN-72: Payments\n- PLN-80: payments\n- PLN-81: Clubs\n")
        self.live["s1"] = "idle"
        self.later()
        said = self.look()
        rec = organize.of(self.store)
        self.assertEqual(rec["state"], "ready")
        self.assertEqual(rec["moves"], [{"key": "PLN-72", "title": "Pay by card", "domain": "Payments", "new": True, "from": None},
                                        {"key": "PLN-80", "title": "Refunds", "domain": "Payments", "new": True, "from": None},
                                        {"key": "PLN-81", "title": "Club invites", "domain": "Clubs", "new": False, "from": None}])
        self.assertEqual(rec["left"], [])
        self.assertEqual(self.retired, ["s1"])
        self.assertFalse(self.path().exists())
        self.assertTrue(any("ready" in line for line in said), said)

    def test_a_title_prefix_domain_is_not_new(self):
        (self.plans / "archive" / "archived.md").write_text(ARCHIVE + "".join(f"- [x] PLN-4{i} **Admin: part {i}.**\n" for i in range(1, 4)))
        self.start()
        self.write("- PLN-72: admin\n")
        self.live["s1"] = "idle"
        self.later()
        self.look()
        self.assertEqual(organize.of(self.store)["moves"], [{"key": "PLN-72", "title": "Pay by card", "domain": "Admin", "new": False, "from": None}])

    def test_an_existing_domain_is_spelt_as_the_board_spells_it(self):
        self.start()
        self.write("- PLN-72: theming\n")
        self.live["s1"] = "idle"
        self.later()
        self.look()
        rec = organize.of(self.store)
        self.assertEqual(rec["moves"], [{"key": "PLN-72", "title": "Pay by card", "domain": "Theming", "new": False, "from": None}])
        self.assertEqual(rec["left"], ["PLN-80", "PLN-81"])

    def test_a_busy_or_just_started_session_is_left_alone(self):
        self.start()
        self.write("- PLN-72: Payments\n")
        self.live["s1"] = "idle"
        self.look()
        self.assertEqual(organize.of(self.store)["state"], "working")
        self.live["s1"] = "busy"
        self.later()
        self.look()
        self.assertEqual(organize.of(self.store)["state"], "working")

    def test_a_proposal_written_before_the_session_stopped_on_a_question_is_kept(self):
        self.start()
        self.write("- PLN-72: Payments\n")
        self.live["s1"] = "waiting"
        self.later()
        self.look()
        self.assertEqual(organize.of(self.store)["state"], "ready")
        self.assertEqual(self.retired, ["s1"])

    def test_a_proposal_left_by_a_session_that_is_gone_is_kept(self):
        self.start()
        self.write("- PLN-72: Payments\n")
        del self.live["s1"]
        self.later()
        self.look()
        self.assertEqual(organize.of(self.store)["state"], "ready")

    def test_a_waiting_session_needs_you(self):
        self.start()
        self.live["s1"] = "waiting"
        self.later()
        self.look()
        rec = organize.of(self.store)
        self.assertEqual(rec["state"], "need")
        self.assertIn("asked a question", rec["why"])
        self.assertFalse(self.path().exists())

    def test_a_lost_session_with_no_file_needs_you(self):
        self.start()
        del self.live["s1"]
        self.later()
        self.look()
        rec = organize.of(self.store)
        self.assertEqual(rec["state"], "need")
        self.assertIn("gone", rec["why"])
        self.assertFalse(self.path().exists())

    def test_a_file_with_no_usable_line_needs_you(self):
        self.start()
        self.write("I could not decide.\n- PLN-99: Payments\n")
        self.live["s1"] = "idle"
        self.later()
        self.look()
        rec = organize.of(self.store)
        self.assertEqual((rec["state"], rec["why"]), ("need", "The agent proposed no moves."))

    def test_a_run_recorded_before_its_session_started_is_lost_like_any_other(self):
        with jobs.Ledger(self.store).change() as state:  # a server that stopped part-way through a start
            state["organize"] = {"state": "working", "started": self.clock[0], "stories": [], "worktree": str(self.path())}
        self.later()
        self.look()
        self.assertEqual(organize.of(self.store)["state"], "need")


class End(Store):
    def test_ending_retires_the_session_removes_the_worktree_and_the_record_and_is_safe_twice(self):
        self.start()
        self.assertTrue(self.path().exists())
        self.assertTrue(organize.end(self.store, sessions=self.sessions))
        self.assertEqual(self.retired, ["s1"])
        self.assertFalse(self.path().exists())
        self.assertIsNone(organize.of(self.store))
        self.assertTrue(organize.end(self.store, sessions=self.sessions))
        self.assertEqual(self.retired, ["s1"])

    def test_a_worktree_on_a_branch_at_the_path_is_left_alone(self):
        self.start()
        organize.end(self.store, sessions=self.sessions)
        git(self.app, "worktree", "add", "-q", "-b", "mine", str(self.path()))
        with jobs.Ledger(self.store).change() as state:
            state["organize"] = {"state": "need", "started": 1, "stories": [], "worktree": str(self.path())}
        with mock.patch.object(pickup, "log"):
            self.assertTrue(organize.end(self.store, sessions=self.sessions))
        self.assertTrue(self.path().exists())

    def test_a_session_that_stays_listed_leaves_the_run_ending_for_the_look(self):
        self.start()
        self.sessions.retire = lambda sid: False
        self.assertFalse(organize.end(self.store, sessions=self.sessions))
        self.assertEqual(organize.of(self.store)["state"], "ending")
        self.assertIsNone(organize.summary(self.store))
        self.sessions.retire = self.retire
        organize.look(self.store, sessions=self.sessions)
        self.assertIsNone(organize.of(self.store))


class Summary(Store):
    def test_the_page_is_told_none_then_the_run(self):
        self.assertIsNone(organize.summary(self.store))
        self.start("a note")
        self.assertEqual(organize.summary(self.store), {"state": "working", "started": self.clock[0], "note": "a note", "refile": False})


class Server(Store):
    def setUp(self):
        super().setUp()
        for target, name, value in ((serve, "SESSIONS", self.sessions), (pickup, "why_off", lambda store: None)):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(serve.gitlog, "record")
        self.record = p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(pickup, "log")
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(serve.ORGANIZE_ASKED.clear)

    def call(self, method, path, body=None):
        srv = serve.ThreadingHTTPServer(("127.0.0.1", 0), type("Handler", (serve.Handler,), {"store": self.store}))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=20)
            c.request(method, path, body=json.dumps(body).encode() if body is not None else None,
                      headers={"Content-Type": "application/json"})
            r = c.getresponse()
            out = (r.status, json.loads(r.read()))
            c.close()
            return out
        finally:
            srv.shutdown()
            srv.server_close()

    def test_the_routes_start_a_run_report_it_and_end_it(self):
        with mock.patch.object(organize, "_launch", return_value={"state": "started", "id": "s1", "session": "s1-full"}):
            status, reply = self.call("POST", "/api/organize", {"note": "apart", "refile": False})
        self.assertEqual(status, 200, reply)
        self.assertEqual(reply["organize"]["state"], "working")
        self.assertTrue(serve.ORGANIZE_ASKED.is_set())
        status, reply = self.call("POST", "/api/organize", {"note": "", "refile": True})
        self.assertEqual(status, 409)
        self.assertIn("already", reply["error"])
        status, reply = self.call("GET", "/api/pickup")
        self.assertEqual(reply["organize"]["note"], "apart")
        status, reply = self.call("POST", "/api/organize/end", {})
        self.assertEqual((status, reply["organize"]), (200, None))
        self.assertFalse(self.path().exists())
        status, reply = self.call("POST", "/api/organize/end", {})
        self.assertEqual((status, reply["organize"]), (200, None))

    def test_a_ready_run_s_moves_reach_the_page(self):
        self.start()
        self.write("- PLN-72: Payments\n")
        self.live["s1"] = "idle"
        self.later()
        organize.look(self.store, sessions=self.sessions)
        status, reply = self.call("GET", "/api/pickup")
        self.assertEqual(reply["organize"]["state"], "ready")
        self.assertEqual(reply["organize"]["moves"], [{"key": "PLN-72", "title": "Pay by card", "domain": "Payments", "new": True, "from": None}])
        self.assertEqual(reply["organize"]["left"], ["PLN-80", "PLN-81"])

    def test_the_watch_s_turn_looks_when_asked(self):
        self.start()
        self.write("- PLN-72: Payments\n")
        self.live["s1"] = "idle"
        self.later()
        serve.ORGANIZE_ASKED.set()
        serve.watch_organize_turn(self.store, {})
        self.assertEqual(organize.of(self.store)["state"], "ready")


if __name__ == "__main__":
    unittest.main()
