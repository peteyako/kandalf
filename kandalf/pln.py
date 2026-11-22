"""Tile keys for a store's boards (PLN-NN, FLY-NN: the prefix is the store's "key").

Every top-level tile on the board carries a key at the front of its line:
`- [ ] PLN-07 **Title.** …`. Numbers are never reused: the next one is one
past the highest key in plans.md, backlog.md and the archive log together.

    kandalf key next      # print the next free key
    kandalf key assign    # key every tile that has none, oldest first

`assign` also puts a tile's key into the title of each plan doc in active/
it links, so the doc and its story read the same.
"""

import re
from pathlib import Path

from . import tiles as reader

ACTIVE_LINK_RE = re.compile(r"\]\(active/([\w.-]+\.md)\)")


def highest(store, *paths: Path) -> int:
    """The highest key on a tile line; keys mentioned in prose don't count."""
    nums = [n for p in paths if p.is_file() for n in reader.numbers(store, p.read_text(encoding="utf-8"))]
    return max(nums, default=0)


def keyed_files(store) -> list:
    """Every file whose tiles hold keys: the plans board, the backlog board, the archive log."""
    return [store.dir / "plans.md", store.dir / "backlog.md", store.dir / "archive" / "archived.md"]


def next_key(store) -> str:
    return store.fmt(highest(store, *keyed_files(store)) + 1)


def tiles_oldest_first(store, lines):
    """Indexes of unkeyed top-level tiles, oldest first: the board puts the newest tile at
    the top of its column and columns run Backlog → Done, so walk the columns right to
    left and each column bottom to top."""
    headings = [i for i, line in enumerate(lines) if line.startswith("## ")]
    columns = [[] for _ in headings]
    for t in reader.parse(store, "\n".join(lines)):
        if t.key is None:
            columns[sum(1 for h in headings if h < t.start) - 1].append(t.start)
    return [i for col in reversed(columns) for i in reversed(col)]


def assign(store) -> list:
    """Key every unkeyed tile: the archive log first (its tiles are the oldest work), then the board."""
    board, backlog, log = keyed_files(store)
    n = highest(store, board, backlog, log)
    given = []
    for path in (log, backlog, board):
        if not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8").split("\n")
        for i in tiles_oldest_first(store, lines):
            n += 1
            lines[i] = reader.with_key(lines[i], store.fmt(n))
            given.append(store.fmt(n))
        path.write_text("\n".join(lines), encoding="utf-8")
        if path != log:
            title_active_docs(store, lines)
    return given


def title_active_docs(store, lines):
    """Prefix each active plan doc's first heading with the key of the tile that links it."""
    key = None
    for line in lines:
        if line.startswith("- "):
            m = store.key_re.search(line)
            key = store.fmt(int(m.group(1))) if m else None
        if not key:
            continue
        for name in ACTIVE_LINK_RE.findall(line):
            doc = store.dir / "active" / name
            if not doc.is_file():
                continue
            text = doc.read_text(encoding="utf-8")
            if store.key_re.search(text.split("\n", 1)[0]):
                continue
            first, _, rest = text.partition("\n")
            if first.startswith("# "):
                doc.write_text(f"# {key}: {first[2:]}\n{rest}", encoding="utf-8")


def main(store, args):
    cmd = args[0] if args else "next"
    if cmd == "next":
        print(next_key(store))
    elif cmd == "assign":
        keys = assign(store)
        if keys and store.root:
            from . import gitlog
            gitlog.record(store, [*keyed_files(store), *(store.dir / "active").glob("*.md")],
                          f"board: key {', '.join(keys)}")
        print(f"keyed {len(keys)} tiles" + (f": {keys[0]}–{keys[-1]}" if keys else ""))
    else:
        raise SystemExit(__doc__)
