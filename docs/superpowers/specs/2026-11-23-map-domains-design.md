# Filing stories into domains from the map: design

## Why

The map groups stories by domain, and a store grows a "No domain" region full of stories
nobody filed (most of them done and archived). Filing them today means opening each tile and
typing a `Domain:` line. This lets you make a domain on the map and put stories in it by
dragging: several at once, from any board.

It is the first thing the map writes. Until now it only read.

## Goals

- **+ New domain** on the map's bar asks for a name and adds an empty region.
- Stories are selected with Shift-click, or by Shift-dragging a box round them.
- Dragging a selected story carries the selection. Dropping it on a region files every
  selected story in that region's domain; dropping on "No domain" takes the domain off.
- One drop is one server call and one commit, whichever of plans, backlog and the archive the
  stories are on, and whichever board is open.
- The last drop can be undone from the map's bar.

## Not in this version

- Changing or adding a story's parent (`Part of`) from the map.
- Renaming or deleting a domain, or a domain picker on a tile's card.
- Saving an empty domain anywhere but the browser.
- Taking the last domain off from the map: once every story has a domain the "No domain"
  region is not drawn, so a domain is taken off by editing the tile.

## Anti-goals

- A drop that changes `Part of`, or anything else on a tile but its `Domain:` line.
- A drop that silently breaks up a family.

## Rules

- **A domain is the tile's own `Domain: name` line.** Filing writes, replaces or removes that
  line and nothing else.
- **Families stay whole.** A story dropped on a region moves its master: the server files the
  top master of each dropped story, once. Sub-stories take their master's domain as they do
  today. A sub-story that has a `Domain:` line of its own keeps it.
- **Archived stories are filed like any other**, in `archive/archived.md`. The line goes
  before the tile's `Archived <date>.` line.
- **Stories in flight can be filed.** The line means nothing to a session.
- **A domain that comes from the title** ("Admin: analytics") has no line. Dropping such a
  story on another domain writes a line, which wins. Dropping it on "No domain" changes
  nothing, and the page says so.
- **A name** is what `Domain:` already reads: it starts with a letter or digit, is at most 39
  characters, and has no backtick or line break; it may not end in a full stop. A name that
  matches a domain already on the map, ignoring case, is that domain as it is already spelt.
- **An empty domain lives in the browser** (with the map's arrangement, per store, kept across
  reloads) until a story is in it; Reset layout clears it. The files know a domain only through the tiles that carry it.

## The server

`POST /api/domain?file=<the open board>` with `{set: {KEY: name | null}}`, and for Undo
`expect: {KEY: name | null}` as well.

- For each key: its tile's top master (itself when it has none) is the tile filed. Keys that
  share a master count once. A key no tile has is skipped.
- Each file that changes (plans.md, backlog.md, the archive log) is written once, atomically,
  and the lot is one commit: `board: 6 stories → Billing`, `board: 6 stories → no domain`,
  or `board: 6 stories re-filed` when the targets differ.
- Reply: the open board's fresh text and mtime with the tile index and links, as other board
  replies carry them, plus `filed: {KEY: {"from": name | null, "to": name | null}}` for the
  tiles whose line changed (their own line before and after) and `unchanged: [KEY]` for the
  ones it left as they were.
- Undo is the same call with each filed key's `from`, and `expect` holding each filed key's
  `to`: what its tile's own `Domain:` line should say now. Before anything is written, each
  expected key is taken to its top master as a `set` key is, and that tile's own line is
  compared with its expected name, ignoring case (`null` matches no line). If any differs, or an
  expected key has no tile, nothing is written or committed and the reply is 409
  `{error: "changed since", changed: [KEY]}`, the masters that differ in the body's order. An
  `expect` that isn't an object is ignored; a drop sends none.
- A bad name is 400 with a sentence saying why. A stale board is not checked: the server
  edits the files as they are on disk. The page refuses to drop while its board has unsaved
  edits ("Save the board first").

The edit is `tiles.with_domain(store, text, key, name)`: replace the tile's first `Domain:`
line, or add one as the last line of its description (before the first bullet, session,
Status, Ready, Options, Decision or Archived line), or remove it for `None`.

## The page

- **Selecting.** Shift-click toggles a story. Shift-drag from empty space draws a box; the
  stories inside join the selection. Esc, or a click on empty space, clears it. Changing the
  filter or the domain scope clears it. A selected story has a ring.
- **Dragging.** A drag that starts on a selected story (no Ctrl/⌘) carries every selected
  story. The region under the pointer is highlighted, pending empty regions included. Released
  anywhere else, the stories go back and nothing is written. Plain drag elsewhere still pans;
  Ctrl/⌘-drag still arranges.
- **Dropping.** The page posts the selection, adopts the reply, forgets the remembered
  positions of the stories it moved (so they take their place in the new region), clears the
  selection and says what happened: `6 stories → Billing`, or with families
  `PLN-70 and its 4 sub-stories → Billing`, or `2 stories → no domain; PLN-12 stays in Admin
  by its title`.
- **Undo.** An **Undo** button appears in the map's bar after a drop (its title says what it
  undoes) and stays until the next drop that files something, or a reload. Like Clear's Undo,
  it is refused when things have changed since: when a filed story's domain is no longer the
  one the drop wrote (edited on its card, by an agent or in an editor), whichever board it is
  on. The page refuses at once what it already knows of (an unsaved edit included); otherwise
  the server checks the files at the moment Undo is pressed (`expect`) and refuses with a 409.
  Either way Undo says `Undo is off: PLN-80 has been changed since.`, writes nothing and goes,
  and after a 409 the page re-reads the open board unless it has unsaved edits.
- **+ New domain.** Asks for the name in the page's own dialog, refuses a bad one with the
  reason, and adds an empty region near the middle of the view. Its region is dashed like the
  others and says "empty". It is kept in the browser with the arrangement across reloads
  until a story is in it (Reset layout clears it), and becomes an ordinary region once a
  story carries its name.

## Approaches considered

1. **The page edits the tiles and saves the board.** Rejected: the page can save only the open
   board, and the map shows stories from three files.
2. **One server call that files stories by key** (chosen): one edit rule, one commit, works
   from any board, and undo is the same call.

## Left to the builder

- The look of the selection ring, the box and the highlighted region, in both theme families.
- Where the new empty region is placed, and how pending domains are kept with the arrangement.
- The helper names on the page.
