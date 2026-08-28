/**
 * The browser's only route to the API.
 *
 * Everything the page needs goes through here so that both credentials stay on
 * the server. The proxy is deliberately dumb: it forwards a path and a body and
 * returns what it gets. It is not a place to add behaviour, because behaviour
 * added here is behaviour the API's own tests do not cover.
 */

import { apiFetch } from "@/lib/api";

const ALLOWED = [
  /^api\/v1\/write$/,
  /^api\/v1\/retrieve$/,
  /^api\/v1\/health$/,
  /^api\/v1\/data\/[A-Za-z0-9_]+$/,
  /^api\/v1\/data\/[A-Za-z0-9_]+\/artifacts$/,
  /^api\/v1\/projects\/[A-Za-z0-9_]+\/staircase$/,
  /^api\/v1\/projects\/[A-Za-z0-9_]+\/memories$/,
  /^api\/v1\/memories\/[A-Za-z0-9_]+\/members$/,
  /^api\/v1\/data\/[A-Za-z0-9_]+\/versions$/,
  /^api\/v1\/data\/[A-Za-z0-9_]+\/memories$/,
  /^api\/v1\/audit(\?.*)?$/,
];

function allowed(path: string): boolean {
  return ALLOWED.some((pattern) => pattern.test(path));
}

async function forward(request: Request, path: string[], method: string) {
  const joined = path.join("/") + (new URL(request.url).search || "");
  if (!allowed(joined)) {
    // An open proxy in front of an authenticated API hands the browser every
    // endpoint the server can reach, including ones this UI never uses.
    return Response.json({ detail: "path not permitted" }, { status: 403 });
  }
  const body = method === "POST" ? await request.text() : undefined;
  const upstream = await apiFetch(`/${joined}`, { method, body });
  const text = await upstream.text();
  return new Response(text, {
    status: upstream.status,
    headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
  });
}

export async function GET(
  request: Request,
  { params }: { params: Promise<{ path: string[] }> },
) {
  return forward(request, (await params).path, "GET");
}

export async function POST(
  request: Request,
  { params }: { params: Promise<{ path: string[] }> },
) {
  return forward(request, (await params).path, "POST");
}
