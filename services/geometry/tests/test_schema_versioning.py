"""1.1.0 is additive-only: existing 1.0.0 program documents must still load."""

import json
from pathlib import Path

from app.models import Program
from app.schema_io import validate_program

DATA = Path(__file__).resolve().parents[1] / "data"


def test_v1_0_0_example_program_unchanged_still_validates():
    data = json.loads((DATA / "program.example.json").read_text())
    assert data["version"] == "1.0.0"
    assert validate_program(data) == []
    prog = Program.model_validate(data)
    # new 1.1.0 fields are absent on this document and stay unset, not defaulted
    assert all(s.max_aspect is None and s.min_area_m2 is None for s in prog.spaces)


def test_v1_0_0_document_without_new_fields_at_all_is_fine():
    minimal = {
        "version": "1.0.0",
        "plot": {"width_m": 10.0, "depth_m": 10.0},
        "orientation": "N",
        "target_area_m2": 80.0,
        "floors": 1,
        "spaces": [
            {"id": "living", "target_m2": 20.0, "min_w_m": 3.0, "min_h_m": 3.0, "category": "living"}
        ],
        "adjacency": {},
    }
    assert validate_program(minimal) == []
    prog = Program.model_validate(minimal)
    assert prog.adjacency.desirable == []
    assert prog.spaces[0].max_aspect is None


def test_circulation_zone_id_accepted_and_live():
    # Task 5: "circulation" is now a LIVE solver zone. It is not in the brief —
    # solver.solve injects a derived corridor Space (zones.inject_circulation) —
    # but ZONE_ORDER includes it so the solver places it, and an explicit
    # circulation space in a document still validates and is left untouched.
    from app import zones as Z

    assert "circulation" in Z.ZONE_ORDER
    assert Z.ZONE_DISPLAY["circulation"] == "Corridor"
    data = {
        "version": "1.1.0",
        "plot": {"width_m": 10.0, "depth_m": 10.0},
        "orientation": "N",
        "target_area_m2": 80.0,
        "floors": 1,
        "spaces": [
            {"id": "living", "target_m2": 20.0, "min_w_m": 3.0, "min_h_m": 3.0, "category": "living"},
            {"id": "circulation", "target_m2": 5.0, "min_w_m": 1.2, "min_h_m": 2.0, "category": "circ"},
        ],
        "adjacency": {},
    }
    assert validate_program(data) == []
    prog = Program.model_validate(data)
    assert prog.space("circulation") is not None
    # inject_circulation is a no-op when the program already carries one
    injected, warns = Z.inject_circulation(prog)
    assert warns == []
    assert sum(1 for s in injected.spaces if s.id == "circulation") == 1
