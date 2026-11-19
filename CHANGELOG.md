# Changelog

## 1.1.0

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
