"""Envelope quality: the plan must tile its footprint exactly.

The architect reported "lazimsiz girinti cixintilar" (unnecessary recesses and
protrusions) twice. The cause was solver.COVERAGE_MIN being a coverage FRACTION
floor (0.95) rather than a tiling constraint: it permitted up to 5% of the
footprint to be dead area, in any shape, anywhere — interior pockets AND notches
bitten out of the facade. At 184 m2 that was 9.75 m2 in 4 disconnected
components, including the notch beside the front door that made the entrance
read as recessed.

COVERAGE_MIN is now 1.00 (exact tiling) and AREA_HI is 1.50 — the ceiling that
was the real blocker. These tests pin the outcome, not the mechanism, so they
stay meaningful if the mechanism is ever reworked.
"""

import pytest

from app import solver as S
from app.slicer import build_layout
from app.solver import solve

PRESETS = ["gW_eN", "gE_eN"]
G = S.GRID_M

# The two-car functional minimum the roomy brief implies (garage min_w 5.4 x
# min_h 5.4). Exact tiling shrinks the garage off its 36 m2 target to pay for
# the tiling; it must never shrink past being a double garage. The rejected
# [0.70, 1.25] band put it at 25.0 m2, which is why that band was not shipped.
GARAGE_TWO_CAR_MIN_M2 = 29.2


def _rooms(roomy_program, preset):
    r = solve(roomy_program, preset, seed=1, time_limit_s=12, workers=1)
    assert r.feasible, f"{preset} infeasible"
    return [tuple(rm.rect_m) for rm in build_layout(r, roomy_program).rooms]


def _footprint(rects):
    return (min(r[0] for r in rects), min(r[1] for r in rects),
            max(r[2] for r in rects), max(r[3] for r in rects))


def _occupied(rects):
    """Grid cells covered by some room, at GRID_M."""
    cells = set()
    for x0, y0, x1, y1 in rects:
        for i in range(int(round(x0 / G)), int(round(x1 / G))):
            for j in range(int(round(y0 / G)), int(round(y1 / G))):
                cells.add((i, j))
    return cells


@pytest.mark.parametrize("preset", PRESETS)
def test_no_unassigned_area_inside_the_footprint(roomy_program, preset):
    """Zero void: every cell of the footprint belongs to a room."""
    rects = _rooms(roomy_program, preset)
    fx0, fy0, fx1, fy1 = _footprint(rects)
    occ = _occupied(rects)
    empty = [
        (i, j)
        for i in range(int(round(fx0 / G)), int(round(fx1 / G)))
        for j in range(int(round(fy0 / G)), int(round(fy1 / G)))
        if (i, j) not in occ
    ]
    assert not empty, (
        f"{preset}: {len(empty) * G * G} m2 of unassigned area inside the "
        f"footprint at cells {sorted(empty)[:12]}"
    )


@pytest.mark.parametrize("preset", PRESETS)
def test_envelope_is_a_clean_rectangle(roomy_program, preset):
    """No notches: every unit segment of all four footprint edges is covered.

    Implied by zero void, but asserted separately because it is the property the
    architect actually sees — a ragged outline reads as a defect in any drawing,
    and this fails with a message naming the offending facade.
    """
    rects = _rooms(roomy_program, preset)
    fx0, fy0, fx1, fy1 = _footprint(rects)
    occ = _occupied(rects)
    i0, j0 = int(round(fx0 / G)), int(round(fy0 / G))
    i1, j1 = int(round(fx1 / G)), int(round(fy1 / G))

    edges = {
        "S": [(i, j0) for i in range(i0, i1)],
        "N": [(i, j1 - 1) for i in range(i0, i1)],
        "W": [(i0, j) for j in range(j0, j1)],
        "E": [(i1 - 1, j) for j in range(j0, j1)],
    }
    ragged = {
        name: [c for c in cells if c not in occ]
        for name, cells in edges.items()
        if any(c not in occ for c in cells)
    }
    assert not ragged, f"{preset}: facade notches on {ragged}"


@pytest.mark.parametrize("preset", PRESETS)
def test_garage_stays_a_double_garage(roomy_program, preset):
    """Exact tiling must not pay for itself by shrinking the garage below two cars."""
    rects = _rooms(roomy_program, preset)
    layout_rooms = {
        rm.name: tuple(rm.rect_m)
        for rm in build_layout(
            solve(roomy_program, preset, seed=1, time_limit_s=12, workers=1), roomy_program
        ).rooms
    }
    x0, y0, x1, y1 = layout_rooms["Garage"]
    area = (x1 - x0) * (y1 - y0)
    assert area >= GARAGE_TWO_CAR_MIN_M2, (
        f"{preset}: garage {area:.2f} m2 is below the two-car minimum "
        f"{GARAGE_TWO_CAR_MIN_M2} m2"
    )
