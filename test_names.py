#!/usr/bin/env python3
"""Which names a store's two boards go by (store.Store.build and .plan), and that every module
asks the store for them: a store on build.md and plan.md works as one on plans.md and backlog.md.

    python3 -m unittest test_names
"""

import io
import json
import subprocess
import tempfile
import threading
import unittest
import http.client
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from kandalf import checkhook, pln, serve
from kandalf import store as stores
from kandalf import tiles as reader

BUILD = """# Build

## To do

- [ ] PLN-70 **Family.**
  The master.
- [ ] PLN-71 **First kid.** Part of PLN-70.

## In flight

- [ ] PLN-80 **Running.**
  Some words.

## Done
"""

PLAN = """# Plan

## Feature

- [ ] PLN-60 **Later.**
  Some day.

## Design decision

- [ ] PLN-30 **Auth.**
  Which way.
"""

ARCHIVE = """# Archive

## Archived

- [x] PLN-95 **Old thing.**
  Archived 2026-11-01.
"""


def folder(*names) -> Path:
    """A temp folder holding a file for each name (a board's text where it has one)."""
    d = Path(tempfile.mkdtemp()).resolve()
    texts = {"build.md": BUILD, "plans.md": BUILD, "plan.md": PLAN, "backlog.md": PLAN, "archive/archived.md": ARCHIVE}
    for n in names:
        (d / n).parent.mkdir(parents=True, exist_ok=True)
        (d / n).write_text(texts.get(n, "{}"), encoding="utf-8")
    return d


def names_of(st) -> dict:
    return {"build": st.build, "plan": st.plan, "on_old_names": st.on_old_names,
            "half_moved": st.half_moved, "strays": st.strays}


class Names(unittest.TestCase):
    def test_a_store_with_build_md_is_on_the_new_names(self):
        st = stores.Store(folder("build.md", "plan.md", "board.json"))
        self.assertEqual(names_of(st), {"build": "build.md", "plan": "plan.md", "on_old_names": False,
                                        "half_moved": False, "strays": []})
        self.assertEqual(st.boards, ("build.md", "plan.md"))
        self.assertEqual(st.family, ("build.md", "plan.md", "archive/archived.md"))
        self.assertTrue(st.is_store)

    def test_a_store_with_plans_md_is_on_the_old_names(self):
        st = stores.Store(folder("plans.md", "backlog.md"))
        self.assertEqual(names_of(st), {"build": "plans.md", "plan": "backlog.md", "on_old_names": True,
                                        "half_moved": False, "strays": []})
        self.assertEqual(st.family, ("plans.md", "backlog.md", "archive/archived.md"))
        self.assertFalse(st.is_store)

    def test_an_empty_folder_is_on_the_new_names(self):
        st = stores.Store(folder())
        self.assertEqual(names_of(st), {"build": "build.md", "plan": "plan.md", "on_old_names": False,
                                        "half_moved": False, "strays": []})

    def test_a_half_moved_store_still_reads_its_backlog(self):
        st = stores.Store(folder("build.md", "backlog.md"))
        self.assertEqual(names_of(st), {"build": "build.md", "plan": "backlog.md", "on_old_names": False,
                                        "half_moved": True, "strays": []})

    def test_an_old_store_with_its_own_plan_md_stays_on_the_old_names(self):
        st = stores.Store(folder("plans.md", "backlog.md", "plan.md"))
        self.assertEqual((st.build, st.plan, st.on_old_names), ("plans.md", "backlog.md", True))

    def test_the_names_are_read_again_after_the_files_change(self):
        d = folder("plans.md", "backlog.md")
        st = stores.Store(d)
        self.assertEqual(st.boards, ("plans.md", "backlog.md"))
        (d / "plans.md").rename(d / "build.md")
        self.assertEqual(st.boards, ("build.md", "backlog.md"))
        self.assertTrue(st.half_moved)
        (d / "backlog.md").rename(d / "plan.md")
        self.assertEqual(st.boards, ("build.md", "plan.md"))
        self.assertFalse(st.half_moved)

    def test_a_plans_md_beside_build_md_is_a_stray(self):
        st = stores.Store(folder("build.md", "plan.md", "plans.md"))
        self.assertEqual((st.build, st.strays), ("build.md", ["plans.md"]))

    def test_a_backlog_md_beside_plan_md_is_a_stray(self):
        st = stores.Store(folder("build.md", "plan.md", "backlog.md"))
        self.assertEqual((st.plan, st.strays), ("plan.md", ["backlog.md"]))
        both = stores.Store(folder("build.md", "plan.md", "plans.md", "backlog.md"))
        self.assertEqual(both.strays, ["plans.md", "backlog.md"])

    def test_an_old_store_has_no_strays(self):
        self.assertEqual(stores.Store(folder("plans.md", "backlog.md", "plan.md")).strays, [])

    def test_a_name_a_caller_sends_is_mapped_onto_the_store_s_own(self):
        new = stores.Store(folder("build.md", "plan.md"))
        old = stores.Store(folder("plans.md", "backlog.md"))
        for name, want_new, want_old in (("plans.md", "build.md", "plans.md"), ("build.md", "build.md", "plans.md"),
                                         ("backlog.md", "plan.md", "backlog.md"), ("plan.md", "plan.md", "backlog.md"),
                                         ("archive/archived.md", "archive/archived.md", "archive/archived.md"),
                                         ("notes.md", "notes.md", "notes.md")):
            with self.subTest(name):
                self.assertEqual((new.board(name), old.board(name)), (want_new, want_old))

    def test_an_old_store_s_own_plan_md_is_that_file_not_the_plan_board(self):
        st = stores.Store(folder("plans.md", "backlog.md", "plan.md"))
        self.assertEqual(st.board("plan.md"), "plan.md")
        self.assertEqual(st.board("backlog.md"), "backlog.md")
        self.assertEqual(stores.Store(folder("plans.md", "backlog.md")).board("plan.md"), "backlog.md")

    def test_an_old_name_on_a_new_store_is_the_present_board_stray_or_not(self):
        self.assertEqual(stores.Store(folder("build.md", "plan.md")).board("plans.md"), "build.md")
        self.assertEqual(stores.Store(folder("build.md", "plan.md", "plans.md")).board("plans.md"), "build.md")
        self.assertEqual(stores.Store(folder("build.md", "plan.md", "backlog.md")).board("backlog.md"), "plan.md")

    def test_the_rule_is_one_function_of_the_files_there(self):
        self.assertEqual(stores.names(set()), ("build.md", "plan.md"))
        self.assertEqual(stores.names({"plans.md"}), ("plans.md", "backlog.md"))
        self.assertEqual(stores.names({"build.md", "backlog.md"}), ("build.md", "backlog.md"))
        self.assertEqual(stores.names({"build.md", "plan.md", "backlog.md"}), ("build.md", "plan.md"))

    def test_a_snapshot_of_plans_md_on_an_empty_store_reads_it_as_the_build_board(self):
        st = stores.Store(folder())
        snap = reader.Snapshot(st, {"plans.md": BUILD})
        self.assertEqual((snap.build, snap.plan), ("plans.md", "backlog.md"))
        self.assertEqual([t["key"] for t in snap.in_flight()], ["PLN-80"])

    def test_a_snapshot_of_build_md_and_backlog_md_reads_backlog_md_as_the_plan_board(self):
        st = stores.Store(folder())
        snap = reader.Snapshot(st, {"build.md": BUILD, "backlog.md": PLAN})
        self.assertEqual((snap.build, snap.plan), ("build.md", "backlog.md"))
        self.assertEqual([t["key"] for t in snap.in_flight()], ["PLN-80"])

    def test_a_snapshot_given_no_build_board_takes_the_store_s_names(self):
        st = stores.Store(folder("plans.md", "backlog.md"))
        snap = reader.Snapshot(st, {"backlog.md": PLAN})
        self.assertEqual((snap.build, snap.plan), ("plans.md", "backlog.md"))


class NewNames(unittest.TestCase):
    """What the old-name tests prove for plans.md and backlog.md, on a store with build.md and plan.md."""

    def setUp(self):
        self.dir = folder("build.md", "plan.md", "archive/archived.md")
        (self.dir / "board.json").write_text(json.dumps({"name": "Shop"}), encoding="utf-8")
        self.store = stores.Store(self.dir)
        patch = mock.patch.object(serve.gitlog, "record")
        self.record = patch.start()
        self.addCleanup(patch.stop)

    def text(self, name):
        return (self.dir / name).read_text(encoding="utf-8")

    def ask(self, method, path, body=None):
        """A request to a board server on this store, as the page sends it: (status, reply)."""
        srv = serve.ThreadingHTTPServer(("127.0.0.1", 0), type("Handler", (serve.Handler,), {"store": self.store}))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
            data = json.dumps(body).encode() if body is not None else None
            c.request(method, path, body=data, headers={"Content-Type": "application/json"} if data else {})
            r = c.getresponse()
            out = (r.status, json.loads(r.read()))
            c.close()
            return out
        finally:
            srv.shutdown()
            srv.server_close()

    def without(self, name, key):
        """(the board without the tile, the tile's markdown), as the page sends a dragged tile."""
        text = self.text(name)
        tile = next(t for t in reader.parse(self.store, text) if t.key == key)
        rest, _ = reader.take(self.store, text, [key])
        return rest, tile.text

    def test_the_snapshot_reads_the_family(self):
        snap = reader.Snapshot.read(self.store)
        self.assertEqual(list(snap.files), ["build.md", "plan.md", "archive/archived.md"])
        index = snap.index()
        self.assertEqual({k: index[k]["file"] for k in ("PLN-70", "PLN-60", "PLN-95")},
                         {"PLN-70": "build.md", "PLN-60": "plan.md", "PLN-95": "archive/archived.md"})

    def test_the_in_flight_tiles_are_the_build_board_s(self):
        self.assertEqual([t["key"] for t in reader.Snapshot.read(self.store).in_flight()], ["PLN-80"])

    def test_pickup_starts_a_tile_that_lands_in_flight_on_build_md(self):
        (self.dir / "board.json").write_text(json.dumps({"pickup": True}), encoding="utf-8")
        self.store = stores.Store(self.dir)
        ticks = []
        ctx = {"seen": None, "last": 0.0, "told": set(), "before": None, "was_on": True, "mtime": self.store.config_mtime}
        with mock.patch.object(serve.pickup, "why_off", lambda store, **kw: None), \
                mock.patch.object(serve.pickup, "tick", lambda store, tiles, **kw: ticks.append([t["key"] for t in tiles])):
            for _ in range(2):  # the first look waits for the boards to settle
                serve.watch_turn(self.store, ctx)
        self.assertEqual(ticks, [["PLN-80"]])
        serve.note_session(self.store, {"key": "PLN-80", "id": "ab12cd34", "started": 1_700_000_000})
        self.assertIn("session `ab12cd34`", self.text("build.md"))
        self.assertEqual(self.record.call_args.args[1], [self.dir / "build.md"])

    def test_promote_moves_a_tile_from_plan_md_to_the_top_of_to_do(self):
        status, board = self.ask("GET", "/api/board?file=plan.md")
        self.assertEqual(board["moveTo"], {"file": "build.md", "columns": ["To do"]})
        rest, tile = self.without("plan.md", "PLN-60")
        status, reply = self.ask("POST", "/api/move?file=plan.md", {"mtime": serve.mtime(self.dir / "plan.md"),
                                                                    "text": rest, "tile": tile, "column": "To do"})
        self.assertEqual(status, 200, reply)
        self.assertNotIn("PLN-60", self.text("plan.md"))
        self.assertTrue(self.text("build.md").split("## To do\n\n", 1)[1].startswith("- [ ] PLN-60 **Later.**"))
        rest, tile = self.without("build.md", "PLN-80")
        status, reply = self.ask("POST", "/api/move?file=build.md", {"mtime": serve.mtime(self.dir / "build.md"),
                                                                     "text": rest, "tile": tile, "column": "Design decision"})
        self.assertEqual(status, 200, reply)
        self.assertEqual(reply["moveTo"]["file"], "plan.md")
        self.assertTrue(self.text("plan.md").split("## Design decision\n\n", 1)[1].startswith("- [ ] PLN-80 **Running.**"))

    def test_archive_and_decide_work_on_a_tile_of_either_board(self):
        rest, tile = self.without("build.md", "PLN-80")
        status, reply = self.ask("POST", "/api/archive?file=build.md", {"mtime": serve.mtime(self.dir / "build.md"),
                                                                        "text": rest, "tile": tile, "docs": []})
        self.assertEqual(status, 200, reply)
        self.assertIn("PLN-60", reply["tiles"])  # the family's snapshot
        self.assertEqual(reply["tiles"]["PLN-80"]["file"], "archive/archived.md")
        rest, tile = self.without("plan.md", "PLN-30")
        status, reply = self.ask("POST", "/api/decide?file=plan.md", {"mtime": serve.mtime(self.dir / "plan.md"),
                                                                      "text": rest, "tile": tile, "docs": [], "answer": "use OAuth."})
        self.assertEqual(status, 200, reply)
        self.assertIn("PLN-70", reply["tiles"])
        self.assertNotIn("PLN-30", self.text("plan.md"))
        self.assertIn("use OAuth.", self.text("archive/archived.md"))

    def test_filing_a_domain_and_writing_options_find_tiles_on_both_boards(self):
        status, reply = serve.set_domains(self.store, {"PLN-71": "Billing", "PLN-60": "Reports"})
        self.assertEqual(status, 200, reply)
        self.assertIn("Domain: Billing", self.text("build.md").split("PLN-70", 1)[1])  # PLN-71's master
        self.assertIn("Domain: Reports", self.text("plan.md"))
        serve.write_options(self.store, "PLN-30", reader.options_block("2026-11-24", []), "board: PLN-30 options drafted")
        self.assertIn("**Options 2026-11-24:**", self.text("plan.md"))
        with mock.patch.object(serve.drafts, "why_not", lambda store: "Draft options is off: test."):
            self.assertEqual(serve.draft_request(self.store, self.dir / "plan.md", {"key": "PLN-30"}),
                             (409, {"error": "Draft options is off: test."}))

    def test_the_next_key_counts_both_boards_and_the_archive(self):
        self.assertEqual(pln.keyed_files(self.store), [self.dir / "build.md", self.dir / "plan.md", self.dir / "archive" / "archived.md"])
        self.assertEqual(pln.next_key(self.store), "PLN-96")
        (self.dir / "plan.md").write_text(PLAN.replace("PLN-60", "PLN-99"), encoding="utf-8")
        self.assertEqual(pln.next_key(self.store), "PLN-100")

    def test_the_edit_hook_checks_the_new_names(self):
        ran, real = [], subprocess.run

        def run(cmd, **kw):  # node fails every check; anything else (git) runs
            if cmd[0] != "/x/node":
                return real(cmd, **kw)
            ran.append(Path(cmd[-1]).relative_to(self.dir).as_posix())
            return mock.Mock(returncode=1, stderr="not round-tripping\n")

        def hook(name):
            stdin = io.StringIO(json.dumps({"tool_input": {"file_path": str(self.dir / name)}}))
            with redirect_stderr(io.StringIO()):
                return checkhook.main(stdin=stdin, stdout=io.StringIO())

        (self.dir / "notes.md").write_text("# Notes\n", encoding="utf-8")
        with mock.patch.object(checkhook.shutil, "which", lambda n: "/x/node"), \
                mock.patch.object(checkhook.subprocess, "run", run):
            got = {n: hook(n) for n in ("build.md", "plan.md", "archive/archived.md", "notes.md")}
        self.assertEqual(got, {"build.md": 2, "plan.md": 2, "archive/archived.md": 2, "notes.md": 0})
        self.assertEqual(ran, ["build.md", "plan.md", "archive/archived.md"])

    def test_a_request_for_the_old_file_name_is_answered_for_the_new_board(self):
        status, reply = self.ask("GET", "/api/board?file=plans.md")
        self.assertEqual((status, reply["text"]), (200, BUILD))
        status, reply = self.ask("GET", "/api/board?file=backlog.md")
        self.assertEqual((status, reply["text"]), (200, PLAN))
        self.assertEqual(reply["moveTo"], {"file": "build.md", "columns": ["To do"]})

    def test_the_files_reply_says_which_file_is_which(self):
        status, reply = self.ask("GET", "/api/files")
        self.assertEqual(status, 200)
        self.assertEqual(reply["boards"], {"build": "build.md", "plan": "plan.md"})
        self.assertEqual(reply["build"], serve.BUILD)  # the version stamp, as before
        self.store = stores.Store(folder("plans.md", "backlog.md"))
        status, reply = self.ask("GET", "/api/files")
        self.assertEqual(reply["boards"], {"build": "plans.md", "plan": "backlog.md"})




class OldStoreWithOwnPlanMd(unittest.TestCase):
    def test_a_request_for_plan_md_opens_the_person_s_own_file(self):
        d = folder("plans.md", "backlog.md", "archive/archived.md")
        (d / "plan.md").write_text("# My own notes\n", encoding="utf-8")
        store = stores.Store(d)
        srv = serve.ThreadingHTTPServer(("127.0.0.1", 0), type("Handler", (serve.Handler,), {"store": store}))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
            c.request("GET", "/api/board?file=plan.md")
            r = c.getresponse()
            body = json.loads(r.read())
            c.close()
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual(r.status, 200)
        self.assertEqual(body["text"], "# My own notes\n")


if __name__ == "__main__":
    unittest.main()
