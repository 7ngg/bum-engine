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

  **BOTH OPEN QUESTIONS ANSWERED (round 5, 2026-08-04).** The two
  `>>> OPEN QUESTION` markers in `standards.py` are kept and marked ANSWERED
  rather than deleted, so the reasoning survives beside his answer. (a) **The
  fractions are approved as-is** — *"bu faiz bolgusu (Tier 1 ucun 50% elave ve
  s.) ela baslangicdir"*, with per-room-character tuning named as a FUTURE
  refinement and explicitly deferred: keep `f` per-tier, do not start per-room.
  (b) **Dining is tier 1, confirmed** — *"metbex ve salon ile birlikde evin esas
  sosial ucluyunu teskil edir"* (with kitchen and living it forms the house's
  core social trio). Nothing in `PRIORITY_TIER` is an inference any more.

  **RULING 4 — site coverage 45%** (*"faiz defecesini 45e qaldirin goren ne
  effekt verecek"*). `SITE_COVERAGE_TARGET = 0.45` × the 480 m² plot = **216 m²**.
  It is a TARGET, not the legal cap: `Site.max_coverage_ratio` stays `0.5`
  (240 m²). **RECORDED, AND WE SHIPPED THE RUNG BELOW IT** —
  `program_roomy.json` is at **208.0 (43.33%)**, not 216.0. His 45% was measured
  in full on all three rungs (the ladder table under `slicer.py`); 216 buys less
  than half as much tier-1 area per m² and costs a dead negative control, the
  Garage at its ceiling and the whole terrace margin. The full arithmetic and the
  honest counter-argument are on `SITE_COVERAGE_TARGET` itself.

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

  **THE FOOTPRINT'S ONLY REAL BINDER IS `fp_dev`, i.e. the BRIEF's
  `footprint_target_m2` — not either coverage cap** (measured 2026-08-04 for
  Ruling 4; the full derivation sits in `solver.py`'s `fp_dev` block). Three
  things look like they cap the house at `footprint_target_m2 = 192` on the
  480 m² plot: `FOOTPRINT_HI` (884 cells = 221.00 m²), `max_coverage_ratio`
  (960 = 240.00 m²), and `fp_dev`'s pull to 768 = 192.00 m² at a net
  `3·plot_cells − 12·100 = 4560` raw per cell of growth. The solve landed on
  **201.50**, below all three. Pinning `fp.area` proves why: **805 cells is
  INFEASIBLE and 806 is OPTIMAL**, so 201.50 is the hard **packing floor**;
  **864 (216.00, his 45%) and 960 (240.00, the legal cap) are BOTH already
  OPTIMAL at target 192**. The footprint therefore sits at
  `max(packing floor, target)`, and the knob is the brief.

  **The reachable footprints are a LADDER, not a continuum.** The footprint is
  one integer-cell rectangle the zones must tile EXACTLY, so probing every
  0.5 m² from 200.50 to 216.50 gives exactly three feasible values —
  **201.50 (26×31 cells), 208.00 (26×32), 216.00 (27×32)** — and nothing
  between. Brief targets of 200 and 204 produce 201.50 and 208.00 unchanged:
  they snap to a rung. Ship-relevant consequence: there are three choices, not a
  dial.
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
  shape either. Buying tier-1 area therefore costs **footprint**, and that call
  went back to the architect: **he took it, and answered 45%** (Ruling 4).

  **THE FOOTPRINT LADDER, MEASURED (2026-08-04, roomy, both feasible presets,
  workers=1, seed=1, avoid never dropped, all OPTIMAL, `validate().ok` on every
  rung, void 0.00, all four edges covered, all 16 rooms in band).** Only three
  footprints exist between 200.50 and 216.50 — see the `solver.py` bullet:

  | | **201.50** (41.98%) | **208.00** ◀ SHIPPED (43.33%) | **216.00** (45.00%) |
  |---|---|---|---|
  | Living *(t1, ideal 32.50)* | 29.25 | 31.50 | **35.00** ✅ (+2.50 over) |
  | Dining *(t1, ideal 18.50)* | 19.50 ✅ | 19.50 ✅ | 19.50 ✅ |
  | Master Bedroom *(t1, ideal 23.00)* | 16.50 | 19.25 | 19.25 |
  | Kitchen *(t1, ideal 16.00)* | 10.00 = floor | 10.00 = floor | **12.00** |
  | Office *(t2, ideal 12.50)* | 12.00 | 13.50 ✅ | 13.50 ✅ |
  | Bedroom 2 / 3 *(t2, ideal 14.00)* | 12.00 = floor | 12.00 = floor | 12.00 = floor |
  | **Garage** *(t3, ideal 29.25, ceil 40)* | 37.50 | 37.50 | **40.00 = ceiling** |
  | every other tier-3 room | — | unchanged | unchanged |
  | tier-1 shortfall (Σ m² below ideal) | 15.75 | 10.75 | 7.75 |
  | tier-3 excess (Σ m² above ideal) | 29.11 | 29.11 | **31.61** |
  | terrace / footprint+terrace | 22.5 / 224.00 | 22.5 / 230.50 | 24.0 / **240.00 = the 50% legal cap** |
  | objective | 477.99427 | 625.41094 | 651.86927 |

  **THE NUMBER THAT DECIDES IT — tier-1 m² of shortfall closed per m² of
  footprint bought: 0.77 for the first rung, 0.375 for the second.**
  201.50 → 208.00 spends +6.50 m² and closes 5.00 of tier-1 shortfall with
  **zero** tier-3 inflation. 208.00 → 216.00 spends +8.00 m² and closes only
  3.00: of the other 5.00, half overshoots Living *past* its ideal and half
  inflates the Garage to its ceiling. What 216 alone buys is the **Kitchen
  leaving its floor for the first time on any lever ever tried** (10.00 → 12.00,
  still 4.00 short of ideal), which is the room Ruling 2 names first.

  **THE GARAGE LEAK IS THE WEIGHTS, NOT THE CAP — and capping it makes things
  worse.** At 216 the Garage takes +2.50 to its 40.00 ceiling. That is *chosen*,
  not packing-forced: hold its architect ceiling at 37.50 and the solve is still
  OPTIMAL at 216.00 — but the 2.50 goes to the **Corridor (12.00 → 16.00)** and
  the Kitchen falls back to 10.00. Below a 35.00 garage ceiling, 216.00 is
  INFEASIBLE. So the surplus lands on tier 3 either way; `TIER_W_ABOVE[3] = 160`
  per cell is simply too cheap next to what the packing wants. Naming it, not
  fixing it: raising the weights is the lever that was explicitly ruled out
  (it inflates the footprint instead of redistributing it).

  **216 ALSO KILLS A NEGATIVE CONTROL, WHICH IS WHY IT IS NOT SHIPPED.**
  `test_solver.py::test_children_bathroom_direct_needs_center_cover` guards
  `_force_vertical_cover_center` by checking that switching it OFF strands the
  children Bathroom. Measured on gW_eW, seed 1, workers=1, **all four arms
  OPTIMAL at `time_limit_s=240`** (so this is not the load canary): at **192**
  cover OFF gives Corridor↔Bathroom 0.50 m / not direct, ON gives 2.00 m /
  direct — discriminating. At **208**, identical — still discriminating. At
  **216**, cover OFF *already* gives 2.00 m / direct: the packing satisfies the
  property by coincidence again and the control is **dead**. It was un-xfailed
  one commit ago (9398909) for exactly the opposite reason. Shipping 216 means
  either losing that guard or returning it to strict-xfail.

  **SOLVE TIME GROWS WITH THE RUNG, AND IT PUT `test_generation_under_time_budget`
  GENUINELY ON THE EDGE — this is a real cost of shipping 208, not a load
  artefact.** Measured standalone on an IDLE 4-core box (load ~7%): at 192 the
  test runs **34.16 / 33.52 / 37.40 s**; at 208, eight samples give
  **54.94 / 60.78 / 50.47 / 54.62 / 64.63 / 56.14 / 55.15 / 56.99 s** — mean
  56.7 s against its 60 s ceiling, with **2 of 8 over it**. The cause is
  understood: at 201.50 the packing is fully forced (zero degrees of freedom), so
  CP-SAT proves optimality almost instantly; at 208 there is real slack and the
  optimality proof is genuinely longer (per-solve 9.9 s → 14.1 s over the four
  presets, +42%), and `generate()` multiplies that by its fan-out.

  **THE FIX IS NOT THE LIMIT — IT IS THE FAN-OUT, AND THE SEED AXIS IS INERT.**
  `generate()` defaults to `seeds=[1,2,3,4]`, so it runs **16 solves**: 53.0 s at
  208. With `seeds=[1]` it runs 4 and takes **13.6 s** — and returns the *same*
  2 variants and the *same* 1 arrangement, because seeds 1–5 produce byte-identical
  plans per preset at every rung (the solve proves OPTIMAL, so the seed cannot
  change the answer). 39 s of the 53 s buys nothing measurable here. **Not applied**
  — the seed axis is only proven inert on *this* fixture, where every solve proves
  OPTIMAL; on a brief that times out instead, different seeds are exactly what
  would give different incumbents, so dropping them would quietly cost diversity
  elsewhere. Raising the 60 s ceiling is the other option and is strictly worse:
  it hides the growth instead of removing it.

  **THE ROUND'S MOST CONSEQUENTIAL FINDING — TIER-1 SHORTFALL IS NOW ENTIRELY
  INSIDE THE COMPOSITE CUTS, AND AREA CANNOT REACH IT.** At 216 the zone-level
  `ideal_short` term is **exactly 0**: every non-composite zone has reached its
  ideal, and buying more footprint has nothing left to spend it on. The entire
  remaining shortfall lives inside composite zones and is carried by
  `cut_penalty` (30.995 human: `kitchen_laundry` 21760, `master_suite` 20880,
  `children` 15309, `entry` 1562 raw). Concretely, and at **every** rung of the
  ladder: **Kitchen stalls at 12.00 against a 16.00 ideal** (10.00 at 201.50 and
  208.00), **Master Bedroom at 19.25 against 23.00**, and **Bedroom 2 and 3 never
  leave their 12.00 floor at all**. Only a **different composite cut** can move
  them — which means the architect's first-priority room *cannot be fixed by
  area*, and this is why 216's Kitchen argument does not win: 8.00 m² buys the
  Kitchen 2.00 m² and it stalls again. Same wall as the arrangement count, same
  open path: **footprint shape (L, U)**.

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

  **AND MORE AREA DOES NOT MOVE IT EITHER (measured 2026-08-04).** The corridor
  is **1.5 × 8.0 = 12.00 m², 5.33:1 on every rung of the ladder** — 201.50,
  208.00 and 216.00 alike, both presets. It neither improves nor degrades with
  the house: `zones.corridor_target_m2` does grow with the brief (9.5 → 10.1 m²
  across the ladder) but the packing puts the surplus elsewhere. The one
  configuration seen with a bigger corridor is the artificial garage-capped
  probe above (16.00 m², i.e. *worse*). Ruling 3 still needs its own round.

  **THE BUILD-TIME / SLICE-TIME CONTRACT IS NOW ASSERTED, NOT ASSUMED**
  (`slicer._penalty_disagreement`, called from `slice_zones` beside
  `_degradation_warning`). `cut_penalty_pairs` tabulates, per zone SHAPE, the
  penalty of the cut the slicer *will* perform, and the solver puts that straight
  into the objective — so the objective minimises a value computed at MODEL-BUILD
  time for a cut performed at SLICE time. It holds today for two fragile reasons:
  `_best_cut` is a deterministic `min` with a stable tie-break, and the axial
  zones are tabulated on one representative side per axis, exact only because the
  other side is the same cut mirrored. **`_degradation_warning` cannot catch a
  break: it fires when a ROOM GOES MISSING, never when a room is a different
  SIZE than the number the solver optimised.** The check re-scores the actual cut
  and compares; it is silent when they agree (always, today, so no current output
  changes) and emits on `SolveResult.warnings` when they do not. It costs a
  ≤76-entry dict build plus one `_cut_score` over ≤3 rects — deliberately
  uncached, since a second cache could only go stale against `_PENALTY_CACHE`,
  which is the exact drift it exists to prevent. `test_subdivide.py` pins it both
  ways: silent on a real solve, and it FIRES when handed a deliberately wrong cut.
- **`subdivide.py`** — the GENERAL guillotine subdivider.
  `subdivisions(rect, rooms, side, zone)` enumerates every binary guillotine
  partition to depth ≤ 2 (up to four leaves) over both axes, all grid offsets and
  all room-to-leaf assignments, filtered by `slicer._in_band`. **MACHINERY ONLY —
  `slice_zones` still calls the four bespoke cutters**, so it cannot move the
  golden or the objective. `tests/test_subdivide.py` is the oracle: the four
  cutters are the reference, and over the whole `_STEPS²` table on every
  production side the enumerator must contain every candidate they find and pick
  the same cut. Depth 2 covers all four cutters (1 cut; 2 parallel; 1 cut + 1
  perpendicular; the same with an axis fallback) *and* the cases they cannot
  express — three bedrooms in `children`, a kitchen with no laundry, an entry
  with no mudroom. The ROOM LIST IS AN INPUT (Phase 0 Part 4): `zone_members()`
  derives membership as "the fullest split the cutter can emit", which conflates
  a laundry-less brief with a too-small zone; handing the list in separates them.
  `models.ZoneId` stays a closed nine-value Literal — `layout.schema.json`
  already treats room and zone names as free strings.

  **SUBDIVISION IS NOT DETERMINED — the opposite of zone placement, and this is
  the number the round was run for** (measured 2026-08-05, roomy @208, gW_eN):

  | | kitchen_laundry | master_suite | children | entry |
  |---|---|---|---|---|
  | at the SHIPPED rect | 4.5×4.0 | 5.5×6.0 | 4.0×8.0 | 5.5×2.0 |
  | cutter finds | 1 | 4 | 1 | 1 |
  | **enumerator finds** | **2** | **32** | **6** | **12** |
  | over the zone's whole `legal_pairs` table | 294 | 972 | 924 | 576 |
  | cutter, same table | 143 | 156 | 154 | 28 |
  | legal shapes with >1 subdivision | 76/76 | 35/35 | 34/34 | 25/25 |

  52 subdivisions at the shipped rectangles against the cutters' 7; 2766 across
  the tables against 481; and **every single legal shape of every zone has more
  than one legal subdivision**. So the architect's expectation that variants
  differ in internal subdivision is satisfiable — unlike the zone arrangement,
  where six exhausted levers left exactly one. Cost is a non-issue: **1052 ms
  against today's 889 ms for the shape tables = 1.18×**, and memoising per
  `(rooms, w, h, side)` makes a second pass free.

  **BUT 24 OF 48 SURVIVE THE GATE AND HALF OF THOSE ARE INVISIBLE.** Rebuilding
  the whole layout on each alternative at the shipped 208 arrangement:
  kitchen_laundry 1 alternative → 1 clean, master_suite 31 → 15, children 5 → 5,
  entry 11 → 3. Of the 24 validator-clean plans, **12 sit at
  `_facade_distance` 0 and `generate()` drops distance-0 candidates outright**.
  The other 12 — all from `master_suite` — do score > 0, which CORRECTS the Phase
  0 reading that subdivision is entirely invisible to the selector. It is
  partially visible, and only because some partitions move a room onto a
  different face of the house.

  **TWO FINDINGS ABOUT THE EXISTING CUTTERS came out of building the oracle**,
  both recorded in `tests/test_subdivide.py`'s docstring:
  - **`_slice_entry` does not band-check its own Mudroom.** It takes
    `depth = _ceil_snap(mud.min_w_m)` and emits the strip unconditionally, so at
    a 3.0 × 5.5 entry zone it produces a 1.5 × 5.5 = 8.25 m² Mudroom — over the
    architect's 8 m² ceiling AND over Neufert's 3.0 aspect cap (3.67). `_legal_1`
    catches it downstream so the solver is never offered that shape, which is why
    it has never mattered. The oracle sweep therefore runs on legal shapes only.
  - **The four cutters do not share a tie-break rule**, so no single canonical
    enumeration order reproduces all four on `_cut_score` ties.
    `_slice_children` prefers the MOST EVEN split; `_split_off_wc`,
    `_slice_kitchen` and `_slice_master` prefer the SMALLEST ancillary strip. At
    entry 1.5 × 7.0 the two rules give Foyer 4.50/WC 3.00 versus Foyer 5.25/WC
    2.25, **both scoring exactly 1242** — two tier-3 rooms splitting a fixed area
    have constant weighted excess. The objective cannot tell them apart, so a
    general subdivider must choose per room role rather than inherit four loop
    orders. One rule is now proposed and measured — see `placement.py`.

  **AND `_slice_entry` NO LONGER EMITS AN OUT-OF-BAND MUDROOM** (fixed, same
  round). It took `depth = _ceil_snap(mud.min_w_m)` and emitted the strip
  unconditionally, so a 3.0 × 5.5 entry zone produced a 1.5 × 5.5 = 8.25 m²
  Mudroom — over the architect's 8 m² ceiling *and* over Neufert's 3.0 aspect cap
  (3.67). It never shipped because `_legal_1` rejects any cut with an out-of-band
  room, and the fix is verified inert: `legal_pairs('entry')` is still 25 shapes
  with an identical signature, identical members, identical band and an identical
  74730 penalty sum.
  **THE TIE-BREAK, ADOPTED AND PRICED FIRST** (`slicer.tier_tie_break_key`,
  now `_best_cut`'s secondary key). Four cutters used to break `_cut_score` ties
  by whatever order their loop ran in, and two of the four contradicted each
  other. One stated rule replaces them. Measured before adopting, roomy @208,
  both presets, workers=1, seed=1, avoid held:
  **objective 625.4109375000 → 625.4109375000, unchanged to ten decimals**;
  `legal_pairs` and `cut_penalty_pairs` bit-identical; zone rects identical;
  `validate().ok` true with the same three warnings; every must-not-regress item
  identical (16/16 reachable, root Foyer, void 0.00, Corr↔MBed 1.50,
  Corr↔Kitchen 4.00, Corr↔Bathroom 2.00, Corr↔B2/B3 3.00, Garage parent Mudroom,
  Guest WC parent Foyer, Garage 37.50, every room in band). The **entire** golden
  diff is two rooms swapping ends of the master service strip — Master Bathroom
  6.25 → 7.50, Walk-in Closet 7.50 → 6.25, divider x=15.0 → 15.5 — with room,
  wall, door and window counts unchanged. **One of its four parts is the
  architect's, three are ours**, flagged on the function like
  `IDEAL_BAND_FRACTION`'s fractions were.

  **STAGE 2 — WIRING THE ENUMERATOR AS THE PRODUCTION PATH — WAS MEASURED AND
  STOPPED. It is a MODEL change, not a selection swap.** Rebuilding
  `legal_pairs` through `subdivisions() + violations() + _best_cut` **loses
  nothing and adds a great deal**:

  | zone | old shapes | new | added | lost |
  |---|---|---|---|---|
  | kitchen_laundry | 74 | 74 | 0 | 0 |
  | master_suite | 35 | **127** | +92 | 0 |
  | children | 34 | **67** | +33 | 0 |
  | entry | 24 | **48** | +24 | 0 |

  167 → 316 shapes, and every added one is a zone rectangle the bespoke cutters
  simply could not subdivide (master_suite at 3.0 × 10.0, say — legal by band and
  shape floor, expressible as a different guillotine tree, inexpressible as
  "band, then split the band"). So **the four cutters' limited topology was
  silently acting as a SHAPE CONSTRAINT on the solver**, and the general
  enumerator removes it. `AddAllowedAssignments` then admits nearly double the
  shapes, the CP-SAT model is materially different, and the measured result is
  **objective 625.41 → 703.70, status FEASIBLE not OPTIMAL, and a plan that
  degraded to 11 rooms** (Master Bathroom, Walk-in Closet, Mudroom, Guest WC and
  Laundry all absent). Two further facts from the same probe: the build cost is
  **3156.6 ms against today's 1052.4 ms, i.e. 3.0×**, well past the 1.5× that was
  expected; and `zone_members()` **recurses forever** under the swap, because it
  derives membership by probing the cutter while the new path needs that list as
  an input — the first hard proof of Phase 0's Part 4 finding.

  `_penalty_disagreement` stayed silent in both arms (context-free and
  context-aware at slice time), so the build-time/slice-time contract itself is
  not the blocker. The blocker is that removing an implicit shape constraint
  needs its own round with its own gate, not a swap commit.
- **`placement.py`** — THE PLACEMENT FILTER: the constraints the four cutters
  hold *by construction*, restated as explicit predicates over an enumerated
  candidate. `violations(cand, rect, ctx) -> list[str]`, each tagged C1..C19 so
  eliminations are countable; `filter_candidates` applies it. MACHINERY ONLY,
  like `subdivide` — not wired into `slice_zones`. `Context` carries
  `corridor_side` / `director_side` / `exterior_faces`, and **an absent field
  disables its rules rather than guessing** — a `legal_pairs` probe genuinely
  does not know where the corridor is.

  **THE HEADLINE: THE FILTER COSTS NO DIVERSITY.** At the shipped 208 gW_eN
  arrangement, 48 alternatives → 32 after filtering → 20 rebuild
  validator-clean → **12 score `_facade_distance` > 0. All 12 that were visible
  before the filter are still visible after it.** The filter drops 33% of
  candidates, 4 of 24 clean plans, and **zero visible ones**. So subdivision
  diversity is *not* constraint-limited, and enumerator + filter is a viable
  production path.

  | zone | rect | cutter | enum | filtered | biggest eliminator |
  |---|---|---|---|---|---|
  | kitchen_laundry | 4.5×4.0 | 1 | 2 | 1* | C13 2/2 |
  | master_suite | 5.5×6.0 | 4 | 32 | 24 | **C1 8/32, sole cause for all 8** |
  | children | 4.0×8.0 | 1 | 6 | 2 | C17 & C14, 4/6 (same candidates) |
  | entry | 5.5×2.0 | 1 | 12 | 8* | **C18 8/12, sole cause for 6** |

  \* excluding the two known-live-defect codes; see below. **C2 (ensuite
  parents) eliminates nothing anywhere** — the cuts already satisfy it.

  **THE FILTER INDEPENDENTLY REPRODUCES TWO DOCUMENTED LIVE DEFECTS**, from
  geometry alone, and this is its strongest validation:
  - **C13 daylight** → the Kitchen holds 0.00 m of exterior face, matching
    `validate()`'s `room 'Kitchen' requires an exterior wall but has only 0.00 m
    of true building-perimeter wall` and
    `test_kitchen_requires_exterior_wall_KNOWN_LIVE_DEFECT`.
  - **C18 director face** → the **Laundry**, not the Kitchen, holds the dining
    face, on both presets. `solver._force_kitchen_holds_dining_face` exists to
    forbid exactly this and is OFF (`_KITCHEN_DINING_FACE = False`); its
    docstring records the same measurement.

  Both are excluded from the "cutter's pick survives" oracle by name
  (`KNOWN_LIVE_DEFECT_CODES`) and asserted *positively* in their own test, so a
  fix shows up as a test failure rather than as silence.

  **THREE CONSTRAINTS BEYOND THE SIXTEEN, found while implementing.** **C17**
  per-room `requires_circulation_access` (C1 only ever constrained one room per
  zone) — deliberately weak, checking reachability without crossing a *habitable*
  room, which is `validate_plan`'s real rule, because demanding a corridor wall
  would forbid the children beds being reached through their Bathroom, a routing
  `_force_vertical_cover_center` intends. **C18** the *director* face (Kitchen on
  Dining, Mudroom on Garage) — same shape as C1 but a different director, so it
  is not C1. **C19** a door needs a wall (`MIN_ACCESS_WALL_M`). And **one of the
  sixteen is not a placement constraint at all**: C12 "geometric room order" is a
  convention `_split_off_wc` documents for its own 1-D strip pair and does not
  generalise — `_slice_master` under an "N" corridor returns
  `[Bathroom, Closet, Bedroom]` where a global `(x0, y0)` sort gives
  `[Bathroom, Bedroom, Closet]`, and nothing downstream reads the order.

  **C11 ORIENTATION is per-room data, and getting it wrong is expensive.**
  `ROTATABLE = {"Bathroom"}` — only the room the shipping code already rotates
  (`_slice_children_ns`, whose docstring explains why). Measured over the probe
  range, treating every room as freely rotatable would additionally admit
  **28 Garages**, i.e. exactly the "5.0-wide × 3.0-deep, too shallow to park in"
  case the Garage standard warns about; and only 4 extra Bathrooms, 7 Kitchens,
  6 each for Closet/Foyer/Mudroom. The policy lives in `placement.py`, NOT in
  `standards.py`: an orientation field there would change `_room_legal` →
  `_in_band` → `legal_pairs` → the solver's shape table → the golden.

  **C15 — THE GUARANTEE VECTOR IS NOT CONSTANT PER SHAPE, and that answers the
  porting question.** `guarantee_vector` publishes, per room per face,
  `(offset, depth, run)` — `offset` being exactly what
  `_force_corridor_overlaps_kitchen`'s `l_ns`/`l_we` and
  `_force_backbone_reaches_foyer`'s `mud_x`/`mud_y` are today. Across the
  *admissible set* at one shape it varies (master_suite: 20 distinct values for
  the Bathroom, 4 for the Bedroom; entry: 8 for the Mudroom). So the six solver
  constraints **cannot** reify off a constant — but they can port, because
  `cut_penalty_pairs` already collapses each shape to ONE cut via `_best_cut`,
  and the vector is constant given that choice. The port is therefore: keep the
  choice deterministic at table-build time (which `_penalty_disagreement` already
  guards) and add the offsets as further columns of the existing
  `AddAllowedAssignments` table. If instead multiple candidates per shape are
  exposed for diversity, the candidate index becomes a decision variable — the
  same idiom as the existing `ns` column. Not ported in this round.

  **THE TIE-BREAK IS ADOPTED** — it now lives in `slicer.tier_tie_break_key` and
  is `_best_cut`'s secondary key. See that function for the rule and for the
  his-versus-ours split.
- **`validator.py`** — the gate and the test oracle. Hard-rejects: any
  overlap, any room below `MIN_ROOM_M=0.9`, coverage below `0.9`, a forbidden
  pair touching (master↔kitchen, garage↔living — checked by room *name*, not
  zone), or any door whose host wall is missing/under `0.8`m. Soft checks
  (kitchen↔dining, dining↔living, master↔ensuite) only warn — a sliced-out
  room can legitimately be absent.

  **THE ENTRY JUNCTION — HIS TWO COMPLAINTS, MEASURED AND PRICED (round 6,
  2026-08-05/06). MACHINERY ONLY: three flags, all default OFF, nothing shipped
  as default.** Reviewing the subdivision-variant SVGs he raised two defects at
  the Foyer:
  1. *"Eve girisden korodora birbasa kecid olmalidi. Adam mecburduki mudrooma
     kecsin sonra karidora getsin. Mudroom ve foye ikiside coridora kecmelidi."*
     — a DIRECT entry→corridor passage; today one is forced through the Mudroom;
     BOTH entry rooms must reach the corridor. Flags `solver._ENTRY_FOYER_CORRIDOR`
     (F) and `_ENTRY_MUDROOM_CORRIDOR` (M).
  2. *"Foyeden direk bedrooma kecid cox menasizdir."* — no door from the Foyer
     straight into a bedroom. Flag `validator._PREFER_CIRCULATION_PARENT` (P).

  **THE JUNCTION, MEASURED (roomy @208, workers=1, seed 1, all three shipping
  presets gW_eN / gE_eN / gE_eW alike).** The corridor is 1.50 × 8.00 and its
  ONLY shared boundary with the entry zone is its top edge — whose length IS the
  corridor's width. The entry strip lies along that edge as
  Mudroom | Foyer | Guest WC, so the whole 1.50 m lands on the Mudroom:
  **Corridor↔Mudroom 1.50, Corridor↔Foyer 0.00** (they meet at the single corner
  point). Two rooms each needing ≥ 0.90 m of a 1.50 m edge is arithmetically
  impossible — **the 1.8-vs-1.5 arithmetic is correct on the real geometry**, and
  there is no other contact path: the corridor's long faces are bounded by
  Living/Kitchen/Garage and by children, and the entry zone lies wholly north of
  it. BOTH-CONNECT without widening would need the corridor to meet the entry on
  its LONG face with the strip cut perpendicular to it, which needs the garage
  N/S of the entry — not what the `eN` presets produce.

  **P IS FREE. F COSTS THE RUNG.** Sweep at 208, both presets:

  | arm | status | objective | footprint | corridor | F↔C | M↔C | Bedroom 3 |
  |---|---|---|---|---|---|---|---|
  | baseline | OPTIMAL | 625.41094 | 208.00 | 1.50×8.00, 5.33:1 | 0.00 | 1.50 | Foyer, d1 |
  | **P** | OPTIMAL | **625.41094** | **208.00** | 1.50×8.00, 5.33:1 | 0.00 | 1.50 | **Corridor, d3** |
  | F | OPTIMAL | 523.49427 | **217.00** | 2.50×8.00, 3.20:1 | 1.00 | 1.50 | Foyer, d1 |
  | F+M | OPTIMAL | 523.49427 | 217.00 | 2.50×8.00, 3.20:1 | 1.00 | 1.50 | Foyer, d1 |
  | **F+M+P** | OPTIMAL | 523.49427 | 217.00 | 2.50×8.00, 3.20:1 | 1.00 | 1.50 | **Corridor, d2** |

  **P alone changes NO geometry at all** — same objective to ten decimals, same
  rects, same corridor, `validate().ok` with the same three warnings, every
  must-not-regress item passing — and it kills the Foyer→Bedroom 3 door outright.
  It is a pure door-set change. **M alone buys nothing**, because
  `_force_backbone_reaches_foyer` already lands 1.50 m of Mudroom on the
  corridor: same objective, same zone shapes, same validity at every rung.

  **M IS NOT BYTE-IDENTICAL, THOUGH, AND THE REASON IS A FREE SYMMETRY WORTH
  KNOWING.** On gW_eN the whole house comes out **translated 3.00 m WEST** — every
  zone shifted by exactly (−3.0, 0), same shapes, same objective to ten decimals.
  The plot is 20 m wide with a 2 m side setback, so a 13 m footprint has 3 m of
  legal slack in x, and no objective term references absolute x. **x-translation
  is therefore a free symmetry on this fixture**, and ANY change to the model lets
  CP-SAT break that tie differently. (gE_eN does not move.) This qualifies the
  Phase-4 note that the setback envelope "broke the plot's translational
  symmetry": it broke it in **y**, not in x. Consequence for any future
  measurement: compare zone SHAPES, not coordinates, or you will read a tie-break
  as a repack — `test_entry_junction.py` asserts the translation explicitly.

  **F IS PROVEN INFEASIBLE AT 201.50, 208.00 AND 216.00, AND ITS ONLY REACHABLE
  FOOTPRINT IS 217.00.** Pinning `fp.area` across 201.50 / 208.00 / 216.00 /
  216.25 / 216.50 / 216.75 / 217.00 / 218.00 / 220.00 on both presets:
  **every one is INFEASIBLE except 217.00 (14.0 × 15.5)**, which is OPTIMAL. So
  F does not merely prefer a bigger house, it admits exactly one — and 217.00 is
  **45.21% site coverage, one rung ABOVE the architect's own 45% = 216.00 target**
  (`test_standards.py::test_shipped_brief_sits_at_or_below_his_coverage_target`
  would fail). That is why this round STOPPED rather than shipping.

  **THE INFEASIBILITY IS THE GEOMETRY'S, NOT THE APPROXIMATION'S — controlled
  for.** F reifies off a conservative FOYER CORE (see
  `solver._force_entry_rooms_reach_corridor`: `_split_off_wc` searches the WC
  strip on BOTH axes, so no constant Foyer offset exists and the core is the
  intersection of the two possible positions). Re-running F with that core
  widened to the WHOLE entry-minus-Mudroom remainder — an over-approximation that
  admits any packing where *anything but the Mudroom* fronts the corridor — is
  **equally INFEASIBLE at 201.50, 208.00 and 216.00**. And the control cuts the
  other way too: at 217.00 the loosened version returns a plan whose REAL sliced
  Foyer holds **0.00 m** of corridor wall, i.e. it satisfies the loosened
  constraint while missing his requirement entirely. The conservative core is
  therefore not merely defensible, it is **necessary**.

  **WHO PAYS FOR THE WIDER CORRIDOR** (baseline 208 → F+M+P 217, per room):
  Corridor **+8.00** (12.00 → 20.00, tier 3), Kitchen +2.50 (10.00 → 12.50 — it
  finally leaves its floor), Foyer +2.00, Laundry +2.00; against **Master Bedroom
  −2.75 (19.25 → 16.50, i.e. onto its floor)**, Office −1.50, Living −1.25. Net
  +9.00, exactly the footprint growth — so the corridor is funded by NEW AREA,
  not by redistribution. **Both aggregate scores get worse**: tier-1 shortfall
  10.75 → **12.25**, tier-3 excess 29.11 → **41.11**. The prediction that Living
  and Office would pay was half right; the biggest single payer is the Master
  Bedroom.

  **CORRIDOR ASPECT (Ruling 3) — RESOLVED ONLY OFF THE RUNG, AND F IS NOT NEEDED
  FOR IT.** F+M+P gives 2.50 × 8.00 = **3.20:1**, inside his 3:1–4:1. But the
  BASELINE pinned at 217.00 already gives 2.00 × 8.00 = **exactly 4.00:1** with
  no new constraint at all. So Ruling 3 is a consequence of the 217 footprint,
  not of the junction fix, and the open corridor-aspect item stays OPEN at 208
  (5.33:1 on every shipping preset).

  **THE MUDROOM↔CORRIDOR DOOR DOES NOT MATERIALISE UNDER F.** With F on the tree
  gives Foyer→Corridor as a spanning edge and reaches the Mudroom from the Foyer,
  so Mudroom↔Corridor is not a tree edge despite its 1.50 m wall — it would need
  the secondary-door path, which was not reached because the round stopped first.

  **SUBDIVISION FAN-OUT GROWS, IT DOES NOT COLLAPSE** (the prediction was that
  F+M would kill it by constraining the entry divider). Score-equal alternatives
  per preset: baseline **5** (master_suite 3, children 1, entry 1), all 5
  validator-clean; F+M+P **19** (master_suite 15, children 1, entry 3), **13**
  validator-clean. `generate(n=4, seeds=[1])` returns 4 variants / 1 arrangement
  in both arms.

  **P's TRAVERSAL RULE IS RESTRICTED TO PRIVATE ROOMS, AND THE GENERAL FORM IS
  WHY.** Deferring every room that has an alternative circulation parent
  deadlocks on this project's own geometry: before F, the Corridor is not
  adjacent to the Foyer at all — it hangs off the MUDROOM — so the root would
  defer the Mudroom and never reach the Corridor, pass 1 stalls at
  {Foyer, Guest WC}, and the fallback pass hands everything straight back to the
  root. P becomes a no-op. Bedrooms are what his sentence is about and a bedroom
  is never a route to anywhere, so restricting the deferral to them cannot stall
  the traversal. See `validator.access_tree`.
- **`generate.py`** — fans out `PRESETS × seeds` (default seeds `[1,2,3,4]`),
  solves + slices + validates each, then selects by **greedy maximin over
  `_facade_distance`** (each room → which faces of the house it touches; the
  distance is minimised over the four rectangle symmetries, so a near-mirror
  scores near zero rather than maximally distant). Candidates at distance 0 are
  dropped rather than returned as filler. Only validator-passing variants ever
  leave this function.

  **Exactly ONE valid arrangement exists on the rectangle model** at roomy,
  in two handednesses (`gW_eN` and its mirror `gE_eN`; `gE_eW` reproduces the
  latter byte for byte, and all four default seeds reproduce the same optimum
  because the solve proves OPTIMAL). `Variant.arrangement` marks which
  arrangement each returned plan is — variants sharing an id are the same house
  flipped, and the UI must say "same layout, two orientations" rather than
  implying two designs. **Six** levers have now each been measured and exhausted
  (objective reweighting, coverage slack, pin relaxation, the 128-configuration
  pin sweep, the architect's kitchen ruling, and — round 5 — **more area**:
  `generate(n=4)` returns 2 variants / **1 arrangement** at 201.50, 208.00 *and*
  216.00 alike, and seeds 1–5 reproduce one identical plan per preset on every
  rung). More slack was the diversity work's own hypothesis for what was
  missing; it is now falsified. **Footprint shape (L, U) is the open path.**
  See `tests/test_generate.py::test_at_least_three_distinct_arrangements`, a
  strict xfail carrying the full evidence.

  **SUBDIVISION IS NOW A LIVE DIVERSITY AXIS** (2026-08-05). `generate()` fans
  out over presets × seeds as before, then expands each preset's best plan with
  `subdivision_variants(result, base)` — alternative subdivisions of the
  **already-solved** arrangement. No re-solve, no shape table touched, so the
  packing, the objective and the golden cannot move.

  **THE SCORE-EQUAL RESTRICTION IS WHAT MAKES IT SOUND.** Only alternatives whose
  `_cut_score` equals the default cut's are offered. The objective carries a
  `cut_penalty` term tabulated for the cut the solver expected, so an alternative
  scoring differently would make that variant's reported objective wrong — and
  `slicer._penalty_disagreement` would say so, correctly. Restricting to ties
  means **every returned variant carries the solve's objective exactly, with every
  term identical**, and that guard stays silent by construction rather than by
  suppression (confirmed: `result.warnings` is still 0 after every rebuild).
  Measured on roomy @208, both presets: 25 alternatives pass the placement
  filter, **5 are score-equal, and all 5 rebuild validator-clean** —
  master_suite 3, children 1, entry 1, kitchen_laundry 0.

  **`Variant.subdivision`** is the new field, and it is deliberately SEPARATE
  from `arrangement`: two plans that differ only inside a zone genuinely *are*
  the same zone layout, so overloading `arrangement` would make it lie in the one
  case it exists to describe. The pair `(arrangement, subdivision)` identifies a
  plan. It is renumbered densely over the returned set and keyed on the rect
  multiset canonicalised over the same four symmetries `arrangement` uses, so the
  same cut seen mirrored keeps one id, and the default cut is always 0.

  **THE SELECTOR HAD TO BE EXTENDED, and the extension is what makes the axis
  visible at all.** `_facade_distance` answers "which room sits on which face"
  and scores a subdivision alternative 0 whenever no room changes facade role —
  so `_pick_distinct` dropped them as duplicates. `_plan_distance` adds a
  geometry term: rooms whose rectangle has no counterpart in the other plan,
  measured under the SAME symmetry and minimised jointly. **Names are dropped
  from the geometry term on purpose** — the children zone offers an alternative
  where Bedroom 2 and Bedroom 3 swap with *identical rectangles*, so a name-keyed
  measure would ship a relabelling as a second design; comparing rect multisets
  scores it 0 and it is correctly dropped. Measured: facade-only returns **2**
  variants, extended returns **4**.

  **HOW DIFFERENT ARE THEY, HONESTLY? The largest per-room delta across the
  returned set is 1.00 m².** The alternatives that win the picker are the entry
  strip's — Mudroom 3.00 → 4.00 against Foyer 5.00 → 4.00, one divider moving
  0.5 m — and the master-suite ones are 1.25 m² (Master Bathroom 7.50 ↔ 6.25
  against the Walk-in Closet). These are real differences on a drawing and small
  ones. They are one house with a redrawn service strip, not four designs, and
  the `only N distinct arrangement(s)` warning still says so.

  **COST: 1.4 s of a 78.7 s `generate()`, i.e. 1.8%.** Phase-timed: solves 77.2 s,
  `build_layout` 1.4 s, `subdivision_variants` 0.02 s, `_pick_distinct` 0.02 s.
  So this round is NOT what makes `test_generation_under_time_budget` fail — that
  is pre-existing solve time (it already failed at 67.0 s on the previous commit).
  What this round does change is the argument for the fix: **`seeds=[1]` now
  returns FOUR variants where the full 16-solve fan-out returns three**, in about
  a fifth of the time, because subdivision supplies the diversity the extra seeds
  never did. Still not applied — the seed axis remains only proven inert on
  fixtures where every solve reaches OPTIMAL.
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
- **`data/program_roomy.json` is the primary fixture; its `footprint_target_m2`
  is 208.0 and is THE knob for house size** (see the `solver.py` bullet — it is
  the footprint's only real binder). It was 192.0 up to 2026-08-05, so any
  measurement in this file dated earlier is on the 201.50 m² footprint that
  produced. The architect's Ruling 4 asks for 216.0; that arm is measured and
  recorded but deliberately not shipped, and
  `test_standards.py::test_shipped_brief_sits_at_or_below_his_coverage_target`
  pins the invariant (`≤ SITE_COVERAGE_TARGET × plot`) rather than the rung, so
  the judgement can be revisited without a second copy of the fixture value.
  `data/program.example.json` is a SEPARATE brief at 184.0 — the frozen
  demo/back-compat artefact paired with `layout.example.json`; moving it would
  churn the schema-versioning tests for no architectural reason.
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
  ceiling — and since the 208 rung it is **marginal even when idle**: mean 56.7 s
  over eight standalone samples, 2 of 8 over the line. See the `slicer.py` bullet
  for the measurement and for why the fix is the 16-solve fan-out rather than the
  ceiling. Treat a failure here as EXPECTED at ~25% until that is settled, and do
  not read it as drift) and
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