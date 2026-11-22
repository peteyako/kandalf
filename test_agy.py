"""The Antigravity CLI agent (agy_cli.py) and its hooks/routing, with a stand-in `agy` on PATH."""

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

from kandalf import agent, agy_cli, autocommit, checkhook, pickup
from kandalf import store as stores


class Agy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.bin, self.work = root / "bin", root / "work"
        self.bin.mkdir()
        self.work.mkdir()
        fake = self.bin / "agy"
        fake.write_text(
            '#!/bin/sh\n'
            'printf "%s\\n" "$@" > args.txt\n'
            'echo \'{"event":"init","conversation_id":"11111111-2222-3333-4444-555555555555"}\'\n'
            'sleep "${FAKE_SLEEP:-0}"\n'
            'echo \'{"event":"step_update","step_update":{"step_type":"agent_response","state":"DONE","text_delta":"done working"}}\'\n'
            'echo \'{"event":"result","result":{"conversation_id":"11111111-2222-3333-4444-555555555555","status":"SUCCESS"}}\'\n'
        )
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        for patch in (mock.patch.object(agy_cli, "STATE_DIR", root / "state"),
                      mock.patch.dict(os.environ, {"PATH": f"{self.bin}:{os.environ['PATH']}"})):
            patch.start()
            self.addCleanup(patch.stop)

    def wait_idle(self, sid):
        for _ in range(100):
            if agy_cli.sessions().get(sid) == "idle":
                return
            time.sleep(0.05)
        self.fail("the session never ended")

    def test_a_started_session_runs_agy_with_the_model_mode_and_folders(self):
        began = agy_cli.start(["--settings", "hooks.json", "--model", "gemini-4-neon-high", "-n", "PLN-1 Title"],
                              self.work, "auto", "do it", [self.work / "api"])
        self.wait_idle(began["id"])
        args = (self.work / "args.txt").read_text().splitlines()
        self.assertIn("--output-format", args)
        self.assertIn("stream-json", args)
        self.assertIn("--model", args)
        self.assertIn("gemini-4-neon-high", args)
        self.assertIn("--mode", args)
        self.assertIn("auto", args)
        self.assertIn("--add-dir", args)
        self.assertIn(str(self.work / "api"), args)
        self.assertIn("--print=do it", args)
        self.assertNotIn("--settings", args)
        self.assertNotIn("-n", args)
        self.assertEqual(agy_cli.known()[began["id"]], "11111111-2222-3333-4444-555555555555")
        self.assertIn("done working", agy_cli.logs(began["id"]))

    def test_auto_model_omits_the_model_flag(self):
        began = agy_cli.start(["--model", "auto"], self.work, "acceptEdits", "do it")
        self.wait_idle(began["id"])
        args = (self.work / "args.txt").read_text().splitlines()
        self.assertNotIn("--model", args)
        self.assertIn("--mode", args)
        self.assertIn("accept-edits", args)

    def test_a_running_session_is_busy_and_a_stopped_one_is_only_known(self):
        with mock.patch.dict(os.environ, {"FAKE_SLEEP": "30"}):
            began = agy_cli.start([], self.work, "auto", "go")
        sid = began["id"]
        self.assertEqual(agy_cli.sessions(), {sid: "busy"})
        agy_cli.stop(sid)
        self.assertEqual(agy_cli.sessions(), {})
        self.assertIn(sid, agy_cli.known())
        agy_cli.remove(sid)
        self.assertEqual(agy_cli.known(), {})

    def test_a_resumed_session_continues_the_conversation_id(self):
        first = agy_cli.start([], self.work, "plan", "go")
        self.wait_idle(first["id"])
        full = agy_cli.known()[first["id"]]
        self.assertEqual(full, "11111111-2222-3333-4444-555555555555")
        again = agy_cli.start(["--resume", full], self.work, "auto", "carry on")
        self.wait_idle(again["id"])
        args = (self.work / "args.txt").read_text().splitlines()
        self.assertIn("--conversation", args)
        self.assertEqual(args[args.index("--conversation") + 1], full)
        self.assertNotEqual(again["id"], first["id"])

    def test_the_board_hooks_are_written_where_agy_reads_them_and_kept_out_of_git(self):
        repo, tree, hooks = self.work / "app", self.work / "app-pln-1", self.work / "hooks.json"
        hooks.write_text("{}")
        git = lambda *a, cwd=repo: subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True).stdout
        repo.mkdir()
        git("init", "-q")
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "first")
        git("worktree", "add", "-q", "-b", "pln-1", str(tree))
        began = agy_cli.start(["--settings", str(hooks)], tree, "auto", "go")
        self.wait_idle(began["id"])
        local = tree / ".agents" / "hooks.json"
        self.assertTrue(local.is_file())
        parsed = json.loads(local.read_text())
        self.assertIn("kandalf", parsed)
        self.assertEqual(git("status", "--porcelain", cwd=tree), "?? args.txt\n")

    def test_an_agy_session_is_given_the_store_and_bypass_skips_permissions(self):
        agent.use("agy")
        self.addCleanup(agent.use, "claude")
        began = agent.start([], self.work, "bypassPermissions", "go", [], store_dir=self.work / "plans")
        self.wait_idle(began["id"])
        args = (self.work / "args.txt").read_text().splitlines()
        self.assertIn("--add-dir", args)
        self.assertIn(str(self.work / "plans"), args)
        self.assertIn("--dangerously-skip-permissions", args)

    def test_cli_lacks_names_the_options_the_help_does_not_list(self):
        full = (
            "  --add-dir                       Add a directory\n"
            "  --conversation                  Resume\n"
            "  --dangerously-skip-permissions  Auto-approve\n"
            "  --mode                          Mode\n"
            "  --output-format                 Output format\n"
            "  --print                         Run a single prompt\n"
        )
        self.assertEqual(agy_cli.cli_lacks(lambda *a: full), [])
        self.assertEqual(agy_cli.cli_lacks(lambda *a: full.replace("--conversation", "--other")), ["agy --conversation"])

    def test_the_log_reads_stream_json_messages_tools_and_result(self):
        events = [
            {"event": "init", "conversation_id": "abc"},
            {"event": "step_update", "step_update": {"step_type": "tool", "state": "ACTIVE", "tool_name": "run_command",
                                                     "tool_info": {"parameters": {"CommandLine": "make  test"}}}},
            {"event": "step_update", "step_update": {"step_type": "tool", "state": "ERROR", "tool_name": "run_command",
                                                     "tool_info": {"error": "exit 2"}}},
            {"event": "step_update", "step_update": {"step_type": "agent_response", "state": "ACTIVE", "text_delta": "Tests fail; "}},
            {"event": "step_update", "step_update": {"step_type": "agent_response", "state": "DONE", "text_delta": "fixing them.\n"}},
            {"event": "result", "result": {"status": "SUCCESS"}},
        ]
        raw = "\n".join(json.dumps(e) for e in events) + "\nnot json\n"
        self.assertEqual(
            agy_cli.transcript(raw),
            "● run_command: make test\n✗ failed: exit 2\nTests fail; fixing them.\n— finished\nnot json",
        )

    def test_a_finished_session_waits_on_an_error_or_a_question_it_ended_with(self):
        self.assertEqual(agy_cli.waiting_for("Done.\n— ended with exit code 1"), "error")
        self.assertEqual(agy_cli.waiting_for("Done.\n— ended with status ERROR"), "error")
        self.assertEqual(agy_cli.waiting_for("Should I drop the old table?\n● read_file\n— finished"), "question")
        self.assertIsNone(agy_cli.waiting_for("All done.\n— finished"))
        self.assertIsNone(agy_cli.waiting_for(""))

    def test_a_session_that_fails_before_any_output_says_its_exit_code(self):
        (self.bin / "agy").write_text("#!/bin/sh\nexit 3\n")
        began = agy_cli.start([], self.work, "auto", "go")
        self.wait_idle(began["id"])
        self.assertEqual(agy_cli.waiting_for(agy_cli.logs(began["id"])), "error")

    def test_attach_prints_the_transcript_then_opens_the_conversation_in_its_folder_with_its_reach(self):
        began = agy_cli.start([], self.work, "auto", "go", [self.work / "plans"])
        self.wait_idle(began["id"])
        out = io.StringIO()
        with mock.patch.object(agy_cli.os, "execvp") as execvp, mock.patch.object(agy_cli.os, "chdir") as chdir:
            agy_cli.attach(began["id"], out=out, poll=0)
        self.assertIn("done working", out.getvalue())
        chdir.assert_called_once_with(str(self.work))
        execvp.assert_called_once_with(
            "agy",
            ["agy", "--conversation", "11111111-2222-3333-4444-555555555555",
             "--add-dir", str(self.work / "plans"), "--mode", "auto"],
        )

    def test_kandalf_attach_and_transcript_go_to_agy_for_its_sessions(self):
        began = agy_cli.start([], self.work, "auto", "go")
        self.wait_idle(began["id"])
        self.assertIn("done working", agent.transcript(began["id"]))
        with mock.patch.object(agy_cli, "attach", return_value=0) as mine, \
             mock.patch.object(agent.claude_code, "attach", return_value=0) as claudes:
            agent.open_session(began["id"])
            agent.open_session("ab12cd34")
        mine.assert_called_once_with(began["id"])
        claudes.assert_called_once_with("ab12cd34")

    def test_a_message_interrupts_a_running_agy_session_and_resumes_the_same_conversation(self):
        self.addCleanup(agent.use, "claude")
        home = Path(tempfile.mkdtemp())
        (home / "board.json").write_text(json.dumps({"key": "PLN", "app": str(self.work), "agent": "agy"}))
        store = stores.Store(home)
        self.assertEqual(agent.name(), "agy")
        with mock.patch.dict(os.environ, {"FAKE_SLEEP": "30"}):
            began = agy_cli.start([], self.work, "auto", "go")
        # Wait briefly for the fake agy to emit its init line
        for _ in range(50):
            if agy_cli.known().get(began["id"]):
                break
            time.sleep(0.02)
        old, full = began["id"], agy_cli.known()[began["id"]]
        self.assertEqual(agy_cli.sessions(), {old: "busy"})
        pickup.save(store, {"inflight": ["PLN-01"], "jobs": [{"key": "PLN-01", "title": "One", "state": "started", "id": old,
                                                              "session": full, "worktree": str(self.work), "branch": "pln-01-one", "model": "auto", "started": 0}]})
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(pickup.reply(store, "PLN-01", "Leave the CSS alone.", sessions=pickup.Sessions(stop=agy_cli.stop)))
        self.assertNotEqual(agy_cli.sessions().get(old), "busy")
        job = pickup.load(store)["jobs"][0]
        self.assertEqual(job["state"], "started")
        self.wait_idle(job["id"])
        args = (self.work / "args.txt").read_text()
        self.assertIn(f"--conversation\n{full}", args)
        self.assertIn("Leave the CSS alone.", args)

    def test_hooks_accept_antigravity_toolcall_payload_and_emit_empty_json(self):
        out = io.StringIO()
        payload = json.dumps({"toolCall": {"name": "write_file", "args": {"Path": str(self.work / "nope.txt")}}})
        self.assertEqual(autocommit.main(io.StringIO(payload), out), 0)
        self.assertEqual(json.loads(out.getvalue()), {})
        out2 = io.StringIO()
        self.assertEqual(checkhook.main(io.StringIO(payload), out2), 0)
        self.assertEqual(json.loads(out2.getvalue()), {})

    def test_agent_choice_for_agy(self):
        self.addCleanup(agent.use, "claude")
        agent.use("agy")
        self.assertEqual((agent.label(), agent.cli(), agent.short(), agent.default_model(), agent.update_hint()),
                         ("Antigravity CLI", "agy", "Antigravity", "auto", "`agy update`"))


if __name__ == "__main__":
    unittest.main()
