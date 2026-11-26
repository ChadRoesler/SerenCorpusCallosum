"""
seren_corpus_callosum.fit
═══════════════════════════════════════════════════════════════════════

Each store's habit: what its best hit usually looks like.

Two stores' scores cannot be set side by side. Their own scores say little
(Loci once scored six unrelated facts 0.92 to 0.94 on a question about a day
off), and their raw distances are in different units: one store reports
straight-line distance and another cosine, and two stores need not share an
embedder at all. Swap a store's model and every number it reports moves.

So nothing here compares stores. It holds each store against ITSELF: the raw
distance of its best hit on each question, remembered over the last WINDOW
questions, gives a median (its usual) and a spread. How a distance sits against
that is the same kind of statement for every store, whatever its unit.

    closeness  how far inside a store's own usual a distance sits, in spreads:
               (usual - distance) / spread. Positive is closer than its best
               hit usually is. Clipped to +-3.

WHAT IT IS USED FOR. A store may set rows aside (options.habit_floor;
Federation._set_aside): a row sitting more than that many spreads past the
store's usual best hit follows the others and rides only if there is room.

WHAT IT WAS TRIED FOR AND IS NOT USED FOR (25 Nov 2026). A TILT: moving SEATS
between stores by how close each store's best hit came. Chad: "something should
be able to go: oh memory seems to fit more vs loci, or loci fits more than
memory". The habit does call the store that holds the answer 73 to 79 times in
100 on Vellmarch's frozen answers. But replayed as a tilt it lost: letting the
closer store take seats cost four to six points on a 1,000-token page, because
Memory holds episodes about the same things the facts are about and comes close
to a fact question too, so the lean took seats from the store that had been
answering every one of them; and letting a far store only give seats up changed
no score and changed none of seven real pages, because it almost never fired.
The knob, its shares and the stretched fuse were removed the same day. The
replays are in Probe (TestDatasets/baselines/runs/tilt-vellmarch*.json).

The habit is learned from the questions asked since the service started and is
not kept across a restart: until a store has been asked WARM questions it has
none, and whatever reads it must then do nothing. A store whose embedder
changes should have its habit forgotten; today only a restart does that.
"""
from __future__ import annotations

import math
import statistics
from collections import deque
from typing import Iterable, Optional

WINDOW = 256        # best-hit distances remembered per store
WARM = 20           # asked fewer times than this, a store has no habit yet
CLIP = 3.0          # a closeness beyond this many spreads is read as this many
_MAD_TO_SIGMA = 1.4826


class FitTracker:
    """Each store's recent best-hit distances, and how a distance compares."""

    def __init__(self, window: int = WINDOW, warm: int = WARM):
        self._window, self._warm = max(1, window), max(1, warm)
        self._seen: dict[str, deque[float]] = {}

    def note(self, store: str, best: Optional[float]) -> None:
        """Remember one question's best-hit distance for a store."""
        if best is None or not math.isfinite(best):
            return
        self._seen.setdefault(store, deque(maxlen=self._window)).append(float(best))

    def seed(self, store: str, bests: Iterable[float]) -> None:
        """Hand a store a habit it did not learn here (a replay, a test)."""
        for b in bests:
            self.note(store, b)

    def closeness(self, store: str, distance: Optional[float]) -> Optional[float]:
        """How far inside the store's own usual a distance sits, in spreads.
        None when the store has no habit yet or there is no distance."""
        seen = self._seen.get(store)
        if distance is None or seen is None or len(seen) < self._warm:
            return None
        vals = list(seen)
        usual = statistics.median(vals)
        spread = statistics.median(abs(v - usual) for v in vals) * _MAD_TO_SIGMA
        if spread <= 0:
            return 0.0                      # a store that always answers the same has nothing to say by this
        return max(-CLIP, min(CLIP, (usual - float(distance)) / spread))

    def usual(self) -> dict[str, dict]:
        """Each store's habit, for a person to read: how many questions it is
        built from, the usual best-hit distance and its spread."""
        out: dict[str, dict] = {}
        for store, seen in self._seen.items():
            vals = list(seen)
            usual = statistics.median(vals)
            out[store] = {"asked": len(vals), "warm": len(vals) >= self._warm, "usual": round(usual, 4),
                          "spread": round(statistics.median(abs(v - usual) for v in vals) * _MAD_TO_SIGMA, 4)}
        return out


def best_distance(hits: list) -> Optional[float]:
    """The raw distance of a store's best hit: its first-ranked hit that carries
    one. Only the first three are looked at; a store whose lead rows carry no
    distance (an exact-key match with no vector behind it) has none to offer."""
    for h in hits[:3]:
        d = getattr(h, "raw_distance", None)
        if d is not None:
            try:
                d = float(d)
            except (TypeError, ValueError):
                continue
            if math.isfinite(d):
                return d
    return None
