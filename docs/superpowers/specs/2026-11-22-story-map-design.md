# Story map: design

## Why

The kanban board shows where each story is, one column at a time. It does not show how stories
relate: which belong to one master, which mention each other, which part of the product they
are in. With a hundred stories that is hard to hold in your head. The map is a second view of
the same boards that shows the relationships, where the work is, and what to pull next.

## Goals

- A **Map** view beside the kanban view: a toggle in the header switches between them. It is
  the same store and the same tiles; nothing is stored twice.
- Every keyed story on plans, backlog and the archive is a node. A master is a hub with its
  sub-stories on a ring round it. Mentions and linked tiles are curves between nodes.
- Stories are grouped into **domains**, each a labelled region. Domain chips narrow the map to one.
- Colour says where a story is: backlog, planned, in flight, done. An archived story counts as
  done and shows green, like one in a Done column. With pickup on, the live
  session states of stories in flight (at work, needs you, ready for review) show as they do on the strip.
- A master's ring shows how many of its sub-stories are done. Stories to pull next pulse: open
  sub-stories of a master that is already moving, and anything that needs you or is ready.
- Click a story: it glides to the centre, everything it is not joined to fades, and a panel
  shows its title, state, master, sub-stories and mentions, each a click to hop to. Esc steps
  back. "Open tile" in the panel opens the story's card on its kanban board.
- Drag pans, the wheel zooms. Ctrl-drag (or ⌘-drag) moves a cluster or a whole domain; the
  arrangement is remembered.
- It follows the board's theme and dark mode, and respects reduced motion.

## Not in this version

- Editing from the map (moving a story between columns, changing its master). The map reads.
- A "blocked by" relation and dependency arrows. kandalf has no such attribute yet.
- Saving the arrangement in the store. It is kept in the browser (localStorage, per store).

## Where a story's domain comes from

1. Its own `Domain: name` line (a new tile attribute, read like `Tests:`).
2. Else its master's domain.
3. Else the first word before the colon in its title ("Admin: analytics" is Admin), when at least
   two masters or three stories share that first word. A prefix only one story has is not a domain.
4. Else none: it sits in a "No domain" region.

`Domain:` is a twin rule: `tiles.py` and `board.html` both read it, with cases in
`tile_cases.json`. The server's tile index (`Snapshot.index`) carries each tile's own `domain`
so the page knows it for tiles on the other boards. The card dialog does not get a domain
picker in this version; the line is written by hand or by an agent.

## Approaches considered

- **Lanes** (columns as bands, lines across): position shows state, but it is the kanban with
  lines on it, and related stories end up far apart. Rejected.
- **Explorer only** (one story in the middle, its neighbours round it): simple, but no overview.
  Rejected as the whole answer; its hopping is the map's click behaviour.
- **Cluster map** (chosen): position shows relationship, colour shows state. Tried as a
  prototype on the flycogo store (104 stories, 13 masters) and it read well.
- A plain force-directed graph was ruled out early: research on network layouts finds it hides
  hierarchy and tangles as it grows.

## How it is built

- **All in `board.html`**, drawn as SVG, no library. No server change except `domain` in the
  tile index.
- **Data**: the page's `tileIndex()` (every keyed tile, its master and mentions), `linkEdges()`
  (linked tiles) and `pickups` (live session states). The map is built from those, so it is
  current with the open board and refreshes on the same polls.
- **Pure functions, tested in `test_page.js`** (inside the lifted span):
  - `domainOf` / `tileDomains(index)`: the rules above.
  - `mapGraph(index, links, jobs)`: nodes (key, title, where, state, domain, master, sub-stories,
    next-up) and edges (sub-story, mention, linked), with a master and its own sub-story never
    doubled as a mention.
  - `mapLayout(graph)`: positions. Each master's sub-stories go on a ring, turned toward what
    they mention. Hubs and loose stories are then spread by a fixed number of steps of a small
    simulation: same-domain hubs draw together, hubs that mention each other draw together,
    nothing overlaps, different domains keep a gap. It is deterministic (no randomness), so the
    map looks the same on every load.
- **The view** (outside the span): drawing, pan and zoom, focus and the panel, domain chips,
  dragging, and saved positions (`localStorage`, keyed by the store's name; a story with no
  saved place gets its laid-out one). A "Reset layout" button forgets them.
- **Switching views**: a Board / Map toggle in the header, remembered per browser. In the map
  view the columns, the column sort and the stats strip are hidden; the filter box fades the
  stories that don't match. The map is offered on plans and backlog (they share one map), not
  on plan docs or the archive list.
- **Docs**: README (a "Map" section, `Domain:` in the tile attributes), GLOSSARY (Domain, Map),
  CHANGELOG.

## Checks

- `tile_cases.json` cases for `Domain:`; Python and page both run them.
- `test_page.js`: domains (own line, inherited, title prefix, the lone-prefix case), the graph
  (edges, next-up, states), the layout (deterministic, no two hubs overlap, a domain's hubs are
  nearer each other than to another domain's).
- In a browser, against a throwaway store: switch views, focus and hop, scope by domain, drag
  and reload, light, dark and classic themes.

## Left to the builder

Exact sizes, colours within the theme's tokens, animation timings, and the panel's wording.
