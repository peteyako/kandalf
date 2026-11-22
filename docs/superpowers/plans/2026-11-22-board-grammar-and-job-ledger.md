# Board grammar and job ledger: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One Python tile reader, one case file both readers run, and one pickup job ledger with a sessions adapter, with no change to prompts, routes or board.json.

**Architecture:**
- `kandalf/tiles.py` takes every board-grammar regex and walker from serve, pickup, gitlog, pln and store, behind a parse/snapshot/edit interface.
- `tile_cases.json` pins the grammar for both readers.
- `kandalf/jobs.py` takes the state file, its lock and the job transitions out of pickup. pickup keeps its policy (prompts, review replies, queue order) and drives sessions through one adapter object.

**Tech Stack:** Python 3.10+ stdlib, `unittest`, the one-file page, `node --test`.

**Spec:** `docs/superpowers/specs/2026-11-22-board-grammar-and-job-ledger-design.md`. The evidence and line refs are in `docs/superpowers/specs/2026-11-22-architecture-findings.md`.

## Global Constraints

- Standard library only. Never edit `kandalf/marked.min.js`.
- Behaviour stays as it is except the six decisions in the spec's "Behaviour decisions". Prompts must stay byte for byte: test_pickup's golden prompt tests must not change.
- Every task ends with the full gate green:
  1. `sh ~/.claude/bin/memwait.sh`
  2. `python3 -m unittest discover -p "test_*.py"`: 368 baseline tests, 1 skipped, plus new ones.
  3. `node --test test_page.js`: 55 baseline tests, plus new ones.
- A test is deleted only when its case moves into `tile_cases.json` or into a test of the new module. Say which, in the commit body.
- Two readers of one format: a rule changed in Python changes in board.html in the same commit, and the reverse.
- Docstrings, comments and test names are plain sentences (AGENTS.md).
- Commit by path, subjects `refactor(tiles): …`, `refactor(jobs): …`, `fix(pickup): …`, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. CHANGELOG lines under `## Unreleased` only for the user-visible decisions.
- Work in `.claude/worktrees/deepen`, on branch `refactor/deepen-grammar-and-ledger`.

## Review Focus

1. **A board file with CRLF line endings, a tile with no key, or a tile under no `## ` column.** It parses as today (CRLF tolerated as the page does), and edits round-trip byte for byte. Owned by Task 1.
2. **An archived master** (only in the archive log) whose sub-story is in flight. The snapshot still finds it for lineage, context and the nearest `Tests:` line. Owned by Task 2.
3. **An old `board-pickup.json`**: jobs with no `model`, a bare `worktree`, closed-early "ended", or no `inflight` key. It loads and ticks. Owned by Task 4.
4. **`kandalf agent` run while the server ticks.** Neither process's save is lost; a cross-process test proves it. Owned by Task 4.
5. **A page opened before the server sends the ledger's view** (an older server). The page falls back to deriving labels as today. Owned by Task 6.

---

### Task 1: The tile reader module

**Files:** create `kandalf/tiles.py` and `test_tiles.py`; add `tiles.py` to the README module list.

**Interfaces (produces):**
- `Tile`: a frozen dataclass with `key: str | None`, `column: str | None`, `start: int`, `end: int` (line range, trailing blanks excluded, as `serve.board_tiles` today), `text: str`, `title: str`, `parent: str | None` (its first `Part of` that isn't itself), `links: list[str]`, `mentions: list[str]`, `done: bool` (in a Done column, or on the archive log).
- `parse(store, text: str, *, file: str = "plans.md") -> list[Tile]`: the one walker. A tile line starts `- ` or `* `, and its key is read the same way in both.
- Attribute lines. One function each, each taking a tile's markdown and returning the value or None. Each uses the regex moved from pickup, with its "same in board.html" comment kept:
  - `model(md)`, `base(md)`, `tests(md)` (`"full"`/`"affected"`), `repos(md)` (a list, or None), `sessions(md)` (an int);
  - `ready(md)` (the note, `""`, or None), `opted_out(md)`.
- Session line:
  - `session(md) -> SessionLine | None`, where `SessionLine` has `agent`, `id`, `branch` and `cuts: list[(repo, worktree)]`. It reads both the one-worktree and the several-worktrees forms, and only an indented line of its own counts.
  - `with_session(store, board_text, key, line) -> str`, and `with_status(store, board_text, key, line) -> str`, both moved from serve.
- Edits, moved from serve: `take(store, text, keys) -> (text_without, [tile_md])`, `insert_after(...)`. Keep the signatures serve uses today.
- Columns: `is_pickup_column(store, name)`, `DONE_COL_RE`, `IN_FLIGHT_RE`.

**Steps:**
- [ ] Write `test_tiles.py`, pinning today's behaviour of each function moved in:
  - port the parsing cases from test_related, test_links and test_pickup.Reading, verbatim;
  - add the decision cases: a `*` tile is keyed; a quoted "Claude session `x`" in a description is no session line.
- [ ] Run `python3 -m unittest test_tiles`. Expected: FAIL (no module).
- [ ] Implement `kandalf/tiles.py`, moving code, not rewriting it, so the old call sites can delegate in Task 2.
- [ ] Run `test_tiles`, then the full gate. Commit `refactor(tiles): one module reads and edits board markdown`.

### Task 2: Every Python reader goes through tiles, and the cross-board snapshot

**Files:** `kandalf/serve.py`, `kandalf/pickup.py`, `kandalf/gitlog.py`, `kandalf/pln.py`, `kandalf/store.py`, `test_tiles.py`, and tests that import the moved names.

**Interfaces (produces):**
- `Snapshot.read(store) -> Snapshot`: plans, backlog and the archive log, read once.
- `snap.index() -> dict`: today's `serve.tile_index` shape, plus `"tests"`, the tile's own `Tests:` value, for Task 3.
- `snap.texts() -> {key: md}`, `snap.in_flight() -> list[dict]` (today's `serve.in_flight_tiles` shape) and `snap.lineage(key) -> [Tile]` (nearest master first).
- `snap.nearest(key, reader) -> value | None`: the first non-None `reader(md)` up the family.

**Behaviour:**
- A ring of masters is broken the page's way: `lineage` stops at the first repeat, and the first of the ring stands as the master.
- The four lineage walks (`pickup.lineage`, the loop in `serve.in_flight_tiles`, `full_suite_on_merge`, `still_to_merge.under`) become `snap.lineage`/`snap.nearest`.

**Steps:**
- [ ] Write tests for the snapshot. Include an archived master with an in-flight sub-story (Review Focus 2) and a two-tile ring.
- [ ] Repoint the callers:
  - serve's `board_tiles`, `tile_index`, `in_flight_tiles`, `Handler.tiles`/`texts`/`linked`, and `unmerged_done` (Done by column, checkbox or not);
  - `gitlog.tiles`, `pln.TILE_RE`;
  - pickup's regexes and readers;
  - `TILE_SESSION_RE` (anchored now).
- [ ] Turn the old names (`serve.board_tiles`, `store.tile_head_re`, and so on) into thin aliases, or delete them where no caller is left. Grep to prove it.
- [ ] test_pickup builds In flight tiles with `tiles.Snapshot`/`parse`, not `serve.in_flight_tiles`.
- [ ] Full gate. Add CHANGELOG lines for the three user-visible decisions (`*` tiles picked up, a quoted session line ignored, Done counts without a checkbox). Commit `refactor(tiles): serve, pickup, gitlog and pln read the board through one snapshot`.

### Task 3: The tile case file, run by both readers

**Files:** create `tile_cases.json`; modify `test_tiles.py`, `test_page.js`, `kandalf/board.html` (`inheritedTests`; the ring rule if it differs), `kandalf/serve.py` (only if the index needs the `tests` field wired through), and `pyproject.toml` (no change: the case file is a test file).

**Interfaces:**
- `tile_cases.json`: `{"lines": [{"line": "  Model: sonnet", "kind": "model", "value": "sonnet"}, …], "tiles": [{"md": "...", "key": …, "title": …, "parent": …, "model": …, "base": …, "tests": …, "repos": …, "sessions": …, "ready": …, "session": {...} | null}], "boards": [{"text": "...", "tiles": [{"key", "column", "done"}]}]}`.
- Python adapter: `test_tiles.TileCases` runs every case through `tiles`.
- Page adapter: a test in test_page.js runs every case through `parse`/`splitCard`/`modelOf`/`baseOf`/`testsOf`/`reposOf`/`sessionsOf`/`sessionLine`.

**Steps:**
- [ ] Write the case file. Cover every twin rule in the findings table, with at least one edge case each (case, backticks, a trailing dot, CRLF, a quoted session line, a `*` tile).
- [ ] Run both adapters. For each case one side fails, make the reader that disagrees with the spec's decisions agree. Otherwise make the page agree with Python, and list each such choice in the commit body.
- [ ] `inheritedTests` walks `serverTiles` (the index) for masters on other boards, using the index's `tests` field.
- [ ] Move twin-flavoured examples from test_page.js and test_tiles.py into the file. Delete those tests only once their case is in it.
- [ ] Full gate. Commit `test(tiles): one case file holds both readers of board markdown to the same rules`.

### Task 4: The job ledger, its lock and its queries

**Files:** create `kandalf/jobs.py` and `test_jobs.py`; modify `kandalf/pickup.py` (`load`/`save`/`_lock` become ledger calls), `kandalf/serve.py` (direct `pickup.load(...)["jobs"]` reads and `pickup._lock` uses), `kandalf/init.py` (`set_agent`) and `kandalf/cli.py`.

**Interfaces (produces):**
- `jobs.Ledger(store)`:
  - `with ledger.change() as state:` is the one transaction. It takes the in-process lock plus a `fcntl.flock` on `board-pickup.lock` beside the state file (no flock where `fcntl` is missing), loads, yields the state dict, and saves on exit with today's KEEP/KEPT pruning.
  - `ledger.read() -> state` is a snapshot without the lock.
  - `ledger.latest(key) -> job | None`, `ledger.live() -> [job]` (KEPT states), `ledger.busy() -> bool` (waiting or TRACKED) and `ledger.with_branch() -> [job]`.
- Constants `TRACKED`, `STOPPED`, `KEPT` move here; pickup re-exports them.
- State file format unchanged. `load`/`save` keep reading old files (Review Focus 3).

**Steps:**
- [ ] Write `test_jobs.py`:
  - a transaction round-trip and pruning;
  - an old file with each legacy shape loads;
  - `latest`, `live`, `busy`;
  - a cross-process test: a child process (`subprocess` running `python3 -c`) holds `change()` and sleeps 0.5s while the parent's `change()` waits, and neither write is lost (Review Focus 4).
- [ ] Implement. Replace each `with _lock: state = load(store) … save(store, state)` in pickup with `with ledger.change()`. Replace serve/init/cli direct reads and every `pickup._lock` use outside pickup with ledger calls. `init.set_agent` saves through `change()`.
- [ ] Full gate. Commit `refactor(jobs): one ledger owns board-pickup.json, locked across processes`.

### Task 5: Named transitions and the sessions adapter

**Files:** `kandalf/jobs.py`, `kandalf/pickup.py`, `kandalf/serve.py`, `test_jobs.py`, `test_pickup.py`.

**Interfaces (produces):**
- Transitions on the job dict, in jobs.py, each enforcing its invariant and returning the log line:
  - `queue(state, job_fields, now)`, `begin(job, started_fields)`;
  - `saw(job, seen, in_flight, now, why)` (today's `_look`);
  - `pausing(job)`, `paused(job)`, `requeue(job, now)`, `lose(job)`, `end(job)`, `close(job)`, `mark_ready(job, note)`.
- `pickup.Sessions`: an adapter object with `start`, `resume`, `switch`, `reroute`, `stop`, `sessions`, `why`, `full_id`, `announce(store, job)` (today's `on_start`) and `now`.
  - `pickup.tick/pause/reply/restart/start_now/tidy/status` take `sessions: Sessions` (default: the real one) instead of their per-call keyword effects.
  - serve builds one real `Sessions` with `announce=note_session` and passes it everywhere.
- test_pickup's `Fake` becomes a `Sessions` subclass, the second adapter. Its tests pass it once.

**Steps:**
- [ ] Write test_jobs tests for each transition's invariant:
  - pausing only from started;
  - a KEPT job is never pruned;
  - one tile, one session (`queue` refuses a key with a live job);
  - a closing job's tile may leave In flight without a pause.
- [ ] Move the dict mutations in pickup to the transitions.
- [ ] Replace the keyword effects with `sessions`. Keep every test_pickup assertion; only the wiring changes.
- [ ] Full gate. Commit `refactor(jobs): a job changes state through named transitions, and pickup drives one sessions adapter`.

### Task 6: The ledger's view for the page

**Files:** `kandalf/jobs.py` (`view`), `kandalf/pickup.py` (`summary` includes it), `kandalf/board.html` (`sessionState`, `wantsReply`, `canMessage` and the wake/continue/queue/start-now button choice read it), `test_jobs.py`, `test_page.js`, README if it describes the states.

**Interfaces:**
- `jobs.view(job) -> {"label": str, "actions": [str]}`. Actions come from `reply`, `pause`, `continue`, `restart`, `queue`, `startnow` and `close`, and must match the server-side guards in `reply`/`pause`/`restart`/`requeue`/`start_now`.
- `pickup.summary` adds `"view"` to each job.
- The page uses `job.view` when present, and falls back to today's derivation when absent (Review Focus 5).

**Steps:**
- [ ] Write a test_jobs table test: for each state and flag combination the page shows today, the view's actions equal the set the server guards allow. Move the page's `sessionState`/`canMessage`/`wantsReply` test cases to assert the page reads `view`, plus one fallback case.
- [ ] Implement, then run the full gate and a browser check of three tiles (at work, idle and ready, paused): the same buttons as before.
- [ ] Commit `refactor(jobs): the page shows the actions the ledger allows`.

### Task 7: Glossary, docs and the final check

**Files:** create `GLOSSARY.md`; modify README (module list: `tiles.py`, `jobs.py`; AGENTS.md's twin-rule note points at `tile_cases.json`) and CHANGELOG.

- [ ] `GLOSSARY.md` defines, one or two lines each:
  - board, column, tile, story, master, sub-story, attribute line, session line;
  - snapshot, lineage;
  - job, ledger, transition, sessions adapter;
  - cut, trunk, base, pickup.
- [ ] AGENTS.md's "Two readers of one format" names `tile_cases.json` as the spec both readers run.
- [ ] Full gate, plus `python3 -m kandalf check` on a sample board. Then the whole-branch review.
