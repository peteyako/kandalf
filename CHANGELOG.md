# Changelog

## Unreleased

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
