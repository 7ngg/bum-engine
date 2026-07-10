"""Golden-file: one seed's layout stays structurally stable across changes."""

import json
from pathlib import Path

from app.slicer import build_layout
from app.solver import solve

GOLDEN = Path(__file__).resolve().parent / "golden" / "gW_eN_seed1.json"


def _signature(layout) -> dict:
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


def test_golden_seed(program):
    # workers=1 for a reproducible solve (see test_seed_is_deterministic).
    r = solve(program, "gW_eN", seed=1, time_limit_s=12, workers=1)
    sig = _signature(build_layout(r, program))
    if not GOLDEN.exists():
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(sig, indent=2))
    expected = json.loads(GOLDEN.read_text())
    assert sig == expected, "layout drifted from golden; review and update if intended"
