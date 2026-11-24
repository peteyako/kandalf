#!/usr/bin/env python3
"""Linked tiles (tiles.py, serve.py): reading the links, and taking tiles out of one board and putting
them into another without breaking the layout board.html round-trips.

    python3 -m unittest test_links
"""

import subprocess
import tempfile
import unittest
from pathlib import Path

from kandalf import serve
from kandalf import store as stores
from kandalf import tiles as reader

HERE = Path(__file__).resolve().parent

BACKLOG = """# Backlog

## Feature

- [ ] PLN-74 ~6-10h **Embed.**
  **Moves with PLN-75**: together. Plan: [x.md](active/x.md).
  - [ ] **Step one.**
    - a detail
- [ ] PLN-23 **Other.**

## Change

- [ ] PLN-75 **Cookie.**
  The pattern.
  - [ ] **Decide.**

## Empty
"""

PLANS = """# Plans

## To do

## In flight

- [ ] PLN-59 **Narrow.**
  Words.

## Done
"""


def round_trips(text: str) -> bool:
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write(text)
    try:
        return subprocess.run(["node", str(HERE / "kandalf" / "check.js"), f.name], capture_output=True).returncode == 0
    finally:
        Path(f.name).unlink()


class Links(unittest.TestCase):
    def setUp(self):
        self.store = stores.Store(tempfile.mkdtemp())  # no board.json: keys are PLN-NN

    def test_a_link_counts_both_ways_and_across_boards(self):
        self.assertEqual(reader.Snapshot(self.store, {"plan.md": BACKLOG, "build.md": PLANS}).linked(), {"PLN-74": ["PLN-75"], "PLN-75": ["PLN-74"]})

    def test_links_chain_and_a_list_of_keys_is_read(self):
        text = ("## A\n\n- [ ] PLN-01 **One.** Moves with PLN-02, PLN-03 and PLN-04.\n- [ ] PLN-02 **Two.**\n"
                "- [ ] PLN-05 **Five.**\n  moves with PLN-04\n- [ ] PLN-06 **Six.** It moves without PLN-01.\n")
        got = reader.Snapshot(self.store, {"build.md": text}).linked()
        self.assertEqual(got["PLN-05"], ["PLN-01", "PLN-02", "PLN-03", "PLN-04"])
        self.assertEqual(got["PLN-02"], ["PLN-01", "PLN-03", "PLN-04", "PLN-05"])
        self.assertNotIn("PLN-06", got)

    def test_a_tile_does_not_link_to_itself(self):
        self.assertEqual(reader.Snapshot(self.store, {"build.md": "## A\n\n- [ ] PLN-01 **One.** Moves with PLN-01.\n"}).linked(), {})

    def test_several_tiles_go_to_the_top_of_an_empty_column_in_order(self):
        _, taken = reader.take(self.store, BACKLOG, {"PLN-74", "PLN-75"})
        new = PLANS
        for tile in reversed(taken):
            new = serve.insert_at_top(new, "To do", tile)
        self.assertEqual([(t.key, t.column) for t in reader.parse(self.store, new)][:2],
                         [("PLN-74", "To do"), ("PLN-75", "To do")])
        self.assertTrue(round_trips(new))


if __name__ == "__main__":
    unittest.main()
