#!/usr/bin/env python3
"""Linked tiles (serve.py): reading the links, and taking tiles out of one board and putting
them into another without breaking the layout board.html round-trips.

    python3 -m unittest test_links
"""

import subprocess
import tempfile
import unittest
from pathlib import Path

from kandalf import serve
from kandalf import store as stores

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

    def test_tiles_are_found_with_their_columns_and_bodies(self):
        tiles = serve.board_tiles(self.store, BACKLOG)
        self.assertEqual([(t["key"], t["column"]) for t in tiles],
                         [("PLN-74", "Feature"), ("PLN-23", "Feature"), ("PLN-75", "Change")])
        self.assertTrue(tiles[0]["text"].endswith("    - a detail"))
        self.assertTrue(tiles[2]["text"].endswith("  - [ ] **Decide.**"))

    def test_a_link_counts_both_ways_and_across_boards(self):
        self.assertEqual(serve.linked(self.store, BACKLOG, PLANS), {"PLN-74": ["PLN-75"], "PLN-75": ["PLN-74"]})

    def test_links_chain_and_a_list_of_keys_is_read(self):
        text = ("## A\n\n- [ ] PLN-01 **One.** Moves with PLN-02, PLN-03 and PLN-04.\n- [ ] PLN-02 **Two.**\n"
                "- [ ] PLN-05 **Five.**\n  moves with PLN-04\n- [ ] PLN-06 **Six.** It moves without PLN-01.\n")
        got = serve.linked(self.store, text)
        self.assertEqual(got["PLN-05"], ["PLN-01", "PLN-02", "PLN-03", "PLN-04"])
        self.assertEqual(got["PLN-02"], ["PLN-01", "PLN-03", "PLN-04", "PLN-05"])
        self.assertNotIn("PLN-06", got)

    def test_a_tile_does_not_link_to_itself(self):
        self.assertEqual(serve.linked(self.store, "## A\n\n- [ ] PLN-01 **One.** Moves with PLN-01.\n"), {})

    def test_taking_tiles_leaves_a_board_that_round_trips(self):
        for keys in ({"PLN-74"}, {"PLN-75"}, {"PLN-23"}, {"PLN-74", "PLN-23"}, {"PLN-74", "PLN-23", "PLN-75"}):
            rest, taken = serve.take_tiles(self.store, BACKLOG, keys)
            self.assertEqual(len(taken), len(keys))
            self.assertEqual({t["key"] for t in serve.board_tiles(self.store, rest)},
                             {"PLN-74", "PLN-23", "PLN-75"} - keys)
            self.assertTrue(round_trips(rest), f"taking {sorted(keys)} left:\n{rest}")

    def test_taking_a_key_that_is_not_there_changes_nothing(self):
        self.assertEqual(serve.take_tiles(self.store, BACKLOG, {"PLN-99"}), (BACKLOG, []))

    def test_taken_tiles_go_in_after_their_partner_whole(self):
        rest, taken = serve.take_tiles(self.store, BACKLOG, {"PLN-74", "PLN-75"})
        new = serve.insert_after(self.store, PLANS, "PLN-59", taken)
        self.assertEqual([(t["key"], t["column"]) for t in serve.board_tiles(self.store, new)],
                         [("PLN-59", "In flight"), ("PLN-74", "In flight"), ("PLN-75", "In flight")])
        self.assertIn("    - a detail\n- [ ] PLN-75", new)
        self.assertTrue(round_trips(new))
        with self.assertRaises(KeyError):
            serve.insert_after(self.store, PLANS, "PLN-01", taken)

    def test_several_tiles_go_to_the_top_of_an_empty_column_in_order(self):
        _, taken = serve.take_tiles(self.store, BACKLOG, {"PLN-74", "PLN-75"})
        new = PLANS
        for tile in reversed(taken):
            new = serve.insert_at_top(new, "To do", tile)
        self.assertEqual([(t["key"], t["column"]) for t in serve.board_tiles(self.store, new)][:2],
                         [("PLN-74", "To do"), ("PLN-75", "To do")])
        self.assertTrue(round_trips(new))


if __name__ == "__main__":
    unittest.main()
