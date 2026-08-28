"use client";

/**
 * The console, five sections.
 *
 * The split follows what the system actually does rather than what a CRUD app
 * would suggest: adding data and updating it are the same endpoint but very
 * different questions ("did it land?" versus "what changed and who changed
 * it?"), and audit and memory are the two views that make the rest
 * accountable.
 */

import { useCallback, useEffect, useState } from "react";

import Capture, { humanBytes } from "./Capture";
import {
  AuditTrail,
  Item,
  Memory,
  MemoryMember,
  Membership,
  Stair,
  Trace,
  Version,
  call,
} from "@/lib/types";

type Section = "add" | "update" | "search" | "audit" | "memory";

const SECTIONS: { key: Section; label: string; hint: string }[] = [
  { key: "add", label: "Add data", hint: "write text, upload or record" },
  { key: "update", label: "Update data", hint: "re-write a key, see revisions" },
  { key: "search", label: "Read / search", hint: "retrieve, with the trace" },
  { key: "audit", label: "Audit", hint: "who read and wrote what" },
  { key: "memory", label: "Memory", hint: "typed containers with a lifecycle" },
];

export default function Console({
  projectId,
  producerId,
}: {
  projectId: string;
  producerId: string;
}) {
  const [section, setSection] = useState<Section>("add");
  const [stair, setStair] = useState<Stair | null>(null);
  const [error, setError] = useState<string | null>(null);

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

  return (
    <div className="shell">
      <nav className="side">
        <div className="brand">mem-dog</div>
        {SECTIONS.map((s) => (
          <button
            key={s.key}
            className={`navitem${section === s.key ? " active" : ""}`}
            onClick={() => setSection(s.key)}
          >
            <span className="navlabel">{s.label}</span>
            <span className="navhint">{s.hint}</span>
          </button>
        ))}
        <div className="sidefoot">
          {stair && <Staircase stair={stair} compact />}
        </div>
      </nav>

      <main className="content">
        {error && <p className="err">{error}</p>}
        {section === "add" && (
          <AddData projectId={projectId} producerId={producerId} onChange={refresh} stair={stair} />
        )}
        {section === "update" && <UpdateData producerId={producerId} onChange={refresh} />}
        {section === "search" && <ReadSearch projectId={projectId} />}
        {section === "audit" && <Audit projectId={projectId} />}
        {section === "memory" && <MemorySection projectId={projectId} />}
      </main>
    </div>
  );
}

/* ------------------------------------------------------------------ shared */

function Staircase({ stair, compact }: { stair: Stair; compact?: boolean }) {
  const rungs = [
    { key: "stored" as const, label: "stored", value: stair.total },
    { key: "searchable" as const, label: "searchable", value: stair.searchable + stair.enriched },
    { key: "enriched" as const, label: "enriched", value: stair.enriched },
  ];
  const max = Math.max(stair.total, 1);
  return (
    <div className={compact ? "stair compact" : "stair"}>
      {rungs.map((r) => (
        <div className="stair-row" key={r.key}>
          <span className="label">{r.label}</span>
          <span className="bar">
            <span className={r.key} style={{ width: `${(r.value / max) * 100}%` }} />
          </span>
          <span className="count">{r.value}</span>
        </div>
      ))}
    </div>
  );
}

function ItemDetail({ item, versions }: { item: Item; versions: Version[] }) {
  const text = item.extracted_text ?? item.content_text ?? "";
  const stuck = item.parse_status !== null && item.parse_status !== "parsed";
  return (
    <>
      <table className="kv">
        <tbody>
          <tr><td>id</td><td><code>{item.data_id}</code></td></tr>
          <tr><td>state</td><td><span className={`chip ${item.state}`}>{item.state}</span></td></tr>
          <tr>
            <td>sniffed type</td>
            <td><code>{item.mime_type ?? "—"}</code> → {item.data_type ?? "—"}</td>
          </tr>
          {item.size_bytes ? (
            <tr><td>size</td><td>{humanBytes(item.size_bytes)}</td></tr>
          ) : null}
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
          untouched, so this is fixable without re-uploading.
        </div>
      )}

      {text && (
        <>
          <h3>Text the pipeline read out of it</h3>
          <div className="hit"><div className="text">{text.slice(0, 4000)}</div></div>
        </>
      )}

      {versions.length > 0 && (
        <>
          <h3>Versions</h3>
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
          <p className="empty">
            Nothing is mutated in place. The write is revision 1; a transcript or a re-parse is a
            later one, each recording which model produced it.
          </p>
        </>
      )}
    </>
  );
}

function useTracked() {
  const [item, setItem] = useState<Item | null>(null);
  const [versions, setVersions] = useState<Version[]>([]);

  const track = useCallback(async (dataId: string, onTick?: () => Promise<void>) => {
    for (let attempt = 0; attempt < 60; attempt += 1) {
      const current = await call<Item>(`api/v1/data/${dataId}`);
      setItem(current);
      setVersions((await call<{ versions: Version[] }>(`api/v1/data/${dataId}/versions`)).versions);
      await onTick?.();
      const settled =
        current.state === "enriched" ||
        (current.parse_status !== null && current.parse_status !== "parsed");
      if (settled) return;
      await new Promise((r) => setTimeout(r, 2000));
    }
  }, []);

  return { item, versions, track, setItem };
}

/* --------------------------------------------------------------- 1. add */

function AddData({
  projectId,
  producerId,
  onChange,
  stair,
}: {
  projectId: string;
  producerId: string;
  onChange: () => Promise<void>;
  stair: Stair | null;
}) {
  const [text, setText] = useState(
    "The checkout service returned 502s for eleven minutes after a bad deploy.\n\nRollback completed at 14:02 UTC and error rates recovered.",
  );
  const [memoryType, setMemoryType] = useState("");
  const [memoryKey, setMemoryKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { item, versions, track } = useTracked();

  async function submit(content: Record<string, unknown>, externalId: string, label: string) {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const memory =
        memoryType || memoryKey
          ? { type: memoryType || "default", key: memoryKey || null }
          : undefined;
      const response = await call<{ results: { data_id: string; memories: string[] }[] }>(
        "api/v1/write",
        { producer_id: producerId, items: [{ external_id: externalId, content, memory }] },
      );
      const first = response.results[0];
      setNote(
        `${label} committed as ${first.data_id}, mapped into ${first.memories.length} memory(ies). ` +
          "It is durable now; interpretation is queued.",
      );
      await track(first.data_id, onChange);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1>Add data</h1>
      <p className="lede">
        Text, a file, or something recorded here and now. All three take the identical write path —
        there is no sandbox shortcut, which is the only reason what you see here tells you anything.
      </p>

      <section className="panel">
        <h2>Paste text</h2>
        <textarea value={text} onChange={(e) => setText(e.target.value)} />
        <div className="row end" style={{ marginTop: 10 }}>
          <button
            onClick={() =>
              submit({ kind: "inline", text }, `text-${Date.now()}`, "Text")
            }
            disabled={busy || !text.trim()}
          >
            {busy ? "Writing…" : "Write item"}
          </button>
        </div>
      </section>

      <section className="panel">
        <h2>Upload or record</h2>
        <div className="notice">
          <strong>Media is interpreted by a model.</strong> Audio and video are transcribed, images
          described and their text transcribed. That costs tokens per file, recorded per revision.
        </div>
        <Capture
          busy={busy}
          onSubmit={(name, mime, base64, size) =>
            submit({ kind: "inline", bytes_b64: base64 }, name, `${name} (${humanBytes(size)})`)
          }
        />
      </section>

      <section className="panel">
        <h2>Memory routing (optional)</h2>
        <div className="row">
          <input
            type="text"
            placeholder="type — conversation, session, factual…"
            value={memoryType}
            onChange={(e) => setMemoryType(e.target.value)}
          />
          <input
            type="text"
            placeholder="key — e.g. thread-8841"
            value={memoryKey}
            onChange={(e) => setMemoryKey(e.target.value)}
          />
        </div>
        <p className="empty">
          Leave both blank and the item lands in your <code>default</code> memory — nothing is
          orphaned. Reuse a key and writes collect into one memory, with no session state anywhere.
        </p>
      </section>

      {note && <p className="empty">{note}</p>}
      {error && <p className="err">{error}</p>}
      {item && (
        <section className="panel">
          <h2>What happened to it</h2>
          <ItemDetail item={item} versions={versions} />
        </section>
      )}
      {stair && (
        <section className="panel">
          <h2>Readiness staircase</h2>
          <Staircase stair={stair} />
        </section>
      )}
    </>
  );
}

/* ------------------------------------------------------------ 2. update */

function UpdateData({
  producerId,
  onChange,
}: {
  producerId: string;
  onChange: () => Promise<void>;
}) {
  const [externalId, setExternalId] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lookup, setLookup] = useState("");
  const { item, versions, track, setItem } = useTracked();

  async function rewrite() {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const response = await call<{ results: { data_id: string; status: string }[] }>(
        "api/v1/write",
        {
          producer_id: producerId,
          items: [{ external_id: externalId, content: { kind: "inline", text } }],
        },
      );
      const first = response.results[0];
      setNote(
        first.status === "updated"
          ? `Updated ${first.data_id} in place — same id, new revision, stale chunks dropped.`
          : `No item had that key, so this created ${first.data_id}.`,
      );
      await track(first.data_id, onChange);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function load() {
    setError(null);
    try {
      const current = await call<Item>(`api/v1/data/${lookup.trim()}`);
      setItem(current);
      await track(current.data_id);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <>
      <h1>Update data</h1>
      <p className="lede">
        There is no update endpoint. A write with an <code>external_id</code> that already exists
        updates that item — the caller&rsquo;s own natural key is what dedupes within a source, so
        re-crawling the same record updates rather than duplicates.
      </p>

      <section className="panel">
        <h2>Re-write a key</h2>
        <div className="row">
          <input
            type="text"
            placeholder="external_id to write"
            value={externalId}
            onChange={(e) => setExternalId(e.target.value)}
            style={{ flex: 1 }}
          />
        </div>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="new content for that key"
          style={{ marginTop: 10 }}
        />
        <div className="row end" style={{ marginTop: 10 }}>
          <button onClick={rewrite} disabled={busy || !externalId.trim() || !text.trim()}>
            {busy ? "Writing…" : "Write"}
          </button>
        </div>
        <p className="empty">
          Updating drops the derived rows and rebuilds them. That is what stops a stale vector
          outliving the text it was made from.
        </p>
      </section>

      <section className="panel">
        <h2>Inspect an item</h2>
        <div className="row">
          <input
            type="text"
            placeholder="data_01…"
            value={lookup}
            onChange={(e) => setLookup(e.target.value)}
            style={{ flex: 1 }}
          />
          <button onClick={load} disabled={!lookup.trim()}>Load</button>
        </div>
      </section>

      {note && <p className="empty">{note}</p>}
      {error && <p className="err">{error}</p>}
      {item && (
        <section className="panel">
          <h2>Item and revisions</h2>
          <ItemDetail item={item} versions={versions} />
        </section>
      )}
    </>
  );
}

/* ------------------------------------------------------------ 3. search */

function ReadSearch({ projectId }: { projectId: string }) {
  const [query, setQuery] = useState("rollback recovered error rates");
  const [trace, setTrace] = useState<Trace | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function search() {
    setBusy(true);
    setError(null);
    try {
      setTrace(
        await call<Trace>("api/v1/retrieve", { query, filter: { project_id: projectId } }),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const REASONS: Record<string, string> = {
    threshold: "retrieved, ranked below the cut",
    not_yet_enriched: "not searchable yet — it could not have matched",
  };

  return (
    <>
      <h1>Read / search</h1>
      <p className="lede">
        The trace is the output, not the answer. An answer is a lagging indicator of ingestion
        quality, filtered through a model that is good at sounding right regardless.
      </p>

      <section className="panel">
        <div className="row">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void search()}
            style={{ flex: 1 }}
          />
          <button onClick={search} disabled={busy || !query.trim()}>
            {busy ? "Searching…" : "Retrieve"}
          </button>
        </div>
        {error && <p className="err">{error}</p>}
      </section>

      {trace && (
        <>
          <section className="panel">
            <h2>Retrieved — ranked, with scores</h2>
            {trace.corpus && (
              <p className="empty" style={{ marginTop: -4 }}>
                Answering over <strong>{trace.corpus.enriched} enriched</strong> of{" "}
                {trace.corpus.total} records
                {trace.corpus.stored > 0 && ` — ${trace.corpus.stored} not searchable yet`}.
              </p>
            )}
            {trace.results.length === 0 ? (
              <p className="empty">
                Nothing matched. Check the panel below for whether anything was eligible to match.
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
                    <span className={`chip ${hit.state}`}>{hit.state}</span>
                    <span className="chip">chars {hit.span_start}–{hit.span_end}</span>
                  </div>
                  <div className="text">{hit.text}</div>
                  <p className="provenance">{hit.data_id}</p>
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
                {trace.excluded.map((e) => (
                  <div className="item" key={`${e.data_id}-${e.reason}`}>
                    <code>{e.data_id}</code>
                    <span className="why">{REASONS[e.reason]}</span>
                    {e.score !== null && <span className="empty">score {e.score.toFixed(4)}</span>}
                  </div>
                ))}
              </div>
            )}
            <p className="empty">
              Records excluded by <strong>ACL are deliberately absent</strong>. Saying something was
              hidden from you discloses that it exists — the predicate runs inside the query, so the
              count does not exist to be shown.
            </p>
          </section>

          <section className="panel">
            <h2>Provenance</h2>
            <table className="kv">
              <tbody>
                <tr><td>query id</td><td><code>{trace.query_id}</code></td></tr>
                <tr><td>embedding model</td><td><code>{trace.model_id}</code></td></tr>
                <tr><td>generator</td><td><code>{trace.generator_version ?? "—"}</code></td></tr>
              </tbody>
            </table>
          </section>
        </>
      )}
    </>
  );
}

/* ------------------------------------------------------------- 4. audit */

function Audit({ projectId }: { projectId: string }) {
  const [trail, setTrail] = useState<AuditTrail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setTrail(await call<AuditTrail>(`api/v1/audit?project_id=${projectId}&limit=100`));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <>
      <h1>Audit</h1>
      <p className="lede">
        Two stores, because they are two different things. Writes are low-volume and never purged.
        Reads are written on every access, never updated, and must survive the deletion of what they
        describe — which is why they carry no foreign keys.
      </p>
      {error && <p className="err">{error}</p>}

      <section className="panel">
        <h2>Writes — everything that is not a read</h2>
        {!trail || trail.writes.length === 0 ? (
          <p className="empty">Nothing yet.</p>
        ) : (
          <div className="excluded">
            {trail.writes.map((w) => (
              <div className="item" key={w.id}>
                <span className="chip on">{w.action}</span>
                <code>{w.target_id ?? "—"}</code>
                {typeof w.detail?.access_level === "string" && (
                  <span className="chip">{w.detail.access_level}</span>
                )}
                <span className="empty">{new Date(w.at).toLocaleTimeString()}</span>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="panel">
        <h2>Reads — append-only access log</h2>
        {!trail || trail.reads.length === 0 ? (
          <p className="empty">Nothing yet.</p>
        ) : (
          <div className="excluded">
            {trail.reads.map((r) => (
              <div className="item" key={r.id}>
                <span className="chip">{r.action}</span>
                <code>{r.data_id ?? r.query_id ?? "—"}</code>
                <span className="empty">{new Date(r.at).toLocaleTimeString()}</span>
              </div>
            ))}
          </div>
        )}
        <p className="empty">
          Every retrieval writes one row per cited record, so &ldquo;who read this?&rdquo; has an
          answer that survives the record itself being deleted.
        </p>
      </section>
    </>
  );
}

/* ------------------------------------------------------------ 5. memory */

function MemorySection({ projectId }: { projectId: string }) {
  const [memories, setMemories] = useState<Memory[]>([]);
  const [selected, setSelected] = useState<Memory | null>(null);
  const [members, setMembers] = useState<MemoryMember[]>([]);
  const [memberships, setMemberships] = useState<Membership[] | null>(null);
  const [expiry, setExpiry] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setMemories((await call<{ memories: Memory[] }>(`api/v1/projects/${projectId}/memories`)).memories);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function open(memory: Memory) {
    setSelected(memory);
    setMemberships(null);
    try {
      setMembers(
        (await call<{ members: MemoryMember[] }>(`api/v1/memories/${memory.memory_id}/members`)).members,
      );
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function showItemMemories(dataId: string) {
    const state = await call<{ memberships: Membership[]; effective_expiry: string | null }>(
      `api/v1/data/${dataId}/memories`,
    );
    setMemberships(state.memberships);
    setExpiry(state.effective_expiry);
  }

  const ttl = (seconds: number | null) =>
    seconds === null ? "never expires" : seconds >= 86400
      ? `${Math.round(seconds / 86400)}d TTL`
      : `${Math.round(seconds / 3600)}h TTL`;

  return (
    <>
      <h1>Memory</h1>
      <p className="lede">
        A memory is a <strong>lifecycle</strong> container — how long does this matter? A case is a
        subject — what is this about? A conversation expires; a patient does not.
      </p>
      {error && <p className="err">{error}</p>}

      <section className="panel">
        <h2>Memories in this project</h2>
        {memories.length === 0 ? (
          <p className="empty">None visible.</p>
        ) : (
          <div className="excluded">
            {memories.map((m) => (
              <div className="item memrow" key={m.memory_id} onClick={() => void open(m)}>
                <span className="chip on">{m.type}</span>
                <code>{m.memory_key ?? m.memory_id}</code>
                <span className="empty">{m.members} member{m.members === 1 ? "" : "s"}</span>
                <span className="chip">{ttl(m.ttl_seconds)}</span>
                {m.on_expiry && <span className="chip">{m.on_expiry}</span>}
              </div>
            ))}
          </div>
        )}
        <p className="empty">
          A memory with no members you can see is not listed at all — a count of zero would still
          disclose that the container exists, and a key is often meaningful on its own.
        </p>
      </section>

      {selected && (
        <section className="panel">
          <h2>{selected.type} · {selected.memory_key ?? selected.memory_id}</h2>
          {members.length === 0 ? (
            <p className="empty">No members you can see.</p>
          ) : (
            members.map((m) => (
              <div className="hit" key={m.data_id}>
                <div className="meta">
                  <code>{m.data_id}</code>
                  <span className={`chip ${m.state}`}>{m.state}</span>
                  <span className="chip">added {m.added_by}</span>
                  <button className="linkish" onClick={() => void showItemMemories(m.data_id)}>
                    where else does this live?
                  </button>
                </div>
                {m.preview && <div className="text">{m.preview}</div>}
              </div>
            ))
          )}
          <p className="empty">
            <code>added_by</code> distinguishes a rule putting an item here from a person doing it —
            different facts with different trust.
          </p>
        </section>
      )}

      {memberships && (
        <section className="panel">
          <h2>Both directions of the mapping</h2>
          <div className="excluded">
            {memberships.map((m) => (
              <div className="item" key={m.memory_id}>
                <span className="chip on">{m.type}</span>
                <code>{m.memory_key ?? m.memory_id}</code>
                <span className="chip">{m.added_by}</span>
                <span className="empty">
                  {m.expires_at ? `expires ${new Date(m.expires_at).toLocaleString()}` : "never expires"}
                </span>
              </div>
            ))}
          </div>
          <p className="empty">
            Effective expiry: <strong>{expiry ? new Date(expiry).toLocaleString() : "never"}</strong>.
            It is the <em>maximum</em> TTL across memberships and it is computed on read, never
            stored — any stored answer is wrong the moment someone adds or removes a member. Taking
            the earliest instead would delete data a permanent memory still depends on.
          </p>
        </section>
      )}
    </>
  );
}
