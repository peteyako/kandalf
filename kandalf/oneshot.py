"""What every agent module's `ask` does with the reply to a one-shot question: the title it
suggests, as one plain line. The modules run their own CLI; nothing here runs one."""

import re

LIMIT = 120  # the longest title a story may have (serve.TITLE_LIMIT says the same)
_QUOTES = "\"'`“”‘’"
_KEY_RE = re.compile(r"^(?:[A-Z][A-Z0-9]*-\d+\b[\s:.,–—-]*)+")


def reply_title(text) -> "str | None":
    """The first non-empty line of a reply as a title: without surrounding quotes, bold or a key
    at its start, at most LIMIT characters; None when nothing is left."""
    line = next((x.strip() for x in str(text or "").splitlines() if x.strip()), "")
    for _ in range(2):  # a quote can sit inside bold, and bold inside a quote
        line = line.replace("**", "").strip().strip(_QUOTES).strip()
    line = _KEY_RE.sub("", line).strip().strip(_QUOTES).strip()
    return line[:LIMIT].strip() or None
