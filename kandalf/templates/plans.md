# Plans

This file is the board that tracks every plan for {name}. Open it with `kandalf serve`. The plan docs live beside it: open ones in `active/`, finished ones in `archive/`.

- **Its own repo:** the board and its plan docs are a plans store, tracked apart from the app and linked into it at `docs/{link}`. The board server commits each save and archive here; commit anything else yourself with `git -C docs/{link} commit -- <paths>`, and never commit plan changes to the app repo.
- **A plan** is a doc in `active/` named `YYYY-MM-DD-slug.md`. Only this board, the backlog and `board.json` sit at the top of the folder.
- **Keys:** every tile carries a sequential `{key}-NN` key at the front of its line, never reused (archived tiles keep theirs). `kandalf key next` prints the next free one; the board fills it in for a tile you create there, and `kandalf key assign` keys any tile added without one.
- **Filing a plan:** write the doc in `active/` with the key in its title (`# {key}-NN: Title`), then add a story at the top of its kind's column on the backlog board, [backlog.md](backlog.md): `- [ ] {key}-NN **Title.**`, an indented description that links the doc, then indented `  - [ ]` sub-tasks for its steps.
- **Priority and estimate (both optional):** after the key, in this order: `- [ ] {key}-NN P2 ~2-3d **Title.**`. Priority is `P1` (Highest) to `P4` (Low). The estimate is wall-clock time in hours, days or weeks (`~4h`, `~1.5d`, `~12-18h`, `~1w`).
- **Columns are statuses:** Bugs, To do, In flight, Done. Rename them if you like (In progress, Shipped), but keep one In flight column and one Done column. Unscheduled work waits on [backlog.md](backlog.md); to schedule a tile, drag it onto that board's Promote column, which moves it to the top of To do. Newest tiles go at the top of their column.
- **Stories that belong together:** a sub-story says `Part of {key}-NN` and folds under that master tile; `Moves with {key}-NN` makes two tiles move as one.
- **In flight means an agent starts on it** when pickup is on in `board.json`. A tile that says `No pickup` is left alone until those words are taken out; only a person writes them, never an agent filing or editing a tile. The session ticks the sub-tasks as it goes and ends with a `**Ready for review:**` line.
- **Closing a plan:** tick its sub-tasks, move the story to Done, and name its feature branch in backticks: a Done tile whose branch isn't merged into `{trunk}` yet is outlined red.
- Keep this file free of `###` headings and code blocks, or the board opens it view-only. Cards in a column sit back to back; a blank line between them, or a missing one under a heading, the board puts right when it next saves. `kandalf check plans.md` names the first line the board would reject.

## Bugs

## To do

## In flight

## Done
