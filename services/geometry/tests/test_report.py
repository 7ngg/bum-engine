"""Task 2.5 cross-fixture findings, locked as regression tests.

The over-constrained tight plot (16x12) and the roomy plot (20x15) share an
IDENTICAL program (same targets, footprint_target_m2=160). Running the same
asserts on both separates fixture artifacts from real code behaviour:

  - The footprint CANNOT reach its 160 m2 target on either plot: the eight
    Neufert-legal zone minima only tile a rectangle >= ~176 m2 (1.10x target).
    That floor is plot-independent, so it is REAL, not a tight-plot artifact.
  - The children zone sits at its AREA_LO area-window floor (~0.86x) on both.
  - The children Bathroom's 2.0 m depth (< 2.2 Neufert) survives on both — a
    slicer cut-fraction defect (Task 3), independent of plot and zone size.
"""

import pytest

from app import solver as S
from app.slicer import build_layout
from app.validator import validate


def _solve(program):
    # Full budget + workers=1: these tests read EXACT optimum values, so the
    # roomy solve must run to OPTIMAL (~10s), not the geometry-invariant cap.
    return S.solve(program, "gW_eN", seed=1, time_limit_s=12, workers=1)


def _footprint_area(r):
    fx0, fy0, fx1, fy1 = r.footprint_m
    return (fx1 - fx0) * (fy1 - fy0)


@pytest.fixture(scope="session")
def solved(program):
    # One solve per fixture, shared by the three value tests below.
    return _solve(program)


def test_footprint_cannot_reach_target_on_either_plot(program, solved):
    # >= 1.10x target on both: the packing floor, not the plot, is binding.
    ratio = _footprint_area(solved) / program.footprint_target_m2
    assert ratio >= 1.10 - 1e-6, ratio


def test_children_zone_sits_at_its_area_floor(program, solved):
    # children is the most area-expensive zone (tallest min_h), so the objective
    # shrinks it to AREA_LO (0.85x). Same on both plots -> not a fixture artifact.
    child = next(z for z in solved.rects if z.zone == "children")
    achieved = (child.x1 - child.x0) * (child.y1 - child.y0)
    ratio = achieved / program.space("children").target_m2
    assert 0.84 <= ratio <= 0.88, ratio


def test_only_children_bathroom_neufert_warning_survives(program, solved):
    # The master/entry composite slivers are fixed (Task 2); what remains on BOTH
    # plots is the children Bathroom depth — slicer.py's 0.24 cut fraction.
    v = validate(build_layout(solved, program), program)
    neufert = [w for w in v.warnings if "Neufert" in w]
    assert len(neufert) == 1, neufert
    assert "Bathroom" in neufert[0] and "depth" in neufert[0], neufert


@pytest.mark.parametrize("cap,feasible_expected", [(1.10, True), (1.075, False)])
def test_packing_floor_is_176_on_both_plots(program, monkeypatch, cap, feasible_expected):
    # The real feasibility floor: a footprint capped at 1.10x target (176 m2)
    # still tiles; at 1.075x (172 m2) it does not. Identical on both plots, so
    # "176 m2 is infeasible" is false — 176 is the FLOOR, not a wall.
    monkeypatch.setattr(S, "FOOTPRINT_HI", cap)
    r = _solve(program)
    assert r.feasible is feasible_expected, (cap, r.status)
