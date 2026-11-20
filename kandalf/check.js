#!/usr/bin/env node
// check.js: does a board file round-trip through the board's own parser?
//
// The board opens a file view-only when parse → serialize doesn't give back
// the same text, blank lines aside (board.html, readsAsBoard), and it doesn't
// say why. A hand edit trips that easily: a blank line between two
// cards, a ### heading, a code block. This runs the board's real parse and
// serialize, lifted out of board.html by name so they can never drift, and
// names the first line that differs. It also fails a board with two In flight
// columns or two Done columns, under whatever names (board.html, doubledKinds).
//
//   node kandalf/check.js plans.md [archive/archived.md ...]
//
// Exit 0: every file round-trips. Exit 1: one doesn't, or has two of a column (details on stderr).
// Exit 0 with a note if board.html's functions can't be found, so a board
// redesign never blocks an edit.

const fs = require("fs");
const path = require("path");

const html = fs.readFileSync(path.join(__dirname, "board.html"), "utf8");
const start = html.indexOf("function parse(");
const end = html.indexOf("\nconst esc", start);
if (start < 0 || end < 0) {
  console.error("check.js: parse/serialize not found in board.html; skipping the check");
  process.exit(0);
}
// parse() hands out card ids from nextId, a board.html global.
const { parse, serialize, doubledKinds, readsAsBoard } = new Function(
  `let nextId = 1;\n${html.slice(start, end)}\nreturn { parse, serialize, doubledKinds, readsAsBoard };`
)();

let failed = false;
for (const file of process.argv.slice(2)) {
  const text = fs.readFileSync(file, "utf8").replace(/\r\n/g, "\n").replace(/\n*$/, "\n");
  const doc = parse(text), out = serialize(doc);
  const doubled = doubledKinds(doc.sections);
  for (const [kind, names] of doubled) {
    console.error(`${file} has ${names.length} ${kind} columns: ${names.join(", ")}. A board has one ${kind} column: ` +
      `move the tiles into one of them and delete or rename the others.`);
  }
  if (doubled.length) failed = true;
  if (readsAsBoard(text, doc)) continue;  // the same but for blank lines: the board puts those right
  failed = true;
  // The first line that differs, blank lines aside (the board puts those right itself).
  const lines = t => t.split("\n").map((line, n) => [line, n + 1]).filter(([line]) => line.trim());
  const a = lines(text), b = lines(out);
  let i = 0;
  while (i < a.length && i < b.length && a[i][0] === b[i][0]) i++;
  const show = s => (s === undefined ? "(end of file)" : s.slice(0, 100));
  console.error(
    `${file} would open view-only on the board: it doesn't round-trip through the board's parser.\n` +
    `  first difference at line ${a[i] ? a[i][1] : "(end of file)"}:\n` +
    `    file has:   ${show(a[i]?.[0])}\n` +
    `    board says: ${show(b[i]?.[0])}\n` +
    `  Usual causes: prose outside a card, a ### heading, or a code block.`
  );
}
process.exit(failed ? 1 : 0);
