# Glossary

The words kandalf uses for the board, its stories and the jobs that work them. Code names are in `backticks`.

## The board

- **Board**: a store's markdown files read as columns of tiles: `plans.md`, plus `backlog.md` and the archive log (`archive/archived.md`). `board.json` configures it.
- **Column**: a `##` heading of a board file and the tiles under it. The one named like In flight is the pickup column (`tiles.is_pickup_column`, `isPickupColumn` in `board.html`).
- **Tile**: one list item of a column with its indented lines, the card on the page. A keyed tile starts with a key such as `PLN-74` (`tiles.Tile`).
- **Story**: a tile that is a unit of work for an agent, one worktree and one branch (more than one with `Repos:`).
- **Master**: a story that sub-stories name in their `Part of` line. Its sub-stories each get their own session and the master gets none while any is in flight.
- **Sub-story**: a story that belongs to a master. It takes its master's `Base:`, `Repos:`, `Model:` and `Sessions:` lines unless it has its own, from a master in flight only; `Tests:` is taken from the nearest master anywhere in the family, backlog and archive included.
- **Attribute line**: an indented line of a tile that is a setting, such as `Model:`, `Base:`, `Repos:`, `Tests:`, `Domain:` or `Ready for review`. `tiles.py` has one reader for each.
- **Domain**: the part of the product a story is in, from its `Domain:` line, its master's, or a title prefix several stories share (`tiles.domain`; `tileDomains` in `board.html` applies the fallbacks). The map groups by it.
- **Map**: the page's other view of the boards: every keyed story as a node, masters as hubs, mentions and links as lines (`mapGraph`, `mapLayout` in `board.html`).
- **Session line**: the tile line naming the agent session at work, its id, branch and worktrees (`tiles.SessionLine`).

## Reading the board

- **Snapshot**: plans, backlog and the archive log read once, with the tile index and the answers that need the whole family (`tiles.Snapshot`). Ask it, rather than re-reading files.
- **Lineage**: the masters a tile is part of, nearest first (`Snapshot.lineage`). A line a sub-story lacks is taken from the nearest master that has it (`Snapshot.nearest`).

## Jobs

- **Job**: the record of one pickup, a story or a master with its sub-stories, from queued to ended, in `board-pickup.json`.
- **Ledger**: the store of jobs, `board-pickup.json` in the store's `.git`, written one locked transaction at a time (`jobs.Ledger`). A store that isn't a git repo keeps it as a dotfile in the store instead.
- **Transition**: a named change of a job's state, such as `begin`, `end` or `lose`, each holding its own rule (`jobs.py`). A job changes state only through one.
- **Sessions adapter**: the one object through which pickup starts, resumes, stops and lists agent sessions (`pickup.Sessions`). The tests swap in a fake.

## Git

- **Cut**: to make a story's worktree and branch from its base. A story that works in several repos is cut once in each.
- **Trunk**: the branch stories are cut from and merged back into: `dev` when the app has one, else its main branch.
- **Base**: the branch a story is cut from and merged into when it is not the trunk, set by a `Base:` line.
- **Pickup**: handing a tile in the In flight column to an agent: cutting its worktrees, starting its session and writing the session line (`pickup.py`).
