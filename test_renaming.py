#!/usr/bin/env python3
"""The move of a store from plans.md and backlog.md to build.md and plan.md (kandalf/renaming.py):
when it may happen, what it changes, the one commit it makes, and what the sessions, the page
and the edit hook are told.

    python3 -m unittest test_renaming
"""

import datetime
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

import re

from kandalf import checkhook, gitlog, init, jobs, pickup, renaming, serve
from kandalf import store as stores
from test_pickup import Fake

TODAY = datetime.date(2026, 11, 24)
DOC = "active/2026-11-20-pln-08-search.md"
DOC_TEXT = """# PLN-08: Search

Filed on [the Plan board](../backlog.md#feature), built from [`plans.md`](../plans.md).
See also [the plan](../active/plans.md-notes.md) and myplans.md.
"""
ARCHIVED_DOC = "archive/2026-10-01-pln-02-login.md"
ARCHIVED_TEXT = "# PLN-02: Login\n\nIt was on [plans.md](../plans.md).\n"


def git(path, *args) -> str:
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout


def make_store(top: Path, *, repo=True, app=False) -> stores.Store:
    """A store made as kandalf init makes one on the old names, with the example story, a plan doc
    in active/ and one in archive/ that link the boards, and, when repo, a git repo with it all
    committed."""
    folder = top / "plans"
    cfg = {"name": "Shop", "key": "PLN", "link": "plans", "trunk": "main"}
    if app:
        (top / "app").mkdir()
        cfg["app"] = "../app"
    init.write_store(folder, cfg, True, TODAY)
    (folder / DOC).write_text(DOC_TEXT, encoding="utf-8")
    (folder / ARCHIVED_DOC).write_text(ARCHIVED_TEXT, encoding="utf-8")
    if repo:
        git(folder, "init", "-q", "-b", "main")
        git(folder, "config", "user.name", "t")
        git(folder, "config", "user.email", "t@t")
        git(folder, "add", "-A")
        git(folder, "commit", "-qm", "board: a new store")
    return stores.Store(folder)


def files_of(folder: Path) -> dict:
    """{path: text} of every file in a store but its .git and, in a store that isn't a git repo,
    the ledger's own files beside it (.board-pickup.json and its lock)."""
    return {p.relative_to(folder).as_posix(): p.read_text(encoding="utf-8")
            for p in sorted(folder.rglob("*")) if p.is_file() and ".git" not in p.relative_to(folder).parts
            and not p.name.startswith(".board-pickup.")}


def commit_now(store, paths, message, subject=False):
    """gitlog.record without the background committer, then the commit it would make, made now."""
    with mock.patch.object(gitlog, "ensure_committer"):
        gitlog.record(store, paths, message, subject=subject)
    with gitlog.queue(store.root) as q:
        gitlog.flush(store, q)


def listing(sessions=None):
    """A sessions adapter whose listing is sessions (None: they can't be listed), counting its listings."""
    asked = []
    adapter = pickup.Sessions(sessions=lambda: asked.append(1) or sessions)
    adapter.asked = asked
    return adapter


IDLE = listing({})


def setUpModule():
    """No test here starts the background committer: a temp store can be gone before it wakes."""
    global _no_committer
    _no_committer = mock.patch.object(gitlog, "ensure_committer")
    _no_committer.start()


def tearDownModule():
    _no_committer.stop()
# A markdown link or reference definition whose target is an old board file.
OLD_LINK = re.compile(r"(\]\(<?|^ {0,3}\[[^\]]+\]:\s*<?)(\./|(\.\./)*)(plans|backlog)\.md\b", re.M)


class Base(unittest.TestCase):
    def setUp(self):
        self.top = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.top, True)

    def jobs(self, st, *tracked, drafts=()):
        jobs.save(st, {"inflight": [], "jobs": [dict(j) for j in tracked], "drafts": [dict(d) for d in drafts]})


class Relink(unittest.TestCase):
    def test_a_link_to_a_board_points_at_its_new_file(self):
        self.assertEqual(renaming.relink("Read [the board](plans.md) first."), "Read [the board](build.md) first.")
        self.assertEqual(renaming.relink("[kinds](./backlog.md#feature)"), "[kinds](./plan.md#feature)")
        self.assertEqual(renaming.relink("[up](../plans.md) and [two up](../../backlog.md)"),
                         "[up](../build.md) and [two up](../../plan.md)")
        self.assertEqual(renaming.relink('[t](plans.md "The board")'), '[t](build.md "The board")')
        self.assertEqual(renaming.relink("[board]: ../plans.md\n"), "[board]: ../build.md\n")

    def test_an_angle_bracket_target_is_relinked(self):
        self.assertEqual(renaming.relink("[x](<plans.md>) and [y](<../backlog.md#kinds>)"), "[x](<build.md>) and [y](<../plan.md#kinds>)")
        self.assertEqual(renaming.relink("[ref]: <plans.md>\n"), "[ref]: <build.md>\n")
        self.assertEqual(renaming.relink("[x](<other/plans.md>)"), "[x](<other/plans.md>)")

    def test_link_text_that_is_the_old_file_name_is_renamed(self):
        self.assertEqual(renaming.relink("[plans.md](plans.md)"), "[build.md](build.md)")
        self.assertEqual(renaming.relink("[`backlog.md`](../backlog.md)"), "[`plan.md`](../plan.md)")
        self.assertEqual(renaming.relink("[the plans.md board](plans.md)"), "[the plans.md board](build.md)")

    def test_prose_code_spans_and_other_files_are_left_alone(self):
        for text in ("[the plan](active/plans.md-notes.md)", "myplans.md and plans.md in a sentence",
                     "a code span `plans.md` and `[x](plans.md)`", "[theirs](../other/plans.md)",
                     "[x](plans.md.bak)", "[x](https://example.com/plans.md)",
                     "```\n[x](plans.md)\n```\n", "~~~md\n[x](backlog.md)\n~~~\n"):
            self.assertEqual(renaming.relink(text), text, text)

    def test_a_text_with_nothing_to_change_comes_back_identical(self):
        text = init.from_template("archived.md", {"name": "Shop", "key": "PLN", "link": "plans", "trunk": "main"})
        self.assertIs(renaming.relink(text), text)
        self.assertEqual(renaming.relink(""), "")


class Blocked(Base):
    def test_a_store_with_no_session_is_free_to_move_without_listing_them(self):
        st = make_store(self.top, repo=False)
        self.jobs(st, {"key": "PLN-41", "state": "waiting"}, {"key": "PLN-42", "state": "paused", "id": "s9"},
                  drafts=[{"key": "PLN-30", "state": "ready", "id": "d1"}])
        sessions = listing(None)
        self.assertIsNone(renaming.blocked(st, sessions=sessions))
        self.assertEqual(sessions.asked, [])

    def test_a_busy_session_blocks_and_is_named(self):
        st = make_store(self.top, repo=False)
        self.jobs(st, {"key": "PLN-40", "state": "idle", "id": "s1"}, {"key": "PLN-41", "state": "started", "id": "s2"},
                  {"key": "PLN-42", "state": "started", "id": "s3"})
        sessions = listing({"s1": "idle", "s2": "busy", "s3": "busy"})
        self.assertEqual(renaming.blocked(st, sessions=sessions),
                         "These boards will be renamed Plan and Build once PLN-41's session finishes its turn.")
        self.assertEqual(len(sessions.asked), 1)

    def test_an_idle_or_waiting_session_does_not_block(self):
        st = make_store(self.top, repo=False)
        self.jobs(st, {"key": "PLN-40", "state": "idle", "id": "s1"}, {"key": "PLN-41", "state": "started", "id": "s2"},
                  {"key": "PLN-42", "state": "lost", "id": "s3"})
        self.assertIsNone(renaming.blocked(st, sessions=listing({"s1": "idle", "s2": "waiting"})))

    def test_sessions_that_cannot_be_listed_block_when_a_job_has_one(self):
        st = make_store(self.top, repo=False)
        self.jobs(st, {"key": "PLN-41", "state": "idle", "id": "s1"})
        self.assertEqual(renaming.blocked(st, sessions=listing(None)),
                         "These boards will be renamed Plan and Build once the agent's sessions can be listed.")

    def test_a_working_draft_s_busy_session_blocks(self):
        st = make_store(self.top, repo=False)
        self.jobs(st, {"key": "PLN-41", "state": "idle", "id": "s1"}, drafts=[{"key": "PLN-30", "state": "working", "id": "d1"}])
        self.assertEqual(renaming.blocked(st, sessions=listing({"s1": "idle", "d1": "busy"})),
                         "These boards will be renamed Plan and Build once PLN-30's session finishes its turn.")

    def test_a_build_md_of_the_store_s_own_is_in_the_way(self):
        st = make_store(self.top)
        (st.dir / "build.md").write_text("# My build notes\n\nNot a board.\n", encoding="utf-8")
        self.assertEqual((st.boards, st.strays, renaming.due(st)), (("plans.md", "backlog.md"), [], True))
        sentence = "build.md is in the way: these boards can't be renamed Plan and Build until it is moved or renamed."
        self.assertEqual(renaming.blocked(st, sessions=IDLE), sentence)
        (st.dir / "plan.md").write_text("# Mine too\n", encoding="utf-8")
        self.assertEqual(renaming.blocked(st, sessions=IDLE), sentence)  # both in the way: build.md is named
        self.assertEqual(renaming.notice(st, sessions=IDLE), {"waiting": sentence})
        before, status = files_of(st.dir), git(st.dir, "status", "--porcelain")
        record = mock.Mock()
        self.assertEqual(renaming.move(st, sessions=IDLE, record=record), [])
        self.assertEqual((files_of(st.dir), git(st.dir, "status", "--porcelain")), (before, status))
        record.assert_not_called()
        self.assertNotIn("renamed", jobs.load(st))

    def test_a_plan_md_of_the_store_s_own_is_in_the_way(self):
        st = make_store(self.top, repo=False)
        (st.dir / "plan.md").write_text("# My plan\n\nNot a board.\n", encoding="utf-8")
        self.jobs(st, {"key": "PLN-41", "state": "started", "id": "s1"})
        sessions = listing({"s1": "busy"})
        self.assertEqual(renaming.blocked(st, sessions=sessions),
                         "plan.md is in the way: these boards can't be renamed Plan and Build until it is moved or renamed.")
        self.assertEqual(sessions.asked, [])
        self.assertTrue(renaming.due(st))
        before = files_of(st.dir)
        record = mock.Mock()
        self.assertEqual(renaming.move(st, sessions=sessions, record=record), [])
        self.assertEqual(files_of(st.dir), before)
        self.assertEqual(st.boards, ("plans.md", "backlog.md"))


class Move(Base):
    def test_a_store_is_moved_in_one_commit_with_its_links_and_notes(self):
        st = make_store(self.top, app=True)
        app = self.top / "app"
        (app / "AGENTS.md").write_text("# App\n\n<!-- kandalf -->\n## Kandalf board\n\nAn old note.\n<!-- /kandalf -->\n",
                                       encoding="utf-8")
        board = st.dir / "plans.md"
        text = board.read_text(encoding="utf-8")
        rule = next(line for line in text.split("\n") if line.startswith("- **Drafting options:**"))
        board.write_text(text.replace(rule + "\n", "").replace("This file is the board", "This file is our board"), encoding="utf-8")
        git(st.dir, "commit", "-qam", "board: an older header, edited")
        lines = renaming.move(st, sessions=IDLE, record=commit_now)
        self.assertTrue(lines)
        self.assertEqual(st.boards, ("build.md", "plan.md"))
        self.assertFalse((st.dir / "plans.md").exists() or (st.dir / "backlog.md").exists())
        shown = git(st.dir, "log", "--name-status", "-M", "-1", "--format=%s").strip().split("\n")
        self.assertEqual(shown[0], "board: plans.md → build.md, backlog.md → plan.md")
        changes = sorted(tuple(line.split("\t")) for line in shown[1:] if line)
        self.assertEqual([c[1:] for c in changes if c[0].startswith("R")], [("backlog.md", "plan.md"), ("plans.md", "build.md")])
        self.assertEqual(sorted(c[1] for c in changes if c[0] == "M"), sorted([ARCHIVED_DOC, DOC]))
        self.assertEqual(len(changes), 4)
        self.assertEqual(git(st.dir, "status", "--porcelain"), "")
        self.assertEqual(git(st.dir, "rev-list", "--count", "HEAD"), "3\n")  # the store, the older header, the move
        build = (st.dir / "build.md").read_text(encoding="utf-8")
        self.assertIn("- **Drafting options:**", build)  # the notes update added the rule the header lacked
        self.assertIn("This file is our board", build)  # an edited header keeps its words, with its links relinked
        self.assertIn("on the backlog board, [plan.md](plan.md)", build)
        self.assertTrue((st.dir / "plan.md").read_text(encoding="utf-8").startswith(init.board_headers(st)["plan"][1]))
        self.assertIn("[the Plan board](../plan.md#feature), built from [`build.md`](../build.md)", (st.dir / DOC).read_text(encoding="utf-8"))
        self.assertIn("[the plan](../active/plans.md-notes.md) and myplans.md", (st.dir / DOC).read_text(encoding="utf-8"))
        self.assertEqual((st.dir / ARCHIVED_DOC).read_text(encoding="utf-8"), "# PLN-02: Login\n\nIt was on [build.md](../build.md).\n")
        self.assertEqual((app / "AGENTS.md").read_text(encoding="utf-8"), "# App\n\n" + init.agent_notes_text("plans"))
        self.assertIn("renamed", jobs.load(st))

    def test_a_change_queued_before_the_move_is_committed_on_its_own_first(self):
        st = make_store(self.top)
        board = st.dir / "plans.md"
        board.write_text(board.read_text(encoding="utf-8").replace("**Dark mode.**", "**Dark theme.**"), encoding="utf-8")
        with mock.patch.object(gitlog, "ensure_committer"):
            gitlog.record(st, [board], "board: PLN-01 edited")
        renaming.move(st, sessions=IDLE, record=commit_now)
        subjects = git(st.dir, "log", "-2", "--format=%s").split("\n")
        self.assertEqual(subjects[:2], ["board: plans.md → build.md, backlog.md → plan.md", "board: PLN-01 edited"])
        self.assertEqual(git(st.dir, "show", "--format=", "--name-status", "HEAD~1").split(), ["M", "plans.md"])
        self.assertIn("**Dark theme.**", (st.dir / "build.md").read_text(encoding="utf-8"))

    def test_no_link_to_an_old_name_is_left_after_a_move(self):
        st = make_store(self.top)
        board = st.dir / "plans.md"
        text = board.read_text(encoding="utf-8")
        rule = next(line for line in text.split("\n") if line.startswith("- **Filing a plan:**"))
        self.assertIn("[backlog.md](backlog.md)", rule)
        board.write_text(text.replace(rule + "\n", "").replace("This file is the board", "This file is our board"), encoding="utf-8")
        renaming.move(st, sessions=IDLE, record=mock.Mock())
        self.assertIn("- **Filing a plan:**", (st.dir / "build.md").read_text(encoding="utf-8"))  # put back by the notes update
        for f in [st.dir / "build.md", st.dir / "plan.md", *sorted((st.dir / "active").glob("*.md")), *sorted((st.dir / "archive").glob("*.md"))]:
            self.assertIsNone(OLD_LINK.search(f.read_text(encoding="utf-8")), f.name)

    def test_a_store_whose_board_json_does_not_parse_is_not_moved(self):
        st = make_store(self.top, repo=False)
        (st.dir / "board.json").write_text('{"name": ', encoding="utf-8")
        self.assertFalse(st.reload())
        before = files_of(st.dir)
        self.assertFalse(renaming.due(st))
        self.assertEqual(renaming.move(st, sessions=IDLE, record=mock.Mock()), [])
        self.assertEqual(files_of(st.dir), before)

    def test_a_file_that_cannot_be_written_stops_the_move_before_its_commit(self):
        st = make_store(self.top)
        active = st.dir / "active"
        active.chmod(0o555)
        self.addCleanup(active.chmod, 0o755)
        record = mock.Mock()
        with self.assertRaises(OSError) as raised:
            renaming.move(st, sessions=IDLE, record=record)
        self.assertIn(DOC, str(raised.exception))
        record.assert_not_called()
        self.assertNotIn("renamed", jobs.load(st))
        self.assertTrue(renaming.due(st))

    def test_a_file_whose_text_does_not_change_is_not_written(self):
        d = self.top / "kept"
        d.mkdir()
        f = d / "x.md"
        f.write_text("# X\n", encoding="utf-8")
        f.chmod(0o444)
        d.chmod(0o555)
        self.addCleanup(d.chmod, 0o755)
        mtime = f.stat().st_mtime_ns
        renaming._write(stores.Store(d), f, "# X\n")
        self.assertEqual(f.stat().st_mtime_ns, mtime)

    def test_a_folder_with_no_board_json_is_never_moved(self):
        st = make_store(self.top, repo=False)
        (st.dir / "board.json").unlink()
        st = stores.Store(st.dir)
        before = files_of(st.dir)
        record = mock.Mock()
        self.assertFalse(renaming.due(st))
        self.assertEqual(renaming.move(st, sessions=IDLE, record=record), [])
        self.assertEqual(files_of(st.dir), before)
        self.assertEqual(sorted(p.name for p in st.dir.iterdir()), ["active", "archive", "backlog.md", "plans.md"])
        record.assert_not_called()

    def test_a_blocked_store_is_left_exactly_as_it_was(self):
        st = make_store(self.top)
        self.jobs(st, {"key": "PLN-01", "state": "started", "id": "s1", "started": 100})
        before, ledger = files_of(st.dir), jobs.load(st)
        record = mock.Mock()
        self.assertEqual(renaming.move(st, sessions=listing({"s1": "busy"}), record=record), [])
        self.assertEqual((files_of(st.dir), jobs.load(st)), (before, ledger))
        self.assertEqual(git(st.dir, "status", "--porcelain"), "")
        record.assert_not_called()
        self.assertTrue(renaming.due(st))

    def test_a_move_stopped_after_the_first_rename_is_finished_on_the_next_run(self):
        st = make_store(self.top)
        real, renames = renaming._rename, []

        def second_fails(store, old, new):
            renames.append(old)
            if len(renames) == 2:
                raise OSError("the disk is full")
            return real(store, old, new)

        record = mock.Mock(side_effect=commit_now)
        with mock.patch.object(renaming, "_rename", second_fails), self.assertRaises(OSError):
            renaming.move(st, sessions=IDLE, record=record)
        record.assert_not_called()
        self.assertTrue(st.half_moved)
        self.assertEqual(st.boards, ("build.md", "backlog.md"))
        self.assertEqual((st.dir / DOC).read_text(encoding="utf-8"), DOC_TEXT)  # no links rewritten for a store that did not rename
        self.assertTrue(renaming.due(st))
        renaming.move(st, sessions=IDLE, record=record)
        self.assertEqual(st.boards, ("build.md", "plan.md"))
        self.assertEqual(record.call_count, 1)
        shown = git(st.dir, "log", "--name-status", "-M", "-1", "--format=%s").strip().split("\n")
        self.assertEqual(shown[0], "board: plans.md → build.md, backlog.md → plan.md")
        self.assertEqual(sorted(tuple(line.split("\t")[1:]) for line in shown[1:] if line.startswith("R")),
                         [("backlog.md", "plan.md"), ("plans.md", "build.md")])
        self.assertEqual(git(st.dir, "status", "--porcelain"), "")

    def test_a_move_stopped_after_the_links_were_rewritten_is_committed_whole_on_the_next_run(self):
        st = make_store(self.top)
        with mock.patch.object(init, "refresh_header", side_effect=OSError("stopped")), self.assertRaises(OSError):
            renaming.move(st, sessions=IDLE, record=commit_now)
        self.assertEqual(git(st.dir, "rev-list", "--count", "HEAD"), "1\n")
        renaming.move(st, sessions=IDLE, record=commit_now)
        shown = git(st.dir, "log", "--name-status", "-M", "-1", "--format=%s").strip().split("\n")
        self.assertEqual(shown[0], "board: plans.md → build.md, backlog.md → plan.md")
        self.assertEqual(sorted(line.split("\t")[0][:1] + " " + line.split("\t")[-1] for line in shown[1:] if line),
                         sorted(["R build.md", "R plan.md", f"M {DOC}", f"M {ARCHIVED_DOC}"]))
        self.assertEqual(git(st.dir, "status", "--porcelain"), "")

    def test_a_second_run_changes_nothing_and_commits_nothing(self):
        st = make_store(self.top)
        record = mock.Mock(side_effect=commit_now)
        renaming.move(st, sessions=IDLE, record=record)
        after, head = files_of(st.dir), git(st.dir, "rev-parse", "HEAD")
        self.assertFalse(renaming.due(st))
        self.assertEqual(renaming.move(st, sessions=IDLE, record=record), [])
        state = jobs.load(st)
        del state["renamed"]  # a store moved, but its record lost: the next run finishes it
        jobs.save(st, state)
        self.assertTrue(renaming.due(st))
        renaming.move(st, sessions=IDLE, record=record)
        self.assertEqual((files_of(st.dir), git(st.dir, "rev-parse", "HEAD")), (after, head))
        self.assertEqual(record.call_count, 1)
        self.assertFalse(renaming.due(st))

    def test_a_finishing_run_that_changes_files_says_so(self):
        st = make_store(self.top)
        git(st.dir, "mv", "plans.md", "build.md")
        git(st.dir, "mv", "backlog.md", "plan.md")
        git(st.dir, "commit", "-qm", "moved by hand")
        record = mock.Mock(side_effect=commit_now)
        renaming.move(st, sessions=IDLE, record=record)
        self.assertEqual(git(st.dir, "log", "-1", "--format=%s").strip(), "board: the move to Plan and Build finished")
        self.assertIn("[build.md](../build.md)", (st.dir / ARCHIVED_DOC).read_text(encoding="utf-8"))

    def test_an_untouched_header_is_replaced_and_an_edited_one_is_kept(self):
        st = make_store(self.top, repo=False)
        heads = init.board_headers(st)
        plan = st.dir / "backlog.md"
        edited = plan.read_text(encoding="utf-8").replace("Work not yet scheduled", "Work we may do one day")
        plan.write_text(edited, encoding="utf-8")
        renaming.move(st, sessions=IDLE, record=mock.Mock())
        build = (st.dir / "build.md").read_text(encoding="utf-8")
        old, new = heads["build"]
        self.assertTrue(build.startswith(new))
        self.assertNotEqual(old, new)
        self.assertIn("on the Plan board, [plan.md](plan.md)", new)
        self.assertIn("`kandalf check build.md`", new)
        self.assertNotIn("backlog.md", new)
        self.assertNotIn("plans.md", new)
        self.assertEqual((st.dir / "plan.md").read_text(encoding="utf-8"), renaming.relink(edited))
        self.assertIn("on the Build board", heads["plan"][1])

    def test_plan_docs_in_active_and_archive_are_relinked(self):
        st = make_store(self.top, repo=False)
        untouched = st.dir / "active" / "2026-11-21-pln-09-other.md"
        untouched.write_text("# PLN-09\n\nNo links to boards.\n", encoding="utf-8")
        mtime = untouched.stat().st_mtime_ns
        renaming.move(st, sessions=IDLE, record=mock.Mock())
        self.assertEqual((st.dir / DOC).read_text(encoding="utf-8"), renaming.relink(DOC_TEXT))
        self.assertIn("(../plan.md#feature)", renaming.relink(DOC_TEXT))
        self.assertEqual((st.dir / ARCHIVED_DOC).read_text(encoding="utf-8"), "# PLN-02: Login\n\nIt was on [build.md](../build.md).\n")
        self.assertEqual(untouched.stat().st_mtime_ns, mtime)  # a file with nothing to change isn't written

    def test_a_store_that_is_not_a_git_repo_is_moved_by_plain_renames(self):
        st = make_store(self.top, repo=False)
        build, plan = ((st.dir / n).read_text(encoding="utf-8") for n in ("plans.md", "backlog.md"))
        record = mock.Mock()
        lines = renaming.move(st, sessions=IDLE, record=record)
        self.assertTrue(lines)
        self.assertEqual(sorted(p.name for p in st.dir.glob("*.md")), ["build.md", "plan.md"])
        self.assertEqual((st.dir / "plan.md").read_text(encoding="utf-8").split("\n## ", 1)[1], plan.split("\n## ", 1)[1])
        self.assertEqual((st.dir / "build.md").read_text(encoding="utf-8").split("\n## ", 1)[1], build.split("\n## ", 1)[1])
        self.assertIsNone(st.root)
        record.assert_not_called()  # nothing to commit to
        self.assertIn("renamed", jobs.load(st))

    def test_the_ledger_records_the_move_last(self):
        st = make_store(self.top)
        with mock.patch.object(init, "board_headers", side_effect=OSError("no templates")), self.assertRaises(OSError):
            renaming.move(st, sessions=IDLE, record=mock.Mock())
        self.assertEqual(st.boards, ("build.md", "plan.md"))
        self.assertNotIn("renamed", jobs.load(st))
        self.assertTrue(renaming.due(st))
        seen = []
        renaming.move(st, sessions=IDLE, record=lambda store, paths, message, **kw: seen.append("renamed" in jobs.load(store)))
        self.assertEqual(seen, [False])  # the commit is queued before the move is recorded as done
        self.assertIn("renamed", jobs.load(st))
        self.assertFalse(renaming.due(st))

    def test_a_move_stopped_between_its_commit_and_its_record_writes_the_record_and_commits_nothing(self):
        st = make_store(self.top)
        record = mock.Mock(side_effect=commit_now)
        with mock.patch.object(jobs.Ledger, "change", side_effect=OSError("stopped")), self.assertRaises(OSError):
            renaming.move(st, sessions=IDLE, record=record)
        self.assertEqual(record.call_count, 1)
        head = git(st.dir, "rev-parse", "HEAD")
        self.assertEqual(git(st.dir, "log", "-1", "--format=%s").strip(), "board: plans.md → build.md, backlog.md → plan.md")
        self.assertNotIn("renamed", jobs.load(st))
        self.assertTrue(renaming.due(st))
        renaming.move(st, sessions=IDLE, record=record)
        self.assertEqual((record.call_count, git(st.dir, "rev-parse", "HEAD")), (1, head))
        self.assertIn("renamed", jobs.load(st))
        self.assertFalse(renaming.due(st))


class KnownMoved(Base):
    """A store moved and committed whose ledger has no record of it (a clone, or a ledger lost)."""

    def test_a_moved_store_with_no_record_and_plans_md_back_is_still_moved(self):
        st = make_store(self.top)
        renaming.move(st, sessions=IDLE, record=commit_now)
        jobs.state_path(st).unlink()
        (st.dir / "plans.md").write_text("# Plans\n\n## In flight\n\n- [ ] PLN-99 **Stale.**\n", encoding="utf-8")
        self.assertEqual((st.boards, st.strays), (("build.md", "plan.md"), ["plans.md"]))
        self.assertIsNone(renaming.blocked(st, sessions=listing(None)))
        self.assertTrue(renaming.due(st))
        head, record = git(st.dir, "rev-parse", "HEAD"), mock.Mock()
        renaming.move(st, sessions=IDLE, record=record)
        record.assert_not_called()
        self.assertEqual(git(st.dir, "rev-parse", "HEAD"), head)
        self.assertIn("renamed", jobs.load(st))
        self.assertEqual(st.strays, ["plans.md"])

    def test_an_old_store_with_its_own_committed_build_md_stays_on_the_old_names(self):
        st = make_store(self.top)
        (st.dir / "build.md").write_text("# My build notes\n", encoding="utf-8")
        git(st.dir, "add", "build.md")
        git(st.dir, "commit", "-qm", "my notes")
        self.assertEqual((st.boards, st.strays, renaming.due(st)), (("plans.md", "backlog.md"), [], True))
        self.assertEqual(renaming.blocked(st, sessions=IDLE),
                         "build.md is in the way: these boards can't be renamed Plan and Build until it is moved or renamed.")


class Restart(Base):
    def test_a_move_stopped_after_its_renames_is_committed_whole_by_the_next_start(self):
        st = make_store(self.top)
        with mock.patch.object(renaming, "_linked_files", side_effect=OSError("stopped")), self.assertRaises(OSError):
            renaming.move(st, sessions=IDLE, record=commit_now)
        self.assertEqual((st.boards, git(st.dir, "rev-list", "--count", "HEAD")), (("build.md", "plan.md"), "1\n"))
        with mock.patch.object(serve, "SESSIONS", IDLE), mock.patch.object(gitlog, "ensure_committer"), \
                mock.patch.object(pickup, "log"):
            serve.resume_store(st)
        with gitlog.queue(st.root) as q:  # the committer
            gitlog.flush(st, q)
        self.assertEqual(git(st.dir, "rev-list", "--count", "HEAD"), "2\n")
        shown = git(st.dir, "log", "--name-status", "-M", "-1", "--format=%s").strip().split("\n")
        self.assertEqual(shown[0], "board: plans.md → build.md, backlog.md → plan.md")
        self.assertEqual(sorted(tuple(line.split("\t")[1:]) for line in shown[1:] if line.startswith("R")),
                         [("backlog.md", "plan.md"), ("plans.md", "build.md")])
        self.assertEqual(git(st.dir, "status", "--porcelain"), "")

    def test_a_store_with_a_session_to_ask_about_is_left_to_the_watch_at_start(self):
        st = make_store(self.top)
        with mock.patch.object(renaming, "_linked_files", side_effect=OSError("stopped")), self.assertRaises(OSError):
            renaming.move(st, sessions=IDLE, record=commit_now)
        jobs.save(st, {**jobs.load(st), "jobs": [{"key": "PLN-01", "title": "Dark mode", "state": "idle", "id": "s1", "started": 1}]})
        listed = mock.Mock(return_value={"s1": "idle"})
        with mock.patch.object(serve, "SESSIONS", pickup.Sessions(sessions=listed)), mock.patch.object(pickup, "log"):
            serve.resume_store(st)
        listed.assert_not_called()  # not before the port is bound: listing them can take a while
        self.assertTrue(renaming.due(st))
        with gitlog.queue(st.root) as q:
            self.assertEqual([p for p in q["paths"] if p in ("plans.md", "backlog.md", "build.md", "plan.md")], [])
        with mock.patch.object(serve, "SESSIONS", listing({"s1": "idle"})), mock.patch.object(pickup, "log"):
            serve.watch_move_turn(st, {"m_before": serve.old_board_mtimes(st)})  # the watch's first turn
        with gitlog.queue(st.root) as q:
            gitlog.flush(st, q)
        self.assertEqual(git(st.dir, "rev-list", "--count", "HEAD"), "2\n")
        shown = git(st.dir, "log", "--name-status", "-M", "-1", "--format=%s").strip().split("\n")
        self.assertEqual(shown[0], "board: plans.md → build.md, backlog.md → plan.md")
        self.assertEqual(sorted(tuple(line.split("\t")[1:]) for line in shown[1:] if line.startswith("R")),
                         [("backlog.md", "plan.md"), ("plans.md", "build.md")])

    def test_resuming_the_queue_of_a_store_still_to_move_leaves_its_renames_to_the_move(self):
        st = make_store(self.top)
        with mock.patch.object(renaming, "_linked_files", side_effect=OSError("stopped")), self.assertRaises(OSError):
            renaming.move(st, sessions=IDLE, record=commit_now)
        (st.dir / DOC).write_text(DOC_TEXT + "\nMore.\n", encoding="utf-8")
        with mock.patch.object(gitlog, "ensure_committer"):
            gitlog.resume(st)
        with gitlog.queue(st.root) as q:
            self.assertEqual(q["paths"], [DOC])


class Told(Base):
    def store(self, renamed_at=None, started=900):
        st = make_store(self.top, repo=False)
        state = {"inflight": ["PLN-01"], "jobs": [{"key": "PLN-01", "title": "Dark mode", "state": "idle", "id": "s1",
                                                    "session": "s1-full", "started": started}]}
        if renamed_at is not None:
            state["renamed"] = {"at": renamed_at}
        jobs.save(st, state)
        return st

    def test_a_session_started_before_the_move_is_told_first(self):
        st = self.store(renamed_at=1000)
        job = jobs.load(st)["jobs"][0]
        self.assertEqual(renaming.told(st, job), renaming.TOLD)
        self.assertEqual(renaming.TOLD, "The boards were renamed since you started: plans.md is now build.md and backlog.md is now plan.md.")
        fake = Fake()
        fake.live.add("s1")
        with mock.patch.object(pickup, "log"):
            self.assertIsNone(pickup.reply(st, "PLN-01", "Rename the button.", sessions=fake))
        self.assertEqual(fake.told[-1], renaming.TOLD + "\n\n" + pickup.reply_text(st, job, "Rename the button."))
        jobs.save(st, {**jobs.load(st), "jobs": [{**job, "state": "idle"}]})
        with mock.patch.object(pickup, "log"):
            self.assertTrue(pickup.restart(st, "PLN-01", sessions=fake, nudge=True))
        self.assertEqual(fake.told[-1], renaming.TOLD + "\n\n" + pickup.NUDGE.format(key="PLN-01"))
        with mock.patch.object(pickup, "log"):
            pickup.reply(st, "PLN-01", "And the label.", sessions=fake)  # resumed since the move: told once
        self.assertFalse(fake.told[-1].startswith(renaming.TOLD))

    def test_a_session_started_after_it_is_told_nothing(self):
        st = self.store(renamed_at=1000, started=1100)
        job = jobs.load(st)["jobs"][0]
        self.assertEqual(renaming.told(st, job), "")
        fake = Fake()
        fake.live.add("s1")
        with mock.patch.object(pickup, "log"):
            self.assertTrue(pickup.restart(st, "PLN-01", sessions=fake, nudge=True))
        self.assertEqual(fake.told, [pickup.NUDGE.format(key="PLN-01")])

    def test_a_store_never_moved_tells_nothing(self):
        st = self.store()
        self.assertEqual(renaming.told(st, jobs.load(st)["jobs"][0]), "")
        self.assertEqual(renaming.told(st, {"key": "PLN-02", "state": "waiting"}), "")
        fake = Fake()
        fake.live.add("s1")
        with mock.patch.object(pickup, "log"):
            pickup.restart(st, "PLN-01", sessions=fake)
        self.assertEqual(fake.told, [None])  # resumed with its usual words


class Notice(Base):
    def test_a_blocked_move_says_what_it_waits_for(self):
        st = make_store(self.top, repo=False)
        self.jobs(st, {"key": "PLN-41", "state": "started", "id": "s1"})
        self.assertEqual(renaming.notice(st, sessions=listing({"s1": "busy"})),
                         {"waiting": "These boards will be renamed Plan and Build once PLN-41's session finishes its turn."})
        self.assertIsNone(renaming.notice(st, sessions=listing({"s1": "idle"})))  # due and free: it is about to be moved

    def test_a_stray_old_file_is_named(self):
        st = make_store(self.top, repo=False)
        renaming.move(st, sessions=IDLE, record=mock.Mock())
        (st.dir / "plans.md").write_text("# Plans\n\n## To do\n", encoding="utf-8")
        self.assertEqual(renaming.notice(st, sessions=IDLE), {
            "stray": ["plans.md"],
            "text": "plans.md is back in this store, but the board is build.md now. Move anything new into build.md and delete plans.md."})
        (st.dir / "backlog.md").write_text("# Backlog\n", encoding="utf-8")
        got = renaming.notice(st, sessions=IDLE)
        self.assertEqual(got["stray"], ["plans.md", "backlog.md"])
        self.assertTrue(got["text"].endswith(" backlog.md is back in this store, but the board is plan.md now. "
                                             "Move anything new into plan.md and delete backlog.md."))

    def test_a_board_json_that_cannot_be_read_is_what_the_move_waits_for(self):
        st = make_store(self.top, repo=False)
        (st.dir / "board.json").write_text('{"name": ', encoding="utf-8")
        self.assertFalse(st.reload())
        self.assertEqual(renaming.notice(st, sessions=listing(None)),
                         {"waiting": "These boards will be renamed Plan and Build once board.json can be read."})

    def test_a_settled_store_has_no_notice(self):
        st = make_store(self.top, repo=False)
        renaming.move(st, sessions=IDLE, record=mock.Mock())
        sessions = listing(None)
        self.assertIsNone(renaming.notice(st, sessions=sessions))
        self.assertEqual(sessions.asked, [])


class Hook(Base):
    def hook(self, path):
        err = io.StringIO()
        with redirect_stderr(err):
            code = checkhook.main(stdin=io.StringIO(json.dumps({"tool_input": {"file_path": str(path)}})), stdout=io.StringIO())
        return code, err.getvalue()

    def test_an_edit_that_brings_plans_md_back_is_handed_back_with_where_to_put_it(self):
        st = make_store(self.top, repo=False)
        renaming.move(st, sessions=IDLE, record=mock.Mock())
        (st.dir / "plans.md").write_text("# Plans\n\n## To do\n", encoding="utf-8")
        (st.dir / "backlog.md").write_text("# Backlog\n", encoding="utf-8")
        with mock.patch.object(checkhook.shutil, "which", lambda n: None):  # needs no node
            self.assertEqual(self.hook(st.dir / "plans.md"), (2, "plans.md is now build.md: put your change there and delete plans.md.\n"))
            self.assertEqual(self.hook(st.dir / "backlog.md"), (2, "backlog.md is now plan.md: put your change there and delete backlog.md.\n"))
            self.assertEqual(self.hook(st.dir / "build.md"), (0, ""))

    def test_an_edit_to_plans_md_in_an_old_store_with_its_own_build_md_is_checked_as_the_board(self):
        st = make_store(self.top, repo=False)
        (st.dir / "build.md").write_text("# Mine\n", encoding="utf-8")
        ran, real = [], subprocess.run

        def run(cmd, **kw):
            if cmd[0] != "/x/node":
                return real(cmd, **kw)
            ran.append(Path(cmd[-1]).name)
            return mock.Mock(returncode=0, stderr="")

        with mock.patch.object(checkhook.shutil, "which", lambda n: "/x/node"), mock.patch.object(checkhook.subprocess, "run", run):
            self.assertEqual(self.hook(st.dir / "plans.md"), (0, ""))
            self.assertEqual(self.hook(st.dir / "build.md"), (0, ""))
        self.assertEqual(ran, ["plans.md"])  # the board is checked; the person's own build.md isn't a board

    def test_an_old_names_store_s_plans_md_is_checked_as_before(self):
        st = make_store(self.top, repo=False)
        ran, real = [], subprocess.run

        def run(cmd, **kw):  # node fails every check; anything else (git) runs
            if cmd[0] != "/x/node":
                return real(cmd, **kw)
            ran.append(Path(cmd[-1]).name)
            return mock.Mock(returncode=1, stderr="line 3 would change\n")

        with mock.patch.object(checkhook.shutil, "which", lambda n: "/x/node"), mock.patch.object(checkhook.subprocess, "run", run):
            self.assertEqual(self.hook(st.dir / "plans.md"), (2, "line 3 would change\n"))
        self.assertEqual(ran, ["plans.md"])
        self.assertTrue(os.path.isfile(st.dir / "plans.md"))


if __name__ == "__main__":
    unittest.main()
