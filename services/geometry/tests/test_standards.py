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
