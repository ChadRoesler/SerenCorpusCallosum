"""
seren_corpus_callosum.names
═══════════════════════════════════════════════════════════════════════

Names: what two stores have in common when their numbers have nothing.

A whole day (25 Nov 2026) went into finding a ruler both stores could be held
to: their scores cannot be compared, their distances are in different units,
and any number either reports moves when its embedder changes. Then Chad:
"MEMORY HAS TOPIC A PARALLEL TO LOCIS KEYS". A memory carries topic tags; a
fact lives at a project and a key. Both are NAMES a person chose, they survive
every model swap, and they can be read.

Checked the same night on a real memory, eight questions, each of Loci's top
eight facts against the names on the top five memories: a question no fact
answered (a good day off) had 0 of 8 facts sharing a name, which no distance
had been able to say; a question about the kitchen had 8 of 8; the two facts
about a face shared "face" and the six beside them shared nothing.

What a shared name means, and does not:
  shared     the stores AGREE what the question is about; the row is on the subject
  not shared the stores DISAGREE, and that is all. It does not say which is
             right: asked what licence the repos are under, the answer was
             Loci's first and nearest fact and shared nothing, because it was
             Memory that had nothing on the subject.

    name_tokens   the words in a tag or a key ("own-mcp-door" -> own, mcp, door)
    row_names     a row's names: its topic tags, and a fact's project and key
    same_name     equal, or one word spelled two ways (licence / license,
                  satellite / satallite); never two different things that only
                  belong together (serenchan / stackchan)
    NameTally     which names are on nearly everything in THIS house and so say
                  nothing. Learned, never listed: no name is built in.

It only works where topics name things. A topic that is a kind of event
("war_declared") names nobody and joins nothing.
"""
from __future__ import annotations

import re
from collections import Counter, deque
from typing import Iterable

_WORD = re.compile(r"[a-z0-9]+")
MIN_LEN = 3            # shorter than this is not a name ("a", "of", "to")
WINDOW = 4000          # rows the tally remembers
WARM = 200             # rows seen before any name can be called common
COMMON_SHARE = 0.15    # a name on more than this share of the rows says nothing


# Words that hold a key together and name nothing ("the-punch-list-lives-in-the-
# kitchen"). English, few, and only the kind no one tags a memory with; the
# words a HOUSE overuses are learned by the tally, never listed here.
_GLUE = frozenset({"the", "and", "for", "but", "not", "with", "from", "that", "this", "are", "was", "its"})


def name_tokens(text) -> set[str]:
    """The words of a tag, a key or a project, lowercased, without the glue."""
    return {w for w in _WORD.findall(str(text or "").lower()) if len(w) >= MIN_LEN and w not in _GLUE}


def row_names(hit) -> set[str]:
    """The names a row answers to: its topic tags and, for a fact, its project
    and key. A project of '*' (true everywhere) is no name."""
    md = getattr(hit, "metadata", None) or {}
    out = name_tokens(md.get("topic"))
    if md.get("key") is not None:
        out |= name_tokens(md.get("key"))
        if md.get("project") not in (None, "", "*"):
            out |= name_tokens(md.get("project"))
    return out


def _edits(a: str, b: str, limit: int) -> int:
    """Edit distance (a swap of neighbours counts once), cut off past `limit`."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2: list[int] = []
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[-1]


def same_name(a: str, b: str) -> bool:
    """Equal, a plural, or one word spelled two ways: one slip in a word of six
    letters or more, two in one of nine or more, the first letter the same.
    Short words must match exactly: one slip turns 'store' into 'stone'."""
    if a == b:
        return True
    short, long_ = sorted((a, b), key=len)
    if len(short) >= 4 and long_ == short + "s":
        return True
    if len(short) < 6 or a[0] != b[0]:
        return False
    allowed = 2 if len(short) >= 9 else 1
    return _edits(a, b, allowed) <= allowed


def shared_names(mine: Iterable[str], theirs: Iterable[str]) -> list[str]:
    """Those of `mine` that some name in `theirs` is the same as, sorted."""
    theirs = list(theirs)
    return sorted(n for n in set(mine) if any(same_name(n, t) for t in theirs))


class NameTally:
    """Which names are on nearly everything here. A house's own name, its
    person's, its partner's turn up in most tags and say nothing about any one
    question; which names those are differs house to house, so they are
    counted, not listed. Until WARM rows have been seen nothing is common."""

    def __init__(self, window: int = WINDOW, warm: int = WARM, share: float = COMMON_SHARE):
        self._window, self._warm, self._share = max(1, window), max(1, warm), share
        self._rows: deque[frozenset[str]] = deque()
        self._count: Counter[str] = Counter()

    def note(self, rows: Iterable[Iterable[str]]) -> None:
        """Remember the names of each row of a packet."""
        for names in rows:
            names = frozenset(names)
            self._rows.append(names)
            self._count.update(names)
            if len(self._rows) > self._window:
                old = self._rows.popleft()
                self._count.subtract(old)

    def common(self, name: str) -> bool:
        n = len(self._rows)
        return n >= self._warm and self._count.get(name, 0) / n > self._share

    def usual(self) -> dict:
        """For a person to read: how many rows it is built from and which names
        it currently holds to be on everything."""
        n = len(self._rows)
        common = sorted((k for k, v in self._count.items() if n >= self._warm and v / n > self._share),
                        key=lambda k: -self._count[k])
        return {"rows": n, "warm": n >= self._warm, "common": common[:40]}
