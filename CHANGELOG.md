# Changelog

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
