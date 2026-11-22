# kandalf: deepening opportunities

Read-only survey of `kandalf/` at `dev` 364ed7b. Line refs are to that tree. Vocabulary per the brief
(module, interface, depth, seam, adapter, leverage, locality, deletion test).

Six candidates, strongest first. 1 and 2 are two halves of one problem (the board-markdown grammar),
on the Python side and across the Python/page split. 3 is the pickup job ledger.

---

## 1. The tile reader: one Python module for board markdown — **Strong**

**Files and functions**

- `serve.py`: `board_tiles` (301), `tile_title` (388), `tile_index` (398), `in_flight_tiles` (417),
  `link_re` (296), `part_re` (380), `DONE_COL_RE`/`TILE_HEAD_RE` (384-385), `with_session` (455),
  `with_status` (483), `SESSION_LINE_RE` (452), `STATUS_LINE_RE` (480), `take_tiles`/`without_tiles`/
  `insert_after` (349-377), and `unmerged_done` (713), which has its own line walker.
  `Handler.tiles`/`texts`/`linked` (927-953) re-read and re-parse the same files on every request.
- `pickup.py`: the tile-line regexes `IN_FLIGHT_RE`, `OPT_OUT_RE`, `READY_RE`, `REPOS_RE`, `MODEL_RE`, `BASE_RE`,
  `TESTS_RE` (64-76), `SESSIONS_RE` (165), `TILE_BRANCH_RE`/`TILE_REPOS_RE` (1304-1305),
  `TILE_SESSION_RE` (1473), plus the readers `ready_note`, `model_for`, `base_named`, `tests_named`,
  `repo_names` (212-266), `tile_cuts` (347) and `tile_worktree` (366).
- `gitlog.py`: `tiles` (42), a third tile walker, used by the commit messages.
- `pln.py`: `TILE_RE` (17) and `tiles_oldest_first` (36), a fourth.
- `store.py`: `tile_key_re`, `tile_head_re`, `keyed_re`, `key_re` (196-200). These are grammar, kept on the
  settings object.

**Problem**

One concept, "what a tile is and what its lines say", is parsed in five Python places, and the rules
already differ:

- `board_tiles` accepts a `* ` tile line (`^[-*] `), but finds its key with `store.tile_head_re`, which is
  `^- ` only. So `* [ ] PLN-07 …` is a keyless tile to Python, and `in_flight_tiles` never picks it up. The
  page's `parse` + `splitCard` (board.html 1132, 1242) do key it, and show it as a normal tile.
- `unmerged_done` (serve 724) needs a checkbox (`^- \[[ xX]\] KEY`). `gitlog.tiles` and `pln.TILE_RE` take
  `- ` only. Each of the four walkers ends a tile body by a slightly different rule.
- The tile's session line is written in serve (`with_session`, 476) and read by three pickup regexes, by
  `SESSION_LINE_RE` in serve, and by `SESSION_RE` in the page. `TILE_SESSION_RE` has no anchor, so a
  description that quotes "Claude session \`abc1234\`" counts as a session line for `pickup.adopt` (1486)
  and `_tidy_cuts` (1341). The page's anchored `SESSION_RE` (board.html 1588) does not count it.
- pickup's input shape (`[{"key","title","text","parent","of","context"}]`) is built in
  `serve.in_flight_tiles`, which calls back into `pickup.is_pickup_column`. So test_pickup has to go through
  serve to make tiles (test_pickup.py 349: `serve.in_flight_tiles(self.store, text)`).
- Walking up the masters is written four times: `pickup.lineage` (168), the loop in
  `serve.in_flight_tiles` (436-440), `pickup.full_suite_on_merge` (240) and `still_to_merge.under` (1078).
  Each guards against cycles in its own way.

**Deepened module**

A `tiles.py` (the name is open) that owns the board grammar on the Python side:

- **Parse:** a board file into tiles, each with its key, column, line span, title, priority and estimate,
  parent ("Part of"), links ("Moves with"), mentions, and its attribute lines: Model, Base, Tests, Repos,
  Sessions, Ready for review, Handoff, No pickup.
- **The session line:** its agent, id, branch and cuts, so the old one-worktree form and the
  several-worktrees form are both understood in one place.
- **Edits:** set the session line, set the status line, take tiles out, put tiles in, each keeping the
  layout so the file still round-trips.
- **A snapshot across boards:** plans, backlog and the archive log, read once. It answers the tile index,
  the texts, the linked groups, the In flight tiles, a tile's lineage, and "the nearest line of kind X up
  the family" (today `family_text` + first regex match, and `full_suite_on_merge`'s own walk).

The interface is a few nouns (Board, Tile, Snapshot) and their queries. Behind it go every regex now in
pickup, serve, gitlog, pln and store.

**Benefits**

- **Locality:** one file mirrors `board.html`'s `parse`/`splitCard`/`tileFacts`. The "same in board.html"
  comments then all point at one place. The divergences above become visible, and get fixed once.
- **Leverage:** serve, pickup, gitlog, pln and the CLI ask a snapshot instead of re-walking text. The
  reply route stops reading the board family twice (`self.tiles` + `self.texts`, serve 1128).
- **Tests:** test_related.py, test_links.py, the grammar half of `test_pickup.Reading`, and the board-edit
  parts of test_clear.py move to one `test_tiles.py` against the new interface. Pickup tests can then build
  tiles without importing serve.
- **Deletion test:** delete `board_tiles` today and its callers have to re-grow it, so it earns its keep. But
  it is only half deep: delete the other four walkers and their callers simply use it. Their complexity
  vanishes; it does not move.

**Risks and cost**

- Medium-large. serve.py and pickup.py change in many places.
- Each divergence is a behaviour decision. Pin current behaviour with characterization tests first, then
  choose deliberately: does a `* ` tile get a key? Does a Done tile with no checkbox count?
- The `store.*_re` attributes are read by name in several places. Keep them as thin aliases for a while.

---

## 2. Twin rules as one shared spec: a conformance corpus both readers run — **Strong**

**Files**

Every rule written twice, in Python and in `board.html`:

| Rule | Python | board.html |
|---|---|---|
| `IN_FLIGHT_RE` | pickup 64 | 1185 |
| `DONE_COL_RE` | serve 384 | 1186 |
| `READY_RE` | pickup 70 | 1725 |
| `REPOS_RE` | pickup 71 | 1601 |
| `MODEL_RE` | pickup 72 | 1598 |
| `BASE_RE` | pickup 74 | 1604 |
| `TESTS_RE` | pickup 76 | 1608 |
| `SESSIONS_RE` | pickup 165 | 1617 |
| The session line | serve 452 and pickup 1473 | `SESSION_RE` 1588 |
| The status line | serve 480 | `STATUS_RE` 1717 |
| `link_re` / `part_re` | serve | `linkRe` / `partRe` (1230-1235) |
| A tile's title | `tile_title` | `splitCard` |
| `BRANCH_NAME_RE` | settings 10 | 1609 |
| `WAITING` | pickup 693 | 1450 |
| The pickup column | `is_pickup_column` | `isPickupColumn` |

**Problem**

AGENTS.md makes "change one, change the other" a ground rule. Only comments enforce it: each side's tests
are separate, hand-picked examples (test_pickup.py, test_page.js). The two readers have already drifted:

- **Parent cycles.** The page's `tileIndex` (board.html ~1316) breaks a ring of masters ("the first of them
  stands as the master"). `serve.tile_index` doesn't, and pickup guards each walk ad hoc.
- **Inherited Tests: lines.** The page's `inheritedTests` (1666) reads only masters on the open board ("one
  on another board counts as none"). `pickup.full_suite_on_merge` (240) walks the cross-board index that
  `Handler.tiles` builds. So the dialog's "Full suite on merge" box can show a different inherited value from
  what a merge from the board then does.
- **Session-line anchoring.** It differs between the two (see 1).

**Deepened module**

A data file of cases, the interface both readers must satisfy. Each case is a line, a tile or a small board,
with the facts it should yield (key, title, column kind, parent, links, each attribute line's value, session
line parts, whether it round-trips). `test_tiles.py` and `test_page.js` each load it and run every case
through their own reader. The twin rule is then a real seam with two adapters (the Python reader and the
page reader), held to one spec. It is no longer two copies kept in step by memory.

A bolder variant is to have the server hand the page the regex sources, as it already hands it the key.
That is not recommended: JS and Python regex dialects differ, and the page must stay self-contained.

**Benefits**

- **Locality:** a new tile line kind is one corpus entry plus two implementations, and a missing one fails at
  once.
- **Tests:** the twin-flavoured examples on both sides (`test_an_in_flight_column_under_another_name…`, the
  `modelOf`/`baseOf`/`testsOf`/`reposOf` tests in test_page.js) collapse into the corpus.
- **The divergences above:** they show up as failing cases, and are then decided once.

**Risks and cost**

- Low cost, no runtime change.
- The first run will surface dialect differences (`\s`, lookbehind, `re.M` vs per-line matching). That is
  the point, but it takes a decision for each one.
- The corpus pays off fully only once candidate 1 exists, since today's Python side has no single reader to
  feed it to.

---

## 3. The pickup ledger and a job's lifecycle — **Strong**

**Files and functions**

- `pickup.py`:
  - The ledger: `_lock` (81), `state_path`/`load`/`save` (140-163) and the KEEP/KEPT pruning.
  - Its transactions, each one `with _lock: state = load(); job = next(reversed…); mutate; save()`: `tick`
    (747-857), `start_now` (976), `pause` (988), `requeue` (1020), `reply` (1191), `restart` (1252),
    `archived` (1320) and `adopt` (1476).
  - The per-job helpers: `_look` (935), `_pause` (1038), `_remodel` (860), `_rerepo` (895) and `_mark_ready`
    (926).
  - The state lists `TRACKED`/`STOPPED`/`KEPT` (742-744).
- Outside pickup:
  - `init.set_agent` (538-580) mutates job states and calls `pickup.save`.
  - serve reads `pickup.load(store)["jobs"]` directly: `watch_turn` 581, `tidy_stragglers` 699,
    `unmerged_done` 736 and `log_view` 1193. It also takes the private `pickup._lock` itself
    (`reload_settings` 529, `save_settings` 1173).
  - `cli.py` 65 does the same lookup again.
- The page: `sessionState` (board.html 1452), `wantsReply`/`canMessage` (~1466), the `STOPPED` labels, and
  38 job-state literals. `pickup.summary` (1666) ships raw job dicts to the page.

**Problem**

The job is an untyped dict with eight states (waiting, started, idle, lost, ended, failed, paused, plus
closed-early "ended") and about ten flags (pausing, closing, again, ready, waiting, behind, want,
repos_error, model_was, …).

- **Transitions are scattered.** They are written as dict assignments in about twelve functions (state set
  at 799, 945-957, 1008, 1055, 1229, 1327, 1436, plus init 567). The invariants ("one tile, one session";
  "KEPT jobs are never forgotten"; "pausing only from started"; "a closing job's tile may leave In flight
  without being paused") live in comments next to whichever function enforces them.
- **The "latest job for this key" lookup is repeated** 10 times, in pickup, serve and cli.
- **The CLI can lose updates.** `kandalf agent` runs in its own process. It calls `pickup.load`/`save` with
  no lock that the server's tick respects (`_lock` is a per-process `threading.Lock`), so a tick that loaded
  before it can save over its "lost" marks. The settings route avoids this only because serve wraps the
  call in `pickup._lock` from outside.
- **The page re-derives the state machine.** `canMessage` ("started/idle/lost, not pausing") must agree with
  `reply`'s guard (1213-1217). `wantsReply` and `wakeButton` must agree with `restart`'s allowed states
  (1263). Nothing ties them together.

**Deepened module**

A `jobs.py` ledger, which owns the state file and the lifecycle:

- **One transaction form:** lock (including across processes, with `fcntl.flock` on the state file, which
  is stdlib and fine on CI's Ubuntu and macOS), load, yield, save.
- **A few queries:** the latest job for a key, the live jobs, whether anything is busy, the jobs with a
  branch.
- **The transitions as named operations:** queue, begin, saw-busy/idle/gone, pause → pausing → paused,
  requeue, lose, end, close. Each holds its own invariant and logs its one line.
- **The page's view:** a `view()` that gives each job its label and the actions open to it (reply, pause,
  continue, queue, start now). Then board.html renders, and pickup decides, from one place.

**The session-driver seam inside it.** Today the effects are injected as one keyword per function:

- `tick` takes 9 (start, sessions, switch, reroute, why, now, on_start, resume, stop).
- `pause`, `reply` and `restart` take 5 or 6 each.
- `tidy` takes 3, and `status` takes 4.
- serve passes `on_start=note_session` by hand at every call (584, 1127, 1137, 1141, 1145).

test_pickup's `Fake` (56-90) is already a second adapter of one interface, but it is wired by hand per call.
Make that one "sessions" object: start, resume, switch model, move repos, stop, look, full id, and announce
(the session-line write). The lifecycle holds it once. The real adapter wraps agent.py, the worktree
preparation and the tile write-back; the fake stays the test one. A new route can then no longer forget
`on_start`.

**Benefits**

- **Locality:** the state rules sit in one file. pickup.py keeps the policy that is truly its own: prompts,
  review replies and the queue order.
- **Leverage:** serve, init and cli use three or four queries and one transaction. They stop knowing the
  dict layout or the private lock.
- **Tests:**
  - The lifecycle tests in test_pickup (the Queue, Pause, Continue and restart groups: about 40 tests from
    337 to 770) can test transitions directly, without composing board texts and a Fake CLI.
  - The set_agent tests in test_init/test_settings shrink.
  - The page's `sessionState`/`canMessage`/`wantsReply` tests become tests of `view()`.
  - Add one cross-process test for the lock.
- **Deletion test:** delete `pickup.load`/`save` today and nothing concentrates; they are a pass-through over
  JSON. Delete the ten copies of the lookup-and-mutate pattern and the real complexity (the transitions)
  concentrates in one place. That is the module that is missing.

**Risks and cost**

- High. `tick` is the densest code in the repo.
- The state file must stay readable for old jobs: "ended" closed early, jobs with no `model`, jobs with a
  bare `worktree`.
- Do it in steps: the ledger and queries first, then the transitions one by one, then the driver object,
  then `view()`.

---

## 4. A story's cuts: its worktrees, branch and trunk across repos — **Worth exploring**

**Files and functions**

- `pickup.py`: `cut_of` (498), `prepare`/`prepare_all` (507-548), `extra_dirs` (550), `_cuts_of` (573),
  `story_trunk`/`as_story` (581-595), `tile_cuts`/`tile_worktree` (347-374), `_tidy_cuts` (1336), `tidy`
  (1349), `discard` (1427), `nothing_to_merge` (1097), `git_facts` (1544), `_start_moved` (1244), `_git`
  (494), and the repos half of `reply_text` (1140-1170).
- `serve.py`: `_app_git`/`_branch_merged` (626-654), handed into `pickup.tidy` as the `merged=` callback
  (680), and `unmerged_done` (713).
- Elsewhere: `store.has_branch` (102) and `init.git` (69).

**Problem**

- **Three shapes for one thing.** "Where a story's work lives" has three shapes: the legacy single
  `job["worktree"]`, the `job["repos"]` list of cuts, and the tile's session line in two forms. They are
  normalised in four places (`_cuts_of`, `cut_of`, `tile_cuts`, `_tidy_cuts`), with 12 reads of `repos` and
  11 of `worktree` across pickup and serve.
- **Merged-ness sits in the wrong module.** Whether a branch is merged is a subtle algorithm (ancestor,
  merge-tree equality, squash-commit grep). It lives in serve, though only tidy and the Done-column outline
  use it, and pickup gets it as an injected lambda.
- **Git runs in many places.** It is called in pickup (24 sites), serve (16), gitlog, init, store and stamp,
  each with its own timeout and error handling.

**Deepened module**

A `Story` (or `cuts.py`), built from a job record or a tile:

- its cuts, normalised once at load (the legacy form folded into one cut);
- each cut's repo, worktree and trunk (the Base line or the recorded trunk);
- its branch;
- the queries: ahead, dirty, merged, and nothing to merge;
- the actions: prepare and remove.

pickup's prompts, tidy, discard and reply, and serve's `unmerged_done`, all ask it.

**Benefits**

- **Locality:** the merged-ness algorithm and the legacy handling each live once.
- **Tests:** tidy, discard and unmerged tests (test_clear.py, test_repos.py) test one module against
  throwaway repos, instead of through the HTTP handler or pickup.
- The `merged=` injection goes away.

**Risks and cost**

- Medium. test_repos.py (865 lines) leans on the current shapes.
- The folding must not lose the old tiles' line form, which archived tiles keep for good.
- Depends on 1 for the session-line parsing.

---

## 5. board.json's schema: one table for every key — **Worth exploring**

**Files and functions**

- `store.py`: the docstring table (14-62), `Store._read` (169-205: defaults, coercion, env overrides
  `PLANS_APP_REPO`/`PLANS_APP_TRUNK`/`BOARD_COMMIT_DELAY`), and `reload`'s save-and-restore of `__dict__`
  (139-161).
- `settings.py`: `EDITABLE` (15-52), `READONLY_HELP` (54-66), `_effective` (73), `_defaults` (79), `view`'s
  readonly dict (118-123), `_check` (128) and `apply` (157). It imports `init` for
  `read_config`/`write_config`/`set_agent` and `serve` for `mtime`.
- `init.py`: `write_config`/`read_config` (449-460), and the hand edits in `add_repo`, `remove_repo` and
  `set_agent`.
- `board.html` `settingsHTML` (1860): the readonly facts' labels, hard-coded again (~1883).
- The README's config table.

**Problem**

- **Defaults twice.** Each key's default is written in `Store._read` and again in `settings._defaults`
  (300, 2, 1, "auto", True, True, False). Adding a key means touching the docstring, `_read`, `EDITABLE`,
  `_effective`, `_defaults`, the README and sometimes `view`'s readonly dict and the page.
- **Writes live in init.** Reading and writing board.json lives in init, so settings depends on the init
  command module.
- **A global side effect.** `Store._read` also calls `agent.use()` (184-189). The process-global "agent in
  use" changes as a side effect of constructing or reloading any Store, so a test or doctor that opens
  another store switches the agent for everything.

**Deepened module**

A declarative table in store.py, one entry per key: attribute name, default (a value or a function of the
store), coercion, check, editable or not, and section/label/help. Then:

- `Store._read` loops over it, and so do `settings.view`, `_check` and `apply`.
- Reading and writing board.json (with its commit-queue record) moves into store.
- The agent becomes `store.agent_module`, read where it is needed, not a global set in `_read`.

**Benefits**

- **Locality:** a new key is one entry plus a README line.
- **Tests:** `test_every_setting_and_store_fact_says_what_it_means` becomes "every key has a default and
  help". `test_the_view_says_what_is_set_what_is_in_force_and_the_defaults` stops pinning a copy of the
  defaults.
- The agent global, and the save-and-restore dance in `reload`, mostly go away.

**Risks and cost**

- Several keys are not simple: `repos` (nested, validated, with problems collected), `trunk` (its default
  comes from git), `agent` (switching it stops sessions), and `commitDelay` (env beats file). The table needs
  escape hatches, or it turns into a mini-framework.
- Removing the agent global touches every `agent.*` caller in pickup.
- Moderate payoff: the panel is already data-driven from `EDITABLE`.

---

## 6. The page's tile-line editors, and the test seam into board.html — **Worth exploring**

**Files and functions**

- `board.html`: `withSessions` (1619), `withModel` (1627), `withBase` (1652), `withTests` (1682) and
  `withRepos` (1694), with their readers `modelOf`, `baseOf`, `testsOf`, `reposOf` and `sessionsOf`
  (1598-1700). Also the `sync*` dialog functions (2763-2800).
- `test_page.js`: 13-37, which lifts the text between `function parse(` and `// A plan doc (anything` and
  keeps a 50-name export list. `kandalf/check.js` lifts board.html in the same way.

**Problem**

- **One routine, five copies.** Five editors each re-implement the same routine: find the first body line
  matching the regex, then replace it, or remove it when the value is the default, or insert
  `"  X: value"` before the first sub-task. The readers each re-slice the markdown, and `sessionsOf` takes a
  body array where the others take markdown, so the interfaces differ for no reason. An insertion bug (for
  example, where the line goes relative to the session line) has five places to be fixed.
- **The test seam is a byte range.** A function the tests need must physically sit inside that span, and be
  named in test_page.js's return list. Anything outside it (`render*`, `openCard`, `sync*`, the session
  block) is untestable, and globals are poked through setter shims (`setRepos`, `setTrunk`,
  `setFullSuite`, …).

**Deepened module**

- **One tile-line module in the page:** a table of line kinds (regex, label, the value it is left off at)
  and two functions, read a kind's value and set a kind's value. The kinds keep their quirks (Repos keeps
  unknown names and the first-chosen repo; Tests compares against the inherited value) as small per-kind
  hooks.
- **An explicit model object.** Wrap the page's model functions in one object, `const Board = (() => { …;
  return { … } })()`, with the store facts as its parameters, not globals. test_page.js and check.js then
  evaluate that object and use its exports, so the export list lives in the page.

**Benefits**

- **Locality:** one insertion rule.
- **Leverage:** the dialog's `sync*` functions shrink to one call each.
- **Tests:** a table-driven test per line kind replaces the per-editor tests, and plugs straight into
  candidate 2's corpus.

**Risks and cost**

- board.html is the most-changed file, so there will be conflicts with work in flight.
- check.js and test_page.js both depend on the current span convention; change all three together.
- Moving globals into parameters touches render code.

---

## Looked at and judged not worth deepening

- **agent.py and the claude_code.py / copilot_cli.py seam:** a real seam with two adapters, deep enough.
  Only the `if name == "claude"` branches in `label`/`update_hint` could become module attributes; that is a
  trivial tidy, not a deepening.
- **gitlog.py, the commit queue:** a small interface (`record`/`flush`/`wait`) over real complexity
  (debounce, a committer process, push). Its only friction is the private tile walker, which candidate 1
  absorbs.
- **pln.py, tile keys:** small and focused. Its `TILE_RE` and its list of keyed files fold into candidate 1.
- **init.py, init/doctor/hooks:** large but mostly linear command code with injected probes, already
  testable. The config helpers move with candidate 5, and `set_agent`'s job edits with candidate 3.
- **stamp.py, autocommit.py, checkhook.py:** tiny and single-purpose.
- **The serve.py HTTP Handler routing (`do_POST`'s if-chain):** wordy, but a pass-through. Deepening it buys
  little. The value lies in what it calls (candidates 1 and 3).
- **pickup's prompt builders (`prompt`, `reply_text`, `tests_paragraph`, `repos_paragraph`):** long, but
  they are deep already. A small interface yields a whole prompt, and they are pinned byte for byte in
  test_pickup. Their repo and trunk lookups improve under candidate 4.
- **`serve.archive_docs`/`relink`/`clear_column`/`undo_clear`:** cohesive and covered by test_clear.py.
  Their tile slicing moves to candidate 1's edits.
- **The page's theme and settings rendering:** the settings panel is already driven by `EDITABLE`, and the
  theme code is self-contained. No seam is needed.
