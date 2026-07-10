# Docker / deployment

Three app services (`geometry`, `api`, `web`) plus an `nginx` front door in prod.

## Dev — services exposed directly
```bash
docker compose -f docker/docker-compose.yml up --build
# geometry  http://localhost:8000
# api       http://localhost:5080
# web       http://localhost:3000
```
Set `GEMINI_API_KEY` in the environment to enable `/extract` (prompt → program).

## Prod — nginx on :80, internals private
```bash
export APS_CLIENT_ID=... APS_CLIENT_SECRET=... APS_ACTIVITY_ID=...
docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml up -d --build
# everything behind http://localhost/  (web on /, api on /api)
```
Prod defaults `Export:Mode=DesignAutomation` so `.rvt` builds run headless via APS
(dev defaults to `AddInHandoff`).

## Images
| service  | base | notes |
|----------|------|-------|
| geometry | `python:3.12-slim` | FastAPI + ortools; build context = repo root (needs `/schemas`) |
| api      | `dotnet/aspnet:10.0` | SQLite + handoff/output under the `/data` volume |
| web      | `node:22-alpine` | Next.js standalone output |

## The Revit builder is not containerised
The Revit API runs only inside Revit. Locally it's the desktop add-in; in the
cloud it's an APS Design Automation activity (see `revit/DesignAutomation`). The
`api` hands layouts to whichever is configured.

## CI
`.github/workflows/ci.yml` runs on push/PR: geometry `pytest`, `api` build,
`RevitBuilder`+`AddIn` build (Windows), and `web` build. Design Automation is
excluded (needs the Revit DA SDK's `DesignAutomationBridge.dll`).
