#!/usr/bin/env python3
"""Decide (serve.py): answering a tile in a decision column writes its decision line, files
the tile in the archive log as Archive does, and names the stories that unblocked.

    python3 -m unittest test_decide
"""

import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kandalf import serve
from kandalf import store as stores
from kandalf import tiles as reader

PLANS = """# Plans

## To do

- [ ] PLN-41 **Needs the theme.** Blocked by PLN-30.
- [ ] PLN-42 **Needs two.** Blocked by PLN-30, PLN-31.
- [ ] PLN-43 **Master.** Blocked by PLN-30.
- [ ] PLN-44 **Sub-story.** Part of PLN-43.
- [ ] PLN-45 **Free.**

## Decisions

- [ ] PLN-30 **Pro theme.** See [the plan](active/2026-01-01-theme.md).
  Keep or retire.
- [ ] PLN-31 **Other.**
- [ ] An unkeyed one.
"""


def git(path, *args):
    return subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          check=True, capture_output=True, text=True).stdout


class Decide(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp()).resolve()
        (self.dir / "active").mkdir()
        self.board = self.dir / "build.md"
        self.board.write_text(PLANS)
        (self.dir / "active" / "2026-01-01-theme.md").write_text("# PLN-30: Theme\n")
        git(self.dir, "init", "-q")
        git(self.dir, "add", "-A")
        git(self.dir, "commit", "-qm", "init")
        self.store = stores.Store(self.dir)
        patch = mock.patch.object(serve.gitlog, "record")
        self.record = patch.start()
        self.addCleanup(patch.stop)

    def body(self, key="PLN-30", answer="retire it.", **over):
        text = self.board.read_text()
        tile = next(t for t in reader.parse(self.store, text) if t.key == key)
        rest, _ = reader.take(self.store, text, [key])
        data = {"mtime": serve.mtime(self.board), "text": rest, "tile": tile.text,
                "docs": ["active/2026-01-01-theme.md"] if key == "PLN-30" else [], "answer": answer}
        data.update(over)
        return data

    def decide(self, data=None):
        return serve.decide_tile(self.store, self.board, data or self.body(), "2026-11-23")

    def log(self):
        p = self.dir / "archive" / "archived.md"
        return p.read_text() if p.is_file() else ""

    def test_a_decided_tile_is_filed_in_the_archive_log_with_its_decision(self):
        status, reply = self.decide()
        self.assertEqual(status, 200)
        self.assertNotIn("PLN-30 **", self.board.read_text())
        tile = next(t for t in reader.parse(self.store, self.log()) if t.key == "PLN-30")
        self.assertTrue(tile.text.startswith("- [x] PLN-30"))
        self.assertEqual(reader.decision(tile.text), {"date": "2026-11-23", "text": "retire it."})
        self.assertIn("maxKey", reply)

    def test_its_plan_docs_move_as_archive_moves_them(self):
        status, reply = self.decide()
        self.assertEqual(reply["moved"], ["active/2026-01-01-theme.md"])
        self.assertTrue((self.dir / "archive" / "2026-01-01-theme.md").is_file())
        self.assertFalse((self.dir / "active" / "2026-01-01-theme.md").exists())

    def test_the_commit_says_the_tile_was_decided(self):
        self.decide()
        self.assertEqual(self.record.call_args.args[2], "board: PLN-30 decided")

    def test_the_tiles_it_unblocked_are_named(self):
        status, reply = self.decide()
        self.assertEqual(serve.unblocked_by(self.store, "PLN-30"), ["PLN-41", "PLN-43", "PLN-44"])
        self.assertEqual(reply["unblocked"], ["PLN-41", "PLN-43", "PLN-44"])
        self.assertNotIn("PLN-42", reply["unblocked"])  # a second open blocker keeps it blocked

    def test_a_sub_story_of_an_unblocked_master_is_named_too(self):
        _, reply = self.decide()
        self.assertIn("PLN-44", reply["unblocked"])

    def test_a_stale_board_is_refused_and_nothing_is_written(self):
        data = self.body(mtime="1")
        before = (self.board.read_text(), self.log(), git(self.dir, "rev-parse", "HEAD"))
        status, reply = self.decide(data)
        self.assertEqual(status, 409)
        self.assertEqual(reply["error"], "changed on disk")
        self.assertEqual(reply["text"], PLANS)
        self.assertTrue((self.dir / "active" / "2026-01-01-theme.md").is_file())
        self.assertFalse((self.dir / "archive").exists())
        self.assertEqual((self.board.read_text(), self.log(), git(self.dir, "rev-parse", "HEAD")), before)

    def test_an_empty_answer_is_refused(self):
        status, reply = self.decide(self.body(answer=" \n\t "))
        self.assertEqual((status, reply), (400, {"error": "an answer is needed"}))
        self.assertEqual(self.board.read_text(), PLANS)
        self.assertTrue((self.dir / "active" / "2026-01-01-theme.md").is_file())

    def test_an_answer_that_is_not_text_is_decided_as_its_text(self):
        status, reply = self.decide(self.body(answer=5))
        self.assertEqual(status, 200, reply)
        tile = next(t for t in reader.parse(self.store, self.log()) if t.key == "PLN-30")
        self.assertEqual(reader.decision(tile.text), {"date": "2026-11-23", "text": "5"})

    def test_a_plan_doc_link_in_the_answer_is_rewritten_as_the_other_links_on_the_tile_are(self):
        status, reply = self.decide(self.body(answer="retire it, as [plan](active/2026-01-01-theme.md) says."))
        self.assertEqual(status, 200, reply)
        tile = next(t for t in reader.parse(self.store, self.log()) if t.key == "PLN-30")
        link = re.search(r"\[the plan\]\(([^)]*)\)", tile.text).group(1)
        self.assertNotEqual(link, "active/2026-01-01-theme.md")
        self.assertEqual(reader.decision(tile.text)["text"], f"retire it, as [plan]({link}) says.")

    def test_an_unkeyed_tile_is_refused(self):
        data = self.body(tile="- [ ] An unkeyed one.", docs=[])
        status, reply = self.decide(data)
        self.assertEqual((status, reply), (400, {"error": "a decision needs a key"}))
        self.assertEqual(self.board.read_text(), PLANS)
        self.assertEqual(self.log(), "")


if __name__ == "__main__":
    unittest.main()
