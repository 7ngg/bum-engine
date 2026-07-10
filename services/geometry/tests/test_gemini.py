"""Gemini extraction: structured output parsed, validated, retried on failure.

Uses an injected httpx MockTransport so no network/API key is needed.
"""

import json
from pathlib import Path

import httpx
import pytest

from app.gemini import GeminiError, extract_program

DATA = Path(__file__).resolve().parents[1] / "data"
VALID_PROGRAM = json.loads((DATA / "program.example.json").read_text())


def _gemini_response(text: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"candidates": [{"content": {"parts": [{"text": text}]}}]},
    )


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_extract_valid_first_try():
    def handler(request: httpx.Request) -> httpx.Response:
        return _gemini_response(json.dumps(VALID_PROGRAM))

    prog = extract_program("a family home", api_key="x", client=_client(handler))
    assert prog.plot.width_m == VALID_PROGRAM["plot"]["width_m"]
    assert prog.space("garage") is not None


def test_extract_retries_on_bad_then_good():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return _gemini_response('{"version": "1.0.0", "plot": {"width_m": 10}}')  # invalid
        return _gemini_response(json.dumps(VALID_PROGRAM))

    prog = extract_program("home", api_key="x", max_retries=2, client=_client(handler))
    assert calls["n"] == 2
    assert prog.floors == VALID_PROGRAM["floors"]


def test_extract_gives_up_after_retries():
    def handler(request: httpx.Request) -> httpx.Response:
        return _gemini_response('{"nope": true}')

    with pytest.raises(GeminiError):
        extract_program("home", api_key="x", max_retries=1, client=_client(handler))


def test_missing_key_errors():
    with pytest.raises(GeminiError):
        extract_program("home", api_key="")
