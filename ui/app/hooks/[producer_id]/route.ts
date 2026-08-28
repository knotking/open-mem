/**
 * The public front door for inbound webhooks.
 *
 * The API sits behind Cloud Run IAM, which a provider cannot satisfy — it has
 * no Google credentials and no way to obtain one. This route is the only thing
 * that opens: it adds the platform's identity token and forwards everything
 * else through untouched.
 *
 * Two rules make it safe to expose:
 *
 * **The body is forwarded byte-for-byte.** Signatures are computed over the
 * raw bytes; parsing and re-serialising here would break every signed
 * integration in a way that looks like a provider bug.
 *
 * **No platform credential is attached.** Unlike the console proxy, this route
 * never sends an API key. The producer's own `inbound_auth` is the entire
 * authentication story, so a caller who cannot satisfy it gets nothing.
 */

import { apiFetch } from "@/lib/api";

// Headers a provider uses to prove itself or identify a delivery. Everything
// else is dropped rather than forwarded, so this cannot be used to smuggle
// platform headers through to the API.
const FORWARDED = new Set([
  "content-type",
  "x-signature",
  "x-signature-timestamp",
  "x-hub-signature-256",
  "x-slack-signature",
  "x-slack-request-timestamp",
  "stripe-signature",
  "x-api-key",
  "x-delivery-id",
  "x-github-delivery",
  "x-request-id",
  "idempotency-key",
  "user-agent",
]);

export async function POST(
  request: Request,
  { params }: { params: Promise<{ producer_id: string }> },
) {
  const { producer_id } = await params;
  if (!/^[A-Za-z0-9_]+$/.test(producer_id)) {
    return Response.json({ detail: "not found" }, { status: 404 });
  }

  const raw = await request.arrayBuffer();
  const headers = new Headers();
  request.headers.forEach((value, key) => {
    if (FORWARDED.has(key.toLowerCase())) headers.set(key, value);
  });

  const upstream = await apiFetch(`/webhooks/${producer_id}`, {
    method: "POST",
    headers,
    body: raw,
    // apiFetch would otherwise attach the console's own credential; a webhook
    // must stand on the producer's inbound auth alone.
    skipAppCredential: true,
  });

  const text = await upstream.text();
  return new Response(text, {
    status: upstream.status,
    headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
  });
}
