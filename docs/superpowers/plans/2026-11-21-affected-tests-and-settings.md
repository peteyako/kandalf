# Affected tests, full suite on merge, and a settings panel: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every story's session runs only the tests for what it changes. The full suite runs on merge only when `fullSuiteOnMerge` or the tile's `Tests: full` asks for it. A ⚙ panel on the board edits the store's settings live.

**Architecture:**
- **Python side.** `pickup.py` gains the `Tests:` line (twin regex in `board.html`), one tests paragraph for every prompt, and a merge-time decision feeding the existing "suite" step. `store.py` gains `full_suite_on_merge` and an in-place `reload()`. A new `kandalf/settings.py` describes, validates and writes the editable settings.
- **Server.** `serve.py` adds `/api/settings`. Its pickup watch thread always runs, so a setting takes effect without a restart.
- **Page.** `board.html` adds the dialog checkbox, the tile chip and the settings panel.

**Tech Stack:** Python 3.10+ stdlib (`http.server`, `json`, `subprocess`), one-file HTML/JS page, `unittest`, `node --test`.

**Spec:** `docs/superpowers/specs/2026-11-21-affected-tests-and-settings-design.md`

## Global Constraints

- Standard library only; no new dependency in `pyproject.toml`. Never edit `kandalf/marked.min.js`.
- Two readers of one format: `pickup.TESTS_RE` and `TESTS_RE` in `board.html` change together, each commented with its twin ("same in board.html").
- `fullSuiteOnMerge` defaults to `false`; a store that sets nothing never runs the full suite.
- `subStoryTests` is retired: read by nothing; `kandalf doctor` says so.
- "The full suite" keeps today's wording: every test tier the repo has.
- Read-only in the panel: `name`, `key`, `port`, `link`, `app`, `repos`, `pickupColumn`, `branch`, `worktree`, `hooks`.
- Before each test run: `sh ~/.claude/bin/memwait.sh`. Python: `python3 -m unittest discover -p "test_*.py"`; page: `node --test test_page.js`.
- Work in a worktree (`git worktree add .claude/worktrees/affected-tests -b feat/affected-tests-settings dev`); commit by path (`git add -- <files> && git commit -m … -- <files>`), subjects `feat(pickup): …` / `feat(board): …`, lower case after the colon, ending with the `Co-Authored-By` line.
- Each user-visible change gets a CHANGELOG line under `## Unreleased` (start the section above `## 1.1.10`). The README's config table and tile-line table change with the code.
- Docstrings, comments and test names are plain sentences about what a thing is or does (AGENTS.md).

## Review Focus

1. **A hand edit that breaks `board.json` while the server runs.** The old settings stay in force, the panel shows the parse error, and nothing is written. Tested in Task 4.
2. **A panel save on a `board.json` with keys the panel doesn't show,** including unknown ones. Every other key survives with its value. Tested in Task 5.
3. **Two tabs saving settings one after the other.** The second gets 409 and the current settings, not a silent overwrite. Tested in Task 6.
4. **Pickup turned on from the panel while tiles already sit in In flight with no session.** They are picked up as at the server's start (`adopt`). Turning pickup off leaves running sessions alone. Tested in Task 6.
5. **`Tests:` written loosely** (`tests: FULL.`, backticks, only on the master). It is still read, and a sub-story inherits its master's line. Tested in Tasks 2 and 3.

---

### Task 1: The tests paragraph for every story, and the default Gates wording

**Files:**
- Modify: `kandalf/pickup.py:400-410` (`sub_story_tests` → `tests_paragraph`), `kandalf/pickup.py:461` (its call in `prompt`)
- Modify: `kandalf/templates/pickup.md:2`
- Test: `test_pickup.py` (the `SUB_STORY_TESTS` golden fragment and the two tests that set `subStoryTests: "all"`, lines ~245 and ~953)

**Interfaces:**
- Produces: `pickup.tests_paragraph(job: dict) -> str`. It is never empty: every prompt gets it.

- [ ] **Step 1: Write the failing tests**

`test_every_story_is_told_to_run_only_the_tests_for_what_it_changes`:
- A plain story's prompt contains "run only the tests that cover what it changes".
- It contains "stands in for any rule, in pickup.md or CLAUDE.md, that asks for every test tier".
- It contains "The full suite isn't yours to run: it runs nightly, and after a merge from the board when the store or the tile asks for it".

`test_a_sub_story_is_told_the_same_and_named_as_its_master_s`:
- A sub-story's prompt contains the same sentences, plus "{key} is a sub-story of {master}".

`test_the_default_rules_gate_on_the_tests_for_what_changed`:
- `templates/pickup.md` contains "`- [ ] **Gates**: lint, the tests for what it changes, and the review`".
- It does not contain "every test tier".

Delete the two `subStoryTests: "all"` tests. Their behaviour is retired.

- [ ] **Step 2: Run** `python3 -m unittest test_pickup`. Expected: the three new tests FAIL.

- [ ] **Step 3: Implement `tests_paragraph(job)` in `kandalf/pickup.py`**

It replaces `sub_story_tests`, has no store check, and is called the same way from `prompt`. Change the template's Gates line to the wording above.

- [ ] **Step 4: Run** `python3 -m unittest test_pickup`. Expected: PASS.

- [ ] **Step 5: Commit** with `feat(pickup): every story runs only the tests for what it changes`, including a CHANGELOG line.

### Task 2: The `Tests:` line and the merge decision

**Files:**
- Modify: `kandalf/pickup.py` (new `TESTS_RE` beside `BASE_RE` at line 74, `tests_named`, `full_suite_on_merge`; `reply_text` around lines 1126-1145; `reply` passes `texts` through)
- Modify: `kandalf/store.py:146` (drop `sub_story_tests`, add `full_suite_on_merge`), the docstring at lines 46-48
- Modify: `kandalf/serve.py` (the `/api/pickup` reply route passes `texts=`; a `Handler.texts(board)` beside `Handler.tiles`)
- Modify: `kandalf/init.py` (`doctor`: a line when `board.json` has `subStoryTests`)
- Test: `test_pickup.py`, `test_init.py`

**Interfaces:**
- Consumes: `still_to_merge(store, job, tiles)` (exists); `_cuts_of(store, job)` (exists).
- Produces:
  - `pickup.TESTS_RE = re.compile(r"^[ \t]+tests:[ \t]*`?(full|affected)`?\.?[ \t]*$", re.I | re.M)  # same in board.html`
  - `pickup.tests_named(text: str) -> str | None`: `"full"`, `"affected"`, or None. The first matching line wins.
  - `pickup.full_suite_on_merge(store, key: str, texts: dict | None, tiles: dict | None) -> bool`. It walks `key` then its masters (`tiles[k]["parent"]`), and the nearest `Tests:` line wins. With none it returns `store.full_suite_on_merge`, and with `texts` None it returns that too.
  - `Store.full_suite_on_merge: bool`, from `board.json` `fullSuiteOnMerge`, default `False`.
  - `Handler.texts(board: Path) -> dict`, giving `{key: tile markdown}` over `FAMILY`, the first board's tile winning, as `tile_index` does.
  - `reply_text(..., tiles=None, texts=None)` and `reply(..., tiles=None, texts=None)`.

- [ ] **Step 1: Write the failing tests** (`test_pickup.py`, class `Reading` unless noted)

`test_a_tests_line_says_full_or_affected_in_any_case`:
- `tests_named("X\n  tests: FULL.")` is `"full"`.
- ``"  Tests: `affected`"`` gives `"affected"`.
- `"  Tests: some"` gives None.
- No line gives None.

`test_the_nearest_tests_line_decides_and_the_store_s_setting_is_the_fallback`:
- A sub-story with no line under a master with `Tests: full` gives True.
- The same sub-story with its own `Tests: affected` gives False.
- With no lines it gives `store.full_suite_on_merge`, both False and True. Set it through `board.json` `{"fullSuiteOnMerge": true}` and a fresh `Store`.

`test_a_story_s_merge_runs_the_full_suite_only_when_asked` (class `Queue`, using `self.reply`):
- A merge reply for a plain story with no line and the default setting contains no "Full suite".
- The same with `texts={"PLN-01": tile with "  Tests: full"}` contains "Run the full suite on main, every test tier this repo has" and "- [ ] **Full suite** on main". Its steps run merge, suite, then push.

`test_a_sub_story_s_merge_runs_the_full_suite_only_when_it_is_the_last_and_asked`:
- Adapt today's last-sub-story test: the suite step appears only when the master's tile says `Tests: full` (or the store's setting is on).
- An earlier sub-story's merge says the suite waits.
- With neither, no suite text at all.

`test_a_story_in_two_repos_runs_the_full_suite_in_each`:
- A job with two cuts gives one "**Full suite** on <name> <trunk>" sub-task per repo.

`test_doctor_says_sub_story_tests_is_retired` (`test_init.py`):
- With `"subStoryTests": "all"` in `board.json`, doctor's output contains "subStoryTests is retired" and "fullSuiteOnMerge".

- [ ] **Step 2: Run** `python3 -m unittest test_pickup test_init`. Expected: the new tests FAIL.

- [ ] **Step 3: Implement**
- `TESTS_RE`, `tests_named` and `full_suite_on_merge` as in the Interfaces block. `Store.full_suite_on_merge = bool(cfg.get("fullSuiteOnMerge", False))`, and remove `sub_story_tests`.
- In `reply_text`: when `"merge" in actions`, compute `full_suite_on_merge(store, key, texts, tiles)`.
  - A sub-story (`master`): keep today's last, waits and no-index branches, gated on that boolean instead of `sub_story_tests`.
  - A story that isn't a sub-story: add the suite step on yes. The tile it ticks is its own.
  - Several cuts: one suite sentence naming every repo and its trunk, and one sub-task line per repo.
- `serve.py`'s reply route passes `texts=self.texts(self.plans / "plans.md")`.

- [ ] **Step 4: Run** `python3 -m unittest discover -p "test_*.py"`. Expected: PASS.

- [ ] **Step 5: Commit** with `feat(pickup): the full suite runs on merge only when the store or the tile's Tests line asks`. Include in the commit:
  - README: config table rows for `fullSuiteOnMerge`, and for `subStoryTests` (removed); the `Tests: full` row in the tile-line table; the "Several at once" paragraph's suite sentence.
  - CHANGELOG.

### Task 3: The tile's Tests checkbox and chip

**Files:**
- Modify: `kandalf/board.html`:
  - `TESTS_RE` beside `BASE_RE`, line 1451;
  - `testsOf` and `withTests` beside `baseOf` and `withBase`, lines 1478-1495, inside the span `test_page.js` lifts;
  - `storyParts` reads it, line 1599;
  - a `testsTag` chip beside `baseTag`, line 1253, used in the key line, line 1780;
  - the dialog checkbox beside `#cardBaseLabel`, line 939;
  - `syncTests` called from `syncPrio`, lines 2493-2505;
  - `listFiles` sets `STORE_FULL_SUITE`, line 2630.
- Modify: `kandalf/serve.py` (`/api/files` adds `"fullSuiteOnMerge": self.store.full_suite_on_merge`)
- Test: `test_page.js` (export `testsOf`, `withTests`, `setFullSuite`)

**Interfaces:**
- Consumes: the `Tests:` line format from Task 2.
- Produces:
  - `testsOf(md) -> "full" | "affected" | ""`: the tile's own line only.
  - `withTests(md, full: boolean, inherited: "full" | "affected" | "")`. It writes `  Tests: full` or `  Tests: affected`, where `withBase` would put a Base line. It removes the line when `full` equals what the tile gets without it (`inherited` if set, else `STORE_FULL_SUITE`).
  - Global `STORE_FULL_SUITE` (boolean).

- [ ] **Step 1: Write the failing test** `test("a Tests line asks for the full suite on merge; withTests writes it only when it differs from what the tile gets anyway", …)`. Its assertions, using `setFullSuite(false)` first:
  - `testsOf("PLN-01 **One.**\n  tests: FULL.")` is `"full"`.
  - `withTests(md, true, "")` puts `  Tests: full` before the first sub-task.
  - `withTests(that, false, "")` removes it.
  - `withTests(md, false, "full")` writes `  Tests: affected`.
  - After `setFullSuite(true)`, `withTests(md, true, "")` leaves `md` unchanged.

- [ ] **Step 2: Run** `node --test test_page.js`. Expected: FAIL (`testsOf` is not a function).

- [ ] **Step 3: Implement the functions and the dialog**
- The dialog control is `<label id="cardTestsLabel" title="…"><input type="checkbox" id="cardTests"> Full suite on merge</label>`.
- It starts checked by `testsOf(tile) || inheritedTests || (STORE_FULL_SUITE ? "full" : "")`. The inherited value is the nearest master's `Tests:` line, walking the parent chain of the tile index (`serverTiles[key].parent`) and reading each master's text from the open board's `doc` when it is there. A master on another board counts as no line; the server's decision at merge (Task 2) is the one that holds.
- On change it rewrites `#cardText` with `withTests`.
- The chip `🧪 full suite` shows only for the tile's own `full`, with the title "Runs the full suite on <trunk> when it is merged from the board".
- Hide the label on the archive, as Base does.

- [ ] **Step 4: Run** `node --test test_page.js` and `python3 -m unittest test_pickup`. Expected: PASS.

- [ ] **Step 5: Commit** with `feat(board): a tile's dialog asks for the full suite on merge`, including the README tile-line wording.

### Task 4: Reloading the store's settings in place

**Files:**
- Modify: `kandalf/store.py` (`Store.reload`, `Store.reload_if_changed`, `Store.config_problem`, `Store.config_mtime`)
- Test: `test_settings.py` (new)

**Interfaces:**
- Produces:
  - `Store.reload() -> bool`. It re-reads `board.json` into the same object (re-running the `__init__` body for `self.dir`) and returns True. On a JSON error it leaves every attribute as it was, sets `config_problem` to `"board.json doesn't parse: <error>"`, and returns False. A good read clears `config_problem`.
  - `Store.reload_if_changed() -> bool`, which reloads when `board.json`'s `st_mtime_ns` differs from `config_mtime`.
  - `Store.config_mtime: int | None`, set by each read.

- [ ] **Step 1: Write the failing tests** (class `Reload`)
- `test_a_store_reads_board_json_again_in_place`: write `{"parallel": 1}`, build a `Store`, write `{"parallel": 3, "fullSuiteOnMerge": true}`, then `reload()`. The same object now has `parallel == 3` and `full_suite_on_merge is True`.
- `test_a_board_json_that_does_not_parse_keeps_the_settings_in_force`: after `{"parallel": 2}`, write `{"parallel": `. `reload()` is False, `parallel == 2`, and `config_problem` starts "board.json doesn't parse". Writing valid JSON again and reloading clears it.
- `test_reload_if_changed_reloads_only_after_a_write`: False with no write, True after one (bump the mtime with `os.utime`).

- [ ] **Step 2: Run** `python3 -m unittest test_settings`. Expected: FAIL.

- [ ] **Step 3: Implement** in `kandalf/store.py`. Move the config parsing into `_read(self, cfg: dict)`, called by `__init__` and `reload`. The JSON parse happens before any attribute changes.

- [ ] **Step 4: Run** `python3 -m unittest discover -p "test_*.py"`. Expected: PASS.

- [ ] **Step 5: Commit** with `feat(store): board.json can be read again while the server runs`.

### Task 5: The settings module: describe, validate, write

**Files:**
- Create: `kandalf/settings.py`
- Test: `test_settings.py`

**Interfaces:**
- Consumes: `Store.reload()` (Task 4); `init.read_config`, `init.write_config(st, cfg, message)`, `init.set_agent(st, name, force=False)` (exists; raises `SystemExit` with its message while sessions are live); `agent.default_model()`, `agent.models()`; `stores.default_trunk(app)`, `stores.has_branch(repo, name)`.
- Produces:
  - `settings.EDITABLE: dict[str, dict]`, keyed by the `board.json` name, each `{"kind": "bool"|"int"|"choice"|"text", "min": int?, "choices": list?, "section": "Pickup"|"Tests"|"Git and commits", "label": str}`. In this order:
    - `pickup`, `agent` (choices `["claude", "copilot"]`), `model`, `permission` (choices `["auto", "acceptEdits", "manual", "dontAsk", "plan", "bypassPermissions"]`), `parallel` (min 1), `sessions` (min 1);
    - `fullSuiteOnMerge`;
    - `trunk`, `tidy`, `push`, `commitDelay` (min 0).
  - `settings.view(store) -> dict`, `{"mtime": str, "set": {key: raw value}, "effective": {key: value}, "defaults": {key: value}, "fields": EDITABLE, "readonly": {name, key, port, folder, app, repos: [{name, trunk}], link, pickupColumn, branch, worktree, hooks}, "problem": store.config_problem or pickup.why_off(store) or None}`.
    - `set` covers only the editable keys `board.json` holds.
    - The defaults are derived for this store: `trunk` is `stores.default_trunk(store.app)` and `model` is `agent.default_model()`. The rest are the constants `store.py` uses.
    - `mtime` is `serve.mtime(board.json)`'s format; import it lazily or move `mtime` into `store.py` and re-export.
  - `settings.SettingsError(Exception)`: its message is one sentence for the page.
  - `settings.apply(store, changes: dict) -> list[str]`.
    - It validates every change before writing any. Errors: a key not in `EDITABLE`; a wrong type; under `min`; not in `choices`; `model` empty or containing whitespace; `trunk` failing `BRANCH_NAME` (letters, digits, `._/-`) or, when the store has an app, not a branch there.
    - `None` removes the key.
    - An `agent` change goes through `init.set_agent` first, and a `SystemExit` becomes a `SettingsError` with its text. The rest are written with one `write_config` call, commit message `"settings: " + ", ".join(sorted(keys))`, keeping every other key.
    - It then calls `store.reload()` and returns one line per change.

- [ ] **Step 1: Write the failing tests** (class `Settings`, real temp store, no app unless noted)
- `test_the_view_says_what_is_set_what_is_in_force_and_the_defaults`: with `{"parallel": 3, "name": "X"}`:
  - `view()["set"] == {"parallel": 3}`;
  - `effective["parallel"] == 3`;
  - `defaults["parallel"] == 1`;
  - `defaults["fullSuiteOnMerge"] is False`;
  - `readonly["name"] == "X"`.
- `test_a_change_is_written_with_every_other_key_kept`: with `{"name": "X", "custom": {"a": 1}, "parallel": 1}`, `apply({"parallel": 2, "fullSuiteOnMerge": True})` leaves `board.json` equal to `{"name": "X", "custom": {"a": 1}, "parallel": 2, "fullSuiteOnMerge": True}`, and `store.parallel == 2`.
- `test_null_resets_a_setting_to_its_default`: `apply({"parallel": None})` removes the key, and `store.parallel == 1`.
- `test_a_bad_change_writes_nothing`. Each of these raises `SettingsError`, and `board.json` is unchanged:
  - `{"parallel": 0}`, `{"parallel": "2"}`;
  - `{"permission": "yolo"}`, `{"port": 9}`, `{"model": "a b"}`;
  - `{"parallel": 2, "bogus": 1}`.
- `test_the_trunk_must_be_a_branch_of_the_app` (with a real git app holding `dev`): `trunk: "nope"` raises, and `trunk: "dev"` is written.
- `test_switching_the_agent_is_refused_while_a_session_is_live`: with a pickup state holding a `started` job, `apply({"agent": "copilot"})` raises with "has a claude session".

- [ ] **Step 2: Run** `python3 -m unittest test_settings`. Expected: FAIL (no module).

- [ ] **Step 3: Implement `kandalf/settings.py`** per the Interfaces block. Its module docstring names it as the one place the panel's rules live.

- [ ] **Step 4: Run** `python3 -m unittest discover -p "test_*.py"`. Expected: PASS.

- [ ] **Step 5: Commit** with `feat(settings): describe, check and write the store's editable settings`.

### Task 6: The settings API and live pickup

**Files:**
- Modify: `kandalf/serve.py`:
  - `do_GET` and `do_POST` gain `/api/settings`;
  - `main` (line ~1155) always starts `watch_pickups`;
  - `watch_pickups` (line 520) checks `store.reload_if_changed()` and the pickup switch each turn.
- Test: `test_settings.py` (HTTP, with the server-in-a-thread pattern in `test_clear.py:150-180`)

**Interfaces:**
- Consumes: `settings.view`, `settings.apply`, `settings.SettingsError` (Task 5); `Store.reload_if_changed` (Task 4); `pickup.adopt`, `pickup.why_off` (exist).
- Produces:
  - `GET /api/settings` gives 200 and `settings.view(store)`. It calls `store.reload_if_changed()` first.
  - `POST /api/settings {mtime, changes}`:
    - a stale `mtime` gives 409 `{"error": "changed on disk", **view}`;
    - a `SettingsError` gives 400 `{"error": msg, **view}`;
    - success gives 200 `{"said": [lines], **view}`.
  - `watch_pickups(store, every=2.0)`:
    - **each turn:** `store.reload_if_changed()` under `pickup._lock`.
    - **pickup off:** while `store.pickup` is false it does no look.
    - **off to on:** on a false-to-true change it calls `pickup.why_off(store)`. If that gives None, it runs `pickup.adopt` (logging as at start) before its next look. If it gives a reason, it logs it once.
  - `main` starts `watch_pickups` whatever `why_off` says. `report_in_flight` stays its first act.

- [ ] **Step 1: Write the failing tests**
- `test_the_settings_route_saves_a_change_and_the_server_uses_it`: GET, then POST `{mtime, changes: {"parallel": 2}}` gives 200. `Handler.store.parallel == 2`, and the returned `effective.parallel == 2`.
- `test_a_save_against_an_old_copy_is_refused`: two POSTs with the same `mtime` give 200, then 409. The 409 body's `set` shows the first save.
- `test_a_bad_value_is_refused_with_why`: `{"parallel": 0}` gives 400, and `error` mentions "parallel".
- `test_pickup_turned_on_picks_up_tiles_already_in_flight`. Drive one turn of the watch loop directly: factor the loop body into `watch_turn(store, ctx) -> None` with `ctx` holding `seen, last, told, before, was_on`. Patch `pickup.why_off` to None and `pickup.tick` and `pickup.adopt` to recorders. Write `{"pickup": true}`, run a turn, and `adopt` has been called once.
- `test_pickup_turned_off_leaves_sessions_alone`: after `pickup: false`, a turn calls neither `tick` nor `agent.stop`.

- [ ] **Step 2: Run** `python3 -m unittest test_settings`. Expected: FAIL.

- [ ] **Step 3: Implement** the routes and the loop change, with `watch_turn` as above. `watch_pickups` is then a `while True` around it.

- [ ] **Step 4: Run** `python3 -m unittest discover -p "test_*.py"`. Expected: PASS.

- [ ] **Step 5: Commit** with `feat(serve): /api/settings, and pickup follows board.json without a restart`.

### Task 7: The settings panel

**Files:**
- Modify: `kandalf/board.html`:
  - a `⚙` button `#settingsBtn` in `#themePick`'s row;
  - `<dialog id="settingsDlg">` after `#askDlg`;
  - its CSS beside the dialog rules, in classic and the non-classic layer;
  - `settingsHTML(view)` and `settingsChanges(view, form)` inside the span `test_page.js` lifts;
  - `openSettings()` and `saveSettings()` outside it.
- Modify: `README.md` (a **Settings** section after **Base**), `CHANGELOG.md`
- Test: `test_page.js` (export `settingsHTML`, `settingsChanges`)

**Interfaces:**
- Consumes: `GET` and `POST /api/settings` (Task 6).
- Produces:
  - `settingsHTML(view) -> string` renders:
    - **one section per `fields[k].section`**, in `EDITABLE` order. Each row is `data-key=k` and holds:
      - the label;
      - the control: a checkbox for bool, `<input type=number min>` for int, `<select>` for choice, `<input list>` for text (model suggestions from `MODELS`, trunk suggestions from `STORE_BRANCHES`);
      - "default: X";
      - a `changed` class and a `Reset` button (`data-reset=k`) when `k in view.set`.
    - **an "About this store" section** with the read-only values, and the note "Set in board.json".
    - **`view.problem`**, when there is one, as a note at the top.
  - `settingsChanges(view, form) -> {key: value | null}`: only the keys whose control differs from `view.effective`, typed by kind. A pending reset gives null.
  - Saving POSTs `{mtime: view.mtime, changes}`. Then:
    - on 200, re-render from the reply, re-run `listFiles()` and `pollPickups()`, and flash the `said` lines;
    - on 409, re-render from the reply and flash "board.json changed on disk: here it is as it is now";
    - on 400, flash the error and leave the form as typed.

- [ ] **Step 1: Write the failing test** `test("the settings panel shows each setting with its default, marks the changed ones, and sends only what changed", …)`. Use a fixed `view` with `set: {parallel: 3}`, `effective: {parallel: 3, pickup: true, …}`, `defaults: {parallel: 1, …}`. Assert that:
  - the HTML has a row `data-key="parallel"` with class `changed`, "default: 1" and a `data-reset="parallel"`;
  - `pickup` has no `changed`;
  - the read-only `name` appears;
  - with a jsdom-free fake form (an object whose `querySelector` returns `{value, checked}` per key), changing only `parallel` to 2 gives `settingsChanges` = `{parallel: 2}`.

- [ ] **Step 2: Run** `node --test test_page.js`. Expected: FAIL.

- [ ] **Step 3: Implement** the panel. It must work in every theme, including classic: use the existing `.dlg` and `.row` styles and tokens only.

- [ ] **Step 4: Run** `node --test test_page.js` and the Python suite. Expected: PASS. Then check in a browser:
  1. Serve a throwaway store (`python3 -m kandalf serve --dir <tmp> --port 8799 --no-open`) and open it with agent-browser.
  2. Open ⚙, change `parallel` to 2 and save. `board.json` now holds `"parallel": 2`, and the row shows changed.
  3. Reset it. The key is gone.
  4. Repeat steps 2 and 3 in the classic theme and one other.

- [ ] **Step 5: Commit** with `feat(board): a settings panel for the store's board.json`, including the README **Settings** section and the CHANGELOG line.

### Task 8: Whole-branch check

- [ ] **Step 1:** `sh ~/.claude/bin/memwait.sh`. Run both suites in full, then `python3 -m kandalf check plans.md` against a throwaway store.
- [ ] **Step 2:** Run the final whole-branch review (Opus, fresh, not a fork) on `git diff dev...feat/affected-tests-settings`. Fix what it confirms.
- [ ] **Step 3:** Report to the user. Merging waits for their "merge it".
