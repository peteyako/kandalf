#!/usr/bin/env python3
"""A draft's own readings (drafts.py): the research doc, the options block, the prompt and the view.

    python3 -m unittest test_drafts
"""

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from kandalf import cli, drafts, jobs, pickup, serve, tiles
from kandalf import store as stores

HERE = Path(__file__).parent

EXAMPLE = """# PLN-30 options: Pro theme: catch up with the new nav, or retire it

Recommends: B

## A. Restyle it

Sidebar card, profile box, tab bar and More sheet in the pro palette. ~2d, for a theme
nothing can switch on.

What it touches, risks, what was read...

## B. Retire it

Delete the app-shell rules in app-neo-pro.css. ~1h; the spectate pages keep their own pro look.

...

## What I read

Lots of files.
"""


class ReadDoc(unittest.TestCase):
    def test_the_spec_s_example_reads_as_two_options_and_a_recommendation(self):
        got = drafts.read_doc(EXAMPLE)
        self.assertEqual(got["recommends"], "B")
        self.assertEqual(got["items"], [
            {"letter": "A", "title": "Restyle it", "summary": "Sidebar card, profile box, tab bar and More sheet in the pro palette. ~2d, for a theme nothing can switch on."},
            {"letter": "B", "title": "Retire it", "summary": "Delete the app-shell rules in app-neo-pro.css. ~1h; the spectate pages keep their own pro look."},
        ])

    def test_a_heading_that_drifts_from_the_format_is_still_an_option(self):
        got = drafts.read_doc("Recommends: b\n\n## Option A: Restyle\n\nOne.\n\n## b) retire it.\n\nTwo.\n")
        self.assertEqual([(i["letter"], i["title"]) for i in got["items"]], [("A", "Restyle"), ("B", "retire it")])
        self.assertEqual(got["recommends"], "B")

    def test_other_headings_and_what_follows_them_are_ignored(self):
        got = drafts.read_doc("## What I read\n\nstuff\n\n## A. One\n\nFirst.\n\n## What I read\n\nmore\n")
        self.assertEqual(got["items"], [{"letter": "A", "title": "One", "summary": "First."}])

    def test_a_summary_is_the_first_paragraph_folded_and_cut_on_a_word(self):
        words = " ".join(["word"] * 80)  # 399 characters
        got = drafts.read_doc(f"## A. One\n\n{words[:200]}\n{words[200:]}\n")
        s = got["items"][0]["summary"]
        self.assertLessEqual(len(s), 241)
        self.assertTrue(s.endswith("…"))
        self.assertNotIn("\n", s)
        self.assertTrue(all(w == "word" for w in s[:-1].split()))

    def test_only_the_first_four_options_are_kept(self):
        text = "".join(f"## {c}. T{c}\n\nS.\n\n" for c in "ABCDEF")
        self.assertEqual([i["letter"] for i in drafts.read_doc(text)["items"]], list("ABCD"))

    def test_a_recommendation_for_an_option_that_is_not_there_is_dropped(self):
        self.assertIsNone(drafts.read_doc("Recommends: C\n\n## A. One\n\nS.\n")["recommends"])

    def test_a_doc_with_no_option_reads_as_empty(self):
        self.assertEqual(drafts.read_doc("# Title\n\nRecommends: A\n\n## What I read\n"), {"recommends": None, "items": []})


    def test_headings_inside_a_code_fence_are_not_options(self):
        text = "Recommends: A\n\n## A. One\n\nFirst.\n\n```markdown\n## A. x\n## B. y\nRecommends: B\n```\n\n## B. Two\n\nSecond.\n"
        got = drafts.read_doc(text)
        self.assertEqual([(i["letter"], i["title"]) for i in got["items"]], [("A", "One"), ("B", "Two")])
        self.assertEqual(got["recommends"], "A")

    def test_only_the_first_option_for_a_letter_is_kept(self):
        got = drafts.read_doc("## A. One\n\nFirst.\n\n## A. Again\n\nLater.\n\n## B. Two\n\nSecond.\n")
        self.assertEqual([(i["letter"], i["title"]) for i in got["items"]], [("A", "One"), ("B", "Two")])

    def test_a_wrapped_line_that_starts_with_a_hash_does_not_end_a_summary(self):
        got = drafts.read_doc("## A. One\n\nFixed in\n#412 by someone.\n")
        self.assertEqual(got["items"][0]["summary"], "Fixed in #412 by someone.")

    def test_a_fenced_block_is_not_part_of_a_summary(self):
        got = drafts.read_doc("## A. One\n\n```\ncode\n\nmore code\n```\n\nThe prose.\n\n## B. Two\n\n```\nonly code\n```\n")
        self.assertEqual([i["summary"] for i in got["items"]], ["The prose.", ""])

    def test_a_hash_that_belongs_to_a_word_stays_in_the_title(self):
        self.assertEqual(drafts.read_doc("## A. Port it to C#\n\nS.\n")["items"][0]["title"], "Port it to C#")

    def test_closing_hashes_are_dropped_from_a_title(self):
        self.assertEqual(drafts.read_doc("## A. Restyle ##\n\nS.\n")["items"][0]["title"], "Restyle")

    def test_a_title_keeps_underscores_and_backticks_but_loses_emphasis(self):
        got = drafts.read_doc("## A. Rename `user_id`\n\nS.\n\n## B. *Restyle* it\n\nS.\n")
        self.assertEqual([i["title"] for i in got["items"]], ["Rename `user_id`", "Restyle it"])

    def test_a_bolded_recommends_label_is_read(self):
        for line in ("**Recommends:** B", "**Recommends: B**", "Recommends: **B**"):
            self.assertEqual(drafts.read_doc(f"{line}\n\n## A. One\n\nS.\n\n## B. Two\n\nS.\n")["recommends"], "B", line)

    def test_a_recommends_line_after_the_first_option_is_ignored(self):
        self.assertIsNone(drafts.read_doc("## A. One\n\nS.\n\nRecommends: A\n")["recommends"])


class Block(unittest.TestCase):
    def test_a_reading_becomes_the_tile_s_block_and_reads_back(self):
        doc = drafts.doc_path("PLN-30", "2026-11-24")
        self.assertEqual(doc, "active/2026-11-24-pln-30-options.md")
        lines = drafts.block(drafts.read_doc(EXAMPLE), "2026-11-24", doc, "cheaper?")
        got = tiles.options("- [ ] PLN-30 **T.** d\n" + "\n".join(lines) + "\n")
        self.assertEqual((got["date"], got["recommends"], got["doc"], got["asked"]), ("2026-11-24", "B", doc, "cheaper?"))
        self.assertEqual([i["letter"] for i in got["items"]], ["A", "B"])

    def test_an_empty_reading_says_the_draft_gave_no_options_and_keeps_the_link(self):
        lines = drafts.block({"recommends": None, "items": []}, "2026-11-24", "active/x.md", None)
        self.assertEqual(len(lines), 1)
        self.assertIn("the draft gave no options.", lines[0])
        self.assertIn("(active/x.md)", lines[0])


STORE = SimpleNamespace(link="plans", name="Demo")
DRAFT = {"key": "PLN-30", "title": "Pro theme", "note": "", "doc": "active/2026-11-24-pln-30-options.md"}


class Prompt(unittest.TestCase):
    def test_it_names_the_one_file_to_write_and_forbids_code_changes_and_commits(self):
        p = drafts.prompt(STORE, DRAFT, "- [ ] PLN-30 **Pro theme.** x", "", [], False)
        self.assertIn("docs/plans/active/2026-11-24-pln-30-options.md", p)
        for phrase in ("Don't decide it", "read-only commands", "Change no code", "thrown away", "exactly one file",
                       "Recommend one", "could not find out", "Don't edit the boards", "Don't commit", "Stop when the file is written",
                       "Recommends: B", "## A. Restyle it", "- [ ] PLN-30 **Pro theme.** x"):
            self.assertIn(phrase, p)
        self.assertNotIn("The last draft is at", p)
        self.assertLess(p.index("## A. Restyle it"), p.index("Don't decide it"))
        self.assertIn("docs/plans is the plans store linked into this worktree", p)

    def test_a_redraft_carries_the_note_and_points_at_the_last_draft(self):
        d = dict(DRAFT, note="what about a cheaper way?")
        p = drafts.prompt(STORE, d, "tile", "the master tile", [{"key": "PLN-31", "title": "Next"}], True)
        self.assertIn("what about a cheaper way?", p)
        self.assertIn("The last draft is at `docs/plans/active/2026-11-24-pln-30-options.md`: revise it.", p)
        self.assertIn("the master tile", p)
        self.assertIn("PLN-31", p)


class PromptNote(unittest.TestCase):
    def test_a_first_draft_is_told_its_note_and_not_to_revise(self):
        p = drafts.prompt(STORE, dict(DRAFT, note="keep it short"), "tile", "", [], False)
        self.assertIn("The person asked: keep it short", p)
        self.assertNotIn("The last draft is at", p)
        self.assertNotIn("redraft", p)

    def test_a_redraft_is_told_its_note_and_the_last_draft(self):
        p = drafts.prompt(STORE, dict(DRAFT, note="keep it short"), "tile", "", [], True)
        self.assertIn("The person asked: keep it short", p)
        self.assertIn("The last draft is at", p)


class View(unittest.TestCase):
    def test_each_case_in_view_cases_json_has_the_view_it_says(self):
        cases = json.loads((HERE / "view_cases.json").read_text(encoding="utf-8"))["drafts"]
        self.assertTrue(set(drafts.STATES) <= {c["draft"]["state"] for c in cases})
        self.assertIn("nonsense", {c["draft"]["state"] for c in cases})  # a state neither side knows shows nothing
        for c in cases:
            self.assertEqual(drafts.view(c["draft"]), {"label": c["label"], "actions": c["actions"]}, c["draft"])



def git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout


TILE = "- [ ] PLN-30 **Pro theme: catch up, or retire it.**\n  Decide it."
STARTED = {"state": "started", "id": "s1", "session": "s1-full"}


class Repos(unittest.TestCase):
    """A temp app repo (one commit, branch dev) and a store beside it, and a sessions adapter that
    records what it retires."""

    def setUp(self):
        self.top = top = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(top)]))
        self.app, self.plans = top / "app", top / "plans"
        (self.app / "docs").mkdir(parents=True)
        self.plans.mkdir()
        (self.plans / "board.json").write_text(json.dumps({"app": "../app", "trunk": "dev"}))
        (self.app / "docs" / "plans").symlink_to("../../plans")
        (self.app / "README").write_text("app\n")
        git(self.app, "init", "-q", "-b", "dev")
        self.commit(self.app, "init")
        self.store = stores.Store(self.plans)
        self.retired, self.live = [], {"s1"}
        self.sessions = pickup.Sessions(retire=self.retire, sessions=lambda: {sid: "idle" for sid in self.live}, now=lambda: time.mktime((2026, 11, 23, 12, 0, 0, 0, 0, -1)))
        self.draft = {"key": "PLN-30", "state": "queued", "title": "Pro theme", "note": None, "doc": "active/2026-11-23-pln-30-options.md", "asked_at": 1}

    stubborn = False  # a session that stays listed after it is retired

    def retire(self, sid):
        self.retired.append(sid)
        if not self.stubborn:
            self.live.discard(sid)
        return not self.stubborn

    def commit(self, repo, message):
        git(repo, "add", "-A")
        git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", message)

    def start(self, tile=TILE, launched=STARTED, draft=None):
        with mock.patch.object(pickup, "_launch", return_value=launched) as launch:
            self.launch = launch
            return drafts.start(self.store, draft or self.draft, tile, "", [], sessions=self.sessions)

    def path(self):
        return pickup.worktree(self.store, {"key": "draft-PLN-30", "title": ""}, self.store.main_repo)


class Request(Repos):
    def test_a_draft_asked_for_is_queued_with_its_doc_path_and_note(self):
        got = drafts.request(self.store, "PLN-30", TILE, "keep it short", sessions=self.sessions)
        self.assertEqual({k: v for k, v in got.items() if k != "asked_at"}, {
            "key": "PLN-30", "state": "queued", "title": "Pro theme: catch up, or retire it", "note": "keep it short",
            "doc": "active/2026-11-23-pln-30-options.md"})
        self.assertEqual(drafts.live(self.store), [got])
        self.assertEqual(drafts.of(self.store, "PLN-30"), got)
        self.assertIsNone(drafts.of(self.store, "PLN-99"))

    def test_asking_again_ends_the_draft_the_tile_had_and_keeps_its_doc_path(self):
        first = drafts.request(self.store, "PLN-30", TILE, None, sessions=self.sessions)
        with jobs.Ledger(self.store).change() as state:
            state["drafts"][0].update(state="ready", doc="active/2026-11-20-pln-30-options.md", id="s1")
        again = drafts.request(self.store, "PLN-30", TILE, "more", sessions=self.sessions)
        self.assertEqual(self.retired, ["s1"])
        self.assertEqual(drafts.live(self.store), [again])
        self.assertEqual((again["state"], again["doc"], again["note"]), ("queued", "active/2026-11-20-pln-30-options.md", "more"))
        self.assertNotEqual(first["doc"], again["doc"])

    def test_asking_again_leaves_no_two_drafts_for_a_key_that_are_not_ending(self):
        drafts.request(self.store, "PLN-30", TILE, None, sessions=self.sessions)
        seen = []
        real = drafts._finish
        def spy(store, draft, sessions):
            seen.append([(d["state"]) for d in drafts.live(store)])
            return real(store, draft, sessions)
        with mock.patch.object(drafts, "_finish", spy):
            drafts.request(self.store, "PLN-30", TILE, None, sessions=self.sessions)
        self.assertEqual(seen, [["ending", "queued"]])

    def test_the_ledger_from_before_drafts_reads_as_having_none(self):
        (self.plans / ".git").mkdir()
        jobs.state_path(self.store).write_text(json.dumps({"inflight": None, "jobs": []}))
        self.assertEqual(drafts.live(self.store), [])
        self.assertEqual(drafts.of(self.store, "PLN-30"), None)
        self.assertTrue(drafts.end(self.store, "PLN-30", sessions=self.sessions))
        self.assertEqual(json.loads(jobs.state_path(self.store).read_text()), {"inflight": None, "jobs": []})


class Start(Repos):
    def test_it_cuts_a_detached_worktree_of_the_trunk_with_the_store_linked_in(self):
        got = self.start()
        path = self.path()
        self.assertEqual((got["state"], got["id"], got["session"]), ("working", "s1", "s1-full"))
        self.assertEqual(got["started"], self.sessions.now())
        self.assertEqual(got["repos"], [{"name": "app", "worktree": str(path), "trunk": "dev"}])
        self.assertEqual(git(path, "rev-parse", "HEAD"), git(self.app, "rev-parse", "dev"))
        self.assertEqual(git(path, "branch", "--show-current").strip(), "")
        self.assertEqual(git(self.app, "branch", "--list").split(), ["*", "dev"])
        self.assertTrue((path / "docs" / "plans").is_symlink())
        self.assertEqual(git(self.app, "status", "--porcelain"), "")
        args, cwd = self.launch.call_args.args[2], self.launch.call_args.args[3]
        self.assertEqual((args, cwd), (pickup.session_args({"key": "PLN-30", "title": "options", "model": self.store.model}), path))
        self.assertIn("-n", args)
        self.assertEqual(args[args.index("-n") + 1], "PLN-30 options")

    def test_a_base_line_on_the_tile_names_the_branch_it_is_cut_from(self):
        git(self.app, "branch", "next")
        (self.app / "more").write_text("x\n")
        self.commit(self.app, "ahead")
        got = self.start(TILE + "\n  Base: next")
        self.assertEqual(got["repos"][0]["trunk"], "next")
        self.assertEqual(git(self.path(), "rev-parse", "HEAD"), git(self.app, "rev-parse", "next"))

    def test_a_registered_worktree_left_at_the_path_is_made_again(self):
        self.start()
        (self.path() / "stale").write_text("left\n")
        got = self.start()
        self.assertEqual(got["state"], "working")
        self.assertFalse((self.path() / "stale").exists())

    def test_a_branch_worktree_with_work_in_it_at_the_path_is_left_alone(self):
        git(self.app, "worktree", "add", "-q", "-b", "mine", str(self.path()))
        (self.path() / "unsaved").write_text("work\n")
        got = self.start()
        self.assertEqual(got["state"], "need")
        self.assertIn(str(self.path()), got["why"])
        self.assertEqual((self.path() / "unsaved").read_text(), "work\n")
        self.launch.assert_not_called()

    def test_a_path_that_is_not_a_worktree_makes_the_draft_need_you_naming_the_path(self):
        self.path().mkdir(parents=True)
        (self.path() / "mine").write_text("not a worktree\n")
        got = self.start()
        self.assertEqual(got["state"], "need")
        self.assertIn(str(self.path()), got["why"])
        self.assertTrue((self.path() / "mine").exists())
        self.launch.assert_not_called()

    def test_a_worktree_add_that_fails_makes_the_draft_need_you_with_git_s_words(self):
        (self.plans / "board.json").write_text(json.dumps({"app": "../app", "trunk": "nope"}))
        self.store = stores.Store(self.plans)
        got = self.start()
        self.assertEqual(got["state"], "need")
        self.assertIn("nope", got["why"])
        self.assertIn("fatal", got["why"])
        self.launch.assert_not_called()

    def test_a_registered_worktree_git_will_not_remove_is_reported_in_git_s_words(self):
        self.start()
        git(self.app, "worktree", "lock", str(self.path()))
        got = self.start()
        self.assertEqual(got["state"], "need")
        self.assertIn("locked", got["why"])
        self.assertNotIn("isn't a worktree", got["why"])

    def test_a_worktree_that_cannot_be_undone_stays_recorded_on_the_need(self):
        with mock.patch.object(drafts, "_drop", return_value="stuck"):
            got = self.start(launched={"state": "failed", "error": "no claude"})
        self.assertEqual(got["state"], "need")
        self.assertEqual(got["repos"], [{"name": "app", "worktree": str(self.path()), "trunk": "dev"}])
        self.assertTrue(self.path().exists())

    def test_a_repo_the_tile_names_that_is_not_there_makes_the_draft_need_you(self):
        got = self.start(TILE + "\n  Repos: nope")
        self.assertEqual(got["state"], "need")
        self.assertIn("nope", got["why"])
        self.assertFalse(self.path().exists())

    def test_a_launch_that_fails_makes_the_draft_need_you(self):
        got = self.start(launched={"state": "failed", "error": "no claude"})
        self.assertEqual(got, {"state": "need", "why": "no claude"})
        self.assertFalse(self.path().exists())

    def test_anything_raised_after_a_worktree_is_cut_undoes_it_and_makes_the_draft_need_you(self):
        with mock.patch.object(pickup, "_launch", side_effect=ValueError("a bad model")):
            got = drafts.start(self.store, self.draft, TILE, "", [], sessions=self.sessions)
        self.assertEqual(got, {"state": "need", "why": "a bad model"})
        self.assertFalse(self.path().exists())
        self.assertEqual(git(self.app, "worktree", "list", "--porcelain").count("worktree "), 1)


class End(Repos):
    def working(self):
        got = self.start()
        with jobs.Ledger(self.store).change() as state:
            state.setdefault("drafts", []).append({**self.draft, **got})

    def test_ending_retires_the_session_and_removes_the_worktree_with_what_it_changed(self):
        self.working()
        (self.path() / "README").write_text("changed\n")
        self.assertTrue(drafts.end(self.store, "PLN-30", sessions=self.sessions))
        self.assertEqual(self.retired, ["s1"])
        self.assertFalse(self.path().exists())
        self.assertEqual(git(self.app, "status", "--porcelain"), "")
        self.assertEqual(git(self.app, "worktree", "list", "--porcelain").count("worktree "), 1)
        self.assertEqual(drafts.live(self.store), [])

    def test_a_queued_draft_ends_without_touching_git_or_the_agent(self):
        drafts.request(self.store, "PLN-30", TILE, None, sessions=self.sessions)
        with mock.patch.object(pickup, "_git") as run:
            self.assertTrue(drafts.end(self.store, "PLN-30", sessions=self.sessions))
        run.assert_not_called()
        self.assertEqual(self.retired, [])
        self.assertEqual(drafts.live(self.store), [])

    def test_a_draft_whose_worktree_will_not_go_stays_as_ending(self):
        self.working()
        real = pickup._git

        def refuse(repo, *args):
            if "remove" in args:
                return subprocess.CompletedProcess(args, 1, "", "cannot remove")
            return real(repo, *args)
        with mock.patch.object(pickup, "_git", refuse):
            self.assertFalse(drafts.end(self.store, "PLN-30", sessions=self.sessions))
        self.assertEqual(drafts.of(self.store, "PLN-30")["state"], "ending")
        self.assertTrue(self.path().exists())
        self.assertTrue(drafts.end(self.store, "PLN-30", sessions=self.sessions))  # and a later try finishes it
        self.assertEqual(drafts.live(self.store), [])

    def test_a_session_that_stays_listed_keeps_the_record_and_the_worktree(self):
        self.working()
        self.stubborn = True
        self.assertFalse(drafts.end(self.store, "PLN-30", sessions=self.sessions))
        self.assertEqual(drafts.of(self.store, "PLN-30")["state"], "ending")
        self.assertTrue(self.path().exists())
        self.stubborn = False
        self.assertTrue(drafts.end(self.store, "PLN-30", sessions=self.sessions))
        self.assertFalse(self.path().exists())

    def test_a_worktree_on_a_branch_is_left_alone_and_forgotten(self):
        self.working()
        other = self.top / "other-wt"
        git(self.app, "worktree", "add", "-b", "story", str(other), "dev")
        with jobs.Ledger(self.store).change() as state:
            state["drafts"][0]["repos"] = [{"name": "app", "worktree": str(other), "trunk": "dev"}]
        with mock.patch.object(pickup, "log") as said:
            self.assertTrue(drafts.end(self.store, "PLN-30", sessions=self.sessions))
        self.assertTrue(other.exists())
        self.assertEqual(drafts.live(self.store), [])
        self.assertIn(str(other), said.call_args.args[0])

    def test_a_path_on_disk_that_is_no_worktree_is_left_alone_and_forgotten(self):
        self.working()
        other = self.top / "plain"
        other.mkdir()
        with jobs.Ledger(self.store).change() as state:
            state["drafts"][0]["repos"] = [{"name": "app", "worktree": str(other), "trunk": "dev"}]
        self.assertTrue(drafts.end(self.store, "PLN-30", sessions=self.sessions))
        self.assertTrue(other.exists())
        self.assertEqual(drafts.live(self.store), [])

    def test_a_draft_whose_path_no_longer_matches_the_pattern_is_still_removed_as_detached(self):
        self.working()
        was = self.path()
        (self.plans / "board.json").write_text(json.dumps({"app": "../app", "trunk": "dev", "worktree": "{app}/.wt/{key}"}))
        self.store = stores.Store(self.plans)
        self.assertNotEqual(self.path(), was)
        self.assertTrue(drafts.end(self.store, "PLN-30", sessions=self.sessions))
        self.assertFalse(was.exists())
        self.assertEqual(drafts.live(self.store), [])

    def test_a_session_list_that_cannot_be_read_keeps_the_record_and_the_worktree(self):
        self.working()
        listing = self.sessions.sessions
        self.sessions.sessions = lambda: None
        self.assertFalse(drafts.end(self.store, "PLN-30", sessions=self.sessions))
        self.assertEqual(drafts.of(self.store, "PLN-30")["state"], "ending")
        self.assertTrue(self.path().exists())
        self.sessions.sessions = listing
        self.assertTrue(drafts.end(self.store, "PLN-30", sessions=self.sessions))
        self.assertFalse(self.path().exists())

    def test_a_worktree_list_that_cannot_be_read_ends_nothing(self):
        self.working()
        cuts = drafts.of(self.store, "PLN-30")["repos"]
        with mock.patch.object(drafts, "_listed", return_value=None):
            self.assertFalse(drafts.end(self.store, "PLN-30", sessions=self.sessions))
        self.assertEqual(drafts.of(self.store, "PLN-30")["state"], "ending")
        self.assertEqual(drafts.of(self.store, "PLN-30")["repos"], cuts)
        self.assertTrue(self.path().exists())
        self.assertTrue(drafts.end(self.store, "PLN-30", sessions=self.sessions))
        self.assertFalse(self.path().exists())
        self.assertEqual(drafts.live(self.store), [])

    def test_a_failed_git_listing_reads_as_unreadable(self):
        failed = subprocess.CompletedProcess([], 128, "", "fatal: dubious ownership")
        with mock.patch.object(pickup, "_git", return_value=failed):
            self.assertIsNone(drafts._listed(self.app))
        self.assertIn(self.app.resolve(), drafts._listed(self.app))

    def test_the_draft_s_own_path_is_removed_even_when_git_lists_it_on_a_branch(self):
        path = self.path()
        git(self.app, "worktree", "add", "-b", "odd", str(path), "dev")
        with jobs.Ledger(self.store).change() as state:
            state.setdefault("drafts", []).append({**self.draft, "state": "working", "repos": [{"name": "app", "worktree": str(path), "trunk": "dev"}]})
        self.assertTrue(drafts.end(self.store, "PLN-30", sessions=self.sessions))
        self.assertFalse(path.exists())

    def test_a_worktree_already_gone_does_not_stop_the_end(self):
        self.working()
        subprocess.run(["rm", "-rf", str(self.path())])
        self.assertTrue(drafts.end(self.store, "PLN-30", sessions=self.sessions))

    def test_a_key_with_no_draft_ends_as_done(self):
        self.assertTrue(drafts.end(self.store, "PLN-99", sessions=self.sessions))


BOARD = """## Decisions

- [ ] PLN-30 **Pro theme: catch up, or retire it.**
  Decide it.
- [ ] PLN-31 **Other.**
  Decide that.

## Later

- [ ] PLN-32 **Waits.**
  Blocked by PLN-30.
"""
ONE = "## A. Restyle it\n\nOnly this one now.\n"


class Look(Repos):
    """The Repos fixture with a session list set per test ({id: state}, or None when it can't be
    read), the reasons idle sessions wait, a clock, the plans board as text, and a write that
    records each block and puts it on the board."""

    def setUp(self):
        super().setUp()
        self.listing, self.whys, self.clock = {}, {}, self.sessions.now()
        self.listed = 0  # how many times the session list was asked for
        self.sessions = pickup.Sessions(retire=self.retire_listed, sessions=self.list_sessions,
                                        why=lambda sid: self.whys.get(sid), now=lambda: self.clock)
        self.board, self.archive = BOARD, ""
        self.written, self.launched = [], []
        self.on_launch = None
        self.write_error = None  # what write raises, when it fails

    def list_sessions(self):
        self.listed += 1
        return None if self.listing is None else dict(self.listing)

    def retire_listed(self, sid):
        self.retired.append(sid)
        if not self.stubborn and self.listing:
            self.listing.pop(sid, None)
        return not self.stubborn

    def write(self, key, block, subject):
        if self.write_error:
            raise self.write_error
        self.written.append((key, block, subject))
        self.board = tiles.with_options(self.store, self.board, key, block)

    def snap(self):
        files = {"build.md": self.board}
        if self.archive:
            files["archive/archived.md"] = self.archive
        return tiles.Snapshot(self.store, files)

    def launch(self, store, draft, *rest):
        sid = f"s{len(self.launched) + 1}"
        self.launched.append(draft["key"])
        if self.listing is not None:
            self.listing[sid] = "busy"
        if self.on_launch:
            self.on_launch(draft)
        return {"state": "started", "id": sid, "session": sid + "-full"}

    def look(self):
        with mock.patch.object(pickup, "_launch", side_effect=self.launch):
            return drafts.look(self.store, self.snap(), sessions=self.sessions, write=self.write)

    def ask(self, key="PLN-30", note=None):
        return drafts.request(self.store, key, self.snap().tile(key).text, note, sessions=self.sessions)

    def later(self):
        self.clock += jobs.SETTLE + 1

    def doc(self, key="PLN-30"):
        return self.store.dir / drafts.of(self.store, key)["doc"]

    def put_doc(self, text, key="PLN-30", bump=0):
        path = self.doc(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if bump:
            st = path.stat()
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + bump))
        return path

    def wt(self, key="PLN-30"):
        return pickup.worktree(self.store, {"key": "draft-" + key, "title": ""}, self.store.main_repo)

    def ready(self):
        """PLN-30 started and finished, with the example doc read onto its tile."""
        self.ask()
        self.look()
        self.put_doc(EXAMPLE)
        self.later()
        self.listing["s1"] = "idle"
        self.look()
        self.assertEqual(drafts.of(self.store, "PLN-30")["state"], "ready")

    def test_the_oldest_queued_draft_starts_and_the_next_waits_for_it(self):
        self.ask("PLN-30")
        self.ask("PLN-31")
        said = self.look()
        self.assertEqual(self.launched, ["PLN-30"])
        got = drafts.of(self.store, "PLN-30")
        self.assertEqual((got["state"], got["id"], got["read"]), ("working", "s1", None))
        self.assertEqual(drafts.of(self.store, "PLN-31")["state"], "queued")
        self.assertIn("PLN-30: drafting options (session s1)", said)
        self.assertTrue(self.wt("PLN-30").exists())
        self.later()
        self.look()
        self.assertEqual(self.launched, ["PLN-30"])
        self.assertEqual(drafts.of(self.store, "PLN-31")["state"], "queued")
        self.assertTrue(drafts.busy(self.store))

    def test_a_draft_whose_session_is_busy_is_left_alone(self):
        self.ask()
        self.look()
        self.put_doc(EXAMPLE)
        self.later()
        self.assertEqual(self.look(), [])
        self.assertEqual(drafts.of(self.store, "PLN-30")["state"], "working")
        self.assertEqual(self.written, [])

    def test_an_idle_session_with_a_doc_makes_the_draft_ready_and_writes_the_block(self):
        self.ask(note="cheaper?")
        self.look()
        path = self.put_doc(EXAMPLE)
        self.later()
        self.listing["s1"] = "idle"
        said = self.look()
        got = drafts.of(self.store, "PLN-30")
        self.assertEqual((got["state"], got["read"]), ("ready", path.stat().st_mtime_ns))
        self.assertEqual(self.written, [("PLN-30", drafts.block(drafts.read_doc(EXAMPLE), "2026-11-23", got["doc"], "cheaper?"),
                                         "board: PLN-30 options drafted")])
        self.assertIn("PLN-30: options ready", said)
        on_tile = tiles.options(self.snap().tile("PLN-30").text)
        self.assertEqual(([i["letter"] for i in on_tile["items"]], on_tile["recommends"], on_tile["doc"]), (["A", "B"], "B", got["doc"]))
        self.assertTrue(self.wt().exists())  # a ready draft keeps its worktree and session
        self.assertEqual(self.retired, [])

    def test_an_idle_session_with_no_doc_makes_it_empty_and_says_so_on_the_tile(self):
        self.ask()
        self.look()
        self.later()
        self.listing["s1"] = "idle"
        said = self.look()
        got = drafts.of(self.store, "PLN-30")
        self.assertEqual((got["state"], got["read"]), ("empty", None))
        self.assertEqual(self.written, [("PLN-30", ["  **Options 2026-11-23:** the draft gave no options."], "board: PLN-30 draft gave no options")])
        self.assertIn("PLN-30: the draft gave no options", said)
        self.assertEqual(self.look(), [])  # and says it once
        self.assertEqual(len(self.written), 1)

    def test_a_session_that_waits_on_a_question_needs_you_with_why(self):
        self.ask("PLN-30")
        self.ask("PLN-31")
        self.look()
        self.later()
        self.listing["s1"] = "idle"
        self.whys["s1"] = "question"
        said = self.look()
        got = drafts.of(self.store, "PLN-30")
        self.assertEqual((got["state"], got["why"]), ("need", "it asked a question"))
        self.assertIn("PLN-30: the draft needs you: it asked a question", said)
        self.assertEqual(self.written, [])
        self.assertEqual(drafts.of(self.store, "PLN-31")["state"], "working")  # a draft that needs you is not at work
        self.later()
        self.listing["s2"] = "waiting"  # blocked mid-turn, whatever its screen shows
        self.assertIn("PLN-31: the draft needs you: it asked a question", self.look())
        self.assertEqual(drafts.of(self.store, "PLN-31")["why"], "it asked a question")

    def test_a_session_gone_from_the_list_is_lost(self):
        self.ask()
        self.look()
        self.later()
        del self.listing["s1"]
        said = self.look()
        self.assertEqual(drafts.of(self.store, "PLN-30")["state"], "lost")
        self.assertIn("PLN-30: the draft's session is gone", said)
        self.assertTrue(self.wt().exists())

    def test_a_session_just_started_is_not_lost_before_it_shows_in_the_list(self):
        self.ask()
        self.look()
        del self.listing["s1"]
        self.assertEqual(self.look(), [])
        self.assertEqual(drafts.of(self.store, "PLN-30")["state"], "working")

    def test_nothing_changes_when_the_sessions_cannot_be_listed(self):
        self.ready()
        self.ask("PLN-31")
        self.look()  # PLN-31 starts, s2
        self.put_doc(ONE, bump=10**9)
        self.later()
        self.listing = None
        before = drafts.live(self.store)
        self.assertEqual(self.look(), [])
        self.assertEqual(drafts.live(self.store), before)
        self.assertEqual(len(self.written), 1)

    def test_a_doc_changed_after_the_draft_was_ready_is_read_again(self):
        self.ready()
        path = self.put_doc(ONE, bump=10**9)
        self.look()
        got = drafts.of(self.store, "PLN-30")
        self.assertEqual((got["state"], got["read"]), ("ready", path.stat().st_mtime_ns))
        self.assertEqual(len(self.written), 2)
        self.assertEqual(self.written[1][1], drafts.block(drafts.read_doc(ONE), "2026-11-23", got["doc"], None))
        self.assertEqual(self.written[1][2], "board: PLN-30 options drafted")
        self.assertEqual([i["letter"] for i in tiles.options(self.snap().tile("PLN-30").text)["items"]], ["A"])

    def test_a_draft_that_needed_you_is_ready_once_its_doc_changes(self):
        self.ask()
        self.look()
        self.later()
        self.listing["s1"] = "idle"
        self.whys["s1"] = "question"
        self.look()
        self.assertEqual(drafts.of(self.store, "PLN-30")["state"], "need")
        self.put_doc(EXAMPLE)
        self.assertIn("PLN-30: options ready", self.look())
        got = drafts.of(self.store, "PLN-30")
        self.assertEqual((got["state"], got.get("why")), ("ready", None))

    def test_a_draft_that_wrote_its_doc_then_asked_a_question_needs_you_until_the_doc_changes_again(self):
        self.ask()
        self.look()
        self.put_doc(EXAMPLE)
        self.later()
        self.listing["s1"] = "idle"
        self.whys["s1"] = "question"
        self.look()
        self.look()
        got = drafts.of(self.store, "PLN-30")
        self.assertEqual((got["state"], got["why"]), ("need", "it asked a question"))
        self.assertEqual(self.written, [])
        self.put_doc(ONE, bump=10**9)
        self.assertIn("PLN-30: options ready", self.look())
        self.assertEqual(drafts.of(self.store, "PLN-30")["state"], "ready")

    def test_a_redraft_that_finishes_without_changing_the_doc_needs_you_and_leaves_the_tile(self):
        self.ready()
        before = self.board
        self.ask(note="again")
        self.later()
        self.look()  # the redraft starts, s2
        self.later()
        self.listing["s2"] = "idle"
        said = self.look()
        got = drafts.of(self.store, "PLN-30")
        self.assertEqual((got["state"], got["why"]), ("need", "it finished without changing the research doc"))
        self.assertIn("PLN-30: the draft needs you: it finished without changing the research doc", said)
        self.assertEqual((self.board, len(self.written)), (before, 1))

    def test_a_redraft_that_rewrote_the_doc_is_ready_with_the_new_options(self):
        self.ready()
        self.ask(note="again")
        self.later()
        self.look()
        self.put_doc(ONE, bump=10**9)
        self.later()
        self.listing["s2"] = "idle"
        self.look()
        got = drafts.of(self.store, "PLN-30")
        self.assertEqual(got["state"], "ready")
        self.assertEqual(self.written[-1][1], drafts.block(drafts.read_doc(ONE), "2026-11-23", got["doc"], "again"))

    def test_a_write_that_fails_is_said_and_tried_again_on_the_next_look(self):
        self.ask("PLN-30")
        self.ask("PLN-31")
        self.look()
        self.put_doc(EXAMPLE)
        self.later()
        self.listing["s1"] = "idle"
        self.write_error = RuntimeError("commit failed")
        said = self.look()
        self.assertIn("PLN-30: the options could not be written: commit failed", said)
        got = drafts.of(self.store, "PLN-30")
        self.assertEqual((got["state"], got["read"]), ("working", None))
        self.write_error = None
        self.assertEqual(self.look(), ["PLN-30: options ready", "PLN-31: drafting options (session s2)"])

    def test_a_draft_just_asked_for_survives_a_look_whose_snapshot_lacks_its_tile(self):
        self.ask()
        self.board = self.board.replace("- [ ] PLN-30 **Pro theme: catch up, or retire it.**\n  Decide it.\n", "")
        self.assertIsNone(self.snap().tile("PLN-30"))
        self.assertEqual(self.look(), [])
        self.assertEqual((drafts.of(self.store, "PLN-30")["state"], self.launched), ("queued", []))
        self.later()
        self.assertIn("PLN-30: draft ended, its tile is gone", self.look())
        self.assertIsNone(drafts.of(self.store, "PLN-30"))

    def test_a_redraft_that_needs_you_is_not_made_ready_by_the_last_draft_s_doc(self):
        self.ready()
        self.ask(note="again")
        self.later()
        self.look()  # the old draft is ended and the new one starts, s2
        self.later()
        self.listing["s2"] = "idle"
        self.whys["s2"] = "question"
        self.look()
        self.assertEqual(drafts.of(self.store, "PLN-30")["state"], "need")

    def test_a_doc_read_again_with_nothing_new_writes_nothing(self):
        self.ready()
        path = self.put_doc(EXAMPLE, bump=10**9)
        self.assertEqual(self.look(), [])
        self.assertEqual(len(self.written), 1)
        self.assertEqual(drafts.of(self.store, "PLN-30")["read"], path.stat().st_mtime_ns)

    def test_a_changed_doc_waits_while_the_session_is_busy(self):
        self.ready()
        was = drafts.of(self.store, "PLN-30")["read"]
        self.listing["s1"] = "busy"  # someone attached and set it to work
        self.put_doc(ONE, bump=10**9)
        self.look()
        self.assertEqual(len(self.written), 1)
        self.assertEqual(drafts.of(self.store, "PLN-30")["read"], was)
        self.listing["s1"] = "idle"
        self.look()
        self.assertEqual(len(self.written), 2)

    def test_a_draft_ends_when_its_tile_is_archived_or_leaves_the_decision_column(self):
        self.ask("PLN-30")
        self.look()
        self.board = self.board.replace("## Later\n\n", "## Later\n\n- [ ] PLN-30 **Pro theme.**\n  Decide it.\n").replace(
            "- [ ] PLN-30 **Pro theme: catch up, or retire it.**\n  Decide it.\n", "", 1)
        self.assertEqual(self.snap().tile("PLN-30").column, "Later")
        self.assertIn("PLN-30: draft ended, its tile is no longer a decision", self.look())
        self.assertIsNone(drafts.of(self.store, "PLN-30"))
        self.assertEqual(self.retired, ["s1"])
        self.assertFalse(self.wt("PLN-30").exists())
        self.ask("PLN-31")
        self.look()
        self.assertEqual(drafts.of(self.store, "PLN-31")["state"], "working")
        self.board = self.board.replace("- [ ] PLN-31 **Other.**\n  Decide that.\n", "")
        self.archive = "## Archived\n\n- [x] PLN-31 **Other.**\n  **Decision 2026-11-23:** that.\n"
        self.assertIn("PLN-31: draft ended, its tile is archived", self.look())
        self.assertIsNone(drafts.of(self.store, "PLN-31"))
        self.assertFalse(self.wt("PLN-31").exists())

    def test_a_queued_draft_whose_tile_was_decided_is_dropped_with_no_git_or_session_call(self):
        self.ask()
        self.board = self.board.replace("## Later", "## Done\n\n- [x] PLN-30 **Pro theme.**\n  **Decision 2026-11-23:** retire it.\n\n## Later").replace(
            "- [ ] PLN-30 **Pro theme: catch up, or retire it.**\n  Decide it.\n", "", 1)
        with mock.patch.object(pickup, "_git") as run:
            said = self.look()
        run.assert_not_called()
        self.assertEqual((self.retired, self.listed, self.launched), ([], 0, []))
        self.assertIn("PLN-30: draft ended, its tile is done", said)
        self.assertEqual(drafts.live(self.store), [])

    def test_a_draft_left_ending_is_ended_on_the_next_look(self):
        self.ask()
        self.look()
        self.stubborn = True
        self.assertFalse(drafts.end(self.store, "PLN-30", sessions=self.sessions))
        self.assertEqual(drafts.of(self.store, "PLN-30")["state"], "ending")
        self.stubborn = False
        self.assertIn("PLN-30: draft ended", self.look())
        self.assertEqual(drafts.live(self.store), [])
        self.assertFalse(self.wt().exists())

    def test_the_next_queued_draft_starts_on_the_look_after_the_one_at_work_is_ready(self):
        self.ask("PLN-30")
        self.ask("PLN-31")
        self.look()
        self.put_doc(EXAMPLE)
        self.later()
        self.listing["s1"] = "idle"
        said = self.look()
        self.assertEqual(said, ["PLN-30: options ready", "PLN-31: drafting options (session s2)"])
        self.assertEqual(drafts.of(self.store, "PLN-30")["state"], "ready")
        self.assertEqual(drafts.of(self.store, "PLN-31")["state"], "working")
        self.assertEqual(self.launched, ["PLN-30", "PLN-31"])

    def test_a_started_draft_is_told_its_master_and_the_tiles_it_blocks(self):
        self.board = self.board.replace("  Decide it.\n", "  Decide it.\n  Part of PLN-40.\n", 1) + "- [ ] PLN-40 **Master.**\n  The big story.\n"
        self.ask()
        with mock.patch.object(drafts, "start", return_value={"state": "need", "why": "x"}) as start:
            self.look()
        tile, context, blocks = start.call_args.args[2:5]
        self.assertTrue(tile.startswith("- [ ] PLN-30 "))
        self.assertIn("The big story.", context)
        self.assertEqual(blocks, [{"key": "PLN-32", "title": "Waits"}])

    def test_a_queued_draft_waits_while_an_older_one_for_its_key_is_still_ending(self):
        self.ask()
        self.look()
        self.stubborn = True
        self.later()
        self.ask(note="again")  # the old one is marked ending, and its session won't go
        self.later()
        self.look()
        self.assertEqual(self.launched, ["PLN-30"])
        self.assertEqual([d["state"] for d in drafts.live(self.store)], ["ending", "queued"])
        self.stubborn = False
        said = self.look()
        self.assertEqual(said, ["PLN-30: draft ended", "PLN-30: drafting options (session s2)"])
        self.assertEqual(self.launched, ["PLN-30", "PLN-30"])
        self.assertEqual([(d["state"], d["id"]) for d in drafts.live(self.store)], [("working", "s2")])
        self.assertTrue(self.wt().exists())

    def test_a_queued_draft_for_another_key_starts_while_one_key_s_ending_record_lingers(self):
        self.ask("PLN-30")
        self.look()
        self.stubborn = True
        self.later()
        self.ask("PLN-30", note="again")  # waits for the old one, whose session won't go
        self.ask("PLN-31")
        self.look()
        self.assertEqual(self.launched, ["PLN-30", "PLN-31"])
        self.assertEqual([(d["key"], d["state"]) for d in drafts.live(self.store)],
                         [("PLN-30", "ending"), ("PLN-30", "queued"), ("PLN-31", "working")])

    def test_the_record_of_what_a_lost_start_made_is_not_dropped_with_the_stopped_one(self):
        self.ask()
        stopped = []

        def mark(draft):  # stopped, with its cleanup still to run
            with jobs.Ledger(self.store).change() as state:
                state["drafts"][0]["state"] = "ending"
            stopped.append(dict(drafts.live(self.store)[0]))
            self.stubborn = True
        self.on_launch = mark
        self.look()
        self.assertEqual([(d["state"], d.get("id")) for d in drafts.live(self.store)], [("ending", None), ("ending", "s1")])
        self.assertTrue(drafts._finish(self.store, stopped[0], self.sessions))
        self.assertEqual([d.get("id") for d in drafts.live(self.store)], ["s1"])
        self.stubborn = False
        self.look()
        self.assertEqual(drafts.live(self.store), [])
        self.assertFalse(self.wt().exists())

    def test_a_draft_stopped_while_it_was_starting_is_not_written_over_and_what_started_is_ended(self):
        self.ask()
        self.on_launch = lambda draft: drafts.end(self.store, "PLN-30", sessions=self.sessions)
        said = self.look()
        self.assertIsNone(drafts.of(self.store, "PLN-30"))
        self.assertEqual(self.retired, ["s1"])
        self.assertFalse(self.wt().exists())
        self.assertIn("PLN-30: the draft was stopped while it started, so its session and worktrees are ended", said)

    def test_a_draft_replaced_while_it_was_starting_is_not_written_over(self):
        self.ask()
        self.on_launch = lambda draft: (self.later(), self.ask(note="again"))
        self.look()
        self.assertEqual([(d["state"], d["note"]) for d in drafts.live(self.store)], [("queued", "again")])
        self.assertEqual(self.retired, ["s1"])
        self.assertFalse(self.wt().exists())

    def test_the_summary_gives_each_draft_its_view(self):
        self.ask("PLN-30")
        self.ask("PLN-31")
        self.look()
        with jobs.Ledger(self.store).change() as state:
            state["drafts"].append({"key": "PLN-32", "state": "ending", "title": "x", "note": None, "doc": "d", "asked_at": 5})
        got = drafts.summary(self.store)
        self.assertEqual([d["key"] for d in got], ["PLN-30", "PLN-31"])
        self.assertEqual(got[0], {"key": "PLN-30", "state": "working", "why": None, "id": "s1", "started": self.clock,
                                  "asked_at": self.clock, "view": drafts.view({"state": "working"})})
        self.assertEqual(got[1], {"key": "PLN-31", "state": "queued", "why": None, "id": None, "started": None,
                                  "asked_at": self.clock, "view": {"label": "queued to draft", "actions": ["stop"]}})

    def test_busy_and_docs_say_what_the_drafts_are_doing(self):
        self.assertFalse(drafts.busy(self.store))
        self.assertEqual(drafts.docs(self.store), {})
        self.ready()
        self.assertFalse(drafts.busy(self.store))
        self.assertEqual(drafts.docs(self.store), {"PLN-30": self.doc().stat().st_mtime_ns})
        self.ask("PLN-31")
        self.assertTrue(drafts.busy(self.store))
        self.assertEqual(drafts.docs(self.store)["PLN-31"], None)


class WhyNot(Repos):
    OK = dict(installed=lambda: True, lacks=lambda: [])

    def test_draft_options_runs_with_pickup_off(self):
        self.assertFalse(self.store.pickup)
        self.assertIsNone(drafts.why_not(self.store, **self.OK))

    def test_it_says_what_is_missing_in_pickup_s_words(self):
        (self.plans / "board.json").write_text(json.dumps({}))
        why = drafts.why_not(stores.Store(self.plans), **self.OK)
        self.assertTrue(why.startswith('Draft options is off: board.json names no "app" repo'), why)
        self.assertIn("`", drafts.why_not(self.store, installed=lambda: False, lacks=lambda: []))

    def test_a_plan_store_cannot_draft(self):
        (self.plans / "board.json").write_text(json.dumps({"app": "../app", "permission": "plan"}))
        self.assertEqual(drafts.why_not(stores.Store(self.plans), **self.OK),
                         "Draft options is off: the store's permission is plan, so a session can't write the research doc.")

    def off(self, **cfg):
        (self.plans / "board.json").write_text(json.dumps(cfg))
        return pickup.why_off(stores.Store(self.plans), **self.OK)

    def test_pickup_s_own_reasons_read_as_they_did(self):
        top = self.top
        self.assertEqual(self.off(app="../app"), 'Pickup is off for this store: set "pickup": true in its board.json to turn it on.')
        self.assertEqual(self.off(pickup=True), 'Pickup is off: board.json names no "app" repo for sessions to work in, so set "app" to its path.')
        self.assertEqual(self.off(pickup=True, app="../nowhere"), f'Pickup is off: the app folder isn\'t there at {top}/nowhere, so fix "app" in board.json.')
        self.assertEqual(self.off(pickup=True, app="../app", worktree="/tmp/{key}"),
                         'Pickup is off: "worktree" in board.json (/tmp/{key}) must be a path relative to the folder the app is in, climbing at most one folder (../), so fix it.')
        self.assertEqual(self.off(pickup=True, app="../app", repos={"a pi": "../a"}), 'Pickup is off: the repo name "a pi" must be letters, digits, ".", "_" or "-", so fix "repos" in board.json.')
        self.assertEqual(self.off(pickup=True, app="../app", agent="bogus"), 'Pickup is off: "agent" must be one of claude, copilot, agy, not \'bogus\', so fix "agent" in board.json.')
        self.assertEqual(self.gone(),
                         f"Pickup is off: `{pickup.agent.cli()}` isn't on your PATH, so install {pickup.agent.label()} or put it there and restart the board.")
        self.assertEqual(self.lacking(["a", "b"]), f"Pickup is off: the installed {pickup.agent.label()} lacks a and b, so update it ({pickup.agent.update_hint()}) and restart the board.")

    def gone(self):
        (self.plans / "board.json").write_text(json.dumps({"pickup": True, "app": "../app"}))
        return pickup.why_off(stores.Store(self.plans), installed=lambda: False, lacks=lambda: [])

    def lacking(self, names):
        (self.plans / "board.json").write_text(json.dumps({"pickup": True, "app": "../app"}))
        return pickup.why_off(stores.Store(self.plans), installed=lambda: True, lacks=lambda: names)


PLANS_BOARD = """# Plans

## Decisions

- [ ] PLN-30 **Pro theme: catch up, or retire it.**
  Decide it.
- [ ] An unkeyed one.

## To do

- [ ] PLN-40 **A story.**
"""
BACKLOG_BOARD = """# Backlog

## Design decision

- [ ] PLN-50 **Auth: keep it or not.**
  Decide that.
"""
OPTIONS = tiles.options_block("2026-11-23", [{"letter": "A", "title": "Restyle it", "summary": "Only this."}], "A", None, None)


class Server(Repos):
    """The Repos fixture with a plans board and a backlog, the server's sessions adapter swapped
    for the fixture's, Draft options able to run, and commits recorded instead of made."""

    def setUp(self):
        super().setUp()
        (self.plans / "build.md").write_text(PLANS_BOARD, encoding="utf-8")
        (self.plans / "plan.md").write_text(BACKLOG_BOARD, encoding="utf-8")
        for target, name, value in ((serve, "SESSIONS", self.sessions), (drafts, "why_not", lambda store: None),
                                    (pickup, "why_off", lambda store: None)):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(serve.gitlog, "record")
        self.record = p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(pickup, "log")
        self.log = p.start()
        self.addCleanup(p.stop)
        self.addCleanup(serve.DRAFTS_ASKED.clear)

    def board(self, name="build.md"):
        return (self.plans / name).read_text(encoding="utf-8")

    def ask(self, data, name="build.md"):
        return serve.draft_request(self.store, self.plans / name, data)

    def test_write_options_puts_the_block_on_a_backlog_tile_and_commits_it(self):
        serve.write_options(self.store, "PLN-50", OPTIONS, "board: PLN-50 options drafted")
        self.assertEqual(tiles.options(self.board("plan.md"))["recommends"], "A")
        self.assertEqual(self.board("build.md"), PLANS_BOARD)
        self.record.assert_called_once_with(self.store, [self.plans / "plan.md"], "board: PLN-50 options drafted")

    def test_write_options_puts_the_block_on_a_plans_tile(self):
        serve.write_options(self.store, "PLN-30", OPTIONS, "board: PLN-30 options drafted")
        tile = tiles.Snapshot.read(self.store).tile("PLN-30")
        self.assertEqual(tiles.options(tile.text)["items"][0]["title"], "Restyle it")
        self.assertEqual(self.board("plan.md"), BACKLOG_BOARD)
        self.record.assert_called_once_with(self.store, [self.plans / "build.md"], "board: PLN-30 options drafted")

    def test_write_options_leaves_the_boards_alone_for_a_key_on_neither(self):
        with self.assertRaises(KeyError):
            serve.write_options(self.store, "PLN-99", OPTIONS, "board: PLN-99 options drafted")
        self.assertEqual((self.board("build.md"), self.board("plan.md")), (PLANS_BOARD, BACKLOG_BOARD))
        self.record.assert_not_called()

    def test_write_options_writes_nothing_when_the_tile_has_the_block_already(self):
        serve.write_options(self.store, "PLN-30", OPTIONS, "board: PLN-30 options drafted")
        before = (self.plans / "build.md").stat().st_mtime_ns
        serve.write_options(self.store, "PLN-30", OPTIONS, "board: PLN-30 options drafted")
        self.assertEqual((self.plans / "build.md").stat().st_mtime_ns, before)
        self.assertEqual(self.record.call_count, 1)

    def test_a_draft_asked_for_is_queued_and_the_reply_carries_the_drafts(self):
        status, reply = self.ask({"key": "PLN-30", "note": "keep it short"})
        self.assertEqual(status, 200, reply)
        self.assertEqual([(d["key"], d["state"]) for d in reply["drafts"]], [("PLN-30", "queued")])
        self.assertIsNone(reply["draftsOff"])
        self.assertIn("jobs", reply)
        self.assertEqual(drafts.of(self.store, "PLN-30")["note"], "keep it short")
        self.assertTrue(serve.DRAFTS_ASKED.is_set())

    def test_a_backlog_decision_can_be_drafted(self):
        status, reply = self.ask({"key": "PLN-50"}, "plan.md")
        self.assertEqual(status, 200, reply)
        self.assertEqual(drafts.of(self.store, "PLN-50")["state"], "queued")

    def test_a_tile_that_is_not_a_decision_on_that_board_is_refused(self):
        for key, name in (("PLN-40", "build.md"), ("PLN-50", "build.md"), ("PLN-99", "build.md"), ("", "build.md")):
            status, reply = self.ask({"key": key}, name)
            self.assertEqual((status, reply), (404, {"error": f"{key} is not a decision on this board"}))
        self.assertEqual(drafts.live(self.store), [])
        self.assertFalse(serve.DRAFTS_ASKED.is_set())

    def test_a_draft_is_refused_when_draft_options_cannot_run(self):
        with mock.patch.object(drafts, "why_not", lambda store: "Draft options is off: no app."):
            status, reply = self.ask({"key": "PLN-30"})
        self.assertEqual((status, reply), (409, {"error": "Draft options is off: no app."}))
        self.assertEqual(drafts.live(self.store), [])

    def test_a_draft_can_be_stopped_when_draft_options_cannot_run(self):
        self.ask({"key": "PLN-30"})
        with mock.patch.object(drafts, "why_not", lambda store: "Draft options is off: no app."):
            status, reply = self.ask({"key": "PLN-30", "stop": True})
        self.assertEqual(status, 200, reply)
        self.assertEqual((reply["drafts"], reply["draftsOff"]), ([], "Draft options is off: no app."))
        self.assertEqual(drafts.live(self.store), [])

    def test_a_stop_that_cannot_finish_leaves_the_draft_ending_for_the_look(self):
        self.ask({"key": "PLN-30"})
        serve.DRAFTS_ASKED.clear()
        with mock.patch.object(drafts, "_finish", side_effect=subprocess.TimeoutExpired("git", 60)):
            status, reply = self.ask({"key": "PLN-30", "stop": True})
        self.assertEqual((status, reply["drafts"]), (200, []))
        self.assertEqual([d["state"] for d in drafts.live(self.store)], ["ending"])
        self.assertTrue(serve.DRAFTS_ASKED.is_set())

    def decide(self, key="PLN-30"):
        text = self.board()
        tile = tiles.Snapshot.read(self.store).tile(key)
        rest, _ = tiles.take(self.store, text, [key])
        data = {"mtime": serve.mtime(self.plans / "build.md"), "text": rest, "tile": tile.text, "docs": [], "answer": "retire it."}
        return serve.decide_tile(self.store, self.plans / "build.md", data, "2026-11-23")

    def test_deciding_a_tile_ends_its_draft(self):
        self.ask({"key": "PLN-30"})
        with jobs.Ledger(self.store).change() as state:
            state["drafts"][0].update(state="ready", id="s1")
        status, reply = self.decide()
        self.assertEqual(status, 200, reply)
        self.assertEqual(self.retired, ["s1"])
        self.assertEqual(drafts.live(self.store), [])
        self.assertIn("PLN-30: draft ended", [c.args[0] for c in self.log.call_args_list])

    def test_a_draft_that_will_not_end_does_not_fail_the_decision(self):
        self.ask({"key": "PLN-30"})
        with mock.patch.object(drafts, "end", side_effect=OSError("no git")), mock.patch.object(pickup, "log") as log:
            status, reply = self.decide()
        self.assertEqual(status, 200, reply)
        self.assertNotIn("PLN-30 **", self.board())
        self.assertEqual([c.args[0] for c in log.call_args_list], ["PLN-30: the draft could not be ended: no git"])

    def test_the_pickup_reply_carries_the_drafts_why_they_cannot_run_and_the_organize_and_split_runs(self):
        self.ask({"key": "PLN-30"})
        with mock.patch.object(drafts, "why_not", lambda store: "Draft options is off: no app."):
            got = serve.pickup_reply(self.store)
        self.assertEqual({k: v for k, v in got.items() if k not in ("drafts", "draftsOff", "organize", "split")}, pickup.summary(self.store))
        self.assertIsNone(got["organize"])
        self.assertIsNone(got["split"])
        self.assertEqual(got["drafts"], drafts.summary(self.store))
        self.assertEqual(got["draftsOff"], "Draft options is off: no app.")

    def test_the_server_s_look_writes_a_finished_draft_s_options_onto_its_tile(self):
        now = self.sessions.now()
        sessions = pickup.Sessions(retire=self.retire, sessions=lambda: {"s1": "idle"}, why=lambda sid: None, now=lambda: now)
        doc = "active/2026-11-23-pln-50-options.md"
        (self.plans / "active").mkdir()
        (self.plans / doc).write_text("# PLN-50 options: Auth\n\nRecommends: B\n\n## A. Keep it\n\nAs it is.\n\n## B. Drop it\n\nGone.\n",
                                      encoding="utf-8")
        with jobs.Ledger(self.store).change() as state:
            state["drafts"] = [{"key": "PLN-50", "state": "working", "title": "Auth", "note": None, "doc": doc, "asked_at": 1,
                                "id": "s1", "started": now - jobs.SETTLE - 1, "read": None}]
        with mock.patch.object(serve, "SESSIONS", sessions):
            failed = serve.look_drafts(self.store, set())
        said = [c.args[0] for c in self.log.call_args_list]
        self.assertEqual(failed, set())
        self.assertFalse([line for line in said if drafts.UNWRITTEN in line], said)
        got = tiles.options(self.board("plan.md"))
        self.assertEqual(([it["letter"] for it in got["items"]], got["recommends"]), (["A", "B"], "B"))
        self.assertEqual(drafts.of(self.store, "PLN-50")["state"], "ready")
        self.record.assert_called_once_with(self.store, [self.plans / "plan.md"], "board: PLN-50 options drafted")
        self.assertIn("PLN-50: options ready", said)

    def test_a_stop_works_for_a_tile_that_has_left_its_decision_column(self):
        self.ask({"key": "PLN-30"})
        (self.plans / "build.md").write_text(PLANS_BOARD.replace("## Decisions", "## Later"), encoding="utf-8")
        status, reply = self.ask({"key": "PLN-30", "stop": True})
        self.assertEqual((status, reply["drafts"]), (200, []))
        self.assertEqual(drafts.live(self.store), [])

    def test_a_stop_for_a_key_with_no_draft_is_refused(self):
        self.assertEqual(self.ask({"key": "PLN-30", "stop": True}), (404, {"error": "PLN-30 has no draft"}))

    def test_a_request_that_queues_nothing_is_an_error(self):
        with mock.patch.object(jobs.Ledger, "change", side_effect=OSError("disk full")):
            status, reply = self.ask({"key": "PLN-30"})
        self.assertEqual((status, reply), (500, {"error": "disk full"}))
        self.assertEqual(drafts.live(self.store), [])
        self.assertEqual([c.args[0] for c in self.log.call_args_list], ["PLN-30: the draft could not be asked for: disk full"])

    def test_a_request_whose_earlier_draft_will_not_end_yet_is_queued(self):
        self.ask({"key": "PLN-30"})
        with mock.patch.object(drafts, "_finish", side_effect=subprocess.TimeoutExpired("git", 60)):
            status, reply = self.ask({"key": "PLN-30", "note": "again"})
        self.assertEqual(status, 200, reply)
        self.assertEqual([d["state"] for d in drafts.live(self.store)], ["ending", "queued"])
        self.assertEqual(len(self.log.call_args_list), 1)
        self.assertIn("PLN-30: the draft before it could not be ended yet", self.log.call_args.args[0])

    def post(self, path, body):
        """A POST to a board server on this store, as the page sends it: (status, reply)."""
        import http.client
        import threading
        srv = serve.ThreadingHTTPServer(("127.0.0.1", 0), type("Handler", (serve.Handler,), {"store": self.store}))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
            c.request("POST", path, body=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
            r = c.getresponse()
            out = (r.status, json.loads(r.read()))
            c.close()
            return out
        finally:
            srv.shutdown()
            srv.server_close()

    def clear(self):
        return self.post("/api/clear?file=build.md", {"mtime": serve.mtime(self.plans / "build.md"),
                                                     "text": self.board(), "column": "Decisions"})

    def test_clearing_a_column_ends_the_drafts_of_the_tiles_it_archives(self):
        self.ask({"key": "PLN-30"})
        with jobs.Ledger(self.store).change() as state:
            state["drafts"][0].update(state="ready", id="s1")
        status, reply = self.clear()
        self.assertEqual(status, 200, reply)
        self.assertEqual(self.retired, ["s1"])
        self.assertEqual(drafts.live(self.store), [])

    def test_a_clear_whose_draft_will_not_end_still_clears(self):
        self.ask({"key": "PLN-30"})
        with mock.patch.object(drafts, "end", side_effect=OSError("no git")):
            status, reply = self.clear()
        self.assertEqual(status, 200, reply)
        self.assertNotIn("PLN-30 **", self.board())
        self.assertIn("PLN-30: the draft could not be ended: no git", [c.args[0] for c in self.log.call_args_list])

    def discard(self):
        tile = tiles.Snapshot.read(self.store).tile("PLN-30").text
        return self.post("/api/pickup", {"key": "PLN-30", "discard": True, "tile": tile})

    def test_deleting_a_tile_ends_its_draft(self):
        self.ask({"key": "PLN-30"})
        with jobs.Ledger(self.store).change() as state:
            state["drafts"][0].update(state="ready", id="s1")
        status, reply = self.discard()
        self.assertEqual((status, reply["drafts"]), (200, []))
        self.assertEqual(self.retired, ["s1"])
        self.assertEqual(drafts.live(self.store), [])

    def test_a_delete_whose_draft_will_not_end_still_answers(self):
        self.ask({"key": "PLN-30"})
        with mock.patch.object(drafts, "end", side_effect=OSError("no git")):
            status, reply = self.discard()
        self.assertEqual(status, 200, reply)
        self.assertIn("PLN-30: the draft could not be ended: no git", [c.args[0] for c in self.log.call_args_list])


class Attach(Repos):
    def attach(self, key="PLN-30"):
        return cli.session_of([key, "--dir", str(self.plans)])

    def test_a_key_with_no_story_session_attaches_to_its_draft(self):
        drafts.request(self.store, "PLN-30", TILE, None, sessions=self.sessions)
        with jobs.Ledger(self.store).change() as state:
            state["drafts"][0].update(state="ready", id="d1")
        self.assertEqual(self.attach(), "d1")

    def test_a_key_with_both_attaches_to_its_story_session(self):
        drafts.request(self.store, "PLN-30", TILE, None, sessions=self.sessions)
        with jobs.Ledger(self.store).change() as state:
            state["drafts"][0].update(state="ready", id="d1")
            state["jobs"].append({"key": "PLN-30", "state": "idle", "id": "j1"})
        self.assertEqual(self.attach(), "j1")

    def test_a_draft_being_ended_is_not_attached_to(self):
        drafts.request(self.store, "PLN-30", TILE, None, sessions=self.sessions)
        with jobs.Ledger(self.store).change() as state:
            state["drafts"][0].update(state="ending", id="d1")
        with mock.patch("sys.stderr") as err:
            self.assertIsNone(self.attach())
        self.assertIn("PLN-30 has no session", "".join(c.args[0] for c in err.write.call_args_list))


if __name__ == "__main__":
    unittest.main()
