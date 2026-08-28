import Sandbox from "@/components/Sandbox";
import { config } from "@/lib/api";

export const dynamic = "force-dynamic";

export default function Page() {
  const configured = config.projectId !== "" && config.producerId !== "";
  return (
    <main>
      <header className="page">
        <h1>mem-dog sandbox</h1>
        <p>
          Write an item, watch it climb the staircase, search it, and inspect what retrieval
          actually did. No chat — the trace is the useful part, and a conversational layer over a
          retrieval path nobody has inspected just hides the thing worth looking at.
        </p>
      </header>
      {configured ? (
        <Sandbox projectId={config.projectId} producerId={config.producerId} />
      ) : (
        <section className="panel">
          <h2>Not configured</h2>
          <p className="empty">
            Set <code>MEMDOG_API_URL</code>, <code>MEMDOG_API_KEY</code>,{" "}
            <code>MEMDOG_PROJECT_ID</code> and <code>MEMDOG_PRODUCER_ID</code> on the service.
          </p>
        </section>
      )}
    </main>
  );
}
