"""Gemini structured extraction: natural-language brief -> program.json.

The LLM owns language and judgment only: it never emits geometry. It fills the
program contract (zones, targets, min dims, adjacency preferences); the CP-SAT
solver turns that into coordinates. We use Gemini's structured-output
(responseSchema) so the reply is JSON-shaped, then validate against our own
JSON Schema and retry with the validator's errors fed back on failure.
"""

from __future__ import annotations

import json
import os

import httpx

from .models import Program
from .schema_io import validate_program

DEFAULT_MODEL = "gemini-2.0-flash"
_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# OpenAPI-subset schema Gemini understands (no $ref/$defs). Mirrors
# program.schema.json closely enough to steer the model.
_ZONE_IDS = ["living", "dining", "kitchen_laundry", "master_suite", "children", "office", "entry", "garage"]
_CATEGORIES = ["living", "private", "wet", "service", "circ", "office", "outdoor"]

RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "version": {"type": "string"},
        "plot": {
            "type": "object",
            "properties": {"width_m": {"type": "number"}, "depth_m": {"type": "number"}},
            "required": ["width_m", "depth_m"],
        },
        "orientation": {"type": "string", "enum": ["N", "E", "S", "W"]},
        "target_area_m2": {"type": "number"},
        "floors": {"type": "integer"},
        "spaces": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "enum": _ZONE_IDS},
                    "target_m2": {"type": "number"},
                    "min_w_m": {"type": "number"},
                    "min_h_m": {"type": "number"},
                    "category": {"type": "string", "enum": _CATEGORIES},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "target_m2", "min_w_m", "min_h_m", "category"],
            },
        },
        "adjacency": {
            "type": "object",
            "properties": {
                "desirable": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
                "semi": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
                "avoid": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
            },
        },
    },
    "required": ["version", "plot", "orientation", "target_area_m2", "floors", "spaces", "adjacency"],
}

SYSTEM = (
    "You convert a house brief into a strict program JSON for a floor-plan "
    "solver. Rules: (1) version is always \"1.0.0\". (2) Provide these eight "
    "canonical spaces by id when the brief implies a family home: garage, "
    "living, dining, kitchen_laundry, master_suite, children, office, entry. "
    "Omit a space only if clearly not wanted. (3) target_m2/min_w_m/min_h_m are "
    "realistic metres; composites (master_suite, children, kitchen_laundry, "
    "entry) need room to subdivide (children min_h_m>=6, kitchen_laundry and "
    "entry min>=3-4 m). (4) categories: living=living/dining, private=bedrooms, "
    "wet=kitchen/bath, service=garage/laundry, circ=entry, office=office. "
    "(5) adjacency.avoid must include [master_suite,kitchen_laundry] and "
    "[garage,living]. Never output coordinates; the solver places rooms."
)


class GeminiError(RuntimeError):
    pass


def extract_program(
    prompt: str,
    *,
    api_key: str | None = None,
    model: str = DEFAULT_MODEL,
    max_retries: int = 2,
    client: httpx.Client | None = None,
) -> Program:
    """Extract a validated Program from a natural-language brief."""
    api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise GeminiError("GEMINI_API_KEY not set")

    owns_client = client is None
    client = client or httpx.Client(timeout=60.0)
    try:
        errors: list[str] = []
        last_raw = ""
        for attempt in range(max_retries + 1):
            user = prompt if attempt == 0 else (
                f"{prompt}\n\nYour previous reply failed validation:\n"
                + "\n".join(errors)
                + f"\n\nPrevious JSON:\n{last_raw}\nReturn corrected JSON only."
            )
            raw = _call(client, api_key, model, user)
            last_raw = raw
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                errors = [f"invalid JSON: {e}"]
                continue
            data.setdefault("version", "1.0.0")
            errors = validate_program(data)
            if not errors:
                return Program.model_validate(data)
        raise GeminiError(f"extraction failed after {max_retries + 1} tries: {errors}")
    finally:
        if owns_client:
            client.close()


def _call(client: httpx.Client, api_key: str, model: str, text: str) -> str:
    url = f"{_BASE}/{model}:generateContent"
    body = {
        "system_instruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "response_schema": RESPONSE_SCHEMA,
            "temperature": 0.2,
        },
    }
    r = client.post(url, params={"key": api_key}, json=body)
    if r.status_code != 200:
        raise GeminiError(f"gemini HTTP {r.status_code}: {r.text[:300]}")
    payload = r.json()
    try:
        return payload["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise GeminiError(f"unexpected gemini response shape: {e}: {str(payload)[:300]}")
