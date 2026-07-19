// Always same-origin: the Next server rewrites /api/* to the orchestrator
// (see next.config.mjs). Avoids baking an API URL into the client bundle.
export const API_BASE = "";

export interface VariantDto {
  id: string;
  index: number;
  preset: string;
  seed: number;
  objective: number;
  coverage: number;
  svg: string;
  exportStatus: string;
  rvtAvailable: boolean;
  exportMessage: string | null;
}

export interface ProjectDto {
  id: string;
  prompt: string | null;
  createdAt: string;
  variants: VariantDto[];
  warnings?: string[];
}

async function json<T>(res: Response): Promise<T> {
  const text = await res.text();
  const body = text ? JSON.parse(text) : {};
  if (!res.ok) throw new Error((body as any)?.error ?? `HTTP ${res.status}`);
  return body as T;
}

// The demo-mode program, single-sourced from services/geometry/data/program.example.json
// (served by the geometry service's GET /example, proxied through the orchestrator) —
// not a hand-maintained copy.
export async function getExampleProgram(): Promise<unknown> {
  const res = await fetch(`${API_BASE}/api/example-program`);
  return json(res);
}

export async function createProject(input: {
  prompt?: string;
  program?: unknown;
  n?: number;
}): Promise<ProjectDto> {
  const res = await fetch(`${API_BASE}/api/projects`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ n: 3, ...input }),
  });
  return json<ProjectDto>(res);
}

export async function exportVariant(
  id: string
): Promise<{ id: string; mode: string; status: string; message: string }> {
  const res = await fetch(`${API_BASE}/api/variants/${id}/export`, { method: "POST" });
  return json(res);
}

export function rvtUrl(id: string): string {
  return `${API_BASE}/api/variants/${id}/rvt`;
}
