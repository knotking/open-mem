import Console from "@/components/Console";
import { config } from "@/lib/api";

export const dynamic = "force-dynamic";

export default function Page() {
  const configured = config.projectId !== "" && config.producerId !== "";
  if (!configured) {
    return (
      <main className="content">
        <h1>Not configured</h1>
        <p className="empty">
          Set <code>MEMDOG_API_URL</code>, <code>MEMDOG_API_KEY</code>,{" "}
          <code>MEMDOG_PROJECT_ID</code> and <code>MEMDOG_PRODUCER_ID</code> on the service.
        </p>
      </main>
    );
  }
  return <Console projectId={config.projectId} producerId={config.producerId} />;
}
