import Console from "@/components/Console";
import Landing from "@/components/Landing";
import { apiFetch, config } from "@/lib/api";
import { authEnabled, isSignedIn } from "@/lib/session";

export const dynamic = "force-dynamic";

export default async function Page() {
  const signedIn = await isSignedIn();

  // With sign-in configured, an unauthenticated visitor gets the landing page
  // and nothing else -- no console shell, no data calls, nothing that would
  // act under the service credential on their behalf.
  if (authEnabled && !signedIn) {
    return <Landing authEnabled={authEnabled} />;
  }

  let me: { email?: string; role?: string } | null = null;
  try {
    const response = await apiFetch("/api/v1/users/me");
    if (response.ok) me = await response.json();
  } catch {
    // A stale session renders the landing page rather than a console acting
    // under someone else's credential.
    if (authEnabled) return <Landing authEnabled={authEnabled} />;
  }

  if (!config.projectId || !config.producerId) {
    return (
      <main className="content">
        <h1>Not configured</h1>
        <p className="empty">
          Set <code>OPENMEM_API_URL</code>, <code>OPENMEM_PROJECT_ID</code> and{" "}
          <code>OPENMEM_PRODUCER_ID</code> on the service.
        </p>
      </main>
    );
  }

  return (
    <Console
      projectId={config.projectId}
      producerId={config.producerId}
      me={me}
      authEnabled={authEnabled}
    />
  );
}
