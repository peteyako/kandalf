#!/usr/bin/env node
// test_page.js: the page's own reading of master tiles, sub-stories and mentions, run on
// board.html's real functions (lifted out by name, as check.js does).
//
//   node --test test_page.js

const fs = require("fs");
const path = require("path");
const test = require("node:test");
const assert = require("node:assert/strict");

const html = fs.readFileSync(path.join(__dirname, "kandalf", "board.html"), "utf8");
const start = html.indexOf("function parse(");
const end = html.indexOf("\n// A plan doc (anything", start);
assert.ok(start > 0 && end > start, "board.html's functions not found");
// The globals the lifted code reads; open() loads a board the way the page's adopt() does.
const page = new Function(
  `let nextId = 1, doc = null, file = null;
   const ARCHIVE_LOG = "archive/archived.md", BACKLOG = "backlog.md";
   const localStorage = null;  // the page's remembered settings: none here
   ${html.slice(start, end)}
   return { inline, keyLinks, tileFacts, tileIndex, kidsOf, relatedTo, whereLabel, columnLayout, foldedTree, cardKey, sessionLine, storyParts, parse, modelOf, withModel, reposOf, withRepos, statusLine, readyLine, handoffLine, statusParts, statusToast, noteParts, pickupHintShows, readsAsBoard, wakeButton, sessionState, wantsReply, replyHTML, isPickupColumn, isDoneCol, columnKind, kindTaken, doubledKinds,
            setRepos: list => { STORE_REPOS = list; },
            setPickupColumn: name => { STORE_PICKUP_COLUMN = name; },
            section: name => doc.sections.find(s => s.name === name),
            open(name, text, tiles) { file = name; doc = parse(text); serverTiles = tiles || {}; } };`
)();
page.setRepos([{ name: "myapp", main: true }, { name: "api", main: false }, { name: "web", main: false }]);

const PLANS = `# Plans

## To do

- [ ] PLN-70 **Dark mode.** The master story.

## In flight

- [ ] PLN-75 **Theme auth.**
  Part of PLN-70. Runs after PLN-41. Moves with PLN-74.

## Done

- [x] PLN-74 **Colour \`tokens\`:** paste one line.
  part of PLN-70 and never part of PLN-99.
- [x] PLN-100 **Late one.** Part of PLN-70.
`;
// What the server knows: the other boards' tiles, and a stale word on this board's.
const SERVER = {
  "PLN-81": { title: "Theme analytics", file: "backlog.md", column: "Feature", done: false, parent: "PLN-70", mentions: ["PLN-70"] },
  "PLN-41": { title: "Login reopen", file: "archive/archived.md", column: "Archived", done: true, parent: null, mentions: [] },
  "PLN-60": { title: "Needs the embed", file: "backlog.md", column: "Change", done: false, parent: null, mentions: ["PLN-75"] },
  "PLN-12": { title: "Deleted here since the last save", file: "plans.md", column: "To do", done: false, parent: "PLN-70", mentions: [] },
};

test("a key in a tile's text becomes a link to that tile", () => {
  assert.equal(page.keyLinks("after PLN-41, then"), 'after <a class="keyref" data-act="jump" data-key="PLN-41">PLN-41</a>, then');
  assert.match(page.inline("**Moves with PLN-75**: both"), /<strong>Moves with <a class="keyref"[^>]*data-key="PLN-75">PLN-75<\/a><\/strong>/);
});

test("a key inside a link, code or an attribute is left alone", () => {
  for (const md of ["[the PLN-41 plan](active/x.md)", "`PLN-41`", "[plan](../PLN-41/notes.txt)", "[PLN-41](https://example.com/PLN-41)"])
    assert.doesNotMatch(page.inline(md), /keyref/, md);
  assert.doesNotMatch(page.inline("XPLN-41 and PLN-41x"), /keyref/);
});

test("a tile's master is the first Part of, in any case, and never itself", () => {
  assert.deepEqual(page.tileFacts("PLN-74", "PLN-74 **A.**\n  part of PLN-70 and never part of PLN-99."), { parent: "PLN-70", mentions: ["PLN-70", "PLN-99"] });
  assert.deepEqual(page.tileFacts("PLN-82", "PLN-82 **B.** Part of PLN-82. Not part of the plan."), { parent: null, mentions: [] });
});

test("the index is this board as it reads now, plus the server's word on the other boards", () => {
  page.open("plans.md", PLANS, SERVER);
  const index = page.tileIndex();
  assert.deepEqual([...index.keys()].sort(), ["PLN-100", "PLN-41", "PLN-60", "PLN-70", "PLN-74", "PLN-75", "PLN-81"]);
  assert.deepEqual(index.get("PLN-74"), { title: "Colour tokens", file: "plans.md", column: "Done", done: true, parent: "PLN-70", mentions: ["PLN-70", "PLN-99"] });
  assert.equal(index.get("PLN-75").done, false);
  assert.equal(index.get("PLN-81").file, "backlog.md");
});

test("a master's sub-stories come from every board, in key order", () => {
  page.open("plans.md", PLANS, SERVER);
  const kids = page.kidsOf("PLN-70", page.tileIndex());
  assert.deepEqual(kids.map(k => [k.key, k.done, page.whereLabel(k)]),
    [["PLN-74", true, "Done"], ["PLN-75", false, "In flight"], ["PLN-81", false, "Backlog"], ["PLN-100", true, "Done"]]);
  assert.deepEqual(page.kidsOf("PLN-75", page.tileIndex()), []);
  assert.equal(page.whereLabel(SERVER["PLN-41"]), "Archived");
});

test("every tile in the archive is done", () => {
  page.open("archive/archived.md", "# Archived plans\n\n## Archived\n\n- [ ] PLN-41 **Old.** Part of PLN-70.\n", {});
  assert.equal(page.tileIndex().get("PLN-41").done, true);
});

test("related is every mention, either way, but the master, sub-stories and linked tiles", () => {
  page.open("plans.md", PLANS, SERVER);
  const index = page.tileIndex();
  const text = "PLN-75 **Theme auth.**\n  Part of PLN-70. Runs after PLN-41. Moves with PLN-74.";
  assert.deepEqual(page.relatedTo("PLN-75", text, index, ["PLN-74"]), ["PLN-41", "PLN-60"]);
  assert.deepEqual(page.relatedTo("PLN-70", "PLN-70 **Dark mode.** The master story.", index, []), []);
  assert.deepEqual(page.relatedTo("PLN-41", "PLN-41 **Login reopen.**", index, []), ["PLN-75"]);
});

// One column holding a master, two of its sub-stories (one a master itself), a sub-story of
// a master in another column, one of a master on another board, and one naming no tile.
const FOLDS = `# Plans

## To do

- [ ] PLN-01 **Master.**
- [ ] PLN-02 **Kid.** Part of PLN-01.
- [ ] PLN-03 **Plain.**
- [ ] PLN-04 **Kid and master.** Part of PLN-01.
- [ ] PLN-05 **Grandkid.** Part of PLN-04.
- [ ] PLN-06 **Of the one in flight.** Part of PLN-10.
- [ ] PLN-07 **Of the backlog one.** Part of PLN-81.
- [ ] PLN-08 **Of the one in flight too.** Part of PLN-10.
- [ ] PLN-09 **Of nothing.** Part of PLN-99.

## In flight

- [ ] PLN-10 **Other master.**
- [ ] PLN-11 **Moved off alone.** Part of PLN-01.
`;
const shape = items => items.map(i => i.ghost ? [i.ghost, ...i.cards.map(page.cardKey)] : page.cardKey(i.card));

test("a column shows a master with its sub-stories folded in, and the rest under a ghost of their master", () => {
  page.open("plans.md", FOLDS, SERVER);
  const todo = page.section("To do"), index = page.tileIndex();
  assert.deepEqual(shape(page.columnLayout(todo.cards, index)),
    ["PLN-01", "PLN-03", ["PLN-10", "PLN-06", "PLN-08"], ["PLN-81", "PLN-07"], "PLN-09"]);
  assert.deepEqual(shape(page.columnLayout(page.section("In flight").cards, index)), ["PLN-10", ["PLN-01", "PLN-11"]]);
});

test("a sub-story whose master is filtered out of the column shows under its ghost", () => {
  page.open("plans.md", FOLDS, SERVER);
  const todo = page.section("To do");
  const shown = todo.cards.filter(c => /Kid|Grandkid/.test(c.text));
  assert.deepEqual(shape(page.columnLayout(shown, page.tileIndex())), [["PLN-01", "PLN-02", "PLN-04"]]);
});

test("the tiles folded into a master are its sub-stories in the same column, and theirs", () => {
  page.open("plans.md", FOLDS, SERVER);
  const todo = page.section("To do"), index = page.tileIndex(), card = k => todo.cards.find(c => page.cardKey(c) === k);
  assert.deepEqual(page.foldedTree(card("PLN-01"), todo.cards, index).map(page.cardKey), ["PLN-02", "PLN-04", "PLN-05"]);
  assert.deepEqual(page.foldedTree(card("PLN-04"), todo.cards, index).map(page.cardKey), ["PLN-05"]);
  assert.deepEqual(page.foldedTree(card("PLN-03"), todo.cards, index), []);
});

test("two tiles that name each other as master: only one is the other's sub-story", () => {
  page.open("plans.md", "## A\n\n- [ ] PLN-01 **One.** Part of PLN-02.\n- [ ] PLN-02 **Two.** Part of PLN-01.\n- [ ] PLN-03 **Three.** Part of PLN-01.\n", {});
  const index = page.tileIndex();
  assert.deepEqual(["PLN-01", "PLN-02", "PLN-03"].map(k => index.get(k).parent), [null, "PLN-01", "PLN-01"]);
  assert.deepEqual(shape(page.columnLayout(page.section("A").cards, index)), ["PLN-01"]);
});

test("the page still reads the multi-repo session line as a session", () => {
  const line = "  Claude session `ab12cd34`: `claude attach ab12cd34`. Branch `pln-07-small-thing`, worktrees api `api/.worktrees/pln-07`, web `web/.worktrees/pln-07`. Model `opus`. Started 2026-11-18.";
  assert.deepEqual(page.sessionLine(line), { id: "ab12cd34", rest: "Branch `pln-07-small-thing`, worktrees api `api/.worktrees/pln-07`, web `web/.worktrees/pln-07`. Model `opus`. Started 2026-11-18." });
});

test("a tile's Claude session line is read apart from its description", () => {
  const line = "  Claude session `53b9a9c3`: `claude attach 53b9a9c3`. Branch `pln-77-dark-mode`, worktree `myapp-pln-77` (cut from dev at `ebf12a01b`). Started 2026-11-18.";
  assert.deepEqual(page.sessionLine(line), { id: "53b9a9c3", rest: "Branch `pln-77-dark-mode`, worktree `myapp-pln-77` (cut from dev at `ebf12a01b`). Started 2026-11-18." });
  assert.deepEqual(page.sessionLine("  Claude session `ab12cd34`: `claude attach ab12cd34`."), { id: "ab12cd34", rest: "" });
  assert.equal(page.sessionLine("  The Claude session `ab12cd34` went well."), null);
  assert.equal(page.sessionLine("  - [ ] Claude session `ab12cd34`: `claude attach ab12cd34`."), null);
});

test("a tile's Copilot session line is read as a session too", () => {
  assert.deepEqual(page.sessionLine("  Copilot session `ab12cd34`: `kandalf attach ab12cd34`. Started 2026-11-19."), { id: "ab12cd34", rest: "Started 2026-11-19." });
});

test("a story's description leaves the session line out, and the story says which session it has", () => {
  const card = page.parse("## A\n\n- [ ] PLN-01 **One.**\n  What it is.\n  Claude session `ab12cd34`: `claude attach ab12cd34`. Branch `pln-01-one`.\n  - [ ] **Step.**\n").sections[0].cards[0];
  const parts = page.storyParts(card);
  assert.deepEqual(parts.desc, ["What it is."]);
  assert.deepEqual(parts.session, { id: "ab12cd34", rest: "Branch `pln-01-one`." });
  assert.equal(parts.subs.length, 1);
});

test("a tile's own model is its Model line, and the dropdown writes or removes that line", () => {
  const md = "PLN-01 **One.**\n  What it is: the data model: users.\n  Claude session `ab12cd34`: `claude attach ab12cd34`.\n  - [ ] **Step.**\n    - a detail";
  assert.equal(page.modelOf(md), "");
  const set = page.withModel(md, "sonnet");
  assert.equal(set, "PLN-01 **One.**\n  What it is: the data model: users.\n  Claude session `ab12cd34`: `claude attach ab12cd34`.\n  Model: sonnet\n  - [ ] **Step.**\n    - a detail");
  assert.equal(page.modelOf(set), "sonnet");
  assert.equal(page.withModel(set, "haiku"), set.replace("Model: sonnet", "Model: haiku"));  // changed in place
  assert.equal(page.withModel(set, ""), md);  // back to the store's default
  assert.equal(page.withModel("PLN-02 **Two.**", "opus"), "PLN-02 **Two.**\n  Model: opus");
  assert.equal(page.modelOf("PLN-03 **Three.**\n  model: `claude-haiku-5-5`."), "claude-haiku-5-5");
});

test("reposOf reads the line in order and ignores the first line", () => {
  assert.deepEqual(page.reposOf("PLN-01 **One.**\n  What it is.\n  - [ ] **Step.**"), []);
  assert.deepEqual(page.reposOf("PLN-01 **One.**\n  Repos: web, `API`, Web."), ["web", "API"]);
  assert.deepEqual(page.reposOf("PLN-01 **One.**\n  repo: api web"), ["api", "web"]);
  assert.deepEqual(page.reposOf("Repos: api, web"), []);  // the first line is the tile, not a body line
});

test("withRepos writes, replaces in place and removes the line", () => {
  const md = "PLN-01 **One.**\n  What it is.\n  - [ ] **Step.**";
  const set = page.withRepos(md, ["web", "api"]);
  assert.equal(set, "PLN-01 **One.**\n  What it is.\n  Repos: api, web\n  - [ ] **Step.**");  // store order
  assert.equal(page.withRepos(set, ["myapp", "web"]), set.replace("api, web", "myapp, web"));  // in place
  assert.equal(page.withRepos(set, []), md);
  assert.equal(page.withRepos(set, ["myapp"]), md);  // just the main repo is the default
  assert.equal(page.withRepos(set, ["MyApp"]), md);
  assert.equal(page.withRepos(md, ["myapp"]), md);
  const hand = "PLN-01 **One.**\n  Repos: `web`, api.";
  assert.equal(page.withRepos(hand, ["api", "WEB"]), hand);  // same set: a hand-written line stays as it is
});

test("withRepos keeps a hand-chosen first repo first", () => {
  const md = "PLN-01 **One.**\n  Repos: api, myapp";
  assert.equal(page.withRepos(md, ["myapp", "api", "web"]), "PLN-01 **One.**\n  Repos: api, myapp, web");
});

test("withRepos keeps a name the store doesn't know", () => {
  const md = "PLN-01 **One.**\n  Repos: api, legacy";
  assert.equal(page.withRepos(md, ["api", "web"]), "PLN-01 **One.**\n  Repos: api, web, legacy");
  assert.equal(page.withRepos(md, []), "PLN-01 **One.**\n  Repos: legacy");
  assert.equal(page.withRepos(md, ["api"]), md);
});

test("withRepos puts a new line before the first sub-task, as withModel does", () => {
  const md = "PLN-01 **One.**\n  Model: sonnet\n  - [ ] **Step.**";
  assert.equal(page.withRepos(md, ["api"]), "PLN-01 **One.**\n  Model: sonnet\n  Repos: api\n  - [ ] **Step.**");
  assert.equal(page.withRepos("PLN-02 **Two.**", ["api"]), "PLN-02 **Two.**\n  Repos: api");
});

test("a story's description leaves its Model line out", () => {
  const card = page.parse("## A\n\n- [ ] PLN-01 **One.**\n  What it is.\n  Model: sonnet\n  - [ ] **Step.**\n").sections[0].cards[0];
  assert.deepEqual(page.storyParts(card).desc, ["What it is."]);
  assert.equal(page.storyParts(card).model, "sonnet");
});

test("a tile's status line is read apart from its description", () => {
  assert.deepEqual(page.statusLine("  **Status 2026-11-18 14:58:** session `ab12cd34` at work. Branch 3 commits ahead of dev."),
    { at: "2026-11-18 14:58", text: "session `ab12cd34` at work. Branch 3 commits ahead of dev." });
  assert.equal(page.statusLine("  Status of the migration: done."), null);
  const card = page.parse("## A\n\n- [ ] PLN-01 **One.**\n  What it is.\n  - [ ] **Step.**\n  **Status 2026-11-18 14:58:** session `ab12cd34` idle.\n").sections[0].cards[0];
  const parts = page.storyParts(card);
  assert.deepEqual(parts.desc, ["What it is."]);
  assert.deepEqual(parts.status, { at: "2026-11-18 14:58", text: "session `ab12cd34` idle." });
});

test("a tile that says it is ready for review is read as ready, with what it says", () => {
  assert.equal(page.readyLine("  **Ready for review:** all gates green (unit, integration, e2e); 4 commits on `pln-77-x`."), "all gates green (unit, integration, e2e); 4 commits on `pln-77-x`.");
  assert.equal(page.readyLine("  Ready for review."), "");
  assert.equal(page.readyLine("  ready for review: tests pass"), "tests pass");
  assert.equal(page.readyLine("  Not ready for review yet."), null);
  assert.equal(page.readyLine("  - [ ] **Ready for review** check"), null);
  const card = page.parse("## A\n\n- [ ] PLN-01 **One.**\n  What it is.\n  - [x] **Step.**\n  **Ready for review:** gates green.\n").sections[0].cards[0];
  assert.deepEqual(page.storyParts(card).desc, ["What it is."]);
  assert.equal(page.storyParts(card).ready, "gates green.");
  assert.equal(page.storyParts(page.parse("## A\n\n- [ ] PLN-02 **Two.**\n  Words.\n").sections[0].cards[0]).ready, null);
});

test("a tile's Handoff line is what its session last said about the work", () => {
  assert.equal(page.handoffLine("  Handoff: all four sub-tasks are committed (`b65bb8445`); nothing uncommitted."), "all four sub-tasks are committed (`b65bb8445`); nothing uncommitted.");
  assert.equal(page.handoffLine("  **Handoff:** running the e2e tier."), "running the e2e tier.");
  assert.equal(page.handoffLine("  Status: all five sub-tasks committed."), "all five sub-tasks committed.");
  assert.equal(page.handoffLine("  **Status 2026-11-18 14:58:** session at work."), null);
  assert.equal(page.handoffLine("  The handoff: went fine."), null);
  assert.equal(page.handoffLine("    - Handoff: nested under a sub-task"), null);
});

test("a status line reads in a toast a fact a line, with the last steps on its screen", () => {
  const line = 'Status 2026-11-19 12:12: session `cae302dd` at work. Branch has no commits yet. 2 files not committed. 1 of 7 sub-tasks ticked in 25 min; at that pace about 2 h to go (ETA 2026-11-19 14:43). On its screen (rough): "… mkdir -p out ● Running command: ls ● Running command: mvn test ● Running command: make".';
  assert.equal(page.statusToast(line), [
    "Session: cae302dd at work", "Branch: has no commits yet", "Files: 2 files not committed",
    "1 of 7 sub-tasks ticked in 25 min; at that pace about 2 h to go (ETA 2026-11-19 14:43)",
    "On its screen:", "● Running command: ls", "● Running command: mvn test", "● Running command: make"].join("\n"));
});

test("the status line is cut into its facts for the status tile", () => {
  const line = 'session `e347739a` at work. Branch 3 commits ahead of dev; last commit 7 min ago: "fix(stripe): Premium follows the status. active keeps it (PLN-78)". 1 file not committed. 2 of 3 sub-tasks ticked in 4 min; at that pace about 2 min to go (ETA 2026-11-18 18:28). On its screen (rough): "… running task lint. Then tests".';
  assert.deepEqual(page.statusParts(line), [
    { label: "Session", text: "`e347739a` at work" },
    { label: "Branch", text: '3 commits ahead of dev; last commit 7 min ago: "fix(stripe): Premium follows the status. active keeps it (PLN-78)"' },
    { label: "Files", text: "1 file not committed" },
    { label: "Progress", text: "2 of 3 sub-tasks ticked in 4 min; at that pace about 2 min to go (ETA 2026-11-18 18:28)" },
    { label: "Screen", text: "… running task lint. Then tests" },
  ]);
  assert.deepEqual(page.statusParts("session `ab12cd34` gone. Its worktree isn't there. No ETA: when it started isn't known.").map(p => p.label), ["Session", "Branch", "Progress"]);
  assert.deepEqual(page.statusParts("session `ab12cd34` idle. Branch has no commits yet. Nothing uncommitted. All 3 sub-tasks ticked, but not done yet: its tests and review are still to finish (no Ready for review line).").map(p => p.text),
    ["`ab12cd34` idle", "has no commits yet", "Nothing uncommitted", "All 3 sub-tasks ticked, but not done yet: its tests and review are still to finish (no Ready for review line)"]);
  assert.deepEqual(page.statusParts("something else entirely."), [{ label: "", text: "something else entirely." }]);
});
test("the session's note is cut at Now and Next", () => {
  assert.deepEqual(page.noteParts("two committed (`14e51c2ae`). Now: the tier rule. Next: lint and every tier. PLN-48 is apart."), [
    { label: "Done", text: "two committed (`14e51c2ae`)." }, { label: "Now", text: "the tier rule." }, { label: "Next", text: "lint and every tier. PLN-48 is apart." }]);
  assert.deepEqual(page.noteParts("running the e2e tier."), [{ label: "", text: "running the e2e tier." }]);
});

test("the In flight column says why pickup is off, on the plans board only, until that sentence is hidden", () => {
  const why = 'Pickup is off for this store: set "pickup": true in its board.json to turn it on.';
  assert.equal(page.pickupHintShows("plans.md", "In flight", why, ""), true);
  assert.equal(page.pickupHintShows("plans.md", "In progress", why, null), true);
  assert.equal(page.pickupHintShows("plans.md", "To do", why, ""), false);
  assert.equal(page.pickupHintShows("backlog.md", "In flight", why, ""), false);
  assert.equal(page.pickupHintShows("plans.md", "In flight", null, ""), false);  // pickup is on
  assert.equal(page.pickupHintShows("plans.md", "In flight", why, why), false);  // hidden with its ×
  assert.equal(page.pickupHintShows("plans.md", "In flight", "Pickup is off: `claude` isn't on your PATH.", why), true);  // a new reason shows again
});

test("the store's pickup column is the only one picked up, by name in any case; unset, any In flight column is", () => {
  for (const name of ["In flight", "in-flight", "Inflight", "In progress", "In Progrees", "In-Progress", "Progress", "Doing",
    "WIP", "Working", "Underway", "Under way", "Ongoing", "Active", "🚀 In progress"]) assert.equal(page.isPickupColumn(name), true, name);
  for (const name of ["To do", "Done", "Bugs", "Backlog", "Not doing", "Inactive", "Ready for review"]) assert.equal(page.isPickupColumn(name), false, name);
  assert.equal(page.isPickupColumn("Building"), false);
  page.setPickupColumn("Building");
  try {
    assert.equal(page.isPickupColumn(" building "), true);
    assert.equal(page.isPickupColumn("In flight"), false);
    assert.equal(page.pickupHintShows("plans.md", "BUILDING", "Pickup is off.", ""), true);
    assert.equal(page.pickupHintShows("plans.md", "In flight", "Pickup is off.", ""), false);
  } finally { page.setPickupColumn(""); }
});

// The theme picker, lifted out of the page's head script, with a stand-in localStorage.
function themes(stored = {}) {
  const from = html.indexOf("const BOARD_THEMES"), to = html.indexOf("let themeNow", from);
  assert.ok(from > 0 && to > from, "board.html's theme script not found");
  const localStorage = { getItem: k => (k in stored ? stored[k] : null) };
  return new Function("localStorage", `${html.slice(from, to)} return { BOARD_THEMES, storedTheme, storedAccent };`)(localStorage);
}

test("every theme in the menu has its style block, and offers valid accents with its default first", () => {
  const { BOARD_THEMES } = themes();
  assert.equal(Object.keys(BOARD_THEMES)[0], "light");  // the default: Clean and light
  for (const [id, t] of Object.entries(BOARD_THEMES)) {
    const block = html.match(new RegExp(`:root\\[data-theme="${id}"\\] \\{([^}]*)\\}`));
    assert.ok(block, `no style block for ${id}`);
    // applyTheme leaves the first accent to the style block, so the two must agree
    assert.equal(block[1].match(/--accent:\s*(#[0-9a-f]{6})/)?.[1], t.accents[0][0], `${id}'s default accent`);
    for (const [c, name] of t.accents) assert.match(c, /^#[0-9a-f]{6}$/, `${id}: ${name}`);
  }
});

test("a stored theme or accent the page no longer offers falls back to Clean and light, or to that theme's own accent", () => {
  assert.equal(themes().storedTheme(), "light");
  assert.equal(themes({ "board.theme": "realm" }).storedTheme(), "realm");
  assert.equal(themes({ "board.theme": "classic" }).storedTheme(), "classic");  // picked, it stays
  assert.equal(themes({ "board.theme": "tidy" }).storedTheme(), "light");  // a theme that's gone
  assert.equal(themes({ "board.theme": "constructor" }).storedTheme(), "light");  // not a key of the object's prototype
  assert.equal(themes({ "board.accent.sticker": "#3dd6c4" }).storedAccent("sticker"), "#3dd6c4");
  assert.equal(themes({ "board.accent.sticker": "#123456" }).storedAccent("sticker"), "#ff5a8a");
  assert.equal(themes().storedAccent("light"), "#2f4bd8");
});

test("a Done column is any named like one, never one that says not", () => {
  for (const name of ["Done", "done ✓", "Shipped", "Complete", "Completed", "Finished", "Closed", "Merged", "Released", "Deployed", "Delivered", "Resolved"])
    assert.equal(page.isDoneCol(name), true, name);
  for (const name of ["Not done", "To do", "Doing", "Undone", "Shipping", "Bugs", "In flight"]) assert.equal(page.isDoneCol(name), false, name);
});

test("a board has one In flight column and one Done column at most, under whatever names", () => {
  assert.equal(page.columnKind("In progress"), "In flight");
  assert.equal(page.columnKind("Shipped"), "Done");
  assert.equal(page.columnKind("To do"), "");
  const sections = [{ name: "To do" }, { name: "In flight" }, { name: "Done" }];
  assert.equal(page.kindTaken(sections, "WIP", sections[0]), sections[1]);  // renaming To do to WIP: In flight has it
  assert.equal(page.kindTaken(sections, "WIP", sections[1]), null);          // renaming In flight itself is fine
  assert.equal(page.kindTaken(sections, "Shipped", sections[0]), sections[2]);
  assert.equal(page.kindTaken(sections, "Later", sections[0]), null);
  assert.deepEqual(page.doubledKinds(sections), []);
  assert.deepEqual(page.doubledKinds([...sections, { name: "Doing" }, { name: "Shipped" }, { name: "Closed" }]),
    [["In flight", ["In flight", "Doing"]], ["Done", ["Done", "Shipped", "Closed"]]]);
});

test("kandalf check fails a board with two In flight or two Done columns", () => {
  const { spawnSync } = require("child_process"), os = require("os");
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "kandalf-check-"));
  const run = text => {
    const f = path.join(dir, "plans.md");
    fs.writeFileSync(f, text);
    return spawnSync("node", [path.join(__dirname, "kandalf", "check.js"), f], { encoding: "utf8" });
  };
  try {
    assert.equal(run("## To do\n\n## In flight\n\n## Done\n").status, 0);
    const twice = run("## In flight\n\n## In progress\n\n## Done\n\n## Shipped\n");
    assert.equal(twice.status, 1);
    assert.match(twice.stderr, /2 In flight columns: In flight, In progress/);
    assert.match(twice.stderr, /2 Done columns: Done, Shipped/);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test("a session at work gets Pause; a stopped one, idle, paused or gone, Continue; one that never started Retry", () => {
  const act = (state, more = {}) => (page.wakeButton({ key: "PLN-01", state, ...more }).match(/data-act="(\w+)"[^>]*>([^<]*)</) || []).slice(1);
  assert.deepEqual(act("started"), ["pause", "⏸ Pause"]);
  assert.deepEqual(act("started", { pausing: true }), ["pause", "⏸ Pause now"]);  // writing its handoff: stop it there if it hangs
  assert.deepEqual(act("waiting"), ["pickup", "▶ Start now"]);  // a paused story queued again
  assert.deepEqual(act("idle"), ["continue", "▶ Continue"]);
  assert.deepEqual(act("paused"), ["continue", "▶ Continue"]);
  assert.deepEqual(act("lost"), ["continue", "▶ Continue"]);
  assert.deepEqual(act("failed"), ["restart", "↻ Retry"]);
  assert.deepEqual(act("ended"), []);
});

test("a session's state reads pausing while it writes its handoff, then paused", () => {
  assert.equal(page.sessionState({ state: "started", pausing: true }), "pausing");
  assert.equal(page.sessionState({ state: "paused" }), "paused");
  assert.equal(page.sessionState({ state: "started" }), "working");
  assert.equal(page.sessionState({ state: "waiting" }), "queued");
});

test("a session waiting for input, or a story ready for review, gets a box to reply in", () => {
  assert.equal(page.wantsReply({ state: "idle", waiting: "question" }), true);
  assert.equal(page.wantsReply({ state: "idle", ready: true }), true);
  assert.equal(page.wantsReply({ state: "lost", ready: true }), true);
  assert.equal(page.wantsReply({ state: "started", ready: true }), false);  // still at work: nothing to answer yet
  assert.equal(page.wantsReply({ state: "idle" }), false);
  assert.equal(page.wantsReply({ state: "paused", ready: true }), false);
  assert.equal(page.wantsReply(undefined), false);
  const box = page.replyHTML({ key: "PLN-01", state: "idle", waiting: "question" });
  assert.match(box, /<textarea[^>]*data-reply="PLN-01"/);
  assert.match(box, /data-act="reply" data-key="PLN-01"[^>]*>Send</);
  assert.doesNotMatch(box, /type="checkbox"/);  // a question: words only
  const review = page.replyHTML({ key: "PLN-01", state: "idle", ready: true });
  assert.deepEqual([...review.matchAll(/type="checkbox" data-step="(\w+)"/g)].map(m => m[1]), ["squash", "merge", "push"]);
  assert.match(page.replyHTML({ key: "PLN-01", state: "idle", ready: true, repos: [{ name: "app", trunk: "develop" }] }), /Merge into develop/);
  assert.match(page.replyHTML({ key: "PLN-01", state: "idle", ready: true, repos: [{ trunk: "dev" }, { trunk: "main" }] }), /Merge into each trunk/);
});

test("a board file that differs from the board's own writing only in blank lines still opens as a board", () => {
  const reads = text => page.readsAsBoard(text, page.parse(text));
  const tidy = "## In flight\n\n- [ ] PLN-01 **One.**\n\n## Done\n\n- [x] PLN-02 **Two.**\n";
  assert.equal(reads(tidy), true);
  assert.equal(reads(tidy.replace("## Done\n\n", "## Done\n")), true);  // a tile straight under its heading
  assert.equal(reads(tidy.replace("**One.**\n", "**One.**\n\n- [ ] PLN-03 **Three.**\n")), true);  // a blank line between two tiles
  assert.equal(reads(tidy.replace("## Done\n\n", "## Done\n\nSome prose.\n\n- [x] PLN-09 **Nine.**\nMore prose after a tile.\n")), false);  // prose would move: view-only
});

test("kandalf check passes a board that differs only in blank lines, and still fails one that would lose its shape", () => {
  const { spawnSync } = require("child_process"), os = require("os");
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "kandalf-check-"));
  const run = text => {
    const f = path.join(dir, "plans.md");
    fs.writeFileSync(f, text);
    return spawnSync("node", [path.join(__dirname, "kandalf", "check.js"), f], { encoding: "utf8" });
  };
  try {
    assert.equal(run("## In flight\n\n## Done\n- [x] PLN-02 **Two.**\n").status, 0);
    assert.equal(run("## In flight\n\n- [ ] PLN-01 **One.**\nProse after a tile.\n").status, 1);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});
