"use client";

import { useState } from "react";
import { createProject, exportVariant, rvtUrl, ProjectDto, VariantDto } from "@/lib/api";
import { EXAMPLE_PROGRAM } from "@/lib/example";

export default function Home() {
  const [prompt, setPrompt] = useState(
    "A single-storey family home on a 16x12 m plot: double garage, open living/dining/kitchen, a master suite, two kids' bedrooms, a home office and an entry."
  );
  const [useExample, setUseExample] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [project, setProject] = useState<ProjectDto | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [busyExport, setBusyExport] = useState<string | null>(null);
  const [exports, setExports] = useState<Record<string, { status: string; message: string }>>({});

  async function onGenerate() {
    setLoading(true);
    setError(null);
    setProject(null);
    setSelected(null);
    setExports({});
    try {
      const p = await createProject(
        useExample ? { program: EXAMPLE_PROGRAM, n: 3 } : { prompt, n: 3 }
      );
      setProject(p);
      setSelected(p.variants[0]?.id ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  async function onExport(v: VariantDto) {
    setBusyExport(v.id);
    try {
      const r = await exportVariant(v.id);
      setExports((prev) => ({ ...prev, [v.id]: { status: r.status, message: r.message } }));
    } catch (e) {
      setExports((prev) => ({
        ...prev,
        [v.id]: { status: "Failed", message: e instanceof Error ? e.message : String(e) },
      }));
    } finally {
      setBusyExport(null);
    }
  }

  return (
    <main>
      <h1>bum-engine</h1>
      <p className="sub">
        Natural-language brief → CP-SAT solver → validated floor-plan variants → native Revit
        <code> .rvt</code>.
      </p>

      <div className="panel">
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          disabled={useExample}
          placeholder="Describe the house…"
        />
        <div className="row">
          <button className="primary" onClick={onGenerate} disabled={loading}>
            {loading ? "Generating…" : "Generate variants"}
          </button>
          <label className="check">
            <input
              type="checkbox"
              checked={useExample}
              onChange={(e) => setUseExample(e.target.checked)}
            />
            Demo mode (example program, no LLM key needed)
          </label>
          <span className="spacer" />
        </div>
        {error && <div className="err">⚠ {error}</div>}
      </div>

      {project && (
        <>
          <div className="row" style={{ marginBottom: 12 }}>
            <strong>{project.variants.length} passing variant(s)</strong>
            <span className="tag" style={{ color: "var(--muted)" }}>
              — pick one to export
            </span>
          </div>
          <div className="grid">
            {project.variants.map((v) => {
              const ex = exports[v.id];
              return (
                <div
                  key={v.id}
                  className={`card${selected === v.id ? " selected" : ""}`}
                  onClick={() => setSelected(v.id)}
                >
                  <div className="svgwrap" dangerouslySetInnerHTML={{ __html: v.svg }} />
                  <div className="meta">
                    <div>
                      <b>{v.preset}</b> <span className="tag">· seed {v.seed}</span>
                    </div>
                    <div className="tag">
                      objective <b>{v.objective.toFixed(1)}</b> · coverage{" "}
                      <b>{(v.coverage * 100).toFixed(0)}%</b>
                    </div>
                    {selected === v.id && (
                      <div className="row">
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            onExport(v);
                          }}
                          disabled={busyExport === v.id}
                        >
                          {busyExport === v.id ? "Exporting…" : "Export to Revit"}
                        </button>
                        {v.rvtAvailable || ex?.status === "Ready" ? (
                          <a href={rvtUrl(v.id)} onClick={(e) => e.stopPropagation()} download>
                            <button>Download .rvt</button>
                          </a>
                        ) : (
                          <button disabled title="Build the .rvt first (needs a Revit host)">
                            Download .rvt
                          </button>
                        )}
                      </div>
                    )}
                    {ex && (
                      <div className={`status${ex.status === "Ready" ? " ok" : ""}`}>
                        {ex.status}: {ex.message}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}
    </main>
  );
}
