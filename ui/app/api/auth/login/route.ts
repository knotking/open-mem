import { authEnabled, signIn } from "@/lib/session";

export async function POST(request: Request) {
  if (!authEnabled) {
    return Response.json({ error: "sign-in is not configured" }, { status: 501 });
  }
  const { email, password } = await request.json();
  if (!email || !password) {
    return Response.json({ error: "Email and password are required." }, { status: 400 });
  }
  const result = await signIn(email, password);
  if (!result.ok) return Response.json({ error: result.error }, { status: 401 });
  return Response.json({ ok: true });
}
