import json
from pathlib import Path

import pytest

from app.models import Program

DATA = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture(scope="session")
def program() -> Program:
    return Program.model_validate(json.loads((DATA / "program.example.json").read_text()))
