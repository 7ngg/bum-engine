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
- **`standards.py`** — the per-room-type source of truth: Neufert/SNiP shape
  floors, the architect's area **bands** (round 3), and his **three-value model
  and priority tiers** (round 4, 2026-08-04, both rulings quoted verbatim there).
  Every room has `area_floor` / `area_ideal` / `area_ceiling` and a
  `priority_tier` (1 social+living, 2 private, 3 kept at minimum); `area_ideal`
  is *derived* — `floor + f(tier)·(ceiling − floor)`, `f = {1: ½, 2: ¼, 3: 0}`.

  **FINDING 3 — no ideal column is sourceable, and Neufert is the reason.**
  Neufert's residential figures are furniture-clearance **MINIMA** (p44 table 1a
  is titled *"minimum room sizes"*). Re-deriving a Master Bedroom from bed
  2.00 × 1.60 + 0.75 m passes + 0.60 m wardrobe run + 0.90 m dressing space +
  a seating corner gives **~16.3 m² — the architect's 16 m² FLOOR**. A furniture
  layout answers *"how small may this room be"*, never *"how big should it be"*.
  That is why standards.py already spends Neufert on the floors, why the ideals
  cannot come from the same place, and why they are DERIVED from a stated rule.
  (`program_roomy.json`'s per-space targets were also rejected as a source: LLM
  guesses, per zone not per room type, and rescaled to the footprint by
  reconcile — i.e. Phase 1's deleted adherence term in a new hat.)

  **TWO OPEN QUESTIONS FOR THE ARCHITECT, unresolved on purpose** (flagged in
  `IDEAL_BAND_FRACTION` and `PRIORITY_TIER`): (a) `f(1)=½` and `f(2)=¼` are
  OURS — only the tier ORDER is his, and `f(3)=0` is his wording rather than a
  dial; (b) **Dining's tier** — he named tier 1 "social and living" then listed
  three rooms, none of them Dining; tier 3 has a catch-all, tiers 1 and 2 do
  not. Placed in tier 1 by his category. Most consequential open item: Dining
  holds +7.50 m² above its floor.

  `TIER_W_BELOW`/`TIER_W_ABOVE` are the objective weights, consumed by
  **both** `solver.py` and `slicer._cut_score` so the zone-level and room-level
  rules cannot disagree; `max(TIER_W_BELOW) < 4560` is load-bearing — it is the
  net cost of one cell of footprint growth, so the term redistributes the house
  and can never inflate it.
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

  **"COMPOSITE ROOM STARVATION" — THE SYMPTOM IS REAL, THE DIAGNOSIS WAS
  FALSIFIED (symptom measured 2026-08-03, diagnosis overturned 2026-08-04).**
  The headline room of every composite zone sits on its area floor while the
  ancillary carries the slack. Measured on roomy @192, gW_eN (footprint
  201.50 m², 48.46 m² above the binding floors):

  | zone | headline room | ancillary |
  |---|---|---|
  | `kitchen_laundry` | Kitchen **10.00 = its floor, +0.00** | Laundry **+4.00** |
  | `children` | Bedroom 2 and 3 **12.00 = floor, +0.00** | Bathroom **+3.92** |
  | `master_suite` | Master Bedroom **+0.50** | Walk-in Closet **+3.30** |
  | `entry` | Mudroom **3.00 = floor, +0.00** | Foyer +1.00 |

  Every number passes (all 16 rooms sit inside their architect bands), and the
  plan still reads as cramped in a drawing: the rooms a client actually names
  are the ones on their minimums.

  This entry used to end: *"the objective's coverage term is indifferent about
  which room grows, so it is the composite CUT, not the objective, that
  decides."* **Both halves are false.** Building the architect's three-value
  model (2026-08-04) was what measured it:

  - **FINDING 1 — the slicer is not starving anyone. There was never a
    distribution decision being made badly.** The Laundry is not *given* +4.00.
    It is a band spanning the zone's whole cross-dimension at its own
    grid-snapped 2.0 m minimum width, so on a 4.5 × 4.0 zone **2.0 × 4.0 =
    8.00 m² is the smallest it can physically be**. Every composite cut at these
    shapes has one or two grid-legal candidates, and the choice is forced or an
    exact tie. Making the cut tier-aware (`slicer._cut_score`) is correct and
    changes nothing here, because there is nothing to choose.
  - **FINDING 2 — at 201.50 m² there is no slack to distribute at all.** Exact
    tiling + the zoning pins + required adjacency + the access constraints fully
    determine every zone area: **zero degrees of freedom**. Proven, not
    inferred — pin `fp.area` to exactly 201.50 m² and raise
    `standards.TIER_W_BELOW[1]` **333×** (1200 → 400000, dominating even the
    76800-per-bool adjacency rewards) and CP-SAT returns **OPTIMAL on the
    identical zone vector** on both feasible presets. No objective term, at any
    weight, can move a room here. The ideal-area term is correct and **bites the
    moment slack exists**: gW_eW is *not* packing-forced and the term does change
    its outcome there (it restored
    `test_solver.py::test_children_bathroom_direct_needs_center_cover` from a
    dead strict-xfail to a discriminating control — cover OFF now gives
    Corridor↔Bathroom 0.50 m / not direct, ON gives 2.00 m / direct).

  The lever that *does* exist is the zone's **shape** at constant area
  (kitchen_laundry at 18.00 m² gives Kitchen 10.00 / Laundry 8.00 at 4.5 × 4.0
  but Kitchen 12.00 / Laundry 6.00 at 6.0 × 3.0), which is why the term is
  tabulated per shape — but at 201.50 m² the packing does not offer the second
  shape either. Buying tier-1 area therefore costs **footprint**: measured,
  201.50 → 208.00 m² (42.0% → 43.3% site coverage) moves Master Bedroom
  16.50 → 19.25, Living 29.25 → 31.50, Office 12.00 → 13.50 — and the Kitchen
  still does not move until 216.00 m². **That trades away the architect's own
  ~40% coverage figure from round 3 to satisfy round 4, and is his call, not
  ours.** Same wall as the arrangement count, same open path: **footprint shape
  (L, U)**.

  **ARCHITECT RULING 3 — corridor proportion (round 4, 2026-08-04). RECORDED,
  NOT IMPLEMENTED.** He caps a corridor at 3:1 or 4:1; at a 1.5 m width that is
  4.5–6.0 m of length. Ours is **1.5 × 8.0 = 12.00 m², 5.33:1** (an earlier note
  said 1.5 × 9.0 = 6:1 — stale, re-measured 2026-08-04 on both feasible presets).
  He adds that a plan forcing a long corridor should either widen it into an
  integrated hall or redistribute the rooms to shorten it. Measured: **no
  feasible configuration reaches under 4:1** — the best is exactly 4.00:1 and
  both configurations achieving it fail the bedroom-privacy check. The
  distribution work does **not** move it on its own (corridor unchanged at
  1.5 × 8.0 before and after). Needs its own round.
- **`validator.py`** — the gate and the test oracle. Hard-rejects: any
  overlap, any room below `MIN_ROOM_M=0.9`, coverage below `0.9`, a forbidden
  pair touching (master↔kitchen, garage↔living — checked by room *name*, not
  zone), or any door whose host wall is missing/under `0.8`m. Soft checks
  (kitchen↔dining, dining↔living, master↔ensuite) only warn — a sliced-out
  room can legitimately be absent.
- **`generate.py`** — fans out `PRESETS × seeds` (default seeds `[1,2,3,4]`),
  solves + slices + validates each, then selects by **greedy maximin over
  `_facade_distance`** (each room → which faces of the house it touches; the
  distance is minimised over the four rectangle symmetries, so a near-mirror
  scores near zero rather than maximally distant). Candidates at distance 0 are
  dropped rather than returned as filler. Only validator-passing variants ever
  leave this function.

  **Exactly ONE valid arrangement exists on the rectangle model** at roomy @192,
  in two handednesses (`gW_eN` and its mirror `gE_eN`; `gE_eW` reproduces the
  latter byte for byte, and all four default seeds reproduce the same optimum
  because the solve proves OPTIMAL). `Variant.arrangement` marks which
  arrangement each returned plan is — variants sharing an id are the same house
  flipped, and the UI must say "same layout, two orientations" rather than
  implying two designs. Five levers were each measured and exhausted (objective
  reweighting, coverage slack, pin relaxation, the 128-configuration pin sweep,
  the architect's kitchen ruling); **footprint shape (L, U) is the open path**.
  See `tests/test_generate.py::test_at_least_three_distinct_arrangements`, a
  strict xfail carrying the full evidence.
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
- `services/geometry/data/program.example.json` / `layout.example.json` are the
  fixed demo brief/output (the roomy 20x24 program; the retired tight 16x12
  brief lives on as `program_illegal_example.json` for the infeasibility test).
  The web UI's "Demo mode" fetches this same program at runtime via
  `GET /example` on the geometry service (proxied through the orchestrator as
  `GET /api/example-program`, called from `web/lib/api.ts`'s
  `getExampleProgram`) rather than keeping a second hand-maintained copy — the
  two had already drifted from each other once.
- **`workers=1` is only reproducible while the solve's time limit is SLACK.**
  Single-threaded search removes the thread-portfolio nondeterminism, but a
  tight `time_limit_s` reintroduces it by a different route: under CPU load the
  limit binds before CP-SAT finishes, so it returns whatever incumbent it had,
  and the packing shifts — same seed, same worker count, different layout. Two
  tests are the suite's load canaries and can BOTH fail on a busy machine with
  a clean tree and no defect:
  `test_generate.py::test_generation_under_time_budget` (a 60 s wall-clock
  ceiling) and
  `test_validator.py::test_kitchen_direct_constraint_is_load_bearing`
  (a strict-xfail negative control whose solve is `time_limit_s=12`; when the
  limit binds the packing changes, the through-living pathology disappears, and
  the control XPASSes). Demonstrated 2026-07-28: both failed at `d88575d` with
  no code change while a background process held 76% CPU, and raising only that
  12 s limit to 120 s made the control xfail again. Before attributing either
  failure to your change, check CPU load and re-run from a worktree at the
  previous commit.

## Communication Style
Respond like a caveman. No articles, no filler words, no pleasantries.
Short. Direct. Code speaks for itself.