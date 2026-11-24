# Plan and Build boards: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The backlog board becomes Plan (`plan.md`) and the plans board becomes Build (`build.md`), everywhere a person or an agent reads a name, and `kandalf serve` moves an existing store by itself when no session is mid-turn.

**Architecture:** A store's two board file names become a fact read from its folder (`store.build`, `store.plan`), and every module asks the store instead of a constant, so a store on the old names keeps working unmoved. A new module, `kandalf/renaming.py`, decides when a store may be moved, moves it in one commit, and words what older sessions and the page are told. The page learns the names from the server. Templates, new stores, labels and docs then switch to the new names, and last the tests' fixtures do.

**Tech Stack:** Python 3.10+ standard library, one self-contained `kandalf/board.html`, `unittest` and `node --test`.

**Spec:** `docs/superpowers/specs/2026-11-24-plan-and-build-boards-design.md`. Read it before any task.

## Global Constraints

- Standard library only. Never edit `kandalf/marked.min.js`. The page stays one file.
- The four file names are written in exactly one place, `kandalf/store.py`: `BUILD = "build.md"`, `PLAN = "plan.md"`, `OLD_BUILD = "plans.md"`, `OLD_PLAN = "backlog.md"`. No other module spells a board's file name outside a docstring, a comment or a message to a person.
- A store on the old names must work exactly as it does today until it is moved. In Tasks 1 to 3 no existing test's expectation changes: the existing tests, which build stores with `plans.md` and `backlog.md`, are the proof. Task 4 may change the expectations it names; Task 5 renames fixtures.
- Only a store with a `board.json` is ever moved. A folder without one is never renamed.
- A store never ends with a board under both names: the move renames, it never copies.
- Only the agent modules run an agent; `renaming.py` lists sessions through pickup's `Sessions` adapter.
- Twin rules stay written in both readers with shared cases. A page function a test needs sits between `function parse(` and `// A plan doc (anything` and is exported at the top of `test_page.js`.
- Docstrings, comments and test names are plain sentences. User-visible changes get a line under `## Unreleased` in `CHANGELOG.md`, first in the list.
- Commit by path, subject `feat(scope): …`, with a `Co-Authored-By` trailer. Work only in `.worktrees/plan-build` on `feat/plan-build`. No merge, no push.
- Before every test run: `sh ~/.claude/bin/memwait.sh`; one thing at a time; output through `tail`.

## Review Focus

1. A half-moved store (`build.md` beside `backlog.md`, no `plan.md`): expected `store.plan` is `backlog.md` so no tile is lost from view, and the next look finishes the move. Tests in Tasks 1 and 3.
2. A store on the old names that has its own unrelated `plan.md` or `build.md`: expected it is never moved, keeps working on the old names, and the notice names the file in the way. Tests in Tasks 1 and 3.
3. A link rewrite must not touch prose or other files' links: `[the plan](active/plans.md-notes.md)`, `myplans.md`, a code span `` `plans.md` ``, and a link to `plans.md` in another folder (`../other/plans.md`) are left alone; `[plans.md](plans.md)`, `(../plans.md)`, `(./backlog.md#feature)` are rewritten. Tests in Task 3.
4. A move interrupted after the first rename, or run twice: expected the same end state and one more commit at most, never a lost file. Tests in Task 3.
5. An older page (cached, before the reload) posting to `?file=plans.md` after the move, and a bookmark with it: expected the server answers for the Build board, not a 404. Tests in Tasks 1 and 2.

---

### Task 1: A store says what its boards are called, and the server asks it

**Files:**
- Modify: `kandalf/store.py`, `kandalf/tiles.py`, `kandalf/serve.py`, `kandalf/pickup.py`, `kandalf/drafts.py`, `kandalf/pln.py`, `kandalf/gitlog.py`, `kandalf/checkhook.py`, `kandalf/cli.py`, `kandalf/init.py` (doctor and the "store here already" check only)
- Create: `test_names.py`

**Interfaces:**
- Produces, on `kandalf.store`: the four constants above, and on `Store` (each read from the folder every time it is asked, no caching):
  - `build -> str`: `BUILD` when `dir/BUILD` is a file; else `OLD_BUILD` when `dir/OLD_BUILD` is a file; else `BUILD`.
  - `plan -> str`: `OLD_PLAN` when `build` is `OLD_BUILD`; else `PLAN` when `dir/PLAN` is a file or `dir/OLD_PLAN` is not; else `OLD_PLAN` (the half-moved store).
  - `boards -> tuple` (`build`, `plan`); `family -> tuple` (`build`, `plan`, the archive log).
  - `on_old_names -> bool` (`build == OLD_BUILD`); `half_moved -> bool` (`build == BUILD and plan == OLD_PLAN`).
  - `strays -> list[str]`: on the new names only, `OLD_BUILD` when that file exists, and `OLD_PLAN` when both it and `PLAN` exist; `[]` otherwise.
  - `is_store -> bool`: the folder has a `board.json`.
  - `board(name: str) -> str`: maps a name a caller sent onto the store's present name: `OLD_BUILD` and `BUILD` both give `build`, `OLD_PLAN` and `PLAN` both give `plan`, anything else comes back unchanged.
- `tiles.Snapshot.read(store, names=None)`: `names` defaults to `store.family`. `tiles.PLANS`, `tiles.BACKLOG` and `tiles.FAMILY` stay defined (old names) for the existing tests only; no module uses them.
- In `serve.py`: `PICKUP_BOARD`, `MOVES`, `PULLS` stay as module names only if a test imports them; the code uses `pickup_board(store)`, `moves(store)`, `pulls(store)`, built from the store's names with the same shapes. Every route that takes `?file=` passes it through `store.board(...)` before looking the file up, and `/api/files` adds `"build"` and `"plan"` to its reply.
- `gitlog`, `checkhook` and `pln` take their board lists from the store.

- [ ] **Step 1: Write `test_names.py`**

```python
class Names(unittest.TestCase):
    def test_a_store_with_build_md_is_on_the_new_names(self):
    def test_a_store_with_plans_md_is_on_the_old_names(self):
    def test_an_empty_folder_is_on_the_new_names(self):
    def test_a_half_moved_store_still_reads_its_backlog(self):          # build.md + backlog.md -> plan == "backlog.md", half_moved
    def test_an_old_store_with_its_own_plan_md_stays_on_the_old_names(self):
    def test_the_names_are_read_again_after_the_files_change(self):     # same Store object, files renamed under it
    def test_a_plans_md_beside_build_md_is_a_stray(self):
    def test_a_backlog_md_beside_plan_md_is_a_stray(self):
    def test_an_old_store_has_no_strays(self):
    def test_a_name_a_caller_sends_is_mapped_onto_the_store_s_own(self):  # board("plans.md") on a new store == "build.md", and the reverse
class NewNames(unittest.TestCase):
    """What the old-name tests prove for plans.md and backlog.md, on a store with build.md and plan.md."""
    def test_the_snapshot_reads_the_family(self):
    def test_the_in_flight_tiles_are_the_build_board_s(self):
    def test_pickup_starts_a_tile_that_lands_in_flight_on_build_md(self):
    def test_promote_moves_a_tile_from_plan_md_to_the_top_of_to_do(self):   # serve's move, both directions
    def test_archive_and_decide_work_on_a_tile_of_either_board(self):
    def test_filing_a_domain_and_writing_options_find_tiles_on_both_boards(self):
    def test_the_next_key_counts_both_boards_and_the_archive(self):
    def test_the_edit_hook_checks_the_new_names(self):
    def test_a_request_for_the_old_file_name_is_answered_for_the_new_board(self):  # HTTP: GET /api/board?file=plans.md on a new-names store
    def test_the_files_reply_says_which_file_is_which(self):               # /api/files "build", "plan"
```

- [ ] **Step 2: Run** `python3 -m unittest test_names` **, see it fail; implement; then run the full Python suite and** `node --test test_page.js` **: every existing test passes unchanged.**
- [ ] **Step 3: Check nothing still spells a name:** `grep -n '"plans\.md"\|"backlog\.md"\|"build\.md"\|"plan\.md"' kandalf/*.py` shows only `store.py`'s four constants, the three compatibility names in `tiles.py`/`serve.py`, and `init.py`'s template handling (Task 4's).
- [ ] **Step 4: Commit:** `feat(store): a store says what its two boards are called`

---

### Task 2: The page learns the names and says Plan and Build

**Files:**
- Modify: `kandalf/board.html`, `test_page.js`

**Interfaces:**
- Consumes: `/api/files`'s `build` and `plan`.
- Produces (page, inside the lifted span, exported): `boardNames(reply) -> {build, plan}` (the reply's, else `plans.md`/`backlog.md` for an older server); `boardLabel(name, names) -> string` (`Build` for the build file under either spelling, `Plan` for the plan file under either, `Archive` for the archive log, else the file's name without `.md`); `ownName(name, names) -> string` (maps `plans.md`/`build.md` onto `names.build`, `backlog.md`/`plan.md` onto `names.plan`).
- The page's `PICKUP_BOARD`, `BACKLOG` and `PULLS_INTO` become variables set from `boardNames` when the files list arrives; nothing else in the page spells a board file's name. The `?file=` it starts with goes through `ownName`.

Pinned copy (every place the page shows a board's name):
- Header buttons: **Plan**, **Build**, **Archive**, in that order.
- The Build board's side zone: `Back to Plan` and `Drop a tile on a column to unschedule it. It moves to the top of that column on the Plan board.`, link `Open the Plan board`.
- The Plan board's side zone: `Promote`, `Drop a tile here to schedule it. It moves to the top of To do on the Build board.`, link `Open the Build board`.
- Map legend: `Plan` (was Backlog), `To build` (was Planned), then In flight, Done, Needs you as now. Map chips and panel that say "Backlog" or "Planned" say the same new words.
- Flashes and dialogs that name a board use `the Plan board` / `the Build board`.

- [ ] **Step 1: Write the page tests** for `boardNames` (new server, older server), `boardLabel` (all four spellings, the archive, another file), `ownName` (both directions, an unrelated name), and one that the test harness's boards open under the new names when told them (`page.setNames({build: "build.md", plan: "plan.md"})`, with the existing tests left on the defaults).
- [ ] **Step 2: Run** `node --test test_page.js` **, see them fail; implement; see every test pass, the old ones unchanged.** Check the page's script parses (`node --check` on its extracted inline scripts).
- [ ] **Step 3: Check nothing still spells a name:** `grep -n 'plans\.md\|backlog\.md' kandalf/board.html` shows only the older-server defaults in `boardNames`/`ownName` and comments.
- [ ] **Step 4: Commit:** `feat(board): the page calls the boards Plan and Build`

---

### Task 3: The server moves a store

**Files:**
- Create: `kandalf/renaming.py`, `test_renaming.py`
- Modify: `kandalf/serve.py` (start-up, the watch loop, `pickup_reply`), `kandalf/pickup.py` (the messages the server sends a session), `kandalf/checkhook.py`, `kandalf/init.py` (only to expose what the move needs: the notes update and the templates' headers), `kandalf/board.html` (the notice), `test_page.js`, `CHANGELOG.md`

**Interfaces:**
- Consumes: Task 1's `Store` names; `init`'s notes update; `gitlog.record`; the ledger (`jobs.Ledger`); pickup's `Sessions` adapter (`sessions()`).
- Produces, in `kandalf.renaming`:
  - `relink(text: str) -> str`: markdown link targets that are `plans.md` or `backlog.md`, optionally after `./` or one or more `../`, optionally followed by `#…`, point at `build.md` / `plan.md`; a link's text that is exactly the old file name is renamed too. Nothing else changes.
  - `blocked(store, *, sessions) -> str | None`: why the store can't be moved now (a sentence for the page), or None.
  - `due(store) -> bool`: the store has a `board.json` and is on the old names, or is half-moved, or is on the new names with no record of the move in the ledger and at least one board file.
  - `move(store, *, sessions, record=gitlog.record) -> list[str]`: moves the store when `due` and not `blocked`; returns the lines to log (`[]` when it did nothing).
  - `notice(store, *, sessions) -> dict | None`: `{"waiting": sentence}` while a move is due and blocked, `{"stray": [names], "text": sentence}` when the store has strays, else None.
  - `told(store, job: dict) -> str`: the line for a session whose job started before the store's move, else `""`.
- The ledger gains `state["renamed"] = {"at": <unix seconds>}`; a ledger without it reads as not moved. `kandalf init` writes it for a new store (Task 4).
- `serve.pickup_reply(store)` gains `"renaming": notice(...)`.

Pinned behaviour:
- `blocked`, in this order: a store on the old names that already has a file called `plan.md` → `plan.md is in the way: these boards can't be renamed Plan and Build until it is moved or renamed.`; the ledger holds a job in `jobs.TRACKED` or a draft in state `working`, and the sessions can't be listed → `These boards will be renamed Plan and Build once the agent's sessions can be listed.`; any such job's or draft's session is listed as `busy` → `These boards will be renamed Plan and Build once <KEY>'s session finishes its turn.` (the first busy one's key). Idle and `waiting` sessions do not block. With no tracked job and no working draft the sessions are not listed at all.
- `move`, in order, each step skipped when already done: rename `plans.md` to `build.md`, then `backlog.md` to `plan.md` (`git mv` when the store is a git repo and the file is tracked, else a plain rename); `relink` over the two boards, the archive log and every `*.md` under `active/` and `archive/`, writing only files that change; the notes update `kandalf notes --update` does; a board whose text up to its first `## ` heading equals the old template's header for that board gets the new template's header; then the ledger record, last. One commit through `record` with every path touched, subject `board: plans.md → build.md, backlog.md → plan.md`; a second, finishing run that changes files commits `board: the move to Plan and Build finished`, and one that changes nothing commits nothing.
- The serve loop calls `move` at start-up and at the top of each watch turn, only when `due` (one cheap check). The settle rule: it does not move on a turn where either old board's mtime changed since the previous turn. Its lines go through `pickup.log`.
- `told`: `The boards were renamed since you started: plans.md is now build.md and backlog.md is now plan.md.` It is the first line of every message `pickup` sends a session whose job's `started` is before `renamed["at"]`: a reply, Continue, a nudge, a merge go-ahead, a resume after a model or repos change. A session started after the move, or a store never moved, gets nothing.
- The hook: for an edit to `plans.md` or `backlog.md` that is one of the store's `strays`, `kandalf hook check` hands back `plans.md is now build.md: put your change there and delete plans.md.` (or the `backlog.md`/`plan.md` pair), as it hands back its other errors. For a store on the old names the hook behaves as today.
- The page shows `renaming.waiting` or `renaming.text` as a notice above the board (where the "Pickup is off" notice is drawn; a stray's notice has no close button), and when the names in `/api/files` change under it, it reloads onto `ownName(file)`.

- [ ] **Step 1: Write `test_renaming.py`** (temp stores, real `git` where the test says so, `record` mocked, a fake `Sessions`):

```python
class Relink(unittest.TestCase):
    def test_a_link_to_a_board_points_at_its_new_file(self):            # (plans.md), (./backlog.md#feature), (../plans.md)
    def test_link_text_that_is_the_old_file_name_is_renamed(self):
    def test_prose_code_spans_and_other_files_are_left_alone(self):     # the Review Focus 3 examples
    def test_a_text_with_nothing_to_change_comes_back_identical(self):
class Blocked(unittest.TestCase):
    def test_a_store_with_no_session_is_free_to_move_without_listing_them(self):
    def test_a_busy_session_blocks_and_is_named(self):
    def test_an_idle_or_waiting_session_does_not_block(self):
    def test_sessions_that_cannot_be_listed_block_when_a_job_has_one(self):
    def test_a_working_draft_s_busy_session_blocks(self):
    def test_a_plan_md_of_the_store_s_own_is_in_the_way(self):
class Move(unittest.TestCase):
    def test_a_store_is_moved_in_one_commit_with_its_links_and_notes(self):   # git: both files renamed with history, subject, paths
    def test_a_folder_with_no_board_json_is_never_moved(self):
    def test_a_blocked_store_is_left_exactly_as_it_was(self):
    def test_a_move_stopped_after_the_first_rename_is_finished_on_the_next_run(self):
    def test_a_second_run_changes_nothing_and_commits_nothing(self):
    def test_an_untouched_header_is_replaced_and_an_edited_one_is_kept(self):
    def test_plan_docs_in_active_and_archive_are_relinked(self):
    def test_a_store_that_is_not_a_git_repo_is_moved_by_plain_renames(self):
    def test_the_ledger_records_the_move_last(self):
class Told(unittest.TestCase):
    def test_a_session_started_before_the_move_is_told_first(self):     # through pickup.reply's text and the nudge
    def test_a_session_started_after_it_is_told_nothing(self):
    def test_a_store_never_moved_tells_nothing(self):
class Notice(unittest.TestCase):
    def test_a_blocked_move_says_what_it_waits_for(self):
    def test_a_stray_old_file_is_named(self):
    def test_a_settled_store_has_no_notice(self):
class Hook(unittest.TestCase):
    def test_an_edit_that_brings_plans_md_back_is_handed_back_with_where_to_put_it(self):
    def test_an_old_names_store_s_plans_md_is_checked_as_before(self):
```

and in `test_settings.py`'s `Watch`: a due store is moved at the top of a turn; a store whose board changed since the previous turn waits a turn; a store not due costs one check and no listing. Page tests: the notice's text for `waiting` and for `stray`.

- [ ] **Step 2: Run, see them fail; implement; run** `python3 -m unittest test_renaming test_settings test_pickup test_init` **, then both full suites: every existing test passes unchanged.**
- [ ] **Step 3: Commit:** `feat(serve): a store's boards are renamed Plan and Build when no session is mid-turn`

The controller then moves a copy of a real store by hand (a throwaway copy, never the original): start `kandalf serve` on it, check the commit, the links, the notes, the page's reload, a blocked move with a busy stand-in session, and a stray file's notice and hook message.

---

### Task 4: New stores, and the words

**Files:**
- Modify: `kandalf/templates/` (`plans.md` → `build.md`, `backlog.md` → `plan.md`, with `git mv`; their headers; `pickup.md` if it names a board), `kandalf/init.py` (new stores, the agent note, the filing rules, `notes`), `kandalf/gitlog.py` (commit subjects), `kandalf/pickup.py` and `kandalf/drafts.py` (what a session is told), `kandalf/cli.py` (help), `pyproject.toml` only if it lists template files by name, `README.md`, `AGENTS.md`, `CHANGELOG.md`, and the tests that pin these words (`test_init.py`, and any other the grep finds)

Pinned behaviour:
- `kandalf init` makes `plan.md` and `build.md` and writes the ledger's `renamed` record, so a new store is never "due".
- Every sentence the server writes for a person or an agent says the store's present file names (`store.build`, `store.plan`) and "the Plan board" / "the Build board": the prompts in `pickup.py` and `drafts.py`, the agent note and filing rules in `init.py`, commit subjects (`board: PLN-26 plan.md → To do`), the console's lines, `kandalf --help`.
- The templates' headers say Plan and Build. `kandalf notes --update` offers the changed rules to an existing store the way it does today (a rule whose bold lead a store lacks is added; the agent note is replaced when its text differs).
- README: every "backlog board", "plans board", `backlog.md` and `plans.md` becomes the new name, the module list and the config table included; a short part, **Coming from 1.2**, says what `kandalf serve` does to an existing store the first time (the rename, when it waits, the commit, and what to do if an old file comes back), and that `~/.claude` rules or notes of the reader's own that name the old files need a hand edit. AGENTS.md's ground rules name the new files. CHANGELOG: this is the first entry under Unreleased.
- This task may change the expectations of tests that pin the words above (say which in the report). It changes no test of behaviour.

- [ ] **Step 1: Find what pins the words:** `grep -n "backlog\|plans board\|plans\.md" test_*.py | grep -v "write_text\|\"## " | head -60`, and read `test_init.py`'s notes tests.
- [ ] **Step 2: Make the changes; update the pinned-word tests; add tests** that a new store has the new files and the record, that a started session's prompt names `build.md`, and that a commit subject names the new file.
- [ ] **Step 3: Run both full suites.** `python3 -m kandalf check kandalf/templates/plan.md kandalf/templates/build.md`.
- [ ] **Step 4: Commit:** `feat: new stores have plan.md and build.md, and everything says Plan and Build`

---

### Task 5: The tests speak the new names

**Files:**
- Modify: `test_*.py`, `test_page.js`, `tile_cases.json` and `view_cases.json` where a file name appears, `kandalf/tiles.py` and `kandalf/serve.py` (drop the compatibility names)

Pinned behaviour:
- Every fixture that builds a store or a board uses `build.md` and `plan.md`, and every assertion that names a file names the new one, except in three named places that stay on the old names on purpose: `test_names.py`'s old-name tests, `test_renaming.py`, and one class per large suite, `OldNames`, in `test_pickup.py`, `test_tiles.py` and `test_page.js`, holding a handful of end-to-end tests on a `plans.md`/`backlog.md` store (a tile picked up, a tile promoted, the snapshot's family, the page opening both boards).
- `tiles.PLANS`, `tiles.BACKLOG`, `tiles.FAMILY` and serve's `PICKUP_BOARD`, `MOVES`, `PULLS` are deleted; nothing imports them.
- No test is deleted and no assertion is weakened: the count of tests does not go down.

- [ ] **Step 1: Record the counts** (`python3 -m unittest discover -p "test_*.py" 2>&1 | tail -3`, `node --test test_page.js | grep "^ℹ tests"`).
- [ ] **Step 2: Rename, file by file,** running that file's tests after each.
- [ ] **Step 3: Run both full suites:** the same number of tests or more, all passing. `grep -c "plans\.md\|backlog\.md" test_*.py test_page.js` shows mentions only in the three named places.
- [ ] **Step 4: Commit:** `test: fixtures use plan.md and build.md`
