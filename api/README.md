# Orchestrator API (ASP.NET Core + EF Core/SQLite)

Standalone service that runs the pipeline end to end: a brief becomes a program,
the geometry service generates gated variants, they are persisted, and each can
be exported to a native `.rvt`.

```
brief -> [geometry /extract] -> [geometry /generate] -> persist program+variants
      -> return variants (SVG) -> trigger Revit export -> deliver .rvt
```

Target **.NET 10**. Persistence is **EF Core + SQLite** (`bumengine.db`, created
on startup via `EnsureCreated`).

## Endpoints
| method | path | purpose |
|--------|------|---------|
| GET  | `/health` | liveness + configured geometry URL + export mode |
| POST | `/api/projects` | `{prompt}` **or** `{program}`, `n` → generate + persist, returns variants |
| GET  | `/api/projects` | list projects |
| GET  | `/api/projects/{id}` | project + variants (with SVG) |
| GET  | `/api/variants/{id}/layout` | the variant's schema-clean `layout.json` |
| POST | `/api/variants/{id}/export` | hand off / submit a Revit build |
| GET  | `/api/variants/{id}/rvt` | download the `.rvt` (409 until Ready) |

`{prompt}` requires the geometry service to have `GEMINI_API_KEY`; `{program}`
does not (the solver is LLM-free), which is the path used in tests.

## Export strategies (`Export:Mode`)
- **AddInHandoff** (default): writes `layout.json` to `HandoffDir` for the desktop
  Revit add-in to build; status `Pending` until the sibling `.rvt` appears, then
  `Ready`.
- **DesignAutomation**: submits an APS Design Automation workitem (needs
  `APS:ClientId`/`APS:ClientSecret`/`APS:ActivityId`); see `revit/DesignAutomation`.

## Run
```bash
# geometry service must be reachable (default http://localhost:8000)
dotnet run --project api            # http://localhost:5080
```
Config in `appsettings.json` (or env, e.g. `GeometryService__BaseUrl`,
`Export__Mode`).

## Notes
- The exported `layout.json` is stripped of the UI-only `svg`/`coverage` fields so
  it validates against `/schemas/layout.schema.json` for the Revit builder.
- Transitive `SQLitePCLRaw.lib.e_sqlite3` currently trips advisory
  GHSA-2m69-gcr7-jv3q; bump when a fully patched bundle ships.
