# Blocked by, and decisions answered in place: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A tile can say `Blocked by PLN-30` and waits until PLN-30 is done; a tile in a decision column is answered on its card, which archives it and unblocks what waited on it.

**Architecture:** Three twin grammar rules (the `Blocked by` phrase, the decision line, the decision column) in `tiles.py` and `board.html`, held together by shared cases. Blocked is derived from a `Snapshot` on every look and never stored. Pickup holds a blocked tile the way it holds a `No pickup` one; `/api/decide` writes the decision line and reuses Archive's path.

**Tech Stack:** Python 3.10+ standard library, one self-contained `kandalf/board.html`, `unittest` and `node --test`.

**Spec:** `docs/superpowers/specs/2026-11-23-blocked-by-and-decisions-design.md`. Read it before any task.

## Global Constraints

- Standard library only. No new dependency, no new package file. Never edit `kandalf/marked.min.js`.
- Every grammar rule is written in both readers in the same commit, each naming its twin in a comment ("same in board.html (X there)" / "tiles.py: X"), with cases in `tile_cases.json` that both `test_tiles.py` and `test_page.js` run.
- A page function a test needs sits between `function parse(` and `// A plan doc (anything` in `board.html` and is added to the object returned at the top of `test_page.js`.
- No module but `claude_code.py`, `copilot_cli.py` and `agy_cli.py` runs an agent. This plan starts no session.
- Tests build throwaway stores in temp dirs. Never point one at a real store.
- Docstrings, comments and test names are plain sentences (`test_a_blocked_tile_in_flight_is_left_alone`).
- Each task adds its user-visible lines under `## Unreleased` in `CHANGELOG.md`.
- Commit by path, subject `feat(scope): … ` lower case after the colon, ending with the `Co-Authored-By` trailer. Work only in `.worktrees/decisions` on `feat/blocked-by-decisions`. No merge, no push.
- Before every test run: `sh ~/.claude/bin/memwait.sh`. A task runs its own test files; Task 7 runs both full suites.
- Suites: `python3 -m unittest discover -p "test_*.py"` and `node --test test_page.js`.

## Review Focus

1. A master that is blocked by its own sub-story (`PLN-01` says `Blocked by PLN-02`, `PLN-02` says `Part of PLN-01`): the sub-story inherits its master's blockers and would block itself for good. Expected: a tile's own key is dropped from inherited blockers. Test in Task 2.
2. An answer with newlines or markdown that would end the tile or start a column (`"## Later\n- [ ] no"`): expected one indented line, nothing else on the board changed. Test in Task 4.
3. A key in another case or another prefix (`blocked by pln-30`, `Blocked by ABC-30` in a PLN store): expected not read as a blocker, as `Moves with` and mentions treat it. Case in Task 1.
4. A blocker in a Done column whose box is not ticked, and a ticked blocker in To do: done is the column or the archive, never the box. Cases in Task 2.
5. Decide on a stale `mtime`: expected 409 with no plan doc moved and no file written. Test in Task 4.

---

### Task 1: The grammar's three rules, in both readers

**Files:**
- Modify: `kandalf/tiles.py` (`_patterns`, `Tile`, `parse`, the attribute readers, the columns section)
- Modify: `kandalf/board.html` (beside `LINK_RE`/`movesWith`, `STATUS_RE`, `IN_FLIGHT_RE`, `tileFacts`, `tileIndex`)
- Modify: `tile_cases.json`, `test_tiles.py` (`TileCases`), `test_page.js` (the returned object, `READERS`, the three case runners)
- Modify: `CHANGELOG.md`

**Interfaces:**
- Produces (Python): `Tile.blockers: list` (keys in the order written, each once, never the tile's own); `tiles.decision(md: str) -> dict | None` giving `{"date": "2026-11-23", "text": "retire it."}` for the last decision line, `None` without one; `tiles.is_decision_column(name: str) -> bool`; `tiles.DECISION_LINE_RE`, `tiles.DECISION_COL_RE`.
- Produces (page): `blockedBy(key, text) -> [keys]`; `decisionLine(line) -> {date, text} | null`; `decisionOf(md) -> {date, text} | null`; `isDecisionCol(name) -> boolean`; `tileFacts(key, text)` gains `blockers`; each `tileIndex()` entry built from this board gains `blockers` and `decision`.

Pinned values:
- The phrase: `\bblocked by\s+(<key list>)`, case-insensitive, with `Moves with`'s key-list grammar and the store's own prefix. It lives in `_patterns` as a seventh pattern (`LINK_RE`'s twin style; the page's is `BLOCKED_RE`).
- Lines the phrase is not read from: the session line (`SESSION_LINE_RE`), Status lines (`STATUS_LINE_RE`), the Ready for review line (`READY_RE`), decision lines. Drop those lines from the text, then match.
- The decision line: `^[ \t]+\*\*Decision (\d{4}-\d\d-\d\d):\*\*(?:[ \t]+(.*))?$`, multiline. Text is trimmed; a line with no text reads as `""`.
- The decision column: `^(?!.*\bnot\b).*\b(?:decisions?|decide)\b`, case-insensitive.

- [ ] **Step 1: Add the cases to `tile_cases.json`**

To `tiles` (each asserts the new `blockers` fact; add `"blockers": []` to the first existing case too):

```json
{"md": "- [ ] PLN-41 **Nav.** Blocked by PLN-30.", "column": "To do", "key": "PLN-41", "blockers": ["PLN-30"], "mentions": ["PLN-30"]},
{"md": "- [ ] PLN-41 **Nav.**\n  blocked by PLN-30, PLN-31 and PLN-32. Blocked by PLN-30 and PLN-33.", "column": "To do", "key": "PLN-41", "blockers": ["PLN-30", "PLN-31", "PLN-32", "PLN-33"]},
{"md": "- [ ] PLN-41 **Nav.** Blocked by PLN-41 and PLN-30.", "column": "To do", "key": "PLN-41", "blockers": ["PLN-30"]},
{"md": "- [ ] PLN-41 **Nav.** blocked by pln-30 and Blocked by ABC-30.", "column": "To do", "key": "PLN-41", "blockers": []},
{"md": "- [ ] PLN-41 **Nav.**\n  **Status 2026-11-23 10:00:** not Blocked by PLN-9 now.\n  **Ready for review:** was Blocked by PLN-8.\n  **Decision 2026-11-23:** Blocked by PLN-7 no more.", "column": "To do", "key": "PLN-41", "blockers": []},
{"md": "- [ ] PLN-41 **Nav.**\n  Claude session `ab12cd34`: `kandalf attach ab12cd34`. Blocked by PLN-6.", "column": "To do", "key": "PLN-41", "blockers": []}
```

To `lines` (a new kind, `decision`):

```json
{"kind": "decision", "line": "  **Decision 2026-11-23:** retire it.", "value": {"date": "2026-11-23", "text": "retire it."}},
{"kind": "decision", "line": "  **Decision 2026-11-23:**", "value": {"date": "2026-11-23", "text": ""}},
{"kind": "decision", "line": "  **Decision 2026-11-20:** keep it.\n  **Decision 2026-11-23:** retire it.", "value": {"date": "2026-11-23", "text": "retire it."}},
{"kind": "decision", "line": "**Decision 2026-11-23:** not indented.", "value": null},
{"kind": "decision", "line": "  Decision 2026-11-23: no bold.", "value": null},
{"kind": "decision", "line": "  The **Decision 2026-11-23:** mid-line.", "value": null}
```

To `columns` (a new `decision` fact):

```json
{"name": "Design decision", "decision": true},
{"name": "Decisions", "decision": true},
{"name": "Decide", "decision": true},
{"name": "Not a decision", "decision": false},
{"name": "Decided", "decision": false},
{"name": "To do", "decision": false}
```

- [ ] **Step 2: Teach both runners the new facts**

`test_tiles.py`: add `"decision": tiles.decision` to `READERS`; add `"blockers": tile.blockers` to `facts`; in the columns test assert `tiles.is_decision_column(name) == case["decision"]` when the case has it (find the columns test by `CASES["columns"]`). `test_page.js`: add `blockedBy, decisionLine, decisionOf, isDecisionCol` to the returned object; `READERS.decision = md => page.decisionOf(md)`; add `blockers: facts.blockers` to `got`; assert `page.isDecisionCol(c.name) === c.decision` when present.

- [ ] **Step 3: Run both and see the new cases fail**

Run: `python3 -m unittest test_tiles.TileCases` and `node --test test_page.js`
Expected: FAIL (`Tile` has no `blockers`; `tiles.decision` missing; `page.decisionOf is not a function`).

- [ ] **Step 4: Write the rules in `tiles.py`**

`Tile.blockers` (default empty list, docstring line), the pattern in `_patterns`, the reading in `parse`, `decision(md)` beside `ready`, `DECISION_LINE_RE` beside `STATUS_LINE_RE`, `DECISION_COL_RE` beside `DONE_COL_RE`, `is_decision_column` in the columns section. Add each to the module docstring's list.

- [ ] **Step 5: Write the twins in `board.html`**

`BLOCKED_RE` and `blockedBy` beside `movesWith`; `DECISION_RE`, `decisionLine`, `decisionOf` beside `STATUS_RE`; `DECISION_COL_RE`, `isDecisionCol` beside `DONE_COL_RE`; `blockers` in `tileFacts`; `decision` in the entry `tileIndex` builds. Update the `serverTiles` comment to list `blockers, decision`.

- [ ] **Step 6: Run both suites' files and see them pass**

Run: `python3 -m unittest test_tiles` and `node --test test_page.js`. Expected: PASS.

- [ ] **Step 7: Commit**

`feat(tiles): a tile says what it is blocked by, and a decision line and column are read`

---

### Task 2: What the whole family says: blocking, decisions, the index

**Files:**
- Modify: `kandalf/tiles.py` (`Snapshot`), `kandalf/board.html` (after `kidsOf`)
- Modify: `tile_cases.json` (a new `blocked` list), `test_tiles.py` (`SnapshotTests`, `TileCases`), `test_page.js`

**Interfaces:**
- Consumes: `Tile.blockers`, `tiles.decision`, the page's `tileFacts(...).blockers` and index `decision`.
- Produces (Python): `Snapshot.blocking(key) -> list[str]`: the keys still blocking the tile, its own first then each master's up the lineage, each once, never the tile's own key; a key with no tile is blocking (`Snapshot.tile(k) is None` says it is unknown). `Snapshot.decisions(key) -> list[Tile]`: the done blockers of the tile and its masters whose text has a decision line, same order. `Snapshot.index()` entries gain `"blockers": [...]` (the tile's own) and `"decision": {"date","text"} | None`. `Snapshot.in_flight()` tiles gain `"blocked": [keys]` and `"decisions": str` (the decided tiles' markdown joined by a newline), each only when non-empty.
- Produces (page): `blocking(key, index) -> [{key, title, known}]` (title `null` and `known: false` for a key the index lacks); `decisionsFor(key, index) -> [{key, date, text}]`; `blocksOf(key, index) -> [keys]` (tiles whose own blockers name this key, in key order); `inRing(key, index) -> [keys]` (the other keys on a ring of open blockers through this tile, in key order, `[]` when none).

- [ ] **Step 1: Add the shared `blocked` cases to `tile_cases.json`**

Each case: `{"name", "files": {"plans.md": …, "backlog.md": …, "archive/archived.md": …}, "expect": {"KEY": {"blocking": [...], "decisions": [...keys], "ring": [...]}}}`. One store text can serve several; cover exactly:

| Case | Expect |
|---|---|
| blocker on the backlog, not done | blocking `[it]` |
| blocker in a Done column with its box unticked | blocking `[]` |
| blocker ticked `[x]` but in To do | blocking `[it]` |
| blocker archived with a decision line | blocking `[]`, decisions `[it]` |
| blocker archived with no decision line | blocking `[]`, decisions `[]` |
| a key no tile has (`PLN-99`) | blocking `["PLN-99"]` |
| sub-story of a master on the backlog that is blocked | the sub-story's blocking is the master's |
| master `Blocked by` its own sub-story | the sub-story's blocking is `[]`; the master's is `[the sub-story]` |
| A blocked by B, B blocked by A | each blocking the other; ring `[other]` |
| two decision lines on an archived blocker | decisions `[it]`; the index's `decision.text` is the later one |

- [ ] **Step 2: Run the cases in both suites**

`test_tiles.py`, in `TileCases`: `test_each_family_says_what_blocks_each_tile` builds `tiles.Snapshot(self.store, case["files"])` and asserts `snap.blocking(key)` and `[t.key for t in snap.decisions(key)]`. `test_page.js`: for each case, open each file other than `plans.md` in turn and collect its `tileIndex()` entries into one object, then `page.open("plans.md", text, thatObject)` and assert `blocking(...).map(b => b.key)`, `decisionsFor(...).map(d => d.key)` and `inRing`. Add the four page functions to the returned object.

- [ ] **Step 3: Add the Snapshot tests that the cases can't say**

In `SnapshotTests`:

```python
def test_the_index_carries_each_tile_s_blockers_and_its_decision(self):
    # index()["PLN-41"]["blockers"] == ["PLN-30"]; index()["PLN-30"]["decision"] == {"date": "2026-11-23", "text": "retire it."}; a tile with neither has [] and None
def test_an_in_flight_tile_carries_what_still_blocks_it(self):
    # in_flight()[0]["blocked"] == ["PLN-30"] with PLN-30 on the backlog; no "decisions" entry
def test_an_in_flight_tile_carries_the_decisions_made_for_it(self):
    # PLN-30 archived with a decision line: no "blocked" entry; "decisions" == PLN-30's tile markdown
def test_a_decided_tile_put_back_on_the_backlog_blocks_again(self):
    # the same PLN-30 tile, decision line and all, in backlog.md instead of the archive: blocking == ["PLN-30"]
```

- [ ] **Step 4: Run and see them fail**

Run: `python3 -m unittest test_tiles` and `node --test test_page.js`. Expected: FAIL (`Snapshot` has no `blocking`; `page.blocking is not a function`).

- [ ] **Step 5: Implement in `Snapshot` and in the page; run again; expect PASS**

- [ ] **Step 6: Commit**

`feat(tiles): the snapshot says what still blocks a tile and which decisions were made for it`

---

### Task 3: Pickup leaves a blocked tile alone and hands the decisions on

**Files:**
- Modify: `kandalf/pickup.py` (`entered`, `tick` near the `held` block, `prompt`, `startup_report`, `summary`), `kandalf/jobs.py:203` (the keys a job carries)
- Test: `test_pickup.py`
- Modify: `CHANGELOG.md`, `README.md` (the pickup section, beside the `No pickup` sentence at line ~124)

**Interfaces:**
- Consumes: the `"blocked"` and `"decisions"` entries of `Snapshot.in_flight()` tiles.
- Produces: ledger state `"blocked": {key: [blockers]}` for the held ones only; `summary(store)` gains `"blocked"` with the same value (`{}` when none); a job carries `"decisions"` (text) when its tile had one.

Pinned copy:
- Log, once per tile while it stays blocked: `{key} is in flight but blocked by {a, b and c}: left alone until {it is|they are} done`.
- Startup report line: `  {key} "{title}": no session: blocked by {a, b and c}` (the form of the `No pickup` line at `test_pickup.py:389`).
- Prompt, appended after the tile text and any master `context`: a blank line, `These decisions were made before {key} started. Follow them:`, then the decisions text.

- [ ] **Step 1: Write the failing tests** (use the file's own `self.tick(board)` helper and store fixtures)

```python
def test_a_blocked_tile_in_flight_is_left_alone(self):
    # plans: PLN-41 In flight "Blocked by PLN-30"; backlog: PLN-30. After tick: no job for PLN-41, state["blocked"] == {"PLN-41": ["PLN-30"]}
def test_a_blocked_tile_is_logged_once_however_many_looks_pass(self):
def test_a_tile_is_picked_up_on_the_look_after_its_last_blocker_is_done(self):
    # second tick with PLN-30 moved to the archive log: a job for PLN-41 is queued, state["blocked"] == {}
def test_a_blocked_master_is_no_umbrella_and_its_sub_stories_wait_with_it(self):
def test_a_tile_at_work_carries_on_when_a_blocker_is_added_and_is_not_listed_as_blocked(self):
def test_the_prompt_gives_the_decisions_made_for_the_story(self):
    # PLN-30 archived with "**Decision 2026-11-23:** retire it.": prompt(...) contains "These decisions were made before PLN-41 started. Follow them:" and "retire it."
def test_a_plain_blocker_that_is_done_adds_nothing_to_the_prompt(self):
def test_the_startup_report_says_a_tile_is_blocked(self):
def test_the_summary_lists_the_blocked_tiles(self):
```

- [ ] **Step 2: Run** `python3 -m unittest test_pickup` **and see them fail**

- [ ] **Step 3: Implement**

In `tick`, a tile with a non-empty `"blocked"` and no live job joins the keys left out of `keys`, exactly where `held` tiles are left out, and is recorded in `state["blocked"]` (kept apart from `state["held"]`). `entered` skips a blocked tile and copies `"decisions"` onto the job. Add `"decisions"` to the carried keys in `jobs.py:203`. No function's signature changes.

- [ ] **Step 4: Run** `python3 -m unittest test_pickup test_jobs` **and see them pass**

- [ ] **Step 5: Commit**

`feat(pickup): a blocked tile waits in flight, and its session is told the decisions made for it`

---

### Task 4: `/api/decide`

**Files:**
- Modify: `kandalf/tiles.py` (the edits section), `kandalf/serve.py` (`archive`, `do_POST`)
- Test: `test_tiles.py` (`Edits`), new `test_decide.py`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: `tiles.decision`, `Snapshot.blocking`.
- Produces: `tiles.with_decision(md: str, answer: str, date: str) -> str`: the tile with `  **Decision {date}:** {answer}` as its last line and its opening `[ ]` ticked (a tile with no box keeps none); the answer is folded to one line (every run of whitespace, newlines included, becomes one space; trimmed). `serve.archive_tile(store, board: Path, text: str, tile: str, docs: list, subject: str | None = None) -> dict`: the body of today's `archive` handler from `archive_docs` to the reply dict, lifted to a module-level function that raises what `archive_docs` raises; `subject` replaces the default commit subject. `serve.unblocked_by(store, key: str) -> list[str]`: the keyed tiles whose own or inherited blockers name `key` and whose `Snapshot.blocking` is now empty, in key order. `serve.decide_tile(store, board: Path, data: dict, today: str) -> tuple[int, dict]`: the status and reply for a decide body, which the handler sends as they are. `POST /api/decide?file=…`, body `{mtime, text, tile, docs, answer}`; reply is Archive's reply plus `"unblocked": [keys]`.

Pinned behaviour: 404 for no such board (as Archive); 409 `{"error": "changed on disk", text, mtime}` on a stale `mtime`, checked before anything is written; 400 `{"error": "an answer is needed"}` when the folded answer is empty; 400 `{"error": "a decision needs a key"}` when the tile's first line has none. The date is the server's `date.today().isoformat()`. Commit subject `board: {key} decided`.

- [ ] **Step 1: Write the failing tests**

`test_tiles.py`, `Edits`:

```python
def test_a_decision_is_the_tile_s_last_line_and_ticks_its_box(self):
    md = "- [ ] PLN-30 **Pro theme.**\n  Keep or retire.\n  - [ ] **Decide.**"
    self.assertEqual(tiles.with_decision(md, "retire it.", "2026-11-23"),
                     "- [x] PLN-30 **Pro theme.**\n  Keep or retire.\n  - [ ] **Decide.**\n  **Decision 2026-11-23:** retire it.")
def test_an_answer_of_several_lines_is_folded_into_one(self):
    out = tiles.with_decision("- [ ] PLN-30 **T.**", "retire it\n\n## Later\n- [ ] no", "2026-11-23")
    self.assertEqual(out.split("\n")[1:], ["  **Decision 2026-11-23:** retire it ## Later - [ ] no"])
def test_a_second_decision_is_added_under_the_first(self):
    # tiles.decision(result)["text"] is the second answer; both lines are there
def test_a_tile_with_no_box_gets_none(self):
```

`test_decide.py` (a temp store with git initialised, modelled on `test_clear.py`'s setUp):

```python
def test_a_decided_tile_is_filed_in_the_archive_log_with_its_decision(self):
def test_its_plan_docs_move_as_archive_moves_them(self):
def test_the_commit_says_the_tile_was_decided(self):          # subject == "board: PLN-30 decided"
def test_the_tiles_it_unblocked_are_named(self):              # unblocked_by == ["PLN-41", "PLN-44"]; a tile with a second open blocker is not named
def test_a_sub_story_of_an_unblocked_master_is_named_too(self):
```

and the refusals, through `serve.decide_tile` (no HTTP server in the tests):

```python
def test_a_stale_board_is_refused_and_nothing_is_written(self):   # no doc moved, archive log unchanged, board unchanged
def test_an_empty_answer_is_refused(self):
def test_an_unkeyed_tile_is_refused(self):
```

- [ ] **Step 2: Run** `python3 -m unittest test_tiles test_decide` **and see them fail**

- [ ] **Step 3: Implement**

Lift `archive_tile` out of the handler first and run `python3 -m unittest test_clear test_links` to see Archive unchanged. Then `with_decision`, `unblocked_by`, the `decide` handler and its route beside `/api/archive`. The handler only finds the board, calls `decide_tile` with today's date and sends what it returns.

- [ ] **Step 4: Run** `python3 -m unittest test_tiles test_decide test_clear test_links` **and see them pass**

- [ ] **Step 5: Commit**

`feat(board): a decision is answered and archived in one step`

---

### Task 5: The board shows locks, answers and the answer box

**Files:**
- Modify: `kandalf/board.html` (`renderCard` at ~2362, `openCard` at ~3423, `buildState` at ~1954, beside `archiveCard` at ~2788, the styles for both theme families)
- Test: `test_page.js`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: `blocking`, `decisionsFor`, `blocksOf`, `inRing`, `isDecisionCol`, `keyList`; `pickups.blocked` (`{key: [blockers]}`, absent from an older server); `POST /api/decide`.
- Produces: `blockerLines(key, index) -> [{kind, key, text}]`, inside the lifted span and exported to the tests. `kind` is `"blocked"`, `"missing"`, `"ring"` or `"decided"`.

Pinned copy (text of each line; the icon is markup, not part of `text`):
- blocked: `Blocked by PLN-30 · {title}`
- missing: `PLN-99 not found`
- ring, one line for the tile in place of its blocked lines for the ring's keys: `PLN-30 and PLN-41 block each other` (the tile's own key included, in key order, joined by `keyList`)
- decided: `PLN-30 decided: {answer}` (the whole answer; CSS clips it to one line)
- a done blocker with no decision line gives no line
- on a decision tile's card: `Blocks PLN-41, PLN-44`, each key a link
- an In flight tile whose key is in `pickups.blocked`, where a queued tile says its place: `waits for PLN-30` (`keyList` for several)
- toast after Decide: `PLN-30 decided: PLN-41 and PLN-44 are unblocked` (`is unblocked` for one); `PLN-30 decided` when the reply's `unblocked` is empty

- [ ] **Step 1: Write the failing tests in `test_page.js`**

```js
test("a tile's blocker lines say what it waits on, what was decided and what is missing", () => {
  // plans: PLN-41 "Blocked by PLN-30, PLN-31, PLN-32 and PLN-99"; index: PLN-30 backlog open "Pro theme",
  // PLN-31 archived with decision {text: "retire it."}, PLN-32 archived with no decision
  assert.deepEqual(page.blockerLines("PLN-41", page.tileIndex()), [
    { kind: "blocked", key: "PLN-30", text: "Blocked by PLN-30 · Pro theme" },
    { kind: "decided", key: "PLN-31", text: "PLN-31 decided: retire it." },
    { kind: "missing", key: "PLN-99", text: "PLN-99 not found" }]);
});
test("two tiles that block each other say so once", () => { /* [{kind: "ring", key: "PLN-30", text: "PLN-30 and PLN-41 block each other"}] for PLN-41 */ });
test("a sub-story shows its master's blockers", () => {});
test("a tile with no blockers has no lines", () => { assert.deepEqual(page.blockerLines("PLN-70", page.tileIndex()), []); });
test("a blocked tile in flight says what it waits for", () => { /* page.setPickups + a setter for blocked; buildState(...) label is "waits for PLN-30" */ });
```

For the last, extend the test harness's `setPickups` to take an optional second argument `blocked` and read how `buildState` returns its label before asserting (follow the neighbouring `buildState` tests).

- [ ] **Step 2: Run** `node --test test_page.js` **and see them fail**

- [ ] **Step 3: Implement `blockerLines` and the "waits for" label; run; expect PASS**

- [ ] **Step 4: Render and wire**

- `renderCard`: under the title, one row per `blockerLines` entry. Blocked, missing and ring rows carry the lock icon and the muted/need colour; decided rows the check icon. Use icons the theme already has (find how the tile's other icons are drawn; no emoji). A click on a row's key opens that tile on its board the way the map panel's **Open tile** does.
- `openCard`: for a card whose section `isDecisionCol`, an Answer box built like `replyHTML` (a textarea, a **Decide** button `data-act="decide"`, disabled while the textarea is empty), and the `Blocks …` line when `blocksOf` is non-empty.
- `decideCard(id, answer)` beside `archiveCard`: builds Archive's body (the board without the tile, the tile, its docs) plus `answer`, gives an unkeyed tile the next key first the way Promote does, posts to `/api/decide`, adopts the reply as `archiveCard` does, and shows the toast. No confirm dialog: Decide is the confirmation. On a 400 or 409 it puts the tile back as `archiveCard` does on failure and flashes the error.
- Both theme families (`:root[data-theme="classic"]` and `:root:not([data-theme="classic"])`) style the rows and the box with existing tokens only.

- [ ] **Step 5: See it in the real page**

Build a throwaway store in the scratchpad (`kandalf init` there, or copy a test fixture), with a decision tile in the backlog's Design decision column and two plans tiles blocked by it. Run `kandalf serve` on it and, with the `agent-browser` skill, check at a wide and a narrow width, in the classic and a non-classic theme: the lock rows, the Answer box, Decide, the toast, and the lock rows turning into decided rows. Stop the server after.

- [ ] **Step 6: Run** `node --test test_page.js` **and commit**

`feat(board): tiles show what blocks them, and a decision is answered on its card`

---

### Task 6: The map joins a blocker to what it blocks

**Files:**
- Modify: `kandalf/board.html` (`mapGraph` at ~2057, the map panel, the edge styles)
- Test: `test_page.js` (beside the `mapGraph` tests at ~783-848)
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: index entries' `blockers`, `blocking(key, index)`.
- Produces: `mapGraph` edges of `kind: "blocks"`; each node gains `blockedBy: [keys]` and `blocks: [keys]` (both key-ordered, own blockers only, keys that have nodes) and `blocked: boolean` (`blocking(key, index)` non-empty, so a master's open blocker and an unknown key count).

Pinned behaviour: a `blocks` edge is joined before mentions, so a pair joined by both is `blocks` and the key is left out of both nodes' `mentions`. `n.next` is false when `n.blocked`. The panel lists **Blocked by** and **Blocks** above mentions, each key a hop like the others. The edge is drawn as a mention edge is; no new arrow.

- [ ] **Step 1: Write the failing tests**

```js
test("the map joins a blocker to what it blocks, and not as a mention", () => {
  // A-1 "Blocked by A-2": edges == [{a: "A-1", b: "A-2", kind: "blocks"}]; nodes A-1.blockedBy == ["A-2"], A-2.blocks == ["A-1"], both mentions == []
});
test("a blocked story is not one to pull next", () => { /* planned, standing alone, blocked: next === false; the same story with its blocker done: next === true */ });
test("a story blocked only through its master is blocked on the map", () => {});
```

- [ ] **Step 2: Run, see them fail, implement, run, see them pass** (`node --test test_page.js`)

- [ ] **Step 3: Commit**

`feat(board): the map shows what blocks a story`

---

### Task 7: The words, and the full gate

**Files:**
- Modify: `README.md` (the tile table at ~262, **Backlog and Promote** at ~231, the map section at ~243, **Development** if it lists test files), `CHANGELOG.md` (read the Unreleased lines of Tasks 1-6 as one entry set), `kandalf/templates/backlog.md` and `kandalf/templates/plans.md` (their header rules), `kandalf/init.py` (the filing rules `kandalf notes` writes), `AGENTS.md` (only if a ground rule now reads wrong)
- Test: `test_init.py` if it pins the filing rules' text

Pinned copy for the README table, after the `No pickup` row:

```
| `Blocked by PLN-30` (or `PLN-30, PLN-31 and PLN-32`) | The story waits for those tiles: it shows a lock, and pickup leaves it alone in In flight until each is in Done or archived. A key no tile has blocks too. A sub-story takes its master's. Take the words off to start it anyway. |
| `**Decision 2026-11-23:** retire it.` (own line) | The answer to a decision. The board writes it when you answer a tile in a decision column, and archives the tile. Stories it blocked show the answer, and their sessions are given the tile to follow. |
```

Backlog header rule to add to the template and the filing rules: a tile in the Design decision column is answered on its card, not promoted; a story that waits on it says `Blocked by` and its key.

- [ ] **Step 1: Write the docs; update `test_init.py` if the filing rules are pinned there**
- [ ] **Step 2: Check a board file that uses the new lines round-trips**: write one to the scratchpad and run `kandalf check <file>`. Expected: no error.
- [ ] **Step 3: Run both full suites, one at a time**

`sh ~/.claude/bin/memwait.sh && python3 -m unittest discover -p "test_*.py" 2>&1 | tail -5`, then `sh ~/.claude/bin/memwait.sh && node --test test_page.js 2>&1 | tail -8`. Expected: both `OK` / `# fail 0`. A timeout is reported as a timeout, not a failure.

- [ ] **Step 4: Commit**

`docs: blocked-by tiles and answering a decision`
