/**
 * What this build can do, for the sign-in page.
 *
 * The console proxy requires a session, so it cannot serve a page nobody has
 * signed into yet. This route carries only the platform identity token — the
 * one Cloud Run IAM wants — and never an API key, so it cannot be used to
 * reach anything that answers with tenant data.
 *
 * It is safe to expose because the upstream endpoint counts registries: file
 * formats, data types, prompts, providers. None of it says who uses the system
 * or what they stored.
 */

import { apiFetch } from "@/lib/api";

export async function GET() {
  try {
    const upstream = await apiFetch("/api/v1/capabilities", {
      skipAppCredential: true,
    });
    if (!upstream.ok) {
      // The page renders without numbers rather than showing an error: a
      // marketing figure that failed to load is not worth a broken sign-in.
      return Response.json({}, { status: 200 });
    }
    return Response.json(await upstream.json(), {
      status: 200,
      headers: { "cache-control": "public, max-age=300" },
    });
  } catch {
    return Response.json({}, { status: 200 });
  }
}
