# Geometry service

FastAPI service that turns a `program.json` brief into validator-passing
floor-plan variants (`layout.json` + SVG preview). **The solver owns all
geometry**; the LLM only fills the program.

## Pipeline
```
program.json
  -> solver.py     CP-SAT places ~8 macro-zones as free rectangles (hard rules)
  -> slicer.py     cut composites; emit explicit walls/doors/windows
  -> validator.py  the gate: reject anything that breaks a hard rule
  -> generate.py   presets x seeds, rank, keep top-N distinct passing
  -> svg.py        preview per variant
```

## Coordinate frame (solver-internal)
Origin at plot SW corner, `+x` east, `+y` north. `y=0` is the south / daylight
side (living + master + terrace); `y=depth` is the street / service side
(garage + entry). See `app/zones.py`.

## Run
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload            # http://127.0.0.1:8000/docs
pytest -q                                 # 40 tests
```

## Endpoints
| method | path        | body                     | returns |
|--------|-------------|--------------------------|---------|
| GET    | `/health`   | –                        | version + gemini-key present |
| POST   | `/extract`  | `{prompt}`               | `program.json` (Gemini, needs `GEMINI_API_KEY`) |
| POST   | `/generate` | `{program, n}`           | `{variants[], warnings}` (all gated) |
| POST   | `/brief`    | `{prompt, n}`            | extract + generate in one call |
| POST   | `/critic`   | `{program, note}`        | optional soft-weight overrides |

Each variant is a full `layout.json` (schema `/schemas/layout.schema.json`) plus
an inline `svg` string and `coverage`.

## Env
- `GEMINI_API_KEY` — required only for `/extract` and `/brief`.

## Notes
- Production solves use 8 workers (fast, ~0.1 s/solve, nondeterministic). Tests
  that need reproducibility pass `workers=1`.
- `data/program.example.json` is the hardcoded M1 brief; `data/layout.example.json`
  is a sample output for the Revit builder.
