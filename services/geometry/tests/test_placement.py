"""THE PLACEMENT FILTER, pinned against the same oracle the enumerator is.

app/placement.py restates as explicit predicates what the four bespoke cutters
hold by construction. The one assertion that matters is the same one that made
the enumerator trustworthy: THE CUTTER'S OWN PICK MUST SURVIVE THE FILTER, at
every shape, on every side. A predicate that rejects what the shipping code
builds is a predicate that is wrong -- or a second latent bug, and the entry
Mudroom was already one of those.

Nothing here is wired into slice_zones. The golden cannot move.
"""

import pytest

from app import placement, slicer, subdivide
from app.solver import GRID_M, ZoneRect

COMPOSITES = ("kitchen_laundry", "master_suite", "children", "entry")
PROBE_SIDES = {
    "kitchen_laundry": ("S", "N", "W", "E"),
    "entry": ("S", "N", "W", "E"),
    "master_suite": (None, "N", "S", "E", "W"),
    "children": (None,),
}
ALL_FACES = frozenset({"N", "S", "E", "W"})

# The two rules the SHIPPED plan breaks. Excluded from the "cutter's pick
# survives" oracle and asserted positively in their own test instead -- each is a
# defect this project already tracks with its own xfails, not a filter bug. Do
# not add to this tuple to make something pass.
KNOWN_LIVE_DEFECT_CODES = ("C13", "C18")


def _cut(zone, w, h, side):
    zr = ZoneRect(zone, 0.0, 0.0, w, h)
    if zone == "master_suite":
        return slicer._slice_master(zr, side)
    if zone == "children":
        return slicer._slice_children(zr, side)
    if zone == "kitchen_laundry":
        return slicer._slice_kitchen(zr, side)
    return slicer._slice_entry(zr, side)


def _legal(zone, got):
    return (len(got) == len(slicer.zone_members(zone))
            and all(slicer._in_band(r.name, r.rect) for r in got))


# ---------------------------------------------------------------------------
# the oracle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("zone", COMPOSITES)
def test_the_cutters_pick_always_survives_the_filter(zone):
    """Permissive context: no corridor side, no director side, every face
    exterior. That is what a legal_pairs probe genuinely knows, and it is where
    the structural, ensuite, orientation and no-through rules all still bite."""
    checked = 0
    for wu in slicer._STEPS:
        for hu in slicer._STEPS:
            w, h = wu * GRID_M, hu * GRID_M
            for side in PROBE_SIDES[zone]:
                got = _cut(zone, w, h, side)
                if not _legal(zone, got):
                    continue
                checked += 1
                ctx = placement.Context(zone=zone, exterior_faces=ALL_FACES)
                bad = placement.violations(got, (0.0, 0.0, w, h), ctx)
                assert not bad, f"{zone} {w}x{h} side={side}: {bad}"
    assert checked > 0


def _real_context(r, zone):
    """The zone's rect and the context an actual solve supplies."""
    fx0, fy0, fx1, fy1 = r.footprint_m
    rect = tuple(next(z for z in r.rects if z.zone == zone).rect_m)
    faces = set()
    if abs(rect[3] - fy1) < 1e-6:
        faces.add("N")
    if abs(rect[1] - fy0) < 1e-6:
        faces.add("S")
    if abs(rect[0] - fx0) < 1e-6:
        faces.add("W")
    if abs(rect[2] - fx1) < 1e-6:
        faces.add("E")
    return rect, placement.Context(
        zone=zone,
        corridor_side=r.corridor_sides.get(zone),
        director_side=r.cut_sides.get(zone),
        exterior_faces=frozenset(faces),
    )


@pytest.mark.parametrize("preset", ["gW_eN", "gE_eN"])
def test_the_shipped_cut_survives_the_filter_with_REAL_context(program, preset):
    """The same assertion with the context an actual solve supplies -- which is
    where C1 (corridor-facing room) and C18 (director face) stop being vacuous.

    Rooms come from slice_zones(), i.e. exactly the production call: _slice_kitchen
    takes BOTH the dining side and the corridor side, and on gE_eN they disagree
    (dining E, corridor W), so the corridor override moves the Kitchen west. A
    _slice_probe here would have compared against a cut production never builds.

    C13 and C18 are excluded and checked separately: the SHIPPED PLAN genuinely
    breaks both, and both are already-documented live defects (see
    KNOWN_LIVE_DEFECT_CODES).
    """
    from app.solver import solve

    r = solve(program, preset, seed=1, time_limit_s=20.0, workers=1)
    assert r.feasible, r.status
    rooms = slicer.slice_zones(r)
    for zone in COMPOSITES:
        rect, ctx = _real_context(r, zone)
        cut = [rm for rm in rooms if rm.zone == zone]
        bad = [v for v in placement.violations(cut, rect, ctx)
               if not v.startswith(KNOWN_LIVE_DEFECT_CODES)]
        assert not bad, f"{preset} {zone}: {bad}"


@pytest.mark.parametrize("preset", ["gW_eN", "gE_eN"])
def test_the_filter_independently_reproduces_two_known_live_defects(program, preset):
    """THE TWO RULES THE SHIPPED PLAN BREAKS, and neither is a filter bug. Each
    reproduces, from geometry alone, a defect this project already tracks:

      C13 daylight -> validate() reports `room 'Kitchen' requires an exterior
          wall but has only 0.00 m of true building-perimeter wall`, and
          test_validator.py::test_kitchen_requires_exterior_wall_KNOWN_LIVE_DEFECT
          xfails on it.
      C18 director face -> the LAUNDRY holds the dining face, not the Kitchen.
          solver._force_kitchen_holds_dining_face exists precisely to forbid this
          and is currently OFF (_KITCHEN_DINING_FACE = False); its docstring
          records the same measurement on BOTH presets -- "Laundry<->Dining
          4.00 m, Kitchen<->Dining 0.00 m". validate() warns `expected adjacency
          Kitchen<->Dining not found`, and three tests xfail on it.

    Two independent measurements agreeing is what makes this worth pinning: the
    validator works from the room's true building perimeter and its door graph,
    the filter from which faces of the ZONE the room holds. Both land on the
    Kitchen. If either stops firing the defect was fixed, and its exclusion from
    the oracle above should go with it."""
    from app.solver import solve

    r = solve(program, preset, seed=1, time_limit_s=20.0, workers=1)
    rooms = slicer.slice_zones(r)
    rect, ctx = _real_context(r, "kitchen_laundry")
    cut = [rm for rm in rooms if rm.zone == "kitchen_laundry"]
    vio = placement.violations(cut, rect, ctx)
    assert any(v.startswith("C13") and "Kitchen" in v for v in vio), vio
    assert any(v.startswith("C18") and "Kitchen" in v for v in vio), vio
    # and no OTHER composite zone breaks either rule
    for zone in COMPOSITES:
        if zone == "kitchen_laundry":
            continue
        rect, ctx = _real_context(r, zone)
        cut = [rm for rm in rooms if rm.zone == zone]
        assert not [v for v in placement.violations(cut, rect, ctx)
                    if v.startswith(KNOWN_LIVE_DEFECT_CODES)]


@pytest.mark.parametrize("zone", COMPOSITES)
def test_filter_leaves_at_least_the_cutters_pick(zone):
    """The filter may never empty a shape the cutter can build."""
    rooms = None
    for wu in slicer._STEPS:
        for hu in slicer._STEPS:
            w, h = wu * GRID_M, hu * GRID_M
            for side in PROBE_SIDES[zone]:
                got = _cut(zone, w, h, side)
                if not _legal(zone, got):
                    continue
                if rooms is None:
                    rooms = [subdivide.SubRoom(x.name, x.category) for x in got]
                ctx = placement.Context(zone=zone, exterior_faces=ALL_FACES)
                enum = subdivide.subdivisions((0.0, 0.0, w, h), rooms, zone=zone)
                kept = placement.filter_candidates(enum, (0.0, 0.0, w, h), ctx)
                assert kept, f"{zone} {w}x{h} side={side}: filter emptied the shape"
                assert subdivide.canonical(got) in {
                    subdivide.canonical(c) for c in kept
                }, f"{zone} {w}x{h} side={side}: filter dropped the cutter's pick"


# ---------------------------------------------------------------------------
# negative controls -- a predicate nobody has seen reject anything proves nothing
# ---------------------------------------------------------------------------


def _fr(name, cat, rect):
    return slicer.FinalRoom(name, cat, "kitchen_laundry", rect)


def test_structural_predicates_reject_what_they_are_for():
    rect = (0.0, 0.0, 4.5, 4.0)
    ctx = placement.Context(zone="kitchen_laundry")
    ok = _cut("kitchen_laundry", 4.5, 4.0, "W")
    assert placement.admissible(ok, rect, ctx)

    overlap = [_fr("Kitchen", "wet", (0.0, 0.0, 3.0, 4.0)),
               _fr("Laundry", "service", (2.0, 0.0, 4.5, 4.0))]
    assert any(v.startswith("C8") for v in placement.violations(overlap, rect, ctx))

    void = [_fr("Kitchen", "wet", (0.0, 0.0, 2.5, 4.0)),
            _fr("Laundry", "service", (3.0, 0.0, 4.5, 4.0))]
    assert any(v.startswith("C8") for v in placement.violations(void, rect, ctx))

    offgrid = [_fr("Kitchen", "wet", (0.0, 0.0, 2.4, 4.0)),
               _fr("Laundry", "service", (2.4, 0.0, 4.5, 4.0))]
    assert any(v.startswith("C7") for v in placement.violations(offgrid, rect, ctx))

    outofband = [_fr("Kitchen", "wet", (0.0, 0.0, 0.5, 4.0)),
                 _fr("Laundry", "service", (0.5, 0.0, 4.5, 4.0))]
    assert any(v.startswith(("C10", "C11"))
               for v in placement.violations(outofband, rect, ctx))


def test_ensuite_predicate_rejects_a_detached_ensuite():
    """C2. Master Bathroom and Walk-in Closet are ensuites of the Master Bedroom,
    so a cut that leaves one touching only the other is not a suite."""
    rect = (0.0, 0.0, 6.0, 6.0)
    ctx = placement.Context(zone="master_suite")
    good = slicer._slice_master(ZoneRect("master_suite", 0.0, 0.0, 6.0, 6.0), None)
    assert placement.admissible(good, rect, ctx)
    # Bedroom in the middle band -> Bathroom and Closet both still touch it, so
    # build the failure explicitly: a Closet that only touches the Bathroom.
    bad = [
        slicer.FinalRoom("Master Bathroom", "wet", "master_suite", (0.0, 0.0, 6.0, 2.5)),
        slicer.FinalRoom("Walk-in Closet", "private", "master_suite", (0.0, 2.5, 2.5, 4.5)),
        slicer.FinalRoom("Master Bedroom", "private", "master_suite", (2.5, 2.5, 6.0, 6.0)),
    ]
    vio = placement.violations(bad, rect, ctx)
    assert any(v.startswith(("C2", "C8")) for v in vio), vio


def test_no_through_traffic_predicate_rejects_the_measured_guest_wc_defect():
    """C14/C5, and this is the exact geometry that once made access_tree reach 3
    of 16 rooms: the Guest WC dropped BETWEEN the Mudroom and the Foyer, and the
    WC is no_through_traffic, so the Foyer's own root is severed from it."""
    rect = (0.0, 0.0, 1.5, 7.0)
    ctx = placement.Context(zone="entry")
    good = [
        slicer.FinalRoom("Mudroom", "service", "entry", (0.0, 0.0, 1.5, 2.0)),
        slicer.FinalRoom("Foyer", "circ", "entry", (0.0, 2.0, 1.5, 5.5)),
        slicer.FinalRoom("Guest WC", "wet", "entry", (0.0, 5.5, 1.5, 7.0)),
    ]
    assert placement.admissible(good, rect, ctx), placement.violations(good, rect, ctx)
    severed = [
        slicer.FinalRoom("Mudroom", "service", "entry", (0.0, 0.0, 1.5, 2.0)),
        slicer.FinalRoom("Guest WC", "wet", "entry", (0.0, 2.0, 1.5, 3.5)),
        slicer.FinalRoom("Foyer", "circ", "entry", (0.0, 3.5, 1.5, 7.0)),
    ]
    assert any(v.startswith("C14") for v in placement.violations(severed, rect, ctx))


def test_corridor_and_cb3_predicates_bite_only_with_a_side():
    """C1 and C4 are skipped without context rather than guessed at."""
    rect = (0.0, 0.0, 4.0, 8.0)
    got = slicer._slice_children(ZoneRect("children", 0.0, 0.0, 4.0, 8.0), None)
    assert placement.admissible(got, rect, placement.Context(zone="children"))
    # three full-width horizontal bands: every room holds the W and E faces...
    assert placement.admissible(
        got, rect, placement.Context(zone="children", corridor_side="W"))
    # ...and none but the end bands holds N or S, which is what CB3 forbids there
    vio = placement.violations(
        got, rect, placement.Context(zone="children", corridor_side="N"))
    assert any(v.startswith(("C1", "C4")) for v in vio), vio


def test_orientation_policy_is_explicit_and_narrow():
    """C11. The Bathroom is the ONLY room the shipping code installs rotated
    (slicer._slice_children_ns), so it is the only entry in ROTATABLE. The Garage
    must never be: its own standard says min_h_m 5.0 is the driving length."""
    assert placement.ROTATABLE == frozenset({"Bathroom"})
    # 2.0 wide x 2.5 deep fails the axis-bound Bathroom test (min_w 2.4) but is
    # the same room turned 90 degrees, which _slice_children_ns accepts.
    assert not slicer._room_legal("Bathroom", (0.0, 0.0, 2.0, 2.5))
    assert placement.rotation_would_admit("Bathroom", (0.0, 0.0, 2.0, 2.5))
    # a garage rotated onto its side is too shallow to park in and must stay out
    assert not slicer._room_legal("Garage", (0.0, 0.0, 5.5, 3.0))
    assert "Garage" not in placement.ROTATABLE


def test_guarantee_vector_is_the_band_offsets_and_is_deterministic():
    """C15. The vector is what a solver constraint would reify off instead of a
    Python constant."""
    zr = ZoneRect("kitchen_laundry", 0.0, 0.0, 4.5, 4.0)
    cut = slicer._slice_kitchen(zr, "W")
    gv = placement.guarantee_vector(cut, (0.0, 0.0, 4.5, 4.0))
    assert set(gv) == {"Kitchen", "Laundry"}
    # The Laundry is the east strip: flush with the E face, 2.0 m deep, running
    # its whole 4.0 m. That 2.0 is precisely _force_corridor_overlaps_kitchen's
    # `l_we` -- the constant it reifies the Kitchen band off today.
    assert gv["Laundry"].bands["E"] == (0.0, 2.0, 4.0)
    assert gv["Kitchen"].bands["W"] == (0.0, 2.5, 4.0)
    # and the Kitchen's offset from the E face IS that Laundry depth
    assert gv["Kitchen"].bands["E"][0] == 2.0
    assert placement.guarantee_vector(cut, (0.0, 0.0, 4.5, 4.0)) == gv


def test_the_adopted_tie_break_derives_both_documented_exemplars():
    """tier_tie_break_key is ONE rule for what used to be four disagreeing loop
    orders, and it derives both exemplars that motivated it: _slice_children's
    even split and _split_off_wc's smallest ancillary strip.

    ADOPTED -- slicer._best_cut now uses it as the secondary key. It can only
    reorder candidates the primary key scores IDENTICALLY, and the price was
    measured before adopting: the objective is unchanged to ten decimals
    (625.4109375) on both presets, legal_pairs and cut_penalty_pairs are
    bit-identical, and the only geometry that moves anywhere in the shipped plan
    is Master Bathroom 6.25 <-> Walk-in Closet 7.50 swapping ends of the master
    service strip."""
    even = [
        slicer.FinalRoom("Bedroom 2", "private", "children", (0.0, 0.0, 3.0, 3.75)),
        slicer.FinalRoom("Bathroom", "wet", "children", (0.0, 3.75, 3.0, 5.75)),
        slicer.FinalRoom("Bedroom 3", "private", "children", (0.0, 5.75, 3.0, 9.5)),
    ]
    lopsided = [
        slicer.FinalRoom("Bedroom 2", "private", "children", (0.0, 0.0, 3.0, 3.0)),
        slicer.FinalRoom("Bathroom", "wet", "children", (0.0, 3.0, 3.0, 5.0)),
        slicer.FinalRoom("Bedroom 3", "private", "children", (0.0, 5.0, 3.0, 9.5)),
    ]
    assert placement.tier_tie_break_key(even) < placement.tier_tie_break_key(lopsided)

    small_strip = [
        slicer.FinalRoom("Foyer", "circ", "entry", (0.0, 2.0, 1.5, 5.5)),
        slicer.FinalRoom("Guest WC", "wet", "entry", (0.0, 5.5, 1.5, 7.0)),
    ]
    big_strip = [
        slicer.FinalRoom("Foyer", "circ", "entry", (0.0, 2.0, 1.5, 5.0)),
        slicer.FinalRoom("Guest WC", "wet", "entry", (0.0, 5.0, 1.5, 7.0)),
    ]
    assert (placement.tier_tie_break_key(small_strip)
            < placement.tier_tie_break_key(big_strip))


# ---------------------------------------------------------------------------
# the _slice_entry Mudroom fix
# ---------------------------------------------------------------------------


def test_slice_entry_no_longer_emits_an_out_of_band_mudroom():
    """It used to hand back a 1.5 x 5.5 = 8.25 m2 Mudroom here -- over the
    architect's 8 m2 ceiling and over Neufert's 3.0 aspect cap (3.67)."""
    got = slicer._slice_entry(ZoneRect("entry", 0.0, 0.0, 3.0, 5.5), "W")
    mud = next((r for r in got if r.name == "Mudroom"), None)
    assert mud is None, f"still emitting {mud}"
    assert [r.name for r in got] == ["Foyer"]


def test_the_mudroom_fix_moves_no_legal_shape():
    """_legal_1 already rejected every shape the bug could reach, so the solver's
    table must be identical -- 25 shapes, unchanged members, unchanged band."""
    lp = slicer.legal_pairs("entry")
    assert len(lp) == 25
    assert slicer.zone_members("entry") == ("Mudroom", "Foyer", "Guest WC")
    assert slicer.zone_band("entry") == (8.5, 21.5)
    assert sum(t[-1] for t in slicer.cut_penalty_pairs("entry")) == 74730
