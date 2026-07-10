// Runtime proxy: forwards /api/* to the orchestrator. A Route Handler runs
// per-request, so process.env.ORCHESTRATOR_URL is read live (unlike
// next.config rewrites, which bake their target at build time).
import { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function proxy(req: NextRequest, path: string[]) {
  const target = process.env.ORCHESTRATOR_URL || "http://localhost:5080";
  const search = req.nextUrl.search;
  const url = `${target}/api/${path.join("/")}${search}`;

  const headers = new Headers(req.headers);
  headers.delete("host");
  headers.delete("connection");

  const init: RequestInit = { method: req.method, headers };
  if (req.method !== "GET" && req.method !== "HEAD") {
    init.body = await req.arrayBuffer();
  }

  let res: Response;
  try {
    res = await fetch(url, init);
  } catch (e) {
    return Response.json(
      { error: `orchestrator unreachable at ${target}: ${e instanceof Error ? e.message : e}` },
      { status: 502 }
    );
  }

  // stream body back, preserving content-type + download headers (for .rvt)
  const out = new Headers();
  for (const h of ["content-type", "content-disposition", "content-length"]) {
    const v = res.headers.get(h);
    if (v) out.set(h, v);
  }
  return new Response(res.body, { status: res.status, headers: out });
}

type Ctx = { params: Promise<{ path: string[] }> };

export async function GET(req: NextRequest, ctx: Ctx) {
  return proxy(req, (await ctx.params).path);
}
export async function POST(req: NextRequest, ctx: Ctx) {
  return proxy(req, (await ctx.params).path);
}
export async function PUT(req: NextRequest, ctx: Ctx) {
  return proxy(req, (await ctx.params).path);
}
export async function DELETE(req: NextRequest, ctx: Ctx) {
  return proxy(req, (await ctx.params).path);
}
