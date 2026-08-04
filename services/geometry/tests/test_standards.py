"""standards.py coverage + the Neufert-warnings baseline (instrumentation
only in this task — validator.py must keep passing today's output; these are
warnings, not rejects, and this is the baseline Task 2 improves against)."""

from app import standards
from app.slicer import build_layout
from app.solver import solve
from app.validator import validate

EXPECTED_ROOM_NAMES = {
    "Living", "Dining", "Kitchen", "Laundry",
    "Master Bedroom", "Master Bathroom", "Walk-in Closet",
    "Bedroom", "Bedroom 2", "Bedroom 3", "Children Bedroom", "Bathroom",
    "Office", "Foyer", "Mudroom", "Garage", "Corridor",
    # The уборная, emitted by slicer._slice_entry as the entry zone's third
    # room. SNiP 2.08.01-89 Posobie, "Санитарные узлы", cl. 3.5.
    "Guest WC",
}


def test_standards_cover_every_room_name_slicer_can_emit():
    assert set(standards.ROOMS) == EXPECTED_ROOM_NAMES


def test_standards_are_positive_and_internally_consistent():
    for name, spec in standards.ROOMS.items():
        assert spec.min_w_m > 0, name
        assert spec.min_h_m > 0, name
        assert spec.min_area_m2 > 0, name
        assert spec.max_aspect >= 1.0, name


def test_no_neufert_violations_after_slicer_fix(program, solve_time_s):
    # Task 3: the slicer's ceil-snap + dimension cuts + legal-shape tables make
    # every sliced room meet its per-room Neufert minimum, so the validator's
    # Neufert checks are now HARD errors and produce ZERO violations — including
    # the children Bathroom (depth ceil-snapped 2.2 -> 2.5). Holds on both plots.
    r = solve(program, "gW_eN", seed=1, time_limit_s=solve_time_s, workers=1)
    layout = build_layout(r, program)
    v = validate(layout, program)

    neufert_errors = [e for e in v.errors if "Neufert" in e]
    assert neufert_errors == [], neufert_errors
    assert v.ok, v.errors


# ---------------------------------------------------------------------------
# THE ARCHITECT'S THREE-VALUE MODEL AND DISTRIBUTION PRIORITY (round 4,
# 2026-08-04). See standards.py for both rulings verbatim.
# ---------------------------------------------------------------------------


def test_every_room_has_a_tier_and_it_is_one_of_his_three():
    for name in standards.ROOMS:
        assert standards.priority_tier(name) in (1, 2, 3), name


def test_his_named_rooms_are_in_the_tier_he_named_them_in():
    # Ruling 2, verbatim. These are the only room assignments he actually made;
    # everything else falls to tier 3 through his own catch-all ("and other
    # ancillary rooms"), except Dining -- see PRIORITY_TIER's note, which is the
    # one inference in the table and a question for him.
    for name in ("Kitchen", "Living", "Master Bedroom"):
        assert standards.priority_tier(name) == 1, name
    for name in ("Bedroom 2", "Bedroom 3", "Office"):
        assert standards.priority_tier(name) == 2, name
    for name in ("Laundry", "Walk-in Closet"):
        assert standards.priority_tier(name) == 3, name


def test_ideal_lies_inside_the_architects_own_band():
    # THE guarantee that makes this safe to put in the objective where Phase 1's
    # adherence term was not. A target outside the room's legal interval is a
    # penalty no solution can avoid -- a constant with a weight on it, which is
    # exactly what the deleted term was (solver.py records the measurement).
    for name in standards.ROOMS:
        floor, ideal, ceiling = (
            standards.area_floor(name),
            standards.area_ideal(name),
            standards.area_ceiling(name),
        )
        assert floor <= ideal <= ceiling, (name, floor, ideal, ceiling)


def test_tier_3_ideal_is_its_floor():
    # "Tier 3 (kept at minimum)" -- his wording, so IDEAL_BAND_FRACTION[3] is
    # not a tunable. Quantising to the 0.25 m2 grid cell may round a floor that
    # is itself off-quantum UP (Garage 29.20 -> 29.25, Walk-in Closet 4.20 ->
    # 4.25); that is the smallest area the grid can actually build, so it is
    # still "at minimum". Nothing may exceed the floor by a whole cell.
    for name in standards.ROOMS:
        if standards.priority_tier(name) == 3:
            assert standards.area_ideal(name) - standards.area_floor(name) < 0.25, name


def test_weight_ordering_properties_that_the_objective_depends_on():
    B, A = standards.TIER_W_BELOW, standards.TIER_W_ABOVE
    # 1. surplus fills tier 1 first (Ruling 2's priority order)
    assert B[1] > B[2] > B[3] > 0
    # 2. whatever is left after everyone reaches ideal settles on tier 1, not on
    #    the Laundry -- which is Ruling 2's actual complaint
    assert 0 < A[1] < A[2] < A[3]
    # 3. requirement (d): a tier-1 room is never starved to trim a tier-3 room's
    #    excess
    assert min(B.values()) > max(A.values())
    # 4. the term REDISTRIBUTES the house, it never inflates it: one cell of
    #    footprint growth costs the fp_dev penalty (3*plot_cells) less the
    #    coverage reward (12*100), and no shortfall weight may outbid that.
    #    MEASURED at 5500 on roomy @192: the footprint goes 201.50 -> 208.00 m2
    #    (42.0% -> 43.3% site coverage), trading away his round-3 coverage
    #    figure to buy tier-1 area.
    assert max(B.values()) < 3 * 1920 - 12 * 100


def test_every_composite_zone_has_a_shape_that_reaches_every_room_ideal():
    # The reachability guarantee the objective term rests on: for each composite
    # zone SOME legal shape cuts with ZERO tier-weighted shortfall, so no zone is
    # penalised for a size a hard constraint forced on it. That is the one
    # property Phase 1's deleted adherence term did not have -- master_suite's
    # Neufert-legal floor exceeded its reconciled target, so it paid at every
    # feasible setting (see solver.py's record of the measurement).
    from app import slicer
    from app.solver import GRID_M

    for zone in ("kitchen_laundry", "master_suite", "children", "entry"):
        pairs = slicer.cut_penalty_pairs(zone)
        assert pairs, zone
        zero = []
        for t in pairs:
            side = (slicer._NS_REP if t[2] else slicer._WE_REP) if len(t) == 4 else None
            rooms = slicer._slice_probe(zone, t[0] * GRID_M, t[1] * GRID_M, side)
            below, _above = slicer._cut_score([(r.name, r.rect) for r in rooms])
            if below == 0:
                zero.append(t[0] * t[1] * GRID_M * GRID_M)
        assert zero, f"{zone}: no legal shape reaches every member's ideal"
        # and that shape sits inside the zone's own architect band
        band = slicer.zone_band(zone)
        assert band[0] <= min(zero) <= band[1], (zone, min(zero), band)


def test_non_composite_zone_ideal_is_the_rooms_own_ideal():
    from app import slicer

    for zone, room in (("living", "Living"), ("dining", "Dining"),
                       ("office", "Office"), ("circulation", "Corridor")):
        assert slicer.zone_ideal(zone) == standards.area_ideal(room), zone
    # composites are scored per SHAPE, not per area -- see cut_penalty_pairs
    for zone in ("kitchen_laundry", "master_suite", "children", "entry"):
        assert slicer.zone_ideal(zone) is None, zone
