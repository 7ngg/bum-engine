# Web (Next.js, App Router, TS)

The UI: a brief (or demo program) → a grid of validated floor-plan variants
(inline SVG) → pick one → export → download the `.rvt`.

```bash
npm install
NEXT_PUBLIC_API_BASE=http://localhost:5080 npm run dev   # http://localhost:3000
```

- **Demo mode** (on by default) posts the example `program.json` so the whole
  flow works with no Gemini key. Uncheck it to send the free-text prompt through
  the LLM extractor (requires the geometry service to have `GEMINI_API_KEY`).
- `NEXT_PUBLIC_API_BASE` points at the orchestrator. Leave empty in production so
  the browser calls the same origin and nginx proxies `/api`.
- Built with `output: "standalone"` for a small Docker runtime image (see
  `web/Dockerfile`).

## Flow
1. `POST /api/projects` `{program|prompt, n}` → variants (each with an inline SVG).
2. Select a card → `POST /api/variants/{id}/export` → shows the export status.
3. `GET /api/variants/{id}/rvt` downloads the model once a Revit host has built it
   (locally the desktop add-in; in the cloud a Design Automation workitem).
