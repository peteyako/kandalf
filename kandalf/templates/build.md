# Build

This file is the Build board, the board that tracks every plan for {name}. Open it with `kandalf serve`. The plan docs live beside it: open ones in `active/`, finished ones in `archive/`.

- **Its own repo:** the board and its plan docs are a plans store, tracked apart from the app and linked into it at `docs/{link}`. The board server commits each save and archive here; commit anything else yourself with `git -C docs/{link} commit -- <paths>`, and never commit plan changes to the app repo.
- **A plan** is a doc in `active/` named `YYYY-MM-DD-slug.md`. Only this board, the Plan board and `board.json` sit at the top of the folder.
- **Keys:** every tile carries a sequential `{key}-NN` key at the front of its line, never reused (archived tiles keep theirs). `kandalf key next` prints the next free one; the board fills it in for a tile you create there, and `kandalf key assign` keys any tile added without one.
- **Filing a plan:** write the doc in `active/` with the key in its title (`# {key}-NN: Title`), then add a story at the top of its kind's column on the Plan board, [plan.md](plan.md): `- [ ] {key}-NN **Title.**`, an indented description that links the doc, then indented `  - [ ]` sub-tasks for its steps.
- **Priority and estimate (both optional):** after the key, in this order: `- [ ] {key}-NN P2 ~2-3d **Title.**`. Priority is `P1` (Highest) to `P4` (Low). The estimate is wall-clock time in hours, days or weeks (`~4h`, `~1.5d`, `~12-18h`, `~1w`).
- **Columns are statuses:** Bugs, To do, In flight, Done. Rename them if you like (In progress, Shipped), but keep one In flight column and one Done column. Unscheduled work waits on [plan.md](plan.md); to schedule a tile, drag it onto that board's Promote column, which moves it to the top of To do. Newest tiles go at the top of their column.
- **Stories that belong together:** a sub-story says `Part of {key}-NN` on an indented line of its own and folds under that master tile; `Moves with {key}-NN` makes two tiles move as one. The board's Map view is drawn from these lines, so file them with care:
  - **A master** is a tile of its own that says what the whole piece of work is; its steps are its sub-stories, not sub-tasks. Aim for three to nine sub-stories, and split a bigger one into two masters. A sub-story has one master, and one level is enough: don't hang sub-stories off a sub-story unless the work really nests.
  - **A domain** is the part of the product a story is in (Admin, Billing, Platform). Give every master, and every story that stands alone, an indented `Domain: Name` line; a sub-story takes its master's and needs none. Use a domain the board already has, spelt the same way, before making a new one, and keep the name to one or two words.
  - **Titles** start with the domain when that reads naturally (`Admin: operator review`), and say the rest in a few words: the map shows only the first twenty or so characters of a sub-story's title.
  - **Naming another story's key** in a tile draws a line between the two on the map. Name a key where the stories really bear on each other (one needs the other's work, or they change the same thing), and say how in a few words; leave it out of passing remarks. A sub-story doesn't need to name its master again.
- **Waiting and deciding:** a story that can't start until other tiles are done says `Blocked by {key}-NN` (or `{key}-NN, {key}-NO and {key}-NP`) on an indented line of its own; it waits in In flight until each is in Done or archived. A choice that blocks stories goes in the Design decision column of [plan.md](plan.md) as a tile of its own: it is answered on its card, never promoted, and the answer is written on it as a `**Decision YYYY-MM-DD:**` line, so the stories that wait on it say `Blocked by` and its key.
- **Drafting options:** a tile in a decision column has a Draft options button on its card: a background session researches the question and the board writes an `**Options YYYY-MM-DD:**` line and up to four `- **A. Title.** summary` lines onto the tile. That block is the board's to write; an agent working a story never edits it, and the answer is still given on the card.
- **In flight means an agent starts on it** when pickup is on in `board.json`. A tile that says `No pickup` is left alone until those words are taken out; only a person writes them, never an agent filing or editing a tile. The session ticks the sub-tasks as it goes and ends with a `**Ready for review:**` line.
- **Closing a plan:** tick its sub-tasks, move the story to Done, and name its feature branch in backticks: a Done tile whose branch isn't merged into `{trunk}` (or the branch its `Base:` line names) yet is outlined red.
- Keep this file free of `###` headings and code blocks, or the board opens it view-only. Cards in a column sit back to back; a blank line between them, or a missing one under a heading, the board puts right when it next saves. `kandalf check build.md` names the first line the board would reject.

## Bugs

## To do

## In flight

## Done
