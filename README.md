# bum-engine — AI floor-plan generator → native Revit models

Turn a natural-language house brief into several valid floor-plan variants and
export each as a native Revit `.rvt` (real walls, rooms, doors, windows).

```
prompt -> LLM extracts a program -> CP-SAT solver places rooms -> slice
composites -> validator (gate) -> rank -> Revit API builds native .rvt
```

**Core rule:** the constraint solver owns geometry; the LLM owns language and
judgment only. The validator is the export gate — nothing exports unless it
passes.

## Repository layout
```
services/geometry/     FastAPI: solver (CP-SAT), slicer, validator, SVG   [Python]
revit/RevitBuilder/    shared, host-agnostic model builder                [C#]
revit/AddIn/           desktop IExternalCommand host                      [C#]
revit/DesignAutomation/APS AppBundle + Activity + workitem client         [C#]
api/                   standalone orchestrator + EF Core/SQLite           [ASP.NET Core]
web/                   prompt -> variant grid -> download .rvt            [Next.js]
schemas/               program.schema.json, layout.schema.json            [JSON Schema]
docker/                compose, nginx, dev/prod split
```

## Data contracts
Two versioned JSON Schemas in `/schemas` are the source of truth shared across
all components:
- **program.json** — the brief the solver consumes (plot, spaces, adjacency).
- **layout.json** — one concrete variant, fully explicit (rooms, wall
  centerlines + thickness, hosted doors/windows) so the Revit builder never
  re-derives geometry.

## Milestones
| # | scope | status |
|---|-------|--------|
| M0 | repo scaffold + both JSON schemas | ✅ |
| M1 | solver emits one validator-passing layout | ✅ |
| M2 | preset×seed variants + rank + gate + SVG | ✅ |
| M4 | Gemini `/extract` with schema validation + retry | ✅ |
| M3 | RevitBuilder + desktop add-in → native `.rvt` | ⏳ |
| M5 | orchestrator + EF Core/SQLite | ⏳ |
| M6 | Next.js UI | ⏳ |
| M7 | APS Design Automation + Docker/compose/nginx/CI | ⏳ |

## Quick start (geometry)
```bash
cd services/geometry
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
uvicorn app.main:app --reload
```
See each component's README for details.
```
```
