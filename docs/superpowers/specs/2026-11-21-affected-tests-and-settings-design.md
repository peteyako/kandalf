# Affected tests only, a full suite on merge when asked, and a settings panel

2026-11-21. Status: approved.

## Intent

The user runs the full test suite nightly on a schedule. A story's session shouldn't spend its
time running it again. Today every story runs every tier: the default session rules
(`kandalf/templates/pickup.md`) give each story a **Gates** sub-task for "lint, every test tier
and the review". Only sub-stories are already limited to the tests for what they change, via
`subStoryTests: "relevant"`, and even then the last sub-story's merge runs the full suite.

They want three things:

1. **Every story runs only the tests that cover what it changes.** Sub-stories already do this;
   now every story does.
2. **A full suite on merge, only when asked.** A store-wide toggle, off by default, and a
   per-story option on the tile.
3. **A settings panel on the board.** The defaults kandalf has gathered (pickup, models,
   sessions, trunk, tidy, push and so on) can be seen and changed there, instead of only by
   editing `board.json` by hand.

## Goals

- A story's session, master, sub-story or neither, runs the tests for what it changes, never
  the whole suite. This holds whatever the store's `pickup.md` or the app's `CLAUDE.md` say
  about running every tier: the prompt says it stands in for those rules, as the sub-story
  paragraph does today.
- The full suite runs only on merge from the board, and only when the store's
  `fullSuiteOnMerge` is on or the story's tile says `Tests: full`. Both default to off.
- A checkbox in the tile dialog sets this for one story, and a chip on the tile shows it.
- A ⚙ Settings panel on the page shows the store's settings. Each one shows its default and
  whether it was changed, and the panel saves the editable ones to `board.json`. Changes take
  effect without restarting the server.

## Non-goals

- Running the full suite at any other time: before review, on a schedule, or in CI. The nightly
  run is the user's own and stays outside kandalf.
- Choosing which tests count as "affected" for the session. The session judges that, as
  sub-stories do today; kandalf doesn't map files to tests.
- Editing the settings that shape files and paths from the panel: `branch`, `worktree`,
  `link`, `repos`, `app`, `key`, `port`, `pickupColumn`, `hooks`. They are shown read-only, with
  a note to edit `board.json`, because changing them under live stories breaks paths.
- Settings for each browser or each person. Theme and accent colour stay in the browser as now;
  everything in the panel is the store's.

## Anti-goals

- The full suite running by default anywhere. A store that sets nothing never runs it.
- The panel losing anything a person wrote into `board.json` by hand: unknown keys, keys it
  doesn't show, or a hand edit made while the panel was open.
- A setting that looks changed on the page but isn't in effect until a restart.

## Approaches

**How the toggle works.** The options were one key with several values, `fullSuite:
off|merge|review`, or a plain toggle. The user wants no full-suite runs except, sometimes, on
merge, so there is nothing for a `review` value to do. Picked: one boolean, `fullSuiteOnMerge`,
default `false`.

**How a story asks for it.** The options were a tile line written from the tile dialog, or a
"Run full suite" box in the review reply. Picked (by the user): a checkbox in the tile dialog
that writes a `Tests:` line, like the Base field writes `Base:`. The review-reply box may come
later if it is missed.

**How settings are edited.** The options were a form panel that writes `board.json`, or
editing `board.json` as text in the page. The text editor adds little over the editor the user
already has. Picked (by the user): a form panel.

## The design

### Tests

- **Prompt.** `pickup.sub_story_tests` becomes one tests paragraph given to every story. It says
  to run only the tests that cover what the story changes, and that this stands in for any rule
  in `pickup.md` or `CLAUDE.md` asking for every tier. It says the full suite runs on the trunk
  after the merge when this store or this tile asks for it, and otherwise not at all from this
  session (it runs nightly). The Ready for review line says which tests ran.
- **Default rules.** The template `pickup.md` **Gates** sub-task reads "lint, the tests for what
  it changes, and the review". A store with its own `pickup.md` keeps its own text; the prompt
  paragraph wins over it.
- **`board.json`.** `fullSuiteOnMerge`, boolean, default `false`. `subStoryTests` is retired:
  ignored if present, with a CHANGELOG note, and `kandalf doctor` mentions it.
- **Tile line.** `Tests: full` or `Tests: affected`, an indented line of its own like `Base:`.
  Regex twins: `pickup.TESTS_RE` and `TESTS_RE` in `board.html` ("same in board.html"). A
  sub-story takes its master's line when it has none of its own (nearest line wins, as `Model:`
  does via `family_text`). Matching is case-insensitive; any other value is ignored.
- **Whether a merge runs the full suite**, decided when the merge reply is written
  (`reply_text`). It reads the story's tile as it is at that moment, from the tile index the
  reply route already passes in:
  - `Tests: full` on the tile, or its master's: yes.
  - `Tests: affected`: no.
  - No line: the store's `fullSuiteOnMerge`.
  - For a sub-story, a yes runs the suite only on the last of its master's sub-stories to merge
    (`still_to_merge`, as today). An earlier one says the suite waits for the last.
  - When the answer is no, the merge reply says nothing about a full suite.
- **The step itself** is today's "suite" step: after the merge and before the push, on the
  trunk's tip, as a `- [ ] **Full suite** on <trunk>` sub-task on the tile (the master's tile,
  for a sub-story), ticked when it passes. If it fails, it stops before the push.
- **Tile dialog.** A "Full suite on merge" checkbox beside the Base field. It starts checked
  when the tile's line (or its master's) says full, or when there is no line and the store's
  setting is on. Changing it writes `Tests: full` or `Tests: affected`, or removes the line
  when the new state equals what the tile would get anyway (`withTests`, like `withBase`). The
  page learns the store's setting from `/api/files` (`fullSuiteOnMerge`).
- **Tile chip.** `full suite` on a tile whose own line says full; nothing for `affected` or no
  line. A sub-story shows no chip for its master's line, the way Base works today.
- **Running session.** It is not told when the line changes. The line is read when the merge
  reply is written, so a change before merge is enough.

### Settings panel

- **Where.** A ⚙ button in the header, beside the theme picker, opens a dialog. It has
  sections: **Pickup**, **Tests**, **Git and commits**, and **About this store** (read-only).
- **Editable settings**, each with its effective value, its default, a "changed" mark when
  `board.json` sets it, and a reset that removes the key so the default applies:
  - Pickup: `pickup` (on/off), `agent` (claude/copilot), `model` (free text with the agent's
    known models suggested), `permission` (select), `parallel` (number ≥ 1), `sessions`
    (number ≥ 1).
  - Tests: `fullSuiteOnMerge` (on/off).
  - Git and commits: `trunk` (the app's branches suggested, as the Base field does), `tidy`,
    `push`, `commitDelay` (seconds ≥ 0).
- **Read-only**: name, key, port, store folder, app, repos (name and trunk), link,
  pickupColumn, branch and worktree patterns, hooks, version. Each has a note that it is set in
  `board.json`.
- **API.**
  - `GET /api/settings` returns `{"mtime", "values"}`. `values` holds the keys `board.json`
    sets, followed by `"effective"` (what the server uses now) and `"defaults"`. It also returns
    the choices for selects.
  - `POST /api/settings {mtime, changes: {key: value | null}}` checks the `mtime`. A stale one
    gets 409 with the current settings, as a board write does. It checks each key: editable keys
    only, each value validated the way `store.py` reads it (types, ranges, known agent and
    permission mode). It writes with `init.write_config`, which keeps every other key and queues
    the commit "settings: <keys>". It reloads the store, and returns the new settings.
  - `null` removes a key.
- **Live reload.** `Store` gains `reload()`, which re-reads `board.json` into the same object, so
  the handler and the pickup thread see the change on their next look. The pickup watch thread
  always starts. It does nothing on a look while `pickup.why_off(store)` says pickup is off, so
  turning pickup on works without a restart. Turning it off stops new pickups and leaves running
  sessions alone, and the panel says so. The page re-fetches `/api/files` and the pickup summary
  after a save, so the In flight hint, the stats strip and the Base and Tests defaults update at
  once.
- **Agent switch.** Changing `agent` follows `kandalf agent`'s rules: it is refused while any
  session of the board is live, with the same message, since there is no `--force` in the
  panel.
- **Hand edits.** The server also reloads the store when `board.json`'s mtime changes (checked on
  each `/api/settings` and pickup look), so a hand edit made while the server runs takes effect
  too.

### Calls made after the builder check (the designer's, not the user's)

- **Several repos.** A story working in several repos runs the full suite in each, on that
  repo's trunk, with one `**Full suite** on <repo> <trunk>` sub-task per repo. A story in one
  repo keeps today's single line.
- **A master's own merge.** A master with commits of its own is merged like any story. When the
  decision is yes, its own merge runs the suite too, even if its last sub-story's merge already
  did. It is rare, since a master with nothing to merge is closed rather than merged, and running
  twice is safer than never.
- **No migration.** `subStoryTests` is ignored. `kandalf doctor` says it is retired and names
  `fullSuiteOnMerge`. No store is rewritten.
- **"The full suite"** keeps today's wording: every test tier the repo has.
- **Pickup on but not running.** When pickup is on but not running (`why_off` gives a reason,
  such as no agent CLI or no pickup column), the panel shows the `board.json` value, editable, and
  the reason as a note beneath it.
- **Defaults that depend on the store** (`trunk`: dev when the app has it; `model`: by agent).
  The panel shows the value derived for this store as the default. Reset removes the key, so the
  derived value applies.
- **Reload safety.** Reload swaps the settings in under the pickup lock. A `board.json` that
  doesn't parse after a hand edit keeps the old settings in force, and the panel shows the error.

## Left to the builder

- The exact dialog layout and styling within the existing themes, which must work in every
  theme including classic.
- Which other code reads `board.json` once and keeps it. `serve.py` keeps the `Store` built at
  startup, and anything caching from it must re-read after `reload()`.
- The wording of the prompt's tests paragraph, to the style of the existing prompt text.
- Tests: the Python side for the tests paragraph, the merge decision table, the settings API
  (validation, mtime conflict, unknown keys kept, reset) and reload. The page side
  (`test_page.js`) for `TESTS_RE`, `withTests` and the checkbox's starting state.
- README: the config table (add `fullSuiteOnMerge`, retire `subStoryTests`), the tile-line table
  (`Tests:`), a short **Settings** section, and the CHANGELOG lines.
