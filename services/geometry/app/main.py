"""FastAPI geometry service.

Endpoints:
  GET  /health              liveness + version
  POST /extract  {prompt}   -> program.json          (Gemini structured output)
  POST /generate {program,n}-> {variants[], warnings} (all validator-gated)
  POST /brief    {prompt,n} -> extract then generate  (convenience for the API)
  POST /critic   {...}      -> optional soft re-rank weight overrides
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import __version__
from .gemini import GeminiError, extract_program
from .generate import generate
from .models import Program
from .schema_io import validate_program

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

app = FastAPI(title="bum-engine geometry service", version=__version__)


class ExtractRequest(BaseModel):
    prompt: str = Field(min_length=1)


class GenerateRequest(BaseModel):
    program: Program
    n: int = Field(default=3, ge=1, le=8)
    time_limit_s: float = Field(default=12.0, gt=0, le=60)


class BriefRequest(BaseModel):
    prompt: str = Field(min_length=1)
    n: int = Field(default=3, ge=1, le=8)


class CriticRequest(BaseModel):
    program: Program
    note: str = ""


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__, "gemini": bool(os.environ.get("GEMINI_API_KEY"))}


@app.get("/example")
def example_program() -> dict:
    """data/program.example.json, served so the web UI's demo mode has one
    source of truth instead of a hand-maintained TS copy (they had already
    drifted from each other once)."""
    return json.loads((DATA_DIR / "program.example.json").read_text())


@app.post("/extract")
def extract(req: ExtractRequest) -> dict:
    try:
        program = extract_program(req.prompt)
    except GeminiError as e:
        raise HTTPException(status_code=503, detail=str(e))
    data = program.model_dump()
    errs = validate_program(data)
    if errs:
        raise HTTPException(status_code=422, detail={"schema_errors": errs})
    return data


@app.post("/generate")
def generate_endpoint(req: GenerateRequest) -> dict:
    errs = validate_program(req.program.model_dump())
    if errs:
        raise HTTPException(status_code=422, detail={"schema_errors": errs})
    result = generate(req.program, n=req.n, time_limit_s=req.time_limit_s)
    if not result.variants:
        raise HTTPException(status_code=422, detail={"error": "no validator-passing variants", "warnings": result.warnings})
    return result.to_dict()


@app.post("/brief")
def brief(req: BriefRequest) -> dict:
    try:
        program = extract_program(req.prompt)
    except GeminiError as e:
        raise HTTPException(status_code=503, detail=str(e))
    result = generate(program, n=req.n)
    return {"program": program.model_dump(), **result.to_dict()}


@app.post("/critic")
def critic(req: CriticRequest) -> dict:
    """Optional hook: map a fuzzy preference note to solver soft-weight overrides.

    Kept deliberately conservative — returns the canonical weights unless a note
    clearly asks for more openness/privacy. The solver still owns geometry.
    """
    weights = {"coverage": 12, "soft": 40, "public_non_south": 3, "service_northness": 2}
    note = req.note.lower()
    if "open" in note or "social" in note:
        weights["soft"] += 10
    if "private" in note or "quiet" in note:
        weights["public_non_south"] += 2
    return {"weights": weights}
