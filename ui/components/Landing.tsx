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
 * The bar, and the page it navigates.
 *
 * This was cut to two anchors once, on the reasoning that a list of seven
 * undifferentiated destinations asks a first-time reader to choose between
 * things they have no basis to choose between. The reasoning was half right:
 * the problem was never the count, it was that the bar said nothing about
 * *where you were* — so it read as seven equivalent choices rather than as a
 * position in an argument.
 *
 * So it is the whole page again, and the bar tracks the reader: the section
 * currently on screen is marked, which is the job a table of contents does and
 * the reason a long page can have one.
 *
 * **This list is the only source of truth for the bar, and dead links cannot
 * render.** The nav is filtered at mount to ids that actually exist in the
 * document, so renaming a section's id removes its anchor rather than leaving
 * one that scrolls nowhere — the failure that is invisible until somebody
 * clicks.
 */
const SECTIONS = [
  { id: "flow", label: "How it works" },
  { id: "graph", label: "Connections" },
  { id: "time", label: "Time" },
  { id: "alerts", label: "Alerts" },
  { id: "compaction", label: "Working set" },
  { id: "sources", label: "Sources" },
  { id: "principles", label: "Principles" },
  { id: "compare", label: "Where this sits" },
];

const STEPS = [
  {
    n: "01",
    title: "Write",
    body: "One endpoint for every producer — upload, webhook, crawler, SDK. It commits before it returns.",
  },
  {
    n: "02",
    title: "Store",
    body: "Bytes to object storage, text and metadata to Postgres. The ACL is sealed at write time.",
  },
  {
    n: "03",
    title: "Read",
    body: "Parsed from the sniffed bytes, never the caller's claim. Audio and video transcribed, images described.",
  },
  {
    n: "04",
    title: "Enrich",
    body: "Off by default — it is the part that spends money. The prompt is picked for the kind of thing it is.",
  },
  {
    n: "05",
    title: "Retrieve",
    body: "Vector and lexical in one query, one plan. The access rule is inside it, not a filter after it.",
  },
  {
    n: "06",
    title: "Answer",
    body: "Cites the passage behind each claim, and says so when the passages do not support one.",
  },
];

const PILLARS = [
  {
    title: "The staircase is visible",
    body: (
      <>
        <code>stored</code> → <code>searchable</code> → <code>enriched</code>. &ldquo;I uploaded it
        and search cannot find it&rdquo; is a state, not a bug, and you can see which one.
      </>
    ),
  },
  {
    title: "The trace, not just the answer",
    body: (
      <>
        Scores, which arm matched, spans into the source — and the records
        <em> considered and excluded</em>, with the reason for each.
      </>
    ),
  },
  {
    title: "Provenance on every derived row",
    body: (
      <>
        Model, build and time on every embedding, summary and transcript. Nothing is mutated in
        place.
      </>
    ),
  },
  {
    title: "Access control inside the query",
    body: (
      <>
        A predicate in the retrieval query, never a filter over results. Asking for ten and hiding
        three is worse than returning the right ten.
      </>
    ),
  },
  {
    title: "Deletion that completes",
    body: (
      <>
        Chunks, vectors, blobs and artifacts reclaimed asynchronously, the root row last. The
        certificate is issued when the bytes are gone, not when the tombstone was written.
      </>
    ),
  },
  {
    title: "Audit that survives the data",
    body: (
      <>
        Append-only, and outliving what it describes. You cannot evidence a deletion if the evidence
        was inside it.
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

/**
 * Which section the reader is looking at, and which anchors are real.
 *
 * Returns only the sections that exist in the document, so the bar cannot show
 * a link that scrolls nowhere. The observer's `rootMargin` pulls the top edge
 * down past the sticky bar and the bottom edge up to a third of the viewport,
 * so "current" means *the heading you are reading*, not the last one that
 * happened to touch the fold.
 */
function useSectionSpy() {
  // Starts as the whole list and *narrows* after mount, rather than starting
  // empty and growing. The first version grew, and the nav was consequently
  // absent from the server-rendered HTML entirely -- no links without
  // JavaScript, and a visible pop-in for everyone else. A correction that can
  // only remove is safe to apply late; one that has to add is not.
  const [present, setPresent] = useState<typeof SECTIONS>(SECTIONS);
  const [active, setActive] = useState<string>("");

  useEffect(() => {
    const found = SECTIONS.filter((s) => document.getElementById(s.id));
    setPresent(found);
    if (found.length === 0) return;

    const observer = new IntersectionObserver(
      (entries) => {
        const onscreen = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (onscreen[0]) setActive(onscreen[0].target.id);
      },
      { rootMargin: "-76px 0px -66% 0px", threshold: 0 },
    );
    found.forEach((s) => {
      const node = document.getElementById(s.id);
      if (node) observer.observe(node);
    });
    return () => observer.disconnect();
  }, []);

  return { present, active };
}

/**
 * How far down the page the reader is, 0 to 1.
 *
 * The bar already says *which* section you are in; this says how much is left,
 * which is the other half of the question a long page raises and the reason a
 * reader bails halfway. Measured on scroll behind `requestAnimationFrame` so a
 * fast scroll does not queue a layout read per event.
 */
function useScrollProgress() {
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    let queued = false;
    function measure() {
      queued = false;
      const doc = document.documentElement;
      const travel = doc.scrollHeight - doc.clientHeight;
      setProgress(travel <= 0 ? 0 : Math.min(1, Math.max(0, doc.scrollTop / travel)));
    }
    function onScroll() {
      if (queued) return;
      queued = true;
      requestAnimationFrame(measure);
    }
    measure();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, []);

  return progress;
}

/**
 * What comes back, including what did not.
 *
 * The headline claims the system shows its work, and a claim in a headline is
 * the cheapest thing on a page. This renders the actual shape of a result: the
 * passages returned with which arm matched them, and — the part no comparable
 * product produces — the records that were considered and **dropped, with the
 * reason for each**.
 *
 * It is an illustration, and it says so. Nothing can be queried before signing
 * in, so rendering a fabricated trace *as if* it were live would be the one
 * dishonest thing on a page whose whole argument is that its claims are
 * checkable.
 */
function Receipt() {
  const returned = [
    { score: "0.81", arm: "vector + lexical", where: "Email · 14 Jul",
      quote: "Legal flagged the indemnity cap and procurement will not move until the security review closes." },
    { score: "0.74", arm: "vector", where: "Call transcript · 2 Aug",
      quote: "They asked twice whether it can run inside their own tenancy." },
    { score: "0.62", arm: "lexical", where: "Deal note · 29 Aug",
      quote: "Security review still open; finance wants the cap at twelve months." },
  ];
  const dropped = [
    { where: "Slack thread · 30 Aug", why: "below the score threshold", detail: "0.31" },
    { where: "Contract PDF · 31 Aug", why: "not yet searchable", detail: "enrichment queued" },
    { where: "Invoice · 12 Jun", why: "you cannot read it", detail: "restricted to finance" },
  ];

  return (
    <figure className="receipt">
      <figcaption className="receipt-head">
        <span className="receipt-ask">&ldquo;why did the Northwind deal slip?&rdquo;</span>
        <span className="receipt-note">the shape of an answer — not a live query</span>
      </figcaption>

      <div className="receipt-band">Returned · 3</div>
      {returned.map((r) => (
        <div className="receipt-row" key={r.where}>
          <span className="receipt-score">{r.score}</span>
          <span className="receipt-body">
            <span className="receipt-quote">{r.quote}</span>
            <span className="receipt-meta">{r.where} · matched on {r.arm}</span>
          </span>
        </div>
      ))}

      <div className="receipt-band receipt-band-out">
        Considered and dropped · 3
      </div>
      {dropped.map((d) => (
        <div className="receipt-row receipt-row-out" key={d.where}>
          <span className="receipt-score receipt-score-out">&mdash;</span>
          <span className="receipt-body">
            <span className="receipt-quote">{d.why}</span>
            <span className="receipt-meta">{d.where} · {d.detail}</span>
          </span>
        </div>
      ))}

      <p className="receipt-foot">
        The bottom half is the difference. &ldquo;It found nothing&rdquo;,
        &ldquo;it is still indexing&rdquo; and &ldquo;you are not allowed to see it&rdquo; are
        three different situations, and they need three different fixes.
      </p>
    </figure>
  );
}

export default function Landing({ authEnabled }: { authEnabled: boolean }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const { present, active } = useSectionSpy();
  const progress = useScrollProgress();

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
          {present.map((section) => (
            <a
              key={section.id}
              href={`#${section.id}`}
              /* `aria-current` rather than a class: the state is "this is the
                 page section you are in", which is exactly what the attribute
                 means, and it reaches a screen reader as well as the eye. */
              aria-current={active === section.id ? "true" : undefined}
            >
              {section.label}
            </a>
          ))}
        </nav>
        <div className="row">
          <ThemeToggle />
          <a className="tab-cta" href="#signin">Sign in</a>
        </div>
        {/* Sits on the bar's own bottom edge, so it reads as the bar filling
            rather than as a second rule under it. */}
        <div
          className="railfill"
          style={{ transform: `scaleX(${progress})` }}
          aria-hidden="true"
        />
      </header>

      <section className="hero-split">
        <div>
          <p className="eyebrow">Memory layer · sandbox</p>
          <h1>
            It answers — then tells you what it <em>left out</em>.
          </h1>
          <p className="hero-lede">
            Documents, spreadsheets, calendars, email, audio, video — retrieved by meaning. Every
            result carries the passage behind it, and every record it dropped carries the reason.
            That second half is what makes an answer checkable.
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

      <Receipt />

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
          People, organizations and places resolve into entities — cautiously. Two nodes you can
          merge later beat one that fused two people and cannot be separated.
        </p>
      </section>

      <GraphFigure />

      <section className="steps">
        <div className="steps-grid">
          <article>
            <span className="step-n">EDGES</span>
            <h3>Claims, with their evidence</h3>
            <p>
              A relationship is something a document asserted, so it carries the records that say so and how many.
            </p>
          </article>
          <article>
            <span className="step-n">FREE</span>
            <h3>Connections that need no model</h3>
            <p>
              Two entities named in the same record are connected by that fact alone. No model, no cost.
            </p>
          </article>
          <article>
            <span className="step-n">ACL</span>
            <h3>Traversal stops where you cannot read</h3>
            <p>
              The walk stops at the edge of what you can read. A neighbourhood is not a way around an ACL.
            </p>
          </article>
        </div>
      </section>

      <section className="steps" id="time">
        <h2 className="section-title">When each thing was true</h2>
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
              A claim carries two clocks: when it was true, and when we came to believe it. Nothing is overwritten.
            </p>
          </article>
          <article>
            <span className="step-n">CLOSE</span>
            <h3>No model decides what stopped being true</h3>
            <p>
              Single-valued predicates close the old claim when a new one lands. A rule, not a judgement call.
            </p>
          </article>
        </div>
      </section>

      <section className="steps" id="alerts">
        <h2 className="section-title">Two ways to say what you are watching for</h2>
        <p className="hero-lede" style={{ marginBottom: 22 }}>
          Most of what people watch for is a rule, and rules are free. What is left over is a
          sentence, judged only over what the rules already narrowed — one call per batch, not per
          event.
        </p>
        <div className="steps-grid">
          <article>
            <span className="step-n">RULES</span>
            <h3>Conditions, over any shape</h3>
            <p>
              Compare a field or a dotted path — is one of, contains, greater than, is absent. An event kind this console has never seen is still reachable.
            </p>
          </article>
          <article>
            <span className="step-n">WORDS</span>
            <h3>And a sentence for the rest</h3>
            <p>
              <em>&ldquo;A customer signals they may leave.&rdquo;</em> Rules run first and decide what the model is shown. With no model available the run defers rather than guessing.
            </p>
          </article>
          <article>
            <span className="step-n">NEVER</span>
            <h3>Not once per write</h3>
            <p>
              Evaluation batches, so ten thousand arrivals are a handful of evaluations. What has been looked at is a mark in the database, not a message in a queue.
            </p>
          </article>
        </div>
      </section>

      <section className="steps" id="compaction">
        <h2 className="section-title">A working set that stops growing</h2>
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
              A summary carries its sources as a list, so the records behind it can still be reached — and erased.
            </p>
          </article>
          <article>
            <span className="step-n">CHEAP</span>
            <h3>The cheap half first</h3>
            <p>
              Deduplication and near-duplicate merging run before any model does.
            </p>
          </article>
          <article>
            <span className="step-n">SEE</span>
            <h3>Previewed before it is scheduled</h3>
            <p>
              A job says what it would fold and what it would cost before it is scheduled.
            </p>
          </article>
        </div>
      </section>

      <section className="steps" id="sources">
        <h2 className="section-title">A source you can tell is still working</h2>
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
              A position per scope, not per connector: one broken mailbox does not reset the other five.
            </p>
          </article>
          <article>
            <span className="step-n">SLOW</span>
            <h3>The limit belongs to the token</h3>
            <p>
              Rate limits belong to the credential, so two crawlers on one token share it rather than racing.
            </p>
          </article>
          <article>
            <span className="step-n">LAG</span>
            <h3>Stale is reported, not inferred</h3>
            <p>
              Seconds since the last item, per producer. Silence is the failure mode, so it is measured rather than inferred.
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
          <strong>A workspace answers over what people remembered to put in it.</strong> That is
          not a shortcoming of Notion, it is what a workspace is. mem-dog owns no documents and has
          no editor; it reads the email, tickets, calls and calendar that were never going to be
          pasted into a page.
        </p>
        <p className="hero-lede" style={{ marginBottom: 26 }}>
          Two things this does <em>not</em> claim. <strong>Self-hosting is not a
          differentiator</strong> — four of the six run on your own infrastructure and Onyx is MIT.
          And <strong>Onyx is ahead on permissions</strong>: it inherits each document&rsquo;s
          access rules from the source system, which mem-dog does not yet do. If that is your
          requirement today, Onyx is the better answer today.
        </p>

        <Matrix formats={caps?.formats ?? null} />

        <p className="legend">
          <span><span className="mark yes" aria-hidden="true" /> yes</span>
          <span><span className="mark part" aria-hidden="true" /> partial</span>
          <span><span className="mark scope" aria-hidden="true" /> not a stated focus</span>
          <span><span className="mark no" aria-hidden="true" /> no</span>
        </p>
        <p className="caveat">
          Every mem-dog cell is verifiable in this repository. Every other reflects what that
          product publicly documents as of September&nbsp;2026 — and where something is outside a
          product&rsquo;s stated scope it is marked so rather than asserted absent.
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
