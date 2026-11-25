#!/usr/bin/env python3
"""Split (split.py): a story's card asks the store's agent to split it into sub-stories, and the
person reviews what it proposes before anything is written (serve.split_story).

    python3 -m unittest test_split
"""

import http.client
import json
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from kandalf import gitlog, jobs, organize, pickup, serve, split, tiles
from kandalf import store as stores

WHY_NOT = split.why_not  # the real rule, before the fixture stubs it

STORY = """- [ ] PLN-72 P2 **Checkout.** Take card payments and refunds. See [the plan](active/2026-11-01-pay.md).
  Domain: Payments
  - [ ] card form
  - [x] refund API
    with a note under it
  - [ ] receipts"""

BUILD = f"""# Build

## To do

- [ ] PLN-70 **Dark mode.** The master story.
- [ ] PLN-71 **Theme auth.** Part of PLN-70.
{STORY}
- [ ] PLN-73 **After.** Mentions PLN-200 in prose.

## In flight

- [ ] PLN-74 **Running.**
  Claude session `ab12cd34`: `claude --resume ab12cd34`. Branch `feat`, worktree `w`.
"""

PLAN = """# Plan

## Feature

- [ ] PLN-80 **Refunds.** Give money back.
"""

ARCHIVE = """# Archive

## Archived

- [x] PLN-95 **Old thing.** Gone.
"""

PROPOSAL = """# Split of PLN-72

Some words before the first heading.

## Card form
Build the card form and take a payment with it.
It checks the card as you type.

- [ ] card form
- [ ] Validate the expiry date

Words after the sub-tasks.

## **Refunds.**
Give money back.

- [ ] refund API

##
- [ ] a story with no title

## Receipts
"""


def git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout


class Store(unittest.TestCase):
    """A temp app repo (one commit, branch dev) and a store beside it (its own git repo) with a
    Build board, a Plan board and an archive, and a sessions adapter that records what it retires."""

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
        for repo in (self.app, self.plans):
            git(repo, "init", "-q", "-b", "dev")
            git(repo, "config", "user.name", "t")
            git(repo, "config", "user.email", "t@t")
            git(repo, "add", "-A")
            git(repo, "commit", "-qm", "init")
        self.store = stores.Store(self.plans)
        self.retired, self.live = [], {}
        self.clock = [time.mktime((2026, 11, 24, 12, 0, 0, 0, 0, -1))]
        self.sessions = pickup.Sessions(retire=self.retire, sessions=lambda: dict(self.live), why=lambda sid: self.waits,
                                        now=lambda: self.clock[0])
        self.waits = None
        for target, name, value in ((split, "why_not", lambda store: None), (pickup, "log", lambda *a: None)):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)

    def retire(self, sid):
        self.retired.append(sid)
        self.live.pop(sid, None)
        return True

    def snap(self):
        return tiles.Snapshot.read(self.store)

    def start(self, key="PLN-72", note=None, launched=None, effect=None):
        launched = launched or {"state": "started", "id": "s1", "session": "s1-full"}
        def launch_(store, args, cwd, text):  # the session, stubbed: effect(cwd) is what it does there
            if effect:
                effect(cwd)
            return launched
        with mock.patch.object(organize, "_launch", side_effect=launch_) as launch:
            self.launch = launch
            got = split.start(self.store, key, note, sessions=self.sessions)
        if launched.get("id"):
            self.live[launched["id"]] = "busy"
        return got

    def path(self):
        return pickup.worktree(self.store, {"key": "kandalf-split", "title": ""}, self.store.main_repo)

    def store_files(self):
        """Every file of the store but the ledger and git's, with its bytes."""
        return {str(f.relative_to(self.plans)): f.read_bytes() for f in sorted(self.plans.rglob("*"))
                if f.is_file() and not f.name.startswith(("board-pickup", "board-pending")) and ".git" not in f.parts}

    def later(self, seconds=jobs.SETTLE + 1):
        self.clock[0] += seconds

    def write(self, text):
        (self.path() / split.OUTPUT).write_text(text, encoding="utf-8")

    def ready(self, text=PROPOSAL):
        self.start()
        self.write(text)
        self.live["s1"] = "idle"
        self.later()
        return split.look(self.store, sessions=self.sessions)


class Story(Store):
    def test_the_story_is_read_with_its_sub_tasks_ticks_and_plan_doc(self):
        got = split.story(self.store, self.snap(), "PLN-72")
        self.assertEqual((got["key"], got["title"], got["doc"]), ("PLN-72", "Checkout", "active/2026-11-01-pay.md"))
        self.assertEqual(got["tasks"], [{"text": "card form", "done": False}, {"text": "refund API", "done": True},
                                        {"text": "receipts", "done": False}])
        self.assertEqual(got["text"], STORY)
        self.assertEqual(got["rev"], self.snap().index()["PLN-72"]["rev"])


class Prompt(Store):
    def prompt(self, note=None):
        return split.prompt(self.store, split.story(self.store, self.snap(), "PLN-72"), note)

    def test_it_gives_the_story_its_plan_doc_copy_its_sub_tasks_the_file_and_the_form(self):
        p = self.prompt()
        for phrase in ("PLN-72 Checkout", "Take card payments and refunds", "Domain: Payments",
                       "docs/plans/active/2026-11-01-pay.md", "- [ ] card form", "- [x] refund API", "- [ ] receipts",
                       "SPLIT.md", "## Title of the first sub-story", "word for word", "stays on the story",
                       "Don't read the app's code", "Don't edit any board or plan doc", "in the order"):
            self.assertIn(phrase, p)
        self.assertNotIn("The person asked", p)
        for other in ("PLN-70", "PLN-73", "PLN-80", "Dark mode"):
            self.assertNotIn(other, p)

    def test_the_note_is_passed_on(self):
        self.assertIn("The person asked: one per screen", self.prompt("one per screen"))

    def test_a_story_with_no_sub_tasks_or_plan_doc_says_neither(self):
        p = split.prompt(self.store, split.story(self.store, self.snap(), "PLN-73"), None)
        self.assertNotIn("Its sub-tasks", p)
        self.assertNotIn("plan doc is a copy", p)


class Parse(unittest.TestCase):
    def test_each_heading_is_a_sub_story_with_its_first_paragraph_and_its_sub_tasks(self):
        got = split.parse(PROPOSAL)
        self.assertEqual([s["title"] for s in got], ["Card form", "Refunds", "Receipts"])
        self.assertEqual(got[0]["description"], "Build the card form and take a payment with it. It checks the card as you type.")
        self.assertEqual(got[0]["tasks"], [{"text": "card form", "done": False}, {"text": "Validate the expiry date", "done": False}])
        self.assertEqual(got[1], {"title": "Refunds", "description": "Give money back.", "tasks": [{"text": "refund API", "done": False}]})
        self.assertEqual(got[2], {"title": "Receipts", "description": "", "tasks": []})

    def test_ticks_are_read(self):
        got = split.parse("## A\n- [x] done one\n* [X] done two\n  - [ ] open\n")
        self.assertEqual(got[0]["tasks"], [{"text": "done one", "done": True}, {"text": "done two", "done": True}, {"text": "open", "done": False}])

    def test_junk_is_ignored(self):
        text = ("Preamble.\n- [ ] a task before any heading\n# Top\n## One\n### a smaller heading\n- a plain bullet\n"
                "Words.\n\nA second paragraph.\n```\n## Not a heading\n- [ ] not a task\n```\n- [ ] real\n")
        got = split.parse(text)
        self.assertEqual(got, [{"title": "One", "description": "Words.", "tasks": [{"text": "real", "done": False}]}])

    def test_numbering_bold_backticks_and_a_full_stop_are_taken_off_a_title(self):
        self.assertEqual([s["title"] for s in split.parse("## 1. **Card `form`.**\n## 2) Refunds:\n")], ["Card form", "Refunds"])

    def test_no_heading_is_no_sub_story(self):
        self.assertEqual(split.parse(""), [])
        self.assertEqual(split.parse("I could not decide.\n- [ ] a\n"), [])


class Refusals(Store):
    def refused(self, key, *words):
        with self.assertRaises(split.Refused) as e:
            self.start(key)
        for w in words:
            self.assertIn(w, str(e.exception))
        self.assertIsNone(split.of(self.store))
        self.assertFalse(self.path().exists())

    def test_each_story_that_cannot_be_split_says_why_and_starts_nothing(self):
        self.refused("PLN-99", "PLN-99", "isn't on the boards")
        self.refused("PLN-95", "PLN-95", "archived")
        self.refused("PLN-71", "PLN-71", "part of PLN-70", "sub-story")
        self.refused("PLN-74", "PLN-74", "session")

    def test_a_story_with_a_job_in_the_pickup_queue_is_refused(self):
        with mock.patch.object(jobs.Ledger, "live", return_value=[{"key": "PLN-72"}]):
            self.refused("PLN-72", "PLN-72", "session")

    def test_one_split_runs_per_store(self):
        self.start()
        with self.assertRaises(split.Refused) as e:
            self.start("PLN-80")
        self.assertIn("A split of PLN-72 is already under way", str(e.exception))

    def test_a_sort_under_way_does_not_stop_a_split(self):
        with jobs.Ledger(self.store).change() as state:
            state["organize"] = {"state": "working", "started": 1, "stories": []}
        self.assertEqual(self.start()["state"], "working")

    def test_an_agent_that_cannot_run_is_refused_in_its_words(self):
        with mock.patch.object(split, "why_not", lambda store: "Split is off: no app."):
            self.refused("PLN-72", "Split is off: no app.")

    def test_why_not_needs_what_pickup_needs_and_a_permission_that_writes(self):
        with mock.patch.object(pickup, "cannot_run", lambda store, **kw: None):
            self.assertIsNone(WHY_NOT(self.store))
        with mock.patch.object(pickup, "cannot_run", lambda store, **kw: "the app folder isn't there."):
            self.assertEqual(WHY_NOT(self.store), "Split is off: the app folder isn't there.")
        (self.plans / "board.json").write_text(json.dumps({"app": "../app", "permission": "plan"}))
        with mock.patch.object(pickup, "cannot_run", lambda store, **kw: None):
            self.assertIn("permission is plan", WHY_NOT(stores.Store(self.plans)))


class Start(Store):
    def test_it_cuts_a_detached_worktree_with_a_copy_of_the_plan_doc_and_records_working(self):
        got = self.start(note="one per screen")
        path = self.path()
        self.assertEqual((got["state"], got["id"], got["key"]), ("working", "s1", "PLN-72"))
        self.assertEqual(git(path, "branch", "--show-current").strip(), "")
        self.assertFalse((path / "docs" / "plans").is_symlink())
        self.assertEqual((path / "docs" / "plans" / "active" / "2026-11-01-pay.md").read_text(), "# Pay\n")
        self.assertFalse((path / "docs" / "plans" / "build.md").exists())
        rec = split.of(self.store)
        self.assertEqual((rec["worktree"], rec["note"], rec["title"], rec["rev"]),
                         (str(path), "one per screen", "Checkout", self.snap().index()["PLN-72"]["rev"]))
        text, cwd = self.launch.call_args.args[3], self.launch.call_args.args[2]
        self.assertEqual(cwd, path)
        self.assertIn("one per screen", text)

    def test_a_branch_worktree_of_the_persons_at_the_path_survives(self):
        git(self.app, "worktree", "add", "-q", "-b", "mine", str(self.path()))
        (self.path() / "unsaved").write_text("work\n")
        got = self.start()
        self.assertEqual(got["state"], "need")
        self.assertIn(str(self.path()), got["why"])
        self.launch.assert_not_called()
        split.end(self.store, sessions=self.sessions)
        self.assertEqual((self.path() / "unsaved").read_text(), "work\n")
        self.assertEqual(git(self.path(), "branch", "--show-current").strip(), "mine")

    def test_the_store_is_byte_identical_whatever_the_session_writes(self):
        before = self.store_files()
        def scribble(cwd):
            (cwd / "docs" / "plans" / "build.md").write_text("scribbled\n")
            (cwd / "docs" / "plans" / "active" / "2026-11-01-pay.md").write_text("scribbled\n")
            (cwd / "README").write_text("scribbled\n")
        self.start(effect=scribble)
        self.write(PROPOSAL)
        self.live["s1"] = "idle"
        self.later()
        split.look(self.store, sessions=self.sessions)
        split.end(self.store, sessions=self.sessions)
        self.assertEqual(self.store_files(), before)
        self.assertEqual(git(self.plans, "status", "--porcelain"), "")

    def test_a_session_that_does_not_start_needs_you_and_leaves_no_worktree(self):
        got = self.start(launched={"state": "failed", "error": "no claude"})
        self.assertEqual((got["state"], got["why"]), ("need", "The session didn't start: no claude"))
        self.assertFalse(self.path().exists())


class Look(Store):
    def look(self):
        return split.look(self.store, sessions=self.sessions)

    def test_a_finished_session_s_file_makes_the_run_ready_with_moved_sub_tasks_marked_and_their_ticks(self):
        said = self.ready()
        rec = split.of(self.store)
        self.assertEqual(rec["state"], "ready")
        self.assertEqual([s["title"] for s in rec["stories"]], ["Card form", "Refunds", "Receipts"])
        self.assertEqual(rec["stories"][0]["tasks"], [{"text": "card form", "done": False, "moved": True},
                                                      {"text": "Validate the expiry date", "done": False, "moved": False}])
        self.assertEqual(rec["stories"][1]["tasks"], [{"text": "refund API", "done": True, "moved": True}])  # ticked as the story has it
        self.assertEqual(rec["stays"], ["receipts"])
        self.assertEqual(self.retired, ["s1"])
        self.assertFalse(self.path().exists())
        self.assertTrue(any("3 sub-stories ready" in line for line in said), said)

    def test_of_two_sub_tasks_alike_the_first_is_moved_and_the_second_stays(self):
        twin = "  - [ ] receipts\n    note one\n  - [x] receipts\n    note two"
        (self.plans / "build.md").write_text(BUILD.replace("  - [ ] receipts", twin))
        self.ready("## A\n- [x] receipts\n")
        rec = split.of(self.store)
        self.assertEqual(rec["stories"][0]["tasks"], [{"text": "receipts", "done": False, "moved": True}])
        self.assertEqual(rec["stays"], ["card form", "refund API", "receipts"])

    def test_a_moved_sub_task_matches_ignoring_case_spaces_and_a_full_stop_and_goes_to_the_first_that_lists_it(self):
        self.ready("## A\n- [ ] Card  Form.\n## B\n- [ ] card form\n- [ ] new\n")
        stories = split.of(self.store)["stories"]
        self.assertEqual(stories[0]["tasks"], [{"text": "card form", "done": False, "moved": True}])
        self.assertEqual(stories[1]["tasks"], [{"text": "new", "done": False, "moved": False}])

    def test_a_busy_or_just_started_session_is_left_alone(self):
        self.start()
        self.write(PROPOSAL)
        self.live["s1"] = "idle"
        self.look()
        self.assertEqual(split.of(self.store)["state"], "working")
        self.live["s1"] = "busy"
        self.later()
        self.look()
        self.assertEqual(split.of(self.store)["state"], "working")

    def test_a_file_with_no_sub_story_needs_you(self):
        self.ready("I could not decide.\n")
        rec = split.of(self.store)
        self.assertEqual((rec["state"], rec["why"]), ("need", "The agent proposed no sub-stories."))

    def test_a_finished_session_with_no_file_needs_you(self):
        self.start()
        self.live["s1"] = "idle"
        self.later()
        self.look()
        rec = split.of(self.store)
        self.assertEqual((rec["state"], rec["why"]), ("need", "The session finished without writing SPLIT.md."))

    def test_a_waiting_session_needs_you(self):
        self.start()
        self.live["s1"] = "waiting"
        self.later()
        self.look()
        rec = split.of(self.store)
        self.assertEqual(rec["state"], "need")
        self.assertIn("asked a question", rec["why"])
        self.assertFalse(self.path().exists())

    def test_a_lost_session_needs_you(self):
        self.start()
        del self.live["s1"]
        self.later()
        self.look()
        self.assertIn("gone", split.of(self.store)["why"])

    def test_ending_retires_the_session_removes_the_worktree_and_the_record(self):
        self.start()
        self.assertTrue(split.end(self.store, sessions=self.sessions))
        self.assertEqual(self.retired, ["s1"])
        self.assertFalse(self.path().exists())
        self.assertIsNone(split.of(self.store))

    def test_the_page_is_told_the_run(self):
        self.assertIsNone(split.summary(self.store))
        self.start(note="a note")
        got = split.summary(self.store)
        self.assertEqual({k: got[k] for k in ("state", "key", "title", "note")}, {"state": "working", "key": "PLN-72", "title": "Checkout", "note": "a note"})
        self.assertNotIn("stories", got)
        self.write(PROPOSAL)
        self.live["s1"] = "idle"
        self.later()
        self.look()
        got = split.summary(self.store)
        self.assertEqual((got["state"], len(got["stories"]), got["rev"]), ("ready", 3, self.snap().index()["PLN-72"]["rev"]))


ASKED = [{"title": "Card form", "description": "Build the card form.", "tasks": [{"text": "card form", "done": False}, {"text": "Validate the expiry", "done": False}]},
         {"title": "Refunds", "description": "Give money back.", "tasks": [{"text": "refund API", "done": False}]}]


class Create(Store):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(gitlog, "ensure_committer")
        p.start()
        self.addCleanup(p.stop)

    def rev(self, key="PLN-72"):
        return self.snap().index()[key]["rev"]

    def ready_run(self, key="PLN-72", rev=None, state="ready"):
        """A run of key's split as the look leaves it ready (rev: what it read of the story)."""
        with jobs.Ledger(self.store).change() as ledger_state:
            ledger_state["split"] = {"state": state, "started": 1, "key": key, "rev": rev or self.rev(key), "tasks": [],
                                     "stories": [], "worktree": None, "tidied": True}

    def create(self, stories=ASKED, key="PLN-72", expect=None):
        """Create, as the page sends it, for a run of key's split that is ready (one is put in the
        ledger when there is none for key), expecting the rev it read (expect, else the story's now)."""
        rev = expect or self.rev(key)
        if (split.of(self.store) or {}).get("key") != key:
            self.ready_run(key, rev)
        return serve.split_story(self.store, key, stories, rev)

    def flush(self):
        with gitlog.queue(self.store.root) as q:
            gitlog.flush(self.store, q)

    def test_sub_stories_get_the_next_keys_a_part_of_line_and_go_below_the_story_in_order(self):
        status, reply = self.create()
        self.assertEqual((status, reply), (200, {"into": "PLN-72", "created": ["PLN-96", "PLN-97"]}))  # past the archive's PLN-95; PLN-200 in prose doesn't count
        snap = self.snap()
        order = [t.key for t in snap.tiles("build.md")]
        self.assertEqual(order, ["PLN-70", "PLN-71", "PLN-72", "PLN-96", "PLN-97", "PLN-73", "PLN-74"])
        for key in ("PLN-96", "PLN-97"):
            self.assertEqual(snap.parent(key), "PLN-72")
            self.assertEqual(snap.tile(key).column, "To do")
            self.assertNotIn("Domain:", snap.tile(key).text)
            self.assertNotIn("Repos:", snap.tile(key).text)
        self.assertEqual(snap.tile("PLN-96").text, "- [ ] PLN-96 **Card form.** Part of PLN-72. Build the card form.\n"
                                                   "  - [ ] card form\n  - [ ] Validate the expiry")
        self.assertEqual(snap.tile("PLN-97").text, "- [ ] PLN-97 **Refunds.** Part of PLN-72. Give money back.\n  - [x] refund API\n    with a note under it")

    def test_a_second_create_is_refused_and_writes_nothing_more(self):
        self.ready_run()
        rev = self.rev()
        self.assertEqual(serve.split_story(self.store, "PLN-72", ASKED[1:], rev)[0], 200)  # moves refund API
        self.assertIsNone(split.of(self.store))
        after = self.store_files()
        self.ready_run("PLN-72", rev)
        status, reply = serve.split_story(self.store, "PLN-72", ASKED[1:], self.rev())  # the master's hash passes again
        self.assertEqual(status, 409, reply)
        with jobs.Ledger(self.store).change() as state:
            state.pop("split", None)
        status, reply = serve.split_story(self.store, "PLN-72", ASKED[1:], self.rev())
        self.assertEqual(status, 409)
        self.assertIn("no split of PLN-72 ready to create", reply["error"])
        self.assertEqual(self.store_files(), after)
        self.assertEqual((self.plans / "build.md").read_text().count("**Refunds.**"), 1)

    def test_two_identical_creates_make_one_set_of_sub_stories(self):
        self.ready_run()
        body = [{"title": "Docs", "description": "", "tasks": [{"text": "write them", "done": False}]}]
        first = serve.split_story(self.store, "PLN-72", body, self.rev())
        second = serve.split_story(self.store, "PLN-72", body, self.rev())  # no sub-task moved: the master's hash is the same
        self.assertEqual((first[0], second[0]), (200, 409))
        self.assertIn("PLN-72", second[1]["error"])
        self.assertEqual((self.plans / "build.md").read_text().count("**Docs.**"), 1)

    def test_a_create_while_the_split_is_working_or_for_another_story_is_refused(self):
        self.ready_run(state="working")
        status, reply = serve.split_story(self.store, "PLN-72", ASKED, self.rev())
        self.assertEqual(status, 409)
        self.assertIn("still at work", reply["error"])
        self.ready_run("PLN-80")
        status, reply = serve.split_story(self.store, "PLN-72", ASKED, self.rev())
        self.assertIn("no split of PLN-72 ready to create", reply["error"])
        self.assertEqual(split.of(self.store)["key"], "PLN-80")

    def test_of_two_sub_tasks_alike_only_the_first_moves_and_the_second_stays_with_its_note(self):
        twin = "  - [ ] receipts\n    note one\n  - [x] receipts\n    note two"
        (self.plans / "build.md").write_text(BUILD.replace("  - [ ] receipts", twin))
        self.create([{"title": "Receipts", "description": "", "tasks": [{"text": "receipts", "done": False}]}])
        snap = self.snap()
        self.assertEqual(snap.tile("PLN-96").text, "- [ ] PLN-96 **Receipts.** Part of PLN-72.\n  - [ ] receipts\n    note one")
        self.assertTrue(snap.tile("PLN-72").text.endswith("  - [ ] card form\n  - [x] refund API\n    with a note under it\n  - [x] receipts\n    note two"))

    def test_a_proposed_title_or_description_cannot_set_a_parent_a_link_or_a_blocker(self):
        self.create([{"title": "PLN-12: Card form. Part of PLN-70", "description": "Blocked by PLN-73 and PLN-80. Moves with PLN-71. Part of PLN-70. Takes cards.",
                      "tasks": []}])
        snap = self.snap()
        self.assertEqual(snap.tile("PLN-96").text, "- [ ] PLN-96 **Card form.** Part of PLN-72. Takes cards.")
        t = snap.tile("PLN-96")
        self.assertEqual((t.parent, t.blockers, t.links), ("PLN-72", [], []))
        self.assertEqual(self.create([{"title": "Part of PLN-70", "description": "", "tasks": []}])[0], 400)

    def test_moved_sub_tasks_leave_the_master_and_the_rest_stay_with_their_ticks(self):
        self.create()
        master = self.snap().tile("PLN-72").text
        self.assertEqual(master, "- [ ] PLN-72 P2 **Checkout.** Take card payments and refunds. See [the plan](active/2026-11-01-pay.md).\n"
                                 "  Domain: Payments\n  - [ ] receipts")

    def test_keys_are_never_reused(self):
        self.create()
        self.flush()
        status, reply = self.create([{"title": "More", "description": "", "tasks": []}], key="PLN-80")
        self.assertEqual(reply["created"], ["PLN-98"])
        self.assertIn("- [ ] PLN-98 **More.** Part of PLN-80.\n", (self.plans / "plan.md").read_text())

    def test_one_commit_with_the_subject_read_from_git_log(self):
        self.create()
        self.flush()
        subject = git(self.plans, "log", "-1", "--format=%s").strip()
        self.assertEqual(subject, "board: PLN-72 split into PLN-96, PLN-97")
        self.assertEqual(git(self.plans, "rev-list", "--count", "HEAD").strip(), "2")
        self.assertEqual(git(self.plans, "show", "--name-only", "--format=", "HEAD").split(), ["build.md"])

    def test_a_title_and_description_are_written_on_one_line_without_bold_breaking(self):
        self.create([{"title": "  Card **form**\n now ", "description": "Line one.\n  Line two.", "tasks": []}])
        self.assertIn("- [ ] PLN-96 **Card form now.** Part of PLN-72. Line one. Line two.\n", (self.plans / "build.md").read_text())

    def test_a_story_changed_since_it_was_read_refuses_with_changed_and_writes_nothing(self):
        before, rev = self.store_files(), self.rev()
        (self.plans / "build.md").write_text(BUILD.replace("  - [ ] receipts", "  - [ ] receipts\n  - [ ] invoices"))
        changed = self.store_files()
        status, reply = self.create(expect=rev)
        self.assertEqual((status, reply["changed"]), (409, ["PLN-72"]))
        self.assertIn("changed since", reply["error"])
        self.assertEqual(self.store_files(), changed)
        self.assertNotEqual(changed, before)

    def refused(self, key, *words, stories=ASKED, status=409):
        before = self.store_files()
        got, reply = serve.split_story(self.store, key, stories, None)
        self.assertEqual(got, status, reply)
        for w in words:
            self.assertIn(w, reply["error"])
        self.assertEqual(self.store_files(), before)

    def test_each_refusal_says_why_and_writes_nothing(self):
        self.refused("PLN-99", "PLN-99", "isn't on the boards")
        self.refused("PLN-95", "PLN-95", "archived")
        self.refused("PLN-71", "PLN-71", "part of PLN-70")
        self.refused("PLN-74", "PLN-74", "session")
        self.refused("PLN-72", "Tick a sub-story", stories=[], status=400)
        self.refused("PLN-72", "needs a title", stories=[{"title": " ", "description": "", "tasks": []}], status=400)
        self.refused("PLN-72", "Say which", stories="nope", status=400)
        with mock.patch.object(jobs.Ledger, "live", return_value=[{"key": "PLN-72"}]):
            self.refused("PLN-72", "session")


class Server(Store):
    def setUp(self):
        super().setUp()
        for target, name, value in ((serve, "SESSIONS", self.sessions), (pickup, "why_off", lambda store: None),
                                    (gitlog, "ensure_committer", lambda *a: None)):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(serve.SPLIT_ASKED.clear)

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
            status, reply = self.call("POST", "/api/split", {"key": "PLN-72", "note": "per screen"})
        self.assertEqual(status, 200, reply)
        self.assertEqual((reply["split"]["state"], reply["split"]["key"]), ("working", "PLN-72"))
        self.assertTrue(serve.SPLIT_ASKED.is_set())
        status, reply = self.call("POST", "/api/split", {"key": "PLN-80"})
        self.assertEqual(status, 409)
        self.assertIn("already under way", reply["error"])
        status, reply = self.call("GET", "/api/pickup")
        self.assertEqual(reply["split"]["note"], "per screen")
        status, reply = self.call("POST", "/api/split/end", {})
        self.assertEqual((status, reply["split"]), (200, None))
        self.assertFalse(self.path().exists())

    def test_create_writes_the_ticked_sub_stories_ends_the_run_and_answers_with_the_board(self):
        self.ready()
        rev = split.summary(self.store)["rev"]
        status, reply = self.call("POST", "/api/split/create?file=build.md", {"key": "PLN-72", "expect": rev, "stories": ASKED[:1]})
        self.assertEqual(status, 200, reply)
        self.assertEqual((reply["created"], reply["into"], reply["split"]), (["PLN-96"], "PLN-72", None))
        self.assertIn("PLN-96 **Card form.** Part of PLN-72.", reply["text"])
        self.assertIsNone(split.of(self.store))

    def test_create_refused_as_changed_keeps_the_proposal(self):
        self.ready()
        status, reply = self.call("POST", "/api/split/create?file=build.md", {"key": "PLN-72", "expect": "stale", "stories": ASKED})
        self.assertEqual((status, reply["changed"]), (409, ["PLN-72"]))
        self.assertEqual(split.of(self.store)["state"], "ready")

    def test_the_watch_s_turn_looks_when_asked(self):
        self.start()
        self.write(PROPOSAL)
        self.live["s1"] = "idle"
        self.later()
        serve.SPLIT_ASKED.set()
        serve.watch_split_turn(self.store, {})
        self.assertEqual(split.of(self.store)["state"], "ready")


if __name__ == "__main__":
    unittest.main()
