# kandalf

A local markdown kanban board that hands stories to Claude Code. `README.md` is the user
guide and lists the module layout under **Development**; read it before changing behaviour.

This file is for any coding agent working on kandalf; `CLAUDE.md` imports it for Claude Code.

## Ground rules

- **Python 3.10+, standard library only.** No dependencies in `pyproject.toml`, no pip installs
  in the code path. The page is one self-contained file, `kandalf/board.html`; the only bundled
  library is `kandalf/marked.min.js` (never edit it).
- **Two readers of one format.** The server (Python) and the page (`board.html`) both parse board
  markdown, and several rules are written twice: `pickup.IN_FLIGHT_RE` / `READY_RE` / `REPOS_RE`
  / `MODEL_RE` and their twins in `board.html`, `pickup.is_pickup_column` and `isPickupColumn`.
  Change one and change the other in the same commit; the comments name the twin ("same in
  board.html"). Cover both sides: `test_pickup.py` and friends for Python, `test_page.js` for the
  page.
- **`claude_code.py` is the only module that runs `claude`, `copilot_cli.py` the only one that runs `copilot`.** Keep every CLI call behind them; `agent.py` picks one by board.json's `agent`.
- **Store vs app.** A store (`<repo>-plans`) is its own git repo holding `plans.md`,
  `backlog.md`, plan docs and `board.json`; the app is the code repo it links into at
  `docs/plans`. `store.py` defines both. Tests build throwaway stores in temp dirs; never point
  them at a real one.

## Tests

```bash
python3 -m unittest discover -p "test_*.py"   # one file: python3 -m unittest test_pickup
node --test test_page.js
```

Some Python tests commit, so git needs a user name and email. `test_page.js` lifts functions out
of `board.html` by name, between `function parse(` and `// A plan doc (anything`: a new page
function the tests need goes inside that span and into the returned object at the top of
`test_page.js`. `kandalf check FILE` checks that a board file round-trips through the page's
parser.

CI (`.github/workflows/ci.yml`) runs both suites on Ubuntu and macOS, Python 3.10, 3.12 and 3.14,
then installs the package and runs `kandalf --help`. New package files go in
`[tool.setuptools.package-data]`.

## Writing style

- Docstrings and comments are plain sentences about what a thing is or does, e.g. "The keyed
  tiles in the plans board's In flight column". Test names read the same way
  (`test_a_tile_that_lands_in_flight_is_started_at_once`).
- Each user-visible change gets a line under `## Unreleased` at the top of `CHANGELOG.md` (start one above the latest release if it isn't there). Update the README
  when you change a command, a `board.json` key (the config table) or what pickup does.
- Commit subjects: `feat(board): …`, `fix(pickup): …`, `docs: …`, lower case after the colon.
