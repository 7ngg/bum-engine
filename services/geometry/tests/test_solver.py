"""Solver output vs the hard acceptance criteria (the solver owns geometry).

Runs against the roomy fixture (the tight plot is retired — see conftest). These
assert HARD constraints — non-overlap, gaps, pins, min-dimensions — which hold in
any FEASIBLE solution, so the roomy plot's time budget (conftest.SOLVE_TIME_S,
feasible-not-proven) is sufficient. Only test_seed_is_deterministic needs the
solve to run to completion, so it uses a full budget.

Only the two entry-NORTH presets (gW_eN, gE_eN) produce a CLEAN valid plan on
roomy once the master bedroom must open off the corridor (Task 5 access fix). The
entry-west presets fail validation (gW_eW strands a child bedroom; gE_eW can only
solve by the retry dropping the master<->kitchen avoid) — see
test_ew_presets_yield_no_valid_layout. The hard-constraint sweeps run over the two
clean presets.
"""

import json
from pathlib import Path

import pytest

from app import geom, solver, standards
from app.models import Program
from app.presets import PRESETS, resolve
from app.slicer import build_layout, legal_pairs
from app.solver import GRID_M, solve

DATA = Path(__file__).resolve().parents[1] / "data"

# The two entry-NORTH presets are the hard-constraint sweep set. They were also,
# until Phase 1, the only presets that packed a CLEAN plan on roomy once the
# master bedroom had to front the corridor (Task 5). Phase 1's area bands lifted
# that for gE_eW, which now validates -- see
# test_ew_presets_yield_no_valid_layout for the measurement. This list is left at
# the two eN presets deliberately: it is the SWEEP set, and widening it would
# change what every hard-constraint test below covers, which is a separate call.
FEASIBLE_PRESETS = ["gW_eN", "gE_eN"]


@pytest.mark.parametrize("preset", FEASIBLE_PRESETS)
def test_feasible_all_presets(program, preset, solve_time_s):
    r = solve(program, preset, seed=1, time_limit_s=solve_time_s)
    assert r.feasible, f"{preset} infeasible"
    # Objective sign carries no meaning (it's a sum of penalty/reward terms
    # scaled by plot_cells, e.g. ADHERE deviation penalties dominate on the
    # 184 m2 fixture) -- only relative comparisons are (see test_adjacency.py).


@pytest.mark.parametrize("preset", ["gW_eW", "gE_eW"])
def test_ew_presets_yield_valid_layouts(program, preset):
    """FULLY LIFTED 2026-08-03 -- BOTH entry-west presets now pack a VALID plan.

    History, because this test has now been re-baselined twice and each move was
    a real change of ground truth, not a drift:

    - ORIGINALLY: with the master bedroom required to open off the corridor,
      neither entry-west preset could pack a clean plan on roomy, so `generate`
      excluded both. The test asserted "nothing invalid leaks out".
    - PHASE 1 (architect area bands): gE_eW started producing a valid plan. The
      test went per-preset -- gE_eW asserted valid, gW_eW kept the negative.
      gW_eW's single remaining error was, verbatim:
          "Kitchen is reached via Living (through-living routing)"
    - THIS COMMIT: the architect re-ruled the through-living check (see
      validator._living_substitutes_for_corridor for his words verbatim). The
      rule is now on the Kitchen's DIRECT access parent, not its ancestor chain,
      and gW_eW's Kitchen opens straight off the Corridor -- Living merely sat
      higher up its tree. So gW_eW's error was the old rule's false positive and
      the plan was valid all along.

    Measured on this commit (roomy @192, seed 1, time_limit_s=15, workers=1):

        gW_eW  OPTIMAL, feasible, validate().ok == True, 16 rooms, no errors
               objective 638.40625, footprint 13.0 x 15.0, void 0.00, coverage
               1.0000, 16/16 rooms reachable, Kitchen's access parent = Corridor
        gE_eW  OPTIMAL, feasible, validate().ok == True, 16 rooms, no errors
               objective 534.65625, footprint 13.0 x 15.5

    Both arms now assert the plan really IS valid rather than silently passing
    an old negative, which is a STRONGER gate than the one it replaces: a
    regression on either preset is visible either way, and a plan that stops
    packing at all no longer hides inside the `if not r.feasible: return`
    escape the negative arm used to need.
    """
    from app.slicer import build_layout as _bl
    from app.validator import validate as _validate

    r = solve(program, preset, seed=1, time_limit_s=15, workers=1)
    assert r.feasible, f"{preset} must stay feasible on roomy"
    res = _validate(_bl(r, program), program)
    assert res.ok, f"{preset} must yield a VALID layout, got {res.errors}"


@pytest.mark.parametrize("preset", FEASIBLE_PRESETS)
def test_no_overlap_and_containment(program, preset, solve_time_s):
    r = solve(program, preset, seed=1, time_limit_s=solve_time_s)
    rects = {z.zone: tuple(z.rect_m) for z in r.rects}
    for a in r.rects:
        assert 0 <= a.x0 < a.x1 <= program.plot.width_m + 1e-6
        assert 0 <= a.y0 < a.y1 <= program.plot.depth_m + 1e-6
    zs = list(rects.values())
    for i in range(len(zs)):
        for j in range(i + 1, len(zs)):
            assert geom.overlap_area(zs[i], zs[j]) < 1e-6


@pytest.mark.parametrize("preset", FEASIBLE_PRESETS)
def test_forbidden_adjacencies_have_gap(program, preset, solve_time_s):
    r = solve(program, preset, seed=1, time_limit_s=solve_time_s)
    rects = {z.zone: tuple(z.rect_m) for z in r.rects}
    assert geom.gap(rects["master_suite"], rects["kitchen_laundry"]) >= 0.5 - 1e-6
    assert geom.gap(rects["garage"], rects["living"]) >= 0.5 - 1e-6


@pytest.mark.parametrize("preset", FEASIBLE_PRESETS)
def test_required_adjacencies_share_wall(program, preset, solve_time_s):
    r = solve(program, preset, seed=1, time_limit_s=solve_time_s)
    rects = {z.zone: tuple(z.rect_m) for z in r.rects}
    assert geom.adjacent(rects["kitchen_laundry"], rects["dining"], 1.5)
    assert geom.adjacent(rects["dining"], rects["living"], 1.5)


@pytest.mark.parametrize("preset", FEASIBLE_PRESETS)
def test_hard_zoning(program, preset, solve_time_s):
    # Pins now anchor to the FOOTPRINT edges, not the plot edges — and under
    # Phase 4 the footprint sits inside a real setback envelope (garden south,
    # street north), so this is literally true.
    spec = resolve(preset)
    r = solve(program, preset, seed=1, time_limit_s=solve_time_s)
    rects = {z.zone: tuple(z.rect_m) for z in r.rects}
    fx0, fy0, fx1, fy1 = r.footprint_m
    assert abs(rects["living"][1] - fy0) < 1e-6  # living on footprint's south edge
    # Task 5 dropped master_suite's south pin so the Master Bedroom can front the
    # Corridor (privacy: it must open off circulation, not sit on the garden wall),
    # so master is NO LONGER on the footprint's south edge — only its north extent
    # is still capped so the suite stays in the garden half.
    assert rects["master_suite"][3] <= fy0 + 0.62 * (fy1 - fy0) + 1e-6
    # garage on the preset's side of the footprint
    if spec.garage_side == "W":
        assert abs(rects["garage"][0] - fx0) < 1e-6
    else:
        assert abs(rects["garage"][2] - fx1) < 1e-6


@pytest.mark.parametrize("preset", FEASIBLE_PRESETS)
def test_min_dimensions_meet_neufert_floor(program, preset, solve_time_s):
    # Task 4b: the brief's declared min is a SOFT preference now — the HARD floor
    # is the Neufert-legal shape (the legal-shape table for a composite zone, the
    # room standard for a simple one). So a composite may come out narrower than
    # the brief asked (a 2.5 m N/S kitchen vs a 4.0 m guess) but never sub-Neufert.
    r = solve(program, preset, seed=1, time_limit_s=solve_time_s)
    for z in r.rects:
        lp = legal_pairs(z.zone)
        if lp:  # composite: (w, h) must be one of the Neufert-legal table shapes
            wu = round((z.x1 - z.x0) / GRID_M)
            hu = round((z.y1 - z.y0) / GRID_M)
            assert any(t[0] == wu and t[1] == hu for t in lp), (z.zone, wu, hu)
        else:  # simple: at least the Neufert room-standard minimum
            zm = standards.zone_minima(z.zone)
            fw = zm.min_w_m if zm else program.space(z.zone).min_w_m
            fh = zm.min_h_m if zm else program.space(z.zone).min_h_m
            assert (z.x1 - z.x0) >= fw - 1e-6, z.zone
            assert (z.y1 - z.y0) >= fh - 1e-6, z.zone


def test_seed_is_deterministic(program):
    # Single-worker CP-SAT is deterministic for a fixed seed. (The production
    # default of 8 workers trades that for speed via a search portfolio.)
    # Phase 4's setback envelope broke the plot's translational symmetry, so both
    # runs PROVE optimal well inside this budget and land on the identical layout.
    a = solve(program, "gW_eN", seed=5, time_limit_s=12, workers=1)
    b = solve(program, "gW_eN", seed=5, time_limit_s=12, workers=1)
    assert [tuple(z.rect_m) for z in a.rects] == [tuple(z.rect_m) for z in b.rects]


def test_tight_is_illegal_brief(tight_program):
    # The original 16x12 brief is retired, illegal two independent ways under
    # Task 5. (1) Site setbacks: front 3 + rear 5 leave only 4 m of build depth
    # on the 12 m plot, far too shallow for the ~11 m+ house — the footprint
    # cannot even fit the envelope. (2) Coverage: its ~168 m2 footprint is ~87%
    # of the 192 m2 plot, over the 0.5 cap (max 96 m2). Either alone makes every
    # preset INFEASIBLE; kept as a guard so nobody relaxes one cause, sees it
    # still fail, and mistakes the other for a regression.
    for preset in PRESETS:
        r = solve(tight_program, preset, seed=1, time_limit_s=12, workers=1)
        assert not r.feasible, (
            f"illegal brief unexpectedly feasible on {preset}: it both exceeds the "
            "0.5 coverage cap AND cannot fit the front+rear setback envelope"
        )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "NEGATIVE CONTROL that lost its power in PHASE 1 (architect area bands) "
        "-- the exact mirror of what happened to "
        "test_validator.py::test_kitchen_direct_constraint_is_load_bearing, which "
        "regained its power in the same commit. This test switches "
        "_CHILD_CENTER_COVER OFF and asserts the hall Bathroom loses its direct "
        "corridor wall, which is how it proved the constraint is what delivers "
        "the guarantee. Under the area bands the band-shaped children zone lands "
        "the Bathroom against the corridor ANYWAY when the constraint is off: "
        "measured on gW_eW, cover OFF, Corridor<->Bathroom 2.00 m, direct == True "
        "(it was 0.50 m when this control was written). "
        "THE GUARANTEE ITSELF IS INTACT -- with the constraint ON, "
        "Corridor<->Bathroom measures 2.00 m on BOTH feasible presets (gW_eN, "
        "gE_eN), and _force_vertical_cover_center is in fact more load-bearing "
        "than ever: it is the proven blocker for the Guest-WC wet-core and "
        "Kitchen<->Dining fixes (see their xfails). It is the CONTROL that is no "
        "longer discriminating, not the constraint. Strict, so if the packing "
        "ever moves back to where switching the cover off strands the Bathroom, "
        "that is the signal to un-xfail this."
    ),
)
def test_children_bathroom_direct_needs_center_cover(roomy_program):
    # Proves _force_vertical_cover_center is LOAD-BEARING, not decorative: with it
    # OFF (children falls back to a plain corridor/entry disjunction) an entry-west
    # handedness stays feasible, but the hall Bathroom loses its direct corridor
    # wall — its only non-through neighbour is no longer the Corridor.
    #
    # VEHICLE CHANGED gE_eW -> gW_eW (the garage-parent "G" commit): G now makes
    # gE_eW INFEASIBLE at SOLVE time, not invalid at validation time — with the
    # garage pinned EAST and entry pinned WEST the two cannot share a wall, so the
    # garage can never reach an allowed ensuite parent (Mudroom/Foyer live inside
    # the entry zone) and the model is genuinely unsatisfiable. That is a correct
    # consequence of the room-level garage guarantee, NOT a workaround for a test
    # failure. gW_eW (garage + entry both WEST) still exercises the exact same
    # center-cover property; measured on roomy, center-cover OFF vs ON, the
    # Corridor<->Bathroom wall is 0.50 m vs 2.50 m — OFF drops it below
    # ACCESS_DOOR_M (0.9 m), ON restores it well past it.
    solver._CHILD_CENTER_COVER = False
    try:
        r = solve(roomy_program, "gW_eW", seed=1, time_limit_s=15, workers=1)
        assert r.feasible, "gW_eW should be feasible once center-cover is relaxed"
        rooms = {rm.name: tuple(rm.rect_m) for rm in build_layout(r, roomy_program).rooms}
        direct = "Corridor" in rooms and geom.adjacent(rooms["Bathroom"], rooms["Corridor"], 0.9)
        assert not direct, "without center-cover the Bathroom should NOT be corridor-direct"
    finally:
        solver._CHILD_CENTER_COVER = True


# --- room-level access hardening: kitchen-direct (K) + garage parent (G) ------
# K and G promote two ZONE-level access guarantees that only held by coincidence
# (a slicer heuristic / the objective's taste) into room-level CP-SAT constraints
# (see _force_corridor_overlaps_kitchen and the garage<->entry attach in
# solver.py). On the roomy 184 m2 fixture BOTH already hold (the fixture packs the
# kitchen_laundry cut on the corridor axis, and the garage already parents the
# Mudroom), so these are regression PINS on the outcome, not exercises of the
# constraints' active paths (K binds only when the corridor is orthogonal to the
# cut; G's stranding only occurs when the garage would attach to circulation) --
# which is exactly what "the sweep proved K and G are free" means. They guard
# against a future repack silently reopening either hole.


@pytest.mark.parametrize("preset", FEASIBLE_PRESETS)
def test_kitchen_corridor_direct_room_level_K(program, preset):
    # K: the corridor's shared segment with the kitchen_laundry zone must land on
    # the KITCHEN room, NOT the Laundry strip. Asserting the Kitchen room (with a
    # Laundry sibling actually present, i.e. the zone really split) is what makes
    # this "not satisfiable by Laundry": a test that only checked the zone would
    # pass with the corridor fronting Laundry. Duplicates the OUTCOME pinned by
    # tests/test_validator.py::test_kitchen_direct_to_corridor_room_level (kept
    # there as the pre-existing 184 pin); this is the separate guard tied to the K
    # constraint, per the commit.
    r = solve(program, preset, seed=1, time_limit_s=12, workers=1)
    assert r.feasible
    rooms = {rm.name: tuple(rm.rect_m) for rm in build_layout(r, program).rooms}
    assert "Kitchen" in rooms and "Laundry" in rooms, "zone must have split into Kitchen + Laundry"
    assert "Corridor" in rooms
    assert geom.adjacent(rooms["Corridor"], rooms["Kitchen"], 0.9), (
        "corridor must share a >=0.9 m wall with the KITCHEN room specifically, "
        "not merely with the kitchen_laundry zone (the Laundry strip)"
    )


@pytest.mark.parametrize("preset", FEASIBLE_PRESETS)
def test_garage_parent_is_mudroom_or_foyer_room_level_G(program, preset):
    # G: the Garage must reach one of its allowed_ensuite_parents (Mudroom/Foyer),
    # room-to-room, >= ACCESS_DOOR_M -- never stranded behind circulation (which
    # the tier-2 rule blocks as a Garage parent). Assert both the geometry and that
    # access_tree actually parents it on one of them.
    from app.validator import ACCESS_DOOR_M, access_tree

    r = solve(program, preset, seed=1, time_limit_s=12, workers=1)
    assert r.feasible
    layout = build_layout(r, program)
    rooms = {rm.name: tuple(rm.rect_m) for rm in layout.rooms}
    assert "Garage" in rooms
    touching = [
        nm for nm in ("Mudroom", "Foyer")
        if nm in rooms and geom.adjacent(rooms["Garage"], rooms[nm], ACCESS_DOOR_M)
    ]
    assert touching, (
        f"Garage must share a >={ACCESS_DOOR_M} m wall with Mudroom or Foyer; touched none"
    )
    names = [rm.name for rm in layout.rooms]
    edges, reached, _root = access_tree(layout.rooms)
    parent_of = {c: p for p, c in edges}
    gidx = names.index("Garage")
    assert gidx in reached, "Garage must be reachable"
    assert names[parent_of[gidx]] in ("Mudroom", "Foyer"), (
        f"Garage's access-tree parent must be Mudroom/Foyer, got {names[parent_of[gidx]]!r}"
    )
