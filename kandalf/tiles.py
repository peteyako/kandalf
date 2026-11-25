"""The tile reader: what a board file's tiles are, what their lines say, and the edits that
keep a board's layout.

This is the one Python module that knows board-markdown grammar: no other module reads a tile's lines itself. board.html reads the same
wording (`parse`, `splitCard`, `tileFacts` there), so each rule that is written twice says
"same in board.html" and names its twin, and a change to one is a change to the other. tile_cases.json
holds both readers to the same cases (test_tiles.TileCases, test_page.js).

    parse(store, text)      every tile of a board file, as Tile
    model(md), base(md), tests(md), repos(md), sessions(md), ready(md), decision(md), options(md), opted_out(md)
                            what one attribute line of a tile says
    is_pickup_column, is_decision_column
                            whether a column's name says it is the pickup column or a decision column
    session(md)             the tile's session line, as SessionLine
    take, without, insert_after, with_session, with_status, with_decision, with_options, with_domain, with_key
                            edits that return the board's text
    domain_name(raw)        a domain's name as it will be written, or ValueError saying why not
    Snapshot                the build and plan boards and the archive log read once: the tile index, the
                            texts, the In flight tiles, a tile's lineage, the nearest line
                            of a kind up its family, what still blocks a tile and the
                            decisions made for it
"""

import re
from dataclasses import dataclass, field
from functools import lru_cache

from . import store as stores

ARCHIVE_LOG = stores.ARCHIVE_LOG  # the archive log's path in a store

# The columns that mean under way, however they are spelt: In flight, in-flight, In progress (or a slip
# of it, "In progrees"), Doing, WIP, Working, Underway, Ongoing, Active; never one that says "not".
IN_FLIGHT_RE = re.compile(r"^(?!.*\bnot\b).*\b(?:in[\s_-]*flight|progre\w*|doing|wip|working|under[\s_-]*way|ongoing|active)\b", re.I)  # same in board.html (IN_FLIGHT_RE there)
DONE_COL_RE = re.compile(r"^(?!.*\bnot\b).*\b(?:done|shipped|completed?|finished|closed|merged|released|deployed|delivered|resolved)\b", re.I)  # same in board.html (DONE_COL_RE there): Done, Shipped, Merged...; never "Not done"
# The columns that hold decisions to make: Decisions, Design decision, Decide; never "Not a decision".
DECISION_COL_RE = re.compile(r"^(?!.*\bnot\b).*\b(?:decisions?|decide)\b", re.I)  # same in board.html (DECISION_COL_RE there)
TILE_HEAD_RE = re.compile(r"^(?:P[1-4] )?(?:~\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)?[hdw] )?(.*)$")  # priority and estimate before a title

OPT_OUT_RE = re.compile(r"\bno pickup\b", re.I)
# A tile says its story is finished and waiting for review with a line of its own that
# starts "Ready for review" (the session writes "**Ready for review:** <gate results>").
# board.html reads the same wording (READY_RE there) and outlines the tile.
READY_RE = re.compile(r"^\s+(?:\*\*)?Ready for review\b[:.]?(?:\*\*)?[:.]?[ \t]*(.*)$", re.I | re.M)
REPOS_RE = re.compile(r"^[ \t]+repos?:[ \t]*(\S.*?)\.?[ \t]*$", re.I | re.M)  # same in board.html (REPOS_RE there)
# A tile picks its own model with a line of its own, "Model: sonnet" (an alias or a full model id).
MODEL_RE = re.compile(r"^[ \t]+model:[ \t]*`?([A-Za-z0-9][A-Za-z0-9._\[\]-]*)`?\.?[ \t]*$", re.I | re.M)  # same in board.html (MODEL_RE there)
# A tile picks the branch its story is cut from and merged into with a line of its own, "Base: feature-x".
BASE_RE = re.compile(r"^[ \t]+base:[ \t]*`?([A-Za-z0-9_][A-Za-z0-9._/-]*?)`?\.?[ \t]*$", re.I | re.M)  # same in board.html (BASE_RE there)
# A tile says how much of the suite its merge runs with a line of its own, "Tests: full" or "Tests: affected".
TESTS_RE = re.compile(r"^[ \t]+tests:[ \t]*`?(full|affected)`?\.?[ \t]*$", re.I | re.M)  # same in board.html (TESTS_RE there)
# "Domain: Admin": the part of the product a story is in, which the story map groups by.
DOMAIN_RE = re.compile(r"^[ \t]+domain:[ \t]*`?([A-Za-z0-9][^`\n]{0,38}?)`?\.?[ \t]*$", re.I | re.M)  # same in board.html (DOMAIN_RE there)
SESSIONS_RE = re.compile(r"^[ \t]+sessions:[ \t]*`?(\d+)`?\.?[ \t]*$", re.I | re.M)  # a master's "Sessions: 3" line, same in board.html (SESSIONS_RE there)

# The session line the server writes into a tile, an indented line of its own (SESSION_RE in
# board.html). A description that quotes "Claude session `abc`" is not one.
SESSION_LINE_RE = re.compile(r"^[ \t]+(Claude|Copilot|Antigravity) session `([0-9a-f]{6,})`: `[^`]+`", re.M)  # same in board.html (SESSION_RE)
SESSION_BRANCH_RE = re.compile(r"Branch `([^`]+)`, worktree `([^`]+)`")
SESSION_REPOS_RE = re.compile(r"Branch `([^`]+)`, worktrees ([^`,]+? `[^`]+`(?:, [^`,]+? `[^`]+`)*)\.")
SESSION_ANY_BRANCH_RE = re.compile(r"Branch `([^`]+)`")
STATUS_LINE_RE = re.compile(r"^[ \t]+\*\*Status \d{4}-\d\d-\d\d \d\d:\d\d:\*\*(?:[ \t]|$)")  # same in board.html (STATUS_RE there)
# A decision answered in place: an indented "**Decision 2026-11-23:** text" line. The last one is the answer.
DECISION_LINE_RE = re.compile(r"^[ \t]+\*\*Decision (\d{4}-\d\d-\d\d):\*\*(?:[ \t]+(.*))?$", re.M)  # same in board.html (DECISION_RE there)


@dataclass(frozen=True)
class Tile:
    """One tile of a board file. start and end are its line range, without the blank lines
    after it; text is those lines. parent is the first master its text says it is part of
    that is not itself, links the keys it says it moves with, mentions the other keys its
    text names (sorted), blockers the keys it says it is blocked by (in the order written, each
    once), done whether it is in a Done column or on the archive log."""
    key: str | None
    column: str | None
    start: int
    end: int
    text: str
    title: str
    parent: str | None
    links: list = field(default_factory=list)
    mentions: list = field(default_factory=list)
    blockers: list = field(default_factory=list)
    done: bool = False


@dataclass(frozen=True)
class SessionLine:
    """What a tile's session line says: the agent's name as written ("Claude"), the session's
    id, its branch (None if the line names none) and its cuts, [(repo name, worktree label)]:
    one per repo in the several-worktrees form, one with no repo name (None) in the old form."""
    agent: str
    id: str
    branch: str | None
    cuts: list


# A tile's options block: an indented "**Options 2026-11-23:**" line (recommends, research link, the
# note it was asked with) and the option lines directly under it.
OPTIONS_LINE_RE = re.compile(r"^[ \t]+\*\*Options (\d{4}-\d\d-\d\d):\*\*(?:[ \t]+(.*))?$")  # same in board.html (OPTIONS_RE there)
OPTION_ITEM_RE = re.compile(r"^[ \t]+[-*] \*\*([A-H])\. (.+?)\*\*(?:[ \t]+(.*))?$")  # same in board.html (OPTION_RE there)
_OPTIONS_RECOMMENDS_RE = re.compile(r"\brecommends ([A-H])\.")
_OPTIONS_DOC_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_OPTIONS_ASKED_RE = re.compile(r"Asked: (.*)$")


def _options_spans(lines: list) -> list:
    """The (start, end) line ranges of the options blocks in a list of lines: an Options line and
    the option lines directly under it. Any other line ends a block. Same in board.html
    (optionsSpans there)."""
    spans, i = [], 0
    while i < len(lines):
        if OPTIONS_LINE_RE.match(lines[i]):
            end = i + 1
            while end < len(lines) and OPTION_ITEM_RE.match(lines[end]):
                end += 1
            spans.append((i, end))
            i = end
        else:
            i += 1
    return spans


def _without_options(md: str) -> str:
    """The tile's markdown without the lines of its options block, which quote other tiles' words
    (same in board.html: withoutOptions)."""
    lines = md.split("\n")
    drop = {i for a, b in _options_spans(lines) for i in range(a, b)}
    return "\n".join(x for i, x in enumerate(lines) if i not in drop)


@lru_cache(maxsize=None)
def _patterns(key: str) -> tuple:
    k = re.escape(key)
    head = re.compile(rf"^[-*] (?:\[[ xX]\] )?({k}-\d+) ")  # the whole key opening a tile line
    keys = re.compile(rf"\b{k}-(\d+)\b")  # a key anywhere in a line
    keyed = re.compile(rf"^({k}-\d+) ")  # the key a tile's text (after "- [ ] ") opens with
    # Linked tiles: "Moves with PLN-74" (or "PLN-74, PLN-75 and PLN-76") moves with those tiles.
    link = re.compile(rf"\bmoves with\s+({k}-\d+(?:(?:\s*,\s*(?:and\s+)?|\s+and\s+){k}-\d+)*)", re.I)  # same in board.html (LINK_RE there)
    # Master tiles: "Part of PLN-70" makes a tile a sub-story of PLN-70. Only the first counts.
    part = re.compile(rf"\bpart of\s+({k}-\d+)\b", re.I)  # same in board.html (PART_RE there)
    number = re.compile(rf"^[-*] (?:\[[ xX]\] )?{k}-(\d+) ", re.M)  # the number of a key opening a tile line, anywhere in a file
    # Blocked tiles: "Blocked by PLN-30" (or "PLN-30, PLN-31 and PLN-32") waits on those tiles.
    blocked = re.compile(rf"\bblocked by\s+({k}-\d+(?:(?:\s*,\s*(?:and\s+)?|\s+and\s+){k}-\d+)*)", re.I)  # same in board.html (BLOCKED_RE there)
    return head, keys, keyed, link, part, number, blocked


def _title(store, md: str) -> str:
    """A tile's title, plain: its first line without the checkbox, key, priority, estimate
    and bold (the page's splitCard)."""
    keyed = _patterns(store.key)[2]
    line = re.sub(r"^[-*] (?:\[[ xX]\] )?", "", md.split("\n", 1)[0])
    rest = TILE_HEAD_RE.match(keyed.sub("", line)).group(1)
    bold = re.match(r"^\*\*(.+?)\*\*", rest)
    title = re.sub(r"[.:]$", "", bold.group(1)) if bold else rest
    return re.sub(r"\*\*|`", "", title)


def parse(store, text: str, *, file: str = "") -> list:
    """Every tile of a board file, in order. A tile line starts "- " or "* ", and its key is
    read the same way in both. file is the board's path in the store (by default a board that
    is not the archive log): the archive log's tiles are done."""
    head, keys, _, link, part, _, blocked = _patterns(store.key)
    lines, out, column, i = text.replace("\r\n", "\n").split("\n"), [], None, 0  # a CRLF file reads as the page reads it
    while i < len(lines):
        line = lines[i]
        if line.startswith("## "):
            column = line[3:].strip()
        elif column is not None and re.match(r"^[-*] ", line):
            end = i + 1
            while end < len(lines) and (not lines[end].strip() or lines[end][0] in " \t"):
                end += 1
            while not lines[end - 1].strip():
                end -= 1
            md = "\n".join(lines[i:end])
            m = head.match(line)
            key = m.group(1) if m else None
            others = {k.group(0) for k in keys.finditer(md)} - {key}
            plain = _without_options(md)  # "Part of" and "Moves with" are not read from the options block
            moves = []
            for found in link.finditer(plain):
                for other in keys.finditer(found.group(1)):
                    if other.group(0) != key and other.group(0) not in moves:
                        moves.append(other.group(0))
            # The session, Status, Ready for review and decision lines quote other tiles' words, not this tile's.
            own = "\n".join(x for x in md.split("\n") if not (
                SESSION_LINE_RE.match(x) or STATUS_LINE_RE.match(x) or READY_RE.match(x) or DECISION_LINE_RE.match(x)))
            own = _without_options(own)
            blocking = []
            for found in blocked.finditer(own):
                for other in keys.finditer(found.group(1)):
                    if other.group(0) != key and other.group(0) not in blocking:
                        blocking.append(other.group(0))
            out.append(Tile(
                key=key, column=column, start=i, end=end, text=md, title=_title(store, md),
                parent=next((p.group(1) for p in part.finditer(plain) if p.group(1) != key), None),
                links=moves, mentions=sorted(others, key=store.num), blockers=blocking,
                done=file == ARCHIVE_LOG or bool(DONE_COL_RE.search(column))))
            i = end
            continue
        i += 1
    return out


def numbers(store, text: str) -> list:
    """The number of every key that opens a tile line in a board file, under a column or not.
    Prose that mentions a key doesn't count."""
    return [int(n) for n in _patterns(store.key)[5].findall(text)]


def head_re(store) -> re.Pattern:
    """The pattern for a tile line that opens with a key: group 1 is the key."""
    return _patterns(store.key)[0]


def opening_key(store, line: str):
    """The key a tile's first line opens with (after its bullet and checkbox), or None."""
    found = head_re(store).match(line)
    return found.group(1) if found else None


def with_key(line: str, key: str) -> str:
    """A tile's first line with a key put in after its bullet and checkbox."""
    m = re.match(r"^([-*] (?:\[[ xX]\] )?)(.*)$", line)
    return f"{m.group(1)}{key} {m.group(2)}"


def climb(parent_of, key: str) -> list:
    """The keys above a key, nearest first: its master, its master's master. parent_of(key)
    is the key a tile is part of, or None. It stops at the first repeat, so a ring of masters
    ends where it began. A master with no tile of its own is the last one named."""
    out, seen, up = [], {key}, parent_of(key)
    while up and up not in seen:
        out.append(up)
        seen.add(up)
        up = parent_of(up)
    return out


def linked(tile_lists) -> dict:
    """{"KEY-NN": [the keys it moves with]} over these lists of tiles, for every tile in a
    group of two or more. A link counts in both directions and chains."""
    edges = {}
    for tiles in tile_lists:
        for t in tiles:
            if t.key is None:  # an unkeyed tile has no place in a group (the page skips it too)
                continue
            for other in t.links:
                edges.setdefault(t.key, set()).add(other)
                edges.setdefault(other, set()).add(t.key)
    out = {}
    for key in edges:
        seen, todo = {key}, [key]
        while todo:
            for other in edges[todo.pop()]:
                if other not in seen:
                    seen.add(other)
                    todo.append(other)
        out[key] = sorted(seen - {key})
    return out


# ---------- a tile's attribute lines ----------
def model(md: str):
    """The model a tile's own "Model: x" line names, or None."""
    found = MODEL_RE.search(md or "")
    return found.group(1) if found else None


def base(md: str):
    """The branch the first "Base: x" line in md names, or None if there is no such line."""
    found = BASE_RE.search(md or "")
    return found.group(1) if found else None


def tests(md: str):
    """What the first "Tests: full" or "Tests: affected" line in md says, in lower case, or
    None if there is no such line."""
    found = TESTS_RE.search(md or "")
    return found.group(1).lower() if found else None


def domain(md: str):
    """What the first "Domain: name" line in md names, as written, or None if there is no such line."""
    found = DOMAIN_RE.search(md or "")
    return found.group(1).strip() if found else None


def repos(md: str):
    """The repos named on the first "Repos: a, b" line in md, in order, any case, no
    duplicates. None if there is no such line."""
    found = REPOS_RE.search(md or "")
    if not found:
        return None
    names = []
    for name in re.split(r"[,\s]+", found.group(1).replace("`", "")):
        if name and name.lower() not in [n.lower() for n in names]:
            names.append(name)
    return names


def sessions(md: str):
    """How many sessions a master's "Sessions: n" line allows at once, or None."""
    found = SESSIONS_RE.search(md or "")
    return int(found.group(1)) if found else None


def ready(md: str):
    """What a tile's "Ready for review" line says, "" if it says nothing more, None if the
    tile has no such line."""
    found = READY_RE.search(md or "")
    return found.group(1).strip() if found else None


def decision(md: str):
    """What a tile's last "Decision <date>:" line says, as {"date", "text"} (text "" if the line
    says nothing more), None if the tile has no such line."""
    found = DECISION_LINE_RE.findall(md or "")
    return {"date": found[-1][0], "text": found[-1][1].strip()} if found else None


def options(md: str):
    """What a tile's first Options line and the option lines under it say, as {"date",
    "recommends", "doc", "asked", "items": [{"letter", "title", "summary"}]} (recommends, doc and
    asked None when absent), None if the tile has no Options line. Same in board.html (optionsOf)."""
    lines = (md or "").split("\n")
    spans = _options_spans(lines)
    if not spans:
        return None
    a, b = spans[0]
    m = OPTIONS_LINE_RE.match(lines[a])
    rest = m.group(2) or ""
    asked = _OPTIONS_ASKED_RE.search(rest)
    head = rest[:asked.start()] if asked else rest
    rec, doc = _OPTIONS_RECOMMENDS_RE.search(head), _OPTIONS_DOC_RE.search(head)
    items = []
    for x in lines[a + 1:b]:
        i = OPTION_ITEM_RE.match(x)
        items.append({"letter": i.group(1), "title": re.sub(r"\.$", "", i.group(2)), "summary": (i.group(3) or "").strip()})
    return {"date": m.group(1), "recommends": rec.group(1) if rec else None, "doc": doc.group(1) if doc else None,
            "asked": asked.group(1).strip() if asked else None, "items": items}


def opted_out(md: str) -> bool:
    """Whether the text says "No pickup"."""
    return bool(OPT_OUT_RE.search(md or ""))


# ---------- columns ----------
def is_pickup_column(store, name: str) -> bool:
    """Whether a column's tiles are picked up: the one the store's "pickupColumn" names (any
    case), else any In flight column (IN_FLIGHT_RE). board.html asks the same (isPickupColumn)."""
    if store.pickup_column:
        return name.strip().lower() == store.pickup_column.lower()
    return bool(IN_FLIGHT_RE.search(name))


def is_decision_column(name: str) -> bool:
    """Whether a column holds decisions to make (DECISION_COL_RE). board.html asks the same
    (isDecisionCol)."""
    return bool(DECISION_COL_RE.search(name))


# ---------- the session line ----------
def session(md: str):
    """The session line of a tile's markdown as SessionLine, or None. Reads the
    one-worktree form (Branch `b`, worktree `w`) and the several-worktrees form (Branch `b`,
    worktrees api `a`, web `w`). Only an indented line of its own counts."""
    found = SESSION_LINE_RE.search(md or "")
    if not found:
        return None
    line = md[found.start():].split("\n", 1)[0]
    several = SESSION_REPOS_RE.search(line)
    if several:
        cuts = [(name.strip(), label) for name, label in re.findall(r"([^`,]+?) `([^`]+)`", several.group(2))]
        return SessionLine(found.group(1), found.group(2), several.group(1), cuts)
    one = SESSION_BRANCH_RE.search(line)
    if one:
        return SessionLine(found.group(1), found.group(2), one.group(1), [(None, one.group(2))])
    named = SESSION_ANY_BRANCH_RE.search(line)
    return SessionLine(found.group(1), found.group(2), named.group(1) if named else None, [])


# ---------- edits ----------
def _tile(store, text: str, key: str):
    return next((t for t in parse(store, text) if t.key == key), None)


def without(text: str, tiles) -> str:
    """The board without these tiles (Tile entries of this text), layout kept."""
    lines = text.split("\n")
    for t in sorted(tiles, key=lambda t: t.start, reverse=True):
        del lines[t.start:t.end]
        at = t.start  # the column's last tile leaves two blank lines behind: keep one
        if 0 < at < len(lines) and not lines[at - 1].strip() and not lines[at].strip():
            del lines[at]
    return "\n".join(lines)


def take(store, text: str, keys) -> tuple:
    """(the board without the tiles that have these keys, those tiles' markdown in board
    order). The board keeps its layout, so it still round-trips through board.html."""
    taken = [t for t in parse(store, text) if t.key in keys]
    return without(text, taken), [t.text for t in taken]


def insert_after(store, text: str, key: str, tile_mds) -> str:
    """Put tiles straight after the tile with this key, in order."""
    at = _tile(store, text, key)
    if at is None:
        raise KeyError(f"no {key} tile")
    lines = text.split("\n")
    lines[at.end:at.end] = [line for md in tile_mds for line in md.split("\n")]
    return "\n".join(lines)


def with_session(store, text: str, key: str, line: str) -> str:
    """The board with a session line (indented, as the server writes it) in the tile with
    this key: after the description and before the first sub-task. It replaces the line an
    earlier session left. A board with no such tile comes back as it was."""
    tile = _tile(store, text, key)
    if tile is None:
        return text
    if not line[:1].isspace():
        line = "  " + line  # an unindented line would end the tile
    lines = text.split("\n")
    body = [x for x in lines[tile.start + 1:tile.end] if not SESSION_LINE_RE.match(x)]
    at = next((i for i, x in enumerate(body) if re.match(r"^  [-*] ", x)), len(body))
    body.insert(at, line)
    lines[tile.start + 1:tile.end] = body
    return "\n".join(lines)


def with_status(store, text: str, key: str, line: str) -> str:
    """The board with a tile's status (pickup.status, "Status <date> <time>: ...") written in
    right under its session line (as its last line, if it has none), its stamp in bold,
    replacing the status it had."""
    tile = _tile(store, text, key)
    if tile is None:
        return text
    lines = text.split("\n")
    body = [x for x in lines[tile.start + 1:tile.end] if not STATUS_LINE_RE.match(x)]
    at = next((i for i, x in enumerate(body) if SESSION_LINE_RE.match(x)), None)
    body.insert(len(body) if at is None else at + 1, "  " + re.sub(r"^(Status \S+ \S+:)", r"**\1**", line))
    lines[tile.start + 1:tile.end] = body
    return "\n".join(lines)


def with_decision(md: str, answer: str, date: str) -> str:
    """A tile's markdown with its answer as the last line, "  **Decision <date>:** <answer>", and
    its opening "[ ]" ticked (a tile with no box keeps none). The answer is folded to one line:
    every run of whitespace, newlines included, becomes one space. An earlier decision stays above."""
    folded = " ".join((answer or "").split())
    lines = md.rstrip("\n").split("\n")
    lines[0] = re.sub(r"^(\s*[-*+] )\[ \]", r"\1[x]", lines[0], count=1)
    lines.append(f"  **Decision {date}:** {folded}")
    return "\n".join(lines)


def options_block(date: str, items: list, recommends=None, doc=None, asked=None) -> list:
    """The lines of an options block, indented as the server writes them: the Options line, then
    one line per item ({"letter", "title", "summary"}). Everything is folded to one line."""
    fold = lambda x: " ".join((x or "").split())
    head = f"  **Options {date}:**"
    if not items:
        head += " the draft gave no options."
    if recommends:
        head += f" recommends {recommends}."
    if doc:
        head += f" [Research]({doc})"
    if fold(asked):
        head += f" Asked: {fold(asked)}"
    out = [head]
    for it in items:
        title = fold(it["title"])
        title += "" if title.endswith((".", "?", "!")) else "."
        out.append(f"  - **{it['letter']}. {title}**" + (f" {fold(it['summary'])}" if fold(it["summary"]) else ""))
    return out


def with_options(store, text: str, key: str, block: list) -> str:
    """The board with an options block (options_block) in the tile with this key: after the
    description and before the first tick-box sub-task. It replaces a block already on the tile,
    wherever that is. A board with no such tile comes back as it was."""
    tile = _tile(store, text, key)
    if tile is None:
        return text
    lines = text.split("\n")
    body = lines[tile.start + 1:tile.end]
    drop = {i for a, b in _options_spans(body) for i in range(a, b)}
    body = [x for i, x in enumerate(body) if i not in drop]
    at = next((i for i, x in enumerate(body) if re.match(r"^  [-*] \[[ xX]\] ", x)), len(body))
    body[at:at] = block
    lines[tile.start + 1:tile.end] = body
    return "\n".join(lines)


# The lines a tile's description ends before: a sub-task, the session line, a status, ready, options or
# decision line, or the archive's stamp.
AFTER_DESCRIPTION_RE = re.compile(r"^(?:  [-*] |[ \t]+(?:\*\*Status|\*\*Ready for review|Ready for review|\*\*Options|\*\*Decision|Archived ))")


def domain_name(raw: str) -> str:
    """A domain's name as it will be written: raw trimmed. Raises ValueError with a sentence
    saying why when it is empty, starts with anything but a letter or a digit, holds a backtick
    or a line break, is over 39 characters or ends in a full stop. Same rule in board.html
    (domainName there); the cases are tile_cases.json's domainNames."""
    name = (raw or "").strip()
    if not name:
        raise ValueError("A domain needs a name.")
    if not re.match(r"[A-Za-z0-9]", name):
        raise ValueError("A domain's name starts with a letter or a digit.")
    if "`" in name or "\n" in name or "\r" in name:
        raise ValueError("A domain's name can't hold a backtick or a line break.")
    if len(name) > 39:
        raise ValueError("A domain's name is at most 39 characters.")
    if name.endswith("."):
        raise ValueError("A domain's name can't end in a full stop.")
    return name


_TITLE_HEAD_RE = re.compile(r"^([A-Za-z][\w&-]*)(?=[ :])[^:]{0,30}:\s", re.ASCII)  # same in board.html (tileDomains' head)


def tile_domains(index: dict) -> dict:
    """Each tile's domain as the map draws it, {key: name or ""}, for an index of {key: {"title",
    "parent", "domain"}} (Snapshot.index): its own Domain line, else its master's domain, else
    the first word before the colon in its title ("Admin: analytics") when two masters or three
    stories start with that word. Names that differ only in case are one domain, spelt as the
    first met. Same in board.html (tileDomains there); the cases are tile_cases.json's tileDomains."""
    def head(t):
        found = _TITLE_HEAD_RE.match(t.get("title") or "")
        return found.group(1) if found else ""
    masters = {t["parent"] for t in index.values() if t.get("parent")}
    stories, led = {}, {}
    for k, t in index.items():
        h = head(t).lower()
        if h:
            stories[h] = stories.get(h, 0) + 1
            if k in masters:
                led[h] = led.get(h, 0) + 1
    spelt, out = {}, {}

    def shared(h):
        return led.get(h, 0) >= 2 or stories.get(h, 0) >= 3

    def find(k, seen):
        if k in out:
            return out[k]
        t, up = index[k], index[k].get("parent")
        up = up if up and up in index and up not in seen else None
        if t.get("domain"):
            d = spelt.setdefault(t["domain"].lower(), t["domain"])
        elif up:
            seen.add(k)
            d = find(up, seen)
        else:
            d = ""
        if not d and shared(head(t).lower()):
            d = spelt.setdefault(head(t).lower(), head(t))
        out[k] = d
        return d
    for k in index:
        find(k, set())
    return out


def with_domain(store, text: str, key: str, name) -> str:
    """The board with the tile with this key in the domain name: its first Domain: line
    replaced by "  Domain: name", or one added as the last line of its description (before a
    sub-task, the session line, a status, ready, options or decision line or the archive's
    stamp). None takes the first Domain: line off. A name domain_name refuses raises its
    ValueError. A board with no such tile, or where nothing would change, comes back as it was."""
    if name is not None:
        name = domain_name(name)
    tile = _tile(store, text, key)
    if tile is None:
        return text
    lines = text.split("\n")
    body = lines[tile.start + 1:tile.end]
    found = next((i for i, x in enumerate(body) if DOMAIN_RE.match(x)), None)
    if name is None:
        if found is None:
            return text
        del body[found]
    elif found is not None:
        if domain(body[found]) == name:
            return text
        body[found] = f"  Domain: {name}"
    else:
        at = next((i for i, x in enumerate(body) if AFTER_DESCRIPTION_RE.match(x) or SESSION_LINE_RE.match(x)), len(body))
        while at == len(body) and at > 0 and not body[at - 1].strip():
            at -= 1  # not after the blank lines that end the tile
        body.insert(at, f"  Domain: {name}")
    lines[tile.start + 1:tile.end] = body
    return "\n".join(lines)


# ---------- a snapshot across the boards ----------
class Snapshot:
    """The tiles of the build board, the plan board and the archive log, read once, with what
    the whole family answers: where a key's tile is, its masters, the nearest line of a kind up
    its family. A key on two files is the first one's tile (build, plan, archive in that order).
    A ring of masters is broken the way board.html does: the first of them stands as the
    master and has none."""

    def __init__(self, store, files: dict):
        """files: {name: text}, names as in the store's family (any others read as plain
        boards). build and plan are the board names these files go by, taken once, by the
        store's rule (store.names) applied to the names given, or the store's own names when
        no build board is among them."""
        self.store = store
        self.files = dict(files)
        given = stores.BUILD in self.files or stores.OLD_BUILD in self.files
        self.build, self.plan = stores.names(self.files) if given else store.boards
        self._parsed = {name: parse(store, text, file=name) for name, text in self.files.items()}
        self._at = {}  # key -> (file name, Tile)
        for name, found in self._parsed.items():
            for t in found:
                if t.key and t.key not in self._at:
                    self._at[t.key] = (name, t)
        self._parent = {k: t.parent for k, (_, t) in self._at.items()}
        for k in self._parent:
            up, seen = self._parent[k], set()
            while up and up != k and up not in seen:
                seen.add(up)
                up = self._parent.get(up)
            if up == k:
                self._parent[k] = None

    @classmethod
    def read(cls, store, names=None):
        """The snapshot of these files of the store that exist (by default its family)."""
        names = store.family if names is None else names
        files = {n: (store.dir / n).read_text(encoding="utf-8") for n in names if (store.dir / n).is_file()}
        return cls(store, files)

    def tiles(self, name: str) -> list:
        """The tiles of one file, in order."""
        return self._parsed.get(name, [])

    def tile(self, key: str):
        """The Tile with this key, or None."""
        found = self._at.get(key)
        return found[1] if found else None

    def parent(self, key: str):
        """The master a key's tile is part of, or None (a ring of masters broken)."""
        return self._parent.get(key)

    def index(self) -> dict:
        """{"KEY-NN": {"title", "file", "column", "done", "parent", "mentions", "blockers",
        "decision", "tests", "domain"}} for every keyed tile. done: in a Done column, or in the
        archive log. parent: the master it says it is part of, or None. mentions: the other keys
        its text names, sorted. blockers: the keys its own text says block it. decision: its
        last decision line, as {"date", "text"}, or None. tests: its own "Tests:" value.
        domain: its own "Domain:" value."""
        return {k: {"title": t.title, "file": name, "column": t.column, "done": t.done,
                    "parent": self._parent[k], "mentions": t.mentions, "blockers": t.blockers,
                    "decision": decision(t.text), "tests": tests(t.text), "domain": domain(t.text)}
                for k, (name, t) in self._at.items()}

    def texts(self) -> dict:
        """{key: tile markdown} for every keyed tile."""
        return {k: t.text for k, (_, t) in self._at.items()}

    def linked(self) -> dict:
        """What linked(...) says over every tile of these files."""
        return linked(self._parsed.values())

    def lineage(self, key: str) -> list:
        """The tiles a tile is part of, nearest master first, as far as they have tiles."""
        return [self._at[k][1] for k in climb(self._parent.get, key) if k in self._at]

    def nearest(self, key: str, reader):
        """The first value reader(markdown) gives that is not None, going from the key's own
        tile up its masters; None if no tile up the family has one."""
        for t in [self.tile(key), *self.lineage(key)]:
            found = reader(t.text) if t else None
            if found is not None:
                return found
        return None

    def _blockers_of(self, key: str) -> list:
        """The blocker keys of a tile and of each master up its lineage, nearest first, each
        once, never the tile's own key. Same in board.html (`blockerKeys` there)."""
        out = []
        for t in [self.tile(key), *self.lineage(key)]:
            for b in (t.blockers if t else []):
                if b != key and b not in out:
                    out.append(b)
        return out

    def blocking(self, key: str) -> list:
        """The keys still blocking a tile: its own blockers, then each master's up the lineage,
        each once, never its own key. A blocker blocks until its tile is done (Tile.done); a key
        with no tile (tile(k) is None) is unknown, and blocks. Same in board.html (`blocking` there)."""
        return [b for b in self._blockers_of(key) if not (self.tile(b) and self.tile(b).done)]

    def decisions(self, key: str) -> list:
        """The done blockers of a tile and its masters whose text has a decision line, as Tile,
        in the order blocking lists blockers. Same in board.html (`decisionsFor` there)."""
        found = (self.tile(b) for b in self._blockers_of(key))
        return [t for t in found if t and t.done and decision(t.text)]

    def in_flight(self) -> list:
        """The keyed tiles in the build board's pickup column (is_pickup_column), for
        pickup.py: [{"key", "title", "text", "parent"}]. A tile whose master isn't in flight
        (it sits in another column, on the backlog or in the archive) also carries "of", that
        master's key, and "context", the tiles of the masters above it, for its session to
        read: a design or a decision is often linked from the master alone. A tile with keys
        still blocking it carries "blocked", those keys, and one with decisions made for it
        carries "decisions", those tiles' markdown joined by a newline."""
        flying = [t for t in self.tiles(self.build) if t.key and is_pickup_column(self.store, t.column)]
        in_flight = {t.key for t in flying}
        out = []
        for t in flying:
            tile = {"key": t.key, "title": t.title, "text": t.text, "parent": self._parent.get(t.key)}
            above = []
            for k in climb(self._parent.get, t.key):
                if k in in_flight or k not in self._at:
                    break
                above.append(self._at[k][1])
            if above:
                tile.update(of=above[0].key, context="\n".join(a.text for a in above))
            blocked, decided = self.blocking(t.key), self.decisions(t.key)
            if blocked:
                tile["blocked"] = blocked
            if decided:
                tile["decisions"] = "\n".join(d.text for d in decided)
            out.append(tile)
        return out
