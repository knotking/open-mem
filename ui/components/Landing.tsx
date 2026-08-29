"use client";

/**
 * The landing page.
 *
 * It says what the thing is before asking anyone to sign in, and every claim
 * on it is one the system can actually back. The numbers are counted from the
 * running build rather than written into copy, because a number a maintainer
 * typed is wrong within a month and wrong in the flattering direction.
 */

import { useEffect, useState } from "react";

import ThemeToggle from "./ThemeToggle";

type Capabilities = {
  formats: number;
  data_types: number;
  prompts: number;
  webhook_providers: number;
  crawler_strategies: number;
  embed_model: string;
  media_interpretation: boolean;
};

const STEPS = [
  {
    n: "01",
    title: "Write",
    body: "One endpoint for every producer — an upload, a webhook, a crawler, an SDK call. The write commits before it returns; only the enrichment is queued.",
  },
  {
    n: "02",
    title: "Store",
    body: "Bytes to object storage, text and metadata to Postgres, an ACL derived at write time and sealed before anything else runs.",
  },
  {
    n: "03",
    title: "Read",
    body: "Parsed by a handler chosen from the sniffed bytes, never from the caller's claim about them. Audio and video are transcribed; images are described.",
  },
  {
    n: "04",
    title: "Enrich",
    body: "Optional and off by default, because it is the expensive part. A prompt picked for the kind of thing it is — a table is not summarised as prose.",
  },
  {
    n: "05",
    title: "Retrieve",
    body: "Vector and lexical arms in one query with one plan, and the access rule inside the query rather than filtering what came back.",
  },
  {
    n: "06",
    title: "Answer",
    body: "A model reads only the passages retrieval returned and cites the one behind each claim. When they do not support an answer, it says so.",
  },
];

const PILLARS = [
  {
    title: "The staircase is visible",
    body: (
      <>
        <code>stored</code> → <code>searchable</code> → <code>enriched</code>. An item is durable
        the moment it is written and findable the moment it is embedded. &ldquo;I uploaded it and
        search cannot find it&rdquo; is a state, not a bug — and you can see which one.
      </>
    ),
  },
  {
    title: "The trace, not just the answer",
    body: (
      <>
        Ranked chunks with scores, which arm matched, spans into the source, and the records
        <em> considered and excluded</em> — with the reason. Missing something you know is there has
        several causes, and they need different fixes.
      </>
    ),
  },
  {
    title: "Provenance on every derived row",
    body: (
      <>
        Every embedding, summary and transcript records the model, the build that answered, and
        when. Nothing is mutated in place, so &ldquo;why does this say something different than last
        week?&rdquo; has an answer.
      </>
    ),
  },
  {
    title: "Access control inside the query",
    body: (
      <>
        The ACL is a predicate in the retrieval query, never a filter over results. Asking for ten
        and hiding three is a different and worse thing than returning the right ten.
      </>
    ),
  },
  {
    title: "Deletion that completes",
    body: (
      <>
        Invisible in the request transaction; chunks, vectors, blobs and artifacts reclaimed
        asynchronously; the root row last. The certificate is issued when the bytes are gone, not
        when the tombstone was written.
      </>
    ),
  },
  {
    title: "Audit that survives the data",
    body: (
      <>
        Reads and writes land in append-only stores that outlive what they describe. You cannot
        evidence &ldquo;we deleted it&rdquo; if the evidence was inside the deletion.
      </>
    ),
  },
];

function Pipeline() {
  return (
    <figure className="pipeline">
      <svg viewBox="0 0 720 132" role="img"
           aria-label="A write commits to storage, then enrichment, embedding and retrieval happen behind it"
           className="pipeline-svg">
        <defs>
          <marker id="tip" viewBox="0 0 10 10" refX="9" refY="5"
                  markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" />
          </marker>
        </defs>

        <rect x="1" y="34" width="112" height="42" rx="7"
              fill="none" stroke="currentColor" strokeWidth="1.2" />
        <text x="57" y="60" textAnchor="middle" fontSize="13" fill="currentColor">write</text>

        <line x1="113" y1="55" x2="171" y2="55" stroke="currentColor"
              strokeWidth="1.2" markerEnd="url(#tip)" />
        <text x="142" y="45" textAnchor="middle" fontSize="10"
              fill="currentColor" opacity="0.65">commits</text>

        <rect x="173" y="34" width="118" height="42" rx="7"
              fill="var(--accent-soft)" stroke="var(--accent)" strokeWidth="1.4" />
        <text x="232" y="60" textAnchor="middle" fontSize="13"
              fill="var(--accent)" fontWeight="600">stored</text>

        <line x1="291" y1="55" x2="349" y2="55" stroke="currentColor"
              strokeWidth="1.2" strokeDasharray="4 3" markerEnd="url(#tip)" />
        <text x="320" y="45" textAnchor="middle" fontSize="10"
              fill="currentColor" opacity="0.65">queued</text>

        <rect x="351" y="34" width="118" height="42" rx="7"
              fill="none" stroke="currentColor" strokeWidth="1.2" />
        <text x="410" y="60" textAnchor="middle" fontSize="13" fill="currentColor">searchable</text>

        <line x1="469" y1="55" x2="527" y2="55" stroke="currentColor"
              strokeWidth="1.2" strokeDasharray="4 3" markerEnd="url(#tip)" />

        <rect x="529" y="34" width="118" height="42" rx="7"
              fill="none" stroke="currentColor" strokeWidth="1.2" />
        <text x="588" y="60" textAnchor="middle" fontSize="13" fill="currentColor">enriched</text>

        <text x="232" y="98" textAnchor="middle" fontSize="10.5"
              fill="currentColor" opacity="0.6">durable and readable</text>
        <text x="500" y="98" textAnchor="middle" fontSize="10.5"
              fill="currentColor" opacity="0.6">behind the request, and visible while it happens</text>
      </svg>
      <figcaption>
        The write is synchronous. Everything after it is not — which is why ingest latency is a
        database write rather than a model call, and why the pipeline being down delays enrichment
        without losing data.
      </figcaption>
    </figure>
  );
}

export default function Landing({ authEnabled }: { authEnabled: boolean }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [caps, setCaps] = useState<Capabilities | null>(null);

  useEffect(() => {
    fetch("/api/capabilities")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => d && d.formats && setCaps(d))
      .catch(() => undefined);
  }, []);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error ?? "Sign-in failed.");
      window.location.reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const numbers = caps
    ? [
        { value: caps.formats, label: "file formats parsed" },
        { value: caps.data_types, label: "data types routed" },
        { value: caps.prompts, label: "extraction prompts" },
        { value: caps.webhook_providers, label: "webhook providers" },
      ]
    : [];

  return (
    <div className="landing">
      <header className="topbar">
        <div className="wordmark">
          <span className="dot" aria-hidden="true" />
          mem-dog
        </div>
        <div className="row">
          <ThemeToggle />
        </div>
      </header>

      <section className="hero-split">
        <div>
          <p className="eyebrow">Memory layer · sandbox</p>
          <h1>
            A memory layer that can <em>show its work</em>.
          </h1>
          <p className="hero-lede">
            Write anything — documents, spreadsheets, calendars, email, audio, video — and get it
            back by meaning, with a trace of exactly why each result was returned and what was
            considered and dropped.
          </p>
          {numbers.length > 0 && (
            <div className="numbers">
              {numbers.map((n) => (
                <div key={n.label}>
                  <span className="numbers-value">{n.value}</span>
                  <span className="numbers-label">{n.label}</span>
                </div>
              ))}
            </div>
          )}
          {caps && (
            <p className="counted">
              Counted from this build, not written into the copy — retrieval is running{" "}
              <code>{caps.embed_model}</code>.
            </p>
          )}
        </div>

        {authEnabled ? (
          <form className="signin-card" onSubmit={submit}>
            <h2>Sign in</h2>
            <p className="empty" style={{ marginTop: 0 }}>
              Opens the console: add data, search it, and inspect every step.
            </p>
            <label>
              Email
              <input
                type="email"
                value={email}
                autoComplete="username"
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </label>
            <label>
              Password
              <input
                type="password"
                value={password}
                autoComplete="current-password"
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </label>
            <button type="submit" disabled={busy}>
              {busy ? "Signing in…" : "Sign in"}
            </button>
            {error && <p className="err">{error}</p>}
            <p className="empty">
              Your session is an httpOnly cookie. The browser never holds a token, and requests are
              attributed to you rather than to a shared key.
            </p>
          </form>
        ) : (
          <div className="signin-card">
            <h2>Sign-in is not configured</h2>
            <p className="empty">
              This deployment has no identity provider set, so the console cannot be opened from a
              browser.
            </p>
          </div>
        )}
      </section>

      <Pipeline />

      <section className="steps">
        <h2 className="section-title">How a record moves</h2>
        <div className="steps-grid">
          {STEPS.map((step) => (
            <article key={step.n}>
              <span className="step-n">{step.n}</span>
              <h3>{step.title}</h3>
              <p>{step.body}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="steps">
        <h2 className="section-title">What it holds itself to</h2>
        <div className="pillars-grid">
          {PILLARS.map((pillar) => (
            <article key={pillar.title}>
              <h3>{pillar.title}</h3>
              <p>{pillar.body}</p>
            </article>
          ))}
        </div>
      </section>

      <footer className="landfoot">
        <span className="empty">
          Every claim on this page is one the console will let you check.
        </span>
      </footer>
    </div>
  );
}
