#!/usr/bin/env python3
"""Combining stories into one: tiles.combine (the text), serve.combine_stories (the boards).

    python3 -m unittest test_combine
"""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kandalf import gitlog
from kandalf import serve
from kandalf import store as stores
from kandalf import tiles as reader

SURVIVOR = """- [ ] PLN-72 P2 ~4h **Main.** Part of PLN-70. Blocked by PLN-30, PLN-73.
  Does main things.
  Moves with PLN-80.
  Domain: Billing
  Model: sonnet
  - [ ] one
  - [x] two"""

OTHER = """- [ ] PLN-73 **Other.** See [plan](active/x.md) Blocked by PLN-31.
  Does other.

  Second paragraph. Part of PLN-71.
  Domain: Admin
  Repos: api
  Model: opus
  No pickup
  Moves with PLN-72, PLN-81.
  **Ready for review:** it is.
  - [x] three
    more
  - [ ] four"""

THIRD = """- [ ] PLN-75 **Third.**
  Repos: web
  - [ ] five"""


class CombineText(unittest.TestCase):
    def setUp(self):
        self.store = stores.Store(Path(tempfile.mkdtemp()).resolve())

    def combined(self, survivor=SURVIVOR, others=(OTHER, THIRD)):
        return reader.combine(self.store, survivor, list(others))

    def test_the_survivor_keeps_its_title_and_gets_each_description_as_a_paragraph_in_order(self):
        text = self.combined()
        self.assertTrue(text.startswith("- [ ] PLN-72 P2 ~4h **Main.** Part of PLN-70."))
        lines = text.split("\n")
        i, j = lines.index("  From PLN-73 (Other): See [plan](active/x.md)"), lines.index("  From PLN-75 (Third).")
        self.assertLess(i, j)
        self.assertEqual(lines[i - 1], "")
        self.assertEqual(lines[i + 1:i + 4], ["  Does other.", "", "  Second paragraph."])
        self.assertEqual(lines[j - 1], "")

    def test_sub_tasks_follow_the_survivors_in_key_order_with_their_ticks(self):
        tasks = [x for x in self.combined().split("\n") if "- [" in x[:8] and x.startswith("  ")]
        self.assertEqual(tasks, ["  - [ ] one", "  - [x] two", "  - [x] three", "  - [ ] four", "  - [ ] five"])
        self.assertIn("  - [x] three\n    more\n  - [ ] four", self.combined())

    def test_blocked_by_is_the_union_without_the_stories_combined(self):
        tile = next(t for t in reader.parse(self.store, "## To do\n\n" + self.combined()))
        self.assertEqual(tile.blockers, ["PLN-30", "PLN-31"])

    def test_moves_with_is_the_union_without_themselves(self):
        tile = next(t for t in reader.parse(self.store, "## To do\n\n" + self.combined()))
        self.assertEqual(tile.links, ["PLN-80", "PLN-81"])

    def test_part_of_stays_the_survivors_and_the_others_are_dropped(self):
        tile = next(t for t in reader.parse(self.store, "## To do\n\n" + self.combined()))
        self.assertEqual(tile.parent, "PLN-70")
        plain = SURVIVOR.replace(" Part of PLN-70.", "")
        tile = next(t for t in reader.parse(self.store, "## To do\n\n" + self.combined(plain)))
        self.assertIsNone(tile.parent)  # PLN-73's Part of PLN-71 is not carried over

    def test_blocked_by_and_moves_with_get_a_line_when_the_survivor_had_none(self):
        text = self.combined("- [ ] PLN-72 **Main.**\n  Words.", [OTHER])
        self.assertIn("  Blocked by PLN-31.\n", text)
        self.assertIn("  Moves with PLN-81.\n", text)

    def test_the_repos_the_caller_works_out_are_written_as_the_survivors_line(self):
        mine = SURVIVOR.replace("  Domain: Billing\n", "  Repos: db\n")
        text = reader.combine(self.store, mine, [OTHER], repos=["db", "api"])
        self.assertEqual(reader.repos(text), ["db", "api"])
        self.assertEqual(text.count("Repos:"), 1)
        self.assertEqual(reader.repos(reader.combine(self.store, SURVIVOR, [OTHER], repos=["myapp", "api"])), ["myapp", "api"])
        self.assertIsNone(reader.repos(self.combined()))  # none asked: no line is written

    def test_domain_is_the_survivors_else_the_first_others_and_never_a_sub_storys(self):
        self.assertEqual(reader.domain(self.combined()), "Billing")
        self.assertEqual(reader.domain(self.combined("- [ ] PLN-72 **Main.**", [OTHER, THIRD])), "Admin")
        sub = reader.combine(self.store, "- [ ] PLN-72 **Main.** Part of PLN-70.", [OTHER], has_master=True)
        self.assertIsNone(reader.domain(sub))

    def test_a_description_in_a_code_fence_stays_whole_in_its_paragraph(self):
        fenced = "- [ ] PLN-73 **O.**\n  Run:\n  ```\n  - [ ] not a task\n  Domain: nope\n  ```\n  - [ ] real"
        text = reader.combine(self.store, "- [ ] PLN-72 **Main.**", [fenced])
        self.assertIn("  ```\n  - [ ] not a task\n  Domain: nope\n  ```\n", text)
        self.assertTrue(text.endswith("  - [ ] real"))

    def test_sub_tasks_go_where_a_story_without_any_has_its_own_before_status_lines(self):
        options = "- [ ] PLN-72 **Main.**\n  Words.\n  **Options 2026-11-20:** recommends A.\n  - **A. One.** x\n  - **B. Two.** y\n  **Status 2026-11-21 10:00:** going"
        text = reader.combine(self.store, options, [THIRD])
        lines = text.split("\n")
        self.assertLess(lines.index("  - **B. Two.** y"), lines.index("  - [ ] five"))
        self.assertLess(lines.index("  - [ ] five"), lines.index("  **Status 2026-11-21 10:00:** going"))
        self.assertEqual(reader.options(text)["items"][1]["letter"], "B")

    def test_model_and_other_run_settings_are_the_survivors_and_the_others_are_left_out(self):
        text = self.combined()
        self.assertEqual(reader.model(text), "sonnet")
        self.assertNotIn("opus", text)
        self.assertNotIn("Ready for review", text)
        self.assertFalse(reader.opted_out(text))

    def test_a_story_with_no_description_still_says_where_it_came_from(self):
        self.assertIn("\n  From PLN-75 (Third).\n", self.combined(SURVIVOR, [THIRD]))

    def test_the_absorbed_story_says_where_it_went(self):
        note = reader.combined_note(self.store, OTHER, "PLN-72")
        self.assertIn("  Combined into PLN-72.\n", note)
        self.assertLess(note.index("Combined into"), note.index("- [x] three"))
        self.assertNotIn("Part of", note)
        self.assertIn("Blocked by PLN-31", note)

    def test_other_tiles_are_repointed_but_prose_is_left_alone(self):
        md = "- [ ] PLN-90 **X.** Part of PLN-73.\n  Blocked by PLN-73, PLN-72 and PLN-30. See PLN-75.\n  Moves with PLN-75."
        new = reader.repoint(self.store, md, {"PLN-73", "PLN-75"}, "PLN-72")
        self.assertEqual(new, "- [ ] PLN-90 **X.** Part of PLN-72.\n  Blocked by PLN-72, PLN-30. See PLN-75.\n  Moves with PLN-72.")


BUILD = """# Build

## To do

- [ ] PLN-70 **Family.**
- [ ] PLN-71 **Kid of other.** Part of PLN-73.
- [ ] PLN-72 **Main.** Blocked by PLN-30.
  Does main things.
  - [ ] one
- [ ] PLN-76 **Waiter.** Blocked by PLN-73.
  Moves with PLN-75. Mentions PLN-73 in prose.

## In flight

- [ ] PLN-74 **Running.**
  Claude session `ab12cd34`: `Branch `feat`, worktree `w``.
"""

PLAN = """# Plan

## Plan

- [ ] PLN-73 **Other.**
  Does other.
  - [ ] four
- [ ] PLN-75 **Third.**
"""

ARCHIVE = """# Archive

## Archived

- [x] PLN-90 **Old.** Blocked by PLN-73.
  Archived 2026-11-01.
"""


class CombineRoute(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp()).resolve()
        (self.dir / "archive").mkdir()
        self.files = {"build.md": BUILD, "plan.md": PLAN, "archive/archived.md": ARCHIVE}
        for name, text in self.files.items():
            (self.dir / name).write_text(text)
        self.store = stores.Store(self.dir)
        patch = mock.patch.object(serve.gitlog, "record")
        self.record = patch.start()
        self.addCleanup(patch.stop)

    def text(self, name):
        return (self.dir / name).read_text()

    def untouched(self):
        for name, text in self.files.items():
            self.assertEqual(self.text(name), text)

    def revs(self, *keys):
        index = reader.Snapshot.read(self.store).index()
        return {k: index[k]["rev"] for k in keys}

    def test_the_others_are_archived_the_survivor_merged_and_references_repointed_in_one_commit(self):
        status, reply = serve.combine_stories(self.store, ["PLN-72", "PLN-73", "PLN-75"], "PLN-72", self.revs("PLN-72", "PLN-73"))
        self.assertEqual((status, reply), (200, {"combined": ["PLN-73", "PLN-75"], "into": "PLN-72"}))
        build, plan, log = self.text("build.md"), self.text("plan.md"), self.text("archive/archived.md")
        self.assertIn("  From PLN-73 (Other): Does other.", build)
        self.assertIn("  - [ ] one\n  - [ ] four", build)
        self.assertNotIn("PLN-73 **Other", plan)
        self.assertNotIn("PLN-75 **Third", plan)
        self.assertEqual(log.count("Combined into PLN-72."), 2)
        self.assertLess(log.index("PLN-73 **Other"), log.index("PLN-75 **Third"))
        self.assertLess(log.index("PLN-75 **Third"), log.index("PLN-90"))
        self.assertIn("PLN-71 **Kid of other.** Part of PLN-72.", build)
        self.assertIn("PLN-76 **Waiter.** Blocked by PLN-72.\n  Moves with PLN-72. Mentions PLN-73 in prose.", build)
        self.assertIn("PLN-90 **Old.** Blocked by PLN-72.", log)
        self.record.assert_called_once()
        self.assertEqual(self.record.call_args[0][2], "board: PLN-73, PLN-75 combined into PLN-72")
        self.assertEqual({p.name for p in self.record.call_args[0][1]}, {"build.md", "plan.md", "archived.md"})

    def test_the_survivor_can_be_any_of_them(self):
        status, _ = serve.combine_stories(self.store, ["PLN-72", "PLN-73"], "PLN-73")
        self.assertEqual(status, 200)
        self.assertIn("From PLN-72 (Main)", self.text("plan.md"))
        self.assertIn("Combined into PLN-73.", self.text("archive/archived.md"))

    def test_a_tile_changed_since_the_page_read_it_refuses_with_changed_and_writes_nothing(self):
        revs = self.revs("PLN-72", "PLN-73")
        (self.dir / "plan.md").write_text(PLAN.replace("Does other.", "Does more."))
        status, reply = serve.combine_stories(self.store, ["PLN-72", "PLN-73"], "PLN-72", revs)
        self.assertEqual((status, reply["changed"]), (409, ["PLN-73"]))
        self.assertEqual(self.text("build.md"), BUILD)
        self.assertEqual(self.text("archive/archived.md"), ARCHIVE)
        self.record.assert_not_called()

    def refused(self, keys, into, *words, status=None):
        got, reply = serve.combine_stories(self.store, keys, into)
        self.assertEqual(got, status or 409)
        for w in words:
            self.assertIn(w, reply["error"])
        self.untouched()
        self.record.assert_not_called()

    def test_each_refusal_says_why_and_writes_nothing(self):
        self.refused(["PLN-72"], "PLN-72", "at least two", status=400)
        self.refused(["PLN-72", "PLN-72"], "PLN-72", "at least two", status=400)
        self.refused(["PLN-72", "PLN-73"], "PLN-75", "has to be one", status=400)
        self.refused(["PLN-72", "PLN-90"], "PLN-72", "PLN-90", "archived")
        self.refused(["PLN-72", "PLN-74"], "PLN-72", "PLN-74", "session")
        self.refused(["PLN-72", "PLN-99"], "PLN-72", "PLN-99")
        self.refused(["PLN-71", "PLN-73"], "PLN-71", "PLN-71", "PLN-73", "master")
        self.refused(["PLN-73", "PLN-71"], "PLN-73", "PLN-71", "PLN-73", "master")

    def test_a_story_with_a_job_in_the_pickup_queue_is_refused(self):
        with mock.patch.object(serve.ledger.Ledger, "live", return_value=[{"key": "PLN-73"}]):
            self.refused(["PLN-72", "PLN-73"], "PLN-72", "PLN-73")


class CombineInGit(unittest.TestCase):
    """The route on a real git store, read back from what was committed."""
    FILES = {
        "build.md": "# Build\n\n## To do\n\n- [ ] PLN-70 **Master.**\n- [ ] PLN-71 **Kid.** Part of PLN-70.\n  Domain: X\n"
                    "- [ ] PLN-72 **First.** Part of PLN-70.\n  - [ ] a\n- [ ] PLN-73 **Second.** Part of PLN-70.\n  Repos: api, web\n"
                    "- [ ] PLN-74 **Loose.**\n- [ ] PLN-75 **Other loose.**\n  Repos: api\n- [ ] PLN-76 **Third.** Part of PLN-70.\n",
        "plan.md": "# Plan\n\n## Plan\n",
        "archive/archived.md": "# Archive\n\n## Archived\n",
    }

    def setUp(self):
        top = Path(tempfile.mkdtemp()).resolve()
        self.dir = top / "plans"
        (self.dir / "archive").mkdir(parents=True)
        for name, text in self.FILES.items():
            (self.dir / name).write_text(text)
        (self.dir / "board.json").write_text('{"app": "../myapp", "repos": {"api": "../api", "web": "../web"}}')
        for args in (["init", "-q", "-b", "main"], ["config", "user.name", "t"], ["config", "user.email", "t@t"], ["add", "-A"], ["commit", "-qm", "init"]):
            subprocess.run(["git", "-C", str(self.dir), *args], check=True, capture_output=True)
        self.store = stores.Store(self.dir)
        patch = mock.patch.object(gitlog, "ensure_committer")
        patch.start()
        self.addCleanup(patch.stop)

    def combine(self, keys, into):
        status, reply = serve.combine_stories(self.store, keys, into)
        self.assertEqual(status, 200, reply)
        with gitlog.queue(self.store.root) as q:
            gitlog.flush(self.store, q)

    def snap(self):
        return reader.Snapshot.read(self.store)

    def test_the_commit_subject_is_the_one_asked_for_and_the_others_are_in_key_order(self):
        self.combine(["PLN-75", "PLN-74", "PLN-72", "PLN-73"], "PLN-72")
        subject = subprocess.run(["git", "-C", str(self.dir), "log", "-1", "--format=%s"], capture_output=True, text=True).stdout.strip()
        self.assertEqual(subject, "board: PLN-73, PLN-74, PLN-75 combined into PLN-72")
        text = self.snap().tile("PLN-72").text
        self.assertLess(text.index("From PLN-73"), text.index("From PLN-74"))
        self.assertLess(text.index("From PLN-74"), text.index("From PLN-75"))

    def test_a_survivor_without_repos_takes_the_main_repo_with_the_others_repos(self):
        self.combine(["PLN-74", "PLN-73"], "PLN-74")
        self.assertEqual(reader.repos(self.snap().tile("PLN-74").text), ["myapp", "api", "web"])

    def test_no_repos_line_is_written_when_the_survivor_already_works_everywhere(self):
        self.combine(["PLN-74", "PLN-72"], "PLN-74")  # both work in the main repo
        self.assertNotIn("Repos:", self.snap().tile("PLN-74").text)
        self.combine(["PLN-74", "PLN-75"], "PLN-74")
        self.assertEqual(reader.repos(self.snap().tile("PLN-74").text), ["myapp", "api"])

    def test_a_sub_story_survivor_inherits_its_masters_repos_and_never_gets_a_domain_line(self):
        self.combine(["PLN-72", "PLN-71"], "PLN-72")
        text = self.snap().tile("PLN-72").text
        self.assertNotIn("Repos:", text)
        self.assertNotIn("Domain:", text)

    def test_a_master_does_not_count_an_absorbed_sub_story(self):
        self.combine(["PLN-72", "PLN-73"], "PLN-72")
        snap = self.snap()
        self.assertEqual(sorted(k for k, t in snap.index().items() if t["parent"] == "PLN-70"), ["PLN-71", "PLN-72", "PLN-76"])
        self.assertIn("Combined into PLN-72.", snap.tile("PLN-73").text)

    def test_the_archive_is_written_before_the_boards(self):
        order = []
        real = serve.write_atomic
        with mock.patch.object(serve, "write_atomic", side_effect=lambda p, t: (order.append(Path(p).name), real(p, t))):
            serve.combine_stories(self.store, ["PLN-72", "PLN-73"], "PLN-72")
        self.assertEqual(order[0], "archived.md")


if __name__ == "__main__":
    unittest.main()
