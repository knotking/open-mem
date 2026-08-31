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
  /^api\/v1\/projects\/[\w-]+\/entities(\?.*)?$/,
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
  // The scope and the key are both variables at the call site -- the settings
  // editor picks the scope per row -- so the pattern cannot spell the four
  // scopes out. The API is the validator that matters: `put` refuses an unknown
  // scope, an unknown key, a scope the setting is not allowed at, and a write
  // below a lock. This is a route, not a second policy.
  /^api\/v1\/settings\/[A-Za-z0-9_]+\/[A-Za-z0-9_]+$/,
  /^api\/v1\/models\/assignments$/,
  /^api\/v1\/reprocess$/,
  // Expiry: the sweep, and the forecast of what it would take.
  /^api\/v1\/expiry\/sweep$/,
  /^api\/v1\/projects\/[A-Za-z0-9_]+\/expiring(\?.*)?$/,
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
  // The hierarchy: `part_of` in both directions, and the link that builds it.
  /^api\/v1\/memories\/[A-Za-z0-9_]+\/tree(\?.*)?$/,
  /^api\/v1\/memories\/[A-Za-z0-9_]+\/links(\?.*)?$/,
  /^api\/v1\/data\/[A-Za-z0-9_]+\/versions$/,
  // One revision, read whole. The listing above only previews.
  /^api\/v1\/data\/[A-Za-z0-9_]+\/versions\/[A-Za-z0-9_]+$/,

  // Alerts. Absent until now, which meant every call the Alerts screen made was
  // refused here -- the screen rendered and never loaded anything, and that
  // looked like a design problem rather than a routing one.
  /^api\/v1\/alerts$/,
  /^api\/v1\/alerts\/surfaces$/,
  /^api\/v1\/alerts\/[A-Za-z0-9_]+$/,
  /^api\/v1\/alerts\/[A-Za-z0-9_]+\/(backtest|enabled|runs)(\?.*)?$/,
  /^api\/v1\/projects\/[A-Za-z0-9_]+\/alerts$/,
  /^api\/v1\/alert-events(\?.*)?$/,
  // Standing queries: the same shape as alerts, matching on the record rather
  // than on a transition.
  /^api\/v1\/standing-queries$/,
  /^api\/v1\/standing-queries\/[A-Za-z0-9_]+$/,
  /^api\/v1\/standing-queries\/[A-Za-z0-9_]+\/(backtest|enabled|matches)(\?.*)?$/,
  /^api\/v1\/projects\/[A-Za-z0-9_]+\/standing-queries$/,
  /^api\/v1\/event-subscriptions$/,
  /^api\/v1\/event-subscriptions\/[A-Za-z0-9_]+$/,
  /^api\/v1\/event-subscriptions\/[A-Za-z0-9_]+\/(rotate|replay|deliveries)(\?.*)?$/,
  /^api\/v1\/projects\/[A-Za-z0-9_]+\/event-subscriptions$/,

  // Compaction.
  /^api\/v1\/compaction\/algorithms$/,
  /^api\/v1\/compaction\/jobs$/,
  /^api\/v1\/compaction\/jobs\/[A-Za-z0-9_]+$/,
  /^api\/v1\/compaction\/jobs\/[A-Za-z0-9_]+\/(preview|run|enabled|runs)(\?.*)?$/,
  /^api\/v1\/projects\/[A-Za-z0-9_]+\/compaction\/jobs$/,
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
  // Enable and disable a producer, revoke a key, revoke an invite, and the
  // per-scope freshness the Producers screen reads.
  /^api\/v1\/producers\/[A-Za-z0-9_]+$/,
  /^api\/v1\/users\/me\/api-keys\/[A-Za-z0-9_]+$/,
  /^api\/v1\/invites$/,
  // Groups: a sharing principal the ACL predicate already resolves, with no way
  // to create one until now.
  /^api\/v1\/groups$/,
  /^api\/v1\/groups\/[A-Za-z0-9_]+\/members$/,
  // The certificate a deletion is answered for with.
  /^api\/v1\/data\/[A-Za-z0-9_]+\/erasure$/,
  /^api\/v1\/invites\/[A-Za-z0-9_]+$/,
  /^api\/v1\/projects\/[A-Za-z0-9_]+\/source-lag$/,
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

// Reachable by GET, with no credential of any kind.
//
// `api/v1/mcp` is one path serving two things. GET is a manifest — the
// transport and the tool names, disclosing nothing about anyone's data, which
// is why the API leaves it unauthenticated too.
const GET_ONLY = [
  /^api\/v1\/mcp$/,
];

// Paths that must carry the *caller's* credential and never the console's.
//
// POST on `api/v1/mcp` is a tool call. Everywhere else this proxy replaces the
// caller's headers with the console's own, falling back to the service key when
// nobody is signed in — which is right for a browser panel and catastrophic
// here: an MCP client has no browser session, so a plain allow-list entry would
// publish an unauthenticated MCP server over whatever that key can reach.
//
// So these forward the key the client sent and nothing else. A request without
// one is refused rather than downgraded, because the downgrade is exactly the
// hole: it would answer, with the platform's own access, and look like it
// worked.
const CALLER_CREDENTIAL = [
  /^api\/v1\/mcp$/,
];

function allowed(path: string, method: string): boolean {
  if (method === "GET" && GET_ONLY.some((pattern) => pattern.test(path))) return true;
  if (method === "POST" && CALLER_CREDENTIAL.some((pattern) => pattern.test(path))) {
    return true;
  }
  return ALLOWED.some((pattern) => pattern.test(path));
}

/** The API key an MCP client sent, from either header the API itself accepts. */
function callerKey(request: Request): string | null {
  const bearer = request.headers.get("authorization") ?? "";
  const match = /^Bearer\s+(.+)$/i.exec(bearer.trim());
  if (match) return match[1].trim() || null;
  const direct = request.headers.get("x-api-key");
  return direct?.trim() || null;
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

  // A tool call stands on the caller's own key, exactly as the webhook route
  // stands on the producer's inbound auth. `skipAppCredential` is the existing
  // seam for that and is why this needs no new mechanism.
  const standalone =
    method === "POST" && CALLER_CREDENTIAL.some((pattern) => pattern.test(joined));
  if (standalone) {
    const key = callerKey(request);
    if (!key) {
      return Response.json(
        { detail: "this endpoint needs your own API key, sent as Authorization: Bearer <key>" },
        { status: 401 },
      );
    }
    try {
      const upstreamStandalone = await apiFetch(`/${joined}`, {
        method,
        body,
        skipAppCredential: true,
        headers: { "X-API-Key": key, "content-type": "application/json" },
      });
      const text = await upstreamStandalone.text();
      return new Response(text, {
        status: upstreamStandalone.status,
        headers: {
          "content-type":
            upstreamStandalone.headers.get("content-type") ?? "application/json",
        },
      });
    } catch {
      return Response.json({ detail: "upstream unavailable" }, { status: 502 });
    }
  }

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
