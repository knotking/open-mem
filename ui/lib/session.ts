/**
 * Sessions.
 *
 * The refresh token lives in an httpOnly cookie and the browser never sees an
 * ID token. That is the point: an ID token in JavaScript is an ID token in
 * every script the page loads.
 *
 * A signed-in request is proxied with *that user's* identity, not the service's
 * shared API key. This is what makes exposing the UI safe at all -- without it,
 * anyone who reaches the page inherits whatever the server key can do.
 */

import { cookies } from "next/headers";

const REFRESH_COOKIE = "open_mem_rt";
const IDENTITY = "https://identitytoolkit.googleapis.com/v1";
const SECURE_TOKEN = "https://securetoken.googleapis.com/v1";

export const webApiKey = process.env.FIREBASE_WEB_API_KEY ?? "";
export const authEnabled = webApiKey !== "";

type SignInResult =
  | { ok: true }
  | { ok: false; error: string };

export async function signIn(email: string, password: string): Promise<SignInResult> {
  const response = await fetch(`${IDENTITY}/accounts:signInWithPassword?key=${webApiKey}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email, password, returnSecureToken: true }),
    cache: "no-store",
  });
  const payload = await response.json();
  if (!response.ok) {
    // Deliberately not distinguishing "no such user" from "wrong password":
    // the difference is an account-enumeration oracle.
    return { ok: false, error: "That email and password do not match an account." };
  }

  const store = await cookies();
  store.set(REFRESH_COOKIE, payload.refreshToken, {
    httpOnly: true,
    secure: true,
    sameSite: "lax",
    path: "/",
    maxAge: 60 * 60 * 24 * 14,
  });
  return { ok: true };
}

export async function signOut(): Promise<void> {
  const store = await cookies();
  store.delete(REFRESH_COOKIE);
}

/** Mint a fresh ID token from the stored refresh token. */
export async function idToken(): Promise<string | null> {
  if (!authEnabled) return null;
  const store = await cookies();
  const refresh = store.get(REFRESH_COOKIE)?.value;
  if (!refresh) return null;

  const response = await fetch(`${SECURE_TOKEN}/token?key=${webApiKey}`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ grant_type: "refresh_token", refresh_token: refresh }),
    cache: "no-store",
  });
  if (!response.ok) return null;
  return (await response.json()).id_token ?? null;
}

export async function isSignedIn(): Promise<boolean> {
  const store = await cookies();
  return store.get(REFRESH_COOKIE) !== undefined;
}
