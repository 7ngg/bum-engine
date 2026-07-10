"""Validate program/layout dicts against the canonical JSON Schemas in /schemas.

The pydantic models guard in-process shape; these functions guard the wire
contract (what actually crosses service boundaries), which is the source of
truth shared with the C# Revit builder and the orchestrator.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path

import jsonschema

_SCHEMA_DIR = Path(__file__).resolve().parents[3] / "schemas"


@functools.lru_cache(maxsize=None)
def _schema(name: str) -> dict:
    return json.loads((_SCHEMA_DIR / name).read_text())


@functools.lru_cache(maxsize=None)
def _validator(name: str) -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator(_schema(name))


def validate_program(data: dict) -> list[str]:
    return _errors("program.schema.json", data)


def validate_layout(data: dict) -> list[str]:
    return _errors("layout.schema.json", data)


def _errors(schema_name: str, data: dict) -> list[str]:
    v = _validator(schema_name)
    return [f"{'/'.join(str(p) for p in e.path)}: {e.message}" for e in v.iter_errors(data)]
