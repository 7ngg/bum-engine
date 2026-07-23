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

# Only the two entry-NORTH presets pack a CLEAN plan on roomy once the master
# bedroom must front the corridor (Task 5): the hard-constraint sweeps run over
# those. The entry-west presets yield no valid layout (see
# test_ew_presets_yield_no_valid_layout).
FEASIBLE_PRESETS = ["gW_eN", "gE_eN"]


@pytest.mark.parametrize("preset", FEASIBLE_PRESETS)
def test_feasible_all_presets(program, preset, solve_time_s):
    r = solve(program, preset, seed=1, time_limit_s=solve_time_s)
    assert r.feasible, f"{preset} infeasible"
    # Objective sign carries no meaning (it's a sum of penalty/reward terms
    # scaled by plot_cells, e.g. ADHERE deviation penalties dominate on the
    # 184 m2 fixture) -- only relative comparisons are (see test_adjacency.py).


@pytest.mark.parametrize("preset", ["gW_eW", "gE_eW"])
def test_ew_presets_yield_no_valid_layout(program, preset):
    # The entry-west presets cannot pack a clean plan on roomy now that the master
    # bedroom must open off the corridor: gW_eW strands a child bedroom
    # (unreachable under the privacy rule); gE_eW can only "solve" by the retry
    # dropping the master<->kitchen avoid. Either way validation fails, so generate
    # excludes them. Documented limitation, asserted so a future change that
    # accidentally admits an invalid _eW plan is caught.
    from app.slicer import build_layout as _bl
    from app.validator import validate as _validate

    r = solve(program, preset, seed=1, time_limit_s=15, workers=1)
    if not r.feasible:
        return  # infeasible is itself a clean "no invalid layout leaks out"
    assert not _validate(_bl(r, program), program).ok


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


def test_children_bathroom_direct_needs_center_cover(roomy_program):
    # Proves _force_vertical_cover_center is LOAD-BEARING, not decorative: with it
    # OFF (children falls back to a plain corridor/entry disjunction) the gE_eW
    # handedness becomes feasible, but the hall Bathroom loses its direct corridor
    # wall — its only non-through neighbour is no longer the Corridor. That is why
    # gE_eW is xfailed rather than "fixed" by relaxing the constraint.
    solver._CHILD_CENTER_COVER = False
    try:
        r = solve(roomy_program, "gE_eW", seed=1, time_limit_s=15, workers=1)
        assert r.feasible, "gE_eW should be feasible once center-cover is relaxed"
        rooms = {rm.name: tuple(rm.rect_m) for rm in build_layout(r, roomy_program).rooms}
        direct = "Corridor" in rooms and geom.adjacent(rooms["Bathroom"], rooms["Corridor"], 0.9)
        assert not direct, "without center-cover the Bathroom should NOT be corridor-direct"
    finally:
        solver._CHILD_CENTER_COVER = True
