# Draft options: design

## Why

A decision tile is answered on its card (see
[blocked-by and decisions](2026-11-23-blocked-by-and-decisions-design.md)). That works when
the answer is already in your head. For a decision that needs research first ("which packages,
which layering rule"), there is nothing to type yet. Draft options sends an agent to read the
code and come back with a few options and a recommendation, so that answering becomes picking
and editing rather than researching. The person still decides.

## Goals

- A **Draft options** button on a decision tile's card starts one background session that
  researches the question and writes a research doc. It needs no In flight column and no branch.
- When the session is done, the server writes a short **options block** onto the tile: two to
  four options, one line each, which one is recommended, and a link to the research doc.
- The card lists the options above the Answer box. **Use** copies one into the Answer box to
  edit. Nothing is decided until Decide is pressed.
- **Draft again** takes a note ("consider keeping the adapter half") and replaces the options.
- `kandalf attach PLN-60` opens the draft's session in a terminal to carry on in it. When the
  research doc changes afterwards, the tile's options follow.
- The session cannot leave changes in your code: it works in a throwaway worktree that is
  deleted with the draft.

## Not in this version

- Deciding for you. A draft never writes a decision line or archives a tile.
- A conversation on the board (a reply box for drafts). One shot, Draft again, or attach.
- Drafting for tiles outside a decision column, or for archived tiles.
- Splitting one decision into several tiles.
- Anything on the map.
- A read-only permission mode for the session. Its edits are discarded, not prevented, and in
  `auto` mode it has the reach outside its folders that a story's session has today.

## Anti-goals

- A session with write access in the checkout you work in.
- Options scraped from what the session said. The research doc is the only thing read.
- A draft that takes a story's place in the queue, or waits behind one.

## How a draft runs

1. **Start.** `POST /api/draft?file=<board>` with `{key, note}`. The tile must be keyed and in
   a decision column of plans or backlog (the page keys an unkeyed tile first, as Decide
   does). The server records a draft in the ledger and, when no other draft is at work, starts
   it; otherwise it is queued and starts when the one before it ends. One draft works at a
   time. Drafts are apart from story jobs: they don't count against `sessions`, and stories
   don't wait for them.
2. **Worktree.** For each repo the draft works in (the tile's `Repos:` line as a story reads
   it, else the main repo), a detached worktree of the repo's trunk:
   `git worktree add --detach <path> <trunk>`. The path is the store's worktree pattern filled
   as for a job whose key is `draft-<KEY>` (so `{key}` reads `draft-pln-30`), kept out of the
   repo's status and with the store linked in at `docs/<link>`, both as `pickup.prepare` does
   (the two share those steps; `prepare` itself always makes a branch, so a draft has its own).
   The trunk is the branch the tile's `Base:` line names when the repo has it, else the repo's
   trunk, as a story's is. No branch is made.
3. **Session.** One background session through `agent.start`, as pickup launches one: the
   tile's `Model:` line or the store's default, the store's permission mode, the board's
   hooks, named `<KEY> options`. It is told (see **The prompt**) to research and to write
   exactly one file, the research doc.
4. **Watching.** Drafts have a turn of their own in the server's watch loop, beside
   `watch_turn` and not inside it: `watch_turn` returns early when pickup is off, and a draft
   must still be followed. The drafts' turn looks when a draft is queued or at work and 20
   seconds have passed since its last look (the same `PICKUP_RECHECK`), or when the research
   doc of a draft that isn't over has a new mtime that has stayed the same for two turns (the
   settle rule the board's look uses). A session's turn is finished by the test a story's
   uses: listed as idle, with nothing it waits for. There is no timeout: the tag says how long
   a draft has been at work, and Stop ends it. On a look, a draft at work whose session is:
   - still busy: left alone;
   - idle, with a readable research doc: **ready**. The options block is written onto the tile
     (one commit, `board: PLN-30 options drafted`);
   - idle, with no doc or one with no option in it: **empty**. The tile's Options line says
     so and links the doc if there is one;
   - waiting on a question or a permission prompt, or ended on an error: **needs you**, with
     why (what `agent.waiting_for` says), as a story's session shows it;
   - gone from the session list: **lost**.
5. **Afterwards.** A ready, empty or needs-you draft keeps its session and its worktree, so
   `kandalf attach` can carry on in it. When its research doc changes (the drafts' turn above)
   and its session is not busy, the doc is read again and the tile's block rewritten (a draft
   that needed you becomes ready this way).
6. **The end.** A draft is over, its session retired and its worktrees removed
   (`git worktree remove --force`), when its tile is decided, archived, deleted or leaves
   the decision column, when **Stop** is pressed, or when Draft again starts its successor.
   Removing the worktree is what discards anything the session changed in the code. The
   research doc is in the store and stays; Decide and Archive move it to `archive/` with the
   tile, as they move any linked plan doc.

`kandalf attach KEY` opens the key's story session when it has one, else its draft's.

A draft leaves the ledger only after its session is retired and its worktrees are removed;
until then it is kept as ending, and the server's start-up finishes ending those and ends the
drafts whose tiles are gone. The ledger is the only record of a draft's worktrees: nothing
scans for folders by name.

Draft works with `"pickup": false`: it is an explicit press, not a tile being picked up. It
needs the rest of what pickup needs: `drafts.why_not(store)` gives the first reason it can't
run, from the checks `pickup.why_off` makes after its `pickup` check (an app repo that is
there, the naming patterns, the repos and agent settings, the agent's CLI installed and
recent enough), worded "Draft options is off: ...", plus a store whose `permission` is `plan`.
The two share the checks. When there is a reason the button is disabled and shows it.

## The research doc

Written by the session at `active/<date>-<key>-options.md` in the store (date: the day the
first draft started; lower-case key). Draft again rewrites the same file. Its opening is
fixed; the rest is free.

```markdown
# PLN-30 options: Pro theme: catch up with the new nav, or retire it

Recommends: B

## A. Restyle it

Sidebar card, profile box, tab bar and More sheet in the pro palette. ~2d, for a theme
nothing can switch on.

What it touches, risks, what was read...

## B. Retire it

Delete the app-shell rules in app-neo-pro.css. ~1h; the spectate pages keep their own pro look.

...

## What I read

...
```

What the server reads (`drafts.read_doc`, Python only):

- `Recommends: <letter>` on a line of its own before the first option. Optional.
- An option is a heading `## <letter>. <title>`, letters A to H. Its summary is the first
  paragraph under it, folded to one line and cut at 240 characters on a word.
- Other headings and everything else are ignored. A doc with no option heading is empty.
- More than four options: the first four are written to the tile.

## The options block (twin rule, tiles.py and board.html)

The server writes it after the tile's description and before its sub-tasks, replacing any
block already there:

```
  **Options 2026-11-24:** recommends B. [Research](active/2026-11-24-pln-30-options.md)
  - **A. Restyle it.** Sidebar card, profile box, tab bar and More sheet in the pro palette. ~2d, for a theme nothing can switch on.
  - **B. Retire it.** Delete the app-shell rules in app-neo-pro.css. ~1h; the spectate pages keep their own pro look.
```

- The **Options line**: indented, `**Options <date>:**`, then, each optional and in this
  order: `recommends <letter>.`, a link to the research doc, `Asked: <note>` (the note given
  to Draft again, on one line). An empty draft's line reads
  `**Options <date>:** the draft gave no options.` with the link if there is a doc.
- **Option lines** follow it directly: `  - **<letter>. <title>.** <summary>`. A line of any
  other shape ends the block. An option line has no tick box, so it is not a sub-task.
- Both readers give the same reading: `{date, recommends, doc, asked, items: [{letter, title,
  summary}]}` or nothing. Cases go in `tile_cases.json`.
- The block's lines are prose about choices, so the phrases that join tiles are not read
  from them: `Blocked by` (they join the lines that rule already skips), `Part of` and
  `Moves with`. Keys they name still count as mentions. The attribute lines (`Model:`,
  `Base:` and the rest) need a line of their own, which no block line is.
- The edit that writes the block (`tiles.with_options`) puts it where `with_session` puts
  the session line, after the description and before the first sub-task, and replaces a
  block already on the tile wherever it is.
- A block written by hand in this shape is read the same way: the rows and Use work without
  a draft.

## What the page shows

On the tile's face, one tag beside its key: `drafting options` (with the agent's icon),
`queued to draft`, `options ready`, `draft needs you`, `draft gave no options`, `draft lost`.
The option lines themselves are not shown on the face.

On the card, above the Answer box:

```
Options · drafted 24 Nov · Research ↗                      [Draft again]
  A  Restyle it. Sidebar card, profile box, tab bar…           [Use]
  B  Retire it. Delete the app-shell rules…  recommended       [Use]
```

- **No draft and no block:** a **Draft options** button, with a one-line note field beside it
  (optional: what to look at).
- **At work or queued:** the tag's words, `kandalf attach PLN-30` to copy, and **Stop**.
  Options from an earlier draft stay listed, dimmed, until the new ones land.
- **Ready:** the rows. **Use** puts `<letter>. <title>. <summary>` in the Answer box,
  replacing what is there (after asking, if the box has text the person typed).
  **Draft again** opens the note field and starts a new draft.
- **Needs you / lost / empty:** why, the attach command, and **Draft again**.
- The research link opens the doc in the page's plan-doc view.

The draft's state reaches the page in `/api/pickup`'s reply as `drafts: [{key, state, why,
id, view}]`, with `view` (label and the actions open) from the server as story jobs carry
theirs; `view_cases.json` gains the draft cases so the page's fallback agrees.

## The prompt

The session is given: the tile's markdown; its master's tile if it has one; the titles of
the tiles it blocks; the note and "the last draft is at `<doc>`: revise it" on Draft again;
and these rules, in the house style of pickup's prompt:

- This is research for a decision a person will make. Don't decide it.
- Read the code and docs you need. You may run read-only commands and tests to check a claim.
- Change no code. This worktree is thrown away when the draft ends; nothing in it is kept.
- Write exactly one file, `<doc path>`, in the format shown (the fixed opening above). Two to
  four options that really differ, each with a one-paragraph summary a person can choose
  from, a rough cost, and what it rules out. Recommend one and say why. Say what you could
  not find out.
- Don't edit the boards. Don't commit. Stop when the file is written.

## Where the code goes

- **`kandalf/drafts.py`** (new): the draft's record and states, `read_doc`, the options block
  it writes, the prompt, start / look / end, and `view`. It uses pickup's worktree and launch
  helpers and `agent`; no module but the three agent modules runs a CLI.
- **`kandalf/jobs.py`**: the ledger's state holds `drafts` (a list, read as empty when a
  ledger has none) beside `jobs`. Story jobs and `busy()` are untouched; a draft's states
  (queued, working, ready, empty, needs you, lost, ending) and its `view` are `drafts.py`'s
  own, and `view_cases.json` gains a `drafts` list beside `views`.
- **`kandalf/tiles.py`** and **`board.html`**: the options block's reader (twin), and the
  edit that writes or replaces it.
- **`kandalf/serve.py`**: `/api/draft` (start, again, stop), drafts in the watch (the busy
  rule and the research docs' mtimes) and in `/api/pickup`'s reply; ending a draft from
  Decide, Archive and Clear.
- **`kandalf/cli.py`**: `attach` finds a draft's session.

## Approaches considered

1. **A read-only (`plan` mode) session whose last message the server transcribes.** The
   strongest guarantee, but the options would be scraped from chat text that differs across
   the three agents, and the agent could not run anything to check a claim.
2. **A throwaway worktree, and the session writes the research doc** (chosen). Reuses
   pickup's worktree, session and attach code, behaves the same on every agent, and the
   output is a file with a fixed opening. Read-only means edits are discarded.
3. **A session in the main checkout, told not to edit.** Rejected: write access in the
   checkout people work in is the wrong risk for research.

Earlier in the conversation: one shot with Draft again and attach was chosen over a
conversation on the board (much more to build, and awkward for long arguments) and over a
draft with no redo.

## Constraints

- Standard library only; the page stays one file; twin rules in both readers with shared
  cases; only the agent modules run an agent.
- A store whose `permission` is `plan` cannot draft (the session can't write the doc, on any
  of the three agents): the button says so. In `manual` the session stops at its first write
  prompt and the draft shows as needing you.
- README: a **Draft options** part in the backlog section, the Options line in the
  tile-attribute table, `permission` notes. CHANGELOG lines. The filing rules say a decision
  can be drafted and that an Options block is the server's to write.

## Left to the builder

- Names inside `drafts.py`, and how the ledger's draft record is laid out beyond `key`,
  `state`, `id`, `session`, `repos`, `doc`, `note`, `why` and the times.
- The exact wording of the prompt beyond the rules above.
- Where the tag and the rows sit in each theme, and their icons.
- Whether ending a draft from Decide shares code with pickup's tidy.
