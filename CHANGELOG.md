# Changelog

## 1.1.10

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
