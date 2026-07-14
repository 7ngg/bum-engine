"""Solver output vs the hard acceptance criteria (the solver owns geometry).

Runs against both fixtures (tight + roomy). These assert HARD constraints —
non-overlap, gaps, pins, min-dimensions — which hold in any FEASIBLE solution,
so the roomy plot's low time budget (conftest.SOLVE_TIME_S, feasible-not-proven)
is sufficient. Only test_seed_is_deterministic needs the solve to run to
completion, so it uses a full budget.
"""

import pytest

from app import geom
from app.presets import PRESETS, resolve
from app.solver import solve


@pytest.mark.parametrize("preset", PRESETS)
def test_feasible_all_presets(program, preset, solve_time_s):
    r = solve(program, preset, seed=1, time_limit_s=solve_time_s)
    assert r.feasible, f"{preset} infeasible"
    assert r.objective > 0


@pytest.mark.parametrize("preset", PRESETS)
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


@pytest.mark.parametrize("preset", PRESETS)
def test_forbidden_adjacencies_have_gap(program, preset, solve_time_s):
    r = solve(program, preset, seed=1, time_limit_s=solve_time_s)
    rects = {z.zone: tuple(z.rect_m) for z in r.rects}
    assert geom.gap(rects["master_suite"], rects["kitchen_laundry"]) >= 0.5 - 1e-6
    assert geom.gap(rects["garage"], rects["living"]) >= 0.5 - 1e-6


@pytest.mark.parametrize("preset", PRESETS)
def test_required_adjacencies_share_wall(program, preset, solve_time_s):
    r = solve(program, preset, seed=1, time_limit_s=solve_time_s)
    rects = {z.zone: tuple(z.rect_m) for z in r.rects}
    assert geom.adjacent(rects["kitchen_laundry"], rects["dining"], 1.5)
    assert geom.adjacent(rects["dining"], rects["living"], 1.5)


@pytest.mark.parametrize("preset", PRESETS)
def test_hard_zoning(program, preset, solve_time_s):
    # Pins now anchor to the FOOTPRINT edges, not the plot edges (the house
    # sits inside the plot with setback around it).
    spec = resolve(preset)
    r = solve(program, preset, seed=1, time_limit_s=solve_time_s)
    rects = {z.zone: tuple(z.rect_m) for z in r.rects}
    fx0, fy0, fx1, fy1 = r.footprint_m
    assert abs(rects["living"][1] - fy0) < 1e-6  # living on footprint's south edge
    assert abs(rects["master_suite"][1] - fy0) < 1e-6
    assert rects["master_suite"][3] <= fy0 + 0.62 * (fy1 - fy0) + 1e-6
    # garage on the preset's side of the footprint
    if spec.garage_side == "W":
        assert abs(rects["garage"][0] - fx0) < 1e-6
    else:
        assert abs(rects["garage"][2] - fx1) < 1e-6


@pytest.mark.parametrize("preset", PRESETS)
def test_min_dimensions(program, preset, solve_time_s):
    r = solve(program, preset, seed=1, time_limit_s=solve_time_s)
    for z in r.rects:
        sp = program.space(z.zone)
        assert (z.x1 - z.x0) >= sp.min_w_m - 1e-6
        assert (z.y1 - z.y0) >= sp.min_h_m - 1e-6


def test_seed_is_deterministic(program):
    # Single-worker CP-SAT is deterministic for a fixed seed. (The production
    # default of 8 workers trades that for speed via a search portfolio.)
    # Needs the solve to run to completion to be reproducible, so it uses a full
    # budget rather than the roomy cap — both runs reach the same optimum.
    a = solve(program, "gW_eN", seed=5, time_limit_s=12, workers=1)
    b = solve(program, "gW_eN", seed=5, time_limit_s=12, workers=1)
    assert [tuple(z.rect_m) for z in a.rects] == [tuple(z.rect_m) for z in b.rects]
