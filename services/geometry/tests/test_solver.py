"""Solver output vs the hard acceptance criteria (the solver owns geometry)."""

import pytest

from app import geom
from app.presets import PRESETS, resolve
from app.solver import solve


@pytest.mark.parametrize("preset", PRESETS)
def test_feasible_all_presets(program, preset):
    r = solve(program, preset, seed=1, time_limit_s=12)
    assert r.feasible, f"{preset} infeasible"
    assert r.objective > 0


@pytest.mark.parametrize("preset", PRESETS)
def test_no_overlap_and_containment(program, preset):
    r = solve(program, preset, seed=1, time_limit_s=12)
    rects = {z.zone: tuple(z.rect_m) for z in r.rects}
    for a in r.rects:
        assert 0 <= a.x0 < a.x1 <= program.plot.width_m + 1e-6
        assert 0 <= a.y0 < a.y1 <= program.plot.depth_m + 1e-6
    zs = list(rects.values())
    for i in range(len(zs)):
        for j in range(i + 1, len(zs)):
            assert geom.overlap_area(zs[i], zs[j]) < 1e-6


@pytest.mark.parametrize("preset", PRESETS)
def test_forbidden_adjacencies_have_gap(program, preset):
    r = solve(program, preset, seed=1, time_limit_s=12)
    rects = {z.zone: tuple(z.rect_m) for z in r.rects}
    assert geom.gap(rects["master_suite"], rects["kitchen_laundry"]) >= 0.5 - 1e-6
    assert geom.gap(rects["garage"], rects["living"]) >= 0.5 - 1e-6


@pytest.mark.parametrize("preset", PRESETS)
def test_required_adjacencies_share_wall(program, preset):
    r = solve(program, preset, seed=1, time_limit_s=12)
    rects = {z.zone: tuple(z.rect_m) for z in r.rects}
    assert geom.adjacent(rects["kitchen_laundry"], rects["dining"], 1.5)
    assert geom.adjacent(rects["dining"], rects["living"], 1.5)


@pytest.mark.parametrize("preset", PRESETS)
def test_hard_zoning(program, preset):
    spec = resolve(preset)
    r = solve(program, preset, seed=1, time_limit_s=12)
    rects = {z.zone: tuple(z.rect_m) for z in r.rects}
    assert rects["living"][1] == 0.0  # living on south edge
    assert rects["master_suite"][1] == 0.0
    assert rects["master_suite"][3] <= 0.62 * program.plot.depth_m + 1e-6
    # garage side per preset
    if spec.garage_side == "W":
        assert rects["garage"][0] == 0.0
    else:
        assert rects["garage"][2] == program.plot.width_m


@pytest.mark.parametrize("preset", PRESETS)
def test_min_dimensions(program, preset):
    r = solve(program, preset, seed=1, time_limit_s=12)
    for z in r.rects:
        sp = program.space(z.zone)
        assert (z.x1 - z.x0) >= sp.min_w_m - 1e-6
        assert (z.y1 - z.y0) >= sp.min_h_m - 1e-6


def test_seed_is_deterministic(program):
    # Single-worker CP-SAT is deterministic for a fixed seed. (The production
    # default of 8 workers trades that for speed via a search portfolio.)
    a = solve(program, "gW_eN", seed=5, time_limit_s=12, workers=1)
    b = solve(program, "gW_eN", seed=5, time_limit_s=12, workers=1)
    assert [tuple(z.rect_m) for z in a.rects] == [tuple(z.rect_m) for z in b.rects]
