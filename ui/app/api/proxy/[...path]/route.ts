/**
 * The browser's only route to the API.
 *
 * Everything the page needs goes through here so that both credentials stay on
 * the server. The proxy is deliberately dumb: it forwards a path and a body and
 * returns what it gets. It is not a place to add behaviour, because behaviour
 * added here is behaviour the API's own tests do not cover.
 */

import { SessionExpired, apiFetch } from "@/lib/api";

const ALLOWED = [
  /^api\/v1\/write$/,
  /^api\/v1\/retrieve$/,
  /^api\/v1\/ask$/,
  /^api\/v1\/crawlers$/,
  /^api\/v1\/crawlers\/[\w-]+$/,
  /^api\/v1\/crawlers\/[\w-]+\/(dry-run|run|runs)$/,
  /^api\/v1\/crawl-runs\/[\w-]+$/,
  /^api\/v1\/crawl-tick$/,
  /^api\/v1\/prompts$/,
  /^api\/v1\/projects\/[\w-]+\/entities$/,
  /^api\/v1\/entities\/[\w-]+$/,
  /^api\/v1\/entities\/[\w-]+\/graph(\?.*)?$/,
  /^api\/v1\/entities\/[\w-]+\/co-mentions(\?.*)?$/,
  /^api\/v1\/graph\/predicates$/,
  /^api\/v1\/entities\/merge$/,
  /^api\/v1\/entities\/merges\/[\w-]+\/undo$/,
  /^api\/v1\/projects\/[\w-]+\/crawlers$/,
  /^api\/v1\/health$/,
  /^api\/v1\/data\/[A-Za-z0-9_]+(\?.*)?$/,
  /^api\/v1\/data\/[A-Za-z0-9_]+\/artifacts$/,
  /^api\/v1\/data\/[A-Za-z0-9_]+\/content$/,
  /^api\/v1\/settings\/(platform|org|project|user)\/[a-z_]+$/,
  /^api\/v1\/models\/assignments$/,
  /^api\/v1\/reprocess$/,
  /^api\/v1\/artifacts\/stale(\?.*)?$/,
  /^api\/v1\/projects\/[A-Za-z0-9_]+\/staircase$/,
  /^api\/v1\/projects\/[A-Za-z0-9_]+\/overview$/,
  /^api\/v1\/projects\/[A-Za-z0-9_]+\/data(\?.*)?$/,
  /^api\/v1\/projects\/[A-Za-z0-9_]+\/memory-types$/,
  /^api\/v1\/memories$/,
  /^api\/v1\/memories\/[A-Za-z0-9_]+(\?.*)?$/,
  /^api\/v1\/memories\/[A-Za-z0-9_]+\/members\/[A-Za-z0-9_]+$/,
  /^api\/v1\/projects\/[A-Za-z0-9_]+\/memories$/,
  /^api\/v1\/memories\/[A-Za-z0-9_]+\/members$/,
  /^api\/v1\/data\/[A-Za-z0-9_]+\/versions$/,
  /^api\/v1\/data\/[A-Za-z0-9_]+\/memories$/,
  /^api\/v1\/audit(\?.*)?$/,
  /^api\/v1\/events(\?.*)?$/,
  /^api\/v1\/data\/[A-Za-z0-9_]+\/enrich$/,
  /^api\/v1\/projects(\?.*)?$/,
  /^api\/v1\/projects\/[A-Za-z0-9_]+\/cases$/,
  /^api\/v1\/cases\/[A-Za-z0-9_]+\/timeline$/,
  /^api\/v1\/shares$/,
  /^api\/v1\/shares\/[A-Za-z0-9_]+$/,
  /^api\/v1\/deletions$/,
  /^api\/v1\/users\/me\/deletion$/,
  /^api\/v1\/data\/[A-Za-z0-9_]+\/erasure$/,
  /^api\/v1\/settings\/effective(\?.*)?$/,
  /^api\/v1\/models$/,
  /^api\/v1\/agents\/[A-Za-z0-9_]+\/config(\?.*)?$/,
  /^api\/v1\/organizations\/members$/,
  /^api\/v1\/users\/me$/,
  /^api\/v1\/users\/me\/api-keys$/,
  /^api\/v1\/producers$/,
  /^api\/v1\/producers\/[A-Za-z0-9_]+\/inbound$/,
  /^api\/v1\/producers\/[A-Za-z0-9_]+\/signing-secret$/,
  /^api\/v1\/producers\/[A-Za-z0-9_]+\/test-delivery$/,
  /^api\/v1\/producers\/[A-Za-z0-9_]+\/deliveries(\?.*)?$/,
  /^api\/v1\/platform\/health$/,
  // The connector catalog and the credentials it is created against. Missing
  // these is why the "Pull from an app" panel rendered an empty category list:
  // the endpoints existed, the panel existed, and the browser's only route to
  // the API refused the request — so `apps` stayed empty and the failure looked
  // like a catalog with nothing in it.
  /^api\/v1\/connectors$/,
  /^api\/v1\/connections(\?.*)?$/,
  /^api\/v1\/crawlers\/[\w-]+\/connection$/,
];

// Reachable by GET and by nothing else.
//
// `api/v1/mcp` is one path serving two things. GET is a manifest — the
// transport and the tool names, disclosing nothing about anyone's data, which
// is why the API leaves it unauthenticated. POST on the same path is a tool
// call.
//
// This proxy does not forward the caller's headers: it replaces them with the
// console's own credential, falling back to the service key when nobody is
// signed in. An MCP client carries no browser session, so putting POST in the
// list above would publish an unauthenticated MCP server over whatever that
// key can reach — every tool, to anyone who can resolve this origin.
const GET_ONLY = [
  /^api\/v1\/mcp$/,
];

function allowed(path: string, method: string): boolean {
  if (method === "GET" && GET_ONLY.some((pattern) => pattern.test(path))) return true;
  return ALLOWED.some((pattern) => pattern.test(path));
}

// The allow-list is matched against path *plus* query string, so any endpoint
// whose behaviour is selected by a query parameter -- ?preview=true being the
// one that bit us -- needs `(\?.*)?` in its pattern or the request is refused
// with no clue as to why.

async function forward(request: Request, path: string[], method: string) {
  const joined = path.join("/") + (new URL(request.url).search || "");
  if (!allowed(joined, method)) {
    // An open proxy in front of an authenticated API hands the browser every
    // endpoint the server can reach, including ones this UI never uses.
    return Response.json({ detail: "path not permitted" }, { status: 403 });
  }
  const body =
    method === "POST" || method === "PUT" || method === "PATCH"
      ? await request.text()
      : undefined;
  let upstream: Response;
  try {
    upstream = await apiFetch(`/${joined}`, { method, body });
  } catch (error) {
    if (error instanceof SessionExpired) {
      // 401 rather than a silent downgrade, so the client signs in again.
      return Response.json({ detail: "session expired, sign in again" }, { status: 401 });
    }
    throw error;
  }
  const type = upstream.headers.get("content-type") ?? "application/json";
  // Stored bytes are not JSON. Reading them as text would corrupt every image,
  // audio file and video on the way through.
  if (!type.startsWith("application/json") && !type.startsWith("text/")) {
    return new Response(upstream.body, {
      status: upstream.status,
      headers: {
        "content-type": type,
        "content-disposition": "inline",
        "x-content-type-options": "nosniff",
      },
    });
  }
  const text = await upstream.text();
  return new Response(text, { status: upstream.status, headers: { "content-type": type } });
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

export async function PUT(
  request: Request,
  { params }: { params: Promise<{ path: string[] }> },
) {
  return forward(request, (await params).path, "PUT");
}

export async function PATCH(
  request: Request,
  { params }: { params: Promise<{ path: string[] }> },
) {
  return forward(request, (await params).path, "PATCH");
}

export async function DELETE(
  request: Request,
  { params }: { params: Promise<{ path: string[] }> },
) {
  return forward(request, (await params).path, "DELETE");
}
