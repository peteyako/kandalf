# Board grammar and job ledger: three deepenings

2026-11-22. Status: approved (the user: "all good. just run").

From the architecture review (`2026-11-22-architecture-findings.md`, candidates 1, 2 and 3): the Python
side parses board markdown in five places whose rules already differ, about fifteen rules are written in
both Python and `board.html` with only comments keeping them in step, and a pickup job is an untyped dict
changed in place by a dozen functions across three modules and two processes.

## Goals

1. **One tile reader** (`kandalf/tiles.py`): the only Python module that knows board grammar. It parses
   a board into tiles with their facts, reads and writes a tile's attribute lines and session/status lines
   so a file still round-trips, and gives a snapshot across plans, backlog and the archive log that answers
   the tile index, the texts, the In flight tiles, a tile's lineage and "the nearest line of a kind up the
   family". serve, pickup, gitlog, pln and store use it; their own walkers and regexes go.
2. **One case file** (`tile_cases.json` at the repo root) that `test_tiles.py` and `test_page.js` both run
   through their own reader: a line, a tile or a small board, with the facts it must yield.
3. **One job ledger** (`kandalf/jobs.py`): owns `board-pickup.json` and a job's lifecycle: one locked
   transaction (also across processes), a few queries, the transitions as named operations, and a view of
   each job for the page (its label and the actions open to it). The effects pickup needs (start, resume,
   switch model, move repos, stop, list sessions, why idle, full id, announce on the tile) are one sessions
   object: the real one wraps agent.py and serve's tile write-back; test_pickup's `Fake` is the second.

## Behaviour decisions (where the readers disagree today; the user approved these)

- A `* [ ] PLN-07` tile has a key in Python too, as on the page, so pickup sees it.
- A session line counts only as its own indented line (anchored, as the page's `SESSION_RE`); a
  description quoting "Claude session \`abc\`" no longer counts for adopt or tidy.
- Every tile in a Done column counts as done for the red unmerged outline, checkbox or not, as on the page.
- A ring of masters is broken the page's way: the first of them stands as the master.
- The page's inherited `Tests:` line sees masters on other boards: the server's tile index carries each
  tile's own `Tests:` value, and `inheritedTests` walks it.
- The ledger's lock is a `fcntl.flock` on a lock file beside the state file, plus the in-process lock;
  where `fcntl` is missing (Windows) only the in-process lock applies.
- Old `board-pickup.json` files load unchanged (closed-early "ended", jobs with no model, a bare worktree).

## Non-goals

- No change to what a session is told (prompts stay byte for byte), to the HTTP routes, or to board.json.
- No change to the page's look. `board.html` changes only where it reads the tile index (inherited Tests)
  and where it decides a job's label and actions (from the ledger's view).
- Candidates 4-6 of the review (a story's cuts, board.json's schema, the page's line editors).

## Constraints

- Standard library only. The refactor gates on every test tier: the full Python suite and the page suite
  after every task, both green, plus `kandalf check` on a sample board at the end.
- Tests that assert today's behaviour move to the new interfaces; a test is deleted only when its case
  moves into `tile_cases.json` or into a test of the new module.
