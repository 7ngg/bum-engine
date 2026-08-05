"""THE ORACLE: the four bespoke cutters are the reference the general guillotine
enumerator is pinned to.

app/subdivide.py ships as machinery, not as the production path -- `slice_zones`
still calls `_slice_master` / `_slice_children` / `_slice_kitchen` / `_slice_entry`.
So nothing here can move the golden or the objective. What these tests buy is the
only thing that makes the enumerator trustworthy enough to build on: proof that
over the WHOLE shape table it never loses a subdivision the cutters find, and
that on their own ground it picks exactly what they pick.

Two assertions, and the second is the subtle one:

  SUPERSET  -- every candidate a cutter enumerates at (w, h, side) also appears
               in subdivisions(). If this fails the enumerator is not general and
               replacing a cutter with it would silently drop legal plans.

  PICK      -- _best_cut over the enumerator's candidates RESTRICTED to the ones
               the cutter also produced returns the cutter's own result. The
               restriction is deliberate and is not a weakening: the enumerator
               finds strictly more partitions than any cutter (that is its whole
               purpose), and some of them score BETTER, so comparing over the
               full set would assert the enumerator is no better than the code it
               replaces. What has to hold is that on shared ground the two agree.

TWO FINDINGS CAME OUT OF BUILDING THIS, and both are recorded here rather than
smoothed over, because each is a real property of the existing cutters.

FINDING A -- THE SWEEP RUNS ON LEGAL SHAPES ONLY, and it has to, because
`_slice_entry` DOES NOT BAND-CHECK ITS OWN MUDROOM. It takes
`depth = _ceil_snap(mud.min_w_m)` and emits the strip unconditionally, so at
e.g. a 3.0 x 5.5 entry zone it produces a 1.5 x 5.5 = 8.25 m2 Mudroom, which
breaks the architect's 8 m2 ceiling AND Neufert's 3.0 aspect cap (5.5/1.5 =
3.67). `_legal_1` catches it downstream -- `legal_pairs('entry')` does not
contain that shape, so the solver is never offered it -- which is why this has
never mattered in production. The enumerator filters every leaf through
`_in_band`, so it correctly returns nothing there. Comparing on shapes the
cutter itself emits illegally would be asserting that the enumerator reproduces
a bug.

FINDING B -- THE FOUR CUTTERS DO NOT SHARE A TIE-BREAK RULE, so no single
canonical enumeration order can reproduce all four on `_cut_score` ties.
`_slice_children` prefers the MOST EVEN split (`key=lambda t: (abs(t - mid), t)`,
correct for two beds with equal minimums); `_split_off_wc`, `_slice_kitchen` and
`_slice_master` all prefer the SMALLEST ancillary strip (plain ascending). The
two rules disagree: at entry 1.5 x 7.0 the even rule gives Foyer 4.50 / WC 3.00
and ascending gives Foyer 5.25 / WC 2.25 -- both scoring exactly 1242, because
both rooms are tier 3 and a fixed area split between two rooms of the same tier
has constant weighted excess. So PICK is asserted as: the enumerator's choice
always has the SAME SCORE as the cutter's, and is byte-identical whenever the
minimum is unique. That is not a weakening either -- `cut_penalty_pairs`
tabulates `below + above`, so on a tie the solver's objective is provably
indifferent between the two. Which one ships is a geometry decision a general
subdivider will have to make explicitly, per room role, rather than inherit from
four different loop orders.
"""

import time

import pytest

from app import slicer, subdivide
from app.solver import GRID_M, ZoneRect

COMPOSITES = ("kitchen_laundry", "master_suite", "children", "entry")

# Every side each cutter is actually reached with in production, not just the two
# representatives legal_pairs probes: _slice_master's "N" flip and
# _slice_kitchen's place_side override are real paths and must be covered too.
PROBE_SIDES = {
    "kitchen_laundry": ("S", "N", "W", "E"),
    "entry": ("S", "N", "W", "E"),
    "master_suite": (None, "N", "S", "E", "W"),
    "children": (None,),  # slicer._CHILD_AXIAL is False: side is ignored
}


def _rooms_for(zone: str) -> list[subdivide.SubRoom]:
    """The zone's room list, READ OFF THE REAL CUTTER rather than hand-listed --
    same discipline as slicer.zone_members, and it carries the category too."""
    want = len(slicer.zone_members(zone))
    for wu in slicer._STEPS:
        for hu in slicer._STEPS:
            for side in (slicer._NS_REP, slicer._WE_REP):
                got = slicer._slice_probe(zone, wu * GRID_M, hu * GRID_M, side)
                if len(got) == want:
                    return [subdivide.SubRoom(r.name, r.category) for r in got]
    raise AssertionError(f"no shape cuts {zone} completely")


def _cut(zone: str, w: float, h: float, side: str | None):
    """(the cutter's chosen cut, every candidate it enumerated)."""
    cands: list[list] = []
    orig = slicer._best_cut

    def spy(cs):
        cands.extend(cs)
        return orig(cs)

    zr = ZoneRect(zone, 0.0, 0.0, w, h)
    slicer._best_cut = spy
    try:
        if zone == "master_suite":
            got = slicer._slice_master(zr, side)
        elif zone == "children":
            got = slicer._slice_children(zr, side)
        elif zone == "kitchen_laundry":
            got = slicer._slice_kitchen(zr, side)
        else:
            got = slicer._slice_entry(zr, side)
    finally:
        slicer._best_cut = orig
    # _slice_entry is the one cutter whose _best_cut call does NOT see a whole
    # zone: it carves the Mudroom off first and hands only the REMAINDER to
    # _split_off_wc, so a captured candidate is a partial 2-of-3 cut. Complete
    # each one with whatever rooms the final cut carries that it lacks. That is
    # exact rather than approximate here, because the Mudroom strip is a fixed
    # _ceil_snap depth with no search behind it, so it is identical across every
    # candidate. A no-op for the other three cutters.
    full = []
    for c in cands:
        names = {r.name for r in c}
        full.append(list(c) + [r for r in got if r.name not in names])
    return got, full


def _legal_cut(zone: str, w: float, h: float, side: str | None):
    """The cutter's (pick, candidates) at this shape, or None when the shape is
    not one the solver can select -- a degraded cut, or a cut with an out-of-band
    room (see FINDING A). Candidates are filtered the same way, so a partially
    illegal candidate list never enters the comparison either."""
    got, cands = _cut(zone, w, h, side)
    if len(got) < len(slicer.zone_members(zone)):
        return None
    if not all(slicer._in_band(r.name, r.rect) for r in got):
        return None
    cands = [c for c in cands if all(slicer._in_band(r.name, r.rect) for r in c)]
    return (got, cands) if cands else None


def _sweep(zone: str):
    """One pass over _STEPS^2 x every production side, yielding
    (w, h, side, cutter_pick, cutter_candidates, enumerator_candidates)."""
    rooms = _rooms_for(zone)
    for wu in slicer._STEPS:
        for hu in slicer._STEPS:
            w, h = wu * GRID_M, hu * GRID_M
            enum = None
            for side in PROBE_SIDES[zone]:
                pair = _legal_cut(zone, w, h, side)
                if pair is None:
                    continue
                if enum is None:
                    enum = subdivide.subdivisions((0.0, 0.0, w, h), rooms, zone=zone)
                yield w, h, side, pair[0], pair[1], enum


@pytest.mark.parametrize("zone", COMPOSITES)
def test_enumerator_is_a_superset_of_the_cutter_everywhere(zone):
    checked = 0
    for w, h, side, got, cands, enum in _sweep(zone):
        keys = {subdivide.canonical(c) for c in enum}
        checked += 1
        for cand in cands:
            assert subdivide.canonical(cand) in keys, (
                f"{zone} {w}x{h} side={side}: the cutter produced a candidate the "
                f"enumerator does not: {subdivide.canonical(cand)}"
            )
        assert subdivide.canonical(got) in keys, (
            f"{zone} {w}x{h} side={side}: the cutter's CHOSEN cut is not in the "
            f"enumerator's output"
        )
    assert checked > 0, f"{zone}: the sweep compared nothing"


@pytest.mark.parametrize("zone", COMPOSITES)
def test_best_cut_agrees_with_the_cutter_on_shared_ground(zone):
    """On shared ground the enumerator never picks a WORSE cut, and picks the
    cutter's exact cut whenever the best score is unique. Where it differs, the
    scores are equal and `cut_penalty_pairs` -- which tabulates `below + above`
    -- cannot tell the two apart, so the objective is indifferent. See FINDING B."""
    compared = differed = 0
    for w, h, side, got, cands, enum in _sweep(zone):
        shared_keys = {subdivide.canonical(c) for c in cands}
        shared = [c for c in enum if subdivide.canonical(c) in shared_keys]
        assert shared, f"{zone} {w}x{h} side={side}: no shared candidates"
        pick = slicer._best_cut(shared)
        compared += 1
        s_pick = slicer._cut_score([(r.name, r.rect) for r in pick])
        s_got = slicer._cut_score([(r.name, r.rect) for r in got])
        assert s_pick == s_got, (
            f"{zone} {w}x{h} side={side}: enumerator scored {s_pick}, cutter "
            f"scored {s_got} -- the enumerator picked a DIFFERENT-VALUE cut, "
            f"which is a real divergence rather than a tie"
        )
        if subdivide.canonical(pick) != subdivide.canonical(got):
            differed += 1
            tied = sum(1 for c in shared
                       if slicer._cut_score([(r.name, r.rect) for r in c]) == s_got)
            assert tied > 1, (
                f"{zone} {w}x{h} side={side}: picks differ but the minimum is "
                f"UNIQUE -- {subdivide.canonical(pick)} vs "
                f"{subdivide.canonical(got)}"
            )
    assert compared > 0
    # kitchen_laundry and master_suite must agree exactly everywhere: neither has
    # a tie-break that disagrees with plain ascending order. children and entry do
    # (FINDING B), so they are allowed to differ -- but only on ties, asserted above.
    if zone in ("kitchen_laundry", "master_suite"):
        assert differed == 0, f"{zone}: {differed} shapes disagreed"


def test_the_tie_break_is_exercised_and_agrees():
    """A tie-break that is never hit proves nothing, and only ONE zone hits one:
    master_suite at the shipped 5.5 x 6.0 has two cuts scoring exactly
    (18000, 2880) -- Master Bathroom 6.25 / Closet 7.50 and the two swapped.
    kitchen_laundry, children and entry never produce a scoring tie anywhere in
    _STEPS^2, which is itself worth knowing: for those three the pick is decided
    by score alone and the enumeration order is irrelevant."""
    rooms = _rooms_for("master_suite")
    w, h = 5.5, 6.0
    got, cands = _cut("master_suite", w, h, None)
    scores = [slicer._cut_score([(r.name, r.rect) for r in c]) for c in cands]
    best = min(scores)
    assert scores.count(best) > 1, "expected a real scoring tie at 5.5 x 6.0"
    enum = subdivide.subdivisions((0.0, 0.0, w, h), rooms, zone="master_suite")
    shared_keys = {subdivide.canonical(c) for c in cands}
    shared = [c for c in enum if subdivide.canonical(c) in shared_keys]
    assert subdivide.canonical(slicer._best_cut(shared)) == subdivide.canonical(got)


def test_enumerator_finds_at_least_as_many_subdivisions_as_the_cutter():
    """The measurement this module exists for, as an invariant rather than a
    number: whatever the counts are, the general machinery may never find FEWER
    legal subdivisions than the bespoke code it would replace."""
    for zone in COMPOSITES:
        for w, h, side, _got, cands, enum in _sweep(zone):
            assert len({subdivide.canonical(c) for c in enum}) >= len(
                {subdivide.canonical(c) for c in cands}
            ), f"{zone} {w}x{h} side={side}"


def test_room_list_is_an_input_so_a_missing_member_is_expressible():
    """Part 4 of the Phase 0 audit, as a test. zone_members() derives membership
    as 'the fullest split the cutter can emit', which makes a kitchen with no
    laundry indistinguishable from a zone that is merely too small. The
    enumerator takes the list, so the two cases separate."""
    both = [subdivide.SubRoom("Kitchen", "wet"), subdivide.SubRoom("Laundry", "service")]
    solo = [subdivide.SubRoom("Kitchen", "wet")]
    rect = (0.0, 0.0, 4.5, 4.0)
    assert subdivide.subdivisions(rect, both, zone="kitchen_laundry")
    only = subdivide.subdivisions(rect, solo, zone="kitchen_laundry")
    assert len(only) == 1
    assert only[0][0].name == "Kitchen"
    assert only[0][0].rect == rect
    # and three bedrooms in the children zone -- four leaves, which the bespoke
    # cutter cannot express at all
    three = [
        subdivide.SubRoom("Bedroom 2", "private"),
        subdivide.SubRoom("Bedroom 3", "private"),
        subdivide.SubRoom("Bedroom", "private"),
        subdivide.SubRoom("Bathroom", "wet"),
    ]
    assert subdivide.subdivisions((0.0, 0.0, 4.0, 12.0), three, zone="children")


def test_subdivisions_is_deterministic():
    """The build-time/slice-time contract rests on this: same arguments, same
    order, every time."""
    rooms = _rooms_for("master_suite")
    rect = (0.0, 0.0, 5.5, 6.0)
    a = [subdivide.canonical(c) for c in subdivide.subdivisions(rect, rooms)]
    b = [subdivide.canonical(c) for c in subdivide.subdivisions(rect, rooms)]
    assert a == b
    assert len(set(a)) == len(a), "subdivisions() returned a duplicate partition"


# ---------------------------------------------------------------------------
# the determinism assertion itself (slicer._penalty_disagreement)
# ---------------------------------------------------------------------------


def test_penalty_disagreement_is_silent_on_a_real_solve(program, solve_time_s):
    from app.solver import solve

    r = solve(program, "gW_eN", seed=1, time_limit_s=solve_time_s, workers=1)
    by = {z.zone: z for z in r.rects}
    sides = {"kitchen_laundry": r.cut_sides.get("kitchen_laundry"),
             "entry": r.cut_sides.get("entry"),
             "children": r.corridor_sides.get("children"),
             "master_suite": None}
    for zone in COMPOSITES:
        zr = by[zone]
        cut = slicer._slice_probe(
            zone,
            zr.rect_m[2] - zr.rect_m[0],
            zr.rect_m[3] - zr.rect_m[1],
            sides[zone] or slicer._WE_REP,
        )
        assert slicer._penalty_disagreement(zone, zr, cut, sides[zone]) is None


def test_penalty_disagreement_actually_fires_when_the_cut_is_wrong():
    """A check nobody has seen fail is a check nobody knows works. Hand it a cut
    that is NOT the one tabulated for that shape and it must say so."""
    zr = ZoneRect("kitchen_laundry", 0.0, 0.0, 4.5, 4.0)
    real = slicer._slice_kitchen(zr, "W")
    assert slicer._penalty_disagreement("kitchen_laundry", zr, real, "W") is None
    wrong = [
        slicer.FinalRoom("Kitchen", "wet", "kitchen_laundry", (0.0, 0.0, 2.0, 4.0)),
        slicer.FinalRoom("Laundry", "service", "kitchen_laundry", (2.0, 0.0, 4.5, 4.0)),
    ]
    msg = slicer._penalty_disagreement("kitchen_laundry", zr, wrong, "W")
    assert msg is not None and "tabulated cut penalty" in msg


def test_penalty_disagreement_is_cheap():
    """It runs once per composite zone per slice_zones() call, so it has to be
    negligible against the ~0.9 s the shape tables already cost to build."""
    zr = ZoneRect("kitchen_laundry", 0.0, 0.0, 4.5, 4.0)
    cut = slicer._slice_kitchen(zr, "W")
    slicer._penalty_disagreement("kitchen_laundry", zr, cut, "W")  # warm the cache
    t0 = time.perf_counter()
    for _ in range(1000):
        slicer._penalty_disagreement("kitchen_laundry", zr, cut, "W")
    per_call_us = (time.perf_counter() - t0) * 1000.0
    assert per_call_us < 200.0, f"{per_call_us:.1f} us/call"
