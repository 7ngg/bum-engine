# Build: AI floor-plan generator → native Revit models

Turn a natural-language house brief into several valid floor-plan variants and
export each as a **native Revit `.rvt`** (real walls, rooms, doors, windows)
via the **Revit API**. Standalone project — no shared framework. Build
incrementally, **geometry service first**, verifying each milestone.

## Pipeline
`prompt → LLM extracts a program → CP-SAT solver places rooms → slice composites
→ validator (gate) → rank → Revit API builds native .rvt`

## Core rules (do not violate)
- **Solver owns geometry.** Room coordinates come from a constraint solver,
  never an LLM. Hard constraints (plot fit, non-overlap, exact min-dimensions,
  forbidden adjacencies) are guaranteed, not approximated.
- **LLM owns language + judgment only** (extract program; optional re-rank /
  fuzzy-pref → weights). **No training/fine-tuning; prompted API only.**
- **Validator is the gate**: nothing exports unless it passes.
- **Geometry in Python; Revit build in C#.** They communicate via `layout.json`.

## Revit export constraint (read first)
The Revit API is .NET, runs **only inside Revit**, so it's a separate component
from the Python service. Implement it **twice-shaped from one core**:
1. **Desktop add-in (build this first):** C# `IExternalCommand` that reads
   `layout.json` from disk and constructs the model in a running Revit session.
2. **Headless (deployment target):** the same C# builder packaged as an
   **APS Design Automation for Revit** AppBundle + Activity; a workitem takes
   `layout.json` and returns a `.rvt`.
   Keep all model-building logic in a shared `RevitBuilder` class so both hosts
   call identical code. Target a recent Revit API version; state which.

## Tech stack
- Geometry service: **Python 3.11 + FastAPI**, `ortools` (CP-SAT). `uv`/venv.
- Revit exporter: **C# / .NET**, Revit API (RevitAPI.dll, RevitAPIUI.dll);
  Design Automation packaging via **Autodesk Platform Services**.
- Orchestrator/API: your choice of a lightweight backend (**ASP.NET Core** is
  natural given the C# exporter, but keep it a plain standalone service — no
  monolith/module system). Persist with **EF Core + SQLite**.
- Frontend: **Next.js** (App Router, TS).
- LLM: **Google Gemini** REST, structured output (`responseSchema`).
- Deploy: Docker + compose for the Python/web/API parts; the Revit piece runs as
  a desktop add-in or an APS Design Automation activity (document both). Nginx,
  dev/prod split, one GitHub Actions workflow.

## Repo layout
```
/services/geometry/     # FastAPI: solver, slicer, validator
/revit/RevitBuilder/    # shared C# model-builder (host-agnostic)
/revit/AddIn/           # desktop IExternalCommand host
/revit/DesignAutomation/# APS AppBundle + Activity + workitem client
/api/                   # standalone orchestrator (ASP.NET Core) + EF Core/SQLite
/web/                   # Next.js
/schemas/               # program.schema.json, layout.schema.json
/docker/
```

## Data contracts (versioned JSON Schema)
**program.json**: `plot{width_m,depth_m}`, `orientation`, `target_area_m2`,
`floors`; `spaces[]{id,target_m2,min_w_m,min_h_m,category,tags[]}` (category ∈
living/private/wet/service/circ/office/outdoor); `adjacency{desirable[],semi[],
avoid[]}`.
**layout.json**: per variant `{objective, rooms[{name,category,
rect_m:[x0,y0,x1,y1]}], walls[], doors[[a,b]], windows[], entry, terrace,
levels, wall_height_m, seed, preset}`. **Design walls/openings explicitly here**
so the Revit builder has everything it needs (centerlines, thicknesses, door/
window positions + sizes) rather than re-deriving them.

## Components

### 1. Solver (`solver.py`) — CP-SAT
0.5 m grid. ~8 macro-zones (Garage, Entry=foyer+mudroom, Office, KitchenLaundry,
Dining, Living, MasterSuite, Children) as free rectangles: `area=w*h` in
`[0.72,1.45]×target`; `w,h≥min`; aspect `w≤3h,h≤3w`; `AddNoOverlap2D`;
containment. **Hard zoning:** Living `y0=0`; MasterSuite `y0=0` & `y1≤0.62H`;
Garage on street edge + side; Entry north; Children on the wall opposite the
garage. **Required adjacency (hard, shared wall ≥1.5 m):** KitchenLaundry–Dining,
Dining–Living (reified four-config share-a-wall boolean). **Forbidden (hard,
≥0.5 m gap):** MasterSuite–KitchenLaundry, Garage–Living. **Soft (reward):**
Entry↔{Living,Office,Children}, Garage↔Entry, KitchenLaundry↔Entry,
Children↔Living. **Objective:** `12·coverage + 40·soft_met − 3·public_non_south
+ 2·service_northness`. Time limit ~12 s, 8 workers, exposed `random_seed`.
Variants = {garage W/E}×{entry N/W} presets × seeds; keep top-N distinct passing.

### 2. Slicer — composites cut so internal adjacencies hold by construction
MasterSuite→Bedroom(exterior)+Ensuite+WIC; Children→Bed+Bath(middle)+Bed along
the exterior wall; KitchenLaundry→Kitchen+Laundry(away from Dining);
Entry→Foyer+Mudroom(toward Garage). Terrace projects south off Living.
Emit explicit `walls/doors/windows` into layout.json.

### 3. Validator — the gate + test oracle
Reject unless: no overlaps; every door pair shares a wall ≥0.8 m; **master not
adjacent to kitchen**; garage not adjacent to living; all rooms in plot; min
dims met; coverage ≥~0.9. Structured warnings.

### 4. Geometry API (FastAPI)
`POST /extract{prompt}`→program.json (Gemini `responseSchema`, validate+retry).
`POST /generate{program,n}`→`{variants[], warnings}` (all gated). `POST /critic`
optional re-rank → weight overrides. SVG preview per variant for the UI.

### 5. RevitBuilder (C#, shared) — the SDK export
Given layout.json, in a Revit `Transaction`: create a Level; place **native
Walls** from `walls[]` centerlines with a real WallType/thickness and
`wall_height_m`; create **Rooms** (place room + set Name from each rect); insert
**Door/Window family instances** at `doors[]`/`windows[]` hosted on the right
walls; set units to meters. Save `.rvt`. Keep it host-agnostic (no UI calls) so
both the add-in and Design Automation invoke the same method. Handle missing
families by loading defaults from the Revit library.

### 6. Hosts
- **AddIn:** `IExternalCommand` — pick a `layout.json`, call `RevitBuilder`,
  save `.rvt`, report results.
- **DesignAutomation:** AppBundle wrapping `RevitBuilder` + an Activity with
  `layout.json` input and `.rvt` output; a small C# workitem client the API
  calls.

### 7. Orchestrator + Frontend
API: brief → `/extract` → `/generate` → persist program+variants → return
variants + trigger Revit export (add-in handoff locally, or a Design Automation
workitem in the cloud) → deliver `.rvt`. Next.js: prompt → variant SVG grid →
select → download `.rvt`.

## Milestones (in order, verify each)
- **M0** repo scaffold + both JSON schemas.
- **M1** geometry service emits **one** validator-passing layout from a
  hardcoded program.json. *(Proves the solver — do first.)*
- **M2** preset×seed variants + ranking + validator gate + SVG previews.
- **M3** `RevitBuilder` + desktop add-in: layout.json → native `.rvt` with
  walls, rooms, a door, a window. *(Proves the SDK mapping.)*
- **M4** Gemini `/extract` with schema validation + retry.
- **M5** orchestrator + EF Core/SQLite persistence.
- **M6** Next.js UI (prompt → pick → download `.rvt`).
- **M7** APS Design Automation packaging for headless `.rvt`; then Docker/
  compose/nginx/CI, dev/prod split.

## Definition of done
Any brief in program format yields **≥3 validator-passing variants** with correct
adjacencies (kitchen–dining–laundry adjacent, living↔terrace,
master↔ensuite↔WIC, bath between the two children, master **not** adjacent to
kitchen, garage via mudroom), each exportable as a **native Revit `.rvt`** with
real walls/rooms/doors/windows in meters. Geometry generation < ~10 s
(Revit build excluded).

## Testing & style
Pytest the validator rules + solver output vs acceptance criteria; golden-file
one seed. For `RevitBuilder`, an integration test that opens the output `.rvt`
and asserts wall/room/door counts. Small typed functions; commit per milestone;
no ML training; no DXF/IFC as the primary path (they're optional extras).
