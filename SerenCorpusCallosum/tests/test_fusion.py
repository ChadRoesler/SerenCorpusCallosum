"""
Tests for seren_corpus_callosum.fusion.

Runnable two ways:
    pytest tests/test_fusion.py
    python tests/test_fusion.py        # plain-assert harness, prints a summary

The headline test is test_embedder_change_does_not_perturb_order: it
simulates a store getting a new embedder (its relevance magnitudes shift
wildly) and proves the merged ranking is identical, because fusion reads
rank, never magnitude.
"""
from __future__ import annotations

import os
import sys

# Allow `python tests/test_fusion.py` from anywhere in the package.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from seren_corpus_callosum.fusion import (  # noqa: E402
    Hit,
    apply_floor,
    base_relevance_from_distance,
    rrf_fuse,
)


def _hit(store: str, hid: str, base: float, dist: float | None = None) -> Hit:
    return Hit(store=store, id=hid, content=f"{store}:{hid}", base_relevance=base,
               raw_distance=dist)


def _order(fused) -> list[str]:
    """The fused result as a flat list of '<store>:<id>' for easy asserting."""
    return [f"{f.hit.store}:{f.hit.id}" for f in fused]


def test_base_relevance_transform():
    # The shared 1/(1+d) transform both real stores use.
    assert base_relevance_from_distance(0.0) == 1.0
    assert abs(base_relevance_from_distance(1.0) - 0.5) < 1e-9
    # Matches the live Loci numbers we saw: d=0.881202 -> ~0.531575
    assert abs(base_relevance_from_distance(0.881202) - 0.531575) < 1e-5
    # Negative distance clamps (defensive).
    assert base_relevance_from_distance(-5.0) == 1.0


def test_interleaves_by_rank_with_stable_tiebreak():
    # A has 3 hits, B has 2. Equal weights. Expect rank-interleave with A
    # winning ties because A is iterated first (stable tie-break).
    a = [_hit("A", "a1", 0.9), _hit("A", "a2", 0.8), _hit("A", "a3", 0.7)]
    b = [_hit("B", "b1", 0.6), _hit("B", "b2", 0.5)]
    fused = rrf_fuse({"A": a, "B": b}, k=60)
    assert _order(fused) == ["A:a1", "B:b1", "A:a2", "B:b2", "A:a3"]


def test_embedder_change_does_not_perturb_order():
    # THE money test. Store B gets a "new embedder": its base_relevance and
    # raw_distance magnitudes change drastically. Its INTERNAL ORDER is the
    # same. Fusion reads rank, not magnitude, so the merged order must be
    # byte-for-byte identical.
    a = [_hit("A", "a1", 0.91, 0.10), _hit("A", "a2", 0.83, 0.20)]
    b_before = [_hit("B", "b1", 0.55, 0.82), _hit("B", "b2", 0.50, 0.99)]
    # Same ranking, totally different number scale (as if re-embedded).
    b_after = [_hit("B", "b1", 0.0009, 1100.0), _hit("B", "b2", 0.0005, 1900.0)]

    before = _order(rrf_fuse({"A": a, "B": b_before}, k=60))
    after = _order(rrf_fuse({"A": a, "B": b_after}, k=60))
    assert before == after, f"embedder change perturbed order: {before} != {after}"


def test_weights_express_cross_store_trust():
    # Give B twice the trust. B's rank-1 (2/61) now beats A's rank-1 (1/61).
    a = [_hit("A", "a1", 0.99)]
    b = [_hit("B", "b1", 0.10)]
    fused = rrf_fuse({"A": a, "B": b}, k=60, weights={"B": 2.0})
    assert _order(fused) == ["B:b1", "A:a1"]
    # ...and note A:a1 has the far higher base_relevance — proving the weight,
    # not the magnitude, is what moved it. Magnitude never entered the sort.


def test_floor_drops_weak_hits_before_fusion():
    hits = [_hit("A", "strong", 0.90), _hit("A", "weak", 0.20)]
    kept = apply_floor(hits, min_base_relevance=0.30)
    assert [h.id for h in kept] == ["strong"]
    # Floor <= 0 disables.
    assert len(apply_floor(hits, 0.0)) == 2


def test_floor_then_fuse_pipeline():
    a = [_hit("A", "a1", 0.90), _hit("A", "a_noise", 0.15)]
    b = [_hit("B", "b1", 0.70), _hit("B", "b_noise", 0.10)]
    floored = {"A": apply_floor(a, 0.3), "B": apply_floor(b, 0.3)}
    fused = rrf_fuse(floored, k=60)
    # Noise from both stores is gone; only the real hits interleave.
    assert _order(fused) == ["A:a1", "B:b1"]


def test_graceful_empty_and_missing_stores():
    # A store that's down contributes an empty list — no crash, just absent.
    a = [_hit("A", "a1", 0.9)]
    fused = rrf_fuse({"A": a, "B": []}, k=60)
    assert _order(fused) == ["A:a1"]
    # Nothing up at all -> empty result, still no crash.
    assert rrf_fuse({}, k=60) == []


def test_n_results_trim():
    a = [_hit("A", f"a{i}", 0.9 - i * 0.01) for i in range(10)]
    fused = rrf_fuse({"A": a}, k=60, n_results=3)
    assert len(fused) == 3
    assert _order(fused) == ["A:a0", "A:a1", "A:a2"]


def _run_all():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} green.")


if __name__ == "__main__":
    _run_all()
