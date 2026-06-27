"""
Tests for seren_corpus_callosum.fusion.

Runnable two ways:
    pytest tests/test_fusion.py
    python tests/test_fusion.py        # plain-assert harness, prints a summary

The headline test is test_embedder_change_does_not_perturb_order: it
simulates a store getting a new embedder (its relevance magnitudes shift
wildly) and proves the merged ranking is identical, because fusion reads
rank, never magnitude.

The authority/keystone block (added after the trust pass) covers the code
that actually RESHAPES the docket - most-confident-store promotion, the lone
exact-key lead, and the percentile / rrf_pct modes - including the cases where
promotion genuinely MOVES a hit (the branch the original suite never hit).
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
    _store_margin,
    _most_confident_store,
    _trim_with_quota,
    _VALID_FUSION_MODES,
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
    # ...and note A:a1 has the far higher base_relevance - proving the weight,
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
    # A store that's down contributes an empty list - no crash, just absent.
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


# ═════════════════════════════════════════════════════════════════════════
# Authority + keystone + N-store modes - the docket-RESHAPING code. Previously
# default-on (authority_margin=0.035) but exercised only in states where it was
# a no-op, so its reorder branch was never covered. These hit it for real.
# ════════════════════════════════════════════════════════════════════════


def test_store_margin_regimes():
    # Ordinary top: confidence is the gap to the runner-up.
    m = _store_margin([_hit("L", "x", 0.80), _hit("L", "y", 0.50)])
    assert abs(m - 0.30) < 1e-9
    # Lone perfect (exact-key 1.0): max-confidence ON ITS OWN MERIT - the keystone.
    assert _store_margin([_hit("L", "exact", 1.0)]) == 1.0
    # Lone non-perfect: sparse, not confident -> abstains.
    assert _store_margin([_hit("L", "meh", 0.45)]) is None
    # Empty -> None.
    assert _store_margin([]) is None
    # Perfect top WITH a runner-up: still max (own merit), not the 0.4 gap.
    assert _store_margin([_hit("L", "exact", 1.0), _hit("L", "y", 0.6)]) == 1.0


def test_most_confident_store_picks_widest_margin():
    a = [_hit("A", "a1", 0.9), _hit("A", "a2", 0.85)]   # margin 0.05
    b = [_hit("B", "b1", 0.9), _hit("B", "b2", 0.50)]   # margin 0.40
    assert _most_confident_store({"A": a, "B": b}, 0.035) == "B"
    # Nobody clears a high threshold -> None.
    assert _most_confident_store({"A": a, "B": b}, 0.95) is None


def test_authority_disabled_is_plain_rrf():
    # margin <= 0 must be byte-identical to the original rank-only RRF.
    a = [_hit("A", "a1", 0.60), _hit("A", "a2", 0.58)]
    b = [_hit("B", "b1", 0.95), _hit("B", "b2", 0.40)]
    off = _order(rrf_fuse({"A": a, "B": b}, k=60, authority_margin=0.0))
    default = _order(rrf_fuse({"A": a, "B": b}, k=60))   # default authority_margin=0.0
    assert off == default == ["A:a1", "B:b1", "A:a2", "B:b2"]


def test_authority_promotes_buried_confident_top():
    # THE branch the old suite never hit. A is iterated first (so A:a1 wins the
    # rank-1 tie under plain RRF), but B is the clearly-confident store. With
    # authority on, B's top is promoted to lead; the prior leader rides behind.
    a = [_hit("A", "a1", 0.60), _hit("A", "a2", 0.58)]   # margin 0.02 - guessing
    b = [_hit("B", "b1", 0.95), _hit("B", "b2", 0.40)]   # margin 0.55 - clear winner
    plain = _order(rrf_fuse({"A": a, "B": b}, k=60))
    assert plain[0] == "A:a1"                             # iteration order leads
    auth = _order(rrf_fuse({"A": a, "B": b}, k=60, authority_margin=0.035))
    assert auth[0] == "B:b1"                              # confident top promoted
    assert auth[1] == "A:a1"                              # context rides right behind


def test_lone_exact_key_hit_leads():
    # The keystone, end to end: a store with a SINGLE exact-key hit (base 1.0 -
    # "here's exactly how it's called") leads the packet even though it arrives
    # alone with no runner-up to form a gap. The most authoritative signal there
    # is must be eligible to lead.
    fact = [_hit("loci", "exact", 1.0)]
    episodes = [_hit("mem", "e1", 0.70), _hit("mem", "e2", 0.66)]
    fused = _order(rrf_fuse({"mem": episodes, "loci": fact}, k=60,
                            authority_margin=0.035))
    assert fused[0] == "loci:exact"


def test_lone_weak_hit_does_not_lead():
    # The other side of the keystone: a lone NON-perfect hit is just sparse, not
    # authoritative, so it abstains and does NOT jump the queue.
    weak = [_hit("loci", "meh", 0.45)]
    episodes = [_hit("mem", "e1", 0.70), _hit("mem", "e2", 0.50)]
    plain = _order(rrf_fuse({"mem": episodes, "loci": weak}, k=60))
    auth = _order(rrf_fuse({"mem": episodes, "loci": weak}, k=60,
                           authority_margin=0.035))
    assert auth == plain
    assert auth[0] == "mem:e1"


def test_exact_key_outranks_merely_confident_store():
    # New precedence: a near-perfect top (exact-key 1.0) is max-confident on its
    # own merit, so it beats a store with a merely-wide gap. "Here's exactly how
    # it's called" leads over "I'm fairly sure about this episode."
    fact = [_hit("loci", "exact", 1.0), _hit("loci", "near", 0.80)]
    episodes = [_hit("mem", "e1", 0.90), _hit("mem", "e2", 0.20)]   # margin 0.70
    fused = _order(rrf_fuse({"mem": episodes, "loci": fact}, k=60,
                            authority_margin=0.035))
    assert fused[0] == "loci:exact"


def test_authority_defers_to_weight_gap():
    # Weight sets the pecking order; authority only breaks a TIE for the lead.
    # If an operator weighted A up enough to STRICTLY outscore the confident
    # store's top, that deliberate trust wins - authority defers.
    a = [_hit("A", "a1", 0.50)]                              # weighted up
    b = [_hit("B", "b1", 0.99), _hit("B", "b2", 0.30)]      # confident (margin 0.69)
    fused = _order(rrf_fuse({"A": a, "B": b}, k=60, weights={"A": 2.0},
                            authority_margin=0.035))
    assert fused[0] == "A:a1"


def test_percentile_mode_orders_by_intra_store_position():
    # percentile: every store's rank-1 == 100, so rank-1s tie and interleave by
    # iteration order; deeper ranks scale by position-within-store.
    a = [_hit("A", "a1", 0.9), _hit("A", "a2", 0.8), _hit("A", "a3", 0.7)]
    b = [_hit("B", "b1", 0.6), _hit("B", "b2", 0.5)]
    fused = _order(rrf_fuse({"A": a, "B": b}, k=60, fusion_mode="percentile"))
    assert fused == ["A:a1", "B:b1", "A:a2", "B:b2", "A:a3"]


def test_percentile_penalizes_small_store_tail_vs_rrf():
    # Documents the known percentile asymmetry (finding #6): a small store's tail
    # ranks BELOW a big store's same-rank hit, because percentile scales by
    # position-within-store. Locked so a future change is a CONSCIOUS one.
    big = [_hit("BIG", f"x{i}", 0.9 - i * 0.05) for i in range(4)]   # pctl 100,75,50,25
    small = [_hit("SML", "s1", 0.8), _hit("SML", "s2", 0.4)]          # pctl 100,50
    rrf = _order(rrf_fuse({"BIG": big, "SML": small}, k=60))
    pct = _order(rrf_fuse({"BIG": big, "SML": small}, k=60, fusion_mode="percentile"))
    # Under RRF, small's rank-2 (s2) beats big's rank-3 (x2) - rank-2 > rank-3.
    assert rrf.index("SML:s2") < rrf.index("BIG:x2")
    # Under percentile they tie at 50, iteration order wins -> small's tail demoted.
    assert pct.index("BIG:x2") < pct.index("SML:s2")


def test_rrf_pct_breaks_ties_on_percentile_not_iteration_order():
    # rrf_pct keeps RRF's rank score but breaks same-rank ties by intra-store
    # percentile instead of store-iteration order. A is iterated first, so plain
    # RRF puts A's rank-2 ahead; rrf_pct flips it because B's rank-2 sits higher
    # within B (B has more hits, so its rank-2 is a better percentile).
    a = [_hit("A", "a1", 0.9), _hit("A", "a2", 0.8)]
    b = [_hit("B", f"b{i}", 0.9 - i * 0.05) for i in range(4)]
    plain = _order(rrf_fuse({"A": a, "B": b}, k=60))
    pct = _order(rrf_fuse({"A": a, "B": b}, k=60, fusion_mode="rrf_pct"))
    assert plain.index("A:a2") < plain.index("B:b1")   # iteration: A's rank-2 first
    assert pct.index("B:b1") < pct.index("A:a2")       # percentile: B's rank-2 first


def test_unknown_fusion_mode_falls_back_to_rrf():
    # A typo'd mode must have ONE defined behavior - rank-only RRF - not a silent
    # surprise. (The config layer additionally warns; the engine just normalizes.)
    a = [_hit("A", "a1", 0.9), _hit("A", "a2", 0.8)]
    b = [_hit("B", "b1", 0.6)]
    expected = _order(rrf_fuse({"A": a, "B": b}, k=60, fusion_mode="rrf"))
    got = _order(rrf_fuse({"A": a, "B": b}, k=60, fusion_mode="nonsense"))
    assert got == expected
    assert "nonsense" not in _VALID_FUSION_MODES


# ═════════════════════════════════════════════════════════════════════════
# Store-class quota - the diversity floor. Guarantees the packet stays a
# briefing (a fact AND a scar) instead of collapsing to one class when a store
# is weighted down or 3+ stores compete for scarce slots. No-op when RRF has
# already balanced (the equal-weight case), which is the correct non-meddling.
# ═════════════════════════════════════════════════════════════════════════


def test_quota_noop_when_rrf_already_balanced():
    # Two equal-weight stores: RRF interleaves their rank-1s into the top two on
    # its own, so the quota changes NOTHING. It must not meddle when balanced.
    mem = [_hit("mem", f"m{i}", 0.9 - i * 0.1) for i in range(3)]
    loci = [_hit("loci", f"l{i}", 0.8 - i * 0.1) for i in range(3)]
    naive = _order(rrf_fuse({"mem": mem, "loci": loci}, k=60, n_results=3, min_per_store=0))
    quota = _order(rrf_fuse({"mem": mem, "loci": loci}, k=60, n_results=3, min_per_store=1))
    assert quota == naive == ["mem:m0", "loci:l0", "mem:m1"]


def test_quota_min_zero_is_plain_top_n():
    # min_per_store<=0 disables the quota - pure top-n trim, a class can drop.
    mem = [_hit("mem", f"m{i}", 0.9 - i * 0.1) for i in range(5)]
    loci = [_hit("loci", "l1", 0.95)]
    got = _order(rrf_fuse({"mem": mem, "loci": loci}, k=60, weights={"mem": 3.0},
                          n_results=3, min_per_store=0))
    assert got == ["mem:m0", "mem:m1", "mem:m2"]   # loci dropped - no quota


def test_quota_rescues_downweighted_store():
    # THE headline win: memory weighted 3x floods naive top-3, burying loci's
    # rank-1 even though it's highly relevant. The quota reserves loci a seat -
    # memory keeps its top 2, a fact is guaranteed present.
    mem = [_hit("mem", f"m{i}", 0.9 - i * 0.1) for i in range(5)]
    loci = [_hit("loci", "l1", 0.95)]
    naive = _order(rrf_fuse({"mem": mem, "loci": loci}, k=60, weights={"mem": 3.0},
                            n_results=3, min_per_store=0))
    assert naive == ["mem:m0", "mem:m1", "mem:m2"]          # loci dropped
    quota = _order(rrf_fuse({"mem": mem, "loci": loci}, k=60, weights={"mem": 3.0},
                            n_results=3, min_per_store=1))
    assert quota == ["mem:m0", "mem:m1", "loci:l1"]          # loci rescued, mem keeps top 2


def test_quota_seats_every_store_when_n_allows():
    # n >= #stores: the guarantee holds. A weighted up floods naive top-3; the
    # quota gives B and C their seats, A yields its 2nd/3rd slots.
    a = [_hit("A", f"a{i}", 0.9 - i * 0.1) for i in range(5)]
    b = [_hit("B", "b1", 0.9)]
    c = [_hit("C", "c1", 0.9)]
    naive = _order(rrf_fuse({"A": a, "B": b, "C": c}, k=60, weights={"A": 3.0},
                            n_results=3, min_per_store=0))
    assert naive == ["A:a0", "A:a1", "A:a2"]
    quota = _order(rrf_fuse({"A": a, "B": b, "C": c}, k=60, weights={"A": 3.0},
                            n_results=3, min_per_store=1))
    assert quota == ["A:a0", "B:b1", "C:c1"]
    assert {h.split(":")[0] for h in quota} == {"A", "B", "C"}   # all seated


def test_quota_best_effort_when_too_few_seats():
    # n < #stores: can't seat everyone. Best-effort in fused-rank order - C
    # (configured last) doesn't make it. Honest limit, not a guarantee.
    a = [_hit("A", "a1", 0.9)]
    b = [_hit("B", "b1", 0.9)]
    c = [_hit("C", "c1", 0.9)]
    quota = _order(rrf_fuse({"A": a, "B": b, "C": c}, k=60, n_results=2, min_per_store=1))
    assert quota == ["A:a1", "B:b1"]
    assert "C:c1" not in quota


def test_quota_preserves_authority_lead():
    # Authority promotes loci's exact-key to lead; the quota trim must keep it
    # first AND still seat the other class.
    mem = [_hit("mem", "m1", 0.70), _hit("mem", "m2", 0.66)]
    loci = [_hit("loci", "exact", 1.0)]
    fused = _order(rrf_fuse({"mem": mem, "loci": loci}, k=60,
                            authority_margin=0.035, n_results=2, min_per_store=1))
    assert fused[0] == "loci:exact"      # authority lead survives the quota
    assert "mem:m1" in fused             # other class still seated


def test_quota_min_per_store_two_seats_two_each():
    # min_per_store=2 reserves two seats per store before filling.
    mem = [_hit("mem", f"m{i}", 0.9 - i * 0.1) for i in range(5)]
    loci = [_hit("loci", f"l{i}", 0.8 - i * 0.1) for i in range(3)]
    quota = _order(rrf_fuse({"mem": mem, "loci": loci}, k=60, weights={"mem": 3.0},
                            n_results=4, min_per_store=2))
    assert quota == ["mem:m0", "mem:m1", "loci:l0", "loci:l1"]


def test_trim_with_quota_helper_edges():
    # Direct helper contract: fewer hits than n -> all returned; min<=0 -> slice.
    fused = rrf_fuse({"A": [_hit("A", "a1", 0.9)], "B": [_hit("B", "b1", 0.8)]},
                     k=60, n_results=10)
    assert len(fused) == 2                      # len <= n: all returned
    assert _trim_with_quota(fused, 5, 1) == fused          # len <= n -> no-op
    assert _trim_with_quota(fused, 1, 0) == fused[:1]      # min<=0 -> plain slice


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
