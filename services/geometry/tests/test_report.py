"""Task 3 outcomes, re-verified on the roomy fixture (the tight plot is retired).

The slicer's ceil-snap + dimension cuts + legal-shape tables make every sliced
room meet its per-room Neufert minimum, so Neufert is a HARD gate with ZERO
violations (incl. the children Bathroom, whose depth ceil-snaps 2.2 -> 2.5 m).

Task 5 note: the footprint packs close to (not exactly at) the 1.15 band ceiling.
Task 3's union tables had opened ~5% of slack (down to ~1.10), but the live
circulation zone adds a rigid corridor to the habitable budget, pushing the
packing back up. The roomy fixture moved to 184 m2 (from 160) so the hard
corridor<->kitchen_laundry wall (solver.py's kitchen-direct constraint) is
satisfiable; on the new target the packing settles at ratio ~1.130 (208 m2),
a bit under the 1.15 ceiling now that the extra footprint itself supplies some
of the slack the corridor used to have to fight for.
"""

import pytest

from app import solver as S
from app.slicer import build_layout
from app.validator import validate


def _solve(program):
    # Phase 4's setback envelope broke the plot's translational symmetry, so the
    # 20x24 roomy solve PROVES optimal in ~1.2 s at workers=1; 12 s is ample.
    return S.solve(program, "gW_eN", seed=1, time_limit_s=12, workers=1)


def _footprint_area(r):
    fx0, fy0, fx1, fy1 = r.footprint_m
    return (fx1 - fx0) * (fy1 - fy0)


@pytest.fixture(scope="session")
def solved(program):
    return _solve(program)


def test_zero_neufert_violations_and_valid(program, solved):
    v = validate(build_layout(solved, program), program)
    assert [e for e in v.errors if "Neufert" in e] == [], v.errors
    assert v.ok, v.errors


def test_footprint_at_the_band_ceiling(program, solved):
    # See module docstring: 208 m2 / 184 m2 target, just under the 1.15 ceiling.
    ratio = _footprint_area(solved) / program.footprint_target_m2
    assert ratio == pytest.approx(208.0 / 184.0), ratio


def test_solver_records_kitchen_cut_axis(program, solved):
    # The slicer no longer infers the cut from _side_of; the solver commits to it.
    assert solved.cut_sides.get("kitchen_laundry") in ("N", "S", "E", "W")


def test_habitable_excludes_only_the_garage(program, solved):
    layout = build_layout(solved, program)
    # program estimate (Task 3): every space target except the garage, by id.
    assert program.habitable_area_m2 == sum(
        s.target_m2 for s in program.spaces if s.id != "garage"
    )
    # as-sliced: the Garage room is excluded, nothing else.
    non_garage = sum(
        (r.rect_m[2] - r.rect_m[0]) * (r.rect_m[3] - r.rect_m[1])
        for r in layout.rooms
        if r.name != "Garage"
    )
    assert layout.habitable_area_m2 == pytest.approx(non_garage)
    assert any(r.name == "Garage" for r in layout.rooms)
    assert layout.habitable_area_m2 < _footprint_area(solved)
