"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import Capture, { humanBytes } from "./Capture";

type Stair = {
  total: number;
  stored: number;
  searchable: number;
  enriched: number;
  awaiting_fetch: number;
};

type Citation = {
  data_id: string;
  chunk_id: string;
  text: string;
  span_start: number;
  span_end: number;
  score: number;
  matched_by: string[];
  state: string;
};

type Version = {
  version_id: string;
  revision: number;
  source: string;
  content_chars: number;
  mime_type: string | null;
  model_id: string | null;
  tokens: number | null;
  preview: string | null;
  created_at: string;
  detail: Record<string, unknown>;
};

type Item = {
  data_id: string;
  state: string;
  mime_type: string | null;
  data_type: string | null;
  storage_ref: string | null;
  checksum: string | null;
  size_bytes: number | null;
  parse_status: string | null;
  parse_detail: Record<string, unknown> | null;
  content_text: string | null;
  extracted_text: string | null;
};

type Excluded = {
  data_id: string;
  reason: "threshold" | "not_yet_enriched";
  score: number | null;
  state: string | null;
};

type Trace = {
  query_id: string;
  results: Citation[];
  model_id: string;
  generator_version: string | null;
  corpus: Stair | null;
  excluded: Excluded[];
};

const REASON_TEXT: Record<Excluded["reason"], string> = {
  threshold: "retrieved, ranked below the cut",
  not_yet_enriched: "not searchable yet — it could not have matched",
};

async function call<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api/proxy/${path}`, {
    method: body ? "POST" : "GET",
    headers: body ? { "content-type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload?.detail ?? `request failed (${response.status})`);
  return payload as T;
}

export default function Sandbox({
  projectId,
  producerId,
}: {
  projectId: string;
  producerId: string;
}) {
  const [text, setText] = useState(
    "The checkout service returned 502s for eleven minutes after a bad deploy.\n\nRollback completed at 14:02 UTC and error rates recovered.",
  );
  const [query, setQuery] = useState("rollback recovered error rates");
  const [stair, setStair] = useState<Stair | null>(null);
  const [trace, setTrace] = useState<Trace | null>(null);
  const [busy, setBusy] = useState<"write" | "search" | "upload" | null>(null);
  const [item, setItem] = useState<Item | null>(null);
  const [versions, setVersions] = useState<Version[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const poll = useRef<ReturnType<typeof setInterval> | null>(null);

  const refresh = useCallback(async () => {
    try {
      setStair(await call<Stair>(`api/v1/projects/${projectId}/staircase`));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [projectId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Poll only while something is actually climbing. A permanent timer is a
  // permanent load on an API that scales to zero.
  useEffect(() => {
    const climbing = stair !== null && stair.stored + stair.searchable > 0;
    if (climbing && poll.current === null) {
      poll.current = setInterval(() => void refresh(), 2000);
    } else if (!climbing && poll.current !== null) {
      clearInterval(poll.current);
      poll.current = null;
    }
    return () => {
      if (poll.current !== null && !climbing) {
        clearInterval(poll.current);
        poll.current = null;
      }
    };
  }, [stair, refresh]);

  // Watch one item up the staircase. Media takes a model call, so this is the
  // difference between "it is working" and "it is broken".
  const track = useCallback(
    async (dataId: string) => {
      for (let attempt = 0; attempt < 60; attempt += 1) {
        const current = await call<Item>(`api/v1/data/${dataId}`);
        setItem(current);
        setVersions((await call<{ versions: Version[] }>(`api/v1/data/${dataId}/versions`)).versions);
        await refresh();
        const settled =
          current.state === "enriched" ||
          (current.parse_status !== null && current.parse_status !== "parsed");
        if (settled) return;
        await new Promise((r) => setTimeout(r, 2000));
      }
    },
    [refresh],
  );

  async function ingestBytes(name: string, mime: string, base64: string, size: number) {
    setBusy("upload");
    setError(null);
    setNote(null);
    try {
      const response = await call<{ results: { data_id: string; state: string }[] }>(
        "api/v1/write",
        {
          producer_id: producerId,
          items: [
            { external_id: name, content: { kind: "inline", bytes_b64: base64 } },
          ],
        },
      );
      const first = response.results[0];
      setNote(
        `${name} (${humanBytes(size)}) committed as ${first.data_id}. ` +
          "The bytes are in object storage; interpretation is queued.",
      );
      await track(first.data_id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function write() {
    setBusy("write");
    setError(null);
    setNote(null);
    try {
      const response = await call<{ results: { data_id: string; state: string }[] }>(
        "api/v1/write",
        {
          producer_id: producerId,
          items: [
            {
              external_id: `sandbox-${Date.now()}`,
              content: { kind: "inline", text },
            },
          ],
        },
      );
      const first = response.results[0];
      setNote(`Committed as ${first.data_id} at state “${first.state}”. Enrichment is queued.`);
      await track(first.data_id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function search() {
    setBusy("search");
    setError(null);
    try {
      setTrace(
        await call<Trace>("api/v1/retrieve", {
          query,
          filter: { project_id: projectId },
        }),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <section className="panel">
        <h2>Write</h2>
        <div className="notice">
          <strong>This is real ingestion.</strong> Text written here goes through the same write
          path, the same workers and the same retrieval as production — into real storage, under
          the project’s real ACL defaults and retention. There is no sandbox code path, which is
          the only reason what you see here tells you anything.
        </div>
        <textarea value={text} onChange={(e) => setText(e.target.value)} />
        <div className="row end" style={{ marginTop: 10 }}>
          <button onClick={write} disabled={busy !== null || text.trim() === ""}>
            {busy === "write" ? "Writing…" : "Write item"}
          </button>
        </div>
        {note && <p className="empty" style={{ marginBottom: 0 }}>{note}</p>}
      </section>

      <section className="panel">
        <h2>Upload or record</h2>
        <div className="notice">
          <strong>Media is interpreted by a model.</strong> Audio and video are transcribed,
          images are described and their text transcribed. That costs tokens per file and the
          cost is recorded per revision below — it is the expensive tier by a wide margin.
        </div>
        <Capture onSubmit={ingestBytes} busy={busy !== null} />
      </section>

      {item && <ItemPanel item={item} versions={versions} />}

      <section className="panel">
        <h2>Readiness staircase</h2>
        {stair === null ? (
          <p className="empty">Loading…</p>
        ) : (
          <Staircase stair={stair} />
        )}
      </section>

      <section className="panel">
        <h2>Search</h2>
        <div className="row">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void search()}
            style={{ flex: 1, minWidth: 240 }}
          />
          <button onClick={search} disabled={busy !== null || query.trim() === ""}>
            {busy === "search" ? "Searching…" : "Retrieve"}
          </button>
        </div>
        {error && <p className="err">{error}</p>}
      </section>

      {trace && <TracePanels trace={trace} />}
    </>
  );
}

function ItemPanel({ item, versions }: { item: Item; versions: Version[] }) {
  const text = item.extracted_text ?? item.content_text ?? "";
  const stuck = item.parse_status !== null && item.parse_status !== "parsed";
  return (
    <section className="panel">
      <h2>The item, and every revision of it</h2>
      <table className="kv">
        <tbody>
          <tr><td>id</td><td><code>{item.data_id}</code></td></tr>
          <tr><td>state</td><td><span className={`chip ${item.state}`}>{item.state}</span></td></tr>
          <tr><td>sniffed type</td><td><code>{item.mime_type ?? "—"}</code> → {item.data_type ?? "—"}</td></tr>
          {item.storage_ref && (
            <tr><td>bytes</td><td className="provenance">{item.storage_ref}</td></tr>
          )}
          {item.checksum && <tr><td>checksum</td><td className="provenance">{item.checksum}</td></tr>}
        </tbody>
      </table>

      {stuck && (
        <div className="notice" style={{ marginTop: 12 }}>
          <strong>Stored but not interpreted — {item.parse_status}.</strong>{" "}
          {String((item.parse_detail?.reason as string) ?? "")} The bytes and their checksum are
          untouched, so turning interpretation on later fixes this without re-uploading.
        </div>
      )}

      {text && (
        <>
          <h2 style={{ marginTop: 18 }}>What the model read out of it</h2>
          <div className="hit"><div className="text">{text.slice(0, 4000)}</div></div>
        </>
      )}

      <h2 style={{ marginTop: 18 }}>Versions</h2>
      <div className="excluded">
        {versions.map((v) => (
          <div className="item" key={v.version_id}>
            <span className="chip on">r{v.revision}</span>
            <span className="chip">{v.source}</span>
            <span className="empty">{v.content_chars} chars</span>
            {v.model_id && <span className="chip">{v.model_id}</span>}
            {v.tokens ? <span className="empty">{v.tokens} tokens</span> : null}
            <span className="empty">{new Date(v.created_at).toLocaleTimeString()}</span>
          </div>
        ))}
      </div>
      <p className="empty" style={{ marginTop: 10, marginBottom: 0 }}>
        Nothing is mutated in place. The upload is revision 1 and the transcript or description is
        a later one, each recording which model produced it — so “why does this say something
        different than last week” has an answer.
      </p>
    </section>
  );
}

function Staircase({ stair }: { stair: Stair }) {
  const rungs = [
    { key: "stored" as const, label: "stored", value: stair.total },
    { key: "searchable" as const, label: "searchable", value: stair.searchable + stair.enriched },
    { key: "enriched" as const, label: "enriched", value: stair.enriched },
  ];
  const max = Math.max(stair.total, 1);
  const behind = stair.total - stair.enriched;
  return (
    <>
      <div className="stair">
        {rungs.map((rung) => (
          <div className="stair-row" key={rung.key}>
            <span className="label">{rung.label}</span>
            <span className="bar">
              <span
                className={rung.key}
                style={{ width: `${(rung.value / max) * 100}%` }}
              />
            </span>
            <span className="count">{rung.value}</span>
          </div>
        ))}
      </div>
      <p className="empty" style={{ marginTop: 12, marginBottom: 0 }}>
        {stair.total === 0
          ? "Nothing written yet."
          : behind === 0
            ? `All ${stair.total} records are enriched.`
            : `${behind} of ${stair.total} still climbing. An item is findable at “searchable”; ` +
              "the envelope arrives at “enriched”."}
        {stair.awaiting_fetch > 0 &&
          ` ${stair.awaiting_fetch} awaiting a fetch worker that this slice does not ship.`}
      </p>
    </>
  );
}

function TracePanels({ trace }: { trace: Trace }) {
  const corpus = trace.corpus;
  return (
    <>
      <section className="panel">
        <h2>Retrieved — ranked, with scores</h2>
        {corpus && (
          <p className="empty" style={{ marginTop: -4 }}>
            Answering over <strong>{corpus.enriched} enriched</strong> of {corpus.total} records
            {corpus.stored > 0 && ` — ${corpus.stored} not searchable yet`}.
          </p>
        )}
        {trace.results.length === 0 ? (
          <p className="empty">
            Nothing matched. That is a result, not an error — check the excluded panel below for
            whether anything was eligible to match at all.
          </p>
        ) : (
          trace.results.map((hit, index) => (
            <div className="hit" key={hit.chunk_id}>
              <div className="meta">
                <span className="chip">#{index + 1}</span>
                <span className="chip">score {hit.score.toFixed(4)}</span>
                {["vec", "lex"].map((arm) => (
                  <span key={arm} className={`chip${hit.matched_by.includes(arm) ? " on" : ""}`}>
                    {arm === "vec" ? "vector" : "lexical"}
                  </span>
                ))}
                <span className="chip">{hit.state}</span>
                <span className="chip">
                  chars {hit.span_start}–{hit.span_end}
                </span>
              </div>
              <div className="text">{hit.text}</div>
              <p className="provenance" style={{ marginBottom: 0, marginTop: 8 }}>
                {hit.data_id}
              </p>
            </div>
          ))
        )}
      </section>

      <section className="panel">
        <h2>Considered but not returned</h2>
        {trace.excluded.length === 0 ? (
          <p className="empty">Nothing was excluded.</p>
        ) : (
          <div className="excluded">
            {trace.excluded.map((item) => (
              <div className="item" key={`${item.data_id}-${item.reason}`}>
                <code>{item.data_id}</code>
                <span className="why">{REASON_TEXT[item.reason]}</span>
                {item.score !== null && <span className="empty">score {item.score.toFixed(4)}</span>}
              </div>
            ))}
          </div>
        )}
        <p className="empty" style={{ marginTop: 12, marginBottom: 0 }}>
          Records excluded by <strong>ACL are deliberately absent from this list</strong>. Reporting
          that something was hidden from you discloses that it exists, which is what the ACL is
          for — the predicate runs inside the query, so the count does not exist to be shown.
        </p>
      </section>

      <section className="panel">
        <h2>Provenance — so this run is reproducible</h2>
        <table className="kv">
          <tbody>
            <tr>
              <td>query id</td>
              <td><code>{trace.query_id}</code></td>
            </tr>
            <tr>
              <td>embedding model</td>
              <td><code>{trace.model_id}</code></td>
            </tr>
            <tr>
              <td>generator version</td>
              <td><code>{trace.generator_version ?? "—"}</code></td>
            </tr>
          </tbody>
        </table>
        <p className="empty" style={{ marginTop: 10, marginBottom: 0 }}>
          Retrieval only ever considers one vector space: the query filters on this model id, so a
          mixed index cannot silently rank incomparable neighbours against each other.
        </p>
      </section>
    </>
  );
}
