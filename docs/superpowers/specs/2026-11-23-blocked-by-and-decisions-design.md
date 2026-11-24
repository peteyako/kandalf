# Blocked by, and decisions answered in place: design

## Why

The backlog's Design decision column holds questions, not work. A decision holds up stories
in other columns until someone answers it, and today kandalf can say neither thing: no tile
can name what it waits on, and a decision is only a tile in a column like any other. Dragging
one to In flight would start a session in a worktree on a branch, which a decision has none of.

This design gives a story a way to say what it waits on, and gives a decision a way to be
answered where it sits. A later design adds **Draft options**: a read-only session that
researches a hard decision and writes options onto its tile. It is not part of this one.

## Goals

- A story says `Blocked by PLN-30` and is blocked until PLN-30 is done. Any keyed tile can be
  a blocker, not only a decision: "land it after PLN-33" is the same relation.
- Blocked is read, never stored. Nothing is written to the blocked tile when it unblocks.
- A blocked tile shows a lock and what it waits on. Pickup doesn't start a blocked tile that
  lands In flight; it starts it once the last blocker is done.
- A decision is answered on its tile, from the page, without a session. Answering it files it
  as done, which unblocks what waited on it.
- The answer reaches every story the decision blocked: shown on their tiles, and given to
  their sessions when they are picked up.

## Not in this version

- Draft options (the next design).
- Starting a blocked story anyway from the page. Take the `Blocked by` line off to override.
- Refusing a drag of a blocked tile to In flight. It lands and waits, as a hand edit would.
- Dependency arrows with their own look on the map beyond what is said under **The map**.
- Copying the answer's text into the tiles it unblocked.

## Anti-goals

- A second place where blocked state lives (the ledger, a flag on the tile) that can disagree
  with the board.
- A decision that needs a branch, a worktree or the In flight column to be answered.

## The grammar (twin rules, tiles.py and board.html)

Three new rules. Each gets cases in `tile_cases.json` or `view_cases.json`, run by both readers.

1. **`Blocked by PLN-30`** anywhere in a tile's text, with the same key list as `Moves with`
   (`PLN-30, PLN-31 and PLN-32`). Case-insensitive. A tile's own key is ignored. `Tile` gains
   `blockers`, the keys in the order written, each once. Several `Blocked by` phrases add up.
   The phrase is not read from lines the server or a session writes: the session line, Status
   lines, the Ready for review line and decision lines. Only the store's own key prefix is
   read, as for `Moves with`.
2. **The decision line**: an indented line of its own, `**Decision 2026-11-23:** retire it.`,
   read like the Status line. The reader returns its date and text; the last one on a tile is
   the answer (an earlier one is a revised decision's history).
3. **A decision column**: a column whose name says decision (`Design decision`, `Decisions`,
   `Decide`), read by one regex in each reader, like `IN_FLIGHT_RE`. It says which tiles get
   the answer box, nothing more.

### When a tile is blocked

A tile is blocked while any key in its `blockers` names a tile that is not done. Done is what
`Tile.done` already means: in a Done column, or on the archive log. So:

- A blocker in Done or archived: not blocking.
- A blocker anywhere else on plans or backlog: blocking.
- A key no tile has (a typo, a tile deleted by hand): blocking, and shown as "PLN-99 not
  found", so a slip can't quietly let a story through.
- A sub-story is blocked by its own blockers and by its masters' (the lineage `No pickup`
  already reads through).
- A ring (A blocked by B, B blocked by A) leaves both blocked, and the page's lock line says
  so ("PLN-30 and PLN-41 block each other"). `kandalf check` and the hook read one file and
  don't look for rings.
- Blockers are looked up on plans, backlog and the archive log (`FAMILY`), not on other
  boards a store may hold.
- Blocked is read fresh each time, so a decided tile that is restored from the archive blocks
  again. A story already at work carries on.

`Snapshot` answers this in one place: `blocking(key)` returns the keys still blocking a tile
(with the unknown ones marked), and `decisions(key)` the done blockers of a tile and its
masters that carry a decision line. Both read the whole snapshot, so a master or a blocker on
the backlog or in the archive counts. The page has the same two functions over the tile index
the server already sends it (`Snapshot.index`, which gains each tile's blockers, done and
decision text).

## Pickup

- Pickup's functions keep their signatures. `Snapshot.in_flight()` already reads the whole
  snapshot to give a tile its master's `context`; it gives each tile two more entries,
  `blocked` (the keys still blocking it) and `decisions` (the decided blockers' tiles).
- A blocked tile in In flight is held exactly as a `No pickup` tile is: left out of the keys
  the look counts, so it is not an umbrella, has no job, and arrives as new on the look after
  it unblocks. Logged once ("PLN-41 is in flight but blocked by PLN-30: left alone until it is
  done"). `/api/pickup`'s state lists it under `blocked: {key: [blockers]}` beside `held`, so
  the page can say why nothing started.
- Each look re-reads the boards, so the tile is picked up on the first look after its last
  blocker is done, as a `No pickup` tile is once the words go. No event is needed.
- A tile that already has a session is not stopped if a `Blocked by` line is added later, and
  is not listed under `blocked` (as a live tile is never `held`). The lock shows on the tile
  from the board's own text; the session carries on.
- **The answer travels in the prompt.** When a job is queued, each done blocker of the tile
  and its masters that carries a decision line is appended to what the session is told, after
  the master's tile if there is one, with a sentence saying these decisions were made before
  the story started and are to be followed. This reuses the job's `context` text; a blocker
  that is done but has no decision line (a plain "after PLN-33") is not appended.

## Answering a decision

- A tile in a decision column shows an **Answer** box on its card (a textarea and a Decide
  button, built like the session reply box), on whichever board the column is. The column
  regex is its own rule and doesn't lean on the CSS `data-kind`, which only picks a colour.
- Decide posts what Archive posts (`{mtime, text, tile, docs}`: the board's text without the
  tile, the tile, and its plan docs) plus `answer`, to a new `POST /api/decide`. An unkeyed
  tile is given the next key by the page first, as Promote does. The server, in one change:
  adds the `**Decision <today>:** <answer>` line to the tile by its own clock (the answer on
  one line, newlines folded to spaces), ticks the tile's box, then archives it by Archive's
  own path, tidying included (plan docs move, links are rewritten, the tile is filed at the
  top of `archive/archived.md`). A link to a plan doc inside the answer is rewritten like any
  other link in the tile, so it still resolves from `archive/`; nothing else on the decision
  line changes. The autocommit subject reads `board: PLN-30 decided`.
- An empty answer is refused. A stale `mtime` is refused as other saves are.
- The reply lists the keys this unblocked, and the page says so in its toast ("PLN-30
  decided: PLN-41 and PLN-44 are unblocked").
- Revising: an archived decision is edited where it lies, or restored and answered again; the
  newest decision line is the answer. Stories already at work are not told.

Archiving is the "done" for a decision because the backlog has no Done column, the archive
log already counts as done everywhere (the reader, the map), and the answer stays readable
there for the prompt and the page.

## What the page shows

On a tile with blockers, under its title:

```
PLN-41 P2 ~1d  Restyle the nav in the pro palette
🔒 Blocked by PLN-30 · Pro theme: catch up or retire it
```

Once PLN-30 is decided:

```
PLN-41 P2 ~1d  Restyle the nav in the pro palette
✓ PLN-30 decided: retire it
```

- One line per blocker. A blocker still open is a lock with its key and title, and a click
  opens that tile on its board. A decided one shows the answer's text, clipped to one line,
  and a click opens the archived tile. A done blocker with no decision line shows nothing.
- An unknown key shows "🔒 PLN-99 not found".
- A blocked tile in In flight with pickup on also says "waits for PLN-30" where a queued
  tile says its place in the queue.
- A decision tile lists what waits on it: "Blocks PLN-41, PLN-44", each a click.
- Icons follow the theme's existing icon set rather than emoji; the sketches above show
  placement only.

### The map

A blocker and what it blocks are joined, so clicking either brings the other in, and the
panel lists them under **Blocked by** and **Blocks**. A blocked story doesn't pulse as
"pull next". No new arrow style in this version.

## Approaches considered

1. **Send a decision through In flight** like a story, and talk to its session. Rejected:
   In flight means a worktree and a branch, which a decision has none of, and a one-line call
   would cost a session.
2. **Store blocked state** (a flag on the tile or in the ledger, cleared when the decision is
   answered). Rejected: two sources of truth, and a hand edit of the board leaves them
   disagreeing.
3. **A derived `Blocked by` line, and decisions answered in place** (chosen). One new relation
   that any tile can use, read fresh on every look; the decision's answer lives on one tile
   and is shown and handed on by reference.

Within 3, the answer could also be copied into each tile it unblocked, so raw markdown is
self-contained. Not chosen: a revised decision would leave stale copies.

## Constraints

- Standard library only; the page stays one file. Every grammar rule is written in both
  readers in the same commit, with shared cases.
- `claude_code.py` and its siblings stay the only modules that run an agent; this design
  starts no session.
- New page functions the tests need go inside the span `test_page.js` lifts.
- README: the tile-attribute table gains `Blocked by` and the decision line, and the pickup
  and backlog sections say what blocked and decided do. `CHANGELOG.md` gets its Unreleased
  lines. `kandalf notes` (the store's filing rules) and `templates/` mention `Blocked by` and
  that decisions are answered, not promoted.

## Left to the builder

- The exact regex for the decision column and the names of the new functions.
- Where the lock line sits in each theme, and its icons.
- Whether `blocking` is computed once per look in pickup or per tile through `Snapshot`.
- How `/api/decide` shares code with `/api/archive`.
