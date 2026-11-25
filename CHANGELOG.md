# Changelog

## Unreleased

- The map's bar has **Hide done**: it leaves finished stories off the map, keeping a done one whose branch isn't merged yet and a done master that still has open sub-stories. A master's count still counts every sub-story. The choice is kept in your browser.
- The map's bar has **Organize…**: the store's agent proposes a domain for each story that has none (or for every story, with **Re-sort filed stories too**), and you tick what to keep. **Apply** files it in one commit, and one **Undo** takes it all back. The session runs in a throwaway worktree with copies of the stories' plan docs, not the store, and without the store's hooks, so nothing it writes reaches the store; the server reads its proposal, `ORGANIZE.md` (`POST /api/organize`, `POST /api/organize/end`). Apply files nothing when a story's domain changed after the proposal, and says which.
- Stories can be combined into one. On the map, Shift-click or Shift-drag two or more stories and press **Combine**; on a board, **Select** in the header puts a checkbox on each keyed card (a click ticks it instead of opening it; Esc leaves) and **Combine (n)** shows once two are ticked. The dialog lists the stories and lets you pick which one stays (the first selected by default). It keeps its key, title, priority, estimate and `Part of`; the others' descriptions follow its own as `From PLN-73 (Title):` paragraphs, their sub-tasks follow its sub-tasks with ticks kept, `Blocked by` and `Moves with` become the union (never a story combined), `Repos` the union, and `Domain` the survivor's, else the first other's. The others go to the archive with `Combined into PLN-72.` and keep their keys; any tile that said `Blocked by`, `Moves with` or `Part of` one of them now names the survivor. A story with a session or a place in the pickup queue, an archived story, and a master with its own sub-story are refused, saying which. One commit (`board: PLN-73, PLN-75 combined into PLN-72`) through `POST /api/combine`; a tile edited since the page read it refuses with `changed` and nothing is written. A survivor's `Repos` line is written only when the stories combined work in more repos than it would inherit anyway (a story with no line works in its master's repos, else the main repo), and its `Domain` is never added to a sub-story. An archived story loses its `Part of`, so its master doesn't count it. There is no Undo; the archive and git history keep the rest.
- A story's card has **Split…**: the store's agent reads the story's tile and its plan doc in a throwaway worktree and proposes sub-stories in `SPLIT.md`, with an optional note ("one per screen"). The card reads **Splitting…** (with Cancel) while it works and **Review split…** when it is ready; you edit each sub-story's title and description, untick the ones you don't want, and **Create** writes the rest below the story with the next free keys and a `Part of` line, moving the story's sub-tasks they take, in one commit (`board: PLN-72 split into PLN-90, PLN-91`). Nothing is written when the story changed since the agent read it, and a sub-story, an archived story or one with a session can't be split. One split runs per store at a time, beside a sort.
- Draft options no longer removes a worktree on a branch that sits at a draft's `draft-<key>` path: the draft needs you and names the path.
- A background committer whose store was deleted stops, instead of writing `board-pending.json` into the folder it was started from.

## 1.3.0

- The two boards are now **Plan** (was Backlog) and **Build** (was Plans), and their files `plan.md` and `build.md`. The page, the prompts agents are given, the filing rules, commit subjects (`board: PLN-26 plan.md → To do`) and `kandalf --help` all say so, and `kandalf init` makes a store with the new files. `kandalf serve` moves an existing store by itself, the first time it is safe to: `plans.md` becomes `build.md` and `backlog.md` becomes `plan.md`, in one commit (`board: plans.md → build.md, backlog.md → plan.md`) that also points the links between the store's files at the new names, brings the filing rules and the agents' notes up to date, and gives a board whose header was never edited the new one. It waits, and the page says why, while an agent session is in the middle of a turn or a `build.md` or `plan.md` of your own is in the way. A session that started before the move is told the new names in the next message it gets. A store that has not moved yet works on the old names as before, and an old `?file=plans.md` link or `kandalf check docs/plans/plans.md` opens the board under its present name. If an old file comes back beside its new one it is never read as a board: the page, the edit hook and `kandalf doctor` name it. See **Coming from 1.2** in the README.
- The map's bar has an **(i)** at its end: hover it, or focus it with Tab or a tap, for a short guide to getting around, arranging, and filing stories into domains.
- Stories are filed into domains from the map. Shift-click stories, or Shift-drag a box, to select them; drag a selected story onto a domain's region and the whole selection is filed there, families whole (a sub-story follows its master, and `Part of` is never changed). Dropping on **No domain** takes the `Domain:` line off. Esc, or a click on empty space, clears the selection, and a drop released elsewhere or abandoned with Esc writes nothing.
- A drop writes, replaces or takes off the `Domain:` line of each story's top master wherever it is (`build.md`, `plan.md` or the archive log), as one commit, through `POST /api/domain`; the open board must be saved first. The page says what it filed, and **Undo** in the map's bar stays until the next drop that files something or a reload; it is refused when a filed story's domain has been changed since.
- **+ New domain** in the map's bar adds an empty region to drop stories into. It is kept in the browser, with the map's arrangement, across reloads until a story is in it; Reset layout clears it.
- A tile in a decision column has a **Draft options** button on its card, with an optional note on what to look at. It cuts a throwaway detached worktree of each repo the tile's `Repos:` line names (else of the main repo) and starts one background session of the store's agent in the first, which researches the question and writes a research doc in the store. When it finishes, the board writes the doc's options onto the tile as an options block, and the card lists them above the Answer box, each with **Use** (it copies the option into the Answer box, asking first before it replaces what you typed). Nothing is decided until Decide.
- **Draft again** takes a note and replaces the draft, and **Stop** ends it. `kandalf attach KEY` opens a draft's session when the key has no story session. When the research doc changes later, the tile's options follow. The tile's tag says where the draft stands: `queued to draft`, `drafting options · 12m`, `options ready`, `draft needs you`, `draft gave no options` or `draft lost`.
- Drafts work one at a time, apart from the story queue (they don't count against `sessions` or `parallel`), and with `"pickup": false` too. A store whose `permission` is `plan` cannot draft. The session's code changes are discarded when the draft ends (the tile is decided, archived, deleted or leaves its decision column, Stop, or Draft again); only the draft's own worktrees are removed (the one at its `draft-<key>` path, or one git lists as detached), and any other worktree on a branch is left alone. Deciding or archiving the tile moves the research doc to `archive/` with it.
- A tile can carry an options block: a `**Options <date>:**` line (which option is recommended, a link to the research, the note it was asked with) and one `- **A. Title.** summary` line per option under it. The server and the page read it the same way, a block written by hand is listed like a drafted one, and what it says is not read as `Blocked by`, `Part of` or `Moves with`.
- A tile that says `Blocked by PLN-30` (or `PLN-30, PLN-31 and PLN-32`) waits for those tiles. A blocker is done when it is in a Done column or archived; a key no tile has blocks too, and a sub-story takes its master's blockers. What is blocked is read from the board each time, never stored. Take the words off to start the story anyway.
- A blocked tile shows a lock row for each open blocker (its key and title), a line for a key no tile has, and a check row with the answer once a blocker carries a decision. Clicking a key opens that tile on its board. Two tiles that block each other say so, and a blocked tile in flight says `waits for PLN-30`.
- Pickup leaves a blocked tile in In flight alone and picks it up on the first look after its last blocker is done; the console and the startup list say what it waits for. Deciding a tile on the Plan board starts what it blocked at once: a change to `plan.md` or the archive log brings a look, as one to `build.md` does. A story that already has a session carries on if a `Blocked by` line is added, and one paused in place stays paused. A story still queued gives up its place in the queue when it becomes blocked, and is queued again at the back once it is not. A story's session is given the tiles of its decided blockers to follow.
- A master with any sub-story in flight gets no session of its own, whenever its sub-stories arrived. This now holds when `No pickup` is taken off such a master too.
- A tile in a column named for decisions (Design decision, Decisions, Decide) has an Answer box on its card and a `Blocks …` line. **Decide** (`POST /api/decide`) writes the answer on the tile as a `**Decision <date>:**` line, folded to one line, ticks the tile and archives it in one step; the toast names the stories it unblocked. A decision is answered, not promoted or dragged to In flight.
- On the map, a blocker is joined to the stories it blocks, the side panel lists what blocks a story and what it blocks, and a blocked story no longer pulses as one to pull next.
- A tile's session line and its working tags show the agent's own mark too, in place of the ✦.
- The stats strip (at work, need you, …) shows above the map too, and a pressed filter fades every story on the map that isn't in that state.
- On the map, a story at work carries its agent's own mark (Claude's, Copilot's, or Gemini's for Antigravity) in place of the ✦, which read as Gemini's whatever the agent.
- `kandalf notes` says whether a store's filing rules are behind the installed kandalf: the `build.md` header and the note for agents in each linked repo. `kandalf notes --update` adds the header rules a store lacks (never changing one it has) and replaces the note, which now ends with a `<!-- /kandalf -->` mark and is also kept current in `GEMINI.md`.
- While the map is showing, the Backlog and Plans buttons are greyed out: the map shows both boards together.
- A new store's `plans.md` header, and the note `kandalf init` leaves for agents in the app, say how to file masters, sub-stories, domains and mentions so the map draws well.
- A **Map** view beside the board (the Board / Map switch in the header): every keyed story on plans, backlog and the archive as a map. A master is a hub with its sub-stories round it and a ring that fills as they are done; mentions and linked tiles are lines between stories; colour says where a story is (an archived one is done) and what its session is doing. Stories to pull next pulse. Click a story to bring it to the middle with what it is joined to, hop from the panel, Esc to step back, and Open tile for its card. Drag to pan, scroll to zoom, Ctrl-drag (or ⌘-drag) a cluster or a domain to move it; the arrangement is remembered in the browser.
- Stories are grouped by **domain** on the map, with a chip per domain to show only its stories. A story's domain is its own `Domain: name` line, else its master's, else the first word before the colon in its title when several stories share it.
- Story worktrees go inside the app by default, at `myapp/.worktrees/pln-74`, instead of beside it (`myapp-pln-74`). Pickup keeps `.worktrees/` out of the app's git status through its `info/exclude` when the app doesn't ignore it yet. A store whose `board.json` sets `"worktree"` keeps its own pattern, and a story already picked up keeps its worktree; set `"worktree": "{app}-{key}"` to cut new ones beside the app again.
- Where worktrees go is a setting in the ⚙ panel (Worktrees, under Git and commits), checked as pickup checks it: it needs `{key}` and may climb at most one folder.
- `kandalf serve` says where a story's worktree goes (`in worktrees at …/myapp/.worktrees/{key}`), instead of always saying "beside" the app.
- The settings panel's Model, Trunk and Worktrees fields get a ▾ list of choices that always shows them all (the browser's own suggestions showed only what matched the field, so a filled field showed nothing). Worktrees lists three places with where a story's worktree would go in your app, and its ⓘ says how to write others in `board.json`.
- The board no longer redraws behind the open settings panel, which closed its dropdowns every few seconds; it catches up when the panel closes.
- A pressed filter on the stats strip (at work, need you, …) is filled in its colour, and the others dim, so it's plain which one the board is showing.

## 1.2.0

- Pickup can hand stories to the Antigravity CLI (`agy`): set `"agent": "agy"` in `board.json`, pass `--agent agy` to `kandalf init`, or switch an existing store with `kandalf agent agy`. Antigravity sessions run detached with `agy --output-format stream-json --print`, get the store with `--add-dir`, receive the board's hooks in `.agents/hooks.json`, and open in `agy --conversation` on `kandalf attach`.
- An Antigravity store's `auto` and `bypassPermissions` both run `agy --dangerously-skip-permissions`, and its `model` takes the short names `neon`, `argon`, `hydrogen`, `flash`, `opus` and `sonnet` (each that model's `-medium` variant).

## 1.1.15

- A story ready for review pulses green, even once its session has stopped; red is kept for something
  wrong: a session gone without the story being ready, or a Done tile not merged.
- Added `GLOSSARY.md`, the words the board, the code and the docs share.
- The `Base:`, `Tests:`, `Repos:` and `Model:` lines are now read on boards with CRLF line endings.
- A `Model:`, `Sessions:` or `Base:` line with its value on the next line no longer counts as set.
- `kandalf key assign` now gives a key to unkeyed tiles that start with `* ` too.
- A merge reply for a sub-story whose master is archived puts the "Full suite" sub-task on the sub-story's own tile, not the master's.
- `kandalf agent` writes `board.json` while it holds the jobs lock, and leaves a job that was pausing lost, not paused.

- `kandalf agent` run while the server is looking at the board no longer loses either one's
  changes to the pickup jobs: every change to `board-pickup.json` takes a lock on
  `board-pickup.lock` beside it, which the other process waits for (where the OS has `fcntl`).
- The tile dialog's "Full suite on merge" box takes the nearest master's `Tests:` line even when that master is on
  another board (the backlog or the archive), as a merge does; the server's tile index now carries each tile's own
  `Tests:` value. A session line the page didn't recognise (an unindented one, or an id under six hex digits) no
  longer counts on the server either.
- A tile that starts with `* ` is picked up like one that starts with `- `: the server reads both
  the way the page does, so `* [ ] PLN-07` has a key and goes to Claude when it lands in In flight.
- A description that quotes "Claude session `abc`" no longer counts as a session line, so adopting
  and tidying a story ignore it; only the indented line the server writes does.
- Every tile in a Done column counts as done for the red unmerged outline, with a checkbox or
  without, as it does on the page.
- A sub-story whose master has been archived still gets the master's tile to read, and a ring of
  masters that name each other is broken the way the page does it: the first stands as the master.
- The tagline shows at the foot of the board in every theme, not only Realm ledger and Sticker pop.
- Every theme has a dark palette, with accents that read on it. A button beside the swatches picks
  System (follow the computer, the default), Light or Dark, kept in this browser. Classic's dark
  mode follows the same button.
- The theme menu and its swatches have room for their focus rings, and the header's status line
  gives way before the header wraps.
- Each setting in the settings panel, and each fact under About this store, has an ⓘ beside its
  name that says what it means, on hover or keyboard focus.
- The settings button is a square at the far right of the header, after the status line.
- The tile dialog's fields sit in a grid, each labelled above its control, with the two
  checkboxes on a line of their own; neither dialog opens with a focus ring on its first control.
- A store that sets nothing no longer runs the full suite when a master's last sub-story merges; set
  `fullSuiteOnMerge`, or put `Tests: full` on the master, to keep it.
- A ⚙ settings panel in the board header edits the store's `board.json` settings (pickup, agent, model, permission,
  stories at once, sessions, full suite on merge, trunk, tidy, push, commit delay), shows each default and resets it.
- Pickup follows `board.json`'s `"pickup"` without a restart: the server reads `board.json` again when it
  changes, picks up the tiles already in flight when pickup comes on, and leaves sessions alone when it goes off.
  `/api/settings` reads and writes the settings the page can change.
- Every story's session is told to run only the tests that cover what it changes (a sub-story
  was already), and the default Gates sub-task reads "lint, the tests for what it changes, and
  the review". The full suite is nightly's, or a merge's when the store or the tile asks.
- A merge from the board runs the full suite on the trunk before the push when `"fullSuiteOnMerge": true`
  in `board.json` or the tile has a `Tests: full` line (`Tests: affected` says not to). `subStoryTests` is
  retired and `kandalf doctor` says so.
- A tile's dialog has a "Full suite on merge" box that writes or removes the `Tests:` line, and a tile that asks
  for the full suite shows a chip.

## 1.1.10

- The page's bottom right corner says which kandalf is serving it: the release's number for a
  build of its tag, else a dev build with when it was built (and its commit in the tooltip).
- A story ready for review with nothing to merge (a master whose sub-stories did the work) has
  a Close button in place of Squash, Merge and Push: its session moves the tile to Done without
  them.
- A master story whose last sub-story leaves In flight is picked up then, as a tile that just
  landed is. It used to sit in flight with no session, no status and no buttons until the
  server restarted.
- A theme's colour swatch recolours the whole page, not only the buttons: the chosen colour
  washes the page, the header and the columns (Realm ledger keeps its open columns), so each
  swatch is a colour scheme. Tiles stay as they are.
- A story can name the branch it is cut from and merged into: a `Base: feature-x` line on the
  tile, written by the new Base field in the tile dialog, which suggests the app's branches. The
  review steps, the full suite after a last sub-story, tidying and the red unmerged outline all
  go by that branch; a sub-story takes its master's. A branch the repo hasn't got falls back to
  the trunk, and the session is told.
- The trunk defaults to `dev` when the app has a dev branch (else `main`, as before), both for
  `kandalf init` and for a store whose `board.json` names no trunk. A store's own `trunk` still wins.
- A sub-story runs only the tests for what it changes, not the whole suite. The full suite runs
  on the trunk once, when the last of its master's sub-stories is merged from the board: that
  merge reply adds a step, after the merge and before the push, and a **Full suite** sub-task on
  the master's tile. Merging an earlier sub-story says the suite waits, and which are still to
  merge. `"subStoryTests": "all"` in `board.json` keeps every tier on each sub-story.
- A story merged from the board moves to Done. When a review reply with **Merge into** ticked
  goes through and nothing is outstanding (every sub-task ticked, every step done), the session
  moves its tile to the top of Done, as the board's header says to close one; before, it was told
  to leave the tile in In flight. With something outstanding the tile stays and its `Handoff:`
  line says what. The session isn't paused for moving its own tile out of In flight.
- A run of tests is a sub-task on the tile while it runs. The default rules already add a last
  **Gates** sub-task; now a later run (after a rebase, a fix or a reply) unticks it or adds a new
  unticked one first, and every reply from the board says so, whatever the store's `pickup.md`
  says. A story no longer reads as all ticked while its tests are still being checked.
- The stats strip counts a story that is ready for review under **ready for review** even when
  its session stopped to wait for you or has gone; it was counted under **need you**, so a
  finished story could show as 0 ready.

## 1.1.5

- A stats strip runs under the header of the plans board while pickup is on: how many builds are
  at work, need you, are ready for review, queued, paused, idle, or done but not merged. A count's
  tooltip lists its stories, and a click narrows the board to them (click again for all).
- The board keeps its place when it is drawn again: pressing **Status** (or any redraw, such as
  an edit picked up from disk) no longer sends every column back to its top. The Status button
  spins until the check is done, and a tile open for editing keeps its caret.
- A Claude session's log on its tile is laid out as its own screen is: `❯` what it was told, `⏺`
  what it said and `⏺ Tool(input)` for each tool, `⎿` under it for the result. Colour codes,
  image notes and "Shell cwd was reset" lines are left out.
- A paused story has **◌ Queue** beside Continue: it goes to the back of the queue and a new session
  takes it over from its handoff in its turn.
- A sub-story picked up on its own is given its master's tile to read (and that master's master's),
  wherever the master sits: another column or the backlog. A design or decision linked only from
  the master reaches the session. The master's `Model:`, `Repos:`, `Sessions:` and `No pickup`
  lines still apply only when the master is in flight itself.
- `"parallel"` in `board.json` sets how many tiles on their own have a session at work at once
  (default 1, as before); a master's sub-stories keep `"sessions"`.
- A queued tile has a **▶ Start now** button beside its queued mark: it starts the session at once,
  in parallel, without asking again.
- A Claude session that stops mid-turn to ask something (a question or a permission prompt: the
  CLI lists it as waiting or blocked) shows on its tile as **waiting for you**, with its message
  box open and Copy attach beside it, instead of as working, or as gone when it had no status.
- A session's log on its tile, in the message box: what it said, the tools it ran and their
  results, refreshed every few seconds while the box is open, with **↗ window** to open it in a
  tab of its own. Claude's is read from its transcript, Copilot's from the board's record.
- Every session the board can reach, Claude or Copilot, has a message box: open when it waits for
  your input, folded behind a chevron otherwise. A message to a session at work interrupts it: it is
  stopped mid-step, given the message, and told to act on it and carry on. A folded box you open
  stays open while the board redraws.
- The board's hooks are part of every store, not a question: `kandalf init` adds them (`--no-hooks`
  leaves them out and records `"hooks": false`), and `kandalf serve` adds them to a store without
  them when it starts. An app with a `.claude/settings.local.json` of its own gets them merged in,
  keeping the rest. The old hooks that ran `board` in their `command -v board` form are found and
  replaced too: they had quietly done nothing since the command became `kandalf`.
- A board file that differs from the board's own layout only in blank lines (a tile written straight
  under its column's heading, a blank line between two tiles) opens as a board instead of
  view-only, and the board puts the blank lines right when it next saves. `kandalf check` and its
  hook pass it too, and when they do fail they point at the first line that really differs.
- A reply box on a tile whose session waits for input, or whose story is ready for review:
  type and **Send**, and it goes into the same Claude or Copilot conversation. On a story ready
  for review, **Squash**, **Merge into** the trunk and **Push** checkboxes tell the session to do
  those, in that order (merge is a rebase and fast-forward). The box keeps what you type while the
  board refreshes. Pause works in an opened tile too.
- **Pause** on a tile's session block while it is at work: the session finishes or undoes its
  step, commits, writes a full `Handoff:` line on the tile and stops, and the story is paused,
  giving up its place in the queue. Moving a tile out of In flight while its session is at work
  pauses it the same way, and moving a paused tile back in queues it. **Continue** on a paused
  story has a new session take over from the handoff, so it can come back on another day, after
  a restart or on another agent. **Pause now** stops a session that hangs on its handoff, and a
  paused story queued again gets **Start now**. A paused or queued job is never dropped from the
  board's job list, however old.
- A session at work gets Pause instead of Continue, and a gone one gets Continue instead of
  Restart: Continue is the button for every stopped session (idle, paused or gone); Retry stays
  for one that never started.
- A Claude session's tile line and the console name `kandalf attach <id>`, as Copilot's do, instead of
  `claude attach <id>`; for a Claude session it hands over to `claude attach`. Tiles that already
  say `claude attach` still work.
- A tile's session block has **Continue** while its session is at work or idle: it wakes a stuck Claude or Copilot session by stopping it and resuming the same conversation, told to carry on. It replaces Restart on an idle session; a gone one keeps Restart.

## 1.1.0

- `kandalf uninstall` removes the `docs/plans` link from each of the store's repos; the store itself is left alone.
- `kandalf init` and `kandalf repo add` add a kandalf section to the repo's `AGENTS.md` and `CLAUDE.md` (made if missing), so agents know what to do when told "add a story to kandalf", "kandalf new story" or "add to board".
- `kandalf init` makes the store in a folder named for the name you give it, beside the repo (`<repo>-plans` when the name is left at its default; `kandalf init DIR` still picks the folder).
- A tile in flight that says `No pickup` is picked up once those words are taken out, and the console says when one lands in flight held back. The board's instructions tell agents not to write `No pickup` themselves.
- Deleting a tile that has a session asks first, naming its Claude or Copilot session, worktree and branch, then stops and removes the session, removes the worktree and deletes the branch unless it has commits of its own.
- Pickup can hand stories to the GitHub Copilot CLI: set `"agent": "copilot"` in `board.json` (default `claude`).
- `kandalf init` asks which agent pickup uses (`--agent claude|copilot`), and `kandalf agent NAME` switches an existing store; a Copilot store's default model is `auto`.
- Copilot pickup sessions get the board's hooks, linked into their worktree as `.claude/settings.local.json`.
- Copilot sessions can be watched: `kandalf attach ID|KEY` shows what the session said and the tools it ran, follows it while it works, then opens the conversation with `copilot --resume`. Status and the tile say when it ended on an error or a question.
- A tile already in In flight with no session when the server starts is picked up, and the startup list says when a tile's `No pickup` keeps it from a session.
- Copilot sessions are given the store's folder (`--add-dir`), so they can read and write the board and plan docs through `docs/plans`; `permission` `bypassPermissions` runs Copilot with `--allow-all`.
- `kandalf attach` opens a finished Copilot session with the folders and permissions it ran with, and says to quit it before Restart: while it is open the board can't resume the session.
- A Copilot session in `auto` mode runs with `--allow-all-paths` too, so it may reach files outside its worktree and the store, as Claude's auto mode does.
- The board's messages are toasts in the bottom-right corner instead of a banner across the top: they stack, go after 5 seconds, stay while the pointer is on them, close with ×, and a Status check reads a fact a line with the last steps on the session's screen.
- `kandalf status KEY` prints the Status line for a story's session.
- The board names the agent in use (Claude or Copilot) on tiles, buttons and messages, and a Copilot store writes ``Copilot session `id` `` on the tile.
- The favicon is Kandalf's wizard hat.

## 1.0.0

The first public release.

- A drag-and-drop kanban board over markdown files (`plans.md`, `backlog.md`), served on
  localhost, with plan docs, an archive, tile keys, master stories and sub-stories.
- Pickup: a story dragged to In flight starts a background Claude Code session in its own
  git worktree. The tile shows the session, its progress and when it is ready for review;
  archiving a merged story removes its worktree, branch and session.
- Columns can be renamed. In flight is any column named like it (In progress, in-flight, Doing,
  WIP, Working, Underway, Ongoing, Active, and slips such as "In progrees"); Done is any named
  Done, Shipped, Complete(d), Finished, Closed, Merged, Released, Deployed, Delivered or
  Resolved. A name that says "not" is neither. A board has one of each: renaming a column into
  a second is refused, and `kandalf check` fails a board with two.
- Several repos per store. `board.json`'s `repos` names other repos beside the main `app`, each
  with its own trunk, and a tile's `Repos: api, web` line says which a story works in. Pickup
  cuts a worktree in each, starts the session in the first one's and gives it the others with
  `--add-dir`. Tidy, the merge check and Status cover every repo. The tile dialog has a Repos
  picker, and `kandalf repo add|list|rm` manage the list.
- The `kandalf` command: `kandalf init` makes a store and links it into a repo, `kandalf serve`
  serves it, `kandalf doctor` says what is missing, `kandalf where` names the store and its
  repos. A clone runs it as `bin/kandalf`.
- The board's Claude Code hooks live in the store's `.claude/settings.json`. Pickup sessions
  load them with `--settings`, so sessions in worktrees run them too, and the app links them in
  as a gitignored `.claude/settings.local.json`. `kandalf hooks --add` adds them to a store.
- Themes, picked from the board's header and remembered in the browser: Clean and light (the
  default), Classic (with dark mode), Aqua bubble, Sticker pop, Realm ledger and Soft and
  sunny, each with its own accent colours and a colour for every plans and backlog column.
  Their fonts are bundled (Latin subsets, OFL), so the board needs no network.
- A batched commit queue for the store, pushed after each commit.
- Every convention with a default is a `board.json` setting, and a store's `pickup.md` sets
  the working rules its sessions are given.
- Safe by default: pickup is off until a store turns it on, and the server answers only
  its own page.
