#!/usr/bin/env python3
"""Clear (serve.py): archive every tile in the Done column at once, and undo it. Also what
the server answers (only its own page), and when the commit queue pushes (gitlog.py).

    python3 -m unittest test_clear
"""

import http.client
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from kandalf import gitlog
from kandalf import serve
from kandalf import store as stores
from kandalf import tiles as reader

HERE = Path(__file__).resolve().parent

PLANS = """# Plans

## To do

- [ ] PLN-10 **Still to do.** See [the plan](active/2026-01-01-big.md).

## Done

- [x] PLN-01 **First.** Plan: [big](active/2026-01-01-big.md).
  - [x] **A step.**
- [x] PLN-02 **Second.**
  Words, and plans/active/2026-01-02-small.md by its repo path.
- [x] An unkeyed one. [small](active/2026-01-02-small.md)
"""


def git(path, *args):
    subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True, capture_output=True)


def round_trips(path: Path) -> bool:
    return subprocess.run(["node", str(HERE / "kandalf" / "check.js"), str(path)], capture_output=True).returncode == 0


class Clear(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp()).resolve()
        (self.dir / "active").mkdir()
        (self.dir / "plans.md").write_text(PLANS)
        (self.dir / "active" / "2026-01-01-big.md").write_text("# PLN-01: Big\n")
        (self.dir / "active" / "2026-01-02-small.md").write_text("# PLN-02: Small\n\nSee plans/active/2026-01-01-big.md.\n")
        git(self.dir, "init", "-q")
        git(self.dir, "add", "-A")
        git(self.dir, "commit", "-qm", "init")
        self.store = stores.Store(self.dir)
        self.board = self.dir / "plans.md"

    def clear(self, text=PLANS, keep=()):
        new, record, _ = serve.clear_column(self.store, self.board, text, "Done", keep)
        self.board.write_text(new)
        return new, record

    def keys(self, text, column):
        return [t.key or t.text[:16] for t in reader.parse(self.store, text) if t.column == column]

    def test_clear_archives_every_tile_in_the_column_with_its_docs(self):
        new, record = self.clear()
        self.assertEqual(self.keys(new, "Done"), [])
        self.assertEqual(self.keys(new, "To do"), ["PLN-10"])
        self.assertIn("[the plan](archive/2026-01-01-big.md)", new)  # a tile that stays follows the doc
        log = (self.dir / "archive" / "archived.md").read_text()
        self.assertEqual(self.keys(log, "Archived"), ["PLN-01", "PLN-02", "- [x] An unkeyed"])  # the column's order
        self.assertIn("[big](2026-01-01-big.md)", log)
        self.assertTrue((self.dir / "archive" / "2026-01-01-big.md").is_file())
        self.assertFalse((self.dir / "active" / "2026-01-02-small.md").exists())
        self.assertEqual(record["count"], 3)
        self.assertTrue(round_trips(self.board) and round_trips(self.dir / "archive" / "archived.md"))

    def test_a_tile_to_keep_stays_in_the_column_with_its_docs(self):
        new, record = self.clear(keep=["PLN-01"])
        self.assertEqual(self.keys(new, "Done"), ["PLN-01"])
        self.assertTrue((self.dir / "active" / "2026-01-01-big.md").is_file())
        self.assertEqual(record["count"], 2)

    def test_an_empty_column_clears_nothing(self):
        text = PLANS.split("## Done")[0] + "## Done\n"
        with self.assertRaises(ValueError):
            serve.clear_column(self.store, self.board, text, "Done", ())
        with self.assertRaises(KeyError):
            serve.clear_column(self.store, self.board, PLANS, "Shipped", ())

    def test_undo_brings_the_tiles_and_their_docs_back_as_they_were(self):
        new, record = self.clear()
        back, _ = serve.undo_clear(self.store, self.board, new, record)
        self.assertEqual(back, PLANS)
        self.assertTrue((self.dir / "active" / "2026-01-01-big.md").is_file())
        self.assertFalse((self.dir / "archive" / "2026-01-01-big.md").exists())
        self.assertEqual((self.dir / "active" / "2026-01-02-small.md").read_text(), "# PLN-02: Small\n\nSee plans/active/2026-01-01-big.md.\n")
        self.assertEqual(self.keys((self.dir / "archive" / "archived.md").read_text(), "Archived"), [])
        self.assertTrue(round_trips(self.dir / "archive" / "archived.md"))

    def test_undo_keeps_what_changed_on_the_board_since(self):
        new, record = self.clear()
        later = new.replace("## Done\n", "## Done\n\n- [x] PLN-20 **Finished after the clear.**\n").replace("Still to do", "Renamed")
        back, _ = serve.undo_clear(self.store, self.board, later, record)
        self.assertEqual(self.keys(back, "Done"), ["PLN-01", "PLN-02", "- [x] An unkeyed", "PLN-20"])
        self.assertIn("**Renamed.** See [the plan](active/2026-01-01-big.md).", back)

    def test_undo_leaves_other_archived_tiles_in_the_archive(self):
        log = self.dir / "archive" / "archived.md"
        log.parent.mkdir()
        log.write_text(serve.ARCHIVE_HEAD + "\n- [x] PLN-00 **Archived long ago.**\n  Archived 2026-01-01.\n")
        new, record = self.clear()
        serve.undo_clear(self.store, self.board, new, record)
        self.assertEqual(self.keys(log.read_text(), "Archived"), ["PLN-00"])

    def test_undo_refuses_when_a_cleared_tile_is_no_longer_in_the_archive(self):
        new, record = self.clear()
        log = self.dir / "archive" / "archived.md"
        log.write_text(serve.ARCHIVE_HEAD)
        with self.assertRaises(LookupError):
            serve.undo_clear(self.store, self.board, new, record)
        self.assertTrue((self.dir / "archive" / "2026-01-01-big.md").is_file())  # nothing half done


class Requests(unittest.TestCase):
    """The server answers only its own page: another site open in the browser can't use it."""

    def test_a_request_to_the_board_s_own_address_is_answered(self):
        for host in ("127.0.0.1:7788", "localhost:7788", "LOCALHOST:7788"):
            self.assertIsNone(serve.refused("GET", host, None, 7788))
            self.assertIsNone(serve.refused("POST", host, None, 7788))  # curl, scripts: no Origin
        self.assertIsNone(serve.refused("PUT", "127.0.0.1:7788", "http://127.0.0.1:7788", 7788))
        self.assertIsNone(serve.refused("POST", "127.0.0.1:7788", "http://localhost:7788", 7788))

    def test_a_request_under_any_other_host_name_is_refused(self):
        for host in ("evil.example:7788", "127.0.0.1:7789", "127.0.0.1", "", None):
            self.assertIsNotNone(serve.refused("GET", host, None, 7788), host)

    def test_a_change_sent_from_another_site_is_refused(self):
        for origin in ("http://evil.example", "http://127.0.0.1:7789", "https://127.0.0.1:7788", "null"):
            self.assertIsNotNone(serve.refused("POST", "127.0.0.1:7788", origin, 7788), origin)
            self.assertIsNotNone(serve.refused("PUT", "127.0.0.1:7788", origin, 7788), origin)
        self.assertIsNone(serve.refused("GET", "127.0.0.1:7788", "http://evil.example", 7788))  # reads are Host-checked only

    def test_a_running_server_answers_its_page_and_refuses_another_site(self):
        handler = type("Handler", (serve.Handler,), {"store": stores.Store(tempfile.mkdtemp())})
        srv = serve.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        port = srv.server_address[1]

        def ask(method, path, headers=None):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            c.request(method, path, body=b"{}" if method != "GET" else None, headers=headers or {})
            r = c.getresponse()
            r.read()
            c.close()
            return r.status

        self.assertEqual(ask("GET", "/api/files"), 200)
        self.assertEqual(ask("GET", "/api/files", {"Host": "evil.example"}), 403)
        self.assertEqual(ask("POST", "/api/pickup", {"Origin": "http://evil.example"}), 403)
        self.assertEqual(ask("POST", "/api/pickup", {"Origin": f"http://127.0.0.1:{port}"}), 409)  # pickup is off

    def test_the_themes_fonts_are_served_and_nothing_else_under_their_path(self):
        handler = type("Handler", (serve.Handler,), {"store": stores.Store(tempfile.mkdtemp())})
        srv = serve.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)

        def get(path):
            c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
            c.request("GET", path)
            r = c.getresponse()
            body = r.read()
            c.close()
            return r.status, r.getheader("Content-Type"), body

        named = re.findall(r"url\(/fonts/([^)]+)\)", (HERE / "kandalf" / "board.html").read_text())
        self.assertTrue(named, "board.html names no bundled fonts")
        for name in set(named):  # every font the page asks for is there, as woff2
            status, kind, body = get("/fonts/" + name)
            self.assertEqual((status, kind, body[:4]), (200, "font/woff2", b"wOF2"), name)
        for path in ("/fonts/../serve.py", "/fonts/..%2fserve.py", "/fonts/missing.woff2", "/fonts/sub/inter.woff2", "/fonts/"):
            self.assertEqual(get(path)[0], 404, path)

    def test_the_page_is_told_the_pickup_column_and_is_not_offered_the_sessions_rules_as_a_board(self):
        def files(**cfg):
            folder = Path(tempfile.mkdtemp())
            (folder / "board.json").write_text(json.dumps(cfg))
            for name in ("plans.md", "pickup.md"):
                (folder / name).write_text("# x\n")
            srv = serve.ThreadingHTTPServer(("127.0.0.1", 0), type("Handler", (serve.Handler,), {"store": stores.Store(folder)}))
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            try:
                c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
                c.request("GET", "/api/files")
                return json.loads(c.getresponse().read())
            finally:
                srv.shutdown()
                srv.server_close()

        plain = files()
        self.assertEqual((plain["pickupColumn"], plain["files"]), ("", ["pickup.md", "plans.md"]))  # pickup off: a board like any
        picked = files(pickup=True, pickupColumn=" Building ")
        self.assertEqual((picked["pickupColumn"], picked["files"]), ("Building", ["plans.md"]))


class Push(unittest.TestCase):
    """The commit queue commits, and pushes when the store says so and has somewhere to push to."""

    def setUp(self):
        top = Path(tempfile.mkdtemp()).resolve()
        self.dir, self.remote = top / "plans", top / "remote.git"
        self.dir.mkdir()
        (self.dir / "plans.md").write_text("# Plans\n\n## To do\n")
        git(self.dir, "init", "-q", "-b", "main")
        git(self.dir, "add", "-A")
        git(self.dir, "commit", "-qm", "init")

    def flush(self, **cfg):
        (self.dir / "board.json").write_text(json.dumps(cfg))
        (self.dir / "plans.md").write_text("# Plans\n\n## To do\n\n- [ ] PLN-01 **New.**\n")
        st = stores.Store(self.dir)
        q = {"paths": ["plans.md", "board.json"], "steps": ["12:00:00 a change"]}
        with mock.patch.dict(os.environ, {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                          "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}):
            self.assertTrue(gitlog.flush(st, q))
        return (self.dir / ".git" / "board-push.log").exists()

    def with_remote(self):
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], check=True, capture_output=True)
        git(self.dir, "remote", "add", "origin", str(self.remote))
        git(self.dir, "push", "-q", "-u", "origin", "main")

    def test_a_store_with_no_remote_commits_and_skips_the_push_quietly(self):
        self.assertFalse(self.flush())

    def test_a_store_with_push_off_commits_and_never_pushes(self):
        self.with_remote()
        self.assertFalse(self.flush(push=False))
        ahead = subprocess.run(["git", "-C", str(self.dir), "rev-list", "--count", "@{upstream}..HEAD"],
                               capture_output=True, text=True).stdout.strip()
        self.assertEqual(ahead, "1")

    def test_a_store_with_a_remote_pushes_each_commit(self):
        self.with_remote()
        self.assertTrue(self.flush())
        head = subprocess.run(["git", "-C", str(self.dir), "rev-parse", "HEAD"], capture_output=True, text=True).stdout
        for _ in range(100):  # the push runs in the background
            there = subprocess.run(["git", "-C", str(self.remote), "rev-parse", "main"], capture_output=True, text=True).stdout
            if there == head:
                break
            time.sleep(0.1)
        self.assertEqual(there, head)


if __name__ == "__main__":
    unittest.main()
