"use client";

/**
 * The landing page.
 *
 * It says what the thing is before asking anyone to sign in. The three claims
 * below are the ones the system can actually back up today -- a landing page
 * that promises what the product does not do is a support ticket waiting to
 * happen.
 */

import { useState } from "react";

import ThemeToggle from "./ThemeToggle";

export default function Landing({ authEnabled }: { authEnabled: boolean }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

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

  return (
    <div className="landing">
      <header className="topbar">
        <div className="wordmark">
          <span className="dot" aria-hidden="true" />
          mem-dog
        </div>
        <div className="row">
          <ThemeToggle />
          {authEnabled ? (
            <button className="secondary" onClick={() => setOpen((v) => !v)}>
              {open ? "Close" : "Sign in"}
            </button>
          ) : (
            <span className="empty">sign-in is not configured</span>
          )}
        </div>
      </header>

      {open && authEnabled && (
        <form className="signin" onSubmit={submit}>
          <h2>Sign in</h2>
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
      )}

      <section className="hero">
        <h1>
          A memory layer that can <em>show its work</em>.
        </h1>
        <p>
          Write anything — text, documents, spreadsheets, calendars, email, audio, video — and get
          it back by meaning, with a trace of exactly why each result was returned.
        </p>
      </section>

      <section className="pillars">
        <article>
          <h3>The staircase is visible</h3>
          <p>
            <code>stored</code> → <code>searchable</code> → <code>enriched</code>. An item is
            durable the moment it is written and findable the moment it is embedded. &ldquo;I just
            uploaded it and search cannot find it&rdquo; is a state, not a bug — and you can see
            which one.
          </p>
        </article>
        <article>
          <h3>The trace, not just the answer</h3>
          <p>
            Ranked chunks with scores, which arm matched, character spans into the source, and the
            records that were <em>considered and excluded</em> — with the reason. Missing something
            you know is in the data has several causes, and they need different fixes.
          </p>
        </article>
        <article>
          <h3>Provenance on every derived row</h3>
          <p>
            Every embedding, summary and transcript records the model that produced it, the build
            that answered, and when. Nothing is mutated in place, so &ldquo;why does this say
            something different than last week?&rdquo; has an answer.
          </p>
        </article>
      </section>

      <section className="pillars">
        <article>
          <h3>Access control inside the query</h3>
          <p>
            The ACL is a predicate in the retrieval query, never a filter over results — asking for
            ten and hiding three is a different and worse thing than returning the right ten.
          </p>
        </article>
        <article>
          <h3>Deletion that actually completes</h3>
          <p>
            Invisible in the request transaction; chunks, vectors, blobs and derived artifacts
            reclaimed asynchronously; the root row last. A certificate is issued when the bytes are
            gone, not when the tombstone was written.
          </p>
        </article>
        <article>
          <h3>Audit that survives the data</h3>
          <p>
            Reads and writes land in separate append-only stores that outlive what they describe.
            You cannot evidence &ldquo;we deleted it&rdquo; if the evidence was inside the deletion.
          </p>
        </article>
      </section>

      <footer className="landfoot">
        <span className="empty">
          Sign in to open the console: add data, search it, and inspect every step.
        </span>
      </footer>
    </div>
  );
}
