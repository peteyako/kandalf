#!/usr/bin/env python3
"""The job ledger (jobs.py): board-pickup.json, its one locked transaction, in this process
and across processes, and the queries the server and the CLI make of it.

    python3 -m unittest test_jobs
"""

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from kandalf import jobs
from kandalf import pickup
from kandalf import store as stores
from kandalf import tiles as reader

HERE = Path(__file__).resolve().parent


def job(key, state, **more):
    return {"key": key, "title": key, "state": state, **more}


class Ledger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = stores.Store(self.tmp.name)
        self.ledger = jobs.Ledger(self.store)

    def write(self, state):
        """Put a state file on disk as an older kandalf left it, byte for byte."""
        self.ledger.path.write_text(json.dumps(state), encoding="utf-8")

    def test_a_store_with_no_state_file_has_no_jobs_and_has_not_looked(self):
        self.assertEqual(self.ledger.read(), {"inflight": None, "jobs": []})
        self.assertFalse(self.ledger.path.exists())

    def test_a_change_is_saved_when_the_transaction_ends(self):
        with self.ledger.change() as state:
            state["inflight"] = ["PLN-01"]
            state["jobs"].append(job("PLN-01", "waiting"))
        self.assertEqual(jobs.Ledger(self.store).read(), {"inflight": ["PLN-01"], "jobs": [job("PLN-01", "waiting")]})

    def test_a_transaction_that_raises_saves_nothing(self):
        with self.assertRaises(RuntimeError):
            with self.ledger.change() as state:
                state["jobs"].append(job("PLN-01", "waiting"))
                raise RuntimeError("no")
        self.assertEqual(self.ledger.read()["jobs"], [])

    def test_a_transaction_that_changes_nothing_writes_nothing(self):
        with self.ledger.change():
            pass
        self.assertFalse(self.ledger.path.exists())

    def test_a_save_remembers_the_last_jobs_done_with_and_every_job_a_tile_still_holds(self):
        held = [job("PLN-01", "paused", id="s1"), job("PLN-02", "waiting"), job("PLN-03", "idle", id="s3")]
        ended = [job(f"PLN-{n:02}", "ended") for n in range(10, 10 + jobs.KEEP + 5)]
        with self.ledger.change() as state:
            state["jobs"] = [held[0], *ended[:3], held[1], *ended[3:], held[2]]
        kept = self.ledger.read()["jobs"]
        self.assertEqual([j["key"] for j in kept if j["state"] in jobs.KEPT], ["PLN-01", "PLN-02", "PLN-03"])
        self.assertEqual([j["key"] for j in kept if j["state"] == "ended"], [j["key"] for j in ended[5:]])

    def test_a_change_inside_a_change_is_part_of_it(self):
        with self.ledger.change() as outer:
            outer["jobs"].append(job("PLN-01", "waiting"))
            with jobs.Ledger(self.store).change() as inner:  # another module's ledger on the same store
                self.assertIs(inner, outer)
                inner["jobs"].append(job("PLN-02", "waiting"))
            self.assertFalse(self.ledger.path.exists())  # the outer transaction saves, once
        self.assertEqual([j["key"] for j in self.ledger.read()["jobs"]], ["PLN-01", "PLN-02"])

    def test_a_change_inside_a_hold_of_the_lock_saves_at_its_own_end(self):
        with self.ledger.locked():
            with jobs.Ledger(self.store).change() as state:
                state["jobs"].append(job("PLN-01", "waiting"))
            self.assertEqual(len(self.ledger.read()["jobs"]), 1)

    def test_threads_changing_the_ledger_at_once_lose_no_write(self):
        def add(n):
            for i in range(20):
                with jobs.Ledger(self.store).change() as state:
                    state["jobs"].append(job(f"T{n}-{i}", "waiting"))
        threads = [threading.Thread(target=add, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(self.ledger.read()["jobs"]), 80)

    def test_without_fcntl_the_ledger_still_works_in_one_process(self):
        with mock.patch.object(jobs, "fcntl", None):
            with self.ledger.change() as state:
                state["jobs"].append(job("PLN-01", "waiting"))
        self.assertEqual(len(self.ledger.read()["jobs"]), 1)
        self.assertFalse(self.ledger.path.with_suffix(".lock").exists())

    def test_the_lock_file_sits_beside_the_state_file(self):
        with self.ledger.change() as state:
            state["inflight"] = []
        self.assertEqual(self.ledger.path.with_suffix(".lock").parent, self.ledger.path.parent)
        self.assertTrue(self.ledger.path.with_suffix(".lock").exists())

    def test_the_state_file_is_in_the_stores_git_dir_when_it_is_a_repo(self):
        subprocess.run(["git", "init", "-q", self.tmp.name], check=True)
        st = stores.Store(self.tmp.name)
        self.assertEqual(jobs.Ledger(st).path, st.root / ".git" / jobs.STATE)
        self.assertEqual(jobs.Ledger(self.store).path, self.store.dir / ("." + jobs.STATE))  # a store that isn't a repo

    def test_pickup_names_the_ledgers_states(self):
        self.assertEqual((pickup.TRACKED, pickup.STOPPED, pickup.KEPT), (jobs.TRACKED, jobs.STOPPED, jobs.KEPT))


class Queries(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = stores.Store(self.tmp.name)
        self.ledger = jobs.Ledger(self.store)

    def have(self, *js):
        with self.ledger.change() as state:
            state["jobs"] = list(js)

    def test_latest_is_the_last_job_on_a_tile(self):
        self.have(job("PLN-01", "ended", id="s1", branch="b1", worktree="/w1"), job("PLN-02", "started", id="s2"),
                  job("PLN-01", "waiting"))
        self.assertEqual(self.ledger.latest("PLN-01")["state"], "waiting")
        self.assertIsNone(self.ledger.latest("PLN-09"))

    def test_latest_with_fields_is_the_last_job_on_a_tile_that_has_them(self):
        self.have(job("PLN-01", "ended", id="s1", branch="b1", worktree="/w1"), job("PLN-01", "failed", branch="b2"),
                  job("PLN-01", "waiting"))
        self.assertEqual(self.ledger.latest("PLN-01", "id")["id"], "s1")
        self.assertEqual(self.ledger.latest("PLN-01", "branch")["branch"], "b2")
        self.assertEqual(self.ledger.latest("PLN-01", "branch", "worktree")["branch"], "b1")
        self.assertIsNone(self.ledger.latest("PLN-02", "id"))

    def test_live_is_every_job_a_tile_still_holds(self):
        self.have(*(job(f"PLN-{n:02}", s) for n, s in enumerate(
            ("waiting", "started", "idle", "lost", "paused", "ended", "failed"))))
        self.assertEqual([j["state"] for j in self.ledger.live()], ["waiting", "started", "idle", "lost", "paused"])

    def test_busy_while_a_job_waits_or_has_a_session_on_a_tile_in_flight(self):
        self.assertFalse(self.ledger.busy())
        for s, busy in (("waiting", True), ("started", True), ("idle", True), ("lost", True),
                        ("paused", False), ("ended", False), ("failed", False)):
            self.have(job("PLN-01", "ended"), job("PLN-02", s))
            self.assertEqual(self.ledger.busy(), busy, s)

    def test_with_branch_is_every_job_that_cut_a_branch(self):
        self.have(job("PLN-01", "ended", branch="b1", worktree="/w1"), job("PLN-02", "failed"),
                  job("PLN-03", "started", branch="b3"))
        self.assertEqual([j["key"] for j in self.ledger.with_branch()], ["PLN-01", "PLN-03"])


class Transitions(unittest.TestCase):
    """The named ways a job changes state, each with its rule, and the console line it gives."""

    def state(self, *js):
        return {"inflight": [], "jobs": list(js)}

    def test_queue_puts_a_tile_that_arrived_at_the_back_waiting(self):
        state = self.state(job("PLN-01", "ended"))
        self.assertIsNone(jobs.queue(state, {"key": "PLN-02", "title": "Two", "model": "opus"}, 1000.7))
        self.assertEqual(state["jobs"][-1], {"key": "PLN-02", "title": "Two", "model": "opus", "state": "waiting", "at": 1000})

    def test_one_tile_one_session_queue_refuses_a_key_with_a_live_job(self):
        for live in jobs.KEPT:
            state = self.state(job("PLN-01", live, id="s1"))
            said = jobs.queue(state, {"key": "PLN-01", "title": "One"}, 1000)
            self.assertEqual(said, "PLN-01 is in flight again and keeps the session it has")
            self.assertEqual(state["jobs"], [job("PLN-01", live, id="s1")], live)

    def test_a_tile_whose_jobs_are_done_with_is_queued_again(self):
        state = self.state(job("PLN-01", "ended", id="s1"), job("PLN-01", "failed", error="x"))
        jobs.queue(state, {"key": "PLN-01", "title": "One"}, 1000)
        self.assertEqual([j["state"] for j in state["jobs"]], ["ended", "failed", "waiting"])

    def test_requeue_puts_a_paused_job_at_the_back_to_be_taken_over_from_its_handoff(self):
        paused = job("PLN-01", "paused", id="s1")
        state = self.state(paused, job("PLN-02", "waiting"))
        said = jobs.requeue(state, paused, 2000)
        self.assertEqual(said, "PLN-01 is queued again: a new session takes it over from its handoff in turn")
        self.assertEqual([j["key"] for j in state["jobs"]], ["PLN-02", "PLN-01"])
        self.assertEqual((paused["state"], paused["again"], paused["at"]), ("waiting", "paused", 2000))

    def test_a_paused_job_back_in_flight_takes_its_tile_as_it_stands_now(self):
        paused = job("PLN-01", "paused", id="s1", master="PLN-00", limit=2, base="dev")
        state = self.state(paused)
        said = jobs.requeue(state, paused, 2000, tile={"key": "PLN-01", "title": "One now", "with": [], "text": "t"})
        self.assertEqual(said, "PLN-01 is in flight again: a new session takes it over from its handoff in turn")
        self.assertEqual(paused, {"key": "PLN-01", "title": "One now", "state": "waiting", "id": "s1", "with": [], "text": "t",
                                  "again": "paused", "at": 2000})

    def test_requeue_is_only_for_a_paused_job(self):
        for other in ("waiting", "started", "idle", "lost", "ended", "failed"):
            j = job("PLN-01", other)
            with self.assertRaises(ValueError):
                jobs.requeue(self.state(j), j, 2000)

    def test_a_waiting_job_whose_tile_left_is_dropped_or_paused_again(self):
        fresh, back = job("PLN-01", "waiting"), job("PLN-02", "waiting", again="paused")
        state = self.state(fresh, back)
        self.assertEqual(jobs.unqueue(state, fresh), "PLN-01 left In flight before its turn: dropped from the queue")
        self.assertEqual(jobs.unqueue(state, back), "PLN-02 left In flight before its turn: paused again")
        self.assertEqual(state["jobs"], [job("PLN-02", "paused")])
        with self.assertRaises(ValueError):
            jobs.unqueue(state, state["jobs"][0])

    def test_begin_sets_what_the_launch_gave_and_stamps_when(self):
        waiting = job("PLN-01", "waiting", behind="PLN-00")
        said = jobs.begin(waiting, {"state": "started", "id": "s1", "session": "s1-full", "branch": "b", "worktree": "/w"}, 3000.9)
        self.assertEqual(said, 'PLN-01 "PLN-01" picked up: session s1 (kandalf attach s1), branch b in /w')
        self.assertEqual((waiting["state"], waiting["id"], waiting["started"]), ("started", "s1", 3000))
        self.assertNotIn("behind", waiting)
        failed = job("PLN-02", "waiting")
        self.assertEqual(jobs.begin(failed, {"state": "failed", "error": "no claude"}, 3000), 'PLN-02 "PLN-02" could not be started: no claude')
        idle = job("PLN-03", "idle", id="s3")
        self.assertEqual(jobs.begin(idle, {"state": "started", "id": "s3"}, 3000, "resumed"), 'PLN-03 "PLN-03" resumed: session s3 (kandalf attach s3)')

    def test_an_ended_job_is_never_begun_again(self):
        with self.assertRaises(ValueError):
            jobs.begin(job("PLN-01", "ended", id="s1"), {"state": "started", "id": "s2"}, 3000)

    def test_saw_leaves_a_session_just_started_alone(self):
        j = job("PLN-01", "started", id="s1", started=1000)
        self.assertIsNone(jobs.saw(j, None, True, 1000 + jobs.SETTLE - 1, lambda sid: None))
        self.assertEqual(j["state"], "started")

    def test_saw_notes_a_session_at_work_idle_waiting_or_gone(self):
        j = job("PLN-01", "started", id="s1", started=0)
        self.assertEqual(jobs.saw(j, "idle", True, 1000, lambda sid: None), "PLN-01 session s1 is idle: it finished its turn")
        self.assertEqual(jobs.saw(j, "busy", True, 1000, lambda sid: None), "PLN-01 session s1 is at work again")
        self.assertEqual(jobs.saw(j, "idle", True, 1000, lambda sid: "question"),
                         "PLN-01 session s1 is waiting for your input (it asked a question)")
        self.assertEqual((j["state"], j["waiting"]), ("idle", "question"))
        self.assertIsNone(jobs.saw(j, "idle", True, 1000, lambda sid: "question"))  # said once
        self.assertEqual(jobs.saw(j, None, True, 1000, lambda sid: None),
                         "PLN-01 session s1 is gone (stopped, closed or removed): Continue is on its tile")
        self.assertNotIn("waiting", j)

    def test_a_session_blocked_on_input_waits_on_a_question_whatever_its_screen_shows(self):
        j = job("PLN-01", "started", id="s1", started=0)
        jobs.saw(j, "waiting", True, 1000, lambda sid: None)
        self.assertEqual((j["state"], j["waiting"]), ("idle", "question"))

    def test_a_session_that_stopped_in_flight_is_past_its_merge(self):
        j = job("PLN-01", "started", id="s1", started=0, closing=True)
        jobs.saw(j, "idle", True, 1000, lambda sid: None)
        self.assertNotIn("closing", j)

    def test_a_closing_job_s_tile_may_leave_in_flight_without_a_pause(self):
        closing = job("PLN-01", "started", id="s1", started=0, closing=True)
        self.assertFalse(jobs.pauses_on_leaving(closing, "busy", False, 1000))
        self.assertEqual(jobs.saw(closing, None, False, 1000, lambda sid: None), "PLN-01 session s1 is finished with: its tile left In flight")
        self.assertEqual(closing["state"], "ended")

    def test_a_session_at_work_whose_tile_left_in_flight_is_paused(self):
        at_work = job("PLN-01", "started", id="s1", started=0)
        self.assertTrue(jobs.pauses_on_leaving(at_work, "busy", False, 1000))
        self.assertTrue(jobs.pauses_on_leaving(at_work, None, False, jobs.SETTLE - 1))  # just started: not listed yet
        self.assertFalse(jobs.pauses_on_leaving(at_work, None, False, 1000))  # gone already
        self.assertFalse(jobs.pauses_on_leaving(at_work, "busy", True, 1000))  # still in flight
        self.assertFalse(jobs.pauses_on_leaving(job("PLN-01", "idle", id="s1", started=0), "busy", False, 1000))
        self.assertFalse(jobs.pauses_on_leaving({**at_work, "pausing": True}, "busy", False, 1000))

    def test_pausing_is_only_from_a_session_at_work(self):
        at_work = job("PLN-01", "started", id="s1")
        self.assertIsNone(jobs.pausing(at_work))
        self.assertTrue(at_work["pausing"])
        for other in ("waiting", "idle", "lost", "paused", "ended", "failed"):
            with self.assertRaises(ValueError):
                jobs.pausing(job("PLN-01", other, id="s1"))

    def test_a_pausing_session_that_stops_is_paused(self):
        j = job("PLN-01", "started", id="s1", started=0, pausing=True)
        self.assertIsNone(jobs.saw(j, "busy", True, 1000, lambda sid: None))  # still writing its handoff
        self.assertEqual((j["state"], j["pausing"]), ("started", True))
        said = jobs.saw(j, "idle", False, 1000, lambda sid: "question")
        self.assertTrue(said.startswith("PLN-01 session s1 is paused: Continue on its tile"), said)
        self.assertEqual(j, job("PLN-01", "paused", id="s1", started=0))

    def test_paused_is_from_a_session_at_work_or_one_writing_its_handoff(self):
        at_work, writing = job("PLN-01", "started", id="s1", waiting="question"), job("PLN-02", "lost", id="s2", pausing=True)
        self.assertEqual(jobs.paused(at_work, ": stopped before it finished its handoff"),
                         "PLN-01 session s1 is paused: stopped before it finished its handoff")
        jobs.paused(writing, "")
        self.assertEqual((at_work, writing), (job("PLN-01", "paused", id="s1"), job("PLN-02", "paused", id="s2")))
        for other in ("waiting", "idle", "lost", "ended", "failed"):
            with self.assertRaises(ValueError):
                jobs.paused(job("PLN-01", other, id="s1"), "")

    def test_lose_is_for_a_session_that_was_started(self):
        for tracked in jobs.TRACKED:
            j = job("PLN-01", tracked, id="s1", waiting="question")
            self.assertIsNone(jobs.lose(j))
            self.assertEqual(j, job("PLN-01", "lost", id="s1"))
        for other in ("waiting", "paused", "ended", "failed"):
            with self.assertRaises(ValueError):
                jobs.lose(job("PLN-01", other))

    def test_end_is_open_to_a_job_in_any_state(self):
        for any_state in (*jobs.KEPT, "ended", "failed"):
            j = job("PLN-01", any_state)
            self.assertIsNone(jobs.end(j))
            self.assertEqual(j["state"], "ended")

    def test_close_marks_a_session_told_to_merge_and_only_one_at_work(self):
        j = job("PLN-01", "started", id="s1")
        jobs.close(j)
        self.assertTrue(j["closing"])
        jobs.close(j, False)
        self.assertNotIn("closing", j)
        with self.assertRaises(ValueError):
            jobs.close(job("PLN-01", "idle", id="s1"))

    def test_mark_ready_follows_the_tile_s_ready_line_and_says_so_once(self):
        j = job("PLN-01", "started", id="s1")
        self.assertEqual(jobs.mark_ready(j, "tests pass"), "PLN-01 is ready for review: tests pass")
        self.assertIsNone(jobs.mark_ready(j, "tests pass"))
        self.assertTrue(j["ready"])
        jobs.mark_ready(j, None)
        self.assertNotIn("ready", j)
        self.assertEqual(jobs.mark_ready(j, ""), "PLN-01 is ready for review")

    def test_a_kept_job_is_never_pruned(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ledger = jobs.Ledger(stores.Store(tmp.name))
        with ledger.change() as state:
            jobs.queue(state, {"key": "PLN-01", "title": "Old"}, 1)
            state["jobs"].append(job("PLN-02", "started", id="s2"))
            jobs.pausing(state["jobs"][-1])
            jobs.paused(state["jobs"][-1], "")
            for n in range(jobs.KEEP + 10):
                state["jobs"].append(job(f"PLN-{n + 10:02}", "started", id=f"s{n}"))
                jobs.end(state["jobs"][-1])
        kept = ledger.read()["jobs"]
        self.assertEqual([(j["key"], j["state"]) for j in kept[:2]], [("PLN-01", "waiting"), ("PLN-02", "paused")])
        self.assertEqual(len(kept), 2 + jobs.KEEP)


def view_cases():
    """The cases in view_cases.json, which the page's fallback (test_page.js) is held to as well."""
    return json.loads((HERE / "view_cases.json").read_text(encoding="utf-8"))["views"]


class View(unittest.TestCase):
    """What the page shows of a job, and that it never offers what the server refuses."""

    def test_each_state_has_its_label_and_actions(self):
        for c in view_cases():
            self.assertEqual(jobs.view(job("PLN-01", **c["job"])), {"label": c["label"], "actions": c["actions"]}, c["job"])

    def allowed(self, state, flags):
        """The actions the server's guards accept for a job like this, each tried on a store of its own."""
        from test_pickup import Fake
        found = set()
        tries = {
            "reply": lambda st, f: pickup.reply(st, "PLN-01", "hello", sessions=f) is None,
            "pause": lambda st, f: pickup.pause(st, "PLN-01", sessions=f),
            "continue": lambda st, f: pickup.restart(st, "PLN-01", sessions=f, nudge=True),
            "restart": lambda st, f: pickup.restart(st, "PLN-01", sessions=f),
            "queue": lambda st, f: pickup.requeue(st, "PLN-01", sessions=f),
            "startnow": lambda st, f: pickup.start_now(st, "PLN-01", sessions=f),
        }
        for act, run in tries.items():
            with tempfile.TemporaryDirectory() as tmp:
                st = stores.Store(tmp)
                j = job("PLN-01", state, id="s1", session="s1-full", started=0, **{k: v for k, v in flags.items() if k != "empty"})
                jobs.save(st, {"inflight": ["PLN-01"], "jobs": [j]})
                fake = Fake()
                fake.live.add("s1")
                with contextlib.redirect_stdout(io.StringIO()):
                    if run(st, fake):
                        found.add(act)
        return found

    def test_the_page_offers_only_what_the_server_allows(self):
        for c in view_cases():
            state, flags, actions = c["job"]["state"], {k: v for k, v in c["job"].items() if k != "state"}, c["actions"]
            allowed = self.allowed(state, flags)
            for act in actions:
                if act in c.get("server_refuses", []):  # known: the case says the server refuses it
                    self.assertNotIn(act, allowed, c["job"])
                elif act != "close":  # a reply that asks for Close: reply's guard, allowed with it
                    self.assertIn(act, allowed, (state, flags, act))
            if "close" in actions:
                self.assertIn("reply", allowed, (state, flags))

    def test_what_the_server_allows_and_the_page_does_not_offer_is_known(self):
        """Continue on a session at work or a failed one, and Restart on an idle or gone one, are
        allowed by the server and not shown: Pause, Retry and Continue are their buttons."""
        extra = {}
        for c in view_cases():
            state, flags = c["job"]["state"], {k: v for k, v in c["job"].items() if k != "state"}
            more = self.allowed(state, flags) - set(c["actions"])
            if more:
                extra[state + ("+pausing" if flags.get("pausing") else "")] = sorted(more)
        self.assertEqual(extra, {"started": ["continue"], "failed": ["continue"], "idle": ["restart"], "lost": ["restart"]})

    def test_the_summary_carries_the_view_of_each_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = stores.Store(tmp)
            jobs.save(st, {"inflight": [], "jobs": [job("PLN-01", "paused")]})
            self.assertEqual(pickup.summary(st)["jobs"][0]["view"], {"label": "paused", "actions": ["continue", "queue"]})


class OldFiles(unittest.TestCase):
    """State files that older kandalfs wrote load unchanged, and a look runs on them."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = stores.Store(self.tmp.name)
        self.ledger = jobs.Ledger(self.store)
        quiet = contextlib.redirect_stdout(io.StringIO())  # pickup logs to the console
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def write(self, state):
        self.ledger.path.write_text(json.dumps(state), encoding="utf-8")

    def tick(self, board, sessions):
        files = {"build.md": board}
        nothing = pickup.Sessions(start=lambda store, job, again=False: {"state": "failed"},
                                  sessions=lambda: sessions, why=lambda sid: None, now=lambda: 10_000,
                                  resume=lambda store, job, text=None: {"state": "failed"}, stop=lambda sid: None,
                                  switch=lambda store, job, model: {"state": "failed"}, reroute=lambda store, job, names: {"state": "failed"})
        return pickup.tick(self.store, reader.Snapshot(self.store, files).in_flight(), sessions=nothing)

    def test_a_job_with_no_model_loads_and_ticks(self):
        old = {"inflight": ["PLN-01"], "jobs": [job("PLN-01", "started", id="ab12cd34", at=1, started=1)]}
        self.write(old)
        self.assertEqual(self.ledger.read(), old)
        state = self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n", {"ab12cd34": "idle"})
        self.assertEqual(state["jobs"][0]["state"], "idle")

    def test_a_job_with_a_bare_worktree_and_no_repos_loads_and_ticks(self):
        old = {"inflight": ["PLN-01"], "jobs": [job("PLN-01", "started", id="ab12cd34", model="opus", at=1, started=1,
                                                    branch="feat/pln-01", worktree="/somewhere/pln-01")]}
        self.write(old)
        self.assertEqual(self.ledger.read(), old)
        state = self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n", {"ab12cd34": "busy"})
        self.assertEqual((state["jobs"][0]["state"], state["jobs"][0]["worktree"]), ("started", "/somewhere/pln-01"))
        self.assertEqual(jobs.Ledger(self.store).latest("PLN-01", "branch", "worktree")["worktree"], "/somewhere/pln-01")

    def test_a_session_closed_early_as_ended_loads_and_ticks(self):
        old = {"inflight": ["PLN-01"], "jobs": [job("PLN-01", "ended", id="ab12cd34", at=1, started=1)]}
        self.write(old)
        self.assertEqual(self.ledger.read(), old)
        state = self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n", {"ab12cd34": "idle"})
        self.assertEqual(state["jobs"][0]["state"], "idle")  # the first pickup's stopped session, noted as it is now

    def test_a_file_with_no_inflight_key_loads_as_not_yet_looked_and_ticks(self):
        self.write({"jobs": [job("PLN-01", "ended", id="s1")]})
        self.assertEqual(self.ledger.read(), {"inflight": None, "jobs": [job("PLN-01", "ended", id="s1")]})
        state = self.tick("## In flight\n\n- [ ] PLN-02 **Two.**\n", {})
        self.assertEqual(state["inflight"], ["PLN-02"])  # the first look: what is in flight already is someone's


CHILD = """
import sys, time
sys.path.insert(0, sys.argv[1])
from kandalf import jobs, store
with jobs.Ledger(store.Store(sys.argv[2])).change() as state:
    print("holding", flush=True)
    time.sleep(0.5)
    state["jobs"].append({"key": "CHILD", "title": "c", "state": "waiting"})
"""


class AcrossProcesses(unittest.TestCase):
    """`kandalf agent` while the server looks: one process's change waits for the other's."""

    @unittest.skipIf(jobs.fcntl is None, "no fcntl: the ledger is locked in one process only")
    def test_a_change_in_another_process_waits_and_neither_write_is_lost(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        st = stores.Store(tmp.name)
        child = subprocess.Popen([sys.executable, "-c", CHILD, str(HERE), tmp.name], stdout=subprocess.PIPE, text=True)
        self.addCleanup(child.wait)
        self.assertEqual(child.stdout.readline().strip(), "holding")  # it has loaded the state and holds the lock
        began = time.monotonic()
        with jobs.Ledger(st).change() as state:
            waited = time.monotonic() - began
            state["jobs"].append({"key": "PARENT", "title": "p", "state": "waiting"})
        child.stdout.close()
        self.assertEqual(child.wait(timeout=10), 0)
        self.assertGreater(waited, 0.2)
        self.assertEqual([j["key"] for j in jobs.Ledger(st).read()["jobs"]], ["CHILD", "PARENT"])


if __name__ == "__main__":
    unittest.main()


class GoneStore(unittest.TestCase):
    """A committer outlives its store when the store is deleted (a throwaway one, a test's):
    it then writes nothing, least of all into the folder it was started from."""

    def test_a_committer_whose_store_is_gone_stops_and_writes_nothing_where_it_runs(self):
        from kandalf import gitlog
        top = Path(tempfile.mkdtemp()).resolve()
        here = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(top), str(here)]))
        subprocess.run(["git", "-C", str(top), "init", "-q"], check=True)
        (top / "board.json").write_text("{}")
        st = stores.Store(top)
        self.assertIsNotNone(st.root)
        subprocess.run(["rm", "-rf", str(top)], check=True)
        with self.assertRaises(OSError):
            gitlog.git_dir(top)
        with contextlib.chdir(here) if hasattr(contextlib, "chdir") else _chdir(here):
            gitlog.wait(st)
        self.assertEqual(list(here.iterdir()), [])


@contextlib.contextmanager
def _chdir(path):
    import os
    was = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(was)
