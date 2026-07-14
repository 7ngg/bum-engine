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
}


def test_standards_cover_every_room_name_slicer_can_emit():
    assert set(standards.ROOMS) == EXPECTED_ROOM_NAMES


def test_standards_are_positive_and_internally_consistent():
    for name, spec in standards.ROOMS.items():
        assert spec.min_w_m > 0, name
        assert spec.min_h_m > 0, name
        assert spec.min_area_m2 > 0, name
        assert spec.max_aspect >= 1.0, name


def test_neufert_warnings_fire_on_todays_output(program):
    # gW_eN seed=1 workers=1: deterministic within this environment. The
    # violating rooms below come from the entry/children zones, which are
    # stable across the (documented, pre-existing, environment-dependent)
    # tie-break variance in the living/dining/master_suite cluster.
    r = solve(program, "gW_eN", seed=1, time_limit_s=12, workers=1)
    layout = build_layout(r, program)
    v = validate(layout, program)

    assert v.ok, "Neufert checks are warnings only; must not affect the hard gate"

    violating_rooms = set()
    for w in v.warnings:
        for name in standards.ROOMS:
            if f"room {name!r}" in w:
                violating_rooms.add(name)

    assert {"Bathroom", "Mudroom", "Foyer"} <= violating_rooms, (
        f"expected today's known slivers among the warnings, got: {v.warnings}"
    )
