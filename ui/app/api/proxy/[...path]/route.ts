/**
 * The browser's only route to the API.
 *
 * Everything the page needs goes through here so that both credentials stay on
 * the server. The proxy is deliberately dumb: it forwards a path and a body and
 * returns what it gets. It is not a place to add behaviour, because behaviour
 * added here is behaviour the API's own tests do not cover.
 */

import { SessionExpired, apiFetch } from "@/lib/api";
// The allow-list is data, and lives where a test and the build check can
// both import it rather than scrape it.
import { CALLER_CREDENTIAL, NO_CREDENTIAL, allowed } from "@/lib/proxy-allow";


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
  // Raw bytes for an upload, text for everything else. `text()` on a PDF
  // corrupts it -- the body has to stay binary all the way to the API, which is
  // the entire point of uploading rather than inlining base64.
  const isUpload = /^api\/v1\/uploads\/[A-Za-z0-9_]+\/bytes$/.test(joined);
  const body =
    method === "POST" || method === "PUT" || method === "PATCH"
      ? isUpload
        ? await request.arrayBuffer()
        : await request.text()
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

  // The public demo travels with no console credential. `apiFetch` falls back
  // to the service key when nobody is signed in, which would silently promote
  // an anonymous visitor's request to whatever that key can do. The endpoint
  // needs none: it decides what it answers from configuration, not from who is
  // asking.
  const anonymous = NO_CREDENTIAL.some((pattern) => pattern.test(joined));

  let upstream: Response;
  try {
    upstream = await apiFetch(`/${joined}`, {
      method, body, skipAppCredential: anonymous,
      // The upload token *is* the capability for `PUT .../bytes`, exactly as a
      // signed URL's signature is. `apiFetch` builds its own headers, so
      // without forwarding this the API sees no token and answers 403 -- which
      // reads as a permissions bug and is a proxy that dropped a header.
      ...(isUpload
        ? {
            headers: {
              "X-Upload-Token": request.headers.get("x-upload-token") ?? "",
              "content-type":
                request.headers.get("content-type") ?? "application/octet-stream",
            },
          }
        : {}),
      // `skipAppCredential` returns early in `apiFetch`, before the branch that
      // adds a content-type -- so without this the API receives a JSON body
      // with no type and FastAPI parses it as a string, answering
      // "Input should be a valid dictionary" to a perfectly good request.
      ...(anonymous && body ? { headers: { "content-type": "application/json" } } : {}),
    });
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
