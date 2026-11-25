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
   const ARCHIVE_LOG = "archive/archived.md";
   let MODELS = ["opus", "sonnet"];
   const localStorage = null;  // the page's remembered settings: none here
   ${html.slice(start, end)}
   return { cardMarkdown, movesWith, blockedBy, decisionLine, decisionOf, isDecisionCol, splitCard, sessionsOf, readyLine, inline, keyLinks, tileFacts, tileIndex, kidsOf, blocking, decisionsFor, blocksOf, inRing, optionsOf, draftOf, draftTag, optionRows, useText, faceBody, onlyOptionsDiffer, sectionIdMap, subTaskLines, pollsFor, blockerLines, blockerRowsHTML, answerKey, relatedTo, whereLabel, columnLayout, foldedTree, cardKey, sessionLine, storyParts, parse, modelOf, scrollPlaces, buildState, buildStats, withModel, baseOf, withBase, testsOf, withTests, inheritedTests, versionLabel, reposOf, withRepos, statusLine, readyLine, handoffLine, statusParts, statusToast, noteParts, pickupHintShows, readsAsBoard, pickupTag, wakeButton, queueButton, sessionState, wantsReply, canMessage, jobView, draftView, cardLook, replyHTML, isPickupColumn, isDoneCol, settingsHTML, settingsChanges, domainOf, domainName, tileDomains, mapGraph, mapLayout, mapStats, mapHideDone, mapToggle, mapShiftClick, combineShown, combineBody, combineDeleteNote, combineTitleHTML, suggestionFor, enterCommits, switchFills, mapKeepShown, mapBoxed, mapRegionAt, mapFiling, mapFiledText, mapPending, mapWithPending, mapSpelt, mapUndoable, organizeReviewHTML, organizeSend, organizeChangedText, organizeFiledText, splitLabel, splitReviewHTML, splitSend, splitCreatedText, splitOffered, agentMark, columnKind, kindTaken, doubledKinds, boardNames, boardLabel, ownName, pickable, boardOrder, renamingNotice, renamingHTML, namesMoved,
            setNames: n => applyNames(n),
            setRepos: list => { STORE_REPOS = list; },
            setTrunk: name => { STORE_TRUNK = name; },
            setFullSuite: on => { STORE_FULL_SUITE = on; },
            setPickupColumn: name => { STORE_PICKUP_COLUMN = name; },
            setUnmerged: map => { unmerged = map; },
            setPickups: (jobs, blocked, enabled = true, drafts, draftsOff = null) => { pickups = { enabled, why: null, jobs, ...(blocked ? { blocked } : {}), ...(drafts ? { drafts, draftsOff } : {}) }; },
            cards: () => doc.sections.flatMap(s => s.cards.map(c => ({ column: s.name, card: c }))),
            section: name => doc.sections.find(s => s.name === name),
            open(name, text, tiles) { file = name; doc = parse(text); serverTiles = tiles || {}; } };`
)();
const NEW_NAMES = { build: "build.md", plan: "plan.md" }, OLD_NAMES = { build: "plans.md", plan: "backlog.md" };
page.setNames(NEW_NAMES);  // the harness reads boards under the new names; OldNames below sets the old ones
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
  "PLN-81": { title: "Theme analytics", file: "plan.md", column: "Feature", done: false, parent: "PLN-70", mentions: ["PLN-70"] },
  "PLN-41": { title: "Login reopen", file: "archive/archived.md", column: "Archived", done: true, parent: null, mentions: [] },
  "PLN-60": { title: "Needs the embed", file: "plan.md", column: "Change", done: false, parent: null, mentions: ["PLN-75"] },
  "PLN-12": { title: "Deleted here since the last save", file: "build.md", column: "To do", done: false, parent: "PLN-70", mentions: [] },
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

test("the index is this board as it reads now, plus the server's word on the other boards", () => {
  page.open("build.md", PLANS, SERVER);
  const index = page.tileIndex();
  assert.deepEqual([...index.keys()].sort(), ["PLN-100", "PLN-41", "PLN-60", "PLN-70", "PLN-74", "PLN-75", "PLN-81"]);
  assert.deepEqual(index.get("PLN-74"), { title: "Colour tokens", file: "build.md", column: "Done", done: true, parent: "PLN-70", mentions: ["PLN-70", "PLN-99"], blockers: [], decision: null, domain: null });
  assert.equal(index.get("PLN-75").done, false);
  assert.equal(index.get("PLN-81").file, "plan.md");
});

test("a master's sub-stories come from every board, in key order", () => {
  page.open("build.md", PLANS, SERVER);
  const kids = page.kidsOf("PLN-70", page.tileIndex());
  assert.deepEqual(kids.map(k => [k.key, k.done, page.whereLabel(k)]),
    [["PLN-74", true, "Done"], ["PLN-75", false, "In flight"], ["PLN-81", false, "Plan"], ["PLN-100", true, "Done"]]);
  assert.deepEqual(page.kidsOf("PLN-75", page.tileIndex()), []);
  assert.equal(page.whereLabel(SERVER["PLN-41"]), "Archived");
});

test("every tile in the archive is done", () => {
  page.open("archive/archived.md", "# Archived plans\n\n## Archived\n\n- [ ] PLN-41 **Old.** Part of PLN-70.\n", {});
  assert.equal(page.tileIndex().get("PLN-41").done, true);
});

test("related is every mention, either way, but the master, sub-stories and linked tiles", () => {
  page.open("build.md", PLANS, SERVER);
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
  page.open("build.md", FOLDS, SERVER);
  const todo = page.section("To do"), index = page.tileIndex();
  assert.deepEqual(shape(page.columnLayout(todo.cards, index)),
    ["PLN-01", "PLN-03", ["PLN-10", "PLN-06", "PLN-08"], ["PLN-81", "PLN-07"], "PLN-09"]);
  assert.deepEqual(shape(page.columnLayout(page.section("In flight").cards, index)), ["PLN-10", ["PLN-01", "PLN-11"]]);
});

test("a sub-story whose master is filtered out of the column shows under its ghost", () => {
  page.open("build.md", FOLDS, SERVER);
  const todo = page.section("To do");
  const shown = todo.cards.filter(c => /Kid|Grandkid/.test(c.text));
  assert.deepEqual(shape(page.columnLayout(shown, page.tileIndex())), [["PLN-01", "PLN-02", "PLN-04"]]);
});

test("the tiles folded into a master are its sub-stories in the same column, and theirs", () => {
  page.open("build.md", FOLDS, SERVER);
  const todo = page.section("To do"), index = page.tileIndex(), card = k => todo.cards.find(c => page.cardKey(c) === k);
  assert.deepEqual(page.foldedTree(card("PLN-01"), todo.cards, index).map(page.cardKey), ["PLN-02", "PLN-04", "PLN-05"]);
  assert.deepEqual(page.foldedTree(card("PLN-04"), todo.cards, index).map(page.cardKey), ["PLN-05"]);
  assert.deepEqual(page.foldedTree(card("PLN-03"), todo.cards, index), []);
});

test("two tiles that name each other as master: only one is the other's sub-story", () => {
  page.open("build.md", "## A\n\n- [ ] PLN-01 **One.** Part of PLN-02.\n- [ ] PLN-02 **Two.** Part of PLN-01.\n- [ ] PLN-03 **Three.** Part of PLN-01.\n", {});
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

test("a tile's Copilot or Antigravity session line is read as a session too", () => {
  assert.deepEqual(page.sessionLine("  Copilot session `ab12cd34`: `kandalf attach ab12cd34`. Started 2026-11-19."), { id: "ab12cd34", rest: "Started 2026-11-19." });
  assert.deepEqual(page.sessionLine("  Antigravity session `ab12cd34`: `kandalf attach ab12cd34`. Started 2026-11-22."), { id: "ab12cd34", rest: "Started 2026-11-22." });
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
  assert.equal(page.pickupHintShows("build.md", "In flight", why, ""), true);
  assert.equal(page.pickupHintShows("build.md", "In progress", why, null), true);
  assert.equal(page.pickupHintShows("build.md", "To do", why, ""), false);
  assert.equal(page.pickupHintShows("plan.md", "In flight", why, ""), false);
  assert.equal(page.pickupHintShows("build.md", "In flight", null, ""), false);  // pickup is on
  assert.equal(page.pickupHintShows("build.md", "In flight", why, why), false);  // hidden with its ×
  assert.equal(page.pickupHintShows("build.md", "In flight", "Pickup is off: `claude` isn't on your PATH.", why), true);  // a new reason shows again
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
    assert.equal(page.pickupHintShows("build.md", "BUILDING", "Pickup is off.", ""), true);
    assert.equal(page.pickupHintShows("build.md", "In flight", "Pickup is off.", ""), false);
  } finally { page.setPickupColumn(""); }
});

// The theme picker, lifted out of the page's head script, with a stand-in localStorage.
function themes(stored = {}, osDark = false) {
  const from = html.indexOf("const BOARD_THEMES"), to = html.indexOf("let themeNow", from);
  assert.ok(from > 0 && to > from, "board.html's theme script not found");
  const localStorage = { getItem: k => (k in stored ? stored[k] : null) };
  const matchMedia = () => ({ matches: osDark, addEventListener() {} });
  return new Function("localStorage", "matchMedia", `${html.slice(from, to)} return { BOARD_THEMES, storedTheme, storedAccent, storedMode, isDark };`)(localStorage, matchMedia);
}

test("the colour mode is System unless the browser keeps Light or Dark, and System follows the OS", () => {
  assert.equal(themes().storedMode(), "system");
  assert.equal(themes({ "board.mode": "dark" }).storedMode(), "dark");
  assert.equal(themes({ "board.mode": "dusk" }).storedMode(), "system");
  assert.equal(themes({}, true).isDark("system"), true);
  assert.equal(themes({}, false).isDark("system"), false);
  assert.equal(themes({}, true).isDark("light"), false);
  assert.equal(themes({}, false).isDark("dark"), true);
});

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
    const f = path.join(dir, "build.md");
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
  assert.deepEqual(act("waiting"), ["startnow", "▶ Start now"]);  // queued, with a session block: Start now is in its row of buttons
  assert.deepEqual(act("idle"), ["continue", "▶ Continue"]);
  assert.deepEqual(act("paused"), ["continue", "▶ Continue"]);
  assert.deepEqual(act("lost"), ["continue", "▶ Continue"]);
  assert.deepEqual(act("failed"), ["restart", "↻ Retry"]);
  assert.deepEqual(act("ended"), []);
});

// view_cases.json: the cases test_jobs.py runs through jobs.view, here through the page's own fallback.
const VIEWS = JSON.parse(fs.readFileSync(path.join(__dirname, "view_cases.json"), "utf8")).views;

test("a job with no view from the server is shown by the page's own table, the same as the server's", () => {
  for (const { job, label, actions } of VIEWS)
    assert.deepEqual(page.jobView(job), { label, actions }, JSON.stringify(job));
  assert.deepEqual(page.jobView(undefined), { label: "", actions: [] });
});

// view_cases.json "drafts": the cases test_drafts.py runs through drafts.view, here through draftView.
const DRAFT_VIEWS = JSON.parse(fs.readFileSync(path.join(__dirname, "view_cases.json"), "utf8")).drafts;

test("a draft is shown by the page's table, the same as the server's", () => {
  for (const { draft, label, actions } of DRAFT_VIEWS)
    assert.deepEqual(page.draftView(draft), { label, actions }, JSON.stringify(draft));
});

test("a job that comes with a view is shown by it, whatever its state says", () => {
  const job = { key: "PLN-01", state: "idle", view: { label: "working", actions: ["pause"] } };
  assert.equal(page.sessionState(job), "working");
  assert.equal(page.canMessage(job), false);
  assert.match(page.wakeButton(job), /data-act="pause"/);
  assert.doesNotMatch(page.wakeButton(job), /data-act="continue"/);
  const talk = { key: "PLN-01", state: "ended", ready: true, view: { label: "idle", actions: ["reply", "continue", "close"] } };
  assert.equal(page.canMessage(talk), true);
  assert.equal(page.wantsReply(talk), true);
  assert.match(page.replyHTML(talk), /data-act="close-story"/);
  assert.doesNotMatch(page.replyHTML({ ...talk, view: { label: "idle", actions: ["reply", "continue"] } }), /data-act="close-story"/);
});

test("a session's state reads pausing while it writes its handoff, then paused", () => {
  assert.equal(page.sessionState({ state: "started", pausing: true }), "pausing");
  assert.equal(page.sessionState({ state: "paused" }), "paused");
  assert.equal(page.sessionState({ state: "started" }), "working");
  assert.equal(page.sessionState({ state: "waiting" }), "");  // the pill beside the key says queued
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
    const f = path.join(dir, "build.md");
    fs.writeFileSync(f, text);
    return spawnSync("node", [path.join(__dirname, "kandalf", "check.js"), f], { encoding: "utf8" });
  };
  try {
    assert.equal(run("## In flight\n\n## Done\n- [x] PLN-02 **Two.**\n").status, 0);
    assert.equal(run("## In flight\n\n- [ ] PLN-01 **One.**\nProse after a tile.\n").status, 1);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test("a session waiting for input has the box open; one at work or idle has it folded behind a chevron", () => {
  for (const job of [{ state: "started" }, { state: "idle" }, { state: "lost" }, { state: "idle", waiting: "question" }])
    assert.equal(page.canMessage(job), true, JSON.stringify(job));
  for (const job of [{ state: "started", pausing: true }, { state: "paused" }, { state: "waiting" }, { state: "failed" }, undefined])
    assert.equal(page.canMessage(job), false, JSON.stringify(job));
  const open = page.replyHTML({ key: "PLN-01", state: "idle", waiting: "question" });
  assert.match(open, /<details class="session-reply"[^>]* open>/);
  const atWork = page.replyHTML({ key: "PLN-02", state: "started" });
  assert.match(atWork, /<details class="session-reply" data-key="PLN-02">/);  // folded
  assert.match(atWork, /<summary[^>]*>[^<]*interrupt with a message/);
  assert.match(atWork, /data-act="reply" data-key="PLN-02"[^>]*>Interrupt and send</);
  assert.match(page.replyHTML({ key: "PLN-03", state: "idle" }), /<summary[^>]*>[^<]*message Claude/);
});

test("the message box carries the session's log, with a link that opens it in a window", () => {
  const box = page.replyHTML({ key: "PLN-02", state: "started" });
  assert.match(box, /<summary[^>]*>[^<]*Log · interrupt with a message/);
  assert.match(box, /<pre class="session-log" data-log="PLN-02"/);
  assert.match(box, /href="\/log\?key=PLN-02" target="_blank"/);
  assert.match(page.replyHTML({ key: "PLN-03", state: "idle" }), /<summary[^>]*>[^<]*Log · message Claude/);
});

test("a queued tile has a Start button beside its queued mark", () => {
  page.setPickups([{ key: "PLN-05", title: "Five", state: "waiting", behind: "PLN-01", at: 1 }]);
  try {
    const tag = page.pickupTag("PLN-05", { name: "In flight" }, null);
    assert.match(tag, /◌ queued/);
    assert.match(tag, /data-act="startnow" data-key="PLN-05"[^>]*>▶ Start now</);
    assert.equal(page.pickupTag("PLN-05", { name: "To do" }, null), "");
    // a paused story queued again still has its old session line: the pill shows all the same
    page.setPickups([{ key: "PLN-05", title: "Five", state: "waiting", again: "paused", id: "ab12cd34", at: 2 }]);
    const again = page.pickupTag("PLN-05", { name: "In flight" }, { id: "ab12cd34", rest: "" });
    assert.match(again, /◌ queued/);
    assert.doesNotMatch(again, /startnow/);  // its Start now is in the session block's row of buttons
  } finally { page.setPickups([]); }
});

test("a paused story has Queue beside Continue", () => {
  const acts = [...page.wakeButton({ key: "PLN-01", state: "paused" }).matchAll(/data-act="(\w+)"[^>]*>([^<]*)</g)].map(m => [m[1], m[2]]);
  assert.deepEqual(acts, [["continue", "▶ Continue"], ["queue", "◌ Queue"]]);
});

// A stand-in for a drawn board: columns of tiles and log panes, each with a scroll place.
function drawn(cols, panes) {
  const lists = cols.map(top => ({ scrollTop: top }));
  const pres = Object.entries(panes).map(([key, p]) => ({ dataset: { log: key }, clientHeight: 100, ...p }));
  return { scrollLeft: 0, scrollTop: 0, lists, pres,
    querySelectorAll: sel => sel.includes("pre") ? pres : lists,
    querySelector: sel => pres.find(p => sel.includes(`"${p.dataset.log}"`)) || null };
}
test("a board drawn again keeps each column and log pane where it was scrolled", () => {
  const root = drawn([600, 0, 240], { "PLN-75": { scrollTop: 30, scrollHeight: 900 }, "PLN-74": { scrollTop: 800, scrollHeight: 900 } });
  root.scrollLeft = 50;
  const back = page.scrollPlaces(root);
  // drawn again: new columns and panes at the top, the log grown, the board's own place lost
  const again = drawn([0, 0, 0], { "PLN-75": { scrollTop: 0, scrollHeight: 1200 }, "PLN-74": { scrollTop: 0, scrollHeight: 1200 } });
  Object.assign(root, again, { scrollLeft: 0 });
  back();
  assert.deepEqual(root.lists.map(l => l.scrollTop), [600, 0, 240]);
  assert.equal(root.scrollLeft, 50);
  assert.equal(root.pres[0].scrollTop, 30);    // scrolled up to read: left there
  assert.equal(root.pres[1].scrollTop, 1200);  // at its newest line: kept at the newest
});

// One story in each state the stats strip counts, and some it leaves out.
const BUILDS = `# Plans

## To do

- [ ] PLN-01 **Not begun.**
- [ ] PLN-02 **Ready, moved back.**
  **Ready for review:** all green.

## In flight

- [ ] PLN-10 **At work.**
- [ ] PLN-11 **Asked a question.**
- [ ] PLN-12 **Session gone.**
- [ ] PLN-13 **Not started.**
- [ ] PLN-14 **Finished.**
  **Ready for review:** all green.
- [ ] PLN-15 **Queued.**
- [ ] PLN-16 **Paused.**
- [ ] PLN-17 **Stopped without a word.**
- [ ] PLN-18 **Ready, but back at work on a review reply.**
  **Ready for review:** all green.
- [ ] PLN-19 **Carried along with PLN-10.**
- [ ] PLN-22 **Finished, and its session stopped to wait for the review.**
  **Ready for review:** all green.
- [ ] PLN-23 **Finished, and its session has gone since.**
  **Ready for review:** all green.

## Done

- [x] PLN-20 **Shipped, branch not merged.**
- [x] PLN-21 **Shipped.**
  **Ready for review:** all green.
`;
const BUILD_JOBS = [
  { key: "PLN-10", state: "started", with: ["PLN-19"] }, { key: "PLN-11", state: "idle", waiting: "question" },
  { key: "PLN-12", state: "lost" }, { key: "PLN-13", state: "failed", error: "no worktree" },
  { key: "PLN-14", state: "idle", ready: true }, { key: "PLN-15", state: "waiting" }, { key: "PLN-16", state: "paused" },
  { key: "PLN-17", state: "idle" }, { key: "PLN-18", state: "started" }, { key: "PLN-21", state: "ended" },
  { key: "PLN-22", state: "idle", waiting: "question", ready: true }, { key: "PLN-23", state: "lost", ready: true },
];
test("the stats strip counts each build once, by what it needs", () => {
  page.open("build.md", BUILDS);
  page.setPickups(BUILD_JOBS); page.setUnmerged({ "PLN-20": ["feat/pln-20"] });
  try {
    assert.deepEqual(page.buildStats(), {
      work: ["PLN-10", "PLN-18"],            // a session is on it; the tile it carries along isn't a build of its own
      need: ["PLN-11", "PLN-12", "PLN-13"],  // asked, gone or not started: the tiles the board marks
      ready: ["PLN-02", "PLN-14", "PLN-22", "PLN-23"],  // waits for review, in any column but Done, however its session stopped
      queued: ["PLN-15"], paused: ["PLN-16"],
      idle: ["PLN-17"],                      // stopped without asking: a master waiting on its sub-stories, say
      unmerged: ["PLN-20"],
    });
    const tile = key => page.section("In flight").cards.find(c => page.cardKey(c) === key);
    assert.equal(page.buildState(tile("PLN-19"), page.section("In flight")), "");
    assert.equal(page.buildState(page.section("To do").cards[0], page.section("To do")), "");
  } finally { page.setPickups([]); page.setUnmerged({}); }
});

test("the corner says the release on its tag, and a dev build with when it was built otherwise", () => {
  const when = iso => "at " + iso;
  assert.deepEqual(page.versionLabel({ version: "1.1.10", tag: "v1.1.10", commit: "32aa882", dirty: false, built: "2026-11-21T20:40:00+00:00" }, when),
                   { text: "kandalf 1.1.10", title: "kandalf 1.1.10, release v1.1.10 (commit 32aa882)" });
  const dev = page.versionLabel({ version: "1.1.10", tag: null, commit: "abc1234", dirty: true, built: "2026-11-21T20:40:00+00:00" }, when);
  assert.equal(dev.text, "kandalf 1.1.10+dev · built at 2026-11-21T20:40:00+00:00");
  assert.match(dev.title, /commit abc1234, with changes not committed/);
  assert.match(page.versionLabel({ version: "1.1.10", tag: "v1.1.5", commit: "abc1234", built: "x" }, when).text, /\+dev/);  // another release's tag
  assert.equal(page.versionLabel({ version: "1.1.10" }).text, "kandalf 1.1.10");  // built from no checkout: nothing more known
  assert.equal(page.versionLabel(undefined).text, "");  // an older server
});
test("a Base line names the branch a story is cut from; withBase writes it where withModel puts a Model line", () => {
  page.setTrunk("dev");
  const md = "PLN-01 **One.**\n  The description.\n  - [ ] **A sub-task.**";
  assert.equal(page.baseOf(md), "");
  const set = page.withBase(md, "feature/x");
  assert.equal(set, "PLN-01 **One.**\n  The description.\n  Base: feature/x\n  - [ ] **A sub-task.**");
  assert.equal(page.baseOf(set), "feature/x");
  assert.equal(page.withBase(set, "release-2"), set.replace("Base: feature/x", "Base: release-2"));  // changed in place
  assert.equal(page.withBase(set, ""), md);  // back to the store's trunk
  assert.equal(page.withBase(set, "dev"), md);  // the trunk's own name: no line either
  assert.equal(page.baseOf("PLN-02 **Two.**\n  base: `release/2.0`."), "release/2.0");
  assert.equal(page.storyParts({ body: ["  Base: feature/x", "  Some words."] }).base, "feature/x");
  assert.deepEqual(page.storyParts({ body: ["  Base: feature/x", "  Some words."] }).desc, ["Some words."]);
});

test("a Tests line asks for the full suite on merge; withTests writes it only when it differs from what the tile gets anyway", () => {
  page.setFullSuite(false);
  const md = "PLN-01 **One.**\n  Some text.\n  - [ ] a step";
  assert.equal(page.testsOf("PLN-01 **One.**\n  tests: FULL."), "full");
  assert.equal(page.testsOf(md), "");
  const set = page.withTests(md, true, "");
  assert.equal(set, "PLN-01 **One.**\n  Some text.\n  Tests: full\n  - [ ] a step");
  assert.equal(page.withTests(set, false, ""), md);
  assert.equal(page.withTests(md, false, "full"), md.replace("  - [ ]", "  Tests: affected\n  - [ ]"));
  assert.equal(page.withTests(set, true, "full"), md);  // the master already asks for it
  page.setFullSuite(true);
  assert.equal(page.withTests(md, true, ""), md);
  assert.equal(page.withTests(md, false, ""), md.replace("  - [ ]", "  Tests: affected\n  - [ ]"));
  page.setFullSuite(false);
});

test("a tile with no Tests line takes its nearest master's", () => {
  page.open("build.md", "## In flight\n- [ ] PLN-01 **Master.**\n  Tests: full\n- [ ] PLN-02 **Kid.**\n  Part of PLN-01.\n- [ ] PLN-03 **Grandkid.**\n  Part of PLN-02.\n- [ ] PLN-04 **Other.**\n");
  assert.equal(page.inheritedTests("PLN-03 **Grandkid.**\n  Part of PLN-02."), "full");
  assert.equal(page.inheritedTests("PLN-04 **Other.**"), "");
});

test("a ring of masters never hands a tile its own Tests line back", () => {
  page.open("build.md", "## A\n\n- [ ] PLN-01 **First.** Part of PLN-02.\n  Tests: full\n- [ ] PLN-02 **Second.** Part of PLN-01.\n", {});
  assert.equal(page.inheritedTests("PLN-01 **First.** Part of PLN-02.\n  Tests: full"), "");  // PLN-01 stands as the master
  assert.equal(page.inheritedTests("PLN-02 **Second.** Part of PLN-01."), "full");
});

test("a tile with no Tests line takes a master on another board's, from the server's index", () => {
  const md = "- [ ] PLN-02 **Kid.**\n  Part of PLN-10.";
  const server = tests => ({ "PLN-10": { title: "Master", file: "plan.md", column: "Feature", done: false, parent: "PLN-11", mentions: [], tests },
                             "PLN-11": { title: "Grand", file: "archive/archived.md", column: "Archived", done: true, parent: null, mentions: [], tests: "affected" } });
  page.open("build.md", "## In flight\n\n" + md + "\n", server("full"));
  assert.equal(page.inheritedTests(md), "full");
  page.open("build.md", "## In flight\n\n" + md + "\n", server(null));  // the master says nothing: its master does
  assert.equal(page.inheritedTests(md), "affected");
  page.open("build.md", "## In flight\n\n" + md + "\n- [ ] PLN-10 **Here now.**\n  Tests: full\n", server("affected"));  // the open board's own text is newer than the server's
  assert.equal(page.inheritedTests(md), "full");
  page.open("build.md", "## In flight\n\n" + md + "\n", {});  // an older server: no index, no line
  assert.equal(page.inheritedTests(md), "");
});

const SETTINGS_VIEW = {
  mtime: "1", set: { parallel: 3 }, problem: null,
  effective: { pickup: true, agent: "claude", model: "opus", permission: "auto", parallel: 3, sessions: 2, fullSuiteOnMerge: false, trunk: "main", tidy: true, push: true, commitDelay: 300 },
  defaults: { pickup: false, agent: "claude", model: "opus", permission: "auto", parallel: 1, sessions: 2, fullSuiteOnMerge: false, trunk: "main", tidy: true, push: true, commitDelay: 300 },
  fields: {
    pickup: { kind: "bool", section: "Pickup", label: "Pickup" },
    agent: { kind: "choice", choices: ["claude", "copilot", "agy"], section: "Pickup", label: "Agent" },
    model: { kind: "text", section: "Pickup", label: "Model" },
    parallel: { kind: "int", min: 1, section: "Pickup", label: "Stories at once", help: "How many stories <b>on their own</b> work at once." },
    trunk: { kind: "text", section: "Git and commits", label: "Trunk" },
  },
  readonly: { name: "T7", key: "PLN", port: 8799, folder: "/tmp/x", app: null, repos: [] },
  readonlyHelp: { key: "The prefix of tile keys." },
};

test("a setting whose value is outside its choices shows it selected, and saving without an edit changes nothing", () => {
  const view = { ...SETTINGS_VIEW, effective: { ...SETTINGS_VIEW.effective, agent: "gemini" } };
  const out = page.settingsHTML(view);
  assert.match(out, /<option selected>gemini<\/option>/);
  assert.equal((out.match(/<option selected>/g) || []).filter(Boolean).length >= 1, true);
  const ctl = { parallel: { value: "3" }, pickup: { checked: true }, agent: { value: "gemini" }, model: { value: "opus" }, trunk: { value: "main" } };
  const form = { querySelector: sel => ctl[sel.match(/name="(\w+)"/)[1]] };
  assert.deepEqual(page.settingsChanges(view, form), {});
});

test("each setting and store fact has its meaning beside it, escaped, and a setting without one has none", () => {
  const out = page.settingsHTML(SETTINGS_VIEW);
  const row = k => out.match(new RegExp(`data-key="${k}"[\\s\\S]*?</div>`))[0];
  assert.match(row("parallel"), /class="shelp"[^>]*data-tip="How many stories &lt;b&gt;on their own&lt;\/b&gt; work at once\."/);
  assert.match(row("parallel"), /aria-label="How many stories &lt;b&gt;on their own/);
  assert.doesNotMatch(row("model"), /shelp/);
  assert.match(out, /<dt>Key<span class="shelp"[^>]*data-tip="The prefix of tile keys\."/);
  assert.match(out, /<dt>Name<\/dt>/);  // no help given: no marker
});

test("the settings panel shows each setting with its default, marks the changed ones, and sends only what changed", () => {
  const out = page.settingsHTML(SETTINGS_VIEW);
  const row = k => out.match(new RegExp(`<div class="[^"]*"[^>]*data-key="${k}"[\\s\\S]*?</div>\\s*(?=<div class="srow|</section>|<h4|$)`))[0];
  assert.match(row("parallel"), /class="[^"]*\bchanged\b/);
  assert.match(row("parallel"), /default: 1/);
  assert.match(row("parallel"), /data-reset="parallel"/);
  assert.doesNotMatch(row("pickup"), /changed/);
  assert.doesNotMatch(row("pickup"), /data-reset/);
  assert.doesNotMatch(row("pickup"), /default:/);  // an unchanged setting is its default: not said again
  assert.match(row("parallel"), /<label for="set-parallel">/);
  assert.match(out, /Git and commits/);
  assert.match(out, /About this store/);
  assert.match(out, /T7/);
  assert.match(out, /Set in board\.json/);
  const ctl = { parallel: { value: "2" }, pickup: { checked: true }, agent: { value: "claude" }, model: { value: "opus" }, trunk: { value: "main" } };
  const form = { querySelector: sel => ctl[sel.match(/name="(\w+)"/)[1]] };
  assert.deepEqual(page.settingsChanges(SETTINGS_VIEW, form), { parallel: 2 });
  ctl.parallel = { value: "3", dataset: { reset: "1" } };
  assert.deepEqual(page.settingsChanges(SETTINGS_VIEW, form), { parallel: null });
  ctl.parallel = { value: "4" };  // reset, then edited: the edit wins (the page drops the flag on input)
  assert.deepEqual(page.settingsChanges(SETTINGS_VIEW, form), { parallel: 4 });
  assert.match(page.settingsHTML({ ...SETTINGS_VIEW, problem: "board.json doesn't parse" }), /doesn&#39;t parse|doesn't parse/);
});

test("a text setting with suggestions gets its own pick list, with examples for where worktrees go", () => {
  const view = { ...SETTINGS_VIEW,
    effective: { ...SETTINGS_VIEW.effective, worktree: "{app}/.worktrees/{key}" },
    defaults: { ...SETTINGS_VIEW.defaults, worktree: "{app}/.worktrees/{key}" },
    fields: { ...SETTINGS_VIEW.fields, worktree: { kind: "text", section: "Git and commits", label: "Worktrees" } },
    readonly: { ...SETTINGS_VIEW.readonly, app: "/x/flycogo-back-end" } };
  const out = page.settingsHTML(view);
  assert.doesNotMatch(out, /<datalist/);  // a datalist shows only what matches the field, so a filled field showed nothing
  const picks = k => out.match(new RegExp(`<div class="spicks" id="picks-${k}"[\\s\\S]*?</div>`))[0];
  assert.match(out, /data-pick="worktree"/);
  const wt = picks("worktree");
  assert.match(wt, /popover/);
  for (const [value, example] of [["{app}/.worktrees/{key}", "flycogo-back-end/.worktrees/pln-12"], ["{app}-{key}", "flycogo-back-end-pln-12"], ["worktrees/{app}-{key}", "worktrees/flycogo-back-end-pln-12"]]) {
    assert.ok(wt.includes(`data-value="${value}"`), value);
    assert.ok(wt.includes(example), example);
  }
  assert.match(wt, /Inside the project/);
  assert.match(wt, /Beside the project/);
  assert.match(picks("model"), /data-value="opus"[\s\S]*data-value="sonnet"/);  // the agent's models
  assert.doesNotMatch(out, /data-pick="trunk"/);  // no branches known: nothing to pick from
});

// ---------- story map ----------
const mapTile = (title, more = {}) => ({ title, file: "plan.md", column: "Feature", done: false, parent: null, mentions: [], domain: null, ...more });
const MAP_INDEX = () => new Map([
  ["PLN-01", mapTile("Admin: shell")],
  ["PLN-02", mapTile("Admin: analytics", { parent: "PLN-01", mentions: ["PLN-01", "PLN-11"] })],
  ["PLN-03", mapTile("Operator: one odd kid", { parent: "PLN-01", file: "build.md", column: "In flight" })],
  ["PLN-04", mapTile("Admin: settings")],
  ["PLN-05", mapTile("Admin: roles", { parent: "PLN-04" })],
  ["PLN-10", mapTile("Broker: foundations", { domain: "Sales" })],
  ["PLN-11", mapTile("Rates", { parent: "PLN-10", mentions: ["PLN-02"] })],
  ["PLN-12", mapTile("Shipped rates", { parent: "PLN-10", file: "archive/archived.md", column: "Archived", done: true })],
  ["PLN-20", mapTile("Docs: one of a kind", { file: "build.md", column: "To do" })],
  ["PLN-21", mapTile("Loose end", { mentions: ["PLN-99"] })],
]);

test("a story's domain is its own line, else its master's, else a title prefix that several stories share", () => {
  const d = page.tileDomains(MAP_INDEX());
  assert.equal(d.get("PLN-01"), "Admin");       // two masters start "Admin:"
  assert.equal(d.get("PLN-02"), "Admin");
  assert.equal(d.get("PLN-03"), "Admin");       // its master's, though its own title says Operator
  assert.equal(d.get("PLN-10"), "Sales");       // its own Domain: line beats its title
  assert.equal(d.get("PLN-11"), "Sales");       // inherited
  assert.equal(d.get("PLN-20"), "");            // "Docs" starts only one story
  assert.equal(d.get("PLN-21"), "");
  const spelt = new Map([["A-1", mapTile("x", { domain: "admin" })], ["A-2", mapTile("y", { domain: "Admin" })]]);
  assert.equal(new Set(page.tileDomains(spelt).values()).size, 1);  // one domain, however it is capitalised
  const ring = new Map([["A-1", mapTile("x", { parent: "A-2" })], ["A-2", mapTile("y", { parent: "A-1" })]]);
  assert.deepEqual([...page.tileDomains(ring).values()], ["", ""]);  // a ring of masters doesn't loop
});

test("a domain can be named anything, and a title's first word counts only as a whole word", () => {
  const index = new Map([["A-1", mapTile("One", { domain: "Ops|Infra", mentions: ["A-2"] })], ["A-2", mapTile("Two", { domain: "Web" })],
    ["A-3", mapTile("Fixing: a")], ["A-4", mapTile("Fix: b")], ["A-5", mapTile("Fix-it: c")], ["A-6", mapTile("Fix: d")], ["A-7", mapTile("Fix: e")]]);
  const g = page.mapGraph(index, new Map(), []), lay = page.mapLayout(g);  // a "|" in a name once broke the layout
  assert.equal(lay.at.size, 7);
  assert.deepEqual(["A-3", "A-4", "A-5"].map(k => g.nodes.get(k).domain), ["", "Fix", ""]);
});

test("a story at work carries its agent's own mark: Claude's, Copilot's, or Gemini's for Antigravity", () => {
  assert.deepEqual(["Claude", "Copilot", "Antigravity", ""].map(a => page.agentMark(a).match(/m-agent (\w+)/)[1]), ["claude", "copilot", "gemini", "claude"]);
  assert.match(page.agentMark("Claude"), /stroke="#d97757"/);
  assert.doesNotMatch(page.agentMark("Copilot"), /<circle/);          // in a line of text: no disc behind it
  assert.match(page.agentMark("Copilot", "var(--bg)"), /<circle r="8.5" fill="var\(--bg\)"/);  // on the map: one
});

test("the strip's keys for the map come from each story's state, and a Done tile not merged", () => {
  const index = new Map([["A-1", mapTile("Work", { file: "build.md", column: "In flight" })], ["A-2", mapTile("Wait", { file: "build.md", column: "In flight" })],
    ["A-3", mapTile("Done", { file: "build.md", column: "Done", done: true })], ["A-4", mapTile("Quiet")]]);
  page.setUnmerged({ "A-3": ["a-3-done"] });
  const stats = page.mapStats(page.mapGraph(index, new Map(), [{ key: "A-1", state: "started" }, { key: "A-2", state: "waiting" }]));
  page.setUnmerged({});
  assert.deepEqual([stats.work, stats.queued, stats.unmerged, stats.need], [["A-1"], ["A-2"], ["A-3"], []]);
});

test("a session's state shows on the map only for a story in flight", () => {
  const index = new Map([["A-1", mapTile("Back in the backlog")], ["A-2", mapTile("In flight", { file: "build.md", column: "In flight" })]]);
  const g = page.mapGraph(index, new Map(), [{ key: "A-1", state: "lost" }, { key: "A-2", state: "started" }]);
  assert.deepEqual([g.nodes.get("A-1").state, g.nodes.get("A-1").next, g.nodes.get("A-2").state], ["", false, "work"]);
});

test("hiding done stories leaves them off the map, with every join to them", () => {
  const index = new Map([["A-1", mapTile("Open", { mentions: ["A-2"], blockers: ["A-2"] })], ["A-2", mapTile("Shipped", { done: true, mentions: ["A-1"] })],
    ["A-3", mapTile("Also open", { mentions: ["A-1"] })]]);
  const g = page.mapHideDone(page.mapGraph(index, new Map([["A-1", new Set(["A-2"])]]), []), new Set());
  assert.deepEqual([...g.nodes.keys()], ["A-1", "A-3"]);
  assert.deepEqual(g.edges, [{ a: "A-1", b: "A-3", kind: "mention" }]);
  const n = g.nodes.get("A-1");
  assert.deepEqual([n.mentions, n.linked, n.blockedBy], [["A-3"], [], []]);
});

test("hiding done stories keeps a done master whose sub-stories are not all done, and its count", () => {
  const index = new Map([["A-1", mapTile("Master", { done: true })], ["A-2", mapTile("Done part", { parent: "A-1", done: true })],
    ["A-3", mapTile("Open part", { parent: "A-1" })], ["A-4", mapTile("Done alone", { done: true })]]);
  const g = page.mapHideDone(page.mapGraph(index, new Map(), []), new Set());
  assert.deepEqual([...g.nodes.keys()], ["A-1", "A-3"]);
  assert.deepEqual([g.nodes.get("A-1").kids, g.nodes.get("A-1").doneKids, g.nodes.get("A-1").allKids, g.nodes.get("A-3").parent], [["A-3"], 1, 2, "A-1"]);
});

test("hiding done stories keeps a done story whose branch is not merged yet", () => {
  const index = new Map([["A-1", mapTile("Shipped", { done: true })], ["A-2", mapTile("Not merged", { done: true })]]);
  const g = page.mapHideDone(page.mapGraph(index, new Map(), []), new Set(["A-2"]));
  assert.deepEqual([...g.nodes.keys()], ["A-2"]);
});

test("hiding done stories leaves the graph it was given as it was", () => {
  const index = new Map([["A-1", mapTile("Open", { mentions: ["A-2"] })], ["A-2", mapTile("Shipped", { done: true, mentions: ["A-1"] })]]);
  const full = page.mapGraph(index, new Map(), []);
  page.mapHideDone(full, new Set());
  assert.deepEqual([[...full.nodes.keys()], full.nodes.get("A-1").mentions, full.edges.length], [["A-1", "A-2"], ["A-2"], 1]);
});

test("the map joins a blocker to what it blocks, and not as a mention", () => {
  const index = new Map([["A-1", mapTile("Waits", { blockers: ["A-2"], mentions: ["A-2"] })], ["A-2", mapTile("Blocks", { mentions: ["A-1"] })]]);
  const g = page.mapGraph(index, new Map(), []), n = k => g.nodes.get(k);
  assert.deepEqual(g.edges, [{ a: "A-1", b: "A-2", kind: "blocks" }]);
  assert.deepEqual([n("A-1").blockedBy, n("A-2").blocks, n("A-1").blocks, n("A-2").blockedBy], [["A-2"], ["A-1"], [], []]);
  assert.deepEqual([n("A-1").mentions, n("A-2").mentions], [[], []]);
});

test("a blocker no tile has is blocking but not on the map as a node", () => {
  const index = new Map([["A-1", mapTile("Waits", { blockers: ["A-9"], file: "build.md", column: "To do" })]]);
  const n = page.mapGraph(index, new Map(), []).nodes.get("A-1");
  assert.deepEqual([n.blockedBy, n.blocked, n.next], [[], true, false]);
});

test("a blocked story is not one to pull next", () => {
  const make = done => new Map([["A-1", mapTile("Waits", { file: "build.md", column: "To do", blockers: ["A-2"] })], ["A-2", mapTile("Blocker", { done })]]);
  const blocked = page.mapGraph(make(false), new Map(), []).nodes.get("A-1"), free = page.mapGraph(make(true), new Map(), []).nodes.get("A-1");
  assert.deepEqual([blocked.blocked, blocked.next, free.blocked, free.next], [true, false, false, true]);
});

test("a story blocked only through its master is blocked on the map", () => {
  const index = new Map([["A-1", mapTile("Master", { blockers: ["A-3"] })], ["A-2", mapTile("Sub", { parent: "A-1" })], ["A-3", mapTile("Blocker")]]);
  const n = page.mapGraph(index, new Map(), []).nodes.get("A-2");
  assert.deepEqual([n.blocked, n.blockedBy], [true, []]);
});

test("the map's graph says where each story is, what it is joined to and what to pull next", () => {
  const g = page.mapGraph(MAP_INDEX(), new Map([["PLN-20", new Set(["PLN-21"])], ["PLN-21", new Set(["PLN-20"])]]),
    [{ key: "PLN-03", state: "idle", waiting: "question" }]);
  const n = k => g.nodes.get(k);
  assert.deepEqual([n("PLN-01").where, n("PLN-03").where, n("PLN-12").where, n("PLN-20").where], ["backlog", "flight", "done", "planned"]);
  assert.deepEqual(n("PLN-01").kids, ["PLN-02", "PLN-03"]);
  assert.equal(n("PLN-03").state, "need");
  assert.equal(n("PLN-10").doneKids, 1);
  // A master and its own sub-story are joined by the tree, not by a mention; a key nobody has is dropped.
  assert.deepEqual(g.edges, [{ a: "PLN-20", b: "PLN-21", kind: "linked" }, { a: "PLN-02", b: "PLN-11", kind: "mention" }]);
  assert.deepEqual([n("PLN-02").mentions, n("PLN-11").mentions, n("PLN-20").linked], [["PLN-11"], ["PLN-02"], ["PLN-21"]]);
  // Next: the one that needs you, the open sub-stories of masters that are moving, a planned story on its own.
  assert.deepEqual([...g.nodes.values()].filter(x => x.next).map(x => x.key), ["PLN-02", "PLN-03", "PLN-11", "PLN-20"]);
  assert.equal(n("PLN-05").next, false);  // its master hasn't started
  assert.equal(n("PLN-12").next, false);  // done
});

test("the map's layout is the same every time, keeps hubs apart and a domain's hubs together", () => {
  const index = MAP_INDEX();
  for (let i = 30; i < 42; i++) index.set(`PLN-${i}`, mapTile(`Broker: thing ${i}`, { parent: i % 4 ? `PLN-${i - i % 4}` : null }));
  const g = page.mapGraph(index, new Map(), []), lay = page.mapLayout(g);
  assert.deepEqual([...page.mapLayout(page.mapGraph(index, new Map(), [])).at], [...lay.at]);
  assert.equal(lay.at.size, g.nodes.size);
  for (const p of lay.at.values()) assert.ok(Number.isFinite(p.x) && Number.isFinite(p.y));
  const hubs = [...lay.hubs].map(([k, h]) => ({ k, ...lay.at.get(k), R: h.R, domain: g.nodes.get(k).domain }));
  const apart = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
  for (const a of hubs) for (const b of hubs) if (a.k < b.k) assert.ok(apart(a, b) >= a.R + b.R - 1, `${a.k} and ${b.k} overlap`);
  // A sub-story sits on its master's ring.
  const kid = lay.at.get("PLN-02"), master = lay.at.get("PLN-01");
  assert.ok(Math.abs(Math.hypot(kid.x - master.x, kid.y - master.y) - 78) < 1);
  // Every Broker hub is nearer the middle of the Broker hubs than any Admin hub is.
  const brokers = hubs.filter(h => h.domain === "Broker"), mid = { x: brokers.reduce((s, h) => s + h.x, 0) / brokers.length, y: brokers.reduce((s, h) => s + h.y, 0) / brokers.length };
  assert.ok(brokers.length >= 3);
  assert.ok(Math.max(...brokers.map(h => apart(h, mid))) < Math.min(...hubs.filter(h => h.domain === "Admin").map(h => apart(h, mid))));
});

test("a sub-story's own sub-stories fan out beyond it", () => {
  const index = new Map([["A-1", mapTile("Top")], ["A-2", mapTile("Mid", { parent: "A-1" })], ["A-3", mapTile("Leaf", { parent: "A-2" })], ["A-4", mapTile("Leaf two", { parent: "A-2" })]]);
  const lay = page.mapLayout(page.mapGraph(index, new Map(), [])), from = k => Math.hypot(lay.at.get(k).x - lay.at.get("A-1").x, lay.at.get(k).y - lay.at.get("A-1").y);
  assert.ok(from("A-3") > from("A-2") + 50 && from("A-4") > from("A-2") + 50);
  assert.ok(lay.hubs.get("A-1").R > from("A-3"));
  assert.notDeepEqual(lay.at.get("A-3"), lay.at.get("A-4"));
});

test("every theme has a dark palette and a dark accent for each of its accents", () => {
  const src = html.match(/const BOARD_THEMES = (\{[\s\S]*?\n\});/)[1];
  const themes = new Function(`return ${src}`)();
  for (const [id, t] of Object.entries(themes)) {
    assert.ok(Array.isArray(t.dark), `${id} has dark accents`);
    assert.equal(t.dark.length, t.accents.length, `${id}: one dark accent per accent`);
    assert.ok(t.dark.every(c => /^#[0-9a-f]{6}$/i.test(c)), `${id}: dark accents are colours`);
    assert.match(html, new RegExp(`:root\\[data-theme="${id}"\\]\\[data-mode="dark"\\] \\{`), `${id} has a dark block`);
  }
  assert.doesNotMatch(html, /prefers-color-scheme: dark\) \{\s*:root\[data-theme/, "dark mode goes by data-mode, not the media query alone");
});

// ---------- tile_cases.json: the cases test_tiles.py runs through tiles.py ----------
const CASES = JSON.parse(fs.readFileSync(path.join(__dirname, "tile_cases.json"), "utf8"));
// What each reader of an attribute line gives for a tile's markdown, in the cases' terms (null: no such line).
const bodyLines = md => md.split("\n").slice(1);
const READERS = {
  model: md => page.modelOf(md) || null,
  base: md => page.baseOf(md) || null,
  tests: md => page.testsOf(md) || null,
  domain: md => page.domainOf(md) || null,
  repos: md => page.reposOf(md).length ? page.reposOf(md) : null,
  sessions: md => page.sessionsOf(bodyLines(md)) || null,
  ready: md => bodyLines(md).map(page.readyLine).find(r => r !== null) ?? null,
  decision: md => page.decisionOf(md),
};
// The page reads a session line for its id; the branch and cuts are the server's (tiles.session).
const sessionOf = md => { const m = bodyLines(md).map(page.sessionLine).find(Boolean); return m ? m.id : null; };

test("each line in tile_cases.json says what the case says", () => {
  for (const c of CASES.lines) {
    const md = "- [ ] PLN-01 **T.**\n" + c.line;
    if (c.kind === "status") assert.equal(page.statusLine(c.line) !== null, c.value, c.line);
    else if (c.kind === "session") assert.equal(sessionOf(md), c.value && c.value.id, c.line);
    else assert.deepEqual(READERS[c.kind](md), c.value, `${c.kind}: ${c.line}`);
  }
});

test("each domain name in tile_cases.json is taken or refused as the case says", () => {
  for (const c of CASES.domainNames) assert.deepEqual(page.domainName(c.raw), "error" in c ? { error: c.error } : { name: c.name }, JSON.stringify(c.raw));
});

test("each index in tile_cases.json has the domains the case says", () => {
  for (const c of CASES.tileDomains) assert.deepEqual(Object.fromEntries(page.tileDomains(new Map(Object.entries(c.index)))), c.domains, Object.keys(c.index).join());
});

test("each options block in tile_cases.json reads as the case says", () => {
  for (const c of CASES.options) assert.deepEqual(page.optionsOf(c.md), c.options, c.md);
});

test("each tile in tile_cases.json has the facts the case says", () => {
  for (const c of CASES.tiles) {
    page.open("build.md", `## ${c.column}\n\n${c.md}\n`);
    const card = page.section(c.column).cards[0], md = [card.text, ...card.body].join("\n");
    const split = page.splitCard(card.text), key = split.key || null;
    const facts = key ? page.tileIndex().get(key) : { ...page.tileFacts("", md), title: split.title.replace(/\*\*|`/g, ""), column: c.column, done: page.isDoneCol(c.column) };
    const got = { key, title: facts.title, parent: facts.parent, links: page.movesWith(key || "", md), mentions: facts.mentions, blockers: facts.blockers, done: facts.done, column: facts.column };
    for (const [name, value] of Object.entries(got)) if (name in c) assert.deepEqual(value, c[name], `${name}: ${c.md}`);
    for (const name of Object.keys(READERS)) if (name in c) assert.deepEqual(READERS[name](md), c[name], `${name}: ${c.md}`);
    if ("session" in c) assert.equal(sessionOf(md), c.session && c.session.id, c.md);
  }
});

test("each board in tile_cases.json has the tiles the case says", () => {
  for (const c of CASES.boards) {
    page.open(c.file || "build.md", c.text);
    const index = page.tileIndex();
    const got = page.cards().map(({ column, card }) => {
      const key = page.splitCard(card.text).key || null;
      return { key, column, done: key ? index.get(key).done : page.isDoneCol(column), md: page.cardMarkdown(card) };
    });
    // The page writes every tile with a "- " bullet (serialize), so a "* " tile reads as its "- " twin here.
    const dashed = md => md.replace(/^\* /, "- ");
    assert.deepEqual(got.map(g => [g.key, g.column, g.done, g.md]), c.tiles.map(w => [w.key, w.column, w.done, dashed(w.md)]), c.name);
    c.tiles.forEach((w, i) => {
      if ("model" in w) assert.equal(READERS.model(got[i].md), w.model, c.name);
      if ("parent" in w) assert.equal(index.get(w.key).parent, w.parent, c.name);
    });
  }
});

test("each column in tile_cases.json is In flight or Done as the case says", () => {
  for (const c of CASES.columns) {
    page.setPickupColumn(c.pickupColumn || "");
    if ("pickup" in c) assert.equal(page.isPickupColumn(c.name), c.pickup, c.name);
    if ("inFlight" in c) assert.equal(page.isPickupColumn(c.name), c.inFlight, c.name);
    if ("done" in c) assert.equal(page.isDoneCol(c.name), c.done, c.name);
    if ("decision" in c) assert.equal(page.isDecisionCol(c.name), c.decision, c.name);
  }
  page.setPickupColumn("");
});

test("each family in tile_cases.json says what blocks each tile and what was decided", () => {
  for (const c of CASES.blocked) {
    const others = {};
    for (const [name, text] of Object.entries(c.files)) {
      if (name === "build.md") continue;
      page.open(name, text);
      for (const [k, t] of page.tileIndex()) others[k] = t;
    }
    page.open("build.md", c.files["build.md"], others);
    const index = page.tileIndex();
    for (const [key, want] of Object.entries(c.expect)) {
      assert.deepEqual(page.blocking(key, index).map(b => b.key), want.blocking, `${c.name}: ${key} blocking`);
      assert.deepEqual(page.decisionsFor(key, index).map(d => d.key), want.decisions, `${c.name}: ${key} decisions`);
      assert.deepEqual(page.inRing(key, index), want.ring, `${c.name}: ${key} ring`);
    }
  }
});

test("a tile with a decision line in its body has that decision in its index entry", () => {
  page.open("build.md", "## Done\n\n- [x] PLN-30 **Auth.**\n  **Decision 2026-11-23:** retire it.\n");
  assert.deepEqual(page.tileIndex().get("PLN-30").decision, { date: "2026-11-23", text: "retire it." });
});

test("the page names a blocker no tile has and finds what a tile blocks", () => {
  page.open("build.md", "## To do\n\n- [ ] PLN-41 **Nav.** Blocked by PLN-99.\n- [ ] PLN-42 **Foot.** Blocked by PLN-41.\n");
  const index = page.tileIndex();
  assert.deepEqual(page.blocking("PLN-41", index), [{ key: "PLN-99", title: null, known: false }]);
  assert.deepEqual(page.blocking("PLN-42", index), [{ key: "PLN-41", title: "Nav", known: true }]);
  assert.deepEqual(page.blocksOf("PLN-41", index), ["PLN-42"]);
});

test("a story ready for review looks ready even when its session has gone; red is for a session gone without it", () => {
  assert.equal(page.cardLook({ state: "lost" }, true), " ready");   // finished, its session ended: green, not red
  assert.equal(page.cardLook({ state: "lost" }, false), " lost");   // gone without being ready: red
  assert.equal(page.cardLook({ state: "idle", ready: true }, true), " ready");
  assert.equal(page.cardLook({ state: "idle", waiting: "question" }, true), " needs-you");  // a question still comes first
  assert.equal(page.cardLook({ state: "started" }, false), "");
  assert.equal(page.cardLook(undefined, true), " ready");            // ready with no job on this server
  assert.equal(page.cardLook(undefined, false), "");
});

test("a tile's blocker lines say what it waits on, what was decided and what is missing", () => {
  page.open("build.md", "## To do\n\n- [ ] PLN-41 **Nav.** Blocked by PLN-30, PLN-31, PLN-32 and PLN-99.\n", {
    "PLN-30": { title: "Pro theme", file: "plan.md", column: "Feature", done: false, parent: null, mentions: [], blockers: [] },
    "PLN-31": { title: "Old auth", file: "archive/archived.md", column: "Archived", done: true, parent: null, mentions: [], blockers: [], decision: { date: "2026-11-20", text: "retire it." } },
    "PLN-32": { title: "Old nav", file: "archive/archived.md", column: "Archived", done: true, parent: null, mentions: [], blockers: [], decision: null },
  });
  assert.deepEqual(page.blockerLines("PLN-41", page.tileIndex()), [
    { kind: "blocked", key: "PLN-30", text: "Blocked by PLN-30 · Pro theme" },
    { kind: "decided", key: "PLN-31", text: "PLN-31 decided: retire it." },
    { kind: "missing", key: "PLN-99", text: "PLN-99 not found" }]);
});
test("two tiles that block each other say so once", () => {
  page.open("build.md", "## To do\n\n- [ ] PLN-41 **Nav.** Blocked by PLN-30.\n- [ ] PLN-30 **Foot.** Blocked by PLN-41.\n");
  assert.deepEqual(page.blockerLines("PLN-41", page.tileIndex()),
    [{ kind: "ring", key: "PLN-30", text: "PLN-30 and PLN-41 block each other" }]);
});
test("a sub-story shows its master's blockers", () => {
  page.open("build.md", "## To do\n\n- [ ] PLN-70 **Master.** Blocked by PLN-30.\n- [ ] PLN-71 **Kid.** Part of PLN-70.\n- [ ] PLN-30 **Theme.**\n");
  assert.deepEqual(page.blockerLines("PLN-71", page.tileIndex()), [{ kind: "blocked", key: "PLN-30", text: "Blocked by PLN-30 · Theme" }]);
});
test("a tile with no blockers has no lines", () => {
  page.open("build.md", "## To do\n\n- [ ] PLN-70 **Plain.**\n");
  assert.deepEqual(page.blockerLines("PLN-70", page.tileIndex()), []);
});
test("a blocked tile in flight says what it waits for", () => {
  page.open("build.md", "## In flight\n\n- [ ] PLN-41 **Nav.** Blocked by PLN-30.\n\n## To do\n\n- [ ] PLN-42 **Foot.** Blocked by PLN-30.\n");
  page.setPickups([], { "PLN-41": ["PLN-30", "PLN-31"], "PLN-42": ["PLN-30"] });
  try {
    assert.match(page.pickupTag("PLN-41", { name: "In flight" }, null), /waits for PLN-30 and PLN-31/);
    assert.equal(page.pickupTag("PLN-42", { name: "To do" }, null), "");
    page.setPickups([], { "PLN-41": ["PLN-30", "PLN-31"] }, false);
    assert.equal(page.pickupTag("PLN-41", { name: "In flight" }, null), "");  // pickup off: the last look's list says nothing
  } finally { page.setPickups([]); }
  assert.equal(page.pickupTag("PLN-41", { name: "In flight" }, null), "");  // an older server has no word on blocked tiles
});

test("a blocker row escapes a title, and a ring row links every key but the tile's own", () => {
  page.open("build.md", "## To do\n\n- [ ] PLN-41 **Nav.** Blocked by PLN-30.\n- [ ] PLN-30 **A <b>bold</b> one.** Blocked by PLN-41.\n- [ ] PLN-50 **Foot.** Blocked by PLN-51.\n- [ ] PLN-51 **A <b>bold</b> one.**\n");
  const index = page.tileIndex();
  const ring = page.blockerRowsHTML("PLN-41", index);
  assert.match(ring, /data-key="PLN-30"/);
  assert.doesNotMatch(ring, /data-key="PLN-41"/);
  const blocked = page.blockerRowsHTML("PLN-50", index);
  assert.match(blocked, /A &lt;b&gt;bold&lt;\/b&gt; one/);
  assert.doesNotMatch(blocked, /<b>bold/);
});
test("an answer's draft is kept under the tile's key, else its first line", () => {
  assert.equal(page.answerKey({ text: "PLN-30 **Auth?**" }), "PLN-30");
  assert.equal(page.answerKey({ text: "**Unkeyed?**", body: [] }), "**Unkeyed?**");
});

// A decision tile with an options block, as the page reads it.
const OPTIONS_TILE = [
  "- [ ] PLN-30 **Old theme.** Restyle or retire?",
  "  **Options 2026-11-24:** recommends B. [Research](active/2026-11-24-pln-30-options.md) Asked: look at the sidebar.",
  "  - **A. Restyle it?** Sidebar card, profile box.",
  "  - **B. Retire it.** Delete the app-shell rules.",
  "  - [ ] a sub-task after the block",
].join("\n");

test("a draft is found by its key, and an older server has none", () => {
  try {
    page.setPickups([], null, true, [{ key: "PLN-30", state: "working", started: 1000 }]);
    assert.equal(page.draftOf("PLN-30").state, "working");
    assert.equal(page.draftOf("PLN-31"), null);
    page.setPickups([]);
    assert.equal(page.draftOf("PLN-30"), null);
  } finally { page.setPickups([]); }
});

test("a tile's draft tag says where its draft stands", () => {
  page.open("build.md", "# Plans\n\n## Design decision\n\n" + OPTIONS_TILE + "\n- [ ] PLN-31 **Bare.** No block.\n");
  const tag = (state, extra = {}, now) => {
    page.setPickups([], null, true, [{ key: "PLN-30", state, ...extra }]);
    return page.draftTag("PLN-30", now);
  };
  try {
    assert.equal(tag("queued"), "queued to draft");
    assert.equal(tag("working", { started: 1000 }, 1000 + 12 * 60), "drafting options · 12m");
    assert.equal(tag("working", { started: 1000 }, 1000 + 30), "drafting options");
    assert.equal(tag("working", {}, 5000), "drafting options");
    assert.equal(tag("ready"), "options ready");
    assert.equal(tag("empty"), "draft gave no options");
    assert.equal(tag("need"), "draft needs you");
    assert.equal(tag("lost"), "draft lost");
    // no draft: a tile with a block is "options ready", one without says nothing
    page.setPickups([], null, true, []);
    assert.equal(page.draftTag("PLN-30"), "options ready");
    assert.equal(page.draftTag("PLN-31"), "");
    // an older server sends no drafts at all: a tile's block still says so, one without says nothing
    page.setPickups([]);
    assert.equal(page.draftTag("PLN-30"), "options ready");
    assert.equal(page.draftTag("PLN-31"), "");
  } finally { page.setPickups([]); }
});

test("option rows mark the recommended one, and a tile with no block has none", () => {
  const rows = page.optionRows(OPTIONS_TILE);
  assert.deepEqual(rows.map(r => [r.letter, r.title, r.recommended]), [["A", "Restyle it?", false], ["B", "Retire it", true]]);
  assert.equal(rows[1].summary, "Delete the app-shell rules.");
  assert.deepEqual(page.optionRows("- [ ] PLN-31 **Bare.** No block."), []);
});

test("an option's text for the Answer box has no doubled full stop", () => {
  assert.equal(page.useText({ letter: "A", title: "Restyle it?", summary: "Sidebar card." }), "A. Restyle it? Sidebar card.");
  assert.equal(page.useText({ letter: "B", title: "Retire it", summary: "Delete the rules." }), "B. Retire it. Delete the rules.");
  assert.equal(page.useText({ letter: "C", title: "Leave it", summary: "" }), "C. Leave it.");
  assert.equal(page.useText({ letter: "D", title: "Stop?", summary: "" }), "D. Stop?");
});

test("a tile's face body leaves out the options block and keeps what follows it", () => {
  const lines = OPTIONS_TILE.split("\n").slice(1);
  assert.deepEqual(page.faceBody(lines, "Design decision"), ["  - [ ] a sub-task after the block"]);
  assert.deepEqual(page.faceBody(["  plain line"], "Design decision"), ["  plain line"]);
  // outside a decision column nothing replaces the lines, so they stay as body text
  assert.deepEqual(page.faceBody(lines, "To do"), lines);
});

test("a tile that differs only in its options block is told apart from one that differs elsewhere", () => {
  const other = OPTIONS_TILE.replace("B. Retire it.", "B. Retire it now.").replace("recommends B", "recommends A");
  assert.equal(page.onlyOptionsDiffer(OPTIONS_TILE, other), true);
  assert.equal(page.onlyOptionsDiffer(OPTIONS_TILE, OPTIONS_TILE), false);
  assert.equal(page.onlyOptionsDiffer(OPTIONS_TILE, OPTIONS_TILE.replace("Restyle or retire?", "Keep it?")), false);
  const bare = OPTIONS_TILE.split("\n").filter((_, n) => n !== 1 && n !== 2 && n !== 3).join("\n");
  assert.equal(page.onlyOptionsDiffer(bare, OPTIONS_TILE), true);  // a block that has just arrived
});

test("a tile's sub-tasks are found at their lines as an options block above them appears, grows, shrinks and goes", () => {
  const head = "- [ ] PLN-30 **Old theme.** Restyle or retire?", tasks = ["  - [ ] restyle the sidebar", "  - [x] read the app-shell rules"];
  const block = n => ["  **Options 2026-11-24:** recommends A.", ...["A", "B", "C"].slice(0, n).map(l => `  - **${l}. Option ${l}.** Its summary.`)];
  const tile = n => [head, ...(n ? block(n) : []), "  Some words.", ...tasks].join("\n");
  assert.deepEqual(page.subTaskLines(tile(0)), [2, 3]);
  assert.deepEqual(page.subTaskLines(tile(2)), [5, 6]);
  assert.deepEqual(page.subTaskLines(tile(3)), [6, 7]);
  assert.deepEqual(page.subTaskLines(tile(1)), [4, 5]);
  assert.deepEqual(page.subTaskLines(OPTIONS_TILE), [4]);
  // the k-th sub-task before is the k-th after, at its new line, whichever way the block changes
  for (const [from, to] of [[0, 2], [2, 3], [3, 1], [1, 0], [2, 0]]) {
    const was = tile(from).split("\n"), now = tile(to).split("\n");
    const before = page.subTaskLines(tile(from)), after = page.subTaskLines(tile(to));
    assert.deepEqual(after.map(i => now[i]), before.map(i => was[i]), `${from} option lines, then ${to}`);
    assert.deepEqual(after.map(i => now[i]), tasks);
  }
  // the title line is never a sub-task, nor is a bullet or an option line
  assert.deepEqual(page.subTaskLines("- [ ] PLN-31 **x.**\n  - a bullet\n  - **A. Do it.** Now."), []);
});

test("story messages and the offer to start in parallel run on the plans board and the map, drafts on any board with a decision column", () => {
  assert.deepEqual(page.pollsFor("build.md", false, false), { poll: true, stories: true });
  assert.deepEqual(page.pollsFor("build.md", false, true), { poll: true, stories: true });
  assert.deepEqual(page.pollsFor("plan.md", false, true), { poll: true, stories: false });
  assert.deepEqual(page.pollsFor("plan.md", true, true), { poll: true, stories: true });
  assert.deepEqual(page.pollsFor("plan.md", true, false), { poll: true, stories: true });
  assert.deepEqual(page.pollsFor("plan.md", false, false), { poll: false, stories: false });
});

test("a column's id in one parse of a board is matched to its id in another by name, and by order within one name", () => {
  const secs = (...names) => names.map((name, n) => ({ id: 100 + n, name }));
  const fresh = (...names) => names.map((name, n) => ({ id: 200 + n, name }));
  // the same names in the same order
  assert.deepEqual([...page.sectionIdMap(secs("To do", "Decisions", "Done"), fresh("To do", "Decisions", "Done"))],
    [["100", 200], ["101", 201], ["102", 202]]);
  // a renamed column has no match; the others still do
  assert.deepEqual([...page.sectionIdMap(secs("To do", "Decisions", "Done"), fresh("To do", "Open decisions", "Done"))],
    [["100", 200], ["102", 202]]);
  // a removed column has no match, and the ones after it are found where they are now
  assert.deepEqual([...page.sectionIdMap(secs("To do", "Doing", "Decisions"), fresh("To do", "Decisions"))],
    [["100", 200], ["102", 201]]);
  // two columns of one name: the first to the first and the second to the second, wherever they moved
  assert.deepEqual([...page.sectionIdMap(secs("Notes", "To do", "Notes"), fresh("To do", "Notes", "Done", "Notes"))],
    [["100", 201], ["101", 200], ["102", 203]]);
  // a second column of one name that is gone: only the first is matched
  assert.deepEqual([...page.sectionIdMap(secs("Notes", "Notes"), fresh("Notes"))], [["100", 200]]);
});

// The map's selection and what a drop means.
const fakeNodes = () => new Map([
  ["PLN-70", { key: "PLN-70", parent: null, kids: ["PLN-71", "PLN-72"] }],
  ["PLN-71", { key: "PLN-71", parent: "PLN-70", kids: ["PLN-73"] }],
  ["PLN-72", { key: "PLN-72", parent: "PLN-70", kids: [] }],
  ["PLN-73", { key: "PLN-73", parent: "PLN-71", kids: [] }],
  ["PLN-12", { key: "PLN-12", parent: null, kids: [] }],
  ["PLN-13", { key: "PLN-13", parent: null, kids: ["PLN-14"] }],
  ["PLN-14", { key: "PLN-14", parent: "PLN-13", kids: [] }],
  ["PLN-15", { key: "PLN-15", parent: null, kids: [] }],
]);
const filedOf = (...keys) => Object.fromEntries(keys.map(k => [k, { from: "", to: "Billing" }]));

test("toggling a story in and out of the selection gives a new set", () => {
  const empty = new Set();
  const one = page.mapToggle(empty, "PLN-01");
  assert.deepEqual([...one], ["PLN-01"]);
  assert.equal(empty.size, 0);
  assert.deepEqual([...page.mapToggle(one, "PLN-01")], []);
  assert.deepEqual([...one], ["PLN-01"]);
});

test("a box gives the shown stories whose centre is inside it, in key order", () => {
  const at = new Map([["PLN-10", { x: 5, y: 5 }], ["PLN-02", { x: 1, y: 1 }], ["PLN-03", { x: 50, y: 50 }], ["PLN-04", { x: 3, y: 3 }]]);
  const shown = new Set(["PLN-10", "PLN-02", "PLN-03"]);
  assert.deepEqual(page.mapBoxed({ x0: 0, y0: 0, x1: 10, y1: 10 }, at, shown), ["PLN-02", "PLN-10"]);
});

test("a box drawn right to left or upward holds the same stories", () => {
  const at = new Map([["PLN-01", { x: 5, y: 5 }]]);
  assert.deepEqual(page.mapBoxed({ x0: 10, y0: 10, x1: 0, y1: 0 }, at, new Set(["PLN-01"])), ["PLN-01"]);
});

test("a story exactly on the box's edge is inside it", () => {
  const at = new Map([["PLN-01", { x: 10, y: 0 }]]);
  assert.deepEqual(page.mapBoxed({ x0: 0, y0: 0, x1: 10, y1: 10 }, at, new Set(["PLN-01"])), ["PLN-01"]);
});

test("a story that is not shown is never boxed", () => {
  const at = new Map([["PLN-01", { x: 5, y: 5 }]]);
  assert.deepEqual(page.mapBoxed({ x0: 0, y0: 0, x1: 10, y1: 10 }, at, new Set()), []);
});

test("the region under a point is the smallest one that holds it", () => {
  const regions = [{ domain: "Outer", x: 0, y: 0, R: 100 }, { domain: "Inner", x: 10, y: 0, R: 20 }, { domain: "", x: 0, y: 0, R: 500 }];
  assert.equal(page.mapRegionAt({ x: 12, y: 0 }, regions), "Inner");
  assert.equal(page.mapRegionAt({ x: -60, y: 0 }, regions), "Outer");
  assert.equal(page.mapRegionAt({ x: 300, y: 0 }, regions), "");
});

test("a point on a region's circle is inside and a point in none gives undefined", () => {
  const regions = [{ domain: "A", x: 0, y: 0, R: 10 }];
  assert.equal(page.mapRegionAt({ x: 10, y: 0 }, regions), "A");
  assert.equal(page.mapRegionAt({ x: 10.5, y: 0 }, regions), undefined);
  assert.equal(page.mapRegionAt({ x: 0, y: 0 }, []), undefined);
});

test("regions of the same size give the first listed", () => {
  const regions = [{ domain: "First", x: 0, y: 0, R: 10 }, { domain: "Second", x: 0, y: 0, R: 10 }];
  assert.equal(page.mapRegionAt({ x: 1, y: 1 }, regions), "First");
});

test("a master and its sub-stories give one filing entry with every descendant counted", () => {
  const nodes = fakeNodes();
  assert.deepEqual(page.mapFiling(["PLN-73", "PLN-72", "PLN-70"], nodes), [{ key: "PLN-70", kids: 3 }]);
  assert.deepEqual(page.mapFiling(["PLN-72", "PLN-71"], nodes), [{ key: "PLN-70", kids: 3 }]);
});

test("a loose story has no kids and entries come in key order", () => {
  assert.deepEqual(page.mapFiling(["PLN-13", "PLN-12", "PLN-12"], fakeNodes()), [{ key: "PLN-12", kids: 0 }, { key: "PLN-13", kids: 1 }]);
});

test("the page says what a drop of one loose story or several did", () => {
  const nodes = fakeNodes(), none = new Map();
  assert.equal(page.mapFiledText({ filed: filedOf("PLN-12"), unchanged: [] }, "Billing", nodes, none), "PLN-12 → Billing");
  assert.equal(page.mapFiledText({ filed: filedOf("PLN-12", "PLN-15"), unchanged: [] }, "Billing", nodes, none), "2 stories → Billing");
});

test("the page says what a drop of one family did", () => {
  const nodes = fakeNodes(), none = new Map();
  assert.equal(page.mapFiledText({ filed: filedOf("PLN-70"), unchanged: [] }, "Billing", nodes, none), "PLN-70 and its 3 sub-stories → Billing");
  assert.equal(page.mapFiledText({ filed: filedOf("PLN-13"), unchanged: [] }, "Billing", nodes, none), "PLN-13 and its sub-story → Billing");
});

test("the page says what a drop of several tiles with a family did", () => {
  const nodes = fakeNodes(), none = new Map();
  assert.equal(page.mapFiledText({ filed: filedOf("PLN-12", "PLN-13", "PLN-15"), unchanged: [] }, "Billing", nodes, none), "3 stories and their sub-stories → Billing");
});

test("a drop on No domain says no domain", () => {
  assert.equal(page.mapFiledText({ filed: filedOf("PLN-12"), unchanged: [] }, null, fakeNodes(), new Map()), "PLN-12 → no domain");
});

test("nothing filed names the one story its title keeps where it is", () => {
  const domains = new Map([["PLN-12", "Admin"], ["PLN-15", "Admin"]]);
  assert.equal(page.mapFiledText({ filed: {}, unchanged: ["PLN-12"] }, null, fakeNodes(), domains), "PLN-12 stays in Admin by its title");
  assert.equal(page.mapFiledText({ filed: {}, unchanged: ["PLN-12", "PLN-15"] }, null, fakeNodes(), domains), "2 stories stay where their titles put them");
});

test("nothing filed and nothing kept by title says there is nothing to move", () => {
  const domains = new Map([["PLN-12", "Admin"]]);
  const nothing = "Nothing to move: they are already there";
  assert.equal(page.mapFiledText({ filed: {}, unchanged: ["PLN-12"] }, "Admin", fakeNodes(), domains), nothing);
  assert.equal(page.mapFiledText({ filed: {}, unchanged: ["PLN-15"] }, null, fakeNodes(), domains), nothing);
});

test("filed stories are followed by the ones their titles keep", () => {
  const domains = new Map([["PLN-12", "Admin"], ["PLN-15", "Admin"], ["PLN-14", ""]]);
  const nodes = fakeNodes();
  assert.equal(page.mapFiledText({ filed: filedOf("PLN-13"), unchanged: ["PLN-12"] }, null, nodes, domains), "PLN-13 and its sub-story → no domain; PLN-12 stays in Admin by its title");
  assert.equal(page.mapFiledText({ filed: filedOf("PLN-13"), unchanged: ["PLN-12", "PLN-15"] }, null, nodes, domains), "PLN-13 and its sub-story → no domain; 2 stories stay where their titles put them");
  assert.equal(page.mapFiledText({ filed: filedOf("PLN-13"), unchanged: ["PLN-14"] }, "Billing", nodes, domains), "PLN-13 and its sub-story → Billing");
});

test("pending domains are sorted and each kept once ignoring case", () => {
  assert.deepEqual(page.mapPending({ "~domains": ["Zed", "billing", "Alpha", "Billing"] }), ["Alpha", "billing", "Zed"]);
  assert.deepEqual(page.mapPending({}), []);
  assert.deepEqual(page.mapPending({ "~domains": "Billing" }), []);
  assert.deepEqual(page.mapPending(null), []);
});

test("saving pending domains keeps the saved positions and never changes its argument", () => {
  const saved = { "PLN-01": [1, 2], "~domains": ["Old"] };
  const out = page.mapWithPending(saved, ["b", "A", "B"]);
  assert.deepEqual(out, { "PLN-01": [1, 2], "~domains": ["A", "b"] });
  assert.deepEqual(saved, { "PLN-01": [1, 2], "~domains": ["Old"] });
  assert.deepEqual(page.mapPending(out), ["A", "b"]);
  assert.deepEqual(page.mapWithPending(saved, []), { "PLN-01": [1, 2] });
  assert.deepEqual(saved["~domains"], ["Old"]);
});

test("a typed domain takes the spelling of the known one it matches", () => {
  assert.equal(page.mapSpelt("billing", ["Billing"]), "Billing");
  assert.equal(page.mapSpelt("Sales", ["Billing"]), "Sales");
});

test("nothing filed and a mix of unchanged stories names only the ones kept by their titles", () => {
  const domains = new Map([["PLN-12", "Admin"]]);
  assert.equal(page.mapFiledText({ filed: {}, unchanged: ["PLN-12", "PLN-15"] }, null, fakeNodes(), domains), "PLN-12 stays in Admin by its title");
});

test("a filed key with no node is a loose story and a missing unchanged list is empty", () => {
  assert.equal(page.mapFiledText({ filed: filedOf("PLN-99") }, "Billing", fakeNodes(), new Map()), "PLN-99 → Billing");
  assert.equal(page.mapFiledText({ filed: {} }, "Billing", fakeNodes(), new Map()), "Nothing to move: they are already there");
});

test("a zero-size box holds only a story centred exactly on it", () => {
  const at = new Map([["PLN-01", { x: 5, y: 5 }], ["PLN-02", { x: 5, y: 6 }]]);
  assert.deepEqual(page.mapBoxed({ x0: 5, y0: 5, x1: 5, y1: 5 }, at, new Set(["PLN-01", "PLN-02"])), ["PLN-01"]);
});

test("a typed domain with no known domains keeps its spelling", () => {
  assert.equal(page.mapSpelt("x", []), "x");
});

test("boxed stories and filing entries come in numeric key order, not lexical", () => {
  const at = new Map([["PLN-10", { x: 1, y: 1 }], ["PLN-2", { x: 1, y: 1 }]]);
  assert.deepEqual(page.mapBoxed({ x0: 0, y0: 0, x1: 2, y1: 2 }, at, new Set(["PLN-10", "PLN-2"])), ["PLN-2", "PLN-10"]);
  const nodes = new Map([["PLN-10", { key: "PLN-10", parent: null, kids: [] }], ["PLN-2", { key: "PLN-2", parent: null, kids: [] }]]);
  assert.deepEqual(page.mapFiling(["PLN-10", "PLN-2"], nodes).map(f => f.key), ["PLN-2", "PLN-10"]);
});

test("pending domains keep only non-blank strings, trimmed", () => {
  const bad = ["A", 5, null, "", "  ", {}, "a", " B "];
  assert.deepEqual(page.mapPending({ "~domains": bad }), ["A", "B"]);
  assert.deepEqual(page.mapPending({ "~domains": [5, "A"] }), ["A"]);
  assert.deepEqual(page.mapWithPending({}, bad), { "~domains": ["A", "B"] });
  assert.deepEqual(page.mapWithPending({ "~domains": ["X"] }, [null, " "]), {});
});

test("a ring of two masters terminates and gives one entry for the ring", () => {
  const nodes = new Map([["PLN-1", { key: "PLN-1", parent: "PLN-2", kids: ["PLN-2"] }], ["PLN-2", { key: "PLN-2", parent: "PLN-1", kids: ["PLN-1"] }]]);
  const out = page.mapFiling(["PLN-1", "PLN-2"], nodes);
  const keys = out.map(f => f.key);
  assert.equal(new Set(keys).size, keys.length);
  assert.ok(out.length >= 1 && out.length <= 2);
  assert.ok(out.every(f => f.kids <= 1));
});

test("a grandchild is counted once under its top master", () => {
  assert.deepEqual(page.mapFiling(["PLN-73", "PLN-71", "PLN-70"], fakeNodes()), [{ key: "PLN-70", kids: 3 }]);
});

test("an undo whose stories all still carry the domain they were filed in can go ahead", () => {
  const filed = { "PLN-70": { from: null, to: "Billing" }, "PLN-73": { from: "Admin", to: "Billing" } };
  assert.deepEqual(page.mapUndoable(filed, new Map([["PLN-70", "Billing"], ["PLN-73", "Billing"]])), []);
});

test("a story re-filed since the drop is named as changed", () => {
  const filed = { "PLN-70": { from: null, to: "Billing" }, "PLN-73": { from: "Admin", to: "Billing" } };
  assert.deepEqual(page.mapUndoable(filed, new Map([["PLN-70", "Payments"], ["PLN-73", "Billing"]])), ["PLN-70"]);
});

test("a filed story the page no longer knows counts as changed", () => {
  const filed = { "PLN-70": { from: null, to: "Billing" }, "PLN-12": { from: null, to: "Billing" } };
  assert.deepEqual(page.mapUndoable(filed, new Map([["PLN-70", "Billing"]])), ["PLN-12"]);
});

test("a domain that differs from the filed one only in case is the same", () => {
  assert.deepEqual(page.mapUndoable({ "PLN-70": { from: null, to: "Billing" } }, new Map([["PLN-70", "billing"]])), []);
});

test("a story filed in no domain still matches when it has no domain of its own", () => {
  assert.deepEqual(page.mapUndoable({ "PLN-70": { from: "Admin", to: null } }, new Map([["PLN-70", null]])), []);
  assert.deepEqual(page.mapUndoable({ "PLN-70": { from: "Admin", to: null } }, new Map([["PLN-70", "Admin"]])), ["PLN-70"]);
});

test("a Shift-click adds a shown story and takes out a selected one, shown or not", () => {
  const shown = new Set(["PLN-71"]);
  assert.deepEqual([...page.mapShiftClick(new Set(), "PLN-71", shown)], ["PLN-71"]);
  assert.deepEqual([...page.mapShiftClick(new Set(["PLN-70", "PLN-71"]), "PLN-70", shown)], ["PLN-71"]);
  assert.deepEqual([...page.mapShiftClick(new Set(["PLN-71"]), "PLN-70", shown)], ["PLN-71"]);
});

test("a drawing keeps only the selected stories it still shows", () => {
  const sel = new Set(["PLN-70", "PLN-71"]);
  assert.deepEqual([...page.mapKeepShown(sel, new Set(["PLN-71", "PLN-72"]))], ["PLN-71"]);
  assert.deepEqual([...sel], ["PLN-70", "PLN-71"]);
});


test("a files reply from a new server names the two boards", () => {
  assert.deepEqual(page.boardNames({ build: "1.3.0", boards: { build: "build.md", plan: "plan.md" } }), NEW_NAMES);
});

test("a files reply from an older server, or a partial one, means plans.md and backlog.md", () => {
  assert.deepEqual(page.boardNames({ build: "1.2.0", files: [] }), OLD_NAMES);
  assert.deepEqual(page.boardNames({ boards: { build: "build.md" } }), OLD_NAMES);
  assert.deepEqual(page.boardNames(null), OLD_NAMES);
});

test("a board's label is Build, Plan or Archive by the store's own file names", () => {
  for (const names of [OLD_NAMES, NEW_NAMES]) {
    assert.equal(page.boardLabel(names.build, names), "Build");
    assert.equal(page.boardLabel(names.plan, names), "Plan");
    assert.equal(page.boardLabel("archive/archived.md", names), "Archive");
    assert.equal(page.boardLabel("notes/some-ideas.md", names), "Some ideas");
  }
});

test("ownName maps either spelling of a board onto the store's own and leaves other names", () => {
  assert.equal(page.ownName("plans.md", NEW_NAMES), "build.md");
  assert.equal(page.ownName("build.md", OLD_NAMES), "plans.md");
  assert.equal(page.ownName("backlog.md", NEW_NAMES), "plan.md");
  assert.equal(page.ownName("plan.md", OLD_NAMES), "backlog.md");
  assert.equal(page.ownName("archive/archived.md", NEW_NAMES), "archive/archived.md");
  assert.equal(page.ownName("active/plans.md", NEW_NAMES), "active/plans.md");
});

test("an old-named file left beside the store's present board is not offered", () => {
  assert.deepEqual(page.pickable(["plans.md", "build.md", "backlog.md", "plan.md", "x.md"], NEW_NAMES), ["build.md", "plan.md", "x.md"]);
  assert.deepEqual(page.pickable(["plans.md", "backlog.md"], OLD_NAMES), ["plans.md", "backlog.md"]);
  assert.deepEqual(page.pickable(["plans.md", "plan.md"], { build: "build.md", plan: "plan.md" }), ["plan.md"]);
});

test("the header's files run Plan, Build, Archive, then the others alphabetically", () => {
  const files = ["zeta.md", "archive/archived.md", "build.md", "alpha.md", "plan.md", "plans.md"];
  assert.deepEqual(page.boardOrder(files, NEW_NAMES), ["plan.md", "build.md", "archive/archived.md", "alpha.md", "zeta.md"]);
});

test("the harness's boards open under the new names when told them", () => {
  page.setNames(NEW_NAMES);
  try {
    page.open("build.md", PLANS, {});
    assert.equal(page.pollsFor("build.md", false, false).poll, true);
    assert.equal(page.pollsFor("plans.md", false, false).poll, false);
    assert.equal(page.pickupHintShows("build.md", "In flight", "why", ""), true);
    assert.equal(page.whereLabel({ file: "plan.md", column: "Feature" }), "Plan");
    assert.equal(page.whereLabel({ file: "backlog.md", column: "Feature" }), "Feature");
  } finally { page.setNames(NEW_NAMES); }
  assert.equal(page.pollsFor("build.md", false, false).poll, true);
});

test("a store on the old names with its own plan.md keeps that file as a file of its own", () => {
  const files = ["plans.md", "backlog.md", "plan.md", "archive/archived.md"];
  assert.equal(page.boardLabel("plan.md", OLD_NAMES), "plan.md");
  assert.equal(page.boardLabel("plan.md", NEW_NAMES), "Plan");
  assert.equal(page.ownName("plan.md", OLD_NAMES, files), "plan.md");
  assert.deepEqual(page.pickable(files, OLD_NAMES), files);
  const labels = page.boardOrder(files, OLD_NAMES).map(f => page.boardLabel(f, OLD_NAMES));
  assert.deepEqual(labels, ["Plan", "Build", "Archive", "plan.md"]);
  assert.equal(labels.filter(l => l === "Plan").length, 1);
});

test("a stray old file on a new-names store still maps onto the present board", () => {
  const files = ["build.md", "plan.md", "plans.md", "backlog.md"];
  assert.equal(page.ownName("plans.md", NEW_NAMES, files), "build.md");
  assert.equal(page.ownName("backlog.md", NEW_NAMES, files), "plan.md");
  assert.deepEqual(page.pickable(files, NEW_NAMES), ["build.md", "plan.md"]);
});

test("the map puts a story on the plan board under backlog and an unstarted build one under planned, on the new names", () => {
  page.setNames(NEW_NAMES);
  try {
    const index = new Map([
      ["A-1", { key: "A-1", title: "Idea", file: "plan.md", column: "Feature", done: false, parent: null, mentions: [] }],
      ["A-2", { key: "A-2", title: "Queued", file: "build.md", column: "To do", done: false, parent: null, mentions: [] }],
    ]);
    const g = page.mapGraph(index, new Map(), []);
    assert.equal(g.nodes.get("A-1").where, "backlog");
    assert.equal(g.nodes.get("A-2").where, "planned");
  } finally { page.setNames(NEW_NAMES); }
});

test("a board's label asked for with no names uses the page's current ones", () => {
  page.setNames(OLD_NAMES);
  try { assert.equal(page.boardLabel("plans.md"), "Build"); } finally { page.setNames(NEW_NAMES); }
  page.setNames(NEW_NAMES);
  try {
    assert.equal(page.boardLabel("build.md"), "Build");
    assert.equal(page.boardLabel("plan.md"), "Plan");
  } finally { page.setNames(NEW_NAMES); }
});

test("a move that waits says what for, and the note can be hidden", () => {
  const waiting = { waiting: "These boards will be renamed Plan and Build once PLN-41's session finishes its turn." };
  assert.deepEqual(page.renamingNotice(waiting), { text: waiting.waiting, close: true });
  const html = page.renamingHTML(waiting, "");
  assert.match(html, /once PLN-41's session finishes its turn\./);
  assert.match(html, /data-act="hiderenaming"/);
  assert.equal(page.renamingHTML(waiting, waiting.waiting), "");  // hidden in this browser: that very sentence
  assert.notEqual(page.renamingHTML({ waiting: "These boards will be renamed Plan and Build once PLN-42's session finishes its turn." }, waiting.waiting), "");
});

test("a stray old file's notice names it and cannot be hidden", () => {
  const stray = { stray: ["plans.md"], text: "plans.md is back in this store, but the board is build.md now. Move anything new into build.md and delete plans.md." };
  assert.deepEqual(page.renamingNotice(stray), { text: stray.text, close: false });
  const html = page.renamingHTML(stray, stray.text);
  assert.match(html, /plans\.md is back in this store, but the board is build\.md now\./);
  assert.doesNotMatch(html, /<button/);
});

test("a store with nothing to say about the move shows no notice", () => {
  for (const r of [null, undefined, {}]) {
    assert.equal(page.renamingNotice(r), null);
    assert.equal(page.renamingHTML(r, ""), "");
  }
});

test("the page sees when the server's board names are no longer its own", () => {
  assert.equal(page.namesMoved({ build: "build.md", plan: "plan.md" }, OLD_NAMES), true);
  assert.equal(page.namesMoved({ build: "build.md", plan: "backlog.md" }, OLD_NAMES), true);
  assert.equal(page.namesMoved({ build: "plans.md", plan: "backlog.md" }, OLD_NAMES), false);
  assert.equal(page.namesMoved(undefined, OLD_NAMES), false);  // an older server says nothing
});

test("a store on the old names with its own build.md keeps that file as a file of its own", () => {
  const files = ["plans.md", "backlog.md", "build.md", "archive/archived.md"];
  assert.equal(page.ownName("build.md", OLD_NAMES, files), "build.md");
  assert.equal(page.ownName("plans.md", OLD_NAMES, files), "plans.md");
  assert.deepEqual(page.pickable(files, OLD_NAMES), files);
  assert.deepEqual(page.boardOrder(files, OLD_NAMES).map(f => page.boardLabel(f, OLD_NAMES)), ["Plan", "Build", "Archive", "build.md"]);
});

const { describe, beforeEach, afterEach } = test;

describe("OldNames", () => {
  const PLANS_OLD = PLANS;
  const SERVER_OLD = Object.fromEntries(Object.entries(SERVER).map(([k, t]) => [k, { ...t, file: t.file === "build.md" ? "plans.md" : t.file === "plan.md" ? "backlog.md" : t.file }]));
  beforeEach(() => page.setNames(OLD_NAMES));
  afterEach(() => page.setNames(NEW_NAMES));

  test("the index of a store on the old names says plans.md and backlog.md for the files", () => {
    page.open("plans.md", PLANS_OLD, SERVER_OLD);
    const index = page.tileIndex();
    assert.equal(index.get("PLN-74").file, "plans.md");
    assert.equal(index.get("PLN-81").file, "backlog.md");
  });

  test("the In flight column says why pickup is off on plans.md only, on the old names", () => {
    const why = 'Pickup is off for this store: set "pickup": true in its board.json to turn it on.';
    assert.equal(page.pickupHintShows("plans.md", "In flight", why, ""), true);
    assert.equal(page.pickupHintShows("plans.md", "To do", why, ""), false);
    assert.equal(page.pickupHintShows("backlog.md", "In flight", why, ""), false);
  });

  test("story messages run on plans.md and the drafts on backlog.md, on the old names", () => {
    assert.deepEqual(page.pollsFor("plans.md", false, false), { poll: true, stories: true });
    assert.deepEqual(page.pollsFor("backlog.md", false, true), { poll: true, stories: false });
    assert.deepEqual(page.pollsFor("backlog.md", true, false), { poll: true, stories: true });
    assert.deepEqual(page.pollsFor("backlog.md", false, false), { poll: false, stories: false });
  });

  test("the page labels the two old files Plan and Build and treats plans.md as the pickup board", () => {
    assert.equal(page.boardLabel("plans.md"), "Build");
    assert.equal(page.boardLabel("backlog.md"), "Plan");
    assert.equal(page.whereLabel({ file: "backlog.md", column: "Feature" }), "Plan");
    page.open("plans.md", PLANS_OLD, {});
    assert.equal(page.isPickupColumn("In flight"), true);
  });

  test("each family in tile_cases.json reads the same on the old names", () => {
    const old = n => n === "build.md" ? "plans.md" : n === "plan.md" ? "backlog.md" : n;
    for (const c of CASES.blocked) {
      const others = {};
      for (const [name, text] of Object.entries(c.files)) {
        if (name === "build.md") continue;
        page.open(old(name), text);
        for (const [k, t] of page.tileIndex()) others[k] = t;
      }
      page.open("plans.md", c.files["build.md"], others);
      const index = page.tileIndex();
      for (const [key, want] of Object.entries(c.expect)) {
        assert.deepEqual(page.blocking(key, index).map(b => b.key), want.blocking, `${c.name}: ${key} blocking`);
        assert.deepEqual(page.decisionsFor(key, index).map(d => d.key), want.decisions, `${c.name}: ${key} decisions`);
      }
    }
  });
});

// Organize: the agent's proposal, reviewed on the map before anything is filed.
const PROPOSAL = { state: "ready", left: ["PLN-90", "PLN-91"], moves: [
  { key: "PLN-72", title: "Pay <by> card", domain: "Payments", new: true },
  { key: "PLN-81", title: "Club invites", domain: "Clubs", new: false },
  { key: "PLN-80", title: "Refunds", domain: "Payments", new: true },
] };

test("the review panel lists a proposal grouped by domain, a new domain marked and every row ticked", () => {
  const html = page.organizeReviewHTML(PROPOSAL);
  const pay = html.indexOf(">Payments<"), clubs = html.indexOf(">Clubs<");
  assert.ok(pay >= 0 && clubs > pay, html);
  assert.ok(html.indexOf('data-org="PLN-80"') < clubs, "PLN-80 is under Payments with PLN-72");
  assert.match(html, />Payments<span class="org-new">new<\/span>/);
  assert.doesNotMatch(html, />Clubs<span class="org-new">/);
  assert.equal((html.match(/type="checkbox"/g) || []).length, 3);
  assert.equal((html.match(/ checked/g) || []).length, 3);
  assert.match(html, /PLN-72<\/span> Pay &lt;by&gt; card/);
  assert.match(html, /left as they are: PLN-90, PLN-91/);
  assert.doesNotMatch(page.organizeReviewHTML({ ...PROPOSAL, left: [] }), /left as they are/);
});

test("Apply sends the domain of each ticked row, and what each story's domain was when it was proposed", () => {
  const org = { ...PROPOSAL, moves: PROPOSAL.moves.map(m => ({ ...m, from: m.key === "PLN-81" ? "Social" : null })) };
  assert.deepEqual(page.organizeSend(org, new Set(["PLN-72", "PLN-81"])),
    { set: { "PLN-72": "Payments", "PLN-81": "Clubs" }, expect: { "PLN-72": null, "PLN-81": "Social" } });
  assert.deepEqual(page.organizeSend(org, new Set(["PLN-99"])), { set: {}, expect: {} });
});

test("a changed story with no row of its own asks for a fresh proposal", () => {
  assert.equal(page.organizeChangedText(["PLN-70"], ["PLN-72"]),
    "PLN-70 has changed since the proposal, so nothing was filed. Ask again for a proposal that fits the boards as they are now.");
  assert.match(page.organizeChangedText(["PLN-72"], ["PLN-72"]), /Its row is unticked/);
});

test("stories changed since the proposal are said, and their rows come back unticked", () => {
  assert.equal(page.organizeChangedText(["PLN-72"]), "PLN-72 has changed since the proposal, so nothing was filed. Its row is unticked: Apply again to file the rest.");
  assert.equal(page.organizeChangedText(["PLN-80", "PLN-72"]), "PLN-72 and PLN-80 have changed since the proposal, so nothing was filed. Their rows are unticked: Apply again to file the rest.");
  const html = page.organizeReviewHTML(PROPOSAL, new Set(["PLN-80"]));
  assert.equal((html.match(/ checked/g) || []).length, 2);
  assert.match(html, /data-org="PLN-80">/);
});

test("what Apply says names one story, one domain or how many of each", () => {
  assert.equal(page.organizeFiledText({ "PLN-72": { from: null, to: "Payments" } }), "PLN-72 → Payments");
  assert.equal(page.organizeFiledText({ "PLN-72": { from: null, to: "Payments" }, "PLN-80": { from: null, to: "Payments" } }), "2 stories → Payments");
  assert.equal(page.organizeFiledText({ "PLN-72": { from: null, to: "Payments" }, "PLN-81": { from: "X", to: "Clubs" } }), "2 stories sorted into 2 domains");
});

test("the Combine button shows once two or more stories are selected", () => {
  assert.equal(page.combineShown([]), false);
  assert.equal(page.combineShown(["PLN-72"]), false);
  assert.equal(page.combineShown(["PLN-72", "PLN-73"]), true);
});

test("the combine dialog opens with a Title field filled with the kept story's title", () => {
  const index = new Map([["PLN-72", { title: 'Main "&" more' }], ["PLN-73", { title: "Other" }]]);
  const html = page.combineTitleHTML("PLN-72", index);
  assert.match(html, /<input id="combineTitle"[^>]*maxlength="120"[^>]*value="Main &quot;&amp;&quot; more"/);
  assert.match(html, /<label for="combineTitle"><b>Title/);
  assert.match(html, /id="combineNote"/);
  assert.match(html, /<button type="button" id="combineUse" hidden>/);
  assert.match(page.combineTitleHTML("PLN-73", index), /value="Other"/);
});

test("a suggestion fills the title field only when untouched, else offers to use it", () => {
  assert.deepEqual(page.suggestionFor({ title: "Merged" }, false, "Claude Code"), { fill: "Merged", note: "Suggested by Claude Code", offer: null });
  assert.deepEqual(page.suggestionFor({ title: "Merged" }, true, "Claude Code"), { fill: null, note: "Suggested by Claude Code", offer: "Merged" });
  assert.deepEqual(page.suggestionFor({ title: null, why: "No suggestion: it took too long." }, false, "Copilot CLI"), { fill: null, note: "No suggestion: it took too long.", offer: null });
  assert.deepEqual(page.suggestionFor(null, true, "Copilot CLI"), { fill: null, note: "No suggestion: the agent didn't answer.", offer: null });
  assert.equal(page.suggestionFor({ title: "  " }, false, "x").fill, null);
});

test("Enter in the Title field presses Combine unless an IME is composing", () => {
  assert.equal(page.enterCommits({ key: "Enter", isComposing: false, keyCode: 13 }), true);
  assert.equal(page.enterCommits({ key: "Enter", isComposing: true, keyCode: 13 }), false);
  assert.equal(page.enterCommits({ key: "Enter", isComposing: false, keyCode: 229 }), false);
  assert.equal(page.enterCommits({ key: "a", isComposing: false, keyCode: 65 }), false);
});

test("switching the kept story changes the Title field only while nothing has typed or filled it", () => {
  assert.equal(page.switchFills(false, false), true);
  assert.equal(page.switchFills(true, false), false);
  assert.equal(page.switchFills(false, true), false);
});

test("the combine dialog lists the stories, ticks the first as the one that stays and says what happens", () => {
  const index = new Map([["PLN-72", { title: "Main & more" }], ["PLN-73", { title: "Other" }]]);
  const [lead, rows, said] = page.combineBody(["PLN-72", "PLN-73"], "PLN-72", index);
  assert.match(lead, /2 stories/);
  assert.equal([...rows.matchAll(/<input type="radio" name="combineInto" value="(PLN-\d+)"( checked)?>/g)].map(m => m[1] + (m[2] || "")).join(), "PLN-72 checked,PLN-73");
  assert.match(rows, /Main &amp; more/);
  assert.match(said, /archive/);
  assert.match(said, /no Undo/i);
  assert.match(page.combineBody(["PLN-72", "PLN-73"], "PLN-73", index)[1], /value="PLN-73" checked/);
});

test("the combine dialog offers to delete the others, and warns when one of them holds the highest key", () => {
  const index = new Map([["PLN-72", { title: "Main" }], ["PLN-73", { title: "Other" }]]);
  assert.match(page.combineBody(["PLN-72", "PLN-73"], "PLN-72", index)[3], /<input type="checkbox" id="combineDelete">.*Delete the others instead of archiving them/);
  assert.equal(page.combineDeleteNote(["PLN-73"], 73), "PLN-73 has the highest key, so a new story may be given it again.");
  assert.equal(page.combineDeleteNote(["PLN-73"], 90), "");
});

// A split run as /api/pickup tells it, ready to review.
const SPLIT_RUN = { state: "ready", key: "PLN-72", title: "Checkout", rev: "abc123", note: null, stays: ["receipts"],
  stories: [{ title: "Card form", description: "Build the <card> form.", tasks: [{ text: "card form", done: false, moved: true }, { text: "Validate the expiry", done: false, moved: false }] },
            { title: "Refunds", description: "", tasks: [{ text: "refund API", done: true, moved: true }] }] };

test("the card's Split button says where a run for its story stands", () => {
  assert.equal(page.splitLabel(null, "PLN-72"), "Split…");
  assert.equal(page.splitLabel({ ...SPLIT_RUN, state: "working" }, "PLN-72"), "Splitting…");
  assert.equal(page.splitLabel(SPLIT_RUN, "PLN-72"), "Review split…");
  assert.equal(page.splitLabel({ ...SPLIT_RUN, state: "need" }, "PLN-72"), "Split didn't finish…");
  assert.equal(page.splitLabel(SPLIT_RUN, "PLN-80"), "Split…");  // another story's run
});

test("the split review lists each sub-story ticked with its title and description to edit, its sub-tasks and what stays", () => {
  const html = page.splitReviewHTML(SPLIT_RUN);
  assert.equal((html.match(/data-split-on="\d" checked/g) || []).length, 2);
  assert.match(html, /<input type="text" data-split-title="0" value="Card form"/);
  assert.match(html, /<textarea data-split-desc="0"[^>]*>Build the &lt;card&gt; form\.<\/textarea>/);
  assert.match(html, /<li class="">☐ card form <em class="split-moved">moved from the story<\/em><\/li>/);
  assert.match(html, /<li class="">☐ Validate the expiry<\/li>/);
  assert.match(html, /<li class="done">☑ refund API/);
  assert.match(html, /Stays on PLN-72: receipts/);
  assert.match(page.splitReviewHTML(SPLIT_RUN, new Set([1])), /class="split-row off" data-split-row="1"><div class="split-head"><input type="checkbox" data-split-on="1" aria-label/);
  assert.doesNotMatch(page.splitReviewHTML({ ...SPLIT_RUN, stays: [] }), /Stays on/);
});

test("Create sends the ticked sub-stories with their edited titles and descriptions, the proposal's sub-tasks and the story's rev", () => {
  const body = page.splitSend(SPLIT_RUN, [{ on: true, title: "  Card form and checks ", description: " Take a card. " }, { on: false, title: "Refunds", description: "" }]);
  assert.deepEqual(body, { key: "PLN-72", expect: "abc123", stories: [{ title: "Card form and checks", description: "Take a card.",
    tasks: [{ text: "card form", done: false }, { text: "Validate the expiry", done: false }] }] });
  const all = page.splitSend(SPLIT_RUN, [{ on: true }, { on: true, title: "Money back", description: "Give it back." }]);
  assert.deepEqual(all.stories.map(s => [s.title, s.description]), [["Card form", "Build the <card> form."], ["Money back", "Give it back."]]);
  assert.deepEqual(all.stories[1].tasks, [{ text: "refund API", done: true }]);
  assert.equal(page.splitCreatedText({ into: "PLN-72", created: ["PLN-90", "PLN-91", "PLN-92"] }), "PLN-72 split into PLN-90, PLN-91 and PLN-92.");
});

test("Split is offered only on a story the server would split: keyed, not a sub-story, with no session or job", () => {
  page.open("build.md", PLANS, SERVER);
  assert.equal(page.splitOffered("PLN-70", "PLN-70 **Dark mode.** The master story.", []), true);
  assert.equal(page.splitOffered(null, "**No key.**", []), false);
  assert.equal(page.splitOffered("PLN-75", "PLN-75 **Theme auth.**\n  Part of PLN-70.", []), false);
  assert.equal(page.splitOffered("PLN-70", "PLN-70 **Dark mode.**\n  Claude session `ab12cd34`: `claude --resume ab12cd34`.", []), false);
  for (const state of ["waiting", "paused", "started", "idle", "lost"])
    assert.equal(page.splitOffered("PLN-70", "PLN-70 **Dark mode.**", [{ key: "PLN-70", state }]), false, state);
  assert.equal(page.splitOffered("PLN-70", "PLN-70 **Dark mode.**", [{ key: "PLN-70", state: "done" }, { key: "PLN-71", state: "started" }]), true);
});
