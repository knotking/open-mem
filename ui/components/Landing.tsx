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
  connectors: number;
  embed_model: string;
  media_interpretation: boolean;
};

const TABS = [
  { id: "flow", label: "How it works" },
  { id: "graph", label: "Graph" },
  { id: "compare", label: "Comparison" },
  { id: "principles", label: "Principles" },
];

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


/* Every mem-dog cell is verifiable in this repository. Every other cell
   reflects what that product publicly documents as of August 2026 — and where
   something simply is not part of a product's stated scope it is marked so,
   rather than asserted absent. That distinction is the difference between a
   comparison and a smear, and it is also the difference between a table that
   survives a reader who knows the space and one that does not. */
const COMPETITORS = ["mem-dog", "Mem0", "Zep", "Letta", "Cognee", "Supermemory"];

type Mark = "yes" | "part" | "scope" | "no";

const MATRIX: { row: string; note?: string; cells: [Mark, string][] }[] = [
  {
    row: "What it is",
    cells: [
      ["yes", "Memory platform with an audit trail"],
      ["yes", "Memory layer you bolt onto an agent"],
      ["yes", "Temporal graph built from conversation"],
      ["yes", "Runtime where the agent is its memory"],
      ["yes", "Graph built from everything else"],
      ["yes", "Memory plus RAG over user context"],
    ],
  },
  {
    row: "Runs on your infrastructure",
    note: "Table stakes, not a moat — Onyx is MIT and air-gapped with SOC 2, Khoj runs fully local. Listed for completeness, not as an advantage. Zep retired its self-hosted Community Edition in 2025.",
    cells: [
      ["yes", "Your own cloud project"],
      ["yes", "Apache-2.0, needs a vector store"],
      ["part", "Graphiti only, Neo4j burden"],
      ["yes", "Apache-2.0"],
      ["yes", "Apache-2.0, embedded stores"],
      ["scope", "Managed service"],
    ],
  },
  {
    row: "Knowledge graph",
    cells: [
      ["yes", "Typed edges, evidence per edge"],
      ["part", "Graph memory available"],
      ["yes", "Graphiti, graph-native"],
      ["scope", "Not the model"],
      ["yes", "Graph-native"],
      ["scope", "Not emphasised"],
    ],
  },
  {
    row: "Point-in-time facts",
    note: "The sharpest divider in the category, and the one place mem-dog is plainly behind.",
    cells: [
      ["no", "No validity interval modelled"],
      ["part", "Timestamps, no past state"],
      ["yes", "Bi-temporal validity windows"],
      ["scope", "Not the model"],
      ["part", "Graph-native, time not a strategy"],
      ["scope", "Not emphasised"],
    ],
  },
  {
    row: "Correct a wrong merge",
    cells: [
      ["yes", "Reversible, evidence retained"],
      ["scope", "Not documented"],
      ["yes", "Merge and cleanup tools"],
      ["part", "Edit core memory by hand"],
      ["yes", "Merge and cleanup tools"],
      ["scope", "Not documented"],
    ],
  },
  {
    row: "Why a result was excluded",
    note: "Ranked results are common. Reporting what was considered and dropped, with the reason, is not.",
    cells: [
      ["yes", "Per record: threshold, or not yet searchable"],
      ["scope", "Not documented"],
      ["scope", "Not documented"],
      ["scope", "Not documented"],
      ["scope", "Not documented"],
      ["scope", "Not documented"],
    ],
  },
  {
    row: "Access control",
    cells: [
      ["yes", "Predicate inside the query"],
      ["part", "Per-user scoping"],
      ["part", "Per-user scoping"],
      ["part", "Per-agent"],
      ["scope", "Not documented"],
      ["part", "Per-user scoping"],
    ],
  },
  {
    row: "Erasure you can evidence",
    cells: [
      ["yes", "Async purge, then a re-queried certificate"],
      ["part", "Delete APIs"],
      ["part", "Delete APIs"],
      ["part", "Delete APIs"],
      ["part", "Delete APIs"],
      ["part", "Delete APIs"],
    ],
  },
  {
    row: "Provenance on derived rows",
    note: "Which model, which build, and a fingerprint of prompt + model + schema + parser, so a changed default makes old output detectably stale.",
    cells: [
      ["yes", "On every artifact and vector"],
      ["scope", "Not documented"],
      ["scope", "Not documented"],
      ["scope", "Not documented"],
      ["scope", "Not documented"],
      ["scope", "Not documented"],
    ],
  },
  {
    row: "Ingestion breadth",
    cells: [
      ["yes", "54 formats, audio and video transcribed"],
      ["part", "Text and messages"],
      ["part", "Conversation"],
      ["part", "Conversation"],
      ["yes", "Documents and structured data"],
      ["part", "Documents and text"],
    ],
  },
  {
    row: "Adoption",
    note: "Where mem-dog is furthest behind, and by a very long way.",
    cells: [
      ["no", "Prototype. No users"],
      ["yes", "~48k stars, 186M calls a quarter"],
      ["yes", "Widely deployed"],
      ["yes", "Large community"],
      ["part", "Growing"],
      ["part", "Growing"],
    ],
  },
];

const MARK_LABEL: Record<Mark, string> = {
  yes: "yes",
  part: "partial",
  scope: "not a stated focus",
  no: "no",
};

function Matrix() {
  return (
    <div className="scroll">
      <table className="matrix">
        <thead>
          <tr>
            <th>Dimension</th>
            {COMPETITORS.map((c) => (
              <th key={c} className={c === "mem-dog" ? "mine" : undefined}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {MATRIX.map((row) => (
            <tr key={row.row}>
              <th scope="row">
                {row.row}
                {row.note && <span className="rownote">{row.note}</span>}
              </th>
              {row.cells.map(([mark, text], i) => (
                <td key={COMPETITORS[i]} className={COMPETITORS[i] === "mem-dog" ? "mine" : undefined}>
                  <span className={`mark ${mark}`} aria-hidden="true" />
                  <span className="srmark">{MARK_LABEL[mark]}: </span>
                  {text}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function GraphFigure() {
  return (
    <figure className="pipeline">
      <svg viewBox="0 0 700 168" role="img"
           aria-label="Entities connected by typed edges, each edge naming the records that assert it"
           className="pipeline-svg">
        <defs>
          <marker id="etip" viewBox="0 0 10 10" refX="9" refY="5"
                  markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M0 0 L10 5 L0 10 z" fill="currentColor" />
          </marker>
        </defs>

        <circle cx="86" cy="58" r="27" fill="var(--accent-soft)" stroke="var(--accent)" strokeWidth="1.5" />
        <text x="86" y="62" textAnchor="middle" fontSize="11" fill="var(--accent)" fontWeight="600">person</text>
        <text x="86" y="102" textAnchor="middle" fontSize="11.5" fill="currentColor">Priya Raman</text>

        <line x1="113" y1="58" x2="253" y2="58" stroke="currentColor" strokeWidth="1.2" markerEnd="url(#etip)" />
        <text x="183" y="49" textAnchor="middle" fontSize="10" fill="currentColor" opacity=".72">works_for</text>
        <text x="183" y="74" textAnchor="middle" fontSize="9.5" fill="currentColor" opacity=".5">3 records assert this</text>

        <circle cx="282" cy="58" r="27" fill="none" stroke="currentColor" strokeWidth="1.3" />
        <text x="282" y="62" textAnchor="middle" fontSize="9.5" fill="currentColor">org</text>
        <text x="282" y="102" textAnchor="middle" fontSize="11.5" fill="currentColor">Northwind</text>

        <line x1="309" y1="58" x2="449" y2="58" stroke="currentColor" strokeWidth="1.2" markerEnd="url(#etip)" />
        <text x="379" y="49" textAnchor="middle" fontSize="10" fill="currentColor" opacity=".72">located_in</text>
        <text x="379" y="74" textAnchor="middle" fontSize="9.5" fill="currentColor" opacity=".5">1 record</text>

        <circle cx="478" cy="58" r="27" fill="none" stroke="currentColor" strokeWidth="1.3" />
        <text x="478" y="62" textAnchor="middle" fontSize="9" fill="currentColor">place</text>
        <text x="478" y="102" textAnchor="middle" fontSize="11.5" fill="currentColor">Lisbon</text>

        <line x1="86" y1="131" x2="478" y2="131" stroke="currentColor" strokeWidth="1"
              strokeDasharray="3 4" opacity=".55" />
        <text x="282" y="150" textAnchor="middle" fontSize="10" fill="currentColor" opacity=".6">
          two hops — reached through a relationship Priya was never named in
        </text>
      </svg>
      <figcaption>
        Entities resolve across name variants when they share a strong identifier, and every edge
        carries the records that assert it. One document saying something is a claim; three saying
        it independently is closer to a fact — so the count is reported rather than collapsed into a
        line on a diagram.
      </figcaption>
    </figure>
  );
}

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
        { value: caps.connectors, label: "app connectors" },
      ]
    : [];

  return (
    <div className="landing">
      <header className="topbar">
        <div className="wordmark">
          <span className="dot" aria-hidden="true" />
          mem-dog
        </div>
        <nav className="tabs" aria-label="Sections">
          {TABS.map((tab) => (
            <a key={tab.id} href={`#${tab.id}`}>{tab.label}</a>
          ))}
        </nav>
        <div className="row">
          <ThemeToggle />
          <a className="tab-cta" href="#signin">Sign in</a>
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
          <form className="signin-card" id="signin" onSubmit={submit}>
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
          <div className="signin-card" id="signin">
            <h2>Sign-in is not configured</h2>
            <p className="empty">
              This deployment has no identity provider set, so the console cannot be opened from a
              browser.
            </p>
          </div>
        )}
      </section>

      <Pipeline />

      <section className="steps" id="flow">
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

      <section className="steps" id="graph">
        <h2 className="section-title">It knows what connects to what</h2>
        <p className="hero-lede" style={{ marginBottom: 22 }}>
          Records name people, organizations and places. Those resolve into entities — cautiously,
          joining on a shared email or an exact name and otherwise keeping them apart, because two
          nodes you can merge later beat one node that fused two people and cannot be separated.
        </p>
      </section>

      <GraphFigure />

      <section className="steps">
        <div className="steps-grid">
          <article>
            <span className="step-n">EDGES</span>
            <h3>Claims, with their evidence</h3>
            <p>
              A relationship is something a document asserted, so it carries the records that say
              so and a count of how many. An edge nobody can check is an assertion, and extracted
              graphs are full of those.
            </p>
          </article>
          <article>
            <span className="step-n">FREE</span>
            <h3>Connections that need no model</h3>
            <p>
              Two entities named in the same record are connected by that fact alone. It costs
              nothing and works before any model has read for relationships — which is most of the
              time, early on.
            </p>
          </article>
          <article>
            <span className="step-n">ACL</span>
            <h3>Traversal stops where you cannot read</h3>
            <p>
              The access rule is inside the recursive query, not applied to its result. A path
              through a record you cannot see is never returned — arriving at its far end would
              disclose that the record exists.
            </p>
          </article>
        </div>
      </section>

      <section className="steps" id="time">
        <h2 className="section-title">And when each thing was true</h2>
        <p className="hero-lede" style={{ marginBottom: 22 }}>
          A claim carries two clocks: when it was true in the world, and when we learned it. A
          document imported today about last year is visible as of last year, and invisible as of
          last month — because we had not read it yet. One timestamp answers one of those
          questions wrongly.
        </p>
        <div className="steps-grid">
          <article>
            <span className="step-n">THEN</span>
            <h3>Nothing is overwritten</h3>
            <p>
              Moving to Berlin closes living in Lisbon rather than replacing it, so the graph as it
              stood in March is still there to ask. A fact can also be withdrawn without ever
              having been false — those are different, and they are stored differently.
            </p>
          </article>
          <article>
            <span className="step-n">RULES</span>
            <h3>No model decides what stopped being true</h3>
            <p>
              Some relationships hold one value at a time and some do not. A second address
              supersedes the first; a second employer does not, because people hold two jobs.
              That is declared, not inferred.
            </p>
          </article>
          <article>
            <span className="step-n">ALERTS</span>
            <h3>Say what matters, and be told</h3>
            <p>
              A location changing, a record becoming org-visible, a guess being confirmed. Recorded
              when it happens, then polled or pushed to a signed endpoint — and nothing runs until
              you have replayed it against history and read what it would have caught.
            </p>
          </article>
        </div>
      </section>

      <section className="steps" id="alerts">
        <h2 className="section-title">Two ways to say what you are watching for</h2>
        <p className="hero-lede" style={{ marginBottom: 22 }}>
          Most of what people watch for is a rule, and a rule costs nothing to evaluate. What is
          left over is a sentence, and that is judged — but only over what the rules already
          narrowed down, in one call for the whole batch rather than one per event.
        </p>
        <div className="steps-grid">
          <article>
            <span className="step-n">RULES</span>
            <h3>Conditions, over any shape</h3>
            <p>
              Compare a field or a dotted path into the event — is one of, contains, greater than,
              is absent. A kind of event this console has never seen is still reachable, so the
              vocabulary does not have to grow every time the system does.
            </p>
          </article>
          <article>
            <span className="step-n">WORDS</span>
            <h3>And a sentence for the rest</h3>
            <p>
              <em>&ldquo;A customer signals they may leave.&rdquo;</em> The rules still run first
              and decide what the model is even shown, which is why they are required. If no model
              is available the run defers rather than guessing — a wrong yes is a false alarm and a
              wrong no is a silence nobody notices.
            </p>
          </article>
          <article>
            <span className="step-n">NEVER</span>
            <h3>Not once per write</h3>
            <p>
              Evaluation waits and batches, so ten thousand records arriving at once is a handful
              of evaluations rather than ten thousand. What has been looked at is a mark in the
              database, not a message in a queue, so nothing is lost when a machine goes away.
            </p>
          </article>
        </div>
      </section>

      <section className="steps" id="principles">
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

      <section className="steps" id="compare">
        <h2 className="section-title">Where this sits</h2>
        <p className="hero-lede" style={{ marginBottom: 8 }}>
          Agent memory is a crowded category — Mem0, Zep, Letta, Supermemory and a steady stream of
          YC batches are all building it. They are mostly optimising for adoption, latency and how
          fast an agent can start remembering.
        </p>
        <p className="hero-lede" style={{ marginBottom: 26 }}>
          mem-dog optimises for a different question: <strong>can you prove what the system knew,
          why it answered that, and that you actually deleted it?</strong> That is a worse trade if
          you are shipping a chatbot this week, and the right one if the data is regulated, shared
          across a team, or subject to erasure requests.
        </p>
        <p className="hero-lede" style={{ marginBottom: 26 }}>
          One thing this deliberately does <em>not</em> claim: self-hosting is not a
          differentiator. Onyx is MIT-licensed, air-gapped and SOC&nbsp;2 Type&nbsp;II with 40+
          connectors; Khoj runs entirely on local models. Private deployment is table stakes here,
          and treating it as a moat is the most common way this category oversells itself.
        </p>

        <Matrix />

        <p className="legend">
          <span><span className="mark yes" aria-hidden="true" /> yes</span>
          <span><span className="mark part" aria-hidden="true" /> partial</span>
          <span><span className="mark scope" aria-hidden="true" /> not a stated focus</span>
          <span><span className="mark no" aria-hidden="true" /> no</span>
        </p>
        <p className="caveat">
          Every mem-dog cell is verifiable in this repository. Every other cell reflects what that
          product publicly documents as of August&nbsp;2026 — and where something is simply not part
          of a product&rsquo;s stated scope it is marked so, rather than asserted absent. Those are
          different claims, and only one of them is defensible.
        </p>

        <h3 style={{ marginTop: 30 }}>Where the others are ahead</h3>
        <p className="hero-lede" style={{ marginBottom: 0 }}>
          A comparison that only flatters itself is not worth reading.
          <strong> Zep&rsquo;s temporal knowledge graph timestamps every fact</strong>, so it can
          answer what was true in March; mem-dog models no validity interval and cannot.
          <strong> Mem0&rsquo;s adoption dwarfs this</strong> — tens of thousands of stars and
          hundreds of millions of API calls a quarter, against a system with none.
          <strong> Supermemory is faster.</strong> <strong>Letta</strong> manages an agent&rsquo;s
          working context, which mem-dog does not attempt at all. If you want a memory layer that
          works this afternoon with a large community behind it, pick one of those. If you need to
          answer an auditor, come back here.
        </p>
      </section>

      <footer className="landfoot">
        <span className="empty">
          Every claim on this page is one the console will let you check.
        </span>
      </footer>
    </div>
  );
}
