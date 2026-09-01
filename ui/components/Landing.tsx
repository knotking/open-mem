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
  generators: number;
  registration_mode: string;
  formats: number;
  data_types: number;
  prompts: number;
  webhook_providers: number;
  crawler_strategies: number;
  connectors: number;
  alert_surfaces: number;
  embed_model: string;
  media_interpretation: boolean;
};

/**
 * Two anchors, not seven.
 *
 * The bar was a table of contents for a page that is one argument: the
 * sections chain — "How a record moves", then "It knows what connects to
 * what", then *"And* a working set that stops growing", *"And* a source you
 * can tell is still working". Copy that continues with "and" is not seven
 * destinations, and offering them as tabs asked a first-time reader to choose
 * between things they have no basis to choose between.
 *
 * So the bar marks the two genuine turns — how it works, and how it compares —
 * and everything between them is reached by reading on, which is what the page
 * was written for. The sections keep their ids: a link somebody already has
 * still lands.
 */
const TABS = [
  { id: "flow", label: "How it works" },
  { id: "compare", label: "Where this sits" },
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


/* The comparison, against the products people actually weigh this against.
 *
 * It used to compare with agent-memory SDKs — Mem0, Zep, Letta, Cognee,
 * Supermemory. That set is real but it is not the one a person asks about:
 * the question that arrives is "we already have Notion, why would we run
 * this?", and answering a question nobody asked is how a comparison table
 * becomes decoration.
 *
 * So the columns are Notion AI, the open-source workspaces people move to when
 * they leave it, and Onyx — which is the closest thing to mem-dog in this set
 * and beats it on the row that matters most to a team.
 *
 * The first two rows exist to stop the rest being read as a scoreboard. These
 * are **different categories**: a workspace owns what you write in it, and
 * mem-dog owns nothing and reads what you wrote elsewhere. A table that hid
 * that would be flattering itself with a category error. */
const COMPETITORS = ["mem-dog", "Notion AI", "AppFlowy", "AFFiNE", "Docmost", "Onyx"];

type Mark = "yes" | "part" | "scope" | "no";

/**
 * The one number in the table, left as a placeholder.
 *
 * `54 formats` was typed here and was 58 by the time anyone read it — four
 * rows above a stat tile that reads the same figure from `GET /capabilities`
 * and has never once been wrong, because nobody types it. So this row is
 * filled in at render time from the same source.
 */
const FORMATS = "FORMATS";

const MATRIX: { row: string; note?: string; cells: [Mark, string][] }[] = [
  {
    row: "What it is",
    note: "Two categories, not six competitors. Four of these are places you write things; two are systems that read what you wrote somewhere else.",
    cells: [
      ["yes", "Reads your existing tools, and shows its working"],
      ["yes", "A workspace with an assistant in it"],
      ["yes", "Open-source workspace, AI in the open-core tier"],
      ["yes", "Docs and whiteboard, with AI writing"],
      ["yes", "Team wiki, Confluence-shaped"],
      ["yes", "Open-source AI search across your tools"],
    ],
  },
  {
    row: "Where the content lives",
    note: "The distinction the rest of the table depends on. A workspace answers over what people remembered to put in it — which is the constraint, not the feature.",
    cells: [
      ["scope", "Nowhere new — it indexes your sources"],
      ["yes", "In Notion. That is the point of Notion"],
      ["yes", "In AppFlowy"],
      ["yes", "In AFFiNE"],
      ["yes", "In Docmost"],
      ["scope", "Nowhere new — it indexes your sources"],
    ],
  },
  {
    row: "You can write documents in it",
    note: "Where mem-dog is straightforwardly worse. It has no editor and will not get one — if your team needs a place to write, one of these is the answer and mem-dog sits behind it.",
    cells: [
      ["no", "No editor. Not the job"],
      ["yes", "Docs, databases, the lot"],
      ["yes", "Docs, kanban, databases"],
      ["yes", "Docs and an infinite canvas"],
      ["yes", "Wiki pages with permissions"],
      ["no", "Search and chat, not authoring"],
    ],
  },
  {
    row: "Answers over things you never wrote there",
    note: "Email, tickets, calls, calendars, CRM records. The material a question usually spans, and the material a workspace never contains.",
    cells: [
      ["yes", `${FORMATS} formats; audio and video transcribed`],
      ["part", "A handful of official connectors"],
      ["scope", "Imports, not live sources"],
      ["scope", "Imports, not live sources"],
      ["scope", "Imports, not live sources"],
      ["yes", "40+ connectors, synced"],
    ],
  },
  {
    row: "Access control",
    note: "Onyx is ahead here and it is the row a team should care about most: it inherits each document's permissions from the system it came from, so a person cannot retrieve what they could not open.",
    cells: [
      ["part", "Predicate inside the query; source permissions not yet synced"],
      ["yes", "Notion's own sharing model"],
      ["part", "Workspace and page level"],
      ["part", "Workspace level"],
      ["yes", "Spaces and page permissions"],
      ["yes", "Document-level, synced from the source"],
    ],
  },
  {
    row: "Why a result was excluded",
    note: "Ranked results are universal. Reporting what was considered and dropped, and the reason for each, is not — and it is the difference between an answer you can check and one you have to believe.",
    cells: [
      ["yes", "Per record: below threshold, or not yet searchable"],
      ["scope", "Not documented"],
      ["scope", "Not documented"],
      ["scope", "Not documented"],
      ["scope", "Not documented"],
      ["part", "Cites its sources; exclusions not reported"],
    ],
  },
  {
    row: "Point-in-time facts",
    note: "Two clocks, not one: when a thing was true, and when we came to believe it. A wiki holds the current page and its revisions, which answers neither.",
    cells: [
      ["yes", "Bi-temporal, in Postgres"],
      ["scope", "Page history, not fact validity"],
      ["scope", "Page history"],
      ["scope", "Page history"],
      ["scope", "Page history"],
      ["scope", "Not the model"],
    ],
  },
  {
    row: "Tell me when it changes",
    note: "Retrieval answers a question; this says something happened. Different failure modes — a search returning nothing is visible, an alert that never fires is silence.",
    cells: [
      ["yes", "On a change, or when a date comes due"],
      ["part", "Reminders and database automations"],
      ["scope", "Not emphasised"],
      ["scope", "Not emphasised"],
      ["scope", "Not emphasised"],
      ["scope", "Not the model"],
    ],
  },
  {
    row: "Erasure you can evidence",
    note: "Every product here deletes. The question is whether anything re-checks afterwards and hands you the result.",
    cells: [
      ["yes", "Async purge, then a re-queried certificate"],
      ["part", "Delete and retention settings"],
      ["part", "Delete"],
      ["part", "Delete"],
      ["part", "Delete"],
      ["part", "Delete and re-index"],
    ],
  },
  {
    row: "Provenance on derived rows",
    note: "Which model, which build, and a fingerprint of prompt + model + schema + parser — so changing a default makes old output detectably stale instead of quietly wrong.",
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
    row: "Runs on your infrastructure",
    note: "Table stakes in this set rather than an advantage — four of the six are self-hostable and one is MIT. Listed because it is the first thing people ask, not because it separates anything.",
    cells: [
      ["yes", "Your own cloud project, or a laptop"],
      ["no", "Hosted only"],
      ["yes", "AGPL-3.0, open-core AI"],
      ["yes", "Open-core; enterprise pieces proprietary"],
      ["yes", "AGPL-3.0, open-core"],
      ["yes", "MIT"],
    ],
  },
];

const MARK_LABEL: Record<Mark, string> = {
  yes: "yes",
  part: "partial",
  scope: "not a stated focus",
  no: "no",
};

function Matrix({ formats }: { formats: number | null }) {
  // The placeholder resolved once, here, rather than the table carrying a
  // number somebody has to remember to update.
  const matrix = MATRIX.map((row) => ({
    ...row,
    cells: row.cells.map(([mark, text]) =>
      [mark, text.replace(FORMATS, String(formats ?? 58))] as [Mark, string]),
  }));
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
          {matrix.map((row) => (
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
        { value: caps.alert_surfaces, label: "kinds of change you can watch" },
        { value: caps.generators, label: "things derivable from a memory" },
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
            {/* Read from the deployment rather than described in both
              * directions. The page used to say nothing at all about how a
              * person gets an account, which is the first question somebody
              * without one has. */}
            {caps?.registration_mode === "invite_only" && (
              <p className="empty">
                <strong>New accounts are invite-only here.</strong> Somebody already inside issues
                one; it is single-use, expiring, and bound to your address.
              </p>
            )}
            {caps?.registration_mode === "open" && (
              <p className="empty">
                Anyone who can authenticate may create an account on this deployment — which grants
                an identity and <strong>no access to anything</strong> until a member adds you.
              </p>
            )}
            {caps?.registration_mode === "disabled" && (
              <p className="empty">
                New accounts are closed on this deployment. Existing members can still sign in.
              </p>
            )}
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

      <section className="steps" id="compaction">
        <h2 className="section-title">And a working set that stops growing</h2>
        <p className="hero-lede" style={{ marginBottom: 22 }}>
          Memory layers usually keep the corpus small by overwriting: a newer memory replaces an
          older one and the old one is gone. That is a reasonable trade if nobody will ever ask
          what you used to believe. Here it would make the two clocks lie.
        </p>
        <div className="steps-grid">
          <article>
            <span className="step-n">KEEP</span>
            <h3>Folding is not deleting</h3>
            <p>
              Compaction archives what it folds. Archived records leave the default view and stay
              readable, searchable and citable when asked for — so the working set shrinks and the
              record does not.
            </p>
          </article>
          <article>
            <span className="step-n">FREE</span>
            <h3>The cheap half first</h3>
            <p>
              Most of what a corpus accumulates is the same record written twice — a re-crawl, a
              re-import. Noticing that needs no model. Summarising does, and it says so before you
              schedule it rather than after the bill.
            </p>
          </article>
          <article>
            <span className="step-n">SEE</span>
            <h3>Previewed before it is scheduled</h3>
            <p>
              A run reports what it would fold and archives nothing, through the same path a live
              one takes. Scheduling is refused until you have looked — a compaction nobody has
              seen is one that empties a memory quietly.
            </p>
          </article>
        </div>
      </section>

      <section className="steps" id="sources">
        <h2 className="section-title">And a source you can tell is still working</h2>
        <p className="hero-lede" style={{ marginBottom: 22 }}>
          A connector that quietly stopped syncing looks exactly like a project that went quiet.
          Both present as no new records. Only one is a problem, and nothing downstream can tell
          them apart without being told when each source was last actually reached.
        </p>
        <div className="steps-grid">
          <article>
            <span className="step-n">WHERE</span>
            <h3>A position per scope, not per connector</h3>
            <p>
              One crawler over forty Slack channels used to keep a single position, so a busy
              channel dragged it past thirty quiet ones and their history was never read. Each
              channel, repo, Jira project and folder now keeps its own.
            </p>
          </article>
          <article>
            <span className="step-n">SLOW</span>
            <h3>The limit belongs to the token</h3>
            <p>
              Two crawlers on one Slack connection draw down the same quota and neither can see
              the other. A rate limit parks the credential, so everything sharing it waits — and
              waits as long as the API asked for, not as long as we guessed.
            </p>
          </article>
          <article>
            <span className="step-n">LAG</span>
            <h3>Stale is reported, not inferred</h3>
            <p>
              Every scope reports when it last succeeded and why it last failed. A run that hit a
              limit is not a failed run: it keeps its position and retries the same range, so a
              gap in the record means a failure and never a silence.
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
          The question that actually arrives is <strong>&ldquo;we already have Notion — why would
          we run this?&rdquo;</strong> So this compares against Notion AI, the open-source
          workspaces people move to when they leave it, and Onyx, which is the closest thing here
          to what mem-dog does.
        </p>
        <p className="hero-lede" style={{ marginBottom: 26 }}>
          The honest answer starts with a category difference rather than a feature. <strong>A
          workspace answers over what people remembered to put in it.</strong> That is not a
          shortcoming of Notion — it is what a workspace is — but it means the answer is bounded by
          the discipline of everybody who did or did not write something down. mem-dog owns no
          documents and has no editor. It reads the email, the tickets, the calls and the calendar
          that were never going to be pasted into a page, and it shows what it considered.
        </p>
        <p className="hero-lede" style={{ marginBottom: 26 }}>
          Two things this deliberately does <em>not</em> claim. <strong>Self-hosting is not a
          differentiator</strong> — four of the six run on your own infrastructure and Onyx is MIT.
          And <strong>Onyx is ahead on permissions</strong>: it inherits each document&rsquo;s
          access rules from the system it came from, so a person cannot retrieve what they could not
          open. mem-dog enforces its own ACL inside the query and does not yet sync source
          permissions. If that is your requirement today, Onyx is the better answer today.
        </p>

        <Matrix formats={caps?.formats ?? null} />

        <p className="legend">
          <span><span className="mark yes" aria-hidden="true" /> yes</span>
          <span><span className="mark part" aria-hidden="true" /> partial</span>
          <span><span className="mark scope" aria-hidden="true" /> not a stated focus</span>
          <span><span className="mark no" aria-hidden="true" /> no</span>
        </p>
        <p className="caveat">
          Every mem-dog cell is verifiable in this repository. Every other cell reflects what that
          product publicly documents as of September&nbsp;2026 — and where something is simply not part
          of a product&rsquo;s stated scope it is marked so, rather than asserted absent. Those are
          different claims, and only one of them is defensible.
        </p>

        <h3 style={{ marginTop: 30 }}>Where the others are ahead</h3>
        <p className="hero-lede" style={{ marginBottom: 0 }}>
          A comparison that only flatters itself is not worth reading.
          <strong> Notion is a better place to write than this will ever be</strong> — mem-dog has
          no editor, no databases, no canvas, and no plan for any of them; it sits behind whatever
          you write in.
          <strong> Onyx has permission-aware retrieval today</strong>, synced from the source
          systems, which is the single most-requested thing here and is not built yet.
          <strong> AppFlowy and AFFiNE have communities</strong> in the tens of thousands of stars,
          against a system with none.
          <strong> Docmost is a finished wiki</strong> and this is not a wiki at all.
          If your team needs somewhere to write, pick one of those — and if you then want to ask
          questions that span the fifteen places your team did <em>not</em> write it down, and check
          the answer afterwards, come back here.
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
