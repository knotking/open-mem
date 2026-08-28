/**
 * Server-side API access.
 *
 * Two headers, and the browser sees neither:
 *
 * - `Authorization` carries a Google identity token for Cloud Run IAM, because
 *   the API sits behind it.
 * - `X-API-Key` carries the mem-dog credential. When a person is signed in this
 *   is *their* identity token, so the API attributes the action to them; only
 *   an unauthenticated deployment falls back to the service key.
 *
 * The fallback is deliberately narrow. A shared key that acts for whoever
 * happens to load the page is the thing sign-in exists to remove.
 */

import { idToken, isSignedIn } from "./session";

export class SessionExpired extends Error {
  constructor() {
    super("session expired");
  }
}

const API_URL = process.env.MEMDOG_API_URL ?? "";
const SERVICE_KEY = process.env.MEMDOG_API_KEY ?? "";

let cachedRunToken: { value: string; expires: number } | null = null;

async function cloudRunToken(): Promise<string | null> {
  if (!API_URL) return null;
  const now = Date.now();
  if (cachedRunToken && cachedRunToken.expires > now + 60_000) return cachedRunToken.value;
  try {
    const response = await fetch(
      "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity" +
        `?audience=${encodeURIComponent(API_URL)}`,
      { headers: { "Metadata-Flavor": "Google" }, cache: "no-store" },
    );
    if (!response.ok) return null;
    const value = await response.text();
    cachedRunToken = { value, expires: now + 45 * 60_000 };
    return value;
  } catch {
    return null;   // not on GCP: local development
  }
}

type ApiInit = RequestInit & { skipAppCredential?: boolean };

export async function apiFetch(path: string, init: ApiInit = {}): Promise<Response> {
  const { skipAppCredential, ...rest } = init;
  const headers = new Headers(rest.headers);

  // The webhook route opts out entirely: a provider's delivery must stand on
  // the producer's own inbound auth, never on the console's credential.
  if (skipAppCredential) {
    const platform = await cloudRunToken();
    if (platform) headers.set("Authorization", `Bearer ${platform}`);
    return fetch(`${API_URL}${path}`, { ...rest, headers, cache: "no-store" });
  }

  // Fail closed. If someone is signed in, their identity is the only
  // credential this request may carry: falling back to the service key when
  // their token cannot be minted would silently promote every signed-in user
  // to whatever the shared key can do -- which is precisely what sign-in
  // exists to prevent. A transient refresh failure must look like a failure.
  if (await isSignedIn()) {
    const user = await idToken();
    if (!user) throw new SessionExpired();
    headers.set("X-API-Key", user);
  } else if (SERVICE_KEY) {
    // Only reachable when sign-in is not configured at all.
    headers.set("X-API-Key", SERVICE_KEY);
  }

  const platform = await cloudRunToken();
  if (platform) headers.set("Authorization", `Bearer ${platform}`);
  if (rest.body && !headers.has("content-type")) headers.set("content-type", "application/json");

  return fetch(`${API_URL}${path}`, { ...rest, headers, cache: "no-store" });
}

export const config = {
  projectId: process.env.MEMDOG_PROJECT_ID ?? "",
  producerId: process.env.MEMDOG_PRODUCER_ID ?? "",
  apiUrl: API_URL,
};
