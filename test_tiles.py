#!/usr/bin/env python3
"""The tile reader (tiles.py): what a board file's tiles are, what their lines say, and the
edits that keep the layout board.html round-trips.

    python3 -m unittest test_tiles
"""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from kandalf import pln
from kandalf import store as stores
from kandalf import tiles

HERE = Path(__file__).resolve().parent

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
"""

LINKS = """# Backlog

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

EMPTY = """# Plans

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


class Parse(unittest.TestCase):
    def setUp(self):
        self.store = stores.Store(tempfile.mkdtemp())  # no board.json: keys are PLN-NN
        self.plans = {t.key: t for t in tiles.parse(self.store, PLANS)}
        self.backlog = {t.key: t for t in tiles.parse(self.store, BACKLOG, file="plan.md")}

    def test_tiles_are_found_with_their_columns_and_bodies(self):
        got = tiles.parse(self.store, LINKS)
        self.assertEqual([(t.key, t.column) for t in got],
                         [("PLN-74", "Feature"), ("PLN-23", "Feature"), ("PLN-75", "Change")])
        self.assertTrue(got[0].text.endswith("    - a detail"))
        self.assertTrue(got[2].text.endswith("  - [ ] **Decide.**"))

    def test_a_tile_spans_its_lines_without_the_blank_ones_after_it(self):
        t = self.plans["PLN-70"]
        lines = PLANS.split("\n")
        self.assertEqual((t.start, t.end), (4, 6))
        self.assertEqual(t.text, "\n".join(lines[t.start:t.end]))

    def test_a_tile_is_frozen(self):
        with self.assertRaises(Exception):
            self.plans["PLN-70"].key = "PLN-71"


class Attributes(unittest.TestCase):
    def test_no_text_reads_as_no_line(self):
        self.assertIsNone(tiles.base(None))
        self.assertIsNone(tiles.tests(None))

    def test_opted_out_is_no_pickup_anywhere_in_the_tile(self):
        self.assertTrue(tiles.opted_out("- [ ] PLN-81 **Hands off.** No pickup: a session has this."))
        self.assertFalse(tiles.opted_out("- [ ] PLN-80 **On its own.**"))




class Edits(unittest.TestCase):
    JOB = "  Claude session `ab12cd34`: `kandalf attach ab12cd34`. Branch `pln-70-dark-mode`, worktree `app-pln-70`. Started 2026-11-18."

    def setUp(self):
        self.store = stores.Store(tempfile.mkdtemp())

    def test_a_decision_is_the_tile_s_last_line_and_ticks_its_box(self):
        md = "- [ ] PLN-30 **Pro theme.**\n  Keep or retire.\n  - [ ] **Decide.**"
        self.assertEqual(tiles.with_decision(md, "retire it.", "2026-11-23"),
                         "- [x] PLN-30 **Pro theme.**\n  Keep or retire.\n  - [ ] **Decide.**\n  **Decision 2026-11-23:** retire it.")

    def test_an_answer_of_several_lines_is_folded_into_one(self):
        out = tiles.with_decision("- [ ] PLN-30 **T.**", "retire it\n\n## Later\n- [ ] no", "2026-11-23")
        self.assertEqual(out.split("\n")[1:], ["  **Decision 2026-11-23:** retire it ## Later - [ ] no"])

    def test_a_second_decision_is_added_under_the_first(self):
        once = tiles.with_decision("- [ ] PLN-30 **T.**", "keep it.", "2026-11-22")
        twice = tiles.with_decision(once, "retire it.", "2026-11-23")
        self.assertEqual(tiles.decision(twice), {"date": "2026-11-23", "text": "retire it."})
        self.assertEqual(twice.count("**Decision "), 2)

    def test_a_tile_with_no_box_gets_none(self):
        self.assertEqual(tiles.with_decision("PLN-30 **T.**", "ok", "2026-11-23"),
                         "PLN-30 **T.**\n  **Decision 2026-11-23:** ok")

    DOMAIN_BOARD = "## To do\n\n- [ ] PLN-01 **One.**\n  The story.\n  - [ ] a step\n- [ ] PLN-02 **Two.**\n  Other.\n"

    def test_a_domain_is_added_as_the_last_line_of_the_description(self):
        out = tiles.with_domain(self.store, self.DOMAIN_BOARD, "PLN-01", "Billing")
        self.assertEqual(out, "## To do\n\n- [ ] PLN-01 **One.**\n  The story.\n  Domain: Billing\n  - [ ] a step\n- [ ] PLN-02 **Two.**\n  Other.\n")
        self.assertEqual(tiles.domain(tiles.parse(self.store, out)[0].text), "Billing")

    def test_a_tile_s_domain_line_is_replaced_where_it_is(self):
        board = "## To do\n\n- [ ] PLN-01 **One.**\n  Domain: Admin\n  The story.\n  - [ ] a step\n"
        self.assertEqual(tiles.with_domain(self.store, board, "PLN-01", "Billing"),
                         "## To do\n\n- [ ] PLN-01 **One.**\n  Domain: Billing\n  The story.\n  - [ ] a step\n")

    def test_no_domain_takes_the_line_off(self):
        board = "## To do\n\n- [ ] PLN-01 **One.**\n  Domain: Admin\n  The story.\n- [ ] PLN-02 **Two.**\n  Domain: Admin\n"
        self.assertEqual(tiles.with_domain(self.store, board, "PLN-01", None),
                         "## To do\n\n- [ ] PLN-01 **One.**\n  The story.\n- [ ] PLN-02 **Two.**\n  Domain: Admin\n")

    def test_a_tile_with_no_domain_line_is_left_alone_when_none_is_asked_for(self):
        self.assertEqual(tiles.with_domain(self.store, self.DOMAIN_BOARD, "PLN-01", None), self.DOMAIN_BOARD)

    def test_the_same_domain_again_changes_nothing(self):
        once = tiles.with_domain(self.store, self.DOMAIN_BOARD, "PLN-01", "Billing")
        self.assertEqual(tiles.with_domain(self.store, once, "PLN-01", "Billing"), once)
        odd = "## To do\n\n- [ ] PLN-01 **One.**\n  domain: `Billing`.\n"
        self.assertEqual(tiles.with_domain(self.store, odd, "PLN-01", "Billing"), odd)

    def test_the_line_goes_before_an_archived_tile_s_archived_line(self):
        board = "## Done\n\n- [x] PLN-01 **One.**\n  The story.\n  Archived 2026-11-23.\n"
        self.assertEqual(tiles.with_domain(self.store, board, "PLN-01", "Billing"),
                         "## Done\n\n- [x] PLN-01 **One.**\n  The story.\n  Domain: Billing\n  Archived 2026-11-23.\n")

    def test_the_line_goes_before_a_session_line_an_options_block_and_a_decision(self):
        for tail in (self.JOB, "  **Options 2026-11-23:** Which?\n  - **A. One.** First.", "  **Decision 2026-11-23:** retire it.",
                     "  **Status 2026-11-23 10:00:** going", "  Ready for review: yes"):
            with self.subTest(tail):
                board = "## To do\n\n- [ ] PLN-01 **One.**\n  The story.\n" + tail + "\n"
                self.assertEqual(tiles.with_domain(self.store, board, "PLN-01", "Billing"),
                                 "## To do\n\n- [ ] PLN-01 **One.**\n  The story.\n  Domain: Billing\n" + tail + "\n")

    def test_a_tile_of_one_line_gets_the_line_under_it(self):
        board = "## To do\n\n- [ ] PLN-01 **One.**\n- [ ] PLN-02 **Two.**\n"
        self.assertEqual(tiles.with_domain(self.store, board, "PLN-01", "Billing"),
                         "## To do\n\n- [ ] PLN-01 **One.**\n  Domain: Billing\n- [ ] PLN-02 **Two.**\n")

    def test_only_the_first_of_two_domain_lines_is_replaced(self):
        board = "## To do\n\n- [ ] PLN-01 **One.**\n  Domain: A\n  Domain: B\n"
        self.assertEqual(tiles.with_domain(self.store, board, "PLN-01", "C"),
                         "## To do\n\n- [ ] PLN-01 **One.**\n  Domain: C\n  Domain: B\n")

    def test_a_board_with_no_such_tile_comes_back_as_it_was(self):
        self.assertEqual(tiles.with_domain(self.store, self.DOMAIN_BOARD, "PLN-99", "Billing"), self.DOMAIN_BOARD)

    def test_a_bad_name_is_refused_before_anything_is_written(self):
        with self.assertRaises(ValueError):
            tiles.with_domain(self.store, self.DOMAIN_BOARD, "PLN-01", "-x")

    def test_taking_tiles_leaves_a_board_that_round_trips(self):
        for keys in ({"PLN-74"}, {"PLN-75"}, {"PLN-23"}, {"PLN-74", "PLN-23"}, {"PLN-74", "PLN-23", "PLN-75"}):
            rest, taken = tiles.take(self.store, LINKS, keys)
            self.assertEqual(len(taken), len(keys))
            self.assertEqual({t.key for t in tiles.parse(self.store, rest)}, {"PLN-74", "PLN-23", "PLN-75"} - keys)
            self.assertTrue(round_trips(rest), f"taking {sorted(keys)} left:\n{rest}")

    def test_taking_a_key_that_is_not_there_changes_nothing(self):
        self.assertEqual(tiles.take(self.store, LINKS, {"PLN-99"}), (LINKS, []))

    def test_taken_tiles_go_in_after_their_partner_whole(self):
        _, taken = tiles.take(self.store, LINKS, {"PLN-74", "PLN-75"})
        new = tiles.insert_after(self.store, EMPTY, "PLN-59", taken)
        self.assertEqual([(t.key, t.column) for t in tiles.parse(self.store, new)],
                         [("PLN-59", "In flight"), ("PLN-74", "In flight"), ("PLN-75", "In flight")])
        self.assertIn("    - a detail\n- [ ] PLN-75", new)
        self.assertTrue(round_trips(new))
        with self.assertRaises(KeyError):
            tiles.insert_after(self.store, EMPTY, "PLN-01", taken)

    def test_the_session_line_goes_after_the_description_and_before_the_sub_tasks(self):
        new = tiles.with_session(self.store, PLANS, "PLN-70", self.JOB)
        self.assertIn("- [ ] PLN-70 P2 ~2-3d **Dark mode.** The master story.\n" + self.JOB + "\n  - [ ] **Agree the scope.**\n", new)
        self.assertTrue(round_trips(new))

    def test_a_tile_with_no_sub_tasks_gets_it_as_its_last_line(self):
        new = tiles.with_session(self.store, PLANS, "PLN-75", self.JOB)
        self.assertIn("PLN-75 itself is not a mention.\n" + self.JOB + "\n\n## Done", new)

    def test_a_tile_picked_up_again_has_its_line_replaced(self):
        once = tiles.with_session(self.store, PLANS, "PLN-70", self.JOB)
        twice = tiles.with_session(self.store, once, "PLN-70", self.JOB.replace("ab12cd34", "ffff0000").replace("attach ab12cd34", "attach ffff0000"))
        self.assertEqual(twice.count("Claude session"), 1)
        self.assertIn("`ffff0000`", twice)

    def test_a_session_line_for_a_tile_that_is_gone_leaves_the_board_as_it_was(self):
        self.assertEqual(tiles.with_session(self.store, PLANS, "PLN-99", self.JOB), PLANS)

    def test_the_status_sits_under_the_session_line_in_bold_and_is_replaced_each_time(self):
        board = tiles.with_session(self.store, PLANS, "PLN-70", self.JOB)
        once = tiles.with_status(self.store, board, "PLN-70", "Status 2026-11-18 14:58: session `ab12cd34` at work.")
        self.assertIn(self.JOB + "\n  **Status 2026-11-18 14:58:** session `ab12cd34` at work.\n  - [ ] **Agree", once)
        old = once.replace("  **Status 2026-11-18 14:58:** session `ab12cd34` at work.\n", "").replace(
            "  - [ ] **Agree the scope.**\n", "  - [ ] **Agree the scope.**\n  **Status 2026-11-18 14:00:** as an earlier board left it, last.\n")
        twice = tiles.with_status(self.store, old, "PLN-70", "Status 2026-11-18 15:10: session `ab12cd34` idle.")
        self.assertEqual(twice.count("**Status "), 1)
        self.assertIn(self.JOB + "\n  **Status 2026-11-18 15:10:** session `ab12cd34` idle.\n  - [ ] **Agree", twice)
        self.assertTrue(round_trips(twice))

    def test_a_status_with_no_session_line_is_the_tile_s_last_line(self):
        bare = tiles.with_status(self.store, PLANS, "PLN-70", "Status 2026-11-18 14:58: x.")
        self.assertIn("  - [ ] **Agree the scope.**\n  **Status 2026-11-18 14:58:** x.\n\n## In flight", bare)
        self.assertEqual(tiles.with_status(self.store, PLANS, "PLN-99", "Status 2026-11-18 14:58: x."), PLANS)


    OPTIONS_TILE = "- [ ] PLN-30 **Layering.**\n  Which rule?\n  - [ ] **Decide.**"
    OPTIONS_BOARD = "## Decisions\n\n" + OPTIONS_TILE + "\n"
    ITEMS = [{"letter": "A", "title": "Keep adapters", "summary": "Smallest change."},
             {"letter": "B", "title": "Split the package", "summary": "Cleaner layering."}]

    def test_the_options_block_goes_after_the_description_and_before_the_sub_tasks(self):
        block = tiles.options_block("2026-11-23", self.ITEMS, recommends="B", doc="active/r.md")
        out = tiles.with_options(self.store, self.OPTIONS_BOARD, "PLN-30", block)
        self.assertEqual(out.split("\n")[2:], [
            "- [ ] PLN-30 **Layering.**", "  Which rule?",
            "  **Options 2026-11-23:** recommends B. [Research](active/r.md)",
            "  - **A. Keep adapters.** Smallest change.", "  - **B. Split the package.** Cleaner layering.",
            "  - [ ] **Decide.**", ""])

    def test_a_new_block_replaces_the_one_already_on_the_tile(self):
        first = tiles.with_options(self.store, self.OPTIONS_BOARD, "PLN-30", tiles.options_block("2026-11-22", self.ITEMS))
        second = tiles.with_options(self.store, first, "PLN-30", tiles.options_block("2026-11-23", self.ITEMS[:1], recommends="A"))
        self.assertEqual(second.count("**Options "), 1)
        self.assertEqual(second.count("**B. "), 0)
        self.assertEqual(tiles.options(second)["date"], "2026-11-23")
        self.assertEqual(tiles.options(second)["items"], self.ITEMS[:1])

    def test_a_block_with_no_items_says_the_draft_gave_no_options(self):
        self.assertEqual(tiles.options_block("2026-11-23", [], doc="r.md"),
                         ["  **Options 2026-11-23:** the draft gave no options. [Research](r.md)"])
        self.assertEqual(tiles.options_block("2026-11-23", []), ["  **Options 2026-11-23:** the draft gave no options."])

    def test_titles_summaries_and_the_note_are_folded_to_one_line(self):
        items = [{"letter": "A", "title": "x\n- [ ] y", "summary": "x\n- [ ] y\n## z"}]
        block = tiles.options_block("2026-11-23", items, asked="x\n- [ ] y\n## z")
        self.assertEqual(block, ["  **Options 2026-11-23:** Asked: x - [ ] y ## z",
                                 "  - **A. x - [ ] y.** x - [ ] y ## z"])

    def test_what_is_written_reads_back_the_same(self):
        want = {"date": "2026-11-23", "recommends": "B", "doc": "active/r.md", "asked": "keep the adapter half", "items": self.ITEMS}
        block = tiles.options_block("2026-11-23", self.ITEMS, recommends="B", doc="active/r.md", asked="keep the adapter half")
        out = tiles.with_options(self.store, self.OPTIONS_BOARD, "PLN-30", block)
        self.assertEqual(tiles.options(out), want)

    def test_a_board_with_no_such_tile_comes_back_as_it_was(self):
        self.assertEqual(tiles.with_options(self.store, self.OPTIONS_BOARD, "PLN-99", ["  **Options 2026-11-23:**"]), self.OPTIONS_BOARD)


class Helpers(unittest.TestCase):
    def setUp(self):
        self.store = stores.Store(tempfile.mkdtemp())

    def test_numbers_are_the_keys_opening_a_tile_line_under_a_column_or_not(self):
        text = "- [ ] PLN-03 **Early.**\n\n## A\n\n* [x] PLN-12 **Starred.**\n  Words about PLN-99.\n- PLN-07 plain\n"
        self.assertEqual(sorted(tiles.numbers(self.store, text)), [3, 7, 12])

    def test_the_key_a_tile_line_opens_with_and_a_key_put_in(self):
        self.assertEqual(tiles.opening_key(self.store, "* [ ] PLN-07 **A.**"), "PLN-07")
        self.assertIsNone(tiles.opening_key(self.store, "- [ ] **A.** PLN-07 follows"))
        self.assertEqual(tiles.with_key("- [x] **A.**", "PLN-09"), "- [x] PLN-09 **A.**")
        self.assertEqual(tiles.with_key("* **A.**", "PLN-09"), "* PLN-09 **A.**")

    def test_climb_goes_up_the_masters_and_stops_at_the_first_repeat(self):
        up = {"c": "b", "b": "a", "a": "c", "x": "y"}.get
        self.assertEqual(tiles.climb(up, "c"), ["b", "a"])
        self.assertEqual(tiles.climb(up, "x"), ["y"])  # a master with no tile is the last one named
        self.assertEqual(tiles.climb(up, "z"), [])

    def test_a_session_line_with_no_indent_is_indented_so_it_cannot_end_the_tile(self):
        new = tiles.with_session(self.store, PLANS, "PLN-70", "Claude session `ab12cd34`: `kandalf attach ab12cd34`.")
        self.assertIn("The master story.\n  Claude session `ab12cd34`", new)
        self.assertTrue(tiles.session(tiles.parse(self.store, new)[0].text))

    def test_key_assign_keys_a_starred_tile_too_and_keeps_its_star(self):
        (self.store.dir / "build.md").write_text("## To do\n\n* [ ] **Starred.**\n- [ ] PLN-04 **Keyed.**\n- [ ] **Dashed.**\n", encoding="utf-8")
        self.assertEqual(pln.assign(self.store), ["PLN-05", "PLN-06"])
        self.assertEqual((self.store.dir / "build.md").read_text(encoding="utf-8"),
                         "## To do\n\n* [ ] PLN-06 **Starred.**\n- [ ] PLN-04 **Keyed.**\n- [ ] PLN-05 **Dashed.**\n")
        self.assertEqual(pln.highest(self.store, self.store.dir / "build.md"), 6)


FAMILY_PLANS = """# Plans

## In flight

- [ ] PLN-80 **Kid of an archived master.**
  Part of PLN-41.
* [ ] PLN-81 **Starred.**
- [ ] PLN-82 **Kid of a master in flight.**
  Part of PLN-83.
- [ ] PLN-83 **Master.**
  Tests: full

## To do

- [ ] PLN-84 **Not in flight.**
"""

FAMILY_ARCHIVE = """# Archived plans

## Archived

- [x] PLN-41 **Old master.** Part of PLN-40.
  Tests: affected
  The design is in the doc.
- [x] PLN-40 **Older master.**
  Tests: full
"""

RING = """## In flight

- [ ] PLN-01 **First.** Part of PLN-02.
- [ ] PLN-02 **Second.** Part of PLN-01.
- [ ] PLN-03 **Third.** Part of PLN-02.
"""


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.store = stores.Store(tempfile.mkdtemp())
        self.snap = tiles.Snapshot(self.store, {"build.md": FAMILY_PLANS, tiles.ARCHIVE_LOG: FAMILY_ARCHIVE})

    def test_it_reads_the_files_of_a_store_that_are_there(self):
        (self.store.dir / "archive").mkdir(parents=True, exist_ok=True)
        (self.store.dir / "build.md").write_text(FAMILY_PLANS, encoding="utf-8")
        (self.store.dir / tiles.ARCHIVE_LOG).write_text(FAMILY_ARCHIVE, encoding="utf-8")
        snap = tiles.Snapshot.read(self.store)  # no plan.md
        self.assertEqual(sorted(snap.index()), ["PLN-40", "PLN-41", "PLN-80", "PLN-81", "PLN-82", "PLN-83", "PLN-84"])
        self.assertEqual(sorted(tiles.Snapshot.read(self.store, ["build.md"]).index()), ["PLN-80", "PLN-81", "PLN-82", "PLN-83", "PLN-84"])

    def test_the_index_says_where_each_keyed_tile_is_and_its_own_tests_line(self):
        index = self.snap.index()
        self.assertEqual(index["PLN-83"], {"title": "Master", "file": "build.md", "column": "In flight", "done": False,
                                           "parent": None, "mentions": [], "blockers": [], "decision": None,
                                           "tests": "full", "domain": None})
        self.assertEqual((index["PLN-41"]["file"], index["PLN-41"]["done"], index["PLN-41"]["tests"]),
                         (tiles.ARCHIVE_LOG, True, "affected"))
        self.assertIsNone(index["PLN-80"]["tests"])  # its own line only: the master's is for nearest()

    def test_a_key_on_two_files_is_the_first_one_s_tile(self):
        both = tiles.Snapshot(self.store, {"build.md": "## A\n\n- [ ] PLN-01 **Plans.**\n", "plan.md": "## B\n\n- [ ] PLN-01 **Backlog.**\n"})
        self.assertEqual(both.index()["PLN-01"]["file"], "build.md")
        self.assertEqual(both.texts(), {"PLN-01": "- [ ] PLN-01 **Plans.**"})

    def test_the_texts_are_the_keyed_tiles_markdown(self):
        self.assertEqual(self.snap.texts()["PLN-41"], FAMILY_ARCHIVE.split("\n\n", 2)[2].split("\n- [x] PLN-40")[0])

    def test_lineage_runs_from_the_nearest_master_up(self):
        self.assertEqual([t.key for t in self.snap.lineage("PLN-80")], ["PLN-41", "PLN-40"])
        self.assertEqual(self.snap.lineage("PLN-83"), [])
        self.assertEqual(self.snap.lineage("PLN-99"), [])

    def test_the_nearest_line_of_a_kind_is_found_up_the_family_even_in_the_archive(self):
        self.assertEqual(self.snap.nearest("PLN-80", tiles.tests), "affected")  # the archived master's, not PLN-40's
        self.assertEqual(self.snap.nearest("PLN-82", tiles.tests), "full")  # its master in flight
        self.assertIsNone(self.snap.nearest("PLN-81", tiles.tests))
        self.assertIsNone(self.snap.nearest("PLN-99", tiles.tests))

    def test_a_tile_s_own_line_beats_its_master_s(self):
        own = tiles.Snapshot(self.store, {"build.md": "## A\n\n- [ ] PLN-01 **M.**\n  Tests: full\n- [ ] PLN-02 **K.** Part of PLN-01.\n  Tests: affected\n"})
        self.assertEqual(own.nearest("PLN-02", tiles.tests), "affected")

    def test_a_ring_of_masters_is_broken_with_the_first_of_them_standing_as_the_master(self):
        ring = tiles.Snapshot(self.store, {"build.md": RING})
        self.assertEqual({k: t["parent"] for k, t in ring.index().items()}, {"PLN-01": None, "PLN-02": "PLN-01", "PLN-03": "PLN-02"})
        self.assertEqual(ring.lineage("PLN-01"), [])
        self.assertEqual([t.key for t in ring.lineage("PLN-03")], ["PLN-02", "PLN-01"])

    def test_the_in_flight_tiles_are_the_keyed_ones_in_the_pickup_column(self):
        got = self.snap.in_flight()
        self.assertEqual([(t["key"], t["title"], t["parent"]) for t in got],
                         [("PLN-80", "Kid of an archived master", "PLN-41"), ("PLN-81", "Starred", None),
                          ("PLN-82", "Kid of a master in flight", "PLN-83"), ("PLN-83", "Master", None)])
        self.assertIn("Tests: full", got[3]["text"])

    def test_a_tile_whose_master_is_in_the_archive_carries_the_masters_above_it_as_context(self):
        kid = self.snap.in_flight()[0]
        self.assertEqual(kid["of"], "PLN-41")
        self.assertEqual(kid["context"], "\n".join([self.snap.tile("PLN-41").text, self.snap.tile("PLN-40").text]))

    def test_a_tile_whose_master_is_in_flight_carries_no_context_of_its_own(self):
        kid = self.snap.in_flight()[2]
        self.assertNotIn("of", kid)
        self.assertNotIn("context", kid)

    def test_a_master_on_the_backlog_is_found_for_context(self):
        snap = tiles.Snapshot(self.store, {"build.md": "## In flight\n\n- [ ] PLN-02 **Kid.**\n  Part of PLN-01.\n",
                                           "plan.md": "## Feature\n\n- [ ] PLN-01 **Master.**\n  The design.\n"})
        self.assertEqual((snap.in_flight()[0]["of"], snap.in_flight()[0]["context"]), ("PLN-01", "- [ ] PLN-01 **Master.**\n  The design."))

    def test_an_unkeyed_tile_s_links_are_ignored(self):
        two = tiles.Snapshot(self.store, {"build.md": "## To do\n\n- [ ] Unkeyed. Moves with PLN-5 and PLN-6.\n- [ ] PLN-5 keyed\n"})
        self.assertEqual(two.linked(), {})
        one = tiles.Snapshot(self.store, {"build.md": "## To do\n\n- [ ] Unkeyed. Moves with PLN-5.\n- [ ] PLN-5 keyed\n"})
        self.assertEqual(one.linked(), {})

    def test_linked_groups_run_across_the_files(self):
        snap = tiles.Snapshot(self.store, {"plan.md": "## A\n\n- [ ] PLN-74 **A.** Moves with PLN-75.\n",
                                           "build.md": "## B\n\n- [ ] PLN-75 **B.**\n- [ ] PLN-01 **One.** Moves with PLN-01.\n"})
        self.assertEqual(snap.linked(), {"PLN-74": ["PLN-75"], "PLN-75": ["PLN-74"]})


BLOCKED_PLANS = "## In flight\n\n- [ ] PLN-41 **Nav.** Blocked by PLN-30.\n"
DECIDED = "- [x] PLN-30 **Auth.**\n  **Decision 2026-11-23:** retire it.\n"


class BlockedSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.store = stores.Store(tempfile.mkdtemp())

    def snap(self, **files):
        return tiles.Snapshot(self.store, {"build.md": BLOCKED_PLANS, **files})

    def test_the_index_carries_each_tile_s_blockers_and_its_decision(self):
        index = self.snap(**{tiles.ARCHIVE_LOG: "## Archived\n\n" + DECIDED}).index()
        self.assertEqual(index["PLN-41"]["blockers"], ["PLN-30"])
        self.assertIsNone(index["PLN-41"]["decision"])
        self.assertEqual(index["PLN-30"]["decision"], {"date": "2026-11-23", "text": "retire it."})
        self.assertEqual(index["PLN-30"]["blockers"], [])

    def test_the_index_decision_is_the_later_of_two_decision_lines(self):
        two = "- [x] PLN-30 **Auth.**\n  **Decision 2026-11-20:** keep it.\n  **Decision 2026-11-23:** retire it.\n"
        index = self.snap(**{tiles.ARCHIVE_LOG: "## Archived\n\n" + two}).index()
        self.assertEqual(index["PLN-30"]["decision"]["text"], "retire it.")

    def test_an_in_flight_tile_carries_what_still_blocks_it(self):
        tile = self.snap(**{"plan.md": "## Ideas\n\n- [ ] PLN-30 **Auth.**\n"}).in_flight()[0]
        self.assertEqual(tile["blocked"], ["PLN-30"])
        self.assertNotIn("decisions", tile)

    def test_an_in_flight_tile_carries_the_decisions_made_for_it(self):
        tile = self.snap(**{tiles.ARCHIVE_LOG: "## Archived\n\n" + DECIDED}).in_flight()[0]
        self.assertNotIn("blocked", tile)
        self.assertEqual(tile["decisions"], DECIDED.rstrip("\n"))

    def test_a_decided_tile_put_back_on_the_backlog_blocks_again(self):
        snap = self.snap(**{"plan.md": "## Ideas\n\n" + DECIDED})
        self.assertEqual(snap.blocking("PLN-41"), ["PLN-30"])


class OldNames(unittest.TestCase):
    """A store still on plans.md and backlog.md reads exactly as it did."""

    def setUp(self):
        folder = Path(tempfile.mkdtemp())
        (folder / "plans.md").write_text("# Plans\n")
        (folder / "backlog.md").write_text("# Backlog\n")
        self.store = stores.Store(folder)

    def test_a_store_with_plans_md_is_on_the_old_names(self):
        self.assertEqual((self.store.build, self.store.plan), ("plans.md", "backlog.md"))

    def test_a_key_on_two_old_files_is_the_first_one_s_tile(self):
        both = tiles.Snapshot(self.store, {"plans.md": "## A\n\n- [ ] PLN-01 **Plans.**\n", "backlog.md": "## B\n\n- [ ] PLN-01 **Backlog.**\n"})
        self.assertEqual(both.index()["PLN-01"]["file"], "plans.md")
        self.assertEqual(both.texts(), {"PLN-01": "- [ ] PLN-01 **Plans.**"})

    def test_a_master_in_backlog_md_is_found_for_context(self):
        snap = tiles.Snapshot(self.store, {"plans.md": "## In flight\n\n- [ ] PLN-02 **Kid.**\n  Part of PLN-01.\n",
                                           "backlog.md": "## Feature\n\n- [ ] PLN-01 **Master.**\n  The design.\n"})
        self.assertEqual((snap.in_flight()[0]["of"], snap.in_flight()[0]["context"]), ("PLN-01", "- [ ] PLN-01 **Master.**\n  The design."))

    def test_an_in_flight_tile_on_plans_md_is_blocked_by_a_tile_in_backlog_md(self):
        snap = tiles.Snapshot(self.store, {"plans.md": BLOCKED_PLANS, "backlog.md": "## Ideas\n\n- [ ] PLN-30 **Auth.**\n"})
        self.assertEqual(snap.in_flight()[0]["blocked"], ["PLN-30"])

    def test_a_store_reads_the_files_it_has_by_their_old_names(self):
        (self.store.dir / "plans.md").write_text(FAMILY_PLANS, encoding="utf-8")
        snap = tiles.Snapshot.read(self.store)
        self.assertEqual(snap.index()["PLN-83"]["file"], "plans.md")


CASES = json.loads((HERE / "tile_cases.json").read_text(encoding="utf-8"))  # test_page.js runs the same file
READERS = {"model": tiles.model, "base": tiles.base, "tests": tiles.tests, "domain": tiles.domain, "repos": tiles.repos,
           "sessions": tiles.sessions, "ready": tiles.ready, "decision": tiles.decision}


def seen_session(found):
    return None if found is None else {"agent": found.agent, "id": found.id, "branch": found.branch,
                                       "cuts": [list(c) for c in found.cuts]}


class TileCases(unittest.TestCase):
    """Every case in tile_cases.json, read through tiles. test_page.js reads the same cases
    through board.html, so the two readers hold to the same rules."""

    def setUp(self):
        self.store = stores.Store(tempfile.mkdtemp())  # no board.json: keys are PLN-NN

    def test_each_line_says_what_the_case_says(self):
        for case in CASES["lines"]:
            md = "- [ ] PLN-01 **T.**\n" + case["line"]
            with self.subTest(case["kind"], line=case["line"]):
                if case["kind"] == "status":
                    self.assertEqual(bool(tiles.STATUS_LINE_RE.match(case["line"])), case["value"])
                elif case["kind"] == "session":
                    self.assertEqual(seen_session(tiles.session(md)), case["value"])
                else:
                    self.assertEqual(READERS[case["kind"]](md), case["value"])

    def test_each_tile_has_the_facts_the_case_says(self):
        for case in CASES["tiles"]:
            with self.subTest(case["md"]):
                got = tiles.parse(self.store, f"## {case['column']}\n\n{case['md']}\n")
                self.assertEqual(len(got), 1)
                tile = got[0]
                facts = {"key": tile.key, "title": tile.title, "parent": tile.parent, "links": tile.links,
                         "mentions": tile.mentions, "blockers": tile.blockers, "done": tile.done, "column": tile.column}
                for name, found in facts.items():
                    if name in case:
                        self.assertEqual(found, case[name], name)
                for name, read in READERS.items():
                    if name in case:
                        self.assertEqual(read(tile.text), case[name], name)
                if "session" in case:
                    self.assertEqual(seen_session(tiles.session(tile.text)), case["session"])

    def test_each_index_s_domains_are_as_the_case_says(self):
        for case in CASES["tileDomains"]:
            with self.subTest(sorted(case["index"])):
                self.assertEqual(tiles.tile_domains(case["index"]), case["domains"])

    def test_each_domain_name_is_taken_or_refused_as_the_case_says(self):
        for case in CASES["domainNames"]:
            with self.subTest(case["raw"]):
                if "error" in case:
                    with self.assertRaises(ValueError) as caught:
                        tiles.domain_name(case["raw"])
                    self.assertEqual(str(caught.exception), case["error"])
                else:
                    self.assertEqual(tiles.domain_name(case["raw"]), case["name"])

    def test_each_options_block_reads_as_the_case_says(self):
        for case in CASES["options"]:
            with self.subTest(case["md"]):
                self.assertEqual(tiles.options(case["md"]), case["options"])

    def test_each_board_has_the_tiles_the_case_says(self):
        for case in CASES["boards"]:
            with self.subTest(case["name"]):
                got = tiles.parse(self.store, case["text"], file=case.get("file", "build.md"))
                self.assertEqual([(t.key, t.column, t.done, t.text) for t in got],
                                 [(w["key"], w["column"], w["done"], w["md"]) for w in case["tiles"]])
                for t, want in zip(got, case["tiles"]):
                    if "model" in want:
                        self.assertEqual(tiles.model(t.text), want["model"])
                    if "parent" in want:
                        self.assertEqual(t.parent, want["parent"])

    def test_each_column_is_in_flight_or_done_as_the_case_says(self):
        for case in CASES["columns"]:
            with self.subTest(case["name"], pickupColumn=case.get("pickupColumn")):
                self.store.pickup_column = case.get("pickupColumn")
                if "pickup" in case:
                    self.assertEqual(tiles.is_pickup_column(self.store, case["name"]), case["pickup"])
                if "inFlight" in case:
                    self.assertEqual(tiles.is_pickup_column(self.store, case["name"]), case["inFlight"])
                if "done" in case:
                    self.assertEqual(bool(tiles.DONE_COL_RE.search(case["name"])), case["done"])
                if "decision" in case:
                    self.assertEqual(tiles.is_decision_column(case["name"]), case["decision"])

    def test_each_family_says_what_blocks_each_tile(self):
        for case in CASES["blocked"]:
            snap = tiles.Snapshot(self.store, case["files"])
            for key, want in case["expect"].items():
                with self.subTest(case["name"], key=key):
                    self.assertEqual(snap.blocking(key), want["blocking"])
                    self.assertEqual([t.key for t in snap.decisions(key)], want["decisions"])


if __name__ == "__main__":
    unittest.main()
