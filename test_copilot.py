"""The Copilot CLI agent (copilot_cli.py) and the choice of agent (agent.py), with a stand-in
`copilot` on PATH."""

import contextlib
import io
import json
import os
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from kandalf import agent, copilot_cli, pickup
from kandalf import store as stores


class Copilot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.bin, self.work = root / "bin", root / "work"
        self.bin.mkdir()
        self.work.mkdir()
        fake = self.bin / "copilot"
        fake.write_text('#!/bin/sh\necho "$@" > args.txt\nsleep "${FAKE_SLEEP:-0}"\necho done working\n')
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        for patch in (mock.patch.object(copilot_cli, "STATE_DIR", root / "state"),
                      mock.patch.dict(os.environ, {"PATH": f"{self.bin}:{os.environ['PATH']}"})):
            patch.start()
            self.addCleanup(patch.stop)

    def wait_idle(self, sid):
        for _ in range(100):
            if copilot_cli.sessions().get(sid) == "idle":
                return
            time.sleep(0.05)
        self.fail("the session never ended")

    def test_a_started_session_runs_copilot_with_the_model_name_and_folders(self):
        began = copilot_cli.start(["--settings", "hooks.json", "--model", "gpt-5", "-n", "PLN-1 Title"], self.work, "auto", "do it", [self.work / "api"])
        self.wait_idle(began["id"])
        args = (self.work / "args.txt").read_text().split()
        self.assertIn("--session-id", args)
        for want in ("-p", "do it", "--output-format json", "--model", "gpt-5", "--name", "--allow-all-tools", "--add-dir"):
            self.assertIn(want, (self.work / "args.txt").read_text())
        self.assertNotIn("--settings", args)
        self.assertEqual(copilot_cli.known()[began["id"]], args[args.index("--session-id") + 1])
        self.assertIn("done working", copilot_cli.logs(began["id"]))

    def test_a_running_session_is_busy_and_a_stopped_one_is_only_known(self):
        with mock.patch.dict(os.environ, {"FAKE_SLEEP": "30"}):
            began = copilot_cli.start([], self.work, "auto", "go")
        sid = began["id"]
        self.assertEqual(copilot_cli.sessions(), {sid: "busy"})
        copilot_cli.stop(sid)
        self.assertEqual(copilot_cli.sessions(), {})
        self.assertIn(sid, copilot_cli.known())
        copilot_cli.remove(sid)
        self.assertEqual(copilot_cli.known(), {})

    def test_a_resumed_session_continues_the_full_id(self):
        first = copilot_cli.start([], self.work, "plan", "go")
        self.wait_idle(first["id"])
        full = copilot_cli.known()[first["id"]]
        again = copilot_cli.start(["--resume", full], self.work, "auto", "carry on")
        self.wait_idle(again["id"])
        self.assertIn(f"--resume={full}", (self.work / "args.txt").read_text())
        self.assertNotEqual(again["id"], first["id"])

    def test_the_board_hooks_are_linked_where_copilot_reads_them_and_kept_out_of_git(self):
        repo, tree, hooks = self.work / "app", self.work / "app-pln-1", self.work / "hooks.json"
        hooks.write_text("{}")
        git = lambda *a, cwd=repo: subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True).stdout
        repo.mkdir()
        git("init", "-q")
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "first")
        git("worktree", "add", "-q", "-b", "pln-1", str(tree))
        began = copilot_cli.start(["--settings", str(hooks)], tree, "auto", "go")
        self.wait_idle(began["id"])
        local = tree / ".claude" / "settings.local.json"
        self.assertEqual(local.resolve(), hooks.resolve())
        self.assertEqual(git("status", "--porcelain", cwd=tree), "?? args.txt\n")

    def test_a_copilot_session_is_given_the_store_and_bypass_allows_every_path(self):
        agent.use("copilot")
        self.addCleanup(agent.use, "claude")
        began = agent.start([], self.work, "bypassPermissions", "go", [], store_dir=self.work / "plans")
        self.wait_idle(began["id"])
        args = (self.work / "args.txt").read_text()
        self.assertIn(f"--add-dir {self.work / 'plans'}", args)
        self.assertIn("--allow-all", args.split())

    def test_cli_lacks_names_the_options_the_help_does_not_list(self):
        full = "  -p, --prompt <text>\n      --session-id <id>\n  -r, --resume [<v>]\n      --add-dir <d>\n  -n, --name <n>\n      --allow-all-tools\n      --allow-all-paths\n"
        self.assertEqual(copilot_cli.cli_lacks(lambda *a: full), [])
        self.assertEqual(copilot_cli.cli_lacks(lambda *a: full.replace("--session-id", "--other")), ["copilot --session-id"])

    def test_the_log_reads_as_what_was_said_the_tools_run_and_how_it_ended(self):
        events = [{"type": "session.start", "data": {}},
                  {"type": "tool.execution_start", "data": {"toolTitle": "Running command", "arguments": {"command": "make  test"}}},
                  {"type": "tool.execution_complete", "data": {"success": False, "error": {"message": "exit 2"}}},
                  {"type": "assistant.message_delta", "data": {"deltaContent": "Tests"}},
                  {"type": "assistant.message", "data": {"content": "Tests fail; fixing them.", "toolRequests": []}},
                  {"type": "result", "exitCode": 0}]
        raw = "\n".join(json.dumps(e) for e in events) + "\nnot json\n"
        self.assertEqual(copilot_cli.transcript(raw),
                         "● Running command: make test\n✗ failed: exit 2\nTests fail; fixing them.\n— finished\nnot json")

    def test_a_finished_session_waits_on_an_error_or_a_question_it_ended_with(self):
        self.assertEqual(copilot_cli.waiting_for("Done.\n— ended with exit code 1"), "error")
        self.assertEqual(copilot_cli.waiting_for("Should I drop the old table?\n● Viewing file\n— finished"), "question")
        self.assertIsNone(copilot_cli.waiting_for("All done.\n— finished"))
        self.assertIsNone(copilot_cli.waiting_for(""))

    def test_a_session_that_fails_before_any_output_says_its_exit_code(self):
        (self.bin / "copilot").write_text("#!/bin/sh\nexit 3\n")
        began = copilot_cli.start([], self.work, "auto", "go")
        self.wait_idle(began["id"])
        self.assertEqual(copilot_cli.waiting_for(copilot_cli.logs(began["id"])), "error")

    def test_attach_prints_the_transcript_then_opens_the_conversation_in_its_folder_with_its_reach(self):
        (self.bin / "copilot").write_text("#!/bin/sh\necho '{\"type\":\"assistant.message\",\"data\":{\"content\":\"Hi there.\"}}'\n")
        began = copilot_cli.start([], self.work, "auto", "go", [self.work / "plans"])
        self.wait_idle(began["id"])
        out = io.StringIO()
        with mock.patch.object(copilot_cli.os, "execvp") as execvp, mock.patch.object(copilot_cli.os, "chdir") as chdir:
            copilot_cli.attach(began["id"], out=out, poll=0)
        self.assertIn("Hi there.", out.getvalue())
        chdir.assert_called_once_with(str(self.work))
        execvp.assert_called_once_with("copilot", ["copilot", f"--resume={copilot_cli.known()[began['id']]}",
                                                   "--add-dir", str(self.work / "plans"), "--allow-all-tools", "--allow-all-paths"])

    def test_kandalf_attach_goes_to_the_agent_that_started_the_session(self):
        began = copilot_cli.start([], self.work, "auto", "go")
        self.wait_idle(began["id"])
        with mock.patch.object(copilot_cli, "attach", return_value=0) as mine, \
             mock.patch.object(agent.claude_code, "attach", return_value=0) as claudes:
            agent.open_session(began["id"])
            agent.open_session("ab12cd34")
        mine.assert_called_once_with(began["id"])
        claudes.assert_called_once_with("ab12cd34")

    def test_a_message_interrupts_a_running_copilot_session_and_resumes_the_same_conversation(self):
        self.addCleanup(agent.use, "claude")
        home = Path(tempfile.mkdtemp())
        (home / "board.json").write_text(json.dumps({"key": "PLN", "app": str(self.work), "agent": "copilot"}))
        store = stores.Store(home)  # a Copilot store: the agent in use is Copilot
        self.assertEqual(agent.name(), "copilot")
        with mock.patch.dict(os.environ, {"FAKE_SLEEP": "30"}):
            began = copilot_cli.start([], self.work, "auto", "go")
        old, full = began["id"], copilot_cli.known()[began["id"]]
        self.assertEqual(copilot_cli.sessions(), {old: "busy"})
        pickup.save(store, {"inflight": ["PLN-01"], "jobs": [{"key": "PLN-01", "title": "One", "state": "started", "id": old,
                                                              "session": full, "worktree": str(self.work), "branch": "pln-01-one", "model": "auto", "started": 0}]})
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(pickup.reply(store, "PLN-01", "Leave the CSS alone.", stop=copilot_cli.stop))
        self.assertNotEqual(copilot_cli.sessions().get(old), "busy")  # its process was stopped
        job = pickup.load(store)["jobs"][0]
        self.assertEqual(job["state"], "started")
        self.wait_idle(job["id"])
        args = (self.work / "args.txt").read_text()
        self.assertIn(f"--resume={full}", args)  # the same conversation
        self.assertIn("Leave the CSS alone.", args)
        self.assertIn("interrupted", args)


class Choice(unittest.TestCase):
    def tearDown(self):
        agent.use("claude")

    def test_the_agent_is_the_one_in_use(self):
        agent.use("copilot")
        self.assertEqual((agent.label(), agent.cli()), ("Copilot CLI", "copilot"))
        agent.use("claude")
        self.assertEqual((agent.label(), agent.cli(), agent.attach("ab12cd34")), ("Claude Code", "claude", "kandalf attach ab12cd34"))
        with self.assertRaises(ValueError):
            agent.use("gemini")


if __name__ == "__main__":
    unittest.main()
