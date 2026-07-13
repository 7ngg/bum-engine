# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

bum-engine turns a natural-language house brief into several validator-passing
floor-plan variants and exports each as a native Revit `.rvt`:

```
prompt -> LLM extracts a program -> CP-SAT solver places rooms -> slice
composites -> validator (gate) -> rank -> Revit API builds native .rvt
```

**Core rules — do not violate:**
- **The CP-SAT solver owns all geometry.** Room coordinates never come from an
  LLM. Hard constraints (plot fit, non-overlap, min-dimensions, forbidden
  adjacencies) are guaranteed by the solver, not approximated or fixed up
  after the fact.
- **The LLM owns language and judgment only** — extracting a program from a
  brief, and an optional fuzzy-preference → soft-weight nudge (`/critic`). It
  never emits coordinates. No training/fine-tuning; prompted API only
  (Gemini, structured `responseSchema` output).
- **The validator is the export gate.** Nothing is ranked, returned, or
  exportable unless `app/validator.py` passes it.
- **Geometry lives in Python; the Revit build lives in C#.** They communicate
  *only* through `layout.json`, validated against `schemas/layout.schema.json`.
  Never let the Revit builder re-derive geometry the solver already decided.

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

Two versioned JSON Schemas in `/schemas` (currently `"1.0.0"`) are the wire
contract shared by every component: **program.json** (the solver's input) and
**layout.json** (one fully-explicit variant — rect coordinates, wall
centerlines + thickness, hosted doors/windows — so the Revit builder never
re-derives geometry). The version string is duplicated in three places that
must move together: `services/geometry/app/models.py` (`SCHEMA_VERSION`),
`schemas/*.schema.json` (`const`), and `revit/RevitBuilder/LayoutModel.cs`.

## Commands

### Geometry service (`services/geometry/`, Python 3.11+/3.12, FastAPI + ortools)
```bash
cd services/geometry
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt   # installs requirements.txt + pytest
pytest -q                              # ~40 tests
pytest tests/test_solver.py::test_feasible_all_presets -q   # single test
pytest tests/test_solver.py -k gW_eN -q                     # by parametrize id
uvicorn app.main:app --reload          # http://127.0.0.1:8000/docs
```
`GEMINI_API_KEY` env var is required only for `/extract` and `/brief` (and
their tests use an injected `httpx.MockTransport`, so the full suite needs no
key). `GEMINI_MODEL` overrides the model id; `GEMINI_TIMEOUT_S` overrides the
request timeout (default 30s).

### Orchestrator (`api/`, target **.NET 10**)
```bash
dotnet build api/Api.csproj -c Release
dotnet run --project api            # http://localhost:5080; needs geometry service reachable
```
No test project exists for `api/` yet — CI only builds it.

### Revit exporter (`revit/`, target **Revit 2025 API / .NET 8**, Windows-only)
```bash
dotnet build revit/RevitBuilder/RevitBuilder.csproj -c Release
dotnet build revit/AddIn/AddIn.csproj -c Release
```
Builds without a Revit install via the `Nice3point.Revit.Api.*` compile-time
metapackages; the real `RevitAPI.dll` loads at runtime inside Revit. There is
no automated test project — the contract (JSON → `LayoutModel`) is exercised
indirectly by the geometry service's schema tests; the integration test
(open the produced `.rvt`, assert wall/room/door counts) requires a real
Revit host and is not part of `pytest`/`dotnet build`.
`revit/DesignAutomation/DesignAutomation.csproj` also builds standalone but is
**excluded from CI** (needs `DesignAutomationBridge.dll` from the Revit DA SDK,
not available in CI).

### Web (`web/`, Next.js App Router + TS)
```bash
cd web
npm install
npm run dev     # http://localhost:3000
npm run build
npm run lint
```

### Everything via Docker
```bash
docker compose -f docker/docker-compose.yml up --build          # dev: geometry:8000, api:5080, web:3000
docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml up -d --build   # prod: nginx on :80
```

### CI (`.github/workflows/ci.yml`)
Four independent jobs, each testing/building only its own component: geometry
`pytest` (ubuntu), `api` build (ubuntu, .NET 10), RevitBuilder+AddIn build
(**windows-latest**, .NET 8 — DesignAutomation excluded), web lint+build
(ubuntu, Node 22).

## Architecture

### Geometry pipeline (`services/geometry/app/`)
```
program.json -> solver.py -> slicer.py -> validator.py -> generate.py -> svg.py
```
- **Coordinate frame** (`zones.py`, solver-internal, fixed): origin at plot SW
  corner, `+x` east, `+y` north. `y=0` is south/daylight (living, master,
  terrace); `y=depth` is north/street (garage, entry). `program.orientation`
  records the real compass mapping; this internal frame is what makes the
  hard-zoning rules in `solver.py`/`presets.py` unambiguous.
- **`solver.py`** — CP-SAT over ~8 macro-zones as free rectangles on a
  `GRID_M=0.5` m grid. Each zone gets `x0,y0,x1,y1,w,h,area` int vars;
  `area=w*h` constrained to `[0.72,1.45]×target_m2`; aspect `w≤3h,h≤3w`;
  `AddNoOverlap2D` across all zones. Adjacency is a reified "share a wall of
  length ≥ N" boolean (`_share_wall`, four directional configs OR'd together)
  used both as a hard `==1` constraint (required adjacency) and as a soft
  reward term. `_forbid_adjacent` forces a minimum gap on at least one axis.
  Zoning pins (which edge a zone must touch) come from `presets.py`, not
  hardcoded here. Production solves use `workers=8` (fast, ~0.1s, but
  **nondeterministic** — search runs a portfolio across threads); anything
  needing reproducibility (tests, the golden file) must pass `workers=1`.
- **`presets.py`** — `PRESETS = ["gW_eN","gW_eW","gE_eN","gE_eW"]` (garage
  west/east × entry north/west). `resolve(name)` turns a preset name into the
  per-zone `Pins` the solver applies. This is the axis `generate.py` fans out
  over for visual diversity.
- **`slicer.py`** — cuts composite macro-zones into named rooms so internal
  adjacency holds *by construction* (not by another solve): `master_suite` →
  Master Bedroom + Master Bathroom + Walk-in Closet; `children` → two bedrooms
  flanking a middle Bathroom, beds along the exterior wall; `kitchen_laundry`
  → Kitchen (kept toward Dining, direction detected via `_side_of`) + Laundry;
  `entry` → Foyer + Mudroom (toward the Garage). Then it **rasterizes walls**
  by scanning the occupancy grid for cell-adjacency changes and merging
  collinear unit-edges into wall runs (exterior = touches an unowned cell,
  thicker: `EXT_WALL_M=0.30` vs `INT_WALL_M=0.15`), and places **doors** via a
  spanning tree over the room-adjacency graph rooted at the Foyer (guarantees
  every room is reachable through exactly one tree path), plus one **main
  entry** door (prefers Foyer's north/street-facing exterior wall) and a
  **terrace** projecting south off Living.
- **`validator.py`** — the gate and the test oracle. Hard-rejects: any
  overlap, any room below `MIN_ROOM_M=0.9`, coverage below `0.9`, a forbidden
  pair touching (master↔kitchen, garage↔living — checked by room *name*, not
  zone), or any door whose host wall is missing/under `0.8`m. Soft checks
  (kitchen↔dining, dining↔living, master↔ensuite) only warn — a sliced-out
  room can legitimately be absent.
- **`generate.py`** — fans out `PRESETS × seeds` (default seeds `[1,2,3,4]`),
  solves + slices + validates each, keeps the best-scoring passing variant per
  preset (for diversity) then backfills from spares by objective, deduped by
  a `(preset, coarse room footprint)` signature. Only validator-passing
  variants ever leave this function.
- **`schema_io.py`** — deliberately validates twice: pydantic models
  (`models.py`) guard in-process shape/types; `jsonschema` against
  `/schemas/*.schema.json` guards the actual wire contract shared with the C#
  side. When changing a field, update the pydantic model **and** the JSON
  Schema — they are not generated from each other.
- **`gemini.py`** — calls Gemini's `generateContent` with `response_schema`
  (an OpenAPI-subset mirror of `program.schema.json`, since Gemini doesn't
  support `$ref`/`$defs`), then validates the JSON reply against the real
  schema and retries (feeding validation errors back to the model) up to
  `max_retries`. Accepts an injected `httpx.Client` so tests never hit the
  network (`tests/test_gemini.py` uses `httpx.MockTransport`).

### Revit exporter (`revit/`)
All model-building logic lives in one host-agnostic class,
**`RevitBuilder.Build`** (`revit/RevitBuilder/RevitBuilder.cs`), so the
desktop add-in and the headless Design Automation engine execute *identical*
code inside one `Transaction`: create a Level → native Walls (one
single-layer `WallType` duplicated per distinct thickness, cached) → Rooms
(placed at each rect's center) → Door/Window `FamilyInstance`s hosted on the
wall named by each opening's `wall_id`. All values are metres, converted to
Revit's internal feet via `UnitUtils`. It makes no UI calls. Missing
door/window families degrade to a warning in `BuildResult`, not an exception.
- **`AddIn/BuildLayoutCommand.cs`** — `IExternalCommand`; file-picker →
  `LayoutModel.Load` → `RevitBuilder().Build` → `SaveAs` a sibling `.rvt`.
- **`DesignAutomation/DesignAutomationApp.cs`** — same builder, triggered by
  `DesignAutomationReadyEvent` instead of a UI command.
- **`DesignAutomation/WorkItemClient.cs`** — a separate, pure-HTTP APS DA v3
  client (2-legged auth → submit workitem with signed I/O URLs → poll).
  **Not currently wired into `api/`**: `api/Services/RevitExporter.cs`'s
  `DesignAutomationExporter.ExportAsync` only checks that APS config is
  present and returns a canned "Building" status — it does not call
  `WorkItemClient`, and `api/Api.csproj` has no project reference to
  `revit/DesignAutomation` at all. Finishing the DesignAutomation export path
  means wiring that client in (and sourcing signed upload/download URLs,
  e.g. via an OSS bucket).

### Orchestrator (`api/`)
Minimal-API endpoints in `Program.cs` run: brief → geometry `/generate` or
`/brief` (via `Services/GeometryClient.cs`) → persist `Project`+`Variant` rows
(EF Core/SQLite, `Data/Entities.cs`, `bumengine.db` via `EnsureCreated`, no
migrations) → return variants → `POST /api/variants/{id}/export` hands off to
whichever `IRevitExporter` is DI-bound from `Export:Mode`
(`Services/RevitExporter.cs`): `AddInHandoffExporter` (default — writes
`{variant}.layout.json` to `HandoffDir` for the desktop add-in to pick up,
and flips `Ready` once it polls the matching `.rvt` into existence in
`OutputDir`) or `DesignAutomationExporter` (see gap above). Note
`ToVariant()` in `Program.cs` strips the UI-only `svg`/`coverage` fields
before storing `LayoutJson`, specifically so the persisted JSON still
validates against `layout.schema.json` for the Revit builder.

### Web (`web/`)
Prompt → variant SVG grid → select → export → download `.rvt`
(`app/page.tsx`). **`web/lib/api.ts` hardcodes `API_BASE = ""`** — the browser
always calls same-origin `/api/*`. That path is caught by the catch-all
`app/api/[...path]/route.ts` Route Handler, which forwards to
`process.env.ORCHESTRATOR_URL` (default `http://localhost:5080`), read fresh
on every request. This is intentionally *not* a `next.config.mjs` rewrite —
rewrites bake their target at build time, which previously froze the wrong
URL into the production image. **`web/README.md`'s `NEXT_PUBLIC_API_BASE` is
stale** — that env var has no effect anywhere in current code; the real knob
is `ORCHESTRATOR_URL`, consumed server-side by the Route Handler (in prod,
nginx also proxies `/api` directly to `api`, per `docker/nginx.conf`).

## Testing notes
- `services/geometry/tests/test_golden.py` freezes one solve
  (`gW_eN`, seed 1, `workers=1`) as a structural signature (room
  names/rects/counts) in `tests/golden/gW_eN_seed1.json`. A failure means the
  layout drifted — inspect the diff and regenerate the golden file only if
  the drift is intended (delete it and rerun to have it recreate itself).
- `data/program.example.json` / `data/layout.example.json` are the fixed M1
  demo brief/output; the web UI's "Demo mode" posts the same example program
  so the full flow works with no Gemini key (`web/lib/example.ts`).
