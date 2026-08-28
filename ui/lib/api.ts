/**
 * Server-side API access.
 *
 * Two credentials, two headers, and neither ever reaches the browser:
 *
 * - `Authorization` carries a Google identity token, because Cloud Run IAM
 *   guards the API and public access is refused by org policy.
 * - `X-API-Key` carries the mem-dog credential, which cannot share the
 *   Authorization header with the platform's own token.
 *
 * The API key living only on the server is not incidental. An API key in a
 * browser is a key you have published.
 */

const API_URL = process.env.MEMDOG_API_URL ?? "";
const API_KEY = process.env.MEMDOG_API_KEY ?? "";

let cachedToken: { value: string; expires: number } | null = null;

async function identityToken(): Promise<string | null> {
  if (!API_URL) return null;
  const now = Date.now();
  if (cachedToken && cachedToken.expires > now + 60_000) return cachedToken.value;

  try {
    const response = await fetch(
      "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity" +
        `?audience=${encodeURIComponent(API_URL)}`,
      { headers: { "Metadata-Flavor": "Google" }, cache: "no-store" },
    );
    if (!response.ok) return null;
    const value = await response.text();
    // Cloud Run identity tokens last an hour; re-mint well before that.
    cachedToken = { value, expires: now + 45 * 60_000 };
    return value;
  } catch {
    // Not on GCP -- local development against an unauthenticated API.
    return null;
  }
}

export async function apiFetch(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(init.headers);
  if (API_KEY) headers.set("X-API-Key", API_KEY);
  const token = await identityToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !headers.has("content-type")) {
    headers.set("content-type", "application/json");
  }
  return fetch(`${API_URL}${path}`, { ...init, headers, cache: "no-store" });
}

export const config = {
  projectId: process.env.MEMDOG_PROJECT_ID ?? "",
  producerId: process.env.MEMDOG_PRODUCER_ID ?? "",
  apiUrl: API_URL,
};
