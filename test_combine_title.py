#!/usr/bin/env python3
"""The combined story's title: the agent's one-shot suggestion (ask in each agent module, the
question, /api/combine/title) and the title the person chooses (tiles.combine, /api/combine).

    python3 -m unittest test_combine_title
"""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kandalf import agent, agy_cli, claude_code, copilot_cli, oneshot
from kandalf import serve
from kandalf import store as stores
from kandalf import tiles as reader
from test_combine import ARCHIVE, BUILD, OTHER, PLAN, SURVIVOR


def done(out="", code=0):
    return subprocess.CompletedProcess([], code, stdout=out, stderr="")


class TheReplyAsATitle(unittest.TestCase):
    def test_it_is_the_first_non_empty_line_without_quotes_bold_or_a_key(self):
        self.assertEqual(oneshot.reply_title('\n\n  "Checkout and receipts"\nBecause...'), "Checkout and receipts")
        self.assertEqual(oneshot.reply_title("**PLN-72: Checkout.**"), "Checkout.")
        self.assertEqual(oneshot.reply_title("“Smart quotes”"), "Smart quotes")
        self.assertEqual(oneshot.reply_title("'Single'"), "Single")

    def test_it_is_capped_at_120_characters_and_empty_replies_give_none(self):
        self.assertEqual(len(oneshot.reply_title("x" * 300)), 120)
        for blank in ("", "  \n \n", '""', "**  **"):
            self.assertIsNone(oneshot.reply_title(blank))


class EachAgentsAsk(unittest.TestCase):
    def run_ask(self, mod, out=done("Merged title\n"), **kw):
        with mock.patch.object(mod.subprocess, "run", return_value=out) as run:
            got = mod.ask("which title?", "/tmp/empty", 45, **kw)
        return got, run

    def test_claude_runs_print_mode_with_no_tools_in_the_given_folder(self):
        got, run = self.run_ask(claude_code)
        self.assertEqual(got, "Merged title")
        self.assertEqual(run.call_args[0][0], ["claude", "-p", "--tools", "", "--no-session-persistence", "which title?"])
        self.assertEqual((run.call_args[1]["cwd"], run.call_args[1]["timeout"]), ("/tmp/empty", 45))
        self.assertEqual(run.call_args[1]["stdin"], subprocess.DEVNULL)

    def test_copilot_runs_a_silent_prompt_with_no_tool_permissions(self):
        got, run = self.run_ask(copilot_cli, done('"Merged title"\n'))
        self.assertEqual(got, "Merged title")
        self.assertEqual(run.call_args[0][0], ["copilot", "-p", "which title?", "-s", "--no-ask-user"])
        self.assertEqual(run.call_args[1]["cwd"], "/tmp/empty")

    def test_antigravity_has_no_one_shot_mode_so_it_gives_none_and_runs_nothing(self):
        with mock.patch.object(agy_cli.subprocess, "run") as run, mock.patch.object(agy_cli.subprocess, "Popen") as popen:
            self.assertIsNone(agy_cli.ask("q", "/tmp/empty", 45))
        run.assert_not_called()
        popen.assert_not_called()

    def test_an_error_a_timeout_a_failed_exit_and_an_empty_reply_give_none(self):
        for mod in (claude_code, copilot_cli):
            self.assertIsNone(self.run_ask(mod, done("Title", 1))[0])
            self.assertIsNone(self.run_ask(mod, done("  \n"))[0])
            with mock.patch.object(mod.subprocess, "run", side_effect=subprocess.TimeoutExpired("x", 45)):
                self.assertIsNone(mod.ask("q", "/tmp", 45))
            with mock.patch.object(mod.subprocess, "run", side_effect=OSError("no such file")):
                self.assertIsNone(mod.ask("q", "/tmp", 45))


class AskGoesToTheAgentInUse(unittest.TestCase):
    def test_it_routes_to_the_module_in_use(self):
        self.addCleanup(agent.use, "claude")
        for name, mod in (("claude", claude_code), ("copilot", copilot_cli), ("agy", agy_cli)):
            agent.use(name)
            with mock.patch.object(mod, "ask", return_value="T") as ask:
                self.assertEqual(agent.ask("q", "/tmp/x", 45), "T")
            ask.assert_called_once_with("q", "/tmp/x", 45)


class TheQuestion(unittest.TestCase):
    def test_it_lists_each_story_with_the_start_of_its_description_and_asks_for_one_title(self):
        store = stores.Store(Path(tempfile.mkdtemp()).resolve())
        long = "- [ ] PLN-73 **Other.** " + "word " * 80 + "\n  Domain: Admin\n  - [ ] a task"
        text = serve.combine_question(store, [SURVIVOR, long])
        self.assertIn("PLN-72: Main", text)
        self.assertIn("PLN-73: Other", text)
        self.assertIn("Reply with one short title for a single story that covers all of them, and nothing else.", text)
        self.assertNotIn("Domain", text)
        self.assertNotIn("a task", text)
        self.assertLess(len(text), 900)
        self.assertIn("word word", text)


class TheSuggestionRoute(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp()).resolve()
        (self.dir / "archive").mkdir()
        for name, text in {"build.md": BUILD, "plan.md": PLAN, "archive/archived.md": ARCHIVE}.items():
            (self.dir / name).write_text(text)
        self.store = stores.Store(self.dir)

    def test_a_suggestion_comes_back_as_the_title_and_the_agent_runs_in_an_empty_folder(self):
        seen = {}

        def ask(text, cwd, timeout):
            seen.update(text=text, cwd=cwd, timeout=timeout, empty=not any(Path(cwd).iterdir()))
            return "Main and other"
        with mock.patch.object(serve.agent, "ask", ask):
            status, reply = serve.combine_suggestion(self.store, ["PLN-72", "PLN-73"])
        self.assertEqual((status, reply), (200, {"title": "Main and other"}))
        self.assertIn("PLN-73: Other", seen["text"])
        self.assertEqual((seen["timeout"], seen["empty"]), (45, True))
        self.assertFalse(Path(seen["cwd"]).exists())

    def test_no_suggestion_says_why_in_a_few_words(self):
        with mock.patch.object(serve.agent, "ask", return_value=None):
            status, reply = serve.combine_suggestion(self.store, ["PLN-72", "PLN-73"])
        self.assertEqual((status, reply["title"]), (200, None))
        self.assertIn("No suggestion", reply["why"])

    def test_keys_that_are_not_stories_are_refused(self):
        with mock.patch.object(serve.agent, "ask") as ask:
            self.assertEqual(serve.combine_suggestion(self.store, "PLN-72")[0], 400)
            self.assertEqual(serve.combine_suggestion(self.store, ["PLN-72"])[0], 400)
            self.assertEqual(serve.combine_suggestion(self.store, ["PLN-72", "PLN-999"])[0], 409)
        ask.assert_not_called()

    def test_the_server_is_threaded_so_a_slow_suggestion_blocks_no_other_request(self):
        from http.server import ThreadingHTTPServer
        self.assertTrue(issubclass(ThreadingHTTPServer, __import__("socketserver").ThreadingMixIn))
        self.assertIn("ThreadingHTTPServer(", Path(serve.__file__).read_text())


class TheChosenTitle(unittest.TestCase):
    def setUp(self):
        self.store = stores.Store(Path(tempfile.mkdtemp()).resolve())

    def test_the_survivors_bold_title_is_replaced_and_the_rest_of_its_line_kept(self):
        text = reader.combine(self.store, SURVIVOR, [OTHER], title="Checkout and receipts")
        self.assertTrue(text.startswith("- [ ] PLN-72 P2 ~4h **Checkout and receipts.** Part of PLN-70."))
        self.assertIn("**Main.** Part of PLN-70.", reader.combine(self.store, SURVIVOR, [OTHER]).split("\n")[0])

    def test_a_title_keeps_its_own_question_mark_and_never_doubles_a_full_stop(self):
        self.assertIn("**Why?**", reader.combine(self.store, SURVIVOR, [OTHER], title="Why?"))
        self.assertIn("**Done.** Part", reader.combine(self.store, SURVIVOR, [OTHER], title="Done."))

    def test_a_story_without_bold_has_its_whole_title_replaced(self):
        text = reader.combine(self.store, "- [ ] PLN-72 Plain words", [OTHER], title="New")
        self.assertEqual(text.split("\n")[0], "- [ ] PLN-72 **New.**")

    def test_the_other_stories_still_name_the_survivors_old_title_in_no_paragraph(self):
        text = reader.combine(self.store, SURVIVOR, [OTHER], title="New")
        self.assertIn("From PLN-73 (Other):", text)


class TheChosenTitleOnTheRoute(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp()).resolve()
        (self.dir / "archive").mkdir()
        for name, text in {"build.md": BUILD, "plan.md": PLAN, "archive/archived.md": ARCHIVE}.items():
            (self.dir / name).write_text(text)
        self.store = stores.Store(self.dir)
        patch = mock.patch.object(serve.gitlog, "record")
        self.record = patch.start()
        self.addCleanup(patch.stop)

    def text(self, name):
        return (self.dir / name).read_text()

    def test_a_title_replaces_the_survivors_and_the_commit_subject_is_unchanged(self):
        status, _ = serve.combine_stories(self.store, ["PLN-72", "PLN-73"], "PLN-72", title="  Fresh name ")
        self.assertEqual(status, 200)
        self.assertIn("PLN-72 **Fresh name.** Blocked by PLN-30.", self.text("build.md"))
        self.assertIn("From PLN-73 (Other):", self.text("build.md"))
        self.assertEqual(self.record.call_args[0][2], "board: PLN-73 combined into PLN-72")

    def test_no_title_leaves_the_survivors_title_alone(self):
        serve.combine_stories(self.store, ["PLN-72", "PLN-73"], "PLN-72", title=None)
        self.assertIn("PLN-72 **Main.** Blocked by PLN-30.", self.text("build.md"))

    def test_a_title_that_is_not_one_is_refused_with_a_sentence_and_nothing_written(self):
        for bad, words in (("   ", "empty"), ("", "empty"), (5, "text"), ("two\nlines", "one line"),
                           ("x" * 121, "120"), ("a **b** c", "**")):
            status, reply = serve.combine_stories(self.store, ["PLN-72", "PLN-73"], "PLN-72", title=bad)
            self.assertEqual(status, 400, bad)
            self.assertIn(words, reply["error"])
            self.assertTrue(reply["error"].endswith("."))
        self.assertEqual(self.text("build.md"), BUILD)
        self.record.assert_not_called()

    def test_120_characters_is_allowed(self):
        self.assertEqual(serve.combine_stories(self.store, ["PLN-72", "PLN-73"], "PLN-72", title="x" * 120)[0], 200)


if __name__ == "__main__":
    unittest.main()
