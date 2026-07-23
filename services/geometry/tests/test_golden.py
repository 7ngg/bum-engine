"""Golden-file layout stability, split two ways:

- test_golden_invariants_portable: environment-portable properties (status,
  objective value, room-name set, REQUIRED_ADJ satisfied, no overlaps,
  coverage). These hold on every toolchain.
- test_golden_exact_coordinates: the full per-room coordinate signature,
  gated on the exact ortools/python toolchain it was captured with. CP-SAT's
  objective has tied optima (see test_adjacency.py's dedupe tests for why),
  so exact coordinates were never portable across ortools builds/platforms
  even at workers=1 — only the objective VALUE is. The golden file records
  the toolchain it was generated on; this test skips rather than false-fails
  when that toolchain doesn't match the one running it.
"""

import json
import sys
from pathlib import Path

import ortools
import pytest

from app import geom
from app import zones as Z
from app.slicer import build_layout
from app.solver import solve

GOLDEN = Path(__file__).resolve().parent / "golden" / "gW_eN_seed1.json"

# Task 5: the golden freezes the ROOMY plot (20x24; the tight plot can no longer
# host a circulation tree). Corridor is a live zone and the door graph is the
# access tree, so objective, counts and coordinates all moved from Task 4. Phase
# 4's setback envelope broke the plot's translational symmetry, so the solve now
# PROVES optimal in ~1.2 s at workers=1 — deterministic, no longer the 30 s+
# symmetry grind of the setback-less interim plot. These portable invariants plus
# the access-graph checks are the primary guard; the exact-coordinate signature
# is trustworthy again now that the structure has settled.
EXPECTED_OBJECTIVE = -15.5625
EXPECTED_ROOM_NAMES = {
    "Living", "Dining", "Kitchen", "Laundry",
    "Master Bedroom", "Master Bathroom", "Walk-in Closet",
    "Bedroom 2", "Bedroom 3", "Bathroom",
    "Office", "Foyer", "Mudroom", "Garage", "Corridor",
}


def _current_toolchain() -> dict:
    return {"ortools": ortools.__version__, "python": f"{sys.version_info.major}.{sys.version_info.minor}"}


def _room_signature(layout) -> dict:
    return {
        "preset": layout.preset,
        "seed": layout.seed,
        "n_rooms": len(layout.rooms),
        "n_walls": len(layout.walls),
        "n_doors": len(layout.doors),
        "n_windows": len(layout.windows),
        "rooms": sorted(
            [r.name, round(r.rect_m[0], 2), round(r.rect_m[1], 2), round(r.rect_m[2], 2), round(r.rect_m[3], 2)]
            for r in layout.rooms
        ),
    }


def _load_golden() -> dict | None:
    return json.loads(GOLDEN.read_text()) if GOLDEN.exists() else None


def test_golden_invariants_portable(roomy_program):
    program = roomy_program
    r = solve(program, "gW_eN", seed=1, time_limit_s=12, workers=1)
    assert r.status == "OPTIMAL"
    assert r.objective == EXPECTED_OBJECTIVE

    layout = build_layout(r, program)
    assert {rm.name for rm in layout.rooms} == EXPECTED_ROOM_NAMES

    rects = [tuple(rm.rect_m) for rm in layout.rooms]
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            assert geom.overlap_area(rects[i], rects[j]) < 1e-6, "rooms must not overlap"

    def room(name: str) -> geom.Rect:
        return next(tuple(rm.rect_m) for rm in layout.rooms if rm.name == name)

    # REQUIRED_ADJ, at the post-slice room level (Kitchen keeps the dining
    # side of kitchen_laundry; see slicer.py::_slice_kitchen).
    assert geom.adjacent(room("Kitchen"), room("Dining"), Z.REQUIRED_SHARE_M)
    assert geom.adjacent(room("Dining"), room("Living"), Z.REQUIRED_SHARE_M)

    # Coverage is now measured against the footprint (room bounding box), which
    # the zones tile to ~0.97; the exact value depends on which tied optimum the
    # toolchain lands on, so assert the portable gate, not an exact figure.
    fx0 = min(rc[0] for rc in rects)
    fy0 = min(rc[1] for rc in rects)
    fx1 = max(rc[2] for rc in rects)
    fy1 = max(rc[3] for rc in rects)
    coverage = sum(geom.area(rc) for rc in rects) / ((fx1 - fx0) * (fy1 - fy0))
    assert coverage >= 0.95
    # the house no longer fills the plot — there is real setback now
    assert (fx1 - fx0) * (fy1 - fy0) < layout.plot.width_m * layout.plot.depth_m - geom.EPS


_golden = _load_golden()
_toolchain_mismatch = _golden is not None and _golden.get("toolchain") != _current_toolchain()


@pytest.mark.skipif(
    _toolchain_mismatch,
    reason=(
        f"golden captured on {_golden.get('toolchain') if _golden else None}, "
        f"running on {_current_toolchain()}; CP-SAT's tied optima make exact "
        f"coordinates non-portable across toolchains (see test_golden_invariants_portable)"
    ),
)
def test_golden_exact_coordinates(roomy_program):
    program = roomy_program
    r = solve(program, "gW_eN", seed=1, time_limit_s=12, workers=1)
    sig = _room_signature(build_layout(r, program))
    if _golden is None:
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps({"toolchain": _current_toolchain(), "signature": sig}, indent=2))
        return
    assert sig == _golden["signature"], "layout drifted from golden; review and update if intended"
