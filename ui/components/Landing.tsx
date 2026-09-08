"use client";

/**
 * The landing page.
 *
 * It says what the thing is before asking anyone to sign in, and every claim
 * on it is one the system can actually back. The numbers are counted from the
 * running build rather than written into copy, because a number a maintainer
 * typed is wrong within a month and wrong in the flattering direction.
 */

import { useEffect, useRef, useState } from "react";

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
  /** Configuration, not code — claimed only where switched on. */
  url_context?: boolean;
  repo_analysis?: boolean;
};

/**
 * The bar, and the page it navigates — five beats, not eight anchors.
 *
 * This was cut to two once and restored to the whole page, on the reasoning
 * that the problem was never the count but that the bar said nothing about
 * *where you were*. That was half right. Position-tracking fixed the "seven
 * equivalent choices" problem; it did not fix eight labels reading as a list of
 * parts rather than an argument with an order.
 *
 * So each entry now covers a run of sections and is named for the beat it is:
 * how it works, what it connects, what it tells you, what it accepts, why to
 * trust it. Fewer things to choose between, and the labels say what the page
 * argues rather than which feature lives where.
 *
 * **Groups must cover *contiguous* sections.** The bar is a position as well as
 * a menu, so a group spanning a gap would light up, go dark, and light again as
 * the reader scrolled through it — which reads as a bug in the page rather than
 * a choice about the menu.
 *
 * **This list is the only source of truth for the bar, and dead links cannot
 * render.** A group is dropped if none of the sections it covers exists, and it
 * anchors to the first that does — so renaming a section id removes or retargets
 * its anchor rather than leaving one that scrolls nowhere.
 */
function sentence(detail: unknown): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const said = detail
      .map((d) => (typeof d === "string" ? d : (d as { msg?: string })?.msg))
      .filter(Boolean);
    if (said.length) return said.join("; ");
  }
  return "Something went wrong. Try again in a moment.";
}

/** One published corpus. Everything corpus-specific lives here rather than in
 *  the component: the starter questions, the placeholder and the attribution
 *  were all written for one text, and a second corpus made every one of them
 *  wrong. The server carries them so a new demo is configuration. */
type DemoApp = {
  key: string; title: string; blurb: string;
  questions: string[]; note: string; records: number;
};

/** The gallery, and the allowance shared across all of it. */
type DemoInfo = {
  demos: DemoApp[]; remaining_today: number; daily_cap: number;
};

/**
 * The whole system in one picture: where records come from, the single path
 * they take in, the three indexes that path builds, and what those indexes are
 * for.
 *
 * Inline SVG rather than an image so it takes the page's own colours in both
 * themes, and so the text in it is text. Deliberately the only diagram on the
 * page -- a second one would be an explanation, and the explanations are in the
 * docs now.
 */
function Flow() {
  return (
    <section className="flow" id="flow" aria-labelledby="flow-title">
      <h2 className="section-title" id="flow-title">One path in, three indexes, then apps</h2>
      <div className="flow-scroll">
        <svg viewBox="0 0 980 330" className="flow-svg" role="img"
             aria-label="Pull and inbound sources feed one write path, which builds a
                         retrieval index, a knowledge graph and a reverse index; those
                         serve alerts, pattern search and state machines.">
          <defs>
            <marker id="flow-arrow" viewBox="0 0 10 10" refX="9" refY="5"
                    markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--muted)" />
            </marker>
          </defs>

          {/* sources */}
          <g className="flow-box">
            <rect x="8" y="46" width="176" height="82" rx="10" />
            <text x="24" y="76" className="flow-h">Pull</text>
            <text x="24" y="98" className="flow-s">crawlers · repos</text>
            <text x="24" y="116" className="flow-s">connectors · feeds</text>
          </g>
          <g className="flow-box">
            <rect x="8" y="196" width="176" height="82" rx="10" />
            <text x="24" y="226" className="flow-h">Inbound</text>
            <text x="24" y="248" className="flow-s">webhooks · uploads</text>
            <text x="24" y="266" className="flow-s">email · SDK</text>
          </g>

          {/* the single write path */}
          <g className="flow-box flow-spine">
            <rect x="250" y="106" width="150" height="112" rx="10" />
            <text x="266" y="140" className="flow-h">Write</text>
            <text x="266" y="162" className="flow-s">one endpoint</text>
            <text x="266" y="180" className="flow-s">commits, then</text>
            <text x="266" y="198" className="flow-s">enriches</text>
          </g>

          {/* what the write path builds */}
          <g className="flow-box">
            <rect x="466" y="26" width="184" height="72" rx="10" />
            <text x="482" y="54" className="flow-h">Retrieval index</text>
            <text x="482" y="76" className="flow-s">chunks · embeddings</text>
          </g>
          <g className="flow-box">
            <rect x="466" y="126" width="184" height="72" rx="10" />
            <text x="482" y="154" className="flow-h">Knowledge graph</text>
            <text x="482" y="176" className="flow-s">entities · edges</text>
          </g>
          <g className="flow-box">
            <rect x="466" y="226" width="184" height="72" rx="10" />
            <text x="482" y="254" className="flow-h">Reverse index</text>
            <text x="482" y="276" className="flow-s">lexical · facets</text>
          </g>

          {/* what they are for */}
          <g className="flow-box flow-app">
            <rect x="796" y="26" width="176" height="72" rx="10" />
            <text x="812" y="54" className="flow-h">Alerts</text>
            <text x="812" y="76" className="flow-s">tell me when</text>
          </g>
          <g className="flow-box flow-app">
            <rect x="796" y="126" width="176" height="72" rx="10" />
            <text x="812" y="154" className="flow-h">Pattern search</text>
            <text x="812" y="176" className="flow-s">standing queries</text>
          </g>
          <g className="flow-box flow-app">
            <rect x="796" y="226" width="176" height="72" rx="10" />
            <text x="812" y="254" className="flow-h">State machines</text>
            <text x="812" y="276" className="flow-s">workflows · cases</text>
          </g>

          <g className="flow-line">
            {/* sources into the one path */}
            <path d="M 184 87 C 216 87 218 132 250 132" />
            <path d="M 184 237 C 216 237 218 192 250 192" />
            {/* the path builds each index */}
            <path d="M 400 148 C 432 148 434 62 466 62" />
            <path d="M 400 162 L 466 162" />
            <path d="M 400 176 C 432 176 434 262 466 262" />
            {/* every index serves every app, via one bus */}
            <path d="M 650 62 L 706 62" />
            <path d="M 650 162 L 706 162" />
            <path d="M 650 262 L 706 262" />
            <path d="M 706 62 L 706 262" className="flow-bus" />
            <path d="M 706 62 L 796 62" />
            <path d="M 706 162 L 796 162" />
            <path d="M 706 262 L 796 262" />
          </g>
        </svg>
      </div>
    </section>
  );
}

function PublicDemo({ app, info, setInfo }: {
  app: DemoApp;
  info: DemoInfo; setInfo: (f: (i: DemoInfo | null) => DemoInfo | null) => void;
}) {
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<{
    question: string; answer: string; grounded: boolean;
    citations: { marker: number; text: string }[];
  }[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const foot = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (turns.length) foot.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns.length, busy]);

  // Switching corpus clears the transcript. Leaving it would show one corpus's
  // answers under another's name, with citations into records the new corpus
  // does not contain -- which reads as the demo answering from the wrong data,
  // because it is.
  useEffect(() => {
    setTurns([]);
    setOpen(null);
    setError(null);
  }, [app.key]);

  async function send(preset?: string) {
    const asked = (preset ?? question).trim();
    if (!asked || busy) return;
    setBusy(true);
    setError(null);
    setQuestion("");
    try {
      const response = await fetch("/api/proxy/api/v1/public/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        // The key, never a project id. The server resolves it against the
        // published registry; a name it did not publish reaches nothing.
        body: JSON.stringify({ question: asked, demo: app.key }),
      });
      const body = await response.json();
      if (!response.ok) {
        // The server's sentence, not a status code. A visitor who has run out
        // of questions is not looking at an error, and "429" reads as broken.
        // `detail` is not always a sentence: FastAPI answers a validation
        // failure with a *list* of objects, and throwing that renders the
        // useless "[object Object]" -- which is what a visitor saw when the
        // proxy forwarded this body without a content-type.
        throw new Error(sentence(body?.detail));
      }
      setTurns((previous) => [...previous, body]);
      setInfo((i) => (i ? { ...i, remaining_today: Math.max(0, i.remaining_today - 1) } : i));
    } catch (e) {
      setError((e as Error).message);
      setQuestion(asked);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="demo-card" id="demo">
      <h2>{app.title}</h2>

      <div className="demo-box">
        <div className="demo-scroll">
        {turns.length === 0 && (
          <div className="demo-starters">
            {app.questions.map((example) => (
              <button key={example} className="secondary" disabled={busy}
                      onClick={() => void send(example)}>
                {example}
              </button>
            ))}
          </div>
        )}

        {turns.map((turn, i) => (
          <div className="chatturn" key={i}>
            <div className="bubble asked">{turn.question}</div>
            <div className={`bubble answered${turn.grounded ? "" : " ungrounded"}`}>
              {turn.answer}
            </div>
            <div className="turnfoot">
              {!turn.grounded && (
                <span className="chip warnchip">not supported by the text</span>
              )}
              {turn.citations.length > 0 && (
                <button className="linkish"
                        onClick={() => setOpen(open === i ? null : i)}>
                  {open === i ? "▾" : "▸"} {turn.citations.length} passage
                  {turn.citations.length === 1 ? "" : "s"}
                </button>
              )}
            </div>
            {open === i && (
              <div className="turndetail">
                {turn.citations.map((c) => (
                  <div className="hit" key={c.marker}>
                    <div className="meta"><span className="chip on">[{c.marker}]</span></div>
                    <div className="text">{c.text}</div>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}

        {busy && (
          <div className="chatturn">
            <div className="bubble answered thinking">
              <span className="dots"><i /><i /><i /></span> reading the text…
            </div>
          </div>
        )}
        <div ref={foot} />
        </div>

        <div className="demo-composer">
          <input
            type="text" value={question} disabled={busy}
            placeholder={`Ask ${app.title} a question…`}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void send(); } }}
          />
          <button onClick={() => void send()} disabled={busy || !question.trim()}>
            {busy ? "Reading…" : "Ask"}
          </button>
          {/* Clearing also brings the starter questions back, which is the
            * point: a transcript somebody is done with is the one thing on the
            * card standing between them and asking something else. The opened
            * citation goes with it -- an index into turns that no longer exist
            * would reopen an unrelated answer's passages. */}
          {turns.length > 0 && (
            <button
              className="secondary"
              disabled={busy}
              onClick={() => { setTurns([]); setOpen(null); setError(null); }}
            >
              Clear
            </button>
          )}
        </div>
        {error && <p className="err">{error}</p>}
        <p className="demo-note">
          {app.note && <>{app.note} </>}
          {app.records > 0 && <>{app.records} records, ingested exactly as your own documents
          would be. </>}
          {/* The allowance sits beside the control that spends it rather than
            * being discovered at zero, and it is shared across every demo here
            * -- one budget for the gallery, not one each. */}
          <strong>{info.remaining_today}</strong> of {info.daily_cap} questions left today,
          across every demo — a fixed daily budget is what keeps this free.
        </p>
      </div>
    </div>
  );
}

export default function Landing({ authEnabled }: { authEnabled: boolean }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [demo, setDemo] = useState<DemoInfo | null>(null);
  // Which app the chat is pointed at. Null until the gallery arrives; the
  // first entry once it does, so a visitor lands on something answerable
  // rather than on a chooser.
  const [picked, setPicked] = useState<string | null>(null);
  // The sign-in form is a panel off the top bar rather than half the hero: the
  // first thing a visitor should be able to do here is ask the corpus a
  // question, and a form demanding an account they do not have is the opposite
  // of that. Signing in is still one click away, where a header keeps it.
  const [signinOpen, setSigninOpen] = useState(false);

  useEffect(() => {
    void fetch("/api/proxy/api/v1/public/demos")
      .then((r) => (r.ok ? r.json() : null))
      .then((body: DemoInfo | null) => {
        setDemo(body);
        setPicked(body?.demos?.[0]?.key ?? null);
      })
      .catch(() => setDemo(null));
  }, []);

  // Escape closes it, because a panel that can only be dismissed by finding
  // the button again is a trap for anyone not using a mouse.
  useEffect(() => {
    if (!signinOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setSigninOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [signinOpen]);

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


  // One definition, two homes: the top-bar panel when the demo holds the
  // hero, and the hero itself when there is no demo to put there. Never
  // both at once -- two elements carrying `id="signin"` would make the
  // anchor mean whichever the browser happened to find first.
  const apps = demo?.demos ?? [];
  const app = apps.find((a) => a.key === picked) ?? apps[0] ?? null;
  const heroHasDemo = Boolean(app);
  const signInCard = authEnabled ? (
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
        );

  return (
    <div className="landing" id="top">
      <header className="topbar">
        {/* A real anchor rather than a scroll handler: it works without
            JavaScript, is reachable by keyboard, and offers the usual
            open-in-new-tab affordances a wordmark is expected to have. */}
        <a className="wordmark" href="#top" aria-label="Back to the top">
          <span className="dot" aria-hidden="true" />
          mem-dog
        </a>
        {/* One destination. The tab strip tracked nine page sections that no
            longer exist; the reading is in the docs now. */}
        <nav className="tabs" aria-label="Sections">
          <a href="/docs">Docs</a>
        </nav>
        <div className="row">
          <ThemeToggle />
          {heroHasDemo ? (
            <div className="signin-menu">
              <button
                className="tab-cta"
                aria-expanded={signinOpen}
                onClick={() => setSigninOpen(!signinOpen)}
              >
                {signinOpen ? "Close" : "Sign in"}
              </button>
              {signinOpen && <div className="signin-pop">{signInCard}</div>}
            </div>
          ) : (
            <a className="tab-cta" href="#signin">Sign in</a>
          )}
        </div>
      </header>

      {/* The demo is the page's opening move, at full width and above the
          argument for it. Somebody who has already asked the corpus a question
          reads the copy below as an explanation of something they have seen;
          the other order asks them to take it on faith first. */}
      {heroHasDemo && (
        <section className="hero-demo">
          <p className="eyebrow">Memory layer · sandbox · no account needed</p>
          {/* Only when there is a choice to make. A row of one card is a
            * control that decides nothing, and it pushes the thing a visitor
            * came to do further down the page. */}
          {apps.length > 1 && (
            <div className="demo-apps" role="tablist" aria-label="Demo corpora">
              {apps.map((a) => (
                <button
                  key={a.key}
                  role="tab"
                  aria-selected={a.key === app!.key}
                  className={`demo-app${a.key === app!.key ? " on" : ""}`}
                  onClick={() => setPicked(a.key)}
                >
                  <span className="demo-app-title">{a.title}</span>
                  {/* What this one shows that the others do not. Without it the
                    * gallery is a row of names and every card looks alike. */}
                  {a.blurb && <span className="demo-app-blurb">{a.blurb}</span>}
                  {a.records > 0 && (
                    <span className="demo-app-count">{a.records} records</span>
                  )}
                </button>
              ))}
            </div>
          )}
          <PublicDemo app={app!} info={demo!} setInfo={setDemo} />
        </section>
      )}

      {/* One headline, one diagram. Everything that used to be argued here --
          nine sections of prose and a comparison matrix -- is in the docs,
          where somebody who wants it is already looking. */}
      {!heroHasDemo && (
        <section className="hero-split">
          <div>
            <p className="eyebrow">Memory layer · sandbox</p>
            <h1>A memory layer that <em>shows its work</em>.</h1>
          </div>
          {signInCard}
        </section>
      )}

      <Flow />


      <footer className="landfoot">
        <span className="empty">
          Every claim on this page is one the console will let you check.
        </span>
      </footer>
    </div>
  );
}
