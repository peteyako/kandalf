# Plan and Build: renaming the two boards

## Why

The two boards are called Backlog (`backlog.md`) and Plans (`plans.md`). Neither name says
what the board is for any more. The backlog is not a passive pile: it is sorted by kind, and
decisions are researched, answered and archived on it without ever leaving. The plans board is
where stories are built, not planned, and its name collides with "plan docs".

The boards become **Plan** (work out what to build) and **Build** (build it), and the files
are renamed to match, so the name on screen and the name on disk agree.

## Goals

- The backlog board is **Plan**, in `plan.md`. The plans board is **Build**, in `build.md`.
- Every place a person or an agent reads a board's name says the new one: the page, the map,
  the CLI's output, commit subjects, the README, the templates and the filing rules.
- An existing store is moved by `kandalf serve`, on its own, the first time it is safe to.
- A store is never left with a board under both its old and its new name.

## Not in this version

- Renaming the store (`<repo>-plans`), the `docs/plans` link or `archive/archived.md`.
- Renaming columns ("To do", "In flight", "Done", the kinds), the `PLN` key, or `board.json`
  keys.
- Renaming the Promote and Archive actions.

## Anti-goals

- Renaming a board while a session is in the middle of a turn that may write the old file.
- Renaming files in a folder that is not a kandalf store.

## Names

| Was | Is | File was | File is |
|---|---|---|---|
| Backlog, the backlog board | **Plan**, the plan board | `backlog.md` | `plan.md` |
| Plans, the plans board | **Build**, the build board | `plans.md` | `build.md` |

- In the page's header the two buttons read **Plan** and **Build**, Plan first.
- The drop zone "Back to backlog" becomes **Back to Plan**; "Promote" still moves a tile to
  the top of To do, now "on the Build board".
- On the map, the colour "Backlog" becomes **Plan** and "Planned" becomes **To build**.
- Commit subjects name the new files: `board: PLN-26 plan.md → To do`.
- "Plan doc" keeps its meaning (a document in `active/`); the Plan board is always written
  "the Plan board" or `plan.md` where the two could be confused.

## Which names a store uses

A store's two board files are a fact about that store, read from its folder each time they
are asked for (two file checks), so a move in the middle of a run is seen at once:

- `build.md` there: the new names.
- else `plans.md` there: the old names. Everything works on them as it does today.
- neither: the new names (a new or empty store).

The buttons and every label say Plan and Build whichever names the files have. A store on the
old names differs only in its file names and in the notice that says it will be moved.

Every module asks the store (`store.build`, `store.plan`: the file names) and none names a
board file itself. `tiles.FAMILY`, `PICKUP_BOARD`, `MOVES`, `PULLS`, the hooks' board list and
the key counter become functions of the store. The page is told the two names by the server
with the files list, and uses them in place of its constants.

This is what lets a store that cannot be moved yet keep working, and what a folder with no
`board.json` (`kandalf serve --dir ~/notes`) relies on: such a folder is never renamed.

## Moving a store

`kandalf serve` moves a store by itself. There is no command to run.

**When.** At start, and then on each look of the watch, a store is moved when all of these
hold:

- it is a kandalf store (it has a `board.json`) and uses the old names;
- no session is in the middle of a turn. When the ledger holds no job with a session and no
  draft at work, that is known without asking. Otherwise the agent's sessions are listed, and
  a session listed as busy blocks the move; so does a list that cannot be read. A session
  that is idle, or stopped mid-turn waiting for a person, does not block it: it writes nothing
  until it is spoken to, and the first thing it is told is the rename (below);
- no file named `plan.md` or `build.md` is already there. A store that has its own `plan.md`
  is not moved, and the notice says that file is in the way;
- neither old file has changed in the last few seconds (the settle rule the watch already
  uses), and the server is not itself in the middle of a write.

While it waits, the page shows a notice: "These boards will be renamed Plan and Build once
PLN-41's session finishes its turn." Nothing else is different.

**What.** In one commit, `board: plans.md → build.md, backlog.md → plan.md`:

1. `git mv` the two files (a plain rename when the store is not a git repo).
2. Rewrite links to them: in the two boards, the archive log and every plan doc in `active/`
   and `archive/`, a markdown link whose target is `plans.md` or `backlog.md` (with any `../`
   or `./` before it) points at the new file. Link text that is exactly the old file name is
   renamed too. Prose is left alone.
3. Bring the filing rules up to date as `kandalf notes --update` does: the board header rules
   and the note for agents in each linked repo, which name the files.
4. Where a board's header still reads exactly as the old template wrote it, it is replaced
   with the new template's, which says Plan and Build. A header someone edited keeps its
   words.
5. Record the move in the store's ledger, with the time. This is written last.

The move can stop at any step and be picked up again. `plans.md` is renamed first: from then
on the store reads as on the new names. A store on the new names that still has `backlog.md`
and no `plan.md`, or whose ledger has no record of the move, is finished off on the next look
under the same conditions: steps 2 to 4 change nothing the second time. A store made by
`kandalf init` gets the record when it is made.

The filing-rules note is updated in each linked repo's own checkout, as `kandalf notes
--update` does. Worktrees of stories already under way keep the note their branch has; their
sessions are told the new names as below.

The page, on its next poll, sees the names changed and reloads onto the board it was showing
under its new name.

**Sessions that started before the move.** A session that is idle, paused or waiting for a
review was told the old names. Every message the server sends it afterwards (a reply, Continue,
a nudge, a merge go-ahead) begins with one line: "The boards were renamed since you started:
plans.md is now build.md and backlog.md is now plan.md." A session started after the move is
told the new names from the start.

**If an old file comes back.** In a store on the new names, a `plans.md`, or a `backlog.md`
beside a `plan.md`, means something wrote the old name. It is never read as a board. The edit
hook (`kandalf hook check`), which opens the store the edited file is in to learn its names,
hands the error straight back to the session that wrote it: "plans.md
is now build.md: put your change there and delete plans.md." The page shows a notice naming
the stray file until it is gone. Nothing is merged or deleted automatically.

## What else changes

- **Templates and `kandalf init`:** a new store gets `plan.md` and `build.md`.
- **CLI:** output and help text use the new names; `kandalf check`, `kandalf key`, `kandalf
  doctor` and `kandalf init`'s "there is a store here already" check work on whichever names
  the store uses.
- **Links and bookmarks:** the page maps `?file=plans.md` and `?file=backlog.md` onto the
  store's present names, so an old bookmark opens the right board.
- **History:** whatever reads the boards' git history (when a tile was first committed) reads
  the old file names' history as well, so a tile keeps its dates across the move.
- **Docs:** the README throughout; CHANGELOG; AGENTS.md's ground rules.
- **Tests:** fixtures use the new names; a set of tests keeps a store on the old names to
  hold the "works unmoved" promise, and the move has its own tests.
- **Version:** this is part of 1.3.0. It lands on `dev` before the release commit, and its
  changelog lines go under the same heading as the rest of 1.3.0, first, since it is the
  change every store will notice. The release commit itself is not this work's.

## Approaches considered

1. **An explicit `kandalf migrate` command.** Safe and visible, but a step to remember, and
   until it is run a person sees old names in a tool that says new ones.
2. **Move automatically when `kandalf serve` starts** (chosen), tempered by the rule above: it
   waits while a session is mid-turn rather than renaming under it.
3. **Read both names for ever.** Nothing to move, but `plans.md` (the old Build board) and
   `plan.md` (the new Plan board) would sit one letter apart indefinitely.

## Constraints

- Standard library only; the page stays one file; a rule written in both readers stays in both
  with shared cases.
- No module but the agent modules runs an agent; the move lists sessions through the adapter
  pickup already has.
- The move is one commit and is safe to interrupt: if it stops part-way, the next start sees
  either the old names (and tries again) or the new ones.

## Left to the builder

- How the store's two names are carried to the functions that today use the constants.
- The exact wording of the notices.
- Whether the map's colours are renamed in the same change as the header buttons.
