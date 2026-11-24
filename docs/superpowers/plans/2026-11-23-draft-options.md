# Draft options: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Draft options button on a decision tile starts one background session in a throwaway worktree; it writes a research doc, and the server writes a short options block onto the tile that the card lists above the Answer box.

**Architecture:** A new `kandalf/drafts.py` owns a draft's record, states, the research doc's reading, the prompt and the lifecycle (request, start, look, end), using pickup's worktree helpers and the `Sessions` adapter. Drafts live in the ledger beside jobs and have their own turn in the server's watch loop. The options block is one more twin grammar rule, read by `tiles.py` and `board.html`.

**Tech Stack:** Python 3.10+ standard library, one self-contained `kandalf/board.html`, `unittest` and `node --test`.

**Spec:** `docs/superpowers/specs/2026-11-23-draft-options-design.md`. Read it before any task.

## Global Constraints

- Standard library only. Never edit `kandalf/marked.min.js`. The page stays one file.
- A twin rule is written in both readers in the same commit, each naming its twin in a comment, with cases in `tile_cases.json` or `view_cases.json` that both suites run.
- A page function a test needs sits between `function parse(` and `// A plan doc (anything` in `board.html` and is in the object returned at the top of `test_page.js`.
- `claude_code.py`, `copilot_cli.py` and `agy_cli.py` stay the only modules that run an agent. `drafts.py` reaches them only through `agent` and pickup's `Sessions` adapter.
- Tests build throwaway stores and git repos in temp dirs, never a real store, and never start a real agent: sessions come from a fake adapter.
- Docstrings, comments and test names are plain sentences. `pyproject.toml` lists the `kandalf` package whole, so a new module needs no entry there.
- Each task adds its user-visible lines under `## Unreleased` in `CHANGELOG.md`.
- Commit by path, subject `feat(scope): …`, ending with the `Co-Authored-By` trailer. Work only in `.worktrees/draft-options` on `feat/draft-options`. No merge, no push.
- Before every test run: `sh ~/.claude/bin/memwait.sh`; one thing at a time; output through `tail`.
- A draft's states are exactly: `queued`, `working`, `ready`, `empty`, `need`, `lost`, `ending`.

## Review Focus

1. A research doc that drifts from the format (`## Option A: Restyle`, `## a) restyle`, `Recommends: b`): expected to read as option A all the same, since an agent wrote it. Test in Task 2.
2. A summary or title carrying markdown that would end the tile (`- [ ] x`, `## y`, newlines, a `Part of PLN-1` phrase): expected one folded line, and no tile joined by it. Tests in Tasks 1 and 2.
3. Draft options pressed twice for one tile, or Draft again while one is at work: expected one draft per key; the newer ends the older first. Test in Task 3.
4. A tile decided, deleted or moved out of its decision column while its draft is still queued: expected the draft dropped with no git or session call. Test in Task 4.
5. The draft's worktree path already exists (a crash left it): expected a registered worktree there is removed and made again; anything else makes the draft `need` with git's message as `why`. Test in Task 3.

---

### Task 1: The options block, in both readers

**Files:**
- Modify: `kandalf/tiles.py`, `kandalf/board.html`, `tile_cases.json`, `test_tiles.py`, `test_page.js`, `CHANGELOG.md`

**Interfaces:**
- Produces (Python): `tiles.options(md: str) -> dict | None`: `{"date", "recommends", "doc", "asked", "items": [{"letter", "title", "summary"}]}` for the tile's first Options line and the option lines directly under it; `recommends`, `doc`, `asked` are `None` when absent; `None` when the tile has no Options line. `tiles.options_block(date: str, items: list, recommends=None, doc=None, asked=None) -> list[str]`: the block's lines, indented. `tiles.with_options(store, text: str, key: str, block: list) -> str`: the board with the block in that tile, after the description and before the first tick-box sub-task, replacing a block already there wherever it is; the board unchanged when no tile has the key. `tiles.OPTIONS_LINE_RE`, `tiles.OPTION_ITEM_RE`.
- Produces (page): `optionsOf(md) -> {date, recommends, doc, asked, items} | null`, same reading.

Pinned grammar:
- Options line: `^[ \t]+\*\*Options (\d{4}-\d\d-\d\d):\*\*(?:[ \t]+(.*))?$`. In the rest: `recommends ([A-H])\.`; the doc is the href of the first markdown link; `Asked: (.*)$` runs to the end of the line.
- Option line: `^[ \t]+[-*] \*\*([A-H])\. (.+?)\*\*(?:[ \t]+(.*))?$`. The title loses one trailing full stop. Any other line ends the block.
- `options_block` writes `  **Options <date>:**` then, each only when given and in this order, ` recommends <L>.`, ` [Research](<doc>)`, ` Asked: <asked>`; with no items it writes ` the draft gave no options.` after the date and before the link. Each item: `  - **<L>. <title>.** <summary>` (no added full stop when the title ends in `.`, `?` or `!`; no trailing space when the summary is empty). `asked`, titles and summaries are folded to one line (runs of whitespace to one space, trimmed).
- `Blocked by`, `Part of` and `Moves with` are not read from the block's lines; keys there still count as mentions.

- [ ] **Step 1: Add shared cases to `tile_cases.json`**: a new list `options`, each `{"md", "options"}` (`options` null when there is none), covering: the spec's two-option block read in full; an Options line alone with no items and "the draft gave no options." (`items: []`, `recommends: null`); a line with `Asked:` after the link; option lines using `*` bullets; a block ended by a tick-box sub-task; a title ending in `?`; no Options line (`null`); an option-shaped line with no Options line above it (`null`). To `tiles`: a tile whose option summary says `Part of PLN-70, Moves with PLN-71 and Blocked by PLN-72` expecting `parent: null`, `links: []`, `blockers: []`, `mentions: ["PLN-70", "PLN-71", "PLN-72"]`.
- [ ] **Step 2: Run the `options` list in both suites** (`test_tiles.TileCases`, `test_page.js`), and add to `test_tiles.py` `Edits`:

```python
def test_the_options_block_goes_after_the_description_and_before_the_sub_tasks(self):
def test_a_new_block_replaces_the_one_already_on_the_tile(self):          # old option lines gone, one Options line
def test_a_block_with_no_items_says_the_draft_gave_no_options(self):
def test_titles_summaries_and_the_note_are_folded_to_one_line(self):      # "x\n- [ ] y\n## z" -> "x - [ ] y ## z"
def test_what_is_written_reads_back_the_same(self):                        # options(with_options(...)) == what went in
def test_a_board_with_no_such_tile_comes_back_as_it_was(self):
```

- [ ] **Step 3: Run both, see them fail** (`python3 -m unittest test_tiles`, `node --test test_page.js`).
- [ ] **Step 4: Implement in `tiles.py`** (the reader beside `decision`, the edits beside `with_session`, the skip beside the lines `Blocked by` already skips, the module docstring's list) **and the twin `optionsOf` in `board.html`** beside `decisionOf`, with the same skip in `tileFacts`/`movesWith`/`blockedBy`.
- [ ] **Step 5: Run both suites' files, see them pass. Commit:** `feat(tiles): a tile carries an options block the server can write`

---

### Task 2: A draft's own readings: the research doc, the prompt, the view

**Files:**
- Create: `kandalf/drafts.py`, `test_drafts.py`
- Modify: `view_cases.json`, `test_page.js`, `kandalf/board.html`

**Interfaces:**
- Consumes: `tiles.options_block`.
- Produces: `drafts.STATES`; `drafts.read_doc(text: str) -> dict`: `{"recommends": letter | None, "items": [{"letter", "title", "summary"}]}`, at most four items; `drafts.doc_path(key: str, date: str) -> str`: `active/<date>-<key lower>-options.md`; `drafts.block(reading: dict, date: str, doc: str | None, asked: str | None) -> list[str]` (through `tiles.options_block`; `doc` is the store-relative path); `drafts.prompt(store, draft: dict, tile: str, context: str, blocks: list, again: bool) -> str`; `drafts.view(draft: dict) -> {"label": str, "actions": list}`. Page twin: `draftView(draft)`.

Pinned values:
- `read_doc`: `Recommends:` is `^recommends:\s*\**([A-Ha-h])\b`, case-insensitive, before the first option, upper-cased. An option heading is `^##\s+(?:option\s+)?([A-Ha-h])[.:)]\s+(.+?)\s*#*$`, case-insensitive, letter upper-cased; the title loses emphasis marks and one trailing full stop. The summary is the first paragraph under the heading (lines up to a blank line or the next heading), folded to one line and cut at 240 characters on a word boundary with `…` added when cut. A `recommends` naming no option read is `None`.
- `view`:

| state | label | actions |
|---|---|---|
| `queued` | `queued to draft` | `["stop"]` |
| `working` | `drafting options` | `["stop"]` |
| `ready` | `options ready` | `["again"]` |
| `empty` | `draft gave no options` | `["again"]` |
| `need` | `draft needs you` | `["again", "stop"]` |
| `lost` | `draft lost` | `["again"]` |
| `ending` | `` | `[]` |

- The prompt carries, word for word, the five rules in the spec's **The prompt** section, the doc's path as the session reaches it (`docs/<store.link>/<doc_path>`), and the spec's research-doc example as the format. With `again`, the note and "The last draft is at `<path>`: revise it."

- [ ] **Step 1: Write `test_drafts.py`**

```python
class ReadDoc(unittest.TestCase):
    def test_the_spec_s_example_reads_as_two_options_and_a_recommendation(self):
    def test_a_heading_that_drifts_from_the_format_is_still_an_option(self):     # "## Option A: Restyle", "## b) retire it", "Recommends: b" -> A, B, recommends "B"
    def test_other_headings_and_what_follows_them_are_ignored(self):             # "## What I read"
    def test_a_summary_is_the_first_paragraph_folded_and_cut_on_a_word(self):    # 400 chars in -> <= 241 out, ends "…", no cut word
    def test_only_the_first_four_options_are_kept(self):
    def test_a_recommendation_for_an_option_that_is_not_there_is_dropped(self):
    def test_a_doc_with_no_option_reads_as_empty(self):                           # {"recommends": None, "items": []}
class Block(unittest.TestCase):
    def test_a_reading_becomes_the_tile_s_block_and_reads_back(self):             # tiles.options(...) round trip
    def test_an_empty_reading_says_the_draft_gave_no_options_and_keeps_the_link(self):
class Prompt(unittest.TestCase):
    def test_it_names_the_one_file_to_write_and_forbids_code_changes_and_commits(self):
    def test_a_redraft_carries_the_note_and_points_at_the_last_draft(self):
class View(unittest.TestCase):
    def test_each_case_in_view_cases_json_has_the_view_it_says(self):             # view_cases.json "drafts"
```

- [ ] **Step 2: Add `"drafts"` to `view_cases.json`** (one `{"draft": {"state": …}, "label", "actions"}` per row of the table) and run it in `test_page.js` through `draftView`.
- [ ] **Step 3: Run, see them fail; implement `drafts.py` and `draftView`; run, see them pass** (`python3 -m unittest test_drafts`, `node --test test_page.js`).
- [ ] **Step 4: Commit:** `feat(drafts): reading a research doc, and what a draft's session is told`

---

### Task 3: A draft is asked for, queued, started and ended

**Files:**
- Modify: `kandalf/drafts.py`, `kandalf/jobs.py` (`_loaded`), `kandalf/pickup.py` (`why_off`, `prepare`), `test_drafts.py`

**Interfaces:**
- Consumes: `pickup.worktree`, `pickup.keep_out`, `pickup._git`, `pickup.story_trunk`, `pickup.resolve_repos`, `pickup.model_for`, `pickup.session_args`, `pickup._launch`, the `pickup.Sessions` adapter (`sessions`, `why`, `full_id`, `retire`, `now`).
- Produces:
  - `jobs._loaded` gives `state["drafts"]` (`[]` when the file has none).
  - `pickup.cannot_run(store, *, installed, lacks) -> str | None`: the reason from every check `why_off` makes after its `pickup` check, without the "Pickup is off: " lead; `why_off` is rebuilt on it and its sentences stay byte for byte what they are. `pickup.link_store(store, repo, path)`: the `docs/<link>` step lifted out of `prepare`, which calls it.
  - `drafts.why_not(store, **kw) -> str | None`: `"Draft options is off: " + reason`, or for a `plan` store `Draft options is off: the store's permission is plan, so a session can't write the research doc.`
  - `drafts.request(store, key: str, tile: str, note: str | None, *, sessions) -> dict`: ends a draft the key already has (as `end`), then appends `{"key", "state": "queued", "title", "note", "doc", "asked_at"}`; `doc` is the earlier draft's when there was one, else `doc_path(key, today)`. Returns the record.
  - `drafts.start(store, draft: dict, tile: str, context: str, blocks: list, *, sessions) -> dict`: makes the worktrees and launches; returns the fields to set: `{"state": "working", "id", "session", "repos": [{"name", "worktree", "trunk"}], "started"}` or `{"state": "need", "why": message}`.
  - `drafts.end(store, key: str, *, sessions) -> bool`: marks the draft `ending`, retires its session, removes each worktree (`git worktree remove --force`), then takes it out of the ledger; a step that fails leaves it `ending` and returns False.
  - `drafts.of(store, key) -> dict | None`; `drafts.live(store) -> list` (every draft in the ledger).

Pinned behaviour:
- The worktree path is `pickup.worktree(store, {"key": "draft-" + key, "title": ""}, repo)`; made with `git worktree add --detach <path> <trunk>` where trunk is `pickup.story_trunk(store, repo, None, tile)`; then `keep_out` and `link_store`.
- The session is launched with `pickup._launch` in the first repo's worktree, args `pickup.session_args({"key": key, "title": "options", "model": pickup.model_for(store, tile)})` (so it is named `<KEY> options`), the other repos' worktrees as extra dirs.
- A draft whose state is `queued` ends without any git or session call.

- [ ] **Step 1: Write the tests** (a temp app repo with one commit and a temp store, as `test_pickup.py`'s fixtures build them; a fake adapter subclassing `pickup.Sessions` that records `retire` and answers `sessions`/`why`/`full_id`; launching patched at `pickup._launch`):

```python
class Request(unittest.TestCase):
    def test_a_draft_asked_for_is_queued_with_its_doc_path_and_note(self):
    def test_asking_again_ends_the_draft_the_tile_had_and_keeps_its_doc_path(self):
    def test_the_ledger_from_before_drafts_reads_as_having_none(self):
class Start(unittest.TestCase):
    def test_it_cuts_a_detached_worktree_of_the_trunk_with_the_store_linked_in(self):   # no new branch in `git branch --list`
    def test_a_base_line_on_the_tile_names_the_branch_it_is_cut_from(self):
    def test_a_registered_worktree_left_at_the_path_is_made_again(self):
    def test_a_path_that_cannot_be_used_makes_the_draft_need_you_with_git_s_words(self):
    def test_a_launch_that_fails_makes_the_draft_need_you(self):
class End(unittest.TestCase):
    def test_ending_retires_the_session_and_removes_the_worktree_with_what_it_changed(self):  # a file edited in the worktree is gone; the app's status is clean
    def test_a_queued_draft_ends_without_touching_git_or_the_agent(self):
    def test_a_draft_whose_worktree_will_not_go_stays_as_ending(self):
class WhyNot(unittest.TestCase):
    def test_draft_options_runs_with_pickup_off(self):                 # pickup False, the rest fine -> None
    def test_it_says_what_is_missing_in_pickup_s_words(self):          # no app -> 'Draft options is off: board.json names no "app" repo …'
    def test_a_plan_store_cannot_draft(self):
    def test_pickup_s_own_reasons_read_as_they_did(self):              # why_off sentences unchanged for each check
```

- [ ] **Step 2: Run** `python3 -m unittest test_drafts` **, see them fail; implement; run** `python3 -m unittest test_drafts test_pickup test_jobs` **, see them pass.**
- [ ] **Step 3: Commit:** `feat(drafts): a draft is queued, cut a throwaway worktree, and ended`

---

### Task 4: The look: one draft at a time, and the tile follows the doc

**Files:**
- Modify: `kandalf/drafts.py`, `test_drafts.py`

**Interfaces:**
- Consumes: Task 3's `start`/`end`, `read_doc`, `block`, `tiles.Snapshot`, `tiles.is_decision_column`.
- Produces: `drafts.look(store, snap, *, sessions, write) -> list[str]`: one pass over the drafts, returning the log lines it has to say. `write(key: str, block: list[str], subject: str)` is called to put a block on a tile. `drafts.busy(store) -> bool` (a draft `queued` or `working`). `drafts.docs(store) -> dict`: `{key: mtime_ns | None}` of the research doc of every draft not `ending`. `drafts.summary(store) -> list`: `[{key, state, why, id, started, asked_at, view}]`.

Pinned behaviour, in this order within one look:
1. A draft whose tile is gone from plans and backlog, is done, or is no longer in a decision column: `end`.
2. A draft `ending`: `end` again.
3. A `working` draft, by its session: busy, left alone; idle with nothing it waits for, `ready` or `empty` after reading the doc; waiting or ended on an error, `need` with `why`; not in the session list, `lost`. When sessions can't be listed (`None`), nothing changes.
4. A `ready`, `empty` or `need` draft whose doc's mtime differs from the `read` mtime on its record, and whose session is not busy: read again; the state becomes `ready` or `empty`.
5. When no draft is `working`, the oldest `queued` one is started.
- Reading the doc sets the record's `read` to the doc's mtime and calls `write` only when the block differs from the tile's present block. Subject: `board: <KEY> options drafted`; for an empty reading `board: <KEY> draft gave no options`. The block's date is the day of the reading; `asked` is the record's note.
- A draft that has no doc when its session goes idle is `empty` and writes the empty Options line with no link.

- [ ] **Step 1: Write the tests** (`class Look`, with the fake adapter's session list set per test and a `write` that records its calls):

```python
def test_the_oldest_queued_draft_starts_and_the_next_waits_for_it(self):
def test_a_draft_whose_session_is_busy_is_left_alone(self):
def test_an_idle_session_with_a_doc_makes_the_draft_ready_and_writes_the_block(self):
def test_an_idle_session_with_no_doc_makes_it_empty_and_says_so_on_the_tile(self):
def test_a_session_that_waits_on_a_question_needs_you_with_why(self):
def test_a_session_gone_from_the_list_is_lost(self):
def test_nothing_changes_when_the_sessions_cannot_be_listed(self):
def test_a_doc_changed_after_the_draft_was_ready_is_read_again(self):
def test_a_doc_read_again_with_nothing_new_writes_nothing(self):
def test_a_changed_doc_waits_while_the_session_is_busy(self):
def test_a_draft_ends_when_its_tile_is_archived_or_leaves_the_decision_column(self):
def test_a_queued_draft_whose_tile_was_decided_is_dropped_with_no_git_or_session_call(self):
def test_a_draft_left_ending_is_ended_on_the_next_look(self):
def test_the_next_queued_draft_starts_on_the_look_after_the_one_at_work_is_ready(self):
def test_the_summary_gives_each_draft_its_view(self):
```

- [ ] **Step 2: Run, see them fail; implement; run** `python3 -m unittest test_drafts` **, see them pass. Commit:** `feat(drafts): one draft at a time, and the tile's options follow the research doc`

---

### Task 5: The server and the CLI

**Files:**
- Modify: `kandalf/serve.py` (`watch_pickups`, a new `watch_drafts_turn`, `do_POST`, `decide_tile`/`archive`/`clear`/the discard route, the start-up), `kandalf/pickup.py` (`summary`), `kandalf/cli.py` (`session_of`), `test_drafts.py`, `test_settings.py` (`Watch`), `CHANGELOG.md`

**Interfaces:**
- Consumes: `drafts.request`, `look`, `end`, `busy`, `docs`, `summary`, `why_not`.
- Produces: `POST /api/draft?file=<board>` body `{key, note}` starts or restarts; `{key, stop: true}` ends. Replies `200` with `pickup.summary`'s dict; `409 {"error": drafts.why_not}` when it can't run; `404 {"error": "<KEY> is not a decision on this board"}` when the key is not a keyed tile in a decision column of that file. `pickup.summary(store)` gains `"drafts"` (`drafts.summary`) and `"draftsOff"` (`drafts.why_not`, or null). `serve.write_options(store, key, block, subject)`: finds the tile's board among plans and backlog, applies `tiles.with_options`, writes atomically and records the commit. `serve.watch_drafts_turn(store, ctx)`.

Pinned behaviour:
- `watch_pickups` calls `watch_drafts_turn` every turn, before `watch_turn` and inside its own try. It looks when `drafts.busy` and `PICKUP_RECHECK` seconds have passed since its last look, or when `drafts.docs` differs from what it last looked at and equals what it was on the turn before (settled). `ctx` keys are its own (`d_last`, `d_before`, `d_seen`). A draft asked for through `/api/draft` is looked at on the next turn (`d_seen` cleared).
- Decide, Archive, Clear and the delete (discard) route call `drafts.end` for each key they remove, after their own write.
- At start-up, one `drafts.look`.
- `kandalf attach KEY`: the story job's session when the key has one with an id, else the draft's; neither says `<KEY> has no session`.

- [ ] **Step 1: Write the tests**: `test_drafts.py` `class Server` (module-level functions, a temp store with git; no HTTP): `write_options` finds a backlog tile and a plans tile, commits with the subject, and leaves a board without the key untouched; the draft request's refusals (not a decision column; `why_not`); deciding a tile ends its draft. `test_settings.py` `Watch`: a queued draft is looked at on the next turn with pickup off; a working draft is looked at again only after the recheck interval; a changed doc is looked at once it has settled; no draft, no look. `test_drafts.py` `class Attach`: `cli.session_of` gives the draft's id for a key with no story session, and the story's when it has both.
- [ ] **Step 2: Run, see them fail; implement; run** `python3 -m unittest test_drafts test_settings test_decide test_clear` **, see them pass. Commit:** `feat(board): draft options is asked for, watched and ended by the server`

---

### Task 6: The page

**Files:**
- Modify: `kandalf/board.html` (`pickupTag` ~1773, `decideHTML` ~3539, `decideCard` ~2935, `pollPickups` ~4019, the tile face's body rendering, styles for both theme families), `test_page.js`, `CHANGELOG.md`

**Interfaces:**
- Consumes: `optionsOf`, `draftView`, `pickups.drafts`, `pickups.draftsOff`, `POST /api/draft`.
- Produces (inside the lifted span, exported): `draftOf(key) -> draft | null` (from `pickups.drafts`, null on an older server); `draftTag(key) -> string` (the label, `""` with no draft; a `working` draft adds its age, `drafting options · 12m`); `optionRows(md) -> [{letter, title, summary, recommended}]`; `useText(row) -> string` (`<letter>. <title>. <summary>`, no doubled full stop); `faceBody(lines) -> lines` (the tile's body lines without the option lines; the Options line is kept out too).

Pinned behaviour and copy:
- Card, above the Answer box: the header `Options · drafted <day month> · Research ↗` (the link opens the doc in the page's plan-doc view; no link when the block has none), a **Draft again** button when `draftView` allows `again` or there is a block and no draft; one row per option with **Use**, the recommended one marked `recommended`; `Asked: <note>` under the header when the block has one.
- No block and no draft: a **Draft options** button and a one-line note field with placeholder `What to look at (optional)`. With `pickups.draftsOff`, the button is disabled and its title is that sentence.
- Queued or working: the tag's words, the text `kandalf attach <KEY>` with a copy control as the session row has, and **Stop**. Rows from an earlier block stay, dimmed.
- `need`, `lost`, `empty`: the label, `why` when there is one, the attach text, **Draft again**.
- **Draft again** reveals the note field and a **Start** button; **Use** fills the Answer box; when the box holds text that differs from every row's `useText`, it asks `Replace what you typed with option <L>?` first.
- Tile face: `draftTag` beside the key where `pickupTag` draws; option lines and the Options line are not drawn in the body; with a block and no draft the tag is `options ready`.
- Both theme families styled with existing tokens; every tile string through `esc`.

- [ ] **Step 1: Write the page tests**: `draftTag` for each state and for no draft and an older server; `optionRows` marks the recommended row and gives `[]` with no block; `useText` for a title ending in `?` and for an empty summary; `faceBody` drops the block's lines and keeps a tick-box sub-task after it; the `view_cases.json` drafts run from Task 2 still pass.
- [ ] **Step 2: Run** `node --test test_page.js` **, see them fail; implement the functions; see them pass.**
- [ ] **Step 3: Render and wire** the card and face as pinned, the `/api/draft` calls (start, again with note, stop) following `decideCard`'s save-guard and error handling, and `pollPickups` keeping `drafts`/`draftsOff`.
- [ ] **Step 4: Commit:** `feat(board): a decision's card drafts options and lists them to pick from`

The controller checks this task in a real browser afterwards (a throwaway store, a hand-written options block, and a draft driven by a stub agent on `PATH`), at two widths and in both theme families.

---

### Task 7: The words, and the full gate

**Files:**
- Modify: `README.md` (the backlog section gains **Draft options**; the tile-attribute table gains the Options line; the `permission` notes for each agent say `plan` can't draft; `kandalf attach`'s line says it finds a draft), `CHANGELOG.md` (the Unreleased lines read as one set), `kandalf/templates/plans.md` and `kandalf/init.py` (the filing rules: a decision can be drafted, and an Options block is the server's to write), `test_init.py` if it pins them.

Pinned row for the README table, after the decision row:

```
| `**Options 2026-11-24:** recommends B. [Research](active/…-options.md)` and `- **A. Title.** summary` lines under it | The options drafted for a decision. The board writes them when a draft's research doc is ready and lists them on the tile's card, each with **Use**. Write the block yourself in the same shape and the card lists it too. |
```

- [ ] **Step 1: Write the docs.** Describe only what was built: one shot, Draft again with a note, attach, the throwaway worktree, one draft at a time, works with pickup off, edits discarded rather than prevented.
- [ ] **Step 2:** `python3 -m kandalf check` on a scratchpad board that carries an options block. Expected: no error.
- [ ] **Step 3: Run both full suites, one at a time** (`python3 -m unittest discover -p "test_*.py"`, then `node --test test_page.js`), memwait before each. Expected: `OK` and `fail 0`. A timeout is reported as a timeout.
- [ ] **Step 4: Commit:** `docs: draft options`
