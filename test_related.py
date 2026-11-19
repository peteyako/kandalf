#!/usr/bin/env python3
"""Master tiles and related tiles (serve.py): the index of every keyed tile the page gets,
with each tile's master ("Part of KEY-NN") and the keys it mentions.

    python3 -m unittest test_related
"""

import tempfile
import unittest

from kandalf import serve
from kandalf import store as stores

PLANS = """# Plans

## To do

- [ ] PLN-70 P2 ~2-3d **Dark mode.** The master story.
  - [ ] **Agree the scope.**

## In flight

- [ ] PLN-75 **Theme auth.**
  Part of PLN-70. Runs after PLN-41, and PLN-75 itself is not a mention.

## Done

- [x] PLN-74 ~6h **Colour tokens:** paste one line.
  part of PLN-70 and never part of PLN-99.
"""

BACKLOG = """# Backlog

## Feature

- [ ] PLN-81 Theme analytics
  Part of PLN-70.
- [ ] PLN-82 **Alone.** Part of PLN-82. Not part of the plan. Needs PLN-75 and PLN-74.
- [ ] Unkeyed, part of PLN-70.
"""

ARCHIVE = """# Archived plans

## Archived

- [x] PLN-41 **Login reopen.**
  Archived 2026-11-12.
- [ ] PLN-70 **A stale copy.**
"""

FILES = {"plans.md": PLANS, "backlog.md": BACKLOG, "archive/archived.md": ARCHIVE}


class Index(unittest.TestCase):
    def setUp(self):
        self.store = stores.Store(tempfile.mkdtemp())  # no board.json: keys are PLN-NN
        self.tiles = serve.tile_index(self.store, FILES)

    def test_every_keyed_tile_is_listed_with_where_it_is(self):
        self.assertEqual(sorted(self.tiles), ["PLN-41", "PLN-70", "PLN-74", "PLN-75", "PLN-81", "PLN-82"])
        self.assertEqual([(self.tiles[k]["file"], self.tiles[k]["column"]) for k in ("PLN-75", "PLN-81", "PLN-41")],
                         [("plans.md", "In flight"), ("backlog.md", "Feature"), ("archive/archived.md", "Archived")])

    def test_the_title_drops_the_key_priority_estimate_and_bold(self):
        self.assertEqual([self.tiles[k]["title"] for k in ("PLN-70", "PLN-74", "PLN-81")],
                         ["Dark mode", "Colour tokens", "Theme analytics"])

    def test_done_is_the_done_column_or_the_archive(self):
        self.assertEqual({k: t["done"] for k, t in self.tiles.items()},
                         {"PLN-70": False, "PLN-75": False, "PLN-74": True, "PLN-81": False, "PLN-82": False,
                          "PLN-41": True})

    def test_part_of_names_the_master_in_any_case_and_the_first_one_counts(self):
        self.assertEqual({k: t["parent"] for k, t in self.tiles.items()},
                         {"PLN-70": None, "PLN-75": "PLN-70", "PLN-74": "PLN-70", "PLN-81": "PLN-70",
                          "PLN-82": None, "PLN-41": None})

    def test_mentions_are_the_other_keys_a_tile_names(self):
        self.assertEqual(self.tiles["PLN-75"]["mentions"], ["PLN-41", "PLN-70"])
        self.assertEqual(self.tiles["PLN-82"]["mentions"], ["PLN-74", "PLN-75"])
        self.assertEqual(self.tiles["PLN-70"]["mentions"], [])

    def test_a_key_on_two_boards_is_the_first_board_s_tile(self):
        self.assertEqual(self.tiles["PLN-70"]["file"], "plans.md")


if __name__ == "__main__":
    unittest.main()
