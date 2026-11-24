# Filing stories into domains from the map: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On the map, make a domain, select several stories and drag them into a region; one server call writes each story's `Domain:` line across plans, backlog and the archive, and the last drop can be undone.

**Architecture:** A Python edit, `tiles.with_domain`, writes one tile's `Domain:` line. A server function, `serve.set_domains`, resolves each dropped key to its top master through a `Snapshot`, edits the files that hold them and makes one commit; `POST /api/domain` wraps it. The page adds a selection, a re-file drag and pending empty regions to the map, built on pure functions the page tests cover.

**Tech Stack:** Python 3.10+ standard library, one self-contained `kandalf/board.html`, `unittest` and `node --test`.

**Spec:** `docs/superpowers/specs/2026-11-23-map-domains-design.md`. Read it before any task.

## Global Constraints

- Standard library only. Never edit `kandalf/marked.min.js`. The page stays one file.
- A drop changes a tile's `Domain:` line and nothing else. `Part of` is never written.
- The `Domain:` reader is an existing twin (`tiles.domain` / the page's `domainOf`, `DOMAIN_RE` on both sides): do not change what it reads. A new rule about what a name may be is written on both sides with shared cases.
- A page function a test needs sits between `function parse(` and `// A plan doc (anything` in `board.html` and is in the object returned at the top of `test_page.js`.
- Tests build throwaway stores in temp dirs; `gitlog.record` is mocked where a test would commit, as `test_decide.py` does.
- No existing test's expectation changes.
- Docstrings, comments and test names are plain sentences. User-visible changes get a line under `## Unreleased` in `CHANGELOG.md`.
- Commit by path, subject `feat(scope): …`, ending with the `Co-Authored-By` trailer. Work only in `.worktrees/map-domains` on `feat/map-domains`. No merge, no push.
- Before every test run: `sh ~/.claude/bin/memwait.sh`; one thing at a time; output through `tail`.

## Review Focus

1. A selection holding a master and some of its own sub-stories, or two sub-stories of one master: expected one write on the master, counted once in the message. Tests in Tasks 2 and 3.
2. A name that differs only in case from a domain already on the map (`billing` vs `Billing`): expected the existing spelling is written. A name with a backtick, a line break, a trailing full stop, a leading space or 40 characters: refused with the reason, nothing written. Tests in Tasks 1 and 3.
3. An archived tile: the line goes before its `Archived <date>.` line, and that line stays the tile's last. A tile with an options block, sub-tasks or a session line: the line goes before them. Tests in Task 1.
4. A story in a domain only by its title, dropped on "No domain": nothing written, reported as unchanged, and the page says why. Tests in Tasks 2 and 3.
5. A drop while the open board has unsaved edits: refused on the page with "Save the board first", nothing posted. Checked in the browser in Task 4.

---

### Task 1: The edit that writes a tile's domain

**Files:**
- Modify: `kandalf/tiles.py`, `kandalf/board.html`, `tile_cases.json`, `test_tiles.py`, `test_page.js`

**Interfaces:**
- Produces (Python): `tiles.domain_name(raw: str) -> str`: the name as it will be written (trimmed), or `ValueError` with one of the pinned sentences. `tiles.with_domain(store, text: str, key: str, name: str | None) -> str`: the board with that tile's first `Domain:` line replaced by `  Domain: <name>`, or one added as the last line of its description, or removed for `None`; the board unchanged when no tile has the key or nothing would change.
- Produces (page): `domainName(raw) -> {name} | {error}` with the same rule and sentences.

Pinned values:
- A name: after trimming, it matches `^[A-Za-z0-9][^`\n]{0,38}$` and does not end in `.`.
- Sentences: empty: `A domain needs a name.`; a bad first character: `A domain's name starts with a letter or a digit.`; too long: `A domain's name is at most 39 characters.`; a backtick or a line break: `A domain's name can't hold a backtick or a line break.`; a trailing full stop: `A domain's name can't end in a full stop.`
- "The last line of its description": before the tile's first line that is a bullet (`^  [-*] `), the session line, or a line starting (after its indent) `**Status`, `**Ready for review`, `Ready for review`, `**Options`, `**Decision` or `Archived `; at the tile's end when it has none.

- [ ] **Step 1: Shared cases.** Add a list `domainNames` to `tile_cases.json`, each `{"raw", "name"}` or `{"raw", "error"}`: `"Billing"`, `"  Admin tools  "` (trimmed), `"2FA"`, a 39-character name, and one for each of the five sentences (a 40-character name, `""`, `"   "`, `"-x"`, ``"a`b"``, `"a\nb"`, `"Ops."`). Run it in both suites.
- [ ] **Step 2: Python tests** in `test_tiles.py` `Edits`:

```python
def test_a_domain_is_added_as_the_last_line_of_the_description(self):       # before sub-tasks; reads back through tiles.domain
def test_a_tile_s_domain_line_is_replaced_where_it_is(self):
def test_no_domain_takes_the_line_off(self):
def test_a_tile_with_no_domain_line_is_left_alone_when_none_is_asked_for(self):   # text identical
def test_the_same_domain_again_changes_nothing(self):
def test_the_line_goes_before_an_archived_tile_s_archived_line(self):       # "  Archived 2026-11-23." stays last
def test_the_line_goes_before_a_session_line_an_options_block_and_a_decision(self):
def test_a_tile_of_one_line_gets_the_line_under_it(self):
def test_only_the_first_of_two_domain_lines_is_replaced(self):
def test_a_board_with_no_such_tile_comes_back_as_it_was(self):
def test_a_bad_name_is_refused_before_anything_is_written(self):            # with_domain raises ValueError
```

- [ ] **Step 3: Run both, see them fail; implement** `domain_name` and `with_domain` in `tiles.py` (beside `with_options`; the module docstring's list) **and `domainName` in `board.html`** beside `domainOf`, each naming its twin; **run, see them pass** (`python3 -m unittest test_tiles`, `node --test test_page.js`).
- [ ] **Step 4: Commit:** `feat(tiles): a tile's domain line can be written`

---

### Task 2: The server files stories by key

**Files:**
- Modify: `kandalf/serve.py`, `CHANGELOG.md`
- Create: `test_domains.py`

**Interfaces:**
- Consumes: `tiles.with_domain`, `tiles.domain_name`, `tiles.domain`, `tiles.Snapshot`.
- Produces: `serve.set_domains(store, changes: dict) -> tuple[int, dict]`: `(200, {"filed": {KEY: {"from", "to"}}, "unchanged": [KEY], "skipped": [KEY]})` or `(400, {"error": sentence})`. `POST /api/domain?file=<board>`: the body is `{"set": {KEY: name | null}}`; a 200 reply is the board reply the page already takes from other routes for that file (`mtime`, `text`, `linked`, `tiles`, `maxKey`) plus `filed`, `unchanged` and `skipped`; 404 `{"error": "no such board"}` as other routes; 400 as `set_domains` gives it, and 400 `{"error": "nothing to file"}` for an empty or missing `set`.

Pinned behaviour:
- Every name is checked with `domain_name` first; one bad name refuses the whole call and writes nothing.
- A name that matches, ignoring case, a domain some tile's own line already carries is written in that spelling (the first found over plans, backlog, archive).
- Each key is resolved to its top master with a `Snapshot` over `tiles.FAMILY` (`lineage(key)[-1]` when it has masters, else itself). Keys that resolve to one tile count once; when their targets differ, the first in the body's order wins. A key with no tile goes in `skipped`.
- `filed[KEY]` is keyed by the tile actually written (the master), with its own line's value before (`None` when it had none) and after. A tile whose line would not change goes in `unchanged`.
- Each file is read once, edited for all its tiles, written with `write_atomic` only when its text changed.
- One commit when anything was filed, through `gitlog.record(store, [paths], subject)`: all targets the same name: `board: <n> stories → <name>` (`1 story`); all `None`: `board: <n> stories → no domain`; mixed: `board: <n> stories re-filed`. `n` is the number filed. Nothing filed: no commit.

- [ ] **Step 1: Write `test_domains.py`** (a temp store with plans.md, backlog.md and `archive/archived.md`; `gitlog.record` mocked):

```python
def test_stories_on_three_files_are_filed_in_one_commit(self):          # subject "board: 3 stories → Billing"; each file reads back
def test_a_sub_story_files_its_master_and_the_family_counts_once(self):  # set PLN-71 and PLN-72 (kids of PLN-70): filed == {"PLN-70": …}
def test_a_master_and_its_own_sub_story_together_are_one_write(self):
def test_a_sub_story_with_its_own_domain_line_keeps_it(self):
def test_no_domain_takes_the_line_off_and_says_so_in_the_commit(self):  # "board: 2 stories → no domain"
def test_a_story_with_no_line_dropped_on_no_domain_is_unchanged(self):  # title-prefix case: unchanged, no commit
def test_the_same_domain_again_is_unchanged_and_commits_nothing(self):
def test_a_name_in_another_case_is_written_as_the_domain_is_already_spelt(self):
def test_one_bad_name_refuses_the_whole_call_and_writes_nothing(self):  # 400, files identical, no commit
def test_a_key_no_tile_has_is_skipped(self):
def test_mixed_targets_are_one_commit_named_re_filed(self):
def test_undo_is_the_same_call_with_what_each_was_before(self):         # apply filed[k]["from"]; files identical to the start
def test_an_archived_tile_keeps_its_archived_line_last(self):
```

- [ ] **Step 2: Run** `python3 -m unittest test_domains` **, see them fail; implement** `set_domains` and the route (beside `/api/archive`, building the board reply the way that route's neighbours do); **run** `python3 -m unittest test_domains test_decide test_clear` **, see them pass.**
- [ ] **Step 3: Commit:** `feat(board): the server files stories into a domain by key`

---

### Task 3: The map's selection and what a drop means, as pure functions

**Files:**
- Modify: `kandalf/board.html`, `test_page.js`

**Interfaces (all inside the lifted span, exported to the tests):**
- `mapToggle(sel: Set, key) -> Set`: a new set with the key added or taken out.
- `mapBoxed(box: {x0, y0, x1, y1}, at: Map key -> {x, y}, shown: Set of keys) -> [keys]`: the shown stories whose centre is inside the box (either corner order), in key order.
- `mapRegionAt(point: {x, y}, regions: [{domain, x, y, R}]) -> domain | undefined`: the domain of the smallest region whose circle holds the point (`""` is "No domain"); `undefined` when none does.
- `mapFiling(keys: [keys], nodes: Map) -> [{key, kids}]`: the top master of each key, each once, in key order, with how many stories are under it.
- `mapFiledText(reply: {filed, unchanged}, target: name | null, nodes: Map, domains: Map key -> domain) -> string`: what the page says after a drop.
- `mapPending(saved: object) -> [names]` and `mapWithPending(saved, names) -> object`: the empty domains kept with the arrangement under the key `"~domains"`, sorted, each once ignoring case.
- `mapSpelt(name, known: [names]) -> name`: the known domain that matches ignoring case, else the name.

Pinned copy for `mapFiledText` (`<D>` is the target, `no domain` for `null`):
- one loose story: `PLN-12 → <D>`; several loose: `6 stories → <D>`;
- exactly one family: `PLN-70 and its 4 sub-stories → <D>` (`its sub-story` for one);
- several tiles with at least one family: `3 stories and their sub-stories → <D>`;
- nothing filed, one unchanged that sits in a domain by its title: `PLN-12 stays in Admin by its title`; nothing filed otherwise: `Nothing to move: they are already there`;
- some filed and some unchanged by title: the filed sentence, then `; PLN-12 stays in Admin by its title` (for several: `; 2 stories stay where their titles put them`).

- [ ] **Step 1: Write the page tests**, one per function, covering: toggling in and out; a box drawn right-to-left; a story on the box's edge counts as inside; hidden stories are never boxed; nested regions give the smallest; a point in no region; two sub-stories of one master and the master itself give one entry with the right count; a loose story has `kids: 0`; each pinned sentence; pending names de-duplicated ignoring case and round-tripped through `mapWithPending` without touching the saved positions; `mapSpelt("billing", ["Billing"])`.
- [ ] **Step 2: Run** `node --test test_page.js` **, see them fail; implement; see them pass.**
- [ ] **Step 3: Commit:** `feat(board): the map's selection and what a drop says, as tested functions`

---

### Task 4: The map selects, drags, drops and undoes

**Files:**
- Modify: `kandalf/board.html` (the map's pointer handlers `mapGrab`/`mapDrop` ~3450-3490, `drawMap` ~3280, `placeMap` ~3356, `#mapBar` ~1222, the map's styles ~1065-1110 for both theme families), `CHANGELOG.md`

**Interfaces:**
- Consumes: Task 3's functions, `domainName`, `POST /api/domain`, the page's `ask` dialog (it takes `input`), `adopt`, `flash`, `mapSaved`/`mapSavedKey`.

Pinned behaviour and copy:
- State on `sm`: `sel` (a Set), `refile` (the drag in progress), `lastFiling` (what Undo sends back and its title).
- **Shift-click** on a story toggles it and does not focus it. **Shift-drag** from anywhere that is not a story draws a box (`.m-box`); on release the boxed stories join the selection. **Esc** clears the selection before it does anything else it does on the map; a plain click on empty space clears it; a change of filter text or of scope clears it. Selected stories carry the class `sel`.
- **Re-file drag:** a pointer that goes down on a selected story with no Ctrl/⌘ and moves more than 4px carries every selected story (their dots follow the pointer; the camera does not pan). The region under the pointer gets the class `drop`. On release over a region, the drop happens; anywhere else, or on `pointercancel`, the stories go back. Ctrl/⌘-drag (arranging) and plain drag elsewhere (panning) behave exactly as before, selected stories included when Ctrl/⌘ is held.
- **The drop:** refused with the flash `Save the board first.` when the board has unsaved edits or is saving. Otherwise it posts `{set: {key: name | null}}` for the selected keys to `/api/domain?file=<file>`, adopts the reply as a board reply, removes the moved masters' and their families' saved positions, clears the selection, redraws, flashes `mapFiledText(…)`, and sets `lastFiling`. An error reply is flashed and changes nothing.
- **Undo:** a button `#mapUndo` in `#mapBar`, hidden until a drop files something. Label `Undo`, title `Undo: <the drop's sentence>`. It posts each filed key's `from`, flashes `Undone: <the drop's sentence>`, and hides. A new drop replaces it. It is forgotten on reload.
- **+ New domain:** a button `#mapNewDomain` in `#mapBar`, label `+ New domain`. It asks `Name the new domain` with an input; `domainName` refuses a bad name in the dialog with its sentence; `mapSpelt` against the domains on the map and the pending ones decides the spelling; a name already on the map just scopes the map to it. Otherwise the name joins the pending domains (saved with the arrangement) and the map redraws.
- **Pending regions** are drawn like regions with the class `pending`, the label `<name>` and a second line `empty`, at a free spot near the centre of the current view, each with a radius of 90. A pending name that a story now carries stops being pending (dropped from the saved list on the next draw). With no domains at all on the map, the "No domain" region is drawn as soon as there is one pending region, so there is somewhere to drag from and to.
- Archived stories are selectable and draggable like the others.
- Styles for `.sel`, `.m-box`, `.m-region.drop`, `.m-region.pending` in both theme families, from existing tokens.

- [ ] **Step 1: Implement** the state, handlers, drawing, the two buttons and the styles as pinned, following the map's existing patterns (`svgEl`, the `drag` object, `placeMap`).
- [ ] **Step 2: Run** `node --test test_page.js` (nothing that passed may fail), and check the page's script parses (extract the inline scripts and `node --check`, as earlier tasks on this repo did).
- [ ] **Step 3: Commit:** `feat(board): stories are filed into domains by dragging them on the map`

The controller checks this task in a real browser afterwards: a throwaway store with loose, master, sub-story and archived stories; box-select, Shift-click, a drop on an existing region, on a new one and on "No domain", Undo, a refused drop with unsaved edits, panning and Ctrl-arranging unchanged, both theme families.

---

### Task 5: The words, and the full gate

**Files:**
- Modify: `README.md` (**The map** section, ~line 239-250: a **Filing** bullet after **Arranging**, and the sentence that says the map only reads, if there is one), `CHANGELOG.md` (the Unreleased lines of Tasks 2 and 4 read as one set), `kandalf/templates/plans.md` and `kandalf/init.py` only if they say how a domain is set (check; the rule that a domain is a tile's `Domain:` line is unchanged).

- [ ] **Step 1: Write the docs.** Say only what was built: + New domain, Shift-click and Shift-drag to select, drag onto a region, families move whole, archived stories too, "No domain" takes the line off, Undo in the bar until the next drop or a reload, an empty domain lives in the browser until a story is in it, and that parents can't be changed from the map.
- [ ] **Step 2: Run both full suites, one at a time** (`python3 -m unittest discover -p "test_*.py"`, then `node --test test_page.js`), memwait before each. Expected: `OK` and `fail 0`. A timeout is reported as a timeout.
- [ ] **Step 3: Commit:** `docs: filing stories into domains from the map`
