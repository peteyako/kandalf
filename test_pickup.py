#!/usr/bin/env python3
"""Pickup (pickup.py): a tile that lands in In flight is handed to a background Claude
session, one at a time. No session is started here: start and the list of live sessions
are passed in.

    python3 -m unittest test_pickup
"""

import contextlib
import io
import json
import os
import re
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from kandalf import claude_code as agent
from kandalf import agent as agents
from kandalf import copilot_cli
from kandalf import gitlog
from kandalf import pickup
from kandalf import serve
from kandalf import store as stores

PLANS = """# Plans

## To do

- [ ] PLN-90 **Waiting.**

## In flight

- [ ] PLN-70 **Dark mode.** The master.
  - [ ] **Agree the scope.**
- [ ] PLN-75 **Theme switch: the `System` option!**
  Part of PLN-70.
- [ ] PLN-80 **On its own.**
- [ ] PLN-81 **Hands off.** No pickup: a session already has this.
- [ ] Unkeyed.

## Done

- [x] PLN-74 **Colour tokens.** Part of PLN-70.
"""


def git(path, *args):
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


class Fake:
    """Stands in for the claude CLI: records what was started, and says which are live."""

    def __init__(self):
        self.started, self.resumed, self.switched, self.live, self.idle, self.n = [], [], [], set(), set(), 0
        self.stopped, self.told = [], []  # sessions stopped, and what each resumed one was told
        self.asking = {}  # {id: "interrupted" or "question"} for an idle session that waits for input
        self.blocked = set()  # sessions the CLI lists as waiting for input mid-turn

    def why(self, sid):
        return self.asking.get(sid)

    def start(self, store, job, again=False):
        self.n += 1
        sid = f"s{self.n}"
        self.started.append(job["key"] + ("*" if again else ""))
        self.live.add(sid)
        return {"state": "started", "id": sid, "session": sid + "-full"}

    def resume(self, store, job, text=None):
        self.resumed.append(job["session"])
        self.told.append(text)
        self.live.add(job["id"])
        self.idle.discard(job["id"])
        return {"state": "started", "id": job["id"], "session": job["session"]}

    def switch(self, store, job, model):
        self.switched.append((job["key"], job["state"], model))
        self.idle.discard(job["id"])
        return {"state": "started", "id": job["id"], "session": job["session"]}

    def sessions(self):
        """{id: "busy" or "idle"} for the sessions that are still there."""
        return {sid: "waiting" if sid in self.blocked else "idle" if sid in self.idle else "busy" for sid in self.live}


# What a session is told today, for a store with no pickup.md: the prompt for a plain job and a
# sub-story job, new and taking over. Pinned byte for byte, so the house rules moving to
# templates/pickup.md changes nothing for a store that has none.
PROMPT_OPENINGS = {
    ("plain", False): """You are picking up PLN-80 "On its own" from the plans plans board: it was just moved to In flight, which is the signal to start developing it.

From /x/plans/plans.md, this tile:

- [ ] PLN-80 **On its own.**

You are in a fresh git worktree of the app at /x/app-wt, on branch b-name, cut from main. The plans store is linked in at docs/plans.

Before anything else, check nobody else has this: if the tile already notes a different branch or worktree, or another session is plainly working on it, stop and say so.""",
    ("plain", True): """You are taking over PLN-80 "On its own" on the plans plans board. An earlier Claude session was developing it and has closed; it can't be resumed, so you carry on from what it left.

When it was picked up, this tile read as below. The board has moved on since: read the tile as it is now in /x/plans/plans.md, above all its "Handoff:" line and which sub-tasks are ticked.

- [ ] PLN-80 **On its own.**

You are in that session's git worktree of the app at /x/app-wt, on branch b-name (cut from main). The plans store is linked in at docs/plans.

First find where it stopped: `git log main..HEAD`, `git status` and the tile. Uncommitted changes in the worktree are its unfinished work: read them before you keep or drop them. Then carry on from there; don't redo what is done.""",
    ("sub", False): """You are picking up PLN-75 "Theme switch: the System option!" from the plans plans board: it was just moved to In flight, which is the signal to start developing it.

From /x/plans/plans.md, this tile, a sub-story of PLN-70, whose tile follows it for you to read. Do only PLN-75: the other sub-stories of PLN-70 have sessions of their own, some at work beside you now:

- [ ] PLN-75 **Theme switch: the `System` option!**
  Part of PLN-70.
- [ ] PLN-70 **Dark mode.** The master.
  - [ ] **Agree the scope.**

You are in a fresh git worktree of the app at /x/app-wt, on branch b-name, cut from main. The plans store is linked in at docs/plans.

Before anything else, check nobody else has this: if the tile already notes a different branch or worktree, or another session is plainly working on it, stop and say so.""",
    ("sub", True): """You are taking over PLN-75 "Theme switch: the System option!" on the plans plans board. An earlier Claude session was developing it and has closed; it can't be resumed, so you carry on from what it left.

When it was picked up, this tile, a sub-story of PLN-70, whose tile follows it for you to read. Do only PLN-75: the other sub-stories of PLN-70 have sessions of their own, some at work beside you now read as below. The board has moved on since: read the tile as it is now in /x/plans/plans.md, above all its "Handoff:" line and which sub-tasks are ticked.

- [ ] PLN-75 **Theme switch: the `System` option!**
  Part of PLN-70.
- [ ] PLN-70 **Dark mode.** The master.
  - [ ] **Agree the scope.**

You are in that session's git worktree of the app at /x/app-wt, on branch b-name (cut from main). The plans store is linked in at docs/plans.

First find where it stopped: `git log main..HEAD`, `git status` and the tile. Uncommitted changes in the worktree are its unfinished work: read them before you keep or drop them. Then carry on from there; don't redo what is done.""",
}
PROMPT_TAIL = """

Follow this repo's CLAUDE.md and the filing rules in the header of plans.md. Read the plan doc the tile links, if it links one.

Keep the tile able to hand the work over. Your session can be closed at any moment, and a new one will have only the tile and this branch to go on:
- Commit as you go, so no finished work lives only in this session.
- Tick each sub-task with its commit as it lands. Commit subjects carry the key, e.g. "... ({key})".
- Ticked sub-tasks must not read as a finished story while its gates still run. If the tile has no sub-task for them, add a last one when you start, `- [ ] **Gates**: lint, every test tier and the review`, and tick it only when all of them have passed.
- Keep one indented "Handoff:" line on the tile, rewritten after each step: what is done, what you are doing now, what comes next, and anything not yet committed or decided. The board has already noted this session, the branch and the worktree on the tile ("Claude session ..."); leave that line as it is.

When every sub-task is ticked and every gate this repo asks for has passed, mark the story ready: add one indented line to the tile, starting `**Ready for review:**`, with the gate results, how many commits are on the branch, and what the reviewer should look at first. That line is what makes the board outline the tile for review; take it off again if you go back to work on the story. Then stop. Don't merge, don't squash, don't push, and don't move the tile to Done: those are the reviewer's to do."""


class Reading(unittest.TestCase):
    def setUp(self):
        self.store = stores.Store(tempfile.mkdtemp())

    def test_the_in_flight_tiles_are_the_keyed_ones_in_that_column(self):
        tiles = serve.in_flight_tiles(self.store, PLANS)
        self.assertEqual([(t["key"], t["title"], t["parent"]) for t in tiles],
                         [("PLN-70", "Dark mode", None), ("PLN-75", "Theme switch: the System option!", "PLN-70"),
                          ("PLN-80", "On its own", None), ("PLN-81", "Hands off", None)])
        self.assertIn("**Agree the scope.**", tiles[0]["text"])

    def test_a_master_that_arrives_with_sub_stories_gives_each_of_them_a_job_and_takes_none(self):
        jobs = pickup.entered(["PLN-80"], serve.in_flight_tiles(self.store, PLANS))
        self.assertEqual([(j["key"], j["master"], j["limit"]) for j in jobs], [("PLN-75", "PLN-70", 2)])
        self.assertIn("PLN-75 **Theme", jobs[0]["text"])
        self.assertIn("**Agree the scope.**", jobs[0]["text"])  # the master's tile, to read

    def test_a_sub_story_that_joins_its_master_in_flight_is_one_of_its_family(self):
        jobs = pickup.entered(["PLN-70", "PLN-80"], serve.in_flight_tiles(self.store, PLANS))
        self.assertEqual([(j["key"], j["master"], j["limit"]) for j in jobs], [("PLN-75", "PLN-70", 2)])

    def test_the_master_s_sessions_line_or_the_store_sets_how_many_work_at_once(self):
        family = "## In flight\n\n- [ ] PLN-01 **Master.**\n  Sessions: 3\n" + "".join(f"- [ ] PLN-0{n} **Kid.**\n  Part of PLN-01.\n" for n in (2, 3))
        tiles = serve.in_flight_tiles(self.store, family)
        self.assertEqual([(j["key"], j["limit"]) for j in pickup.entered([], tiles)], [("PLN-02", 3), ("PLN-03", 3)])
        plain = serve.in_flight_tiles(self.store, family.replace("  Sessions: 3\n", ""))
        self.assertEqual([j["limit"] for j in pickup.entered([], plain, 4)], [4, 4])
        alone = pickup.entered([], serve.in_flight_tiles(self.store, "## In flight\n\n- [ ] PLN-01 **Master.**\n"))
        self.assertEqual([(j["key"], j.get("limit")) for j in alone], [("PLN-01", None)])  # no sub-stories: its own job

    def test_no_pickup_on_a_master_covers_its_sub_stories(self):
        family = "## In flight\n\n- [ ] PLN-01 **Master.** No pickup.\n- [ ] PLN-02 **Kid.**\n  Part of PLN-01.\n"
        self.assertEqual(pickup.entered([], serve.in_flight_tiles(self.store, family)), [])

    def test_a_tile_that_says_no_pickup_is_left_alone(self):
        self.assertEqual(pickup.entered(["PLN-70", "PLN-75", "PLN-80"], serve.in_flight_tiles(self.store, PLANS)), [])

    def test_the_branch_and_worktree_are_named_after_the_tile(self):
        self.store.app = Path("/x/myapp")
        job = {"key": "PLN-75", "title": "Theme switch: the System option!"}
        self.assertEqual(pickup.branch(self.store, job), "pln-75-theme-switch-the-system-option")
        self.assertEqual(pickup.worktree(self.store, job), Path("/x/myapp-pln-75"))

    def test_a_story_runs_on_opus_unless_the_store_or_the_tile_says_otherwise(self):
        self.assertEqual(pickup.model_for(self.store, "- [ ] PLN-01 **One.**\n  The data model: users and teams."), "opus")
        self.assertEqual(pickup.model_for(self.store, "- [ ] PLN-01 **One.**\n  Words.\n  Model: sonnet\n  - [ ] **Step.**"), "sonnet")
        self.assertEqual(pickup.model_for(self.store, "- [ ] PLN-01 **One.**\n  model: `claude-haiku-5-5`."), "claude-haiku-5-5")
        folder = Path(tempfile.mkdtemp())
        (folder / "board.json").write_text(json.dumps({"model": "sonnet"}))
        cheaper = stores.Store(folder)
        self.assertEqual(pickup.model_for(cheaper, "- [ ] PLN-01 **One.**"), "sonnet")
        self.assertEqual(pickup.model_for(cheaper, "- [ ] PLN-01 **One.**\n  Model: opus"), "opus")

    def test_a_job_carries_its_model_to_the_session(self):
        tiles = serve.in_flight_tiles(self.store, "## In flight\n\n- [ ] PLN-01 **One.**\n  Model: sonnet\n- [ ] PLN-02 **Two.**\n")
        fake = Fake()
        with contextlib.redirect_stdout(io.StringIO()):
            pickup.tick(self.store, [], start=fake.start, sessions=fake.sessions)
            pickup.tick(self.store, tiles, start=fake.start, sessions=fake.sessions)
        self.assertEqual([(j["key"], j["model"]) for j in pickup.load(self.store)["jobs"]], [("PLN-01", "sonnet"), ("PLN-02", "opus")])
        self.assertEqual(pickup.session_args({"key": "PLN-01", "title": "One", "model": "sonnet"}),
                         ["--model", "sonnet", "-n", "PLN-01 One"])
        self.assertEqual(pickup.session_args({"key": "PLN-01", "title": "One"}), ["-n", "PLN-01 One"])  # a job from before models

    def test_the_prompt_carries_the_tile_and_the_rules(self):
        self.store.app = Path("/x/myapp")
        job = pickup.entered([], serve.in_flight_tiles(self.store, PLANS))[0]
        text = pickup.prompt(self.store, job, "pln-70-dark-mode", Path("/x/myapp-pln-70"))
        self.assertEqual(job["key"], "PLN-75")
        for part in ("PLN-70", "PLN-75", "**Agree the scope.**", "pln-70-dark-mode", "Don't merge", "**Ready for review:**", "Do only PLN-75"):
            self.assertIn(part, text)

    def test_a_store_without_pickup_md_tells_its_sessions_exactly_what_it_did_before(self):
        st = stores.Store("/x/plans")  # no pickup.md there
        plain = {"key": "PLN-80", "title": "On its own", "with": [], "text": "- [ ] PLN-80 **On its own.**"}
        sub = pickup.entered([], serve.in_flight_tiles(st, PLANS))[0]
        for name, job in (("plain", plain), ("sub", sub)):
            for again in (False, True):
                want = PROMPT_OPENINGS[(name, again)] + PROMPT_TAIL.replace("{key}", job["key"])
                self.assertEqual(pickup.prompt(st, job, "b-name", Path("/x/app-wt"), again), want, (name, again))


    def test_a_store_that_names_its_pickup_column_has_only_that_column_s_tiles_picked_up(self):
        board = "## In flight\n\n- [ ] PLN-01 **One.**\n\n## building \n\n- [ ] PLN-02 **Two.**\n\n## Doing\n\n- [ ] PLN-03 **Three.**\n"
        self.assertEqual([t["key"] for t in serve.in_flight_tiles(self.store, board)], ["PLN-01", "PLN-03"])  # unset: any In flight column
        (self.store.dir / "board.json").write_text(json.dumps({"pickupColumn": " Building"}))
        named = stores.Store(self.store.dir)
        self.assertEqual([t["key"] for t in serve.in_flight_tiles(named, board)], ["PLN-02"])
        self.assertFalse(pickup.is_pickup_column(named, "In flight"))

    def test_an_in_flight_column_under_another_name_is_still_picked_up(self):
        for name in ("In flight", "in-flight", "Inflight", "In progress", "In Progrees", "In-Progress",
                     "Progress", "Doing", "WIP", "Working", "Underway", "Under way", "Ongoing", "Active", "🚀 In progress"):
            self.assertTrue(pickup.is_pickup_column(self.store, name), name)
        for name in ("To do", "Done", "Bugs", "Backlog", "Building", "Not doing", "Inactive", "Ready for review"):
            self.assertFalse(pickup.is_pickup_column(self.store, name), name)

    def test_a_done_column_under_another_name_counts_as_done(self):
        for name in ("Done", "Shipped", "Completed", "Finished", "Merged", "Released", "Closed"):
            self.assertTrue(serve.DONE_COL_RE.search(name), name)
        for name in ("Not done", "Undone", "Shipping", "In flight", "To do"):
            self.assertFalse(serve.DONE_COL_RE.search(name), name)

    def test_the_store_s_pickup_md_takes_the_place_of_the_default_rules(self):
        st = stores.Store(tempfile.mkdtemp())
        job = {"key": "PLN-80", "title": "On its own", "with": [], "text": "- [ ] PLN-80 **On its own.**"}
        default = pickup.prompt(st, job, "b", Path("/x/wt"))
        self.assertIn('Commit subjects carry the key, e.g. "... (PLN-80)".', default)
        (st.dir / "pickup.md").write_text("Run `make lint` first; say {key} in each subject, not {KEY} or {} or {0}.\n- Never touch {vendor}/.\n")
        own = pickup.prompt(st, job, "b", Path("/x/wt"))
        self.assertIn("- Tick each sub-task with its commit as it lands. Run `make lint` first; say PLN-80 in each subject, "
                      "not {KEY} or {} or {0}.\n- Never touch {vendor}/.\n- Keep one indented \"Handoff:\" line", own)
        self.assertNotIn("Commit subjects carry the key", own)
        self.assertNotIn("**Gates**", own)
        for kept in ("Claude session ...", "**Ready for review:**", "Tick each sub-task with its commit"):  # what the board reads back
            self.assertIn(kept, own)
        (st.dir / "pickup.md").write_text("- A rule of its own.\n")
        self.assertIn("as it lands.\n- A rule of its own.\n- Keep one", pickup.prompt(st, job, "b", Path("/x/wt")))
        (st.dir / "pickup.md").write_text("\n")  # empty: no house rules
        self.assertIn("as it lands.\n- Keep one indented", pickup.prompt(st, job, "b", Path("/x/wt")))

    def test_the_branch_and_worktree_follow_the_store_s_patterns(self):
        folder = Path(tempfile.mkdtemp())
        (folder / "board.json").write_text(json.dumps({"branch": "feat/{KEY}/{slug}-{x}", "worktree": "../wt/{app}-{key}"}))
        st = stores.Store(folder)
        st.app = Path("/x/y/myapp")
        job = {"key": "PLN-75", "title": "Theme switch"}
        self.assertEqual(pickup.branch(st, job), "feat/PLN-75/theme-switch-{x}")  # an unknown placeholder stays as written
        self.assertEqual(pickup.worktree(st, job), Path("/x/wt/myapp-pln-75"))
        self.assertEqual(pickup.worktree_label(st, pickup.worktree(st, job)), "../wt/myapp-pln-75")
        self.assertEqual(pickup.worktree_label(self.store, "/x/app-pln-70"), "app-pln-70")  # no app: the folder's name
        self.store.app = Path("/x/y/myapp")  # the default: beside the app, named by its folder alone
        self.assertEqual(pickup.worktree_label(self.store, pickup.worktree(self.store, job)), "myapp-pln-75")

    def test_a_worktree_pattern_that_leaves_the_app_s_surroundings_or_has_no_key_is_refused(self):
        folder = Path(tempfile.mkdtemp())
        (folder / "app").mkdir()
        refused = []
        for cfg in ({"worktree": "/tmp/{key}"}, {"worktree": "../../{key}"}, {"worktree": "{key}/.."}, {"worktree": "~/{key}"},
                    {"worktree": "wt/{app}"}, {"branch": "work"}):
            (folder / "board.json").write_text(json.dumps({"pickup": True, "app": "app", **cfg}))
            st = stores.Store(folder)
            refused.append(pickup.why_off(st, installed=lambda: True, lacks=lambda: []))
        self.assertTrue(all(why and why.startswith("Pickup is off") for why in refused), refused)
        self.assertIn('"worktree" in board.json (/tmp/{key}) must be a path relative', refused[0])
        self.assertIn('"branch" in board.json (work) has no {key}', refused[-1])
        for fine in ("wt/{app}-{key}", "../wt/{KEY}", "{app}-{key}"):
            (folder / "board.json").write_text(json.dumps({"pickup": True, "app": "app", "worktree": fine}))
            self.assertIsNone(pickup.why_off(stores.Store(folder), installed=lambda: True, lacks=lambda: []), fine)


class Queue(unittest.TestCase):
    def setUp(self):
        self.store = stores.Store(tempfile.mkdtemp())
        self.fake = Fake()
        self.clock = 1000
        self.noted = []  # what the server was told to write into a tile: (key, model)
        quiet = contextlib.redirect_stdout(io.StringIO())  # pickup logs to the console
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def tick(self, text, later=600):
        self.clock += later  # by default, long enough that a session just started has shown itself busy
        return pickup.tick(self.store, serve.in_flight_tiles(self.store, text), start=self.fake.start,
                           sessions=self.fake.sessions, switch=self.fake.switch, why=self.fake.why, now=lambda: self.clock,
                           resume=self.fake.resume, stop=self.stop,
                           on_start=lambda store, job: self.noted.append((job["key"], job["model"])))

    def jobs(self):
        return [(j["key"], j["state"]) for j in pickup.load(self.store)["jobs"]]

    def test_tiles_already_in_flight_when_the_board_first_looks_are_not_picked_up(self):
        self.tick(PLANS)
        self.assertEqual(self.fake.started, [])
        self.assertEqual(self.jobs(), [])

    def test_a_tile_in_flight_with_no_session_when_the_server_starts_is_picked_up(self):
        board = ("## In flight\n\n- [ ] PLN-01 **Fresh.**\n\n- [ ] PLN-02 **Mine.**\n  No pickup.\n\n"
                 "- [ ] PLN-03 **Had one.**\n  Claude session `ab12cd34`: `claude attach ab12cd34`.\n\n## Done\n")
        self.tick(board)  # the first look: all three are someone's
        tiles = serve.in_flight_tiles(self.store, board)
        self.assertEqual(pickup.adopt(self.store, tiles), ["PLN-01"])
        self.tick(board)
        self.assertEqual(self.fake.started, ["PLN-01"])
        self.assertEqual(pickup.adopt(self.store, tiles), [])  # it has a job now
        self.assertIn('  PLN-02 "Mine": no session: its tile says No pickup', pickup.startup_report(self.store, tiles, sessions=lambda: {}))

    def test_a_tile_in_flight_that_loses_its_no_pickup_is_picked_up(self):
        held = "## In flight\n\n- [ ] PLN-05 **Mine.**\n  No pickup. Rename things.\n\n## Done\n"
        self.tick("## In flight\n\n## Done\n")
        self.tick(held)
        self.tick(held)
        self.assertEqual(self.fake.started, [])
        self.tick(held.replace("No pickup. ", ""))
        self.assertEqual(self.fake.started, ["PLN-05"])

    def test_a_tile_that_lands_in_flight_is_started_at_once(self):
        self.tick(PLANS)
        self.tick(PLANS.replace("## In flight\n", "## In flight\n\n- [ ] PLN-90 **Waiting.**").replace("## To do\n\n- [ ] PLN-90 **Waiting.**\n", "## To do\n"))
        self.assertEqual(self.fake.started, ["PLN-90"])
        self.assertEqual(self.jobs(), [("PLN-90", "started")])

    def test_a_tile_that_seems_to_arrive_again_keeps_the_session_it_has(self):
        one = "## In flight\n\n- [ ] PLN-01 **One.**\n\n## Done\n"
        self.tick("## In flight\n\n## Done\n")
        self.tick(one)
        self.tick("## In flight\n", later=10)  # half a board, read while its session was still new
        self.tick(one)
        self.assertEqual(self.fake.started, ["PLN-01"])
        self.assertEqual(self.jobs(), [("PLN-01", "started")])

    def test_a_tile_that_left_and_comes_back_after_its_session_ended_is_picked_up_afresh(self):
        one = "## In flight\n\n- [ ] PLN-01 **One.**\n\n## Done\n"
        self.tick("## In flight\n\n## Done\n")
        self.tick(one)
        self.fake.idle.add("s1")
        self.tick("## In flight\n\n## Done\n")
        self.tick(one)
        self.assertEqual(self.fake.started, ["PLN-01", "PLN-01"])

    def test_a_master_s_sub_stories_run_two_at_a_time(self):
        kids = "".join(f"- [ ] PLN-0{n} **Kid {n}.**\n  Part of PLN-01.\n" for n in (2, 3, 4))
        family = "## In flight\n\n- [ ] PLN-01 **Master.**\n" + kids + "\n## Done\n"
        self.tick("## In flight\n\n## Done\n")
        state = self.tick(family)
        self.assertEqual(self.jobs(), [("PLN-02", "started"), ("PLN-03", "started"), ("PLN-04", "waiting")])
        self.assertEqual(state["jobs"][2]["behind"], "PLN-02")
        self.fake.idle.add("s1")  # the first one finished its turn
        self.tick(family)
        self.assertEqual(self.jobs(), [("PLN-02", "idle"), ("PLN-03", "started"), ("PLN-04", "started")])

    def test_a_sub_story_keeps_the_model_it_took_from_its_master(self):
        family = "## In flight\n\n- [ ] PLN-01 **Master.**\n  Model: sonnet\n- [ ] PLN-02 **Kid.**\n  Part of PLN-01.\n"
        self.tick("## In flight\n")
        self.tick(family)
        self.tick(family)
        self.assertEqual([j["model"] for j in pickup.load(self.store)["jobs"]], ["sonnet"])
        self.assertEqual(self.fake.switched, [])

    def test_a_second_tile_waits_for_the_first_session_to_end(self):
        empty = "## In flight\n\n## Done\n"
        self.tick(empty)
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n\n## Done\n")
        both = "## In flight\n\n- [ ] PLN-01 **One.**\n- [ ] PLN-02 **Two.**\n\n## Done\n"
        state = self.tick(both)
        self.assertEqual(self.jobs(), [("PLN-01", "started"), ("PLN-02", "waiting")])
        self.assertEqual(state["jobs"][1]["behind"], "PLN-01")
        self.fake.idle.add("s1")  # the first session finished its turn
        self.tick(both)
        self.assertEqual(self.fake.started, ["PLN-01", "PLN-02"])
        self.assertEqual(self.jobs(), [("PLN-01", "idle"), ("PLN-02", "started")])

    def test_an_idle_session_that_goes_back_to_work_is_live_again(self):
        self.tick("## In flight\n")
        one = "## In flight\n\n- [ ] PLN-01 **One.**\n"
        self.tick(one)
        self.fake.idle.add("s1")
        self.tick(one)
        self.assertEqual(self.jobs(), [("PLN-01", "idle")])
        self.fake.idle.clear()
        self.tick(one)
        self.assertEqual(self.jobs(), [("PLN-01", "started")])

    def test_an_idle_session_that_waits_for_input_says_so_until_it_is_at_work_again(self):
        self.tick("## In flight\n")
        one = "## In flight\n\n- [ ] PLN-01 **One.**\n"
        self.tick(one)
        self.fake.idle.add("s1")
        self.tick(one)
        self.assertIsNone(pickup.load(self.store)["jobs"][0].get("waiting"))  # it just finished its turn
        self.fake.asking["s1"] = "question"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.tick(one)
        self.assertEqual(pickup.load(self.store)["jobs"][0]["waiting"], "question")
        self.assertIn("PLN-01 session s1 is waiting for your input (it asked a question)", out.getvalue())
        self.fake.idle.clear()
        self.tick(one)
        self.assertNotIn("waiting", pickup.load(self.store)["jobs"][0])

    def test_a_tile_that_says_it_is_ready_for_review_is_marked_and_said_once(self):
        self.tick("## In flight\n")
        one = "## In flight\n\n- [ ] PLN-01 **One.**\n"
        self.tick(one)
        self.assertFalse(pickup.load(self.store)["jobs"][0].get("ready"))
        ready = one + "  **Ready for review:** gates green.\n"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.tick(ready)
            self.tick(ready)
        self.assertTrue(pickup.load(self.store)["jobs"][0]["ready"])
        self.assertEqual(out.getvalue().count("PLN-01 is ready for review: gates green."), 1)
        self.tick(one)  # work went on: the line was taken off
        self.assertFalse(pickup.load(self.store)["jobs"][0].get("ready"))

    def test_a_session_that_is_no_longer_there_is_lost_and_the_next_tile_goes(self):
        self.tick("## In flight\n")
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n")
        self.fake.live.clear()  # stopped, closed or removed
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n- [ ] PLN-02 **Two.**\n")
        self.assertEqual(self.jobs(), [("PLN-01", "lost"), ("PLN-02", "started")])

    def test_an_idle_or_lost_job_is_closed_once_its_tile_leaves_in_flight(self):
        self.tick("## In flight\n")
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n")
        self.fake.idle.add("s1")
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n")
        self.tick("## In flight\n\n## Done\n\n- [x] PLN-01 **One.**\n")
        self.assertEqual(self.jobs(), [("PLN-01", "ended")])

    def test_a_job_an_older_board_closed_while_its_tile_is_still_in_flight_is_looked_at_again(self):
        self.tick("## In flight\n")
        one = "## In flight\n\n- [ ] PLN-01 **One.**\n"
        self.tick(one)
        state = pickup.load(self.store)
        state["jobs"][0]["state"] = "ended"  # as the first version of pickup left a stopped session
        pickup.save(self.store, state)
        self.fake.live.clear()
        self.tick(one)
        self.assertEqual(self.jobs(), [("PLN-01", "lost")])

    def test_changing_a_tile_s_model_in_flight_moves_its_session_to_that_model(self):
        self.tick("## In flight\n")
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n")
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n  Model: sonnet\n")
        self.assertEqual(self.fake.switched, [("PLN-01", "started", "sonnet")])
        job = pickup.load(self.store)["jobs"][0]
        self.assertEqual((job["state"], job["model"]), ("started", "sonnet"))
        self.assertEqual(self.noted, [("PLN-01", "opus"), ("PLN-01", "sonnet")])  # the tile's session line follows
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n  Model: sonnet\n")
        self.assertEqual(len(self.fake.switched), 1)  # once

    def test_an_idle_session_is_switched_too_and_a_lost_or_queued_one_just_takes_the_model(self):
        self.tick("## In flight\n")
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n")
        self.fake.idle.add("s1")
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n- [ ] PLN-02 **Two.**\n- [ ] PLN-03 **Three.**\n")
        self.assertEqual(self.jobs(), [("PLN-01", "idle"), ("PLN-02", "started"), ("PLN-03", "waiting")])
        self.fake.live.discard("s2")  # PLN-02's session is lost
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n  Model: haiku\n- [ ] PLN-02 **Two.**\n  Model: haiku\n- [ ] PLN-03 **Three.**\n  Model: haiku\n")
        self.assertEqual(self.fake.switched, [("PLN-01", "idle", "haiku")])
        self.assertEqual({j["key"]: j["model"] for j in pickup.load(self.store)["jobs"]},
                         {"PLN-01": "haiku", "PLN-02": "haiku", "PLN-03": "haiku"})

    def test_a_job_from_before_models_is_not_switched_when_the_board_first_sees_it(self):
        self.tick("## In flight\n")
        one = "## In flight\n\n- [ ] PLN-01 **One.**\n"
        self.tick(one)
        state = pickup.load(self.store)
        del state["jobs"][0]["model"]
        pickup.save(self.store, state)
        self.tick(one)
        self.assertEqual(self.fake.switched, [])
        self.assertEqual(pickup.load(self.store)["jobs"][0]["model"], "opus")

    def test_a_switch_that_fails_leaves_the_session_as_it_was_and_is_not_tried_again(self):
        self.tick("## In flight\n")
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n")
        self.fake.switch = lambda store, job, model: {"state": "failed", "error": "no"}
        changed = "## In flight\n\n- [ ] PLN-01 **One.**\n  Model: sonnet\n"
        self.tick(changed)
        job = pickup.load(self.store)["jobs"][0]
        self.assertEqual((job["state"], job["model"], job["id"]), ("started", "sonnet", "s1"))

    def restart(self, key):
        return pickup.restart(self.store, key, start=self.fake.start, resume=self.fake.resume)

    def test_restart_resumes_the_same_session(self):
        self.tick("## In flight\n")
        one = "## In flight\n\n- [ ] PLN-01 **One.**\n"
        self.tick(one)
        self.fake.idle.add("s1")  # interrupted: it sits idle
        self.tick(one)
        self.assertTrue(self.restart("PLN-01"))
        self.assertEqual((self.fake.resumed, self.fake.started), (["s1-full"], ["PLN-01"]))
        self.assertEqual(self.jobs(), [("PLN-01", "started")])

    def test_restart_starts_afresh_in_the_same_worktree_when_the_session_cannot_be_resumed(self):
        self.tick("## In flight\n")
        one = "## In flight\n\n- [ ] PLN-01 **One.**\n"
        self.tick(one)
        self.fake.live.clear()
        self.tick(one)
        self.assertEqual(self.jobs(), [("PLN-01", "lost")])
        self.fake.resume = lambda store, job, text=None: {"state": "failed", "error": "no such conversation"}
        self.assertTrue(self.restart("PLN-01"))
        self.assertEqual(self.fake.started, ["PLN-01", "PLN-01*"])  # the second with the restart prompt
        job = pickup.load(self.store)["jobs"][0]
        self.assertEqual((job["state"], job["id"]), ("started", "s2"))

    def test_restart_is_only_for_a_session_that_stopped(self):
        self.tick("## In flight\n")
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n")
        self.assertFalse(self.restart("PLN-01"))  # it is at work
        self.assertFalse(self.restart("PLN-09"))  # never picked up

    def nudge(self, key):
        return pickup.restart(self.store, key, start=self.fake.start, resume=self.fake.resume, stop=self.stop, nudge=True)

    def stop(self, sid):
        self.fake.stopped.append(sid)
        self.fake.live.discard(sid)

    def test_continue_stops_a_session_at_work_and_resumes_it_told_to_carry_on(self):
        self.tick("## In flight\n")
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n")
        self.assertTrue(self.nudge("PLN-01"))
        self.assertEqual((self.fake.stopped, self.fake.resumed), (["s1"], ["s1-full"]))
        self.assertIn("looked stuck", self.fake.told[0])
        self.assertIn("PLN-01", self.fake.told[0])
        self.assertEqual(self.jobs(), [("PLN-01", "started")])

    def test_continue_stops_an_idle_session_that_waits_for_input_before_resuming_it(self):
        self.tick("## In flight\n")
        one = "## In flight\n\n- [ ] PLN-01 **One.**\n"
        self.tick(one)
        self.fake.idle.add("s1")
        self.fake.asking["s1"] = "question"
        self.tick(one)
        self.assertTrue(self.nudge("PLN-01"))
        self.assertEqual((self.fake.stopped, self.fake.resumed), (["s1"], ["s1-full"]))
        self.assertNotIn("waiting", pickup.load(self.store)["jobs"][0])

    def test_continue_on_a_lost_session_resumes_it_without_stopping_anything(self):
        self.tick("## In flight\n")
        one = "## In flight\n\n- [ ] PLN-01 **One.**\n"
        self.tick(one)
        self.fake.live.clear()
        self.tick(one)
        self.assertTrue(self.nudge("PLN-01"))
        self.assertEqual((self.fake.stopped, self.fake.resumed), ([], ["s1-full"]))
        self.assertEqual(self.fake.told, [None])  # told it was restarted (RESUME), not that it looked stuck

    def test_continue_starts_a_new_session_when_the_stuck_one_cannot_be_resumed(self):
        self.tick("## In flight\n")
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n")
        self.fake.resume = lambda store, job, text=None: {"state": "failed", "error": "no such conversation"}
        self.assertTrue(self.nudge("PLN-01"))
        self.assertEqual((self.fake.stopped, self.fake.started), (["s1"], ["PLN-01", "PLN-01*"]))

    def test_continue_that_cannot_stop_the_session_leaves_it_as_it_was(self):
        self.tick("## In flight\n")
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n")
        def stuck(sid):
            raise OSError("claude stop hung")
        self.assertFalse(pickup.restart(self.store, "PLN-01", start=self.fake.start, resume=self.fake.resume, stop=stuck, nudge=True))
        self.assertEqual((self.fake.resumed, self.jobs()), ([], [("PLN-01", "started")]))

    def test_continue_is_only_for_a_tile_the_board_started_a_session_for(self):
        self.tick("## In flight\n")
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n")
        self.assertFalse(self.nudge("PLN-09"))
        self.assertEqual(self.fake.stopped, [])

    def pause(self, key, resume=None):
        return pickup.pause(self.store, key, resume=resume or self.fake.resume, stop=self.stop, now=lambda: self.clock)

    def job(self, key="PLN-01"):
        return next(j for j in reversed(pickup.load(self.store)["jobs"]) if j["key"] == key)

    def test_pause_stops_a_session_at_work_and_has_it_write_its_handoff_and_stop(self):
        self.tick("## In flight\n")
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n")
        self.assertTrue(self.pause("PLN-01"))
        self.assertEqual((self.fake.stopped, self.fake.resumed), (["s1"], ["s1-full"]))
        self.assertIn("Pause", self.fake.told[0])
        self.assertIn('"Handoff:" line in full', self.fake.told[0])
        self.assertEqual((self.job()["state"], self.job().get("pausing")), ("started", True))

    def test_a_pausing_session_that_stops_is_paused_and_the_next_tile_starts(self):
        self.tick("## In flight\n")
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n")
        both = "## In flight\n\n- [ ] PLN-01 **One.**\n- [ ] PLN-02 **Two.**\n"
        self.tick(both)
        self.pause("PLN-01")
        self.tick(both)  # still writing its handoff: PLN-02 waits
        self.assertEqual(self.jobs(), [("PLN-01", "started"), ("PLN-02", "waiting")])
        self.fake.idle.add("s1")  # done: it stopped
        self.tick(both)
        self.assertEqual(self.jobs(), [("PLN-01", "paused"), ("PLN-02", "started")])
        self.assertNotIn("pausing", self.job())

    def test_a_paused_tile_is_left_alone_until_continue_has_a_new_session_take_over(self):
        self.tick("## In flight\n")
        one = "## In flight\n\n- [ ] PLN-01 **One.**\n"
        self.tick(one)
        self.pause("PLN-01")
        self.fake.live.clear()  # its session closed after the handoff
        self.tick(one)
        self.tick(one)
        self.assertEqual((self.jobs(), self.fake.started), ([("PLN-01", "paused")], ["PLN-01"]))
        self.assertTrue(self.nudge("PLN-01"))
        self.assertEqual(self.fake.started, ["PLN-01", "PLN-01*"])  # a new session, told it takes over
        self.assertEqual(self.fake.resumed, ["s1-full"])  # only the handoff turn: the old conversation isn't resumed
        self.assertEqual((self.job()["state"], self.job()["id"]), ("started", "s2"))

    def test_pause_is_only_for_a_session_at_work(self):
        self.tick("## In flight\n")
        one = "## In flight\n\n- [ ] PLN-01 **One.**\n"
        self.tick(one)
        self.fake.idle.add("s1")
        self.tick(one)
        self.assertFalse(self.pause("PLN-01"))  # idle
        self.assertFalse(self.pause("PLN-09"))  # never picked up
        self.assertEqual(self.fake.stopped, [])

    def test_a_session_that_cannot_be_told_to_write_its_handoff_is_paused_at_once(self):
        self.tick("## In flight\n")
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n")
        self.assertTrue(self.pause("PLN-01", resume=lambda store, job, text=None: {"state": "failed", "error": "no such conversation"}))
        self.assertEqual(self.jobs(), [("PLN-01", "paused")])

    def test_pause_that_cannot_stop_the_session_leaves_it_at_work(self):
        self.tick("## In flight\n")
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n")
        def stuck(sid):
            raise OSError("claude stop hung")
        self.assertFalse(pickup.pause(self.store, "PLN-01", resume=self.fake.resume, stop=stuck, now=lambda: self.clock))
        self.assertEqual((self.fake.resumed, self.jobs()), ([], [("PLN-01", "started")]))

    def test_pause_on_a_session_still_writing_its_handoff_stops_it_now(self):
        self.tick("## In flight\n")
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n")
        self.pause("PLN-01")
        self.assertTrue(self.pause("PLN-01"))  # it hung on its handoff: Pause now
        self.assertEqual((self.fake.stopped, self.fake.resumed), (["s1", "s1"], ["s1-full"]))
        self.assertEqual(self.jobs(), [("PLN-01", "paused")])
        self.assertNotIn("pausing", self.job())

    def test_a_model_change_while_it_writes_its_handoff_waits_for_the_take_over(self):
        self.tick("## In flight\n")
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n")
        self.pause("PLN-01")
        sonnet = "## In flight\n\n- [ ] PLN-01 **One.**\n  Model: sonnet\n"
        self.tick(sonnet)
        self.assertEqual(self.fake.switched, [])  # not resumed with "carry on" over the pause
        self.assertTrue(self.job().get("pausing"))
        self.fake.idle.add("s1")
        self.tick(sonnet)
        self.assertTrue(self.nudge("PLN-01"))
        self.assertEqual((self.fake.started, self.job()["model"]), (["PLN-01", "PLN-01*"], "sonnet"))

    def test_a_paused_story_is_taken_over_with_the_repos_its_tile_names_now(self):
        self.tick("## In flight\n")
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n")
        self.pause("PLN-01", resume=lambda store, job, text=None: {"state": "failed"})
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n  Repos: api, web\n")
        self.assertEqual(self.job()["want"], ["api", "web"])
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n")
        self.assertNotIn("want", self.job())

    def test_a_paused_tile_back_in_flight_queues_behind_those_already_waiting(self):
        self.tick("## In flight\n")
        self.tick("## In flight\n\n- [ ] PLN-03 **Three.**\n")
        self.pause("PLN-03", resume=lambda store, job, text=None: {"state": "failed"})
        away = "## To do\n\n- [ ] PLN-03 **Three.**\n\n## In flight\n\n- [ ] PLN-01 **One.**\n"
        self.tick(away)
        self.tick(away.replace("- [ ] PLN-01 **One.**\n", "- [ ] PLN-01 **One.**\n- [ ] PLN-02 **Two.**\n"))
        back = "## In flight\n\n- [ ] PLN-01 **One.**\n- [ ] PLN-02 **Two.**\n- [ ] PLN-03 **Three.**\n"
        self.tick(back)
        self.fake.idle.add("s2")  # PLN-01 finishes its turn
        self.tick(back)
        self.assertEqual(self.fake.started, ["PLN-03", "PLN-01", "PLN-02"])  # PLN-02 queued first, so it goes first
        self.assertEqual(dict(self.jobs())["PLN-03"], "waiting")

    def test_an_archived_paused_story_is_done_with(self):
        self.tick("## In flight\n")
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n")
        self.pause("PLN-01", resume=lambda store, job, text=None: {"state": "failed"})
        serve.tidy_archived(self.store, ["- [x] PLN-01 **One.**"])
        self.assertEqual(self.jobs(), [("PLN-01", "ended")])

    def test_a_paused_job_is_remembered_however_many_jobs_come_after_it(self):
        paused = {"key": "PLN-01", "title": "One", "state": "paused", "id": "s1"}
        ended = [{"key": f"PLN-{n:02}", "title": "x", "state": "ended"} for n in range(2, 2 + pickup.KEEP + 5)]
        pickup.save(self.store, {"inflight": [], "jobs": [paused, *ended]})
        jobs = pickup.load(self.store)["jobs"]
        self.assertEqual((jobs[0]["key"], len(jobs)), ("PLN-01", pickup.KEEP + 1))

    def test_a_session_the_cli_says_is_blocked_on_input_is_waiting_for_you_not_working(self):
        self.tick("## In flight\n")
        one = "## In flight\n\n- [ ] PLN-01 **One.**\n"
        self.tick(one)
        self.fake.blocked.add("s1")
        self.tick(one)
        self.assertEqual((self.job()["state"], self.job()["waiting"]), ("idle", "question"))
        self.fake.blocked.clear()  # answered in the terminal: at work again
        self.tick(one)
        self.assertEqual(self.job()["state"], "started")
        self.assertNotIn("waiting", self.job())

    def test_the_session_list_reads_waiting_and_blocked_as_waiting(self):
        listed = [{"kind": "background", "id": "a", "status": "busy"}, {"kind": "background", "id": "b", "status": "idle", "state": "done"},
                  {"kind": "background", "id": "c", "status": "waiting", "state": "blocked", "waitingFor": "input needed"},
                  {"kind": "background", "id": "d", "state": "blocked"}, {"kind": "background", "id": "e"}]
        with mock.patch.object(agent, "_agents", lambda *flags: listed):
            self.assertEqual(agent.sessions(), {"a": "busy", "b": "idle", "c": "waiting", "d": "waiting"})

    def reply(self, key, text, resume=None, actions=()):
        return pickup.reply(self.store, key, text, actions, resume=resume or self.fake.resume, stop=self.stop, now=lambda: self.clock)

    def test_a_reply_goes_into_the_session_that_waits_for_input(self):
        self.tick("## In flight\n")
        one = "## In flight\n\n- [ ] PLN-01 **One.**\n"
        self.tick(one)
        self.fake.idle.add("s1")
        self.fake.asking["s1"] = "question"
        self.tick(one)
        self.assertIsNone(self.reply("PLN-01", "  Use the blue one.\n"))
        self.assertEqual((self.fake.stopped, self.fake.resumed), (["s1"], ["s1-full"]))
        self.assertIn("Use the blue one.", self.fake.told[0])
        self.assertIn("PLN-01", self.fake.told[0])
        self.assertEqual(self.jobs(), [("PLN-01", "started")])
        self.assertNotIn("waiting", self.job())

    def test_a_reply_to_a_story_ready_for_review_goes_to_its_session(self):
        self.tick("## In flight\n")
        ready = "## In flight\n\n- [ ] PLN-01 **One.**\n  **Ready for review:** gates green.\n"
        self.tick(ready)
        self.fake.idle.add("s1")
        self.tick(ready)
        self.assertTrue(self.job()["ready"])
        self.assertIsNone(self.reply("PLN-01", "Rename the helper, then it's good."))
        self.assertIn("Rename the helper", self.fake.told[0])
        self.assertIn("Ready for review", self.fake.told[0])  # told what to do with the line

    def test_a_review_reply_can_tell_it_to_squash_merge_and_push_in_that_order(self):
        self.tick("## In flight\n")
        ready = "## In flight\n\n- [ ] PLN-01 **One.**\n  **Ready for review:** gates green.\n"
        self.tick(ready)
        self.fake.idle.add("s1")
        self.tick(ready)
        self.assertIsNone(self.reply("PLN-01", "", actions=["push", "squash", "merge"]))  # no words needed
        told = self.fake.told[0]
        steps = [told.index(w) for w in ("`git merge-base main HEAD`", "fast-forward main to it", "Then push main")]
        self.assertEqual(steps, sorted(steps))
        self.assertLess(told.index("Rebase the branch onto main"), told.index("squash the commits"))  # never squash against an old main
        self.assertIn("no merge commit", told)
        self.assertIn('take the "Ready for review" line off', told)
        self.assertIn("for these steps, now", told)
        self.fake.idle.add("s1")
        self.tick(ready)
        self.assertIsNone(self.reply("PLN-01", "", actions=["push"]))  # pushing alone pushes the branch
        self.assertIn("Push the branch", self.fake.told[-1])
        self.assertNotIn("squash the commits", self.fake.told[-1])
        self.assertIn('keep the "Ready for review" line', self.fake.told[-1])  # still not merged: still for review
        self.assertIn("not an action", self.reply("PLN-01", "", actions=["deploy"]))

    def test_words_and_ticked_steps_go_words_first_and_a_plain_reply_withholds_the_steps(self):
        st = stores.Store(tempfile.mkdtemp())
        job = {"key": "PLN-01", "branch": "b", "worktree": "/x/app", "ready": True}
        both = pickup.reply_text(st, job, "Rename the helper.", ["merge"])
        self.assertLess(both.index("Rename the helper."), both.index("First do what they wrote"))
        self.assertLess(both.index("First do what they wrote"), both.index("fast-forward"))
        plain = pickup.reply_text(st, job, "Rename the helper.")
        self.assertIn("Don't squash, merge or push", plain)

    def test_steps_for_a_story_in_several_repos_name_each_repo_and_its_trunk(self):
        st = stores.Store(tempfile.mkdtemp())
        job = {"key": "PLN-01", "branch": "b", "ready": True,
               "repos": [{"name": "api", "worktree": "/x/api-wt", "trunk": "dev"}, {"name": "web", "worktree": "/x/web-wt", "trunk": "main"}]}
        told = pickup.reply_text(st, job, "", ["squash", "merge", "push"])
        self.assertIn("in every repo this story works in", told)
        self.assertIn("api (trunk dev, worktree /x/api-wt)", told)
        self.assertIn("web (trunk main, worktree /x/web-wt)", told)
        self.assertIn("Then push the trunk.", told)

    def test_a_reply_that_stopped_an_idle_session_but_could_not_resume_it_leaves_it_gone(self):
        self.tick("## In flight\n")
        one = "## In flight\n\n- [ ] PLN-01 **One.**\n"
        self.tick(one)
        self.fake.idle.add("s1")
        self.fake.asking["s1"] = "question"
        self.tick(one)
        why = self.reply("PLN-01", "hello", resume=lambda store, job, text=None: {"state": "failed", "error": "no such conversation"})
        self.assertIn("Continue", why)
        self.assertEqual(self.jobs(), [("PLN-01", "lost")])

    def test_a_reply_to_a_gone_session_resumes_it_without_stopping_anything(self):
        self.tick("## In flight\n")
        one = "## In flight\n\n- [ ] PLN-01 **One.**\n"
        self.tick(one)
        self.fake.live.clear()
        self.tick(one)
        self.assertIsNone(self.reply("PLN-01", "Carry on with the tests."))
        self.assertEqual((self.fake.stopped, self.fake.resumed), ([], ["s1-full"]))

    def test_a_message_to_a_session_at_work_interrupts_it_and_it_carries_on_after(self):
        self.tick("## In flight\n")
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n")
        self.assertIsNone(self.reply("PLN-01", "Leave the CSS alone."))
        self.assertEqual((self.fake.stopped, self.fake.resumed), (["s1"], ["s1-full"]))
        told = self.fake.told[0]
        self.assertIn("interrupted", told)
        self.assertIn("Leave the CSS alone.", told)
        self.assertIn("carry on", told)
        self.assertEqual(self.jobs(), [("PLN-01", "started")])

    def test_no_message_goes_to_a_paused_or_pausing_story(self):
        self.tick("## In flight\n")
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n")
        self.pause("PLN-01")
        self.assertIn("pausing", self.reply("PLN-01", "hello"))
        self.pause("PLN-01")  # Pause now
        self.assertIn("paused", self.reply("PLN-01", "hello"))
        self.assertEqual(self.fake.resumed, ["s1-full"])  # only the handoff turn

    def test_a_reply_is_refused_when_it_says_nothing(self):
        self.tick("## In flight\n")
        one = "## In flight\n\n- [ ] PLN-01 **One.**\n"
        self.tick(one)
        self.fake.idle.add("s1")
        self.tick(one)
        self.assertIn("empty", self.reply("PLN-01", "  \n "))
        self.assertIn("no session", self.reply("PLN-09", "hello"))
        self.assertEqual((self.fake.stopped, self.fake.resumed), ([], []))

    def test_a_reply_that_cannot_reach_the_conversation_says_so_and_leaves_the_session(self):
        self.tick("## In flight\n")
        one = "## In flight\n\n- [ ] PLN-01 **One.**\n"
        self.tick(one)
        self.fake.live.clear()
        self.tick(one)
        why = self.reply("PLN-01", "hello", resume=lambda store, job, text=None: {"state": "failed", "error": "no such conversation"})
        self.assertIn("no such conversation", why)
        self.assertEqual(self.jobs(), [("PLN-01", "lost")])

    def test_a_tile_moved_out_of_in_flight_while_at_work_is_paused_as_the_button_does(self):
        self.tick("## In flight\n")
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n")
        todo = "## To do\n\n- [ ] PLN-01 **One.**\n\n## In flight\n"
        self.tick(todo)
        self.assertEqual((self.fake.stopped, self.fake.resumed), (["s1"], ["s1-full"]))
        self.assertIn('"Handoff:" line in full', self.fake.told[0])
        self.assertEqual((self.job()["state"], self.job().get("pausing")), ("started", True))
        self.fake.idle.add("s1")
        self.tick(todo)
        self.assertEqual(self.jobs(), [("PLN-01", "paused")])
        self.tick(todo)  # and it stays paused out there
        self.assertEqual((self.jobs(), self.fake.stopped), ([("PLN-01", "paused")], ["s1"]))

    def test_a_paused_tile_moved_back_into_in_flight_is_taken_over_by_a_new_session(self):
        self.tick("## In flight\n")
        one = "## In flight\n\n- [ ] PLN-01 **One.**\n"
        self.tick(one)
        self.tick("## To do\n\n- [ ] PLN-01 **One.**\n\n## In flight\n")
        self.fake.idle.add("s1")
        self.tick("## To do\n\n- [ ] PLN-01 **One.**\n\n## In flight\n")
        self.tick(one)
        self.assertEqual(self.fake.started, ["PLN-01", "PLN-01*"])  # a new session, told it takes over
        self.assertEqual(self.fake.resumed, ["s1-full"])  # only the handoff turn
        self.assertEqual((self.jobs(), self.job()["id"]), ([("PLN-01", "started")], "s2"))

    def test_a_paused_tile_moved_back_in_waits_its_turn_and_stays_paused_if_it_leaves_first(self):
        self.tick("## In flight\n")
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n")
        self.pause("PLN-01")
        self.fake.idle.add("s1")
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n")
        self.tick("## In flight\n\n- [ ] PLN-02 **Two.**\n\n## To do\n\n- [ ] PLN-01 **One.**\n")  # PLN-02 at work, PLN-01 out
        both = "## In flight\n\n- [ ] PLN-01 **One.**\n- [ ] PLN-02 **Two.**\n"
        self.tick(both)
        self.assertEqual(self.jobs(), [("PLN-02", "started"), ("PLN-01", "waiting")])  # at the back of the queue
        self.tick("## In flight\n\n- [ ] PLN-02 **Two.**\n\n## To do\n\n- [ ] PLN-01 **One.**\n")
        self.assertEqual(self.jobs(), [("PLN-02", "started"), ("PLN-01", "paused")])
        self.assertEqual(self.job()["id"], "s1")  # its session and worktree are kept

    def test_a_new_session_on_a_paused_story_is_told_it_was_paused_and_to_go_by_the_handoff(self):
        st = stores.Store("/x/plans")
        job = {"key": "PLN-80", "title": "On its own", "with": [], "text": "- [ ] PLN-80 **On its own.**"}
        text = pickup.prompt(st, job, "b-name", Path("/x/app-wt"), "paused")
        self.assertIn("was paused from the board", text)
        self.assertIn('"Handoff:" line', text)
        self.assertNotIn("can't be resumed", text)

    def test_a_session_just_started_counts_as_live_before_it_shows_busy(self):
        self.tick("## In flight\n")
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n")
        self.fake.live.clear()  # not listed as busy yet
        both = "## In flight\n\n- [ ] PLN-01 **One.**\n- [ ] PLN-02 **Two.**\n"
        self.tick(both, later=5)
        self.assertEqual(self.jobs(), [("PLN-01", "started"), ("PLN-02", "waiting")])
        self.tick(both, later=pickup.SETTLE)
        self.assertEqual(self.jobs(), [("PLN-01", "lost"), ("PLN-02", "started")])

    def test_a_waiting_tile_can_be_started_in_parallel(self):
        self.tick("## In flight\n")
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n")
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n- [ ] PLN-02 **Two.**\n")
        self.assertTrue(pickup.start_now(self.store, "PLN-02", start=self.fake.start))
        self.assertEqual(self.jobs(), [("PLN-01", "started"), ("PLN-02", "started")])
        self.assertFalse(pickup.start_now(self.store, "PLN-02", start=self.fake.start))  # nothing waiting

    def test_the_console_says_what_was_picked_up_queued_and_ended(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.tick("## In flight\n")
            self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n")
            both = "## In flight\n\n- [ ] PLN-01 **One.**\n- [ ] PLN-02 **Two.**\n"
            self.tick(both)
            self.fake.live.clear()
            self.tick(both)
        lines = [line.split(" ", 2)[2] for line in out.getvalue().splitlines()]  # after the date and time
        self.assertEqual(lines, [
            'pickup: PLN-01 "One" picked up: session s1 (kandalf attach s1)',
            'pickup: PLN-02 "Two" queued behind PLN-01',
            "pickup: PLN-01 session s1 is gone (stopped, closed or removed): Continue is on its tile",
            'pickup: PLN-02 "Two" picked up: session s2 (kandalf attach s2)'])

    def test_the_console_says_why_a_start_failed(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.tick("## In flight\n")
            pickup.tick(self.store, serve.in_flight_tiles(self.store, "## In flight\n\n- [ ] PLN-01 **One.**\n"),
                        start=lambda store, job: {"state": "failed", "error": "no claude"}, sessions=self.fake.sessions)
        self.assertIn('pickup: PLN-01 "One" could not be started: no claude', out.getvalue())

    def test_a_waiting_tile_moved_out_of_in_flight_is_dropped(self):
        self.tick("## In flight\n")
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n")
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n- [ ] PLN-02 **Two.**\n")
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n")
        self.assertEqual(self.jobs(), [("PLN-01", "started")])

    def test_when_the_sessions_cannot_be_listed_nothing_more_is_started(self):
        self.tick("## In flight\n")
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n")
        self.fake.sessions = lambda: None
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n- [ ] PLN-02 **Two.**\n")
        self.assertEqual(self.jobs(), [("PLN-01", "started"), ("PLN-02", "waiting")])

    def test_a_start_that_fails_is_kept_with_its_error_and_the_next_one_goes(self):
        def broken(store, job):
            return {"state": "failed", "error": "no claude"}
        self.tick("## In flight\n")
        pickup.tick(self.store, serve.in_flight_tiles(self.store, "## In flight\n\n- [ ] PLN-01 **One.**\n"),
                    start=broken, sessions=self.fake.sessions)
        self.assertEqual(pickup.load(self.store)["jobs"][0]["error"], "no claude")
        self.tick("## In flight\n\n- [ ] PLN-01 **One.**\n- [ ] PLN-02 **Two.**\n")
        self.assertEqual(self.jobs(), [("PLN-01", "failed"), ("PLN-02", "started")])


class Startup(unittest.TestCase):
    """What the server prints about In flight when it starts."""

    BOARD = """## In flight

- [ ] PLN-01 **At work.**
  Claude session `aaaa1111`: `claude attach aaaa1111`. Branch `pln-01-at-work`.
- [ ] PLN-02 **Idle one.**
  Claude session `bbbb2222`: `claude attach bbbb2222`.
- [ ] PLN-03 **Gone one.**
  Claude session `cccc3333`: `claude attach cccc3333`.
- [ ] PLN-04 **Queued one.**
- [ ] PLN-05 **By hand.**
"""

    def setUp(self):
        self.store = stores.Store(tempfile.mkdtemp())
        self.tiles = serve.in_flight_tiles(self.store, self.BOARD)
        pickup.save(self.store, {"inflight": ["PLN-01"], "jobs": [
            {"key": "PLN-04", "title": "Queued one", "state": "waiting", "behind": "PLN-01"},
            {"key": "PLN-09", "title": "Moved to Done", "state": "started", "id": "dddd4444"}]})

    def test_each_in_flight_tile_is_listed_with_its_session(self):
        there = {"aaaa1111": "busy", "bbbb2222": "idle", "dddd4444": "busy"}
        self.assertEqual(pickup.startup_report(self.store, self.tiles, sessions=lambda: there), [
            "5 in flight:",
            '  PLN-01 "At work": session aaaa1111 at work',
            '  PLN-02 "Idle one": session bbbb2222 idle',
            '  PLN-03 "Gone one": session cccc3333 gone (Continue is on its tile)',
            '  PLN-04 "Queued one": queued behind PLN-01',
            '  PLN-05 "By hand": no session',
            "1 other session the board started:",
            '  PLN-09 "Moved to Done": session dddd4444 at work'])

    def test_it_says_so_when_the_sessions_cannot_be_listed_or_nothing_is_in_flight(self):
        lines = pickup.startup_report(self.store, self.tiles[:1], sessions=lambda: None)
        self.assertEqual(lines, ["1 in flight:", '  PLN-01 "At work": session aaaa1111 (the Claude sessions could not be listed)'])
        pickup.save(self.store, {"inflight": [], "jobs": []})
        self.assertEqual(pickup.startup_report(self.store, [], sessions=lambda: {}), ["Nothing in flight."])


DAY = time.strftime("%Y-%m-%d", time.localtime(1795000000))


class Note(unittest.TestCase):
    """The session's id is written into the tile it was started for."""

    def setUp(self):
        self.store = stores.Store(tempfile.mkdtemp())
        self.job = {"key": "PLN-70", "id": "ab12cd34", "branch": "pln-70-dark-mode", "worktree": "/x/app-pln-70", "model": "opus", "started": 1795000000}
        self.line = "  Claude session `ab12cd34`: `kandalf attach ab12cd34`. Branch `pln-70-dark-mode`, worktree `app-pln-70`. Model `opus`. Started " + DAY + "."

    def round_trips(self, text):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write(text)
        try:
            return subprocess.run(["node", str(Path(__file__).resolve().parent / "kandalf" / "check.js"), f.name], capture_output=True).returncode == 0
        finally:
            Path(f.name).unlink()

    def test_the_line_goes_after_the_description_and_before_the_sub_tasks(self):
        new = serve.with_session(self.store, PLANS, self.job)
        self.assertIn("- [ ] PLN-70 **Dark mode.** The master.\n"
                      + self.line + "\n"
                      "  - [ ] **Agree the scope.**\n", new)
        self.assertEqual(new.count("Claude session"), 1)
        self.assertTrue(self.round_trips(new))

    def test_a_tile_with_no_sub_tasks_gets_it_as_its_last_line(self):
        new = serve.with_session(self.store, PLANS, {**self.job, "key": "PLN-75"})
        self.assertIn("  Part of PLN-70.\n" + self.line + "\n- [ ] PLN-80", new)
        self.assertTrue(self.round_trips(new))

    def test_a_tile_picked_up_again_has_its_line_replaced(self):
        once = serve.with_session(self.store, PLANS, self.job)
        edited = once.replace("Started " + DAY, "(cut from dev at `ebf12a01b`). Started " + DAY)  # the session added to it
        twice = serve.with_session(self.store, edited, {**self.job, "id": "ffff0000"})
        self.assertEqual(twice.count("Claude session"), 1)
        self.assertIn("  Claude session `ffff0000`: `kandalf attach ffff0000`. Branch `pln-70-dark-mode`", twice)

    def test_a_tile_that_is_gone_leaves_the_board_as_it_was(self):
        self.assertEqual(serve.with_session(self.store, PLANS, {**self.job, "key": "PLN-99"}), PLANS)

    def test_tick_hands_a_started_job_to_be_noted(self):
        fake, noted = Fake(), []
        look = lambda text: pickup.tick(self.store, serve.in_flight_tiles(self.store, text), start=fake.start,
                                        sessions=fake.sessions, on_start=lambda store, job: noted.append((job["key"], job["id"])))
        with contextlib.redirect_stdout(io.StringIO()):
            look("## In flight\n")
            look("## In flight\n\n- [ ] PLN-01 **One.**\n")
        self.assertEqual(noted, [("PLN-01", "s1")])


class Waiting(unittest.TestCase):
    """Reading a session's recent output for whether it waits for input."""

    def test_the_last_thing_on_its_screen_says_whether_it_waits(self):
        esc = "\x1b[2K\x1b[1G"
        self.assertEqual(agent.waiting_for(f"Ran{esc}shell commands{esc}⎿ Interrupted · What should Claude do instead?{esc}⏵⏵ auto mode on"), "interrupted")
        self.assertEqual(agent.waiting_for(f"Bash(rm -rf build){esc}Do you want to proceed?{esc}❯ 1. Yes{esc}  2. No"), "question")
        self.assertIsNone(agent.waiting_for(f"⎿ Interrupted · What should Claude do instead?{esc}carry on{esc}Worked for 2m 4s · done 2:20 PM"))
        self.assertIsNone(agent.waiting_for("Flambéing… (2s)"))
        self.assertIsNone(agent.waiting_for(""))

    def test_the_last_of_its_screen_is_read_without_the_terminal_s_own_furniture(self):
        esc = "\x1b[2K\x1b[1G"
        parts = ["Old words that scrolled away " * 20, "a23438d43", "committed,", "one small follow-up edit", "waiting on the gate.",
                 "I'll report as soon as it **finishes**.", "Befuddling…", "(2s · ↓ 12 tokens)", "1 shell", "Brewed for 8s ·",
                 "done 3:30 PM · 1 shell still running", "⏵⏵ auto mode on", "(shift+tab to cycle)", "· ← for agents · esc to interrupt"]
        want = "a23438d43 committed, one small follow-up edit waiting on the gate. I'll report as soon as it finishes."
        self.assertEqual(agent.screen_tail(esc.join(parts), limit=len(want)), "… " + want)
        self.assertEqual(agent.screen_tail(esc.join(parts), limit=len(want) - 3), "… " + want.split(" ", 1)[1])  # never half a word
        self.assertEqual(agent.screen_tail("short and whole"), "short and whole")
        self.assertEqual(agent.screen_tail(esc.join(["Befuddling…", "⏵⏵ auto mode on"])), "")
        footer = ["Running the gate", "──── PLN-78 Stripe statuses ──", "· ↓", "to manage", "2 shells", "· ↓ to manage"]
        self.assertEqual(agent.screen_tail(esc.join(footer)), "Running the gate")

    def test_the_status_says_a_waiting_session_waits(self):
        self.assertEqual(pickup.status_text("ab12cd34", "question", None, "dev", now=0),
                         "session `ab12cd34` waiting for your input (it asked a question). Its worktree isn't there.")
        self.assertIn("waiting for your input (it was interrupted)", pickup.status_text("ab12cd34", "interrupted", None, "dev", now=0))


class Status(unittest.TestCase):
    """Refresh status: where a story's session and branch stand right now, written into the tile."""

    def test_the_status_says_what_the_session_is_doing_and_where_the_branch_is(self):
        git_facts = {"ahead": 3, "last_at": 1000 - 12 * 60, "last": "feat: the snippet (PLN-01)", "dirty": 2}
        self.assertEqual(pickup.status_text("ab12cd34", "busy", git_facts, "dev", now=1000),
                         'session `ab12cd34` at work. Branch 3 commits ahead of dev; last commit 12 min ago: "feat: the snippet (PLN-01)". 2 files not committed.')
        self.assertEqual(pickup.status_text("ab12cd34", "idle", {"ahead": 1, "last_at": 1000 - 3 * 3600, "last": "x", "dirty": 0}, "dev", now=1000),
                         'session `ab12cd34` idle. Branch 1 commit ahead of dev; last commit 3 h ago: "x". Nothing uncommitted.')
        self.assertEqual(pickup.status_text("ab12cd34", None, {"ahead": 0, "last_at": 0, "last": "", "dirty": 1}, "dev", now=1000),
                         "session `ab12cd34` gone. Branch has no commits yet. 1 file not committed.")
        self.assertEqual(pickup.status_text("ab12cd34", "unknown", None, "dev", now=1000),
                         "session `ab12cd34` could not be checked. Its worktree isn't there.")
        self.assertEqual(pickup.age(2 * 86400 + 5), "2 d")

    def test_the_eta_goes_by_the_pace_of_the_sub_tasks_else_by_the_estimate(self):
        hour, now = 3600, 1795000000
        at = lambda secs: time.strftime("%Y-%m-%d %H:%M", time.localtime(now + secs))
        tile = lambda head, ticks: "- [ ] PLN-01 " + head + "**One.**\n" + "".join(f"  - [{t}] **Step.**\n" for t in ticks)
        eta = lambda head, ticks, ago: pickup.eta_text(tile(head, ticks), now - ago if ago else None, now)
        self.assertEqual(eta("", "xx   ", 2 * hour), f"2 of 5 sub-tasks ticked in 2 h; at that pace about 3 h to go (ETA {at(3 * hour)}).")
        self.assertEqual(eta("~4h ", "   ", hour), f"0 of 3 sub-tasks ticked after 1 h; by its ~4h estimate about 3 h to go (ETA {at(3 * hour)}).")
        self.assertEqual(eta("P2 ~1-3h ", "", 5 * hour), "It is 3 h past its ~1-3h estimate.")
        self.assertEqual(eta("~1d ", "", 6 * hour), f"By its ~1d estimate about 18 h to go (ETA {at(18 * hour)}).")
        self.assertEqual(eta("", "xxx", 2 * hour), "All 3 sub-tasks ticked, but not done yet: its tests and review are still to finish (no Ready for review line).")
        ready = tile("", "xxx") + "\n  **Ready for review:** every tier green."
        self.assertEqual(pickup.eta_text(ready, now - hour, now), "All 3 sub-tasks ticked, and it is ready for review.")
        self.assertEqual(eta("", " x", None), "1 of 2 sub-tasks ticked. No ETA: when it started isn't known.")
        self.assertEqual(eta("", "", hour), "No ETA: no sub-tasks ticked and no estimate to go by.")
        self.assertEqual(eta("", "  ", hour), "0 of 2 sub-tasks ticked after 1 h. No ETA: no sub-tasks ticked and no estimate to go by.")

    def test_the_status_ends_with_what_is_on_the_session_s_screen(self):
        store = stores.Store(tempfile.mkdtemp())
        tile = "- [ ] PLN-01 **One.**\n  Claude session `ab12cd34`: `claude attach ab12cd34`."
        line = pickup.status(store, "PLN-01", tile, sessions=lambda: {"ab12cd34": "busy"}, why=lambda sid: None,
                             screen=lambda sid: "running the e2e tier", now=lambda: 1795000000)
        self.assertTrue(line.endswith('On its screen (rough): "running the e2e tier".'), line)
        gone = pickup.status(store, "PLN-01", tile, sessions=lambda: {}, why=lambda sid: None,
                             screen=lambda sid: "should not be asked", now=lambda: 1795000000)
        self.assertNotIn("On its screen", gone)

    def test_the_branch_facts_come_from_the_worktree(self):
        app = Path(tempfile.mkdtemp()).resolve()
        git(app, "init", "-q", "-b", "dev")
        (app / "a.txt").write_text("a")
        git(app, "add", "-A")
        git(app, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        git(app, "switch", "-qc", "pln-01-x")
        (app / "b.txt").write_text("b")
        git(app, "add", "-A")
        git(app, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "feat: b (PLN-01)")
        (app / "c.txt").write_text("c")
        facts = pickup.git_facts(app, "dev")
        self.assertEqual((facts["ahead"], facts["last"], facts["dirty"]), (1, "feat: b (PLN-01)", 1))
        self.assertIsNone(pickup.git_facts(app / "nowhere", "dev"))

    def test_the_status_line_sits_under_the_session_line_in_bold_and_is_replaced_each_time(self):
        store = stores.Store(tempfile.mkdtemp())
        session = "  Claude session `ab12cd34`: `claude attach ab12cd34`.\n"
        board = PLANS.replace("- [ ] PLN-70 **Dark mode.** The master.\n", "- [ ] PLN-70 **Dark mode.** The master.\n" + session)
        once = serve.with_status(store, board, "PLN-70", "Status 2026-11-18 14:58: session `ab12cd34` at work.")
        self.assertIn(session + "  **Status 2026-11-18 14:58:** session `ab12cd34` at work.\n  - [ ] **Agree the scope.**\n- [ ] PLN-75", once)
        old = once.replace("  **Status 2026-11-18 14:58:** session `ab12cd34` at work.\n", "").replace(
            "  - [ ] **Agree the scope.**\n", "  - [ ] **Agree the scope.**\n  **Status 2026-11-18 14:00:** as an earlier board left it, last.\n")
        twice = serve.with_status(store, old, "PLN-70", "Status 2026-11-18 15:10: session `ab12cd34` idle.")
        self.assertEqual(twice.count("**Status "), 1)
        self.assertIn(session + "  **Status 2026-11-18 15:10:** session `ab12cd34` idle.\n  - [ ] **Agree", twice)
        bare = serve.with_status(store, PLANS, "PLN-70", "Status 2026-11-18 14:58: x.")  # no session line: the tile's last line
        self.assertIn("  - [ ] **Agree the scope.**\n  **Status 2026-11-18 14:58:** x.\n- [ ] PLN-75", bare)
        self.assertEqual(serve.with_status(store, PLANS, "PLN-99", "Status 2026-11-18 14:58: x."), PLANS)
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write(twice)
        self.assertEqual(subprocess.run(["node", str(Path(__file__).resolve().parent / "kandalf" / "check.js"), f.name], capture_output=True).returncode, 0)
        Path(f.name).unlink()


class Worktree(unittest.TestCase):
    def test_prepare_cuts_a_worktree_from_the_trunk_and_links_the_store_in(self):
        top = Path(tempfile.mkdtemp()).resolve()
        app, plans = top / "app", top / "plans"
        (app / "docs").mkdir(parents=True)
        plans.mkdir()
        (plans / "board.json").write_text(json.dumps({"app": "../app", "trunk": "dev"}))
        (app / "docs" / "plans").symlink_to("../../plans")
        (app / ".gitignore").write_text("docs/plans\n")
        git(app, "init", "-q", "-b", "dev")
        git(app, "add", "-A")
        git(app, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        git(app, "switch", "-qc", "elsewhere")  # the app's own checkout is on some other branch
        st = stores.Store(plans)
        job = {"key": "PLN-07", "title": "Small thing"}
        path, branch = pickup.prepare(st, job)
        self.assertEqual((path, branch), (top / "app-pln-07", "pln-07-small-thing"))
        head = subprocess.run(["git", "-C", str(path), "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True)
        self.assertEqual(head.stdout.strip(), "pln-07-small-thing")
        self.assertEqual((path / "docs" / "plans").resolve(), plans)
        self.assertEqual(pickup.prepare(st, job), (path, branch))  # again: the same worktree, untouched


class Tidy(unittest.TestCase):
    """An archived story's worktree and branch go, when nothing would be lost."""

    def setUp(self):
        self.top = top = Path(tempfile.mkdtemp()).resolve()
        self.app, plans = top / "app", top / "plans"
        (self.app / "docs").mkdir(parents=True)
        plans.mkdir()
        (plans / "board.json").write_text(json.dumps({"app": "../app", "trunk": "dev"}))
        (self.app / "docs" / "plans").symlink_to("../../plans")
        (self.app / "README").write_text("app\n")
        git(self.app, "init", "-q", "-b", "dev")
        self.commit(self.app, "init")
        self.store = stores.Store(plans)
        self.path, self.branch = pickup.prepare(self.store, {"key": "PLN-07", "title": "Small thing"})
        (self.path / "feature").write_text("done\n")
        self.commit(self.path, "the work")
        self.tile = ("- [x] PLN-07 **Small thing.**\n  Claude session `ab12cd34`: `claude attach ab12cd34`. "
                     f"Branch `{self.branch}`, worktree `{self.path.name}`. Model `opus`. Started {DAY}.")

    def commit(self, repo, message):
        git(repo, "add", "-A")
        git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", message)

    def has_branch(self):
        return pickup._git(self.app, "rev-parse", "--verify", "--quiet", f"refs/heads/{self.branch}").returncode == 0

    def tidy(self, merged=True, sessions=lambda: {}, there=False):
        self.retired = []
        return pickup.tidy(self.store, self.tile, merged=lambda r, b: merged, sessions=sessions,
                           retire=lambda sid: self.retired.append(sid) or there)

    def test_a_landed_story_loses_its_worktree_and_its_branch(self):
        said = self.tidy()
        self.assertEqual(said[0], "removed worktree app-pln-07")
        self.assertRegex(said[1], r"^deleted branch pln-07-small-thing \(was [0-9a-f]{7,}\)$")
        self.assertFalse(self.path.exists())
        self.assertFalse(self.has_branch())
        self.assertEqual(self.tidy(), [])  # nothing left to do

    def test_its_session_is_closed_first_when_it_is_still_listed(self):
        said = self.tidy(sessions=lambda: {"ab12cd34": "idle"}, there=True)
        self.assertEqual(said[:2], ["closed session ab12cd34", "removed worktree app-pln-07"])
        self.assertEqual(self.retired, ["ab12cd34"])

    def test_a_session_is_closed_even_when_the_worktree_and_branch_are_gone(self):
        self.tidy()
        self.assertEqual(self.tidy(there=True), ["closed session ab12cd34"])

    def test_a_branch_that_is_not_in_the_trunk_keeps_both(self):
        self.assertEqual(self.tidy(merged=False), ["kept branch pln-07-small-thing and worktree app-pln-07 and session ab12cd34: the branch isn't in dev yet"])
        self.assertTrue(self.path.exists() and self.has_branch())
        self.assertEqual(self.retired, [])

    def test_uncommitted_work_keeps_both(self):
        (self.path / "scratch").write_text("not committed\n")
        self.assertEqual(self.tidy(), ["kept branch pln-07-small-thing and worktree app-pln-07 and session ab12cd34: 1 uncommitted file in the worktree"])
        self.assertTrue(self.path.exists() and self.has_branch())

    def test_a_session_still_at_work_keeps_both(self):
        self.assertEqual(self.tidy(sessions=lambda: {"ab12cd34": "busy"}),
                         ["kept branch pln-07-small-thing and worktree app-pln-07 and session ab12cd34: the session is still at work"])
        self.assertTrue(self.path.exists() and self.has_branch())

    def test_the_branch_goes_when_the_worktree_is_already_gone(self):
        git(self.app, "worktree", "remove", "--force", str(self.path))
        self.assertRegex(self.tidy()[0], "^deleted branch pln-07-small-thing")

    def test_a_tile_that_lost_its_session_line_goes_by_the_board_s_record_of_the_pickup(self):
        job = {"key": "PLN-07", "id": "ab12cd34", "branch": self.branch, "worktree": str(self.path)}
        said = pickup.tidy(self.store, "- [x] PLN-07 **Small thing.**\n  Merged.", merged=lambda r, b: True, job=job,
                           sessions=lambda: {}, retire=lambda sid: True)
        self.assertEqual(said[:2], ["closed session ab12cd34", "removed worktree app-pln-07"])
        self.assertFalse(self.path.exists() or self.has_branch())

    def test_a_deleted_story_loses_its_session_and_worktree_but_keeps_a_branch_with_commits(self):
        (self.path / "scratch").write_text("not committed\n")
        pickup.save(self.store, {"inflight": ["PLN-07"], "jobs": [{"key": "PLN-07", "id": "ab12cd34", "state": "started"}]})
        retired = []
        said = pickup.discard(self.store, "PLN-07", self.tile, retire=lambda sid: retired.append(sid) or True)
        self.assertEqual(said, ["closed session ab12cd34", "removed worktree app-pln-07",
                                "kept branch pln-07-small-thing: 1 commit not in dev"])
        self.assertEqual(retired, ["ab12cd34"])
        self.assertFalse(self.path.exists())
        self.assertTrue(self.has_branch())
        self.assertEqual([j["state"] for j in pickup.load(self.store)["jobs"]], ["ended"])

    def test_a_deleted_story_with_no_commits_loses_its_branch_too(self):
        git(self.path, "reset", "-q", "--hard", "HEAD~1")
        said = pickup.discard(self.store, "PLN-07", self.tile, retire=lambda sid: False)
        self.assertEqual(said, ["removed worktree app-pln-07", "deleted branch pln-07-small-thing"])
        self.assertFalse(self.has_branch())

    def test_a_deleted_tile_that_was_never_picked_up_touches_nothing(self):
        self.assertEqual(pickup.discard(self.store, "PLN-99", "- [ ] PLN-99 **Other.**"), [])
        self.assertTrue(self.path.exists() and self.has_branch())

    def test_a_story_archived_behind_the_server_s_back_is_tidied_on_the_next_look(self):
        from kandalf import serve
        pickup.save(self.store, {"inflight": None, "jobs": [
            {"key": "PLN-07", "id": "", "state": "ended", "branch": self.branch, "worktree": str(self.path)}]})
        git(self.app, "merge", "-q", "--ff-only", self.branch)
        plans = self.store.dir
        (plans / "plans.md").write_text("# Plans\n\n## Done\n\n- [x] PLN-07 **Small thing.**\n")
        (plans / "archive").mkdir()
        (plans / "archive" / "archived.md").write_text("# Archived plans\n\n## Archived\n")
        told = set()
        serve.tidy_stragglers(self.store, told)  # still on the board: left alone
        self.assertTrue(self.path.exists())
        (plans / "plans.md").write_text("# Plans\n\n## Done\n")
        (plans / "archive" / "archived.md").write_text("# Archived plans\n\n## Archived\n\n- [x] PLN-07 **Small thing.**\n")
        serve.tidy_stragglers(self.store, told)
        self.assertFalse(self.path.exists() or self.has_branch())
        self.assertIn(("tidied", "PLN-07"), told)

    def test_a_tile_without_a_session_line_is_left_alone(self):
        self.assertEqual(pickup.tidy(self.store, "- [x] PLN-08 **Other.** Branch `dev`.", merged=lambda r, b: True), [])

    def test_a_store_with_tidy_off_keeps_the_worktree_the_branch_and_the_session(self):
        (self.store.dir / "board.json").write_text(json.dumps({"app": "../app", "trunk": "dev", "tidy": False}))
        self.store = stores.Store(self.store.dir)
        self.assertEqual(self.tidy(sessions=lambda: {"ab12cd34": "idle"}, there=True), [])
        self.assertTrue(self.path.exists() and self.has_branch())
        self.assertEqual(self.retired, [])

    def test_a_done_story_on_a_default_named_branch_is_outlined_until_it_is_merged(self):
        top = Path(tempfile.mkdtemp()).resolve()
        app, plans = top / "app", top / "plans"
        app.mkdir()
        plans.mkdir()
        (plans / "board.json").write_text(json.dumps({"app": "../app", "trunk": "dev"}))
        (app / "README").write_text("app\n")
        git(app, "init", "-q", "-b", "dev")
        git(app, "add", "-A")
        git(app, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        st = stores.Store(plans)
        job = {"key": "PLN-07", "title": "Small thing"}
        path, name = pickup.prepare(st, job)
        self.assertEqual(name, "pln-07-small-thing")  # no slash in it
        (path / "feature").write_text("done\n")
        git(path, "add", "feature")
        git(path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "the work (PLN-07)")
        started = {**job, "id": "ab12cd34", "branch": name, "worktree": str(path), "model": "opus", "started": 1795000000}
        board = serve.with_session(st, "## Done\n\n- [x] PLN-07 **Small thing.**\n", started)
        self.assertEqual(serve.unmerged_done(st, board), {"PLN-07": ["pln-07-small-thing"]})
        git(app, "merge", "-q", "--ff-only", name)
        self.assertEqual(serve.unmerged_done(st, board), {})


class Naming(unittest.TestCase):
    """A store's own branch and worktree patterns, from the pickup to the tidy."""

    def test_a_patterned_branch_and_worktree_round_trip_from_the_pickup_to_the_tidy(self):
        top = Path(tempfile.mkdtemp()).resolve()
        app, plans = top / "app", top / "plans"
        (app / "docs").mkdir(parents=True)
        plans.mkdir()
        (plans / "board.json").write_text(json.dumps({"app": "../app", "trunk": "dev", "branch": "feat/{key}-{slug}", "worktree": "wt/{app}-{key}"}))
        (app / "docs" / "plans").symlink_to("../../plans")
        (app / "README").write_text("app\n")
        git(app, "init", "-q", "-b", "dev")
        git(app, "add", "-A")
        git(app, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        st = stores.Store(plans)
        job = {"key": "PLN-07", "title": "Small thing"}
        path, name = pickup.prepare(st, job)
        self.assertEqual((path, name), (top / "wt" / "app-pln-07", "feat/pln-07-small-thing"))
        (path / "feature").write_text("done\n")
        git(path, "add", "feature")
        git(path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "the work (PLN-07)")
        started = {**job, "id": "ab12cd34", "branch": name, "worktree": str(path), "model": "opus", "started": 1795000000}
        board = serve.with_session(st, "## Done\n\n- [x] PLN-07 **Small thing.**\n", started)
        self.assertIn("Branch `feat/pln-07-small-thing`, worktree `wt/app-pln-07`.", board)
        tile = board.split("\n\n", 1)[1]
        self.assertEqual(pickup.tile_worktree(st, tile), path)
        self.assertEqual(serve.unmerged_done(st, board), {"PLN-07": ["feat/pln-07-small-thing"]})  # Done, not merged: outlined
        line = pickup.status(st, "PLN-07", tile, sessions=lambda: {}, why=lambda sid: None, screen=lambda sid: "", now=time.time)
        self.assertIn("Branch 1 commit ahead of dev", line)  # found the worktree from the tile alone
        agents.use("copilot")
        self.addCleanup(agents.use, "claude")
        tile = serve.with_session(st, "## Done\n\n- [x] PLN-07 **Small thing.**\n", started).split("\n\n", 1)[1]
        self.assertIn("Copilot session `ab12cd34`: `kandalf attach ab12cd34`.", tile)
        self.assertEqual(pickup.tile_worktree(st, tile), path)
        line = pickup.status(st, "PLN-07", tile, sessions=lambda: {"ab12cd34": "idle"}, why=lambda sid: "error", screen=lambda sid: "", now=time.time)
        self.assertIn("session `ab12cd34` waiting for your input (it ended on an error).", line)
        self.assertEqual(pickup.prepare(st, {**job, "worktree": str(path)}), (path, name))  # a restart carries on in it
        git(app, "merge", "-q", "--ff-only", name)
        self.assertEqual(serve.unmerged_done(st, board), {})
        said = pickup.tidy(st, tile, merged=lambda r, b: b == name, sessions=lambda: {}, retire=lambda sid: True)
        self.assertEqual(said[:2], ["closed session ab12cd34", "removed worktree app-pln-07"])
        self.assertRegex(said[2], r"^deleted branch feat/pln-07-small-thing \(was [0-9a-f]{7,}\)$")
        self.assertFalse(path.exists())
        self.assertNotEqual(pickup._git(app, "rev-parse", "--verify", "--quiet", "refs/heads/" + name).returncode, 0)

    def test_a_restart_takes_over_in_the_worktree_its_job_has_even_after_the_pattern_changed(self):
        top = Path(tempfile.mkdtemp()).resolve()
        app, plans = top / "app", top / "plans"
        app.mkdir()
        plans.mkdir()
        (plans / "board.json").write_text(json.dumps({"app": "../app", "trunk": "dev"}))
        git(app, "init", "-q", "-b", "dev")
        git(app, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init")
        job = {"key": "PLN-07", "title": "Small thing"}
        path, name = pickup.prepare(stores.Store(plans), job)
        (plans / "board.json").write_text(json.dumps({"app": "../app", "trunk": "dev", "worktree": "wt/{key}"}))
        self.assertEqual(pickup.prepare(stores.Store(plans), {**job, "worktree": str(path)}), (path, name))


# What `claude --help` and `claude agents --help` print (Claude Code 2.1), cut down.
HELP = """Usage: claude [options] [command] [prompt]

Options:
  --bg, --background                    Start the session in the background and
                                        return immediately. Prints the id that
                                        `claude attach`, `logs`, `stop` and `rm`
                                        take
  --permission-mode <mode>              Permission mode to use for the session

Commands:
  agents [options]                      Manage background agents
  attach <id|name>                      Open a background session in this
                                        terminal.
  logs <id|name>                        Print a background session's recent
                                        terminal output
  rm <id>                               If it's safe, remove a background
                                        session from the list
  stop|kill <id>                        Stop a background session.
"""
AGENTS_HELP = """Usage: claude agents [options]

Options:
  --all                                 With --json: also include completed
  --json                                Print active sessions as a JSON array
"""


def helps(top=HELP, agents=AGENTS_HELP):
    """Stands in for `claude [sub] --help`: the help text for those args."""
    return lambda *args: agents if args == ("agents",) else top


class Settings(unittest.TestCase):
    """What board.json lets pickup, tidy and the commit queue do on your machine."""

    def store(self, **cfg):
        folder = Path(tempfile.mkdtemp())
        if cfg:
            (folder / "board.json").write_text(json.dumps(cfg))
        return stores.Store(folder)

    def test_a_store_that_says_nothing_has_pickup_off_and_tidies_and_pushes_in_auto_mode(self):
        st = self.store()
        self.assertEqual((st.pickup, st.permission, st.tidy, st.push), (False, "auto", True, True))

    def test_each_setting_is_read_from_board_json(self):
        st = self.store(pickup=True, permission="acceptEdits", tidy=False, push=False)
        self.assertEqual((st.pickup, st.permission, st.tidy, st.push), (True, "acceptEdits", False, False))

    def test_sessions_start_in_the_store_s_permission_mode(self):
        failed = subprocess.CompletedProcess([], 1, "", "no")
        with mock.patch("kandalf.claude_code.subprocess.run", return_value=failed) as run:
            pickup._launch(self.store(permission="plan"), {"key": "PLN-01"}, ["-n", "PLN-01 One"], ".", "go", {})
        self.assertEqual(run.call_args[0][0], ["claude", "--bg", "-n", "PLN-01 One", "--permission-mode", "plan", "go"])

    def test_sessions_load_the_store_s_hooks_when_it_has_them(self):
        st = self.store()
        st.hooks_file.parent.mkdir()
        st.hooks_file.write_text("{}")
        failed = subprocess.CompletedProcess([], 1, "", "no")
        with mock.patch("kandalf.claude_code.subprocess.run", return_value=failed) as run:
            pickup._launch(st, {"key": "PLN-01"}, ["-n", "PLN-01 One"], ".", "go", {})
        self.assertEqual(run.call_args[0][0][:4], ["claude", "--bg", "--settings", str(st.hooks_file)])


    def test_the_commit_delay_is_the_store_s_unless_the_environment_says_otherwise(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("BOARD_COMMIT_DELAY", None)
            self.assertEqual(self.store().commit_delay(), 300)
            self.assertEqual(self.store(commitDelay=60).commit_delay(), 60)
            os.environ["BOARD_COMMIT_DELAY"] = "5"
            self.assertEqual(self.store(commitDelay=60).commit_delay(), 5)
            self.assertEqual(self.store().commit_delay(), 5)

    def test_commit_status_counts_down_from_the_store_s_delay(self):
        folder = Path(tempfile.mkdtemp()).resolve()
        git(folder, "init", "-q")
        (folder / "board.json").write_text(json.dumps({"commitDelay": 120}))
        st = stores.Store(folder)
        with gitlog.queue(st.root) as q:
            q.update(paths=["plans.md"], steps=["12:00:00 a change"], last=time.time())
        out = io.StringIO()
        with mock.patch.dict(os.environ), contextlib.redirect_stdout(out):
            os.environ.pop("BOARD_COMMIT_DELAY", None)
            gitlog.main(st, ["status"])
        self.assertRegex(out.getvalue(), r"^1 paths queued, committing in (1m59s|2m00s) ")

    def test_a_started_session_s_id_is_read_from_what_the_cli_prints(self):
        said = subprocess.CompletedProcess([], 0, "Session backgrounded as ab12cd34 (attach with ...)\n", "")
        with mock.patch("kandalf.claude_code.subprocess.run", return_value=said):
            self.assertEqual(agent.start(["-n", "x"], ".", "auto", "go"), {"id": "ab12cd34"})
        listed = subprocess.CompletedProcess([], 0, json.dumps([{"id": "ab12cd34", "sessionId": "full-1"}, {"id": "ab12cd34", "sessionId": "full-2"}, {"kind": "x"}]), "")
        with mock.patch("kandalf.claude_code.subprocess.run", return_value=listed):
            self.assertEqual(agent.known(), {"ab12cd34": "full-1"})
            self.assertEqual(pickup.full_id("ab12cd34"), "full-1")
        with mock.patch("kandalf.claude_code.subprocess.run", side_effect=OSError("no claude")):
            self.assertEqual(agent.start([], ".", "auto", "go"), {"error": "no claude"})
            self.assertIsNone(agent.sessions())
            self.assertEqual((pickup.session_screen("ab12cd34"), pickup.idle_reason("ab12cd34")), ("", None))

    def test_only_the_seam_runs_the_claude_cli(self):
        package = Path(__file__).resolve().parent / "kandalf"
        runs = re.compile(r"""\[\s*["']claude["']|which\(\s*["']claude["']""")
        found = [f.name for f in package.glob("*.py") if f.name != "claude_code.py" and runs.search(f.read_text(encoding="utf-8"))]
        self.assertEqual(found, [])


class Cli(unittest.TestCase):
    """Pickup runs only when it can: switched on, with an app, and a claude that has what it needs."""

    def setUp(self):
        self.top = Path(tempfile.mkdtemp()).resolve()
        (self.top / "app").mkdir()
        (self.top / "plans").mkdir()
        self.write(pickup=True, app="../app")

    def write(self, **cfg):
        (self.top / "plans" / "board.json").write_text(json.dumps(cfg))
        self.store = stores.Store(self.top / "plans")

    def why(self, which="/usr/local/bin/claude", lacking=()):
        return pickup.why_off(self.store, installed=lambda: bool(which), lacks=lambda: list(lacking))

    def test_the_installed_claude_has_all_pickup_needs(self):
        self.assertEqual(agent.cli_lacks(helps()), [])

    def test_a_claude_without_a_command_or_flag_pickup_needs_says_which(self):
        old = HELP.replace("  --bg, --background", "  --verbose").replace("  rm <id>", "  remove <id>")
        self.assertEqual(agent.cli_lacks(helps(top=old, agents="Usage: claude agents\n")),
                         ["claude --bg", "claude agents --json", "claude rm"])
        self.assertEqual(agent.cli_lacks(helps(top=HELP.replace("  agents [options]", "  other"))),
                         ["claude agents --json"])

    def test_a_claude_whose_help_cannot_be_read_lacks_everything(self):
        self.assertEqual(len(agent.cli_lacks(lambda *args: "")), 5)

    def test_pickup_runs_when_nothing_is_in_its_way(self):
        self.assertIsNone(self.why())

    def test_each_reason_pickup_is_off_says_how_to_fix_it(self):
        self.assertRegex(self.why(which=None), r"`claude` isn't on your PATH")
        self.assertRegex(self.why(lacking=["claude logs", "claude rm"]), r"lacks claude logs and claude rm.*[Uu]pdate it")
        self.write(pickup=True, app="../nowhere")
        self.assertIn(str(self.top / "nowhere"), self.why())
        self.write(pickup=True)
        self.assertRegex(self.why(), r'no "app"')
        self.write(app="../app")
        self.assertRegex(self.why(), r'"pickup": true')

    def test_a_store_with_pickup_off_never_asks_the_cli(self):
        self.write(app="../app")
        asked = []
        pickup.why_off(self.store, installed=lambda: asked.append("which"), lacks=lambda: asked.append("help") or [])
        self.assertEqual(asked, [])
        self.assertFalse(pickup.enabled(self.store))

    def test_the_page_is_told_why_pickup_is_off(self):
        self.write(app="../app")
        said = pickup.summary(self.store)
        self.assertFalse(said["enabled"])
        self.assertRegex(said["why"], r'"pickup": true')


class Transcript(unittest.TestCase):
    """A session's log as the board shows it: what was said, the tools run, their results."""

    def setUp(self):
        self.projects = Path(tempfile.mkdtemp())
        patch = mock.patch.object(agent, "PROJECTS", self.projects)
        patch.start()
        self.addCleanup(patch.stop)

    def write(self, session, records):
        folder = self.projects / "-x-app-pln-01"
        folder.mkdir(exist_ok=True)
        (folder / f"{session}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))

    def test_a_claude_transcript_reads_as_what_was_said_the_tools_run_and_their_results(self):
        said = lambda kind, *blocks: {"type": kind, "message": {"content": list(blocks)}}
        self.write("aaaa-1111", [
            {"type": "permission-mode", "mode": "auto"},
            {"type": "user", "message": {"content": "You are picking up PLN-01."}},
            said("assistant", {"type": "thinking", "thinking": "hmm", "signature": "x"}, {"type": "text", "text": "Reading the tile first."}),
            said("assistant", {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "go test ./internal/...", "description": "tests"}}),
            said("user", {"type": "tool_result", "tool_use_id": "t1", "content": "ok  internal/service  4.2s\nok  internal/models  1.1s"}),
            said("assistant", {"type": "tool_use", "id": "t2", "name": "Edit", "input": {"file_path": "/x/app/main.go", "old_string": "a", "new_string": "b"}}),
            said("user", {"type": "tool_result", "tool_use_id": "t2", "is_error": True, "content": [{"type": "text", "text": "File has not been read yet."}]}),
        ])
        self.assertEqual(agent.transcript("aaaa-1111").splitlines(), [
            "You: You are picking up PLN-01.",
            "Claude: Reading the tile first.",
            "● Bash  go test ./internal/...",
            "  ⎿ ok  internal/service  4.2s",
            "    ok  internal/models  1.1s",
            "● Edit  /x/app/main.go",
            "  ✗ File has not been read yet.",
        ])
        self.assertIsNone(agent.transcript("no-such-session"))

    def test_the_log_comes_from_the_agent_that_ran_the_session_and_keeps_its_last_lines(self):
        self.write("bbbb-2222", [{"type": "assistant", "message": {"content": [{"type": "text", "text": f"line {n}"}]}} for n in range(10)])
        with mock.patch.object(copilot_cli, "has", lambda sid: False):
            self.assertEqual(agents.transcript("bbbb", "bbbb-2222", limit=3).splitlines(), ["Claude: line 7", "Claude: line 8", "Claude: line 9"])
        with mock.patch.object(copilot_cli, "has", lambda sid: sid == "cccc"), mock.patch.object(copilot_cli, "logs", lambda sid: "● bash  ls\nCopilot said hi"):
            self.assertEqual(agents.transcript("cccc", "cccc-full"), "● bash  ls\nCopilot said hi")

    def test_the_board_serves_a_tile_s_log_by_its_key(self):
        store = stores.Store(tempfile.mkdtemp())
        pickup.save(store, {"inflight": [], "jobs": [{"key": "PLN-01", "title": "One", "state": "started", "id": "bbbb", "session": "bbbb-2222"}]})
        self.write("bbbb-2222", [{"type": "assistant", "message": {"content": [{"type": "text", "text": "at it"}]}}])
        with mock.patch.object(copilot_cli, "has", lambda sid: False):
            view = serve.log_view(store, "PLN-01")
        self.assertEqual((view["id"], view["state"], view["text"]), ("bbbb", "started", "Claude: at it"))
        self.assertIsNone(serve.log_view(store, "PLN-09"))


if __name__ == "__main__":
    unittest.main()
