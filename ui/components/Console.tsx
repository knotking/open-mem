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
import ThemeToggle from "./ThemeToggle";
import {
  ARMS,
  ArmKey,
  AuditTrail,
  Item,
  Membership,
  Memory,
  MemoryMember,
  MemoryType,
  Stair,
  Trace,
  Version,
  call,
  describeTtl,
} from "@/lib/types";

type Section =
  | "overview"
  | "add" | "update" | "search" | "ask" | "inbound" | "crawlers" | "mcp"
  | "memory" | "cases" | "entities"
  | "audit" | "sharing" | "deletion"
  | "settings" | "models" | "prompts"
  | "projects" | "keys" | "producers" | "platform";

/**
 * Grouped by concern rather than by endpoint.
 *
 * The split that matters is Organize versus Governance: memories and cases
 * answer *how is my data arranged*; audit, sharing and deletion answer *who
 * touched it and can I prove it*. Mixing them buries the compliance surface
 * inside a browsing surface.
 */
const GROUPS: { title: string; items: { key: Section; label: string; hint: string }[] }[] = [
  {
    title: "Data",
    items: [
      { key: "overview", label: "Overview", hint: "is this working?" },
      { key: "add", label: "Add data", hint: "paste, upload or record" },
      { key: "update", label: "Update data", hint: "re-write a key, see revisions" },
      { key: "search", label: "Search", hint: "retrieve, with the trace" },
      { key: "ask", label: "Chat", hint: "ask your data, with citations" },
      { key: "inbound", label: "Inbound", hint: "webhooks providers post to" },
      { key: "crawlers", label: "Crawlers", hint: "pull what won't push" },
    ],
  },
  {
    title: "Organize",
    items: [
      { key: "memory", label: "Memories", hint: "lifecycle containers" },
      { key: "cases", label: "Cases", hint: "subjects and timelines" },
      { key: "entities", label: "Entities", hint: "who and what, with evidence" },
    ],
  },
  {
    title: "Governance",
    items: [
      { key: "audit", label: "Audit", hint: "who read and wrote what" },
      { key: "sharing", label: "Sharing", hint: "what is public, and revoke" },
      { key: "deletion", label: "Deletion", hint: "dry-run, then erase" },
    ],
  },
  {
    title: "Configuration",
    items: [
      { key: "settings", label: "Settings", hint: "precedence and locks" },
      { key: "models", label: "Models", hint: "assignment per purpose" },
      { key: "prompts", label: "Prompts", hint: "per data type, test first" },
      { key: "mcp", label: "MCP", hint: "use this corpus from Claude" },
    ],
  },
  {
    title: "Admin",
    items: [
      { key: "projects", label: "Projects & members", hint: "org structure" },
      { key: "keys", label: "API keys", hint: "issue and revoke" },
      { key: "producers", label: "Producers", hint: "freshness and status" },
      { key: "platform", label: "Platform", hint: "operational shape only" },
    ],
  },
];

export default function Console({
  projectId,
  producerId,
  me,
  authEnabled,
}: {
  projectId: string;
  producerId: string;
  me: { email?: string; role?: string } | null;
  authEnabled: boolean;
}) {
  const [section, setSection] = useState<Section>("overview");
  // Only the group you are working in is expanded. Fifteen items visible at
  // once is a list to scan; four groups with one open is a place to be.
  const [openGroup, setOpenGroup] = useState<string>("Data");
  // Handoffs between Search and Entities. A search result explains itself by
  // naming the entity it was reached through; the entity panel hands a name
  // back. Held here because the two panels are siblings and neither owns the
  // other.
  const [focusEntity, setFocusEntity] = useState<string | null>(null);
  const [seededQuery, setSeededQuery] = useState<string | null>(null);
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
        <div className="brand">
          <span className="dot" aria-hidden="true" />
          mem-dog
        </div>
        <div className="navscroll">
          {GROUPS.map((group) => {
            const open = openGroup === group.title;
            const current = group.items.some((i) => i.key === section);
            return (
              <div className="navgroup" key={group.title}>
                <button
                  className={`navtitle${current ? " current" : ""}`}
                  aria-expanded={open}
                  onClick={() => setOpenGroup(open ? "" : group.title)}
                >
                  <span className={`caret${open ? " open" : ""}`} aria-hidden="true">
                    ›
                  </span>
                  {group.title}
                  {!open && current && <span className="here" aria-hidden="true" />}
                </button>
                {open &&
                  group.items.map((item) => (
                    <button
                      key={item.key}
                      className={`navitem${section === item.key ? " active" : ""}`}
                      onClick={() => setSection(item.key)}
                    >
                      <span className="navlabel">{item.label}</span>
                      <span className="navhint">{item.hint}</span>
                    </button>
                  ))}
              </div>
            );
          })}
        </div>
        <div className="sidefoot">
          {stair && <Staircase stair={stair} compact />}
          <div className="row" style={{ marginTop: 12, justifyContent: "space-between" }}>
            <ThemeToggle />
            {authEnabled && (
              <button
                className="linkish"
                onClick={async () => {
                  await fetch("/api/auth/logout", { method: "POST" });
                  window.location.reload();
                }}
              >
                Sign out
              </button>
            )}
          </div>
          {me?.email && (
            <p className="empty" style={{ marginTop: 6, marginBottom: 0 }}>
              {me.email} · {me.role ?? "member"}
            </p>
          )}
        </div>
      </nav>

      <main className="content">
        {error && <p className="err">{error}</p>}
        {section === "overview" && <Overview projectId={projectId} me={me} />}
        {section === "add" && (
          <AddData projectId={projectId} producerId={producerId} onChange={refresh} stair={stair} />
        )}
        {section === "update" && (
          <UpdateData projectId={projectId} producerId={producerId} onChange={refresh} />
        )}
        {section === "search" && (
          <ReadSearch
            projectId={projectId}
            seeded={seededQuery}
            onOpenEntity={(entityId) => {
              setFocusEntity(entityId);
              setSection("entities");
              setOpenGroup("Organize");
            }}
          />
        )}
        {section === "ask" && <AskSection projectId={projectId} />}
        {section === "inbound" && <InboundSection projectId={projectId} />}
        {section === "crawlers" && <CrawlersSection projectId={projectId} />}
        {section === "audit" && <Audit projectId={projectId} />}
        {section === "memory" && <MemorySection projectId={projectId} />}
        {section === "cases" && <CasesSection projectId={projectId} />}
        {section === "entities" && (
          <EntitiesSection
            projectId={projectId}
            focus={focusEntity}
            onSearchFor={(name) => {
              setSeededQuery(name);
              setSection("search");
              setOpenGroup("Data");
            }}
          />
        )}
        {section === "sharing" && <SharingSection />}
        {section === "deletion" && <DeletionSection projectId={projectId} onChange={refresh} />}
        {section === "settings" && <SettingsSection projectId={projectId} />}
        {section === "models" && <ModelsSection />}
        {section === "prompts" && <PromptsSection projectId={projectId} />}
        {section === "mcp" && <McpSection projectId={projectId} />}
        {section === "projects" && <ProjectsSection />}
        {section === "keys" && <KeysSection />}
        {section === "producers" && <ProducersSection />}
        {section === "platform" && <PlatformSection />}
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

      {item.storage_ref && (
        <>
          <h3>The bytes we stored</h3>
          <StoredMedia item={item} />
        </>
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

/**
 * The original bytes, played back from object storage.
 *
 * Being able to see what was actually stored is most of what makes a memory
 * layer trustworthy -- an item you cannot open is one you have to take on
 * faith. Served through the proxy so the ACL still applies and the read is
 * audited, rather than by handing the browser a signed URL that outlives the
 * check.
 */
function StoredMedia({ item }: { item: Item }) {
  const src = `/api/proxy/api/v1/data/${item.data_id}/content`;
  const mime = item.mime_type ?? "";

  if (mime.startsWith("image/")) {
    return <img className="preview" src={src} alt={item.external_id ?? "stored image"} />;
  }
  if (mime.startsWith("audio/")) {
    return <audio className="preview" src={src} controls preload="metadata" />;
  }
  if (mime.startsWith("video/")) {
    return <video className="preview" src={src} controls preload="metadata" />;
  }
  return (
    <p className="empty">
      <a href={src} target="_blank" rel="noreferrer">
        Open the stored file
      </a>{" "}
      — {mime || "unknown type"}
      {item.size_bytes ? `, ${humanBytes(item.size_bytes)}` : ""}. The browser cannot render this
      type inline.
    </p>
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
  projectId,
  producerId,
  onChange,
}: {
  projectId: string;
  producerId: string;
  onChange: () => Promise<void>;
}) {
  const [rows, setRows] = useState<Item[]>([]);
  const [selected, setSelected] = useState<Item | null>(null);
  const [versions, setVersions] = useState<Version[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showing, setShowing] = useState<Version | null>(null);

  const load = useCallback(async () => {
    try {
      const page = await call<{ items: Item[] }>(`api/v1/projects/${projectId}/data?limit=50`);
      setRows(page.items);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function open(dataId: string) {
    setError(null);
    setNote(null);
    setShowing(null);
    try {
      const item = await call<Item>(`api/v1/data/${dataId}`);
      setSelected(item);
      setDraft(item.content_text ?? item.extracted_text ?? "");
      setVersions((await call<{ versions: Version[] }>(`api/v1/data/${dataId}/versions`)).versions);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function save() {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      // The same write endpoint: an external_id that already exists updates in
      // place. There is no separate update path to drift from the write path.
      const response = await call<{ results: { data_id: string; status: string }[] }>(
        "api/v1/write",
        {
          producer_id: producerId,
          items: [
            {
              external_id: selected.external_id,
              content: { kind: "inline", text: draft },
            },
          ],
        },
      );
      const first = response.results[0];
      setNote(
        first.status === "updated"
          ? "Saved as a new revision. The previous text is still readable below."
          : "That key did not exist, so this created a new item.",
      );
      await open(first.data_id);
      await load();
      await onChange();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const dirty = selected !== null && draft !== (selected.content_text ?? selected.extracted_text ?? "");

  return (
    <>
      <h1>Update data</h1>
      <p className="lede">
        There is no update endpoint. A write whose <code>external_id</code> already exists updates
        that item — the caller&rsquo;s natural key is what dedupes within a source, so re-crawling
        the same record updates rather than duplicates. Every save appends a revision; nothing is
        mutated in place.
      </p>
      {error && <p className="err">{error}</p>}

      <section className="panel">
        <h2>Items in this project</h2>
        {rows.length === 0 ? (
          <p className="empty">Nothing written yet.</p>
        ) : (
          <div className="excluded">
            {rows.map((r) => (
              <div
                className={`item memrow${selected?.data_id === r.data_id ? " chosen" : ""}`}
                key={r.data_id}
                onClick={() => void open(r.data_id)}
              >
                <span className={`chip ${r.state}`}>{r.state}</span>
                <code>{r.external_id ?? r.data_id}</code>
                <span className="empty">{r.data_type ?? r.mime_type ?? "—"}</span>
                <span className="chip">r{String((r as unknown as { revisions: number }).revisions)}</span>
                <span className="empty preview">
                  {(r as unknown as { preview: string | null }).preview ?? ""}
                </span>
              </div>
            ))}
          </div>
        )}
      </section>

      {selected && (
        <section className="panel">
          <h2>{selected.external_id ?? selected.data_id}</h2>
          <ItemDetail item={selected} versions={[]} />

          {selected.content_text !== null || selected.extracted_text !== null ? (
            <>
              <h3>Edit</h3>
              {selected.content_text === null && (
                <div className="notice">
                  <strong>This text was extracted, not written.</strong> Saving stores it as
                  caller-supplied content, which means a later re-parse will no longer replace it.
                </div>
              )}
              <textarea value={draft} onChange={(e) => setDraft(e.target.value)} />
              <div className="row end" style={{ marginTop: 10 }}>
                <button onClick={save} disabled={busy || !dirty}>
                  {busy ? "Saving…" : dirty ? "Save as new revision" : "No changes"}
                </button>
              </div>
              {note && <p className="empty">{note}</p>}
            </>
          ) : (
            <p className="empty">
              This item has no text yet — it is {selected.parse_status ?? "awaiting processing"}.
            </p>
          )}

          <h3>Revisions</h3>
          <div className="excluded">
            {versions.map((v) => (
              <div
                className={`item memrow${showing?.version_id === v.version_id ? " chosen" : ""}`}
                key={v.version_id}
                onClick={() => setShowing(showing?.version_id === v.version_id ? null : v)}
              >
                <span className="chip on">r{v.revision}</span>
                <span className="chip">{v.source}</span>
                <span className="empty">{v.content_chars} chars</span>
                {v.model_id && <span className="chip">{v.model_id}</span>}
                <span className="empty">{new Date(v.created_at).toLocaleString()}</span>
              </div>
            ))}
          </div>
          {showing && (
            <div className="hit" style={{ marginTop: 10 }}>
              <div className="meta">
                <span className="chip on">revision {showing.revision}</span>
                <span className="chip">{showing.source}</span>
              </div>
              <div className="text">{showing.preview ?? "(no text in this revision)"}</div>
            </div>
          )}
          <p className="empty">
            Click a revision to read it. &ldquo;Why does this say something different than last
            week?&rdquo; is answerable because the old text is still here.
          </p>
        </section>
      )}
    </>
  );
}

/* --------------------------------------------------------- 3. entities */

type Entity = {
  entity_id: string;
  type: string;
  display_name: string;
  identifiers: string[];
  visible_mentions: number;
};

type GraphNode = { entity_id: string; display_name: string; type: string; depth: number };
type GraphEdge = {
  subject_id: string; predicate: string; object_id: string;
  evidence: number; source_data_ids: string[]; confidence: number;
};
type GraphView = {
  root: GraphNode; nodes: GraphNode[]; edges: GraphEdge[]; truncated: boolean;
};
type CoMention = {
  entity_id: string; display_name: string; type: string; shared_records: number;
};

type EntityDetail = Entity & {
  mentions: { data_id: string; surface: string; resolved_by: string;
              external_id: string; state: string; data_type: string | null }[];
  visible_mention_count: number;
};

const ENTITY_TYPES = ["person", "organization", "location", "product",
                      "event", "topic", "other"];

function EntitiesSection({
  projectId,
  focus,
  onSearchFor,
}: {
  projectId: string;
  // An entity the search trace linked through to. Opened on arrival, so the
  // hop from "why is this result here?" to "what else is connected?" is one
  // click rather than a name to remember and re-find in a list.
  focus?: string | null;
  onSearchFor?: (name: string) => void;
}) {
  const [list, setList] = useState<Entity[]>([]);
  const [kind, setKind] = useState<string | null>(null);
  const [detail, setDetail] = useState<EntityDetail | null>(null);
  const [chosen, setChosen] = useState<string[]>([]);
  const [graph, setGraph] = useState<GraphView | null>(null);
  const [together, setTogether] = useState<CoMention[]>([]);
  const [depth, setDepth] = useState(1);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [lastMerge, setLastMerge] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const page = await call<{ entities: Entity[] }>(
        `api/v1/projects/${projectId}/entities${kind ? `?type=${kind}` : ""}`,
      );
      setList(page.entities);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [projectId, kind]);

  const inspect = useCallback(
    async (entityId: string) => {
      try {
        const [d, g, c] = await Promise.all([
          call<EntityDetail>(`api/v1/entities/${entityId}`, undefined, "GET"),
          call<GraphView>(
            `api/v1/entities/${entityId}/graph?depth=${depth}`, undefined, "GET"),
          call<{ co_mentions: CoMention[] }>(
            `api/v1/entities/${entityId}/co-mentions`, undefined, "GET"),
        ]);
        setDetail(d);
        setGraph(g);
        setTogether(c.co_mentions);
      } catch (e) {
        setError((e as Error).message);
      }
    },
    [depth],
  );

  useEffect(() => {
    if (focus) void inspect(focus);
  }, [focus, inspect]);

  useEffect(() => {
    void load();
  }, [load]);

  async function act(message: string, work: () => Promise<void>) {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      await work();
      setNote(message);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function toggle(id: string) {
    setChosen((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id].slice(-2),
    );
  }

  return (
    <>
      <h1>Organize / entities</h1>
      <p className="lede">
        The people, organizations and things your records name. Resolution is
        deliberately cautious: it joins on a shared email or an exact name and
        otherwise keeps them apart, because two nodes you can merge later beat one
        node that fused two people and cannot be separated.
      </p>

      <section className="panel">
        <div className="row">
          <button className={kind === null ? "" : "secondary"} onClick={() => setKind(null)}>
            All
          </button>
          {ENTITY_TYPES.map((t) => (
            <button key={t} className={kind === t ? "" : "secondary"} onClick={() => setKind(t)}>
              {t}
            </button>
          ))}
        </div>
        {note && <p className="ok">{note}</p>}
        {error && <p className="err">{error}</p>}
      </section>

      {chosen.length === 2 && (
        <section className="panel">
          <h2>Merge these two?</h2>
          <p className="empty">
            The second becomes the survivor. Nothing is destroyed — the merge can be undone,
            because &ldquo;these are the same person&rdquo; is a judgement and judgements are
            sometimes wrong.
          </p>
          <div className="row">
            <button
              disabled={busy}
              onClick={() =>
                act("Merged. You can undo this.", async () => {
                  const result = await call<{ merge_id: string }>("api/v1/entities/merge", {
                    source_id: chosen[0], target_id: chosen[1],
                  });
                  setLastMerge(result.merge_id);
                  setChosen([]);
                  setDetail(null);
                  await load();
                })
              }
            >
              Merge
            </button>
            <button className="secondary" onClick={() => setChosen([])}>Cancel</button>
          </div>
        </section>
      )}

      {lastMerge && (
        <section className="panel">
          <div className="row" style={{ alignItems: "center", gap: 12 }}>
            <button
              className="secondary"
              disabled={busy}
              onClick={() =>
                act("Merge undone.", async () => {
                  await call(`api/v1/entities/merges/${lastMerge}/undo`, {});
                  setLastMerge(null);
                  await load();
                })
              }
            >
              Undo that merge
            </button>
          </div>
        </section>
      )}

      <section className="panel">
        <h2>{list.length} entities</h2>
        {list.length === 0 ? (
          <p className="empty">
            Nothing yet. Entities are resolved when a record is enriched, so enrich something
            first.
          </p>
        ) : (
          list.map((entity) => (
            <div
              className="hit"
              key={entity.entity_id}
              style={{
                cursor: "pointer",
                borderColor: chosen.includes(entity.entity_id) ? "var(--accent)" : undefined,
              }}
              onClick={() => act("", () => inspect(entity.entity_id))}
            >
              <div className="meta">
                <span className="chip on">{entity.type}</span>
                <span className="chip">{entity.visible_mentions} record
                  {entity.visible_mentions === 1 ? "" : "s"}</span>
                {entity.identifiers?.map((id) => (
                  <span className="chip" key={id}>{id}</span>
                ))}
              </div>
              <div className="text">{entity.display_name}</div>
              <div className="row" style={{ marginTop: 6 }}>
                <button
                  className="secondary"
                  onClick={(e) => { e.stopPropagation(); toggle(entity.entity_id); }}
                >
                  {chosen.includes(entity.entity_id) ? "Selected" : "Select to merge"}
                </button>
              </div>
            </div>
          ))
        )}
      </section>

      {graph && (
        <section className="panel">
          <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
            <h2 style={{ margin: 0 }}>Connections</h2>
            <div className="row">
              {[1, 2, 3].map((d) => (
                <button
                  key={d}
                  className={depth === d ? "" : "secondary"}
                  disabled={busy}
                  onClick={() =>
                    act("", async () => {
                      setDepth(d);
                      setGraph(
                        await call<GraphView>(
                          `api/v1/entities/${graph.root.entity_id}/graph?depth=${d}`,
                          undefined, "GET"),
                      );
                    })
                  }
                >
                  {d} hop{d > 1 ? "s" : ""}
                </button>
              ))}
            </div>
          </div>

          {graph.edges.length === 0 ? (
            <p className="empty">
              No asserted relationships yet. Edges come from what a document actually stated —
              &ldquo;Priya works for Northwind&rdquo; — so they need an enrichment pass that read
              for them. Co-mentions below need nothing and work today.
            </p>
          ) : (
            <>
              <p className="empty" style={{ marginTop: 4 }}>
                Each edge names the records that assert it. One document saying something is a
                claim; several saying it independently is closer to a fact.
              </p>
              {graph.edges.map((edge, i) => {
                const name = (id: string) =>
                  graph.nodes.find((n) => n.entity_id === id)?.display_name ?? id;
                return (
                  <div className="hit" key={`${edge.subject_id}-${edge.predicate}-${i}`}>
                    <div className="meta">
                      <span className="chip on">{edge.predicate.replace(/_/g, " ")}</span>
                      <span className="chip">
                        {edge.evidence} record{edge.evidence === 1 ? "" : "s"} assert this
                      </span>
                    </div>
                    <div className="text">
                      {name(edge.subject_id)} <span className="edge-arrow">→</span>{" "}
                      {name(edge.object_id)}
                    </div>
                    <p className="provenance">{edge.source_data_ids.join(" · ")}</p>
                  </div>
                );
              })}
            </>
          )}

          {graph.nodes.length > 1 && (
            <>
              <h3>Reachable within {depth} hop{depth > 1 ? "s" : ""}</h3>
              <div className="row">
                {graph.nodes
                  .filter((n) => n.entity_id !== graph.root.entity_id)
                  .map((n) => (
                    <span className="chip" key={n.entity_id}>
                      {n.display_name} · {n.depth}
                    </span>
                  ))}
              </div>
              {graph.truncated && (
                <p className="empty">
                  Truncated at the result limit — there is more here than is shown.
                </p>
              )}
            </>
          )}
        </section>
      )}

      {together.length > 0 && (
        <section className="panel">
          <h2>Mentioned alongside</h2>
          <p className="empty" style={{ marginTop: 4 }}>
            Entities appearing in the same records. This is weak evidence — appearing together is
            not a relationship — but it needs no extraction, so it works before any model has read
            for relationships.
          </p>
          <div className="row">
            {together.map((c) => (
              <span className="chip" key={c.entity_id}>
                {c.display_name} · {c.shared_records} shared
              </span>
            ))}
          </div>
        </section>
      )}

      {detail && (
        <section className="panel">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h2 style={{ margin: 0 }}>{detail.display_name}</h2>
            {onSearchFor && (
              <button
                className="secondary"
                onClick={() => onSearchFor(detail.display_name)}
                title="Search with the graph arm, seeded from this entity"
              >
                Search from here
              </button>
            )}
          </div>
          <p className="empty">
            Every mention is kept with the record it came from and why it resolved here — so a
            wrong join is something you can see rather than something you inherit.
          </p>
          {detail.mentions.map((m, i) => (
            <div className="hit" key={`${m.data_id}-${i}`}>
              <div className="meta">
                <span className="chip">as &ldquo;{m.surface}&rdquo;</span>
                <span className="chip on">{m.resolved_by.replace("_", " ")}</span>
                <span className={`chip ${m.state}`}>{m.state}</span>
                {m.data_type && <span className="chip">{m.data_type}</span>}
              </div>
              <p className="provenance">{m.external_id} · {m.data_id}</p>
            </div>
          ))}
        </section>
      )}
    </>
  );
}

/* ---------------------------------------------------------- 4. crawlers */

type Crawler = {
  crawler_id: string;
  name: string;
  strategy: string;
  enabled: boolean;
  config_version: number;
  dry_run_version: number | null;
  dry_run_current: boolean;
  watermark: string | null;
  last_status: string | null;
  last_run_id: string | null;
  discovered: number | null;
  emitted: number | null;
  skipped: number | null;
  failed: number | null;
  seconds_since_last_success: number | null;
};

function staleness(seconds: number | null): { label: string; warn: boolean } | null {
  if (seconds === null) return null;
  const hours = seconds / 3600;
  if (hours < 1) return { label: `fresh — succeeded ${Math.round(seconds / 60)}m ago`, warn: false };
  if (hours < 48) return { label: `succeeded ${Math.round(hours)}h ago`, warn: false };
  return { label: `stale — last success ${Math.round(hours / 24)}d ago`, warn: true };
}

type RunResult = {
  run_id: string;
  status: string;
  discovered: number;
  emitted: number;
  skipped: number;
  failed: number;
  reason: string | null;
};

type TickResult = {
  started: string[];
  skipped: string[];
  reaped: number;
  runs: RunResult[];
  skipped_lock?: boolean;
};

type RunDetail = RunResult & {
  mode: string;
  sample: { external_id: string; url: string | null;
            payload: { title: string | null; preview: string | null } }[];
  errors: { external_id: string | null; reason: string }[];
};

const PRESETS: Record<string, { label: string; blurb: string; build: (v: string) => object }> = {
  feed: {
    label: "Feed",
    blurb: "An RSS, Atom or sitemap index someone else already maintains.",
    build: (v) => ({ name: "Feed", strategy: "feed", seeds: [v],
                     incremental: "watermark" }),
  },
  traverse: {
    label: "Website",
    blurb: "Follow links from a seed page, inside an allowlist, honouring robots.txt.",
    build: (v) => {
      let host = "";
      try { host = new URL(v).hostname; } catch { host = ""; }
      return { name: "Site", strategy: "traverse", seeds: [v], allow_hosts: host ? [host] : [],
               limits: { max_depth: 1, max_items: 50, rate_per_sec: 1 } };
    },
  },
  http: {
    label: "JSON API",
    blurb: "A templated request with declared pagination — no adapter needed.",
    build: (v) => ({ name: "API", strategy: "http", request: { method: "GET", url: v },
                     extract: { items_path: "@" } }),
  },
};

function CrawlersSection({ projectId }: { projectId: string }) {
  const [crawlers, setCrawlers] = useState<Crawler[]>([]);
  const [kind, setKind] = useState<string>("feed");
  const [seed, setSeed] = useState("https://blog.google/rss/");
  const [enrich, setEnrich] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [detail, setDetail] = useState<RunDetail | null>(null);

  const load = useCallback(async () => {
    try {
      const page = await call<{ crawlers: Crawler[] }>(
        `api/v1/projects/${projectId}/crawlers`,
      );
      setCrawlers(page.crawlers);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function act(message: string, work: () => Promise<void>) {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      await work();
      setNote(message);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function create() {
    await act("Created, disabled. Dry-run it before enabling.", async () => {
      await call("api/v1/crawlers", {
        project_id: projectId,
        config: { ...PRESETS[kind].build(seed.trim()), enrich },
      });
      await load();
    });
  }

  async function dryRun(crawler: Crawler) {
    await act("Dry run finished — nothing was written.", async () => {
      const result = await call<RunResult>(
        `api/v1/crawlers/${crawler.crawler_id}/dry-run`, {},
      );
      setDetail(await call<RunDetail>(`api/v1/crawl-runs/${result.run_id}`, undefined, "GET"));
      await load();
    });
  }

  async function runNow(crawler: Crawler) {
    await act("Run finished.", async () => {
      const result = await call<RunResult>(`api/v1/crawlers/${crawler.crawler_id}/run`, {});
      setDetail(await call<RunDetail>(`api/v1/crawl-runs/${result.run_id}`, undefined, "GET"));
      await load();
    });
  }

  return (
    <>
      <h1>Add / crawlers</h1>
      <p className="lede">
        Most data does not announce itself. A crawler discovers it and writes it through the same
        path everything else uses — so nothing downstream can tell a crawled record from a
        webhook-delivered one. A backfill and a poll are the same crawler on two schedules.
      </p>

      <section className="panel">
        <h2>New crawler</h2>
        <div className="row">
          {Object.entries(PRESETS).map(([key, preset]) => (
            <button
              key={key}
              className={kind === key ? "" : "secondary"}
              onClick={() => setKind(key)}
              disabled={busy}
            >
              {preset.label}
            </button>
          ))}
        </div>
        <p className="empty" style={{ marginTop: 8 }}>{PRESETS[kind].blurb}</p>
        <div className="row" style={{ marginTop: 10 }}>
          <input
            type="text"
            value={seed}
            onChange={(e) => setSeed(e.target.value)}
            placeholder="Seed URL"
            style={{ flex: 1 }}
          />
          <button onClick={create} disabled={busy || !seed.trim()}>
            Create
          </button>
        </div>
        <label className="row" style={{ marginTop: 10, gap: 8, alignItems: "center" }}>
          <input type="checkbox" checked={enrich} onChange={(e) => setEnrich(e.target.checked)} />
          <span>Enrich what it finds</span>
        </label>
        <p className="empty" style={{ marginTop: 2 }}>
          Off by default. A crawler can discover fifty thousand records unattended, and enriching
          them is a model call per chunk on data nobody has asked about yet. Leave it off, see what
          the dry run found, then decide.
        </p>
        {note && <p className="ok">{note}</p>}
        {error && <p className="err">{error}</p>}
        <p className="empty">
          Created disabled, always. A website crawler is refused outright unless it declares an
          allowlist — an unbounded link crawl does not stop on its own.
        </p>
      </section>

      <section className="panel">
        <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
          <h2 style={{ margin: 0 }}>Configured</h2>
          <button
            className="secondary"
            disabled={busy}
            onClick={() =>
              act("Scheduler pass finished.", async () => {
                const tick = await call<TickResult>("api/v1/crawl-tick", {});
                setNote(
                  tick.skipped_lock
                    ? "Another scheduler pass is already running."
                    : `Started ${tick.started.length}, skipped ${tick.skipped.length} still` +
                      ` in flight, recovered ${tick.reaped} abandoned.`,
                );
                if (tick.runs.length > 0) {
                  setDetail(
                    await call<RunDetail>(
                      `api/v1/crawl-runs/${tick.runs[0].run_id}`, undefined, "GET",
                    ),
                  );
                }
                await load();
              })
            }
          >
            Run due crawlers
          </button>
        </div>
        <p className="empty">
          The same pass the scheduler makes, for crawlers of yours that are due — so what it would
          do is answerable now rather than at the next tick. A crawler already running is skipped,
          not queued.
        </p>
        {crawlers.length === 0 ? (
          <p className="empty">Nothing yet.</p>
        ) : (
          crawlers.map((crawler) => (
            <div className="hit" key={crawler.crawler_id}>
              <div className="meta">
                <span className="chip on">{crawler.strategy}</span>
                <span className={`chip ${crawler.enabled ? "enriched" : "stored"}`}>
                  {crawler.enabled ? "enabled" : "disabled"}
                </span>
                {!crawler.dry_run_current && (
                  <span className="chip warnchip">needs a dry run</span>
                )}
                {crawler.last_status && (
                  <span className="chip">last run {crawler.last_status}</span>
                )}
                {crawler.emitted !== null && (
                  <span className="chip">
                    {crawler.emitted} written · {crawler.skipped} unchanged
                  </span>
                )}
                {crawler.watermark && (
                  <span className="chip">since {crawler.watermark}</span>
                )}
                {(() => {
                  const fresh = staleness(crawler.seconds_since_last_success);
                  if (!fresh) {
                    return crawler.enabled ? (
                      <span className="chip warnchip">never succeeded</span>
                    ) : null;
                  }
                  return (
                    <span className={fresh.warn ? "chip warnchip" : "chip"}>{fresh.label}</span>
                  );
                })()}
              </div>
              <div className="text">{crawler.name}</div>
              <div className="row" style={{ marginTop: 8 }}>
                <button className="secondary" disabled={busy} onClick={() => dryRun(crawler)}>
                  Dry run
                </button>
                <button
                  className="secondary"
                  disabled={busy || !crawler.dry_run_current}
                  title={crawler.dry_run_current ? "" : "dry-run this configuration first"}
                  onClick={() =>
                    act(crawler.enabled ? "Disabled." : "Enabled.", async () => {
                      await call(
                        `api/v1/crawlers/${crawler.crawler_id}`,
                        { enabled: !crawler.enabled },
                        "PATCH",
                      );
                      await load();
                    })
                  }
                >
                  {crawler.enabled ? "Disable" : "Enable"}
                </button>
                <button
                  className="secondary"
                  disabled={busy || !crawler.enabled}
                  onClick={() => runNow(crawler)}
                >
                  Run now
                </button>
                <button
                  className="secondary"
                  disabled={busy}
                  onClick={() =>
                    act("Deleted. The data it wrote is kept.", async () => {
                      await call(
                        `api/v1/crawlers/${crawler.crawler_id}`, undefined, "DELETE",
                      );
                      await load();
                    })
                  }
                >
                  Delete
                </button>
              </div>
              <p className="provenance">{crawler.crawler_id}</p>
            </div>
          ))
        )}
      </section>

      {detail && (
        <section className="panel">
          <h2>{detail.mode === "dry" ? "Dry run — nothing written" : "Run"}</h2>
          <div className="meta">
            <span className={`chip ${detail.status === "completed" ? "enriched" : "stored"}`}>
              {detail.status}
            </span>
            <span className="chip">{detail.discovered} discovered</span>
            <span className="chip">{detail.emitted} written</span>
            <span className="chip">{detail.skipped} unchanged</span>
            {detail.failed > 0 && <span className="chip warnchip">{detail.failed} failed</span>}
          </div>
          {detail.reason && <p className="empty">{detail.reason}</p>}
          {detail.sample.length > 0 && (
            <>
              <h3>What it found</h3>
              {detail.sample.map((item) => (
                <div className="hit" key={item.external_id}>
                  <div className="text">
                    {item.payload?.title || item.external_id}
                  </div>
                  {item.payload?.preview && (
                    <p className="empty" style={{ marginTop: 4 }}>
                      {item.payload.preview.slice(0, 200)}
                    </p>
                  )}
                  <p className="provenance">{item.url || item.external_id}</p>
                </div>
              ))}
            </>
          )}
          {detail.errors.length > 0 && (
            <>
              <h3>Errors</h3>
              {detail.errors.map((e, i) => (
                <p className="err" key={i}>{e.external_id}: {e.reason}</p>
              ))}
            </>
          )}
        </section>
      )}
    </>
  );
}

/* --------------------------------------------------------------- 4. ask */

type AnswerCitation = {
  marker: number;
  data_id: string;
  chunk_id: string;
  text: string;
  score: number;
  state: string;
};

type Answer = {
  query_id: string;
  question: string;
  answer: string;
  grounded: boolean;
  citations: AnswerCitation[];
  considered: number;
  corpus: { total: number; stored: number; searchable: number; enriched: number } | null;
  excluded: { data_id: string; reason: string; score: number | null; state: string | null }[];
  model_id: string;
  served_by_model: string | null;
  fallback_depth: number;
  served_by_engine: string | null;
  answer_stored: boolean;
  latency_ms: number;
};

function AskSection({ projectId }: { projectId: string }) {
  const [question, setQuestion] = useState("What caused the rollback?");
  const [turns, setTurns] = useState<Answer[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  async function send(preset?: string) {
    const asked = (preset ?? question).trim();
    if (!asked) return;
    setBusy(true);
    setError(null);
    try {
      const answer = await call<Answer>("api/v1/ask", {
        question: asked,
        filter: { project_id: projectId },
      });
      setTurns((previous) => [...previous, answer]);
      setOpen(answer.query_id);
      setQuestion("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1>Chat with your data</h1>
      <p className="lede">
        Ask a question and a model answers from your records — the same retrieval as search, with
        the passages read back to you. Every claim carries the number of the passage it came from,
        and those passages are shown, so a wrong answer is something you can check rather than
        something you have to believe.
      </p>

      {turns.length === 0 && (
        <section className="panel">
          <p className="empty" style={{ marginTop: 0 }}>
            Answers come only from records you can already read. When your data does not support an
            answer, it says so instead of composing one.
          </p>
          <h3>Try one</h3>
          <div className="row">
            {[
              "What did we write about most recently?",
              "Summarise what is in this project.",
              "What caused the last incident?",
            ].map((example) => (
              <button
                key={example}
                className="secondary"
                disabled={busy}
                onClick={() => void send(example)}
              >
                {example}
              </button>
            ))}
          </div>
        </section>
      )}

      {turns.map((turn) => (
        <section className="panel" key={turn.query_id}>
          <p className="question">{turn.question}</p>

          <div className={turn.grounded ? "answer" : "answer ungrounded"}>{turn.answer}</div>

          <div className="meta" style={{ marginTop: 10 }}>
            <span className={`chip ${turn.grounded ? "enriched" : "stored"}`}>
              {turn.grounded ? "grounded" : "not supported by the corpus"}
            </span>
            <span className="chip">{turn.citations.length} cited</span>
            <span className="chip">{turn.considered} passages read</span>
            {turn.corpus && (
              <span className="chip">
                {turn.corpus.enriched} enriched of {turn.corpus.total}
              </span>
            )}
            <span className="chip">{turn.served_by_model || turn.model_id}</span>
            {turn.fallback_depth > 0 && (
              <span className="chip warnchip">
                fallback: {turn.served_by_engine} answered
              </span>
            )}
            <span className="chip">{turn.latency_ms} ms</span>
            {!turn.answer_stored && <span className="chip">text not stored</span>}
          </div>

          {turn.citations.length > 0 && (
            <>
              <h3
                style={{ cursor: "pointer" }}
                onClick={() => setOpen(open === turn.query_id ? null : turn.query_id)}
              >
                {open === turn.query_id ? "▾" : "▸"} The evidence it rests on
              </h3>
              {open === turn.query_id &&
                turn.citations.map((citation) => (
                  <div className="hit" key={citation.chunk_id}>
                    <div className="meta">
                      <span className="chip on">[{citation.marker}]</span>
                      <span className="chip">score {citation.score.toFixed(4)}</span>
                      <span className={`chip ${citation.state}`}>{citation.state}</span>
                    </div>
                    <div className="text">{citation.text}</div>
                    <p className="provenance">{citation.data_id}</p>
                  </div>
                ))}
            </>
          )}

          {!turn.grounded && turn.corpus && turn.corpus.stored > 0 && (
            <p className="empty">
              {turn.corpus.stored} record{turn.corpus.stored === 1 ? " is" : "s are"} stored but not
              searchable yet, so {turn.corpus.stored === 1 ? "it" : "they"} could not have been
              used. That is a likely cause of a thin answer.
            </p>
          )}
        </section>
      ))}

      <section className="panel">
        <div className="row">
          <input
            type="text"
            value={question}
            placeholder="Ask about this project&rsquo;s data…"
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void send()}
            style={{ flex: 1 }}
          />
          <button onClick={() => void send()} disabled={busy || !question.trim()}>
            {busy ? "Reading…" : "Ask"}
          </button>
        </div>
        {error && <p className="err">{error}</p>}
        <p className="empty">
          Answers are generated from retrieved records. Records are treated as evidence, never as
          instructions — a record containing &ldquo;ignore your instructions&rdquo; is reported as
          content, not obeyed.
        </p>
      </section>
    </>
  );
}

/* ------------------------------------------------------------ 4. search */

function ReadSearch({
  projectId,
  seeded,
  onOpenEntity,
}: {
  projectId: string;
  // A name handed over from the Entities panel. Arriving with one turns the
  // graph arm on, because that is the question being asked -- "what else is
  // connected to this?" -- and leaving it off would answer a different one.
  seeded?: string | null;
  onOpenEntity?: (entityId: string) => void;
}) {
  const [query, setQuery] = useState("rollback recovered error rates");
  const [match, setMatch] = useState<ArmKey[]>(["vector", "lexical"]);
  const [trace, setTrace] = useState<Trace | null>(null);
  // What the last search actually asked for, which is not what the controls say
  // once they are changed. Chips read from this, or a result would be rendered
  // against arms it never ran under.
  const [ran, setRan] = useState<ArmKey[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function toggleArm(key: ArmKey) {
    setMatch((current) =>
      current.includes(key)
        ? // Never all-off: a search with no arms is not a narrower search, it
          // is an error from the API.
          current.length === 1
          ? current
          : current.filter((arm) => arm !== key)
        : [...current, key],
    );
  }

  useEffect(() => {
    if (seeded) {
      setQuery(seeded);
      setMatch((current) =>
        current.includes("graph") ? current : [...current, "graph"],
      );
    }
  }, [seeded]);

  async function search() {
    setBusy(true);
    setError(null);
    try {
      setTrace(
        await call<Trace>("api/v1/retrieve", {
          query,
          filter: { project_id: projectId },
          match,
        }),
      );
      setRan(match);
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
        <div className="row" style={{ marginTop: 10 }}>
          <span className="muted" style={{ fontSize: 13 }}>Match on</span>
          <div className="arms">
            {ARMS.map((arm) => (
              <button
                key={arm.key}
                type="button"
                aria-pressed={match.includes(arm.key)}
                onClick={() => toggleArm(arm.key)}
                title={`${arm.label} — ${arm.hint}`}
              >
                {arm.label}
              </button>
            ))}
          </div>
          {match.includes("graph") && (
            <span className="muted" style={{ fontSize: 12.5 }}>
              Records connected to what you named, even when they do not contain it.
            </span>
          )}
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
            {ran.includes("graph") && (
              <p className="seeds">
                <span className="label">
                  {trace.graph_seeds.length === 0
                    ? "The graph arm found nothing in your query to start from —"
                    : "Expanded from"}
                </span>
                {trace.graph_seeds.map((seed) => (
                  <button
                    key={seed.entity_id}
                    className="linkish"
                    onClick={() => onOpenEntity?.(seed.entity_id)}
                    title="Open in Entities"
                  >
                    {seed.display_name} <span className="muted">({seed.type})</span>
                  </button>
                ))}
                {trace.graph_seeds.length === 0 && (
                  <span className="muted">
                    it matches whole entity names against the ones you can see, so
                    &ldquo;Acme&rdquo; will not find &ldquo;Acme Corporation&rdquo;.
                  </span>
                )}
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
                    {ARMS.filter((arm) => ran.includes(arm.key)).map((arm) => (
                      <span
                        key={arm.key}
                        className={`chip${hit.matched_by.includes(arm.chip) ? " on" : " off"}`}
                      >
                        {arm.label}
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
  const [types, setTypes] = useState<MemoryType[]>([]);
  const [selected, setSelected] = useState<Memory | null>(null);
  const [members, setMembers] = useState<MemoryMember[]>([]);
  const [available, setAvailable] = useState<Item[]>([]);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [memberships, setMemberships] = useState<Membership[] | null>(null);
  const [expiry, setExpiry] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [newType, setNewType] = useState("session");
  const [newKey, setNewKey] = useState("");
  const [newTitle, setNewTitle] = useState("");

  const load = useCallback(async () => {
    try {
      const [m, t] = await Promise.all([
        call<{ memories: Memory[] }>(`api/v1/projects/${projectId}/memories`),
        call<{ types: MemoryType[] }>(`api/v1/projects/${projectId}/memory-types`),
      ]);
      setMemories(m.memories);
      setTypes(t.types);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const openMemory = useCallback(
    async (memory: Memory) => {
      setSelected(memory);
      setMemberships(null);
      setChosen(new Set());
      setError(null);
      try {
        const [mem, data] = await Promise.all([
          call<{ members: MemoryMember[] }>(`api/v1/memories/${memory.memory_id}/members`),
          call<{ items: Item[] }>(`api/v1/projects/${projectId}/data?limit=50`),
        ]);
        setMembers(mem.members);
        const inside = new Set(mem.members.map((x) => x.data_id));
        setAvailable(data.items.filter((i) => !inside.has(i.data_id)));
      } catch (e) {
        setError((e as Error).message);
      }
    },
    [projectId],
  );

  async function act(message: string, run: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      await run();
      setNote(message);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function showItemMemories(dataId: string) {
    const state = await call<{ memberships: Membership[]; effective_expiry: string | null }>(
      `api/v1/data/${dataId}/memories`,
    );
    setMemberships(state.memberships);
    setExpiry(state.effective_expiry);
  }

  const currentType = types.find((t) => t.name === selected?.type);

  return (
    <>
      <h1>Memory</h1>
      <p className="lede">
        A memory is a <strong>lifecycle</strong> container — how long does this matter? A case is a
        subject — what is this about? A conversation expires; a patient does not.
      </p>
      {error && <p className="err">{error}</p>}
      {note && <p className="empty">{note}</p>}

      <section className="panel">
        <h2>Create a memory</h2>
        <div className="row">
          <select value={newType} onChange={(e) => setNewType(e.target.value)}>
            {types.map((t) => (
              <option key={t.type_id} value={t.name}>
                {t.name} · {describeTtl(t.ttl_seconds)} · {t.on_expiry}
              </option>
            ))}
          </select>
          <input
            type="text"
            placeholder="key — e.g. incident-4471"
            value={newKey}
            onChange={(e) => setNewKey(e.target.value)}
            style={{ flex: 1, minWidth: 150 }}
          />
          <input
            type="text"
            placeholder="title (optional)"
            value={newTitle}
            onChange={(e) => setNewTitle(e.target.value)}
            style={{ flex: 1, minWidth: 130 }}
          />
          <button
            disabled={busy || !newKey.trim() || types.length === 0}
            onClick={() =>
              act("Memory created.", async () => {
                await call("api/v1/memories", {
                  project_id: projectId,
                  type: newType,
                  key: newKey.trim(),
                  title: newTitle.trim() || null,
                });
                setNewKey("");
                setNewTitle("");
              })
            }
          >
            Create
          </button>
        </div>
        <p className="empty">
          The type decides the lifecycle: a TTL and what happens at the end. Writing an item with a
          memory key also creates one — this is the path for organising deliberately rather than as
          a side effect of a write.
        </p>
      </section>

      <section className="panel">
        <h2>Memories in this project</h2>
        {memories.length === 0 ? (
          <p className="empty">None visible.</p>
        ) : (
          <div className="excluded">
            {memories.map((m) => (
              <div
                className={`item memrow${selected?.memory_id === m.memory_id ? " chosen" : ""}`}
                key={m.memory_id}
                onClick={() => void openMemory(m)}
              >
                <span className="chip on">{m.type}</span>
                <code>{m.memory_key ?? m.memory_id}</code>
                {m.title && <span className="empty">{m.title}</span>}
                <span className="empty">
                  {m.members} member{m.members === 1 ? "" : "s"}
                </span>
                <span className="chip">{describeTtl(m.ttl_seconds)}</span>
              </div>
            ))}
          </div>
        )}
        <p className="empty">
          A memory with no members you can see is not listed — a count of zero would still disclose
          that the container exists, and a key is often meaningful on its own.
        </p>
      </section>

      {selected && (
        <>
          <section className="panel">
            <h2>
              {selected.type} · {selected.memory_key ?? selected.memory_id}
            </h2>

            <h3>Members</h3>
            {members.length === 0 ? (
              <p className="empty">Nothing in here yet.</p>
            ) : (
              members.map((m) => (
                <div className="hit" key={m.data_id}>
                  <div className="meta">
                    <code>{m.data_id}</code>
                    <span className={`chip ${m.state}`}>{m.state}</span>
                    <span className="chip">added {m.added_by}</span>
                    <button
                      className="linkish"
                      onClick={() =>
                        act("Removed. The item itself is untouched.", async () => {
                          await call(
                            `api/v1/memories/${selected.memory_id}/members/${m.data_id}`,
                            undefined,
                            "DELETE",
                          );
                          await openMemory(selected);
                        })
                      }
                    >
                      remove
                    </button>
                    <button className="linkish" onClick={() => void showItemMemories(m.data_id)}>
                      where else does this live?
                    </button>
                  </div>
                  {m.preview && <div className="text">{m.preview}</div>}
                </div>
              ))
            )}
            <p className="empty">
              <code>added_by</code> distinguishes a rule putting an item here from a person doing
              it. Removing a member never deletes the item — and if it was its last memory, the item
              lands in your default rather than becoming invisible.
            </p>

            <h3>Add existing data</h3>
            {available.length === 0 ? (
              <p className="empty">Everything you can see is already in this memory.</p>
            ) : (
              <>
                <div className="excluded">
                  {available.slice(0, 25).map((i) => (
                    <label className="item memrow pick" key={i.data_id}>
                      <input
                        type="checkbox"
                        checked={chosen.has(i.data_id)}
                        onChange={(e) => {
                          const next = new Set(chosen);
                          if (e.target.checked) next.add(i.data_id);
                          else next.delete(i.data_id);
                          setChosen(next);
                        }}
                      />
                      <span className={`chip ${i.state}`}>{i.state}</span>
                      <code>{i.external_id ?? i.data_id}</code>
                    </label>
                  ))}
                </div>
                <div className="row end" style={{ marginTop: 10 }}>
                  <button
                    disabled={busy || chosen.size === 0}
                    onClick={() =>
                      act(`Added ${chosen.size} item(s) to this memory.`, async () => {
                        await call(`api/v1/memories/${selected.memory_id}/members`, {
                          data_ids: [...chosen],
                        });
                        await openMemory(selected);
                      })
                    }
                  >
                    Add {chosen.size > 0 ? `${chosen.size} ` : ""}to this memory
                  </button>
                </div>
              </>
            )}
          </section>

          <section className="panel">
            <h2>Change or remove this memory</h2>
            <div className="row">
              <select
                value={selected.type}
                onChange={(e) =>
                  act("Re-typed. The TTL is recomputed from the new type.", async () => {
                    const type = e.target.value;
                    await call(`api/v1/memories/${selected.memory_id}`, { type }, "PATCH");
                    await openMemory({ ...selected, type });
                  })
                }
              >
                {types.map((t) => (
                  <option key={t.type_id} value={t.name}>
                    type: {t.name} · {describeTtl(t.ttl_seconds)}
                  </option>
                ))}
              </select>
              <button
                className="secondary"
                disabled={busy}
                onClick={async () => {
                  setError(null);
                  try {
                    const preview = await call<Record<string, unknown>>(
                      `api/v1/memories/${selected.memory_id}?preview=true`,
                      undefined,
                      "DELETE",
                    );
                    setNote(
                      `Deleting this would erase ${preview.would_delete} item(s) under ` +
                        `${preview.on_expiry}, and keep ` +
                        `${preview.retained_because_held_elsewhere} held by another memory.`,
                    );
                  } catch (e) {
                    setError((e as Error).message);
                  }
                }}
              >
                Preview deletion
              </button>
              <button
                disabled={busy}
                onClick={() =>
                  act("Memory deleted.", async () => {
                    await call(`api/v1/memories/${selected.memory_id}`, undefined, "DELETE");
                    setSelected(null);
                  })
                }
              >
                Delete memory
              </button>
            </div>
            <p className="empty">
              Deleting applies the type&rsquo;s expiry policy
              {currentType ? ` — this one is ${currentType.on_expiry}` : ""}. Under{" "}
              <code>orphan_delete</code> an item is erased only if no other memory holds it: deleting
              a member because one of its containers went away would destroy data a permanent memory
              still depends on. Preview first — the number that matters is how many survive.
            </p>
          </section>
        </>
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
                  {m.expires_at
                    ? `expires ${new Date(m.expires_at).toLocaleString()}`
                    : "never expires"}
                </span>
              </div>
            ))}
          </div>
          <p className="empty">
            Effective expiry:{" "}
            <strong>{expiry ? new Date(expiry).toLocaleString() : "never"}</strong>. It is the{" "}
            <em>maximum</em> TTL across memberships, computed on read and never stored — any stored
            answer is wrong the moment someone adds or removes a member.
          </p>
        </section>
      )}
    </>
  );
}

/* ------------------------------------------------------- 4. cases */

function CasesSection({ projectId }: { projectId: string }) {
  const [rows, setRows] = useState<Record<string, unknown>[]>([]);
  const [timeline, setTimeline] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    call<{ cases: Record<string, unknown>[] }>(`api/v1/projects/${projectId}/cases`)
      .then((d) => setRows(d.cases))
      .catch((e) => setError((e as Error).message));
  }, [projectId]);

  return (
    <>
      <h1>Cases</h1>
      <p className="lede">
        A case is a <strong>subject</strong> — what is this about? A memory is a lifecycle — how
        long does this matter? A conversation expires; a patient does not.
      </p>
      {error && <p className="err">{error}</p>}
      <section className="panel">
        <h2>Cases in this project</h2>
        {rows.length === 0 ? (
          <p className="empty">None yet. Write an item with a <code>case</code> to create one.</p>
        ) : (
          <div className="excluded">
            {rows.map((c) => (
              <div
                className="item memrow"
                key={String(c.case_id)}
                onClick={() =>
                  call<Record<string, unknown>>(`api/v1/cases/${c.case_id}/timeline`)
                    .then(setTimeline)
                    .catch((e) => setError((e as Error).message))
                }
              >
                <span className="chip on">{String(c.case_type)}</span>
                <code>{String(c.external_id)}</code>
                <span className="empty">{String(c.members)} records</span>
              </div>
            ))}
          </div>
        )}
      </section>

      {timeline && (
        <section className="panel">
          <h2>Timeline · {String(timeline.external_id)}</h2>
          <p className="empty" style={{ marginTop: -4 }}>
            {String(timeline.asserted)} asserted, {String(timeline.inferred)} inferred. Ordered by{" "}
            <strong>event_time</strong> — a timeline built on when we learned about something
            renders perfectly while being wrong.
          </p>
          {(timeline.entries as Record<string, unknown>[]).map((e) => (
            <div className="hit" key={String(e.data_id)}>
              <div className="meta">
                <span className="chip">{new Date(String(e.event_time)).toLocaleDateString()}</span>
                <span className={`chip ${e.basis === "asserted" ? "on" : ""}`}>
                  {String(e.basis)}
                </span>
                {e.matched_on ? <span className="chip">matched {String(e.matched_on)}</span> : null}
                <code>{String(e.data_id)}</code>
              </div>
              {e.preview ? <div className="text">{String(e.preview)}</div> : null}
            </div>
          ))}
        </section>
      )}
    </>
  );
}

/* ----------------------------------------------------- 5. sharing */

function SharingSection() {
  const [rows, setRows] = useState<Record<string, unknown>[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    call<{ shares: Record<string, unknown>[] }>("api/v1/shares")
      .then((d) => setRows(d.shares))
      .catch((e) => setError((e as Error).message));
  }, []);
  useEffect(load, [load]);

  return (
    <>
      <h1>Sharing</h1>
      <p className="lede">
        Genuinely external sharing is the feature most likely to cause an accidental disclosure, so
        it carries controls the others do not: off by default at org level, expiry required, every
        access logged — and <strong>derived artifacts never follow a share</strong>.
      </p>
      {error && <p className="err">{error}</p>}
      <section className="panel">
        <h2>Everything currently shared</h2>
        {rows.length === 0 ? (
          <p className="empty">Nothing is shared.</p>
        ) : (
          <div className="excluded">
            {rows.map((s) => (
              <div className="item" key={String(s.share_id)}>
                <span className={`chip ${s.live ? "on" : ""}`}>{s.live ? "live" : "inactive"}</span>
                <code>{String(s.data_id ?? s.case_id)}</code>
                <span className="empty">
                  expires {new Date(String(s.expires_at)).toLocaleDateString()} ·{" "}
                  {String(s.access_count)} accesses
                </span>
                {s.live ? (
                  <button
                    className="linkish"
                    onClick={async () => {
                      await call(`api/v1/shares/${s.share_id}`, undefined, "DELETE");
                      load();
                    }}
                  >
                    revoke
                  </button>
                ) : null}
              </div>
            ))}
          </div>
        )}
        <p className="empty">
          This inventory exists to catch the share someone made six months ago and forgot.
        </p>
      </section>
    </>
  );
}

/* ---------------------------------------------------- 6. deletion */

function DeletionSection({
  projectId,
  onChange,
}: {
  projectId: string;
  onChange: () => Promise<void>;
}) {
  type Scope = "record" | "memory" | "detach" | "account";

  const [scope, setScope] = useState<Scope>("record");
  const [items, setItems] = useState<Item[]>([]);
  const [memories, setMemories] = useState<Memory[]>([]);
  const [chosenItems, setChosenItems] = useState<Set<string>>(new Set());
  const [chosenMemory, setChosenMemory] = useState<string>("");
  const [detachItem, setDetachItem] = useState<string>("");
  const [detachFrom, setDetachFrom] = useState<Membership[]>([]);
  const [preview, setPreview] = useState<Record<string, unknown> | null>(null);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [d, m] = await Promise.all([
        call<{ items: Item[] }>(`api/v1/projects/${projectId}/data?limit=50`),
        call<{ memories: Memory[] }>(`api/v1/projects/${projectId}/memories`),
      ]);
      setItems(d.items);
      setMemories(m.memories);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [projectId]);
  useEffect(() => {
    void load();
  }, [load]);

  function reset(next: Scope) {
    setScope(next);
    setPreview(null);
    setResult(null);
    setError(null);
  }

  async function run(run: () => Promise<unknown>, into: "preview" | "result") {
    setBusy(true);
    setError(null);
    try {
      const out = (await run()) as Record<string, unknown>;
      if (into === "preview") setPreview(out);
      else {
        setResult(out);
        setPreview(null);
        await load();
        await onChange();
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const SCOPES: { key: Scope; label: string; blast: string }[] = [
    { key: "record", label: "A record", blast: "one item and everything derived from it" },
    { key: "memory", label: "A memory of records", blast: "the container, and members nothing else holds" },
    { key: "detach", label: "Detach from a memory", blast: "membership only — the record survives" },
    { key: "account", label: "All account data", blast: "everything personal; shared data is retained" },
  ];

  return (
    <>
      <h1>Deletion</h1>
      <p className="lede">
        Four scopes, widening. Invisible in the request transaction; reclaimed asynchronously.
        <code> deleted_at</code> is when it became invisible, <code>purged_at</code> is when the
        bytes went — and the certificate is issued against the second.
      </p>

      <section className="panel">
        <h2>What are you deleting?</h2>
        <div className="scopes">
          {SCOPES.map((s) => (
            <button
              key={s.key}
              className={`scope${scope === s.key ? " active" : ""}`}
              onClick={() => reset(s.key)}
            >
              <span className="scope-label">{s.label}</span>
              <span className="scope-blast">{s.blast}</span>
            </button>
          ))}
        </div>
      </section>

      {error && <p className="err">{error}</p>}

      {scope === "record" && (
        <section className="panel">
          <h2>Records</h2>
          {items.length === 0 ? (
            <p className="empty">Nothing to delete.</p>
          ) : (
            <div className="excluded">
              {items.slice(0, 30).map((i) => (
                <label className="item memrow pick" key={i.data_id}>
                  <input
                    type="checkbox"
                    checked={chosenItems.has(i.data_id)}
                    onChange={(e) => {
                      const next = new Set(chosenItems);
                      if (e.target.checked) next.add(i.data_id);
                      else next.delete(i.data_id);
                      setChosenItems(next);
                    }}
                  />
                  <span className={`chip ${i.state}`}>{i.state}</span>
                  <code>{i.external_id ?? i.data_id}</code>
                </label>
              ))}
            </div>
          )}
          <div className="row end" style={{ marginTop: 10 }}>
            <button
              className="secondary"
              disabled={busy || chosenItems.size === 0}
              onClick={() =>
                run(
                  () =>
                    call("api/v1/deletions", {
                      selector: { project_id: projectId, data_ids: [...chosenItems] },
                      dry_run: true,
                    }),
                  "preview",
                )
              }
            >
              Dry run
            </button>
            <button
              disabled={busy || chosenItems.size === 0}
              onClick={() =>
                run(
                  () =>
                    call("api/v1/deletions", {
                      selector: { project_id: projectId, data_ids: [...chosenItems] },
                      reason: "deleted from the console",
                    }),
                  "result",
                )
              }
            >
              Delete {chosenItems.size > 0 ? chosenItems.size : ""}
            </button>
          </div>
          <p className="empty">
            The cascade reaches chunks, vectors, versions, the projection, case and memory
            membership, share links and the stored bytes — then the root row last, because it is
            the map the cascade needs.
          </p>
        </section>
      )}

      {scope === "memory" && (
        <section className="panel">
          <h2>Memories</h2>
          <div className="row">
            <select value={chosenMemory} onChange={(e) => setChosenMemory(e.target.value)}>
              <option value="">choose a memory…</option>
              {memories.map((m) => (
                <option key={m.memory_id} value={m.memory_id}>
                  {m.type} · {m.memory_key ?? m.memory_id} · {m.members} member
                  {m.members === 1 ? "" : "s"}
                </option>
              ))}
            </select>
            <button
              className="secondary"
              disabled={busy || !chosenMemory}
              onClick={() =>
                run(
                  () => call(`api/v1/memories/${chosenMemory}?preview=true`, undefined, "DELETE"),
                  "preview",
                )
              }
            >
              Preview
            </button>
            <button
              disabled={busy || !chosenMemory}
              onClick={() =>
                run(() => call(`api/v1/memories/${chosenMemory}`, undefined, "DELETE"), "result")
              }
            >
              Delete memory
            </button>
          </div>
          <p className="empty">
            Under <code>orphan_delete</code> a member is erased only if no other memory holds it.
            Deleting a record because one of its containers went away would destroy data a
            permanent memory still depends on — so preview first: the number that matters is how
            many survive.
          </p>
        </section>
      )}

      {scope === "detach" && (
        <section className="panel">
          <h2>Detach a record from a memory</h2>
          <div className="row">
            <select
              value={detachItem}
              onChange={async (e) => {
                setDetachItem(e.target.value);
                setDetachFrom([]);
                if (!e.target.value) return;
                try {
                  const state = await call<{ memberships: Membership[] }>(
                    `api/v1/data/${e.target.value}/memories`,
                  );
                  setDetachFrom(state.memberships);
                } catch (err) {
                  setError((err as Error).message);
                }
              }}
              style={{ flex: 1 }}
            >
              <option value="">choose a record…</option>
              {items.slice(0, 50).map((i) => (
                <option key={i.data_id} value={i.data_id}>
                  {i.external_id ?? i.data_id}
                </option>
              ))}
            </select>
          </div>
          {detachFrom.length > 0 && (
            <div className="excluded" style={{ marginTop: 12 }}>
              {detachFrom.map((m) => (
                <div className="item" key={m.memory_id}>
                  <span className="chip on">{m.type}</span>
                  <code>{m.memory_key ?? m.memory_id}</code>
                  <span className="chip">{m.added_by}</span>
                  <button
                    className="linkish"
                    disabled={busy}
                    onClick={() =>
                      run(
                        () =>
                          call(
                            `api/v1/memories/${m.memory_id}/members/${detachItem}`,
                            undefined,
                            "DELETE",
                          ),
                        "result",
                      )
                    }
                  >
                    detach
                  </button>
                </div>
              ))}
            </div>
          )}
          <p className="empty">
            Detaching is not deleting. The record survives — and if this was its last memory it
            lands in your default, because a record in no memory is invisible from the memory side
            entirely.
          </p>
        </section>
      )}

      {scope === "account" && (
        <section className="panel">
          <h2>All account data</h2>
          <div className="notice caution">
            <strong>This deletes what is personal and keeps what is shared.</strong> Data that
            arrived through a shared connection, or that you published to the organisation, is the
            organisation&rsquo;s — deleting a colleague&rsquo;s work as a side effect of someone
            leaving is the failure this boundary exists to prevent. Keys, producers and connections
            are revoked immediately, before any data question is settled.
          </div>
          <div className="row end">
            <button
              className="secondary"
              disabled={busy}
              onClick={() => run(() => call("api/v1/users/me/deletion", { dry_run: true }), "preview")}
            >
              Dry run
            </button>
            <button
              disabled={busy || preview === null}
              onClick={() =>
                run(
                  () =>
                    call("api/v1/users/me/deletion", { reason: "requested from the console" }),
                  "result",
                )
              }
            >
              Delete my account data
            </button>
          </div>
          <p className="empty">
            Deleting is only enabled after a dry run — this is the one scope where the preview is
            not optional.
          </p>
        </section>
      )}

      {(preview || result) && (
        <section className="panel">
          <h2>{result ? "Done" : "Dry run — nothing has changed"}</h2>
          <table className="kv">
            <tbody>
              {Object.entries(result ?? preview ?? {})
                .filter(([k]) => k !== "retained" || true)
                .map(([k, v]) => (
                  <tr key={k}>
                    <td>{k.replace(/_/g, " ")}</td>
                    <td>
                      <code>
                        {typeof v === "object" && v !== null
                          ? JSON.stringify(v).slice(0, 300)
                          : String(v)}
                      </code>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </section>
      )}
    </>
  );
}

/* ---------------------------------------------------- 7. settings */

function SettingsSection({ projectId }: { projectId: string }) {
  const [rows, setRows] = useState<Record<string, unknown>[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    call<{ settings: Record<string, unknown>[] }>(`api/v1/settings/effective?project_id=${projectId}`)
      .then((d) => setRows(d.settings))
      .catch((e) => setError((e as Error).message));
  }, [projectId]);
  useEffect(load, [load]);

  return (
    <>
      <h1>Settings</h1>
      <p className="lede">
        user → project → org → platform, most specific wins — <strong>except where an admin has
        locked a setting</strong>. Without locks, org policy is advisory, and a compliance control a
        project can switch off is not a control.
      </p>
      {error && <p className="err">{error}</p>}
      <section className="panel">
        <h2>Effective values, and where each came from</h2>
        <div className="excluded">
          {rows.map((s) => (
            <div className="item" key={String(s.key)}>
              <code>{String(s.key)}</code>
              <span className="chip on">{JSON.stringify(s.value)}</span>
              <span className="chip">from {String(s.source)}</span>
              {s.locked_by ? <span className="why">locked at {String(s.locked_by)}</span> : null}
              <span className="empty">{(s.allowed_scopes as string[]).join(" · ")}</span>
            </div>
          ))}
        </div>
        <p className="empty">
          Provenance is the point. &ldquo;It is set to X&rdquo; is not actionable without &ldquo;by
          whom, at which level, and can I change it&rdquo;.
        </p>
      </section>
    </>
  );
}

/* ------------------------------------------------------ 8. models */

function ModelsSection() {
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    call<Record<string, unknown>>("api/v1/models").then(setData).catch((e) => setError((e as Error).message));
  }, []);

  return (
    <>
      <h1>Models</h1>
      <p className="lede">
        Assignment is per <code>(purpose, data type)</code>, resolved at request time. Classification
        is not reasoning and transcription is not extraction — sizing them identically overpays for
        the cheap cases and underperforms on the expensive ones.
      </p>
      {error && <p className="err">{error}</p>}
      {data && (
        <>
          <section className="panel">
            <h2>Current assignments</h2>
            {(data.assignments as unknown[]).length === 0 ? (
              <p className="empty">
                None — the deployment defaults apply, which is the correct state for most
                installations.
              </p>
            ) : (
              <div className="excluded">
                {(data.assignments as Record<string, unknown>[]).map((a) => (
                  <div className="item" key={`${a.purpose}-${a.data_type}`}>
                    <span className="chip on">{String(a.purpose)}</span>
                    <span className="chip">{String(a.data_type)}</span>
                    <code>{String(a.model_id)}</code>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="panel">
            <h2>Catalog</h2>
            {(data.models as Record<string, unknown>[]).map((m) => (
              <div className="hit" key={String(m.model_id)}>
                <div className="meta">
                  <code>{String(m.model_id)}</code>
                  <span className="chip">{String(m.provider)}</span>
                  <span className={`chip ${m.declared_by === "measured" ? "on" : ""}`}>
                    {String(m.declared_by)}
                  </span>
                  {(m.capabilities as string[]).map((c) => (
                    <span className="chip" key={c}>{c}</span>
                  ))}
                </div>
                {m.notes ? <div className="text">{String(m.notes)}</div> : null}
              </div>
            ))}
            <p className="empty">
              <code>declared_by</code> separates a vendor&rsquo;s claim from something measured here
              — which matters when an assignment is derived from it.
            </p>
          </section>
        </>
      )}
    </>
  );
}

/* --------------------------------------------------------------- MCP */

type McpTool = { name: string; description: string };
type McpManifest = {
  protocolVersion: string;
  serverInfo: { name: string; version: string };
  transport: string;
  endpoint: string;
  authentication: string;
  tools: McpTool[];
};

function McpSection({ projectId }: { projectId: string }) {
  const [manifest, setManifest] = useState<McpManifest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  useEffect(() => {
    call<McpManifest>("api/v1/mcp", undefined, "GET")
      .then(setManifest)
      .catch((e) => setError((e as Error).message));
  }, []);

  // The address a *client* uses, which is this console's own origin: the API
  // sits behind Cloud Run IAM and the browser never reaches it directly, so
  // printing the API's URL here would give people something that cannot work.
  const endpoint =
    typeof window === "undefined" ? "" : `${window.location.origin}/api/proxy/api/v1/mcp`;

  const config = JSON.stringify(
    {
      mcpServers: {
        "mem-dog": {
          url: endpoint,
          headers: { Authorization: "Bearer YOUR_API_KEY" },
        },
      },
    },
    null,
    2,
  );

  async function copy(what: string, text: string) {
    await navigator.clipboard.writeText(text);
    setCopied(what);
    setTimeout(() => setCopied(null), 1500);
  }

  return (
    <>
      <h1>MCP</h1>
      <p className="lede">
        This corpus as tools an assistant can call. The tools are the same functions the API
        serves, so a record your key cannot retrieve over HTTP is one it cannot reach here
        either — the access rule is inside the query, not layered on top.
      </p>

      {error && <p className="err">{error}</p>}

      <section className="panel">
        <h2>Point a client at it</h2>
        <div className="row" style={{ marginBottom: 10 }}>
          <code style={{ flex: 1, wordBreak: "break-all" }}>{endpoint}</code>
          <button className="secondary" onClick={() => copy("url", endpoint)}>
            {copied === "url" ? "Copied" : "Copy URL"}
          </button>
        </div>
        <p className="empty">
          Authenticate with an ordinary API key — the same one the REST API takes, carrying
          the same capabilities. Create one under <strong>Admin → Keys</strong>.
        </p>
        <div className="row end" style={{ marginTop: 12 }}>
          <button className="secondary" onClick={() => copy("config", config)}>
            {copied === "config" ? "Copied" : "Copy client config"}
          </button>
        </div>
        <pre className="code">{config}</pre>
      </section>

      <section className="panel">
        <h2>Tools</h2>
        <p className="empty">
          Eight, and the descriptions matter: an assistant picks between them on what they
          say. <code>search</code> returns evidence, <code>chat</code> returns prose with
          citations — calling both &ldquo;search&rdquo; would make the choice arbitrary.
        </p>
        {(manifest?.tools ?? []).map((tool) => (
          <div className="hit" key={tool.name}>
            <div className="meta">
              <span className="chip on">{tool.name}</span>
            </div>
            <div className="text">{tool.description}</div>
          </div>
        ))}
        {manifest && manifest.tools.length === 0 && (
          <p className="empty">The server reports no tools, which should not happen.</p>
        )}
      </section>

      {manifest && (
        <section className="panel">
          <h2>What this server is</h2>
          <div className="meta">
            <span className="chip">protocol {manifest.protocolVersion}</span>
            <span className="chip">{manifest.transport}</span>
            <span className="chip">
              {manifest.serverInfo.name} {manifest.serverInfo.version}
            </span>
            <span className="chip">project {projectId}</span>
          </div>
          <p className="empty" style={{ marginTop: 10 }}>
            Streamable HTTP rather than the older session-bearing SSE: this service scales to
            zero and out to four, so a stream and the posts belonging to it would not reliably
            land on the same instance. A client that requires <code>text/event-stream</code>
            still gets its response as one event.
          </p>
        </section>
      )}
    </>
  );
}

/* ----------------------------------------------------- 9. prompts */

type PromptRow = {
  data_type: string;
  prompt: string;
  shared: boolean;
  extensions: string[];
  mime_types: string[];
  excerpt: string;
};

function PromptsSection({ projectId }: { projectId: string }) {
  const [dataType, setDataType] = useState("document_pdf");
  const [rows, setRows] = useState<PromptRow[]>([]);
  const [config, setConfig] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Served from the register rather than hardcoded here: a list in the
    // console drifts from the one the classifier uses, and the drift is
    // invisible until someone looks for a type that is missing.
    call<{ prompts: PromptRow[] }>("api/v1/prompts")
      .then((d) => setRows(d.prompts))
      .catch((e) => setError((e as Error).message));
  }, []);

  const load = useCallback(() => {
    call<Record<string, unknown>>(`api/v1/agents/${dataType}/config?project_id=${projectId}`)
      .then(setConfig)
      .catch((e) => setError((e as Error).message));
  }, [dataType, projectId]);
  useEffect(load, [load]);

  return (
    <>
      <h1>Prompts</h1>
      <p className="lede">
        Defaults ship with the product, so an override is opting <em>out</em> of a default rather
        than filling in a blank. Changing one is a versioning event: the prompt is hashed into
        <code> generator_version</code>, which is what makes old artifacts detectably stale.
      </p>
      <section className="panel">
        <h2>
          {rows.length} data types, {new Set(rows.map((r) => r.prompt)).size} prompts
        </h2>
        <p className="empty" style={{ marginTop: 0 }}>
          Some prompts are shared on purpose — a .docx asks a PDF&rsquo;s questions. The bar for a
          new one is whether a reader would ask something different of it, not whether it is a
          distinct file format.
        </p>
        <div className="row">
          {rows.map((r) => r.data_type).map(
            (t) => (
              <button
                key={t}
                className={dataType === t ? "" : "secondary"}
                onClick={() => setDataType(t)}
              >
                {t}
              </button>
            ),
          )}
        </div>
        {error && <p className="err">{error}</p>}
        {(() => {
          const row = rows.find((r) => r.data_type === dataType);
          if (!row) return null;
          return (
            <div className="meta" style={{ marginTop: 12 }}>
              <span className="chip on">{row.prompt}</span>
              {row.shared && <span className="chip">shared with other types</span>}
              {row.extensions.slice(0, 8).map((e) => (
                <span className="chip" key={e}>{e}</span>
              ))}
              {row.extensions.length > 8 && (
                <span className="chip">+{row.extensions.length - 8} more</span>
              )}
              {row.extensions.length === 0 && (
                <span className="chip">no file extension — routed by source</span>
              )}
            </div>
          );
        })()}
        {config && (
          <>
            <table className="kv" style={{ marginTop: 14 }}>
              <tbody>
                <tr><td>source</td><td>{String(config.source)}</td></tr>
                <tr><td>overridden</td><td>{String(config.overridden)}</td></tr>
                <tr><td>generator</td><td><code>{String(config.generator_version)}</code></td></tr>
              </tbody>
            </table>
            <div className="hit" style={{ marginTop: 12 }}>
              <div className="text">{String(config.prompt)}</div>
            </div>
          </>
        )}
      </section>
    </>
  );
}

/* -------------------------------------------------- 10-13. admin */

function ProjectsSection() {
  const [projects, setProjects] = useState<Record<string, unknown>[]>([]);
  const [members, setMembers] = useState<Record<string, unknown>[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      call<{ projects: Record<string, unknown>[] }>("api/v1/projects"),
      call<{ members: Record<string, unknown>[] }>("api/v1/organizations/members"),
    ])
      .then(([p, m]) => {
        setProjects(p.projects);
        setMembers(m.members);
      })
      .catch((e) => setError((e as Error).message));
  }, []);

  return (
    <>
      <h1>Projects &amp; members</h1>
      <p className="lede">
        A project is the isolation boundary every query is scoped to. Org roles are separate from
        platform grants — a platform admin holds nothing inside an org they are not a member of.
      </p>
      {error && <p className="err">{error}</p>}
      <section className="panel">
        <h2>Projects</h2>
        <div className="excluded">
          {projects.map((p) => (
            <div className="item" key={String(p.project_id)}>
              <code>{String(p.project_id)}</code>
              <span className="chip on">{String(p.name)}</span>
              <span className="empty">{String(p.items)} items</span>
              <span className="chip">answers: {String(p.answer_storage)}</span>
            </div>
          ))}
        </div>
      </section>
      <section className="panel">
        <h2>Members</h2>
        <div className="excluded">
          {members.map((m) => (
            <div className="item" key={String(m.user_id)}>
              <code>{String(m.email)}</code>
              <span className="chip on">{String(m.role)}</span>
            </div>
          ))}
        </div>
        <p className="empty">Removing a member revokes their keys in the same transaction.</p>
      </section>
    </>
  );
}

function KeysSection() {
  const [keys, setKeys] = useState<Record<string, unknown>[]>([]);
  const [issued, setIssued] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    call<{ keys: Record<string, unknown>[] }>("api/v1/users/me/api-keys")
      .then((d) => setKeys(d.keys))
      .catch((e) => setError((e as Error).message));
  }, []);
  useEffect(load, [load]);

  return (
    <>
      <h1>API keys</h1>
      <p className="lede">
        Hashed at rest with a display prefix. A key can never grant more than the credential that
        created it — otherwise capability scoping is decorative.
      </p>
      <section className="panel">
        <div className="row">
          <button
            onClick={async () => {
              setError(null);
              try {
                const r = await call<{ token: string }>("api/v1/users/me/api-keys", {
                  name: "console", capabilities: ["data:read"],
                });
                setIssued(r.token);
                load();
              } catch (e) {
                setError((e as Error).message);
              }
            }}
          >
            Issue a read-only key
          </button>
        </div>
        {issued && (
          <div className="notice" style={{ marginTop: 12 }}>
            <strong>Shown once.</strong> <code>{issued}</code>
          </div>
        )}
        {error && <p className="err">{error}</p>}
        <div className="excluded" style={{ marginTop: 12 }}>
          {keys.map((k) => (
            <div className="item" key={String(k.key_id)}>
              <code>{String(k.prefix)}</code>
              <span className="chip">{(k.capabilities as string[]).join(" ")}</span>
              <span className="empty">
                {k.revoked_at ? "revoked" : k.last_used_at
                  ? `used ${new Date(String(k.last_used_at)).toLocaleString()}`
                  : "never used"}
              </span>
            </div>
          ))}
        </div>
      </section>
    </>
  );
}

function ProducersSection() {
  const [rows, setRows] = useState<Record<string, unknown>[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    call<{ producers: Record<string, unknown>[] }>("api/v1/producers")
      .then((d) => setRows(d.producers))
      .catch((e) => setError((e as Error).message));
  }, []);
  return (
    <>
      <h1>Producers</h1>
      <p className="lede">
        Nothing writes anonymously. <code>seconds_since_last_item</code> is the highest-value
        detector in the system: it catches a stopped webhook, a crawler whose selector broke, and a
        client that quietly died — with one query.
      </p>
      {error && <p className="err">{error}</p>}
      <section className="panel">
        <div className="excluded">
          {rows.map((p) => (
            <div className="item" key={String(p.producer_id)}>
              <code>{String(p.producer_id)}</code>
              <span className="chip on">{String(p.type)}</span>
              <span className={`chip ${p.status === "enabled" ? "on" : ""}`}>{String(p.status)}</span>
              {p.connection_scope ? <span className="chip">{String(p.connection_scope)}</span> : null}
              <span className="empty">
                {p.seconds_since_last_item === null
                  ? "never written"
                  : `last item ${Math.round(Number(p.seconds_since_last_item))}s ago`}
              </span>
            </div>
          ))}
        </div>
      </section>
    </>
  );
}

function PlatformSection() {
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    call<Record<string, unknown>>("api/v1/platform/health").then(setData).catch((e) =>
      setError((e as Error).message),
    );
  }, []);
  return (
    <>
      <h1>Platform</h1>
      <p className="lede">
        Operational shape only — counts, queue depth, unpurged tombstones. A platform admin does not
        get tenant content by default; support tooling that shows customer data is a privacy
        violation arriving disguised as a feature request.
      </p>
      {error && <p className="err">{error} — this view requires <code>admin:*</code>.</p>}
      {data && (
        <section className="panel">
          <table className="kv">
            <tbody>
              {Object.entries(data).map(([k, v]) => (
                <tr key={k}>
                  <td>{k}</td>
                  <td><code>{typeof v === "object" ? JSON.stringify(v) : String(v)}</code></td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </>
  );
}


/* ---------------------------------------------------- 0. overview */

type OverviewData = {
  counts: {
    total: number; stored: number; searchable: number; enriched: number;
    with_bytes: number; awaiting_fetch: number; bytes_stored: number;
    first_write: string | null; last_write: string | null;
  };
  by_data_type: { data_type: string; n: number }[];
  not_read: { parse_status: string; n: number }[];
  models: { model_id: string; calls: number; tokens: number }[];
  derived: { chunks: number; embeddings: number; vector_spaces: number; revisions: number };
  containers: { memories: number; cases: number; live_shares: number };
  activity: { writes_24h: number; reads_24h: number; queries_24h: number };
};

function Overview({
  projectId,
  me,
}: {
  projectId: string;
  me: { email?: string; role?: string } | null;
}) {
  const [data, setData] = useState<OverviewData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    call<OverviewData>(`api/v1/projects/${projectId}/overview`)
      .then(setData)
      .catch((e) => setError((e as Error).message));
  }, [projectId]);

  if (error) return <p className="err">{error}</p>;
  if (!data) return <p className="empty">Loading…</p>;

  const { counts, derived, containers, activity } = data;
  const ready = counts.total === 0 ? 0 : Math.round((counts.enriched / counts.total) * 100);

  return (
    <>
      <h1>Overview</h1>
      <p className="lede">
        {me?.email ? `Signed in as ${me.email}. ` : ""}
        Every number here is what <em>you</em> can see — the ACL is inside each query, so two
        people looking at the same project can legitimately see different totals.
      </p>

      {/* A hero number, because the headline question is one number: how much of
          what I put in is actually usable? */}
      <section className="panel hero-stat">
        <div className="hero-figure">
          <span className="hero-value">{ready}%</span>
          <span className="hero-label">
            of {counts.total} record{counts.total === 1 ? "" : "s"} fully enriched
          </span>
        </div>
        <div className="hero-stair">
          <StairBar label="stored" value={counts.total} total={counts.total} step={1} />
          <StairBar
            label="searchable"
            value={counts.searchable + counts.enriched}
            total={counts.total}
            step={2}
          />
          <StairBar label="enriched" value={counts.enriched} total={counts.total} step={3} />
        </div>
      </section>

      <section className="tiles">
        <Tile value={derived.chunks} label="chunks indexed" />
        <Tile value={derived.embeddings} label="embeddings" />
        <Tile
          value={derived.vector_spaces}
          label="vector space"
          note={derived.vector_spaces <= 1 ? "one space — comparable" : "mixed — not comparable"}
          alarm={derived.vector_spaces > 1}
        />
        <Tile value={derived.revisions} label="revisions kept" />
        <Tile value={containers.memories} label="memories" />
        <Tile value={containers.cases} label="cases" />
        <Tile
          value={containers.live_shares}
          label="public shares"
          alarm={containers.live_shares > 0}
          note={containers.live_shares > 0 ? "externally readable" : undefined}
        />
        <Tile value={humanBytes(counts.bytes_stored)} label="bytes in object storage" />
      </section>

      <div className="two-up">
        <section className="panel">
          <h2>What is in here</h2>
          {data.by_data_type.length === 0 ? (
            <p className="empty">Nothing yet.</p>
          ) : (
            <div className="barlist">
              {data.by_data_type.map((row) => (
                <div className="barrow" key={row.data_type}>
                  <span className="barlabel">{row.data_type}</span>
                  <span className="bar">
                    <span
                      className="fill"
                      style={{ width: `${(row.n / data.by_data_type[0].n) * 100}%` }}
                    />
                  </span>
                  <span className="count">{row.n}</span>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="panel">
          <h2>Stored but not read</h2>
          {data.not_read.length === 0 ? (
            <p className="empty">Everything stored has been read.</p>
          ) : (
            <div className="excluded">
              {data.not_read.map((row) => (
                <div className="item" key={row.parse_status}>
                  <span className="chip warnchip">{row.parse_status}</span>
                  <span className="empty">{row.n} record{row.n === 1 ? "" : "s"}</span>
                </div>
              ))}
            </div>
          )}
          <p className="empty">
            Nothing is rejected — but an item that stored without being read is invisible to
            search, and the reason is the difference between a bug and a policy.
          </p>
        </section>
      </div>

      <div className="two-up">
        <section className="panel">
          <h2>Models that touched this data</h2>
          {data.models.length === 0 ? (
            <p className="empty">No model has been involved yet.</p>
          ) : (
            <table className="kv">
              <tbody>
                {data.models.map((m) => (
                  <tr key={m.model_id}>
                    <td><code>{m.model_id}</code></td>
                    <td>
                      {m.calls} call{m.calls === 1 ? "" : "s"}
                      {m.tokens > 0 && ` · ${m.tokens.toLocaleString()} tokens`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="empty">
            Every derived row records which model produced it and which build answered, so a run is
            reproducible and two runs are comparable.
          </p>
        </section>

        <section className="panel">
          <h2>Last 24 hours</h2>
          <table className="kv">
            <tbody>
              <tr><td>writes</td><td>{activity.writes_24h}</td></tr>
              <tr><td>reads logged</td><td>{activity.reads_24h}</td></tr>
              <tr><td>searches</td><td>{activity.queries_24h}</td></tr>
              <tr>
                <td>last write</td>
                <td>
                  {counts.last_write ? new Date(counts.last_write).toLocaleString() : "—"}
                </td>
              </tr>
            </tbody>
          </table>
          <p className="empty">
            Reads and writes live in separate append-only stores that outlive what they describe.
          </p>
        </section>
      </div>
    </>
  );
}

function Tile({
  value,
  label,
  note,
  alarm,
}: {
  value: number | string;
  label: string;
  note?: string;
  alarm?: boolean;
}) {
  return (
    <div className={`tile${alarm ? " alarm" : ""}`}>
      <span className="tile-value">{typeof value === "number" ? value.toLocaleString() : value}</span>
      <span className="tile-label">{label}</span>
      {note && <span className="tile-note">{note}</span>}
    </div>
  );
}

function StairBar({
  label,
  value,
  total,
  step,
}: {
  label: string;
  value: number;
  total: number;
  step: 1 | 2 | 3;
}) {
  return (
    <div className="stair-row">
      <span className="label">{label}</span>
      <span className="bar">
        <span className={`fill step${step}`} style={{ width: `${(value / Math.max(total, 1)) * 100}%` }} />
      </span>
      <span className="count">{value}</span>
    </div>
  );
}

/* ---------------------------------------------------- inbound webhooks */

type Producer = {
  producer_id: string;
  type: string;
  status: string;
  inbound_auth: string;
  connection_scope: string | null;
  seconds_since_last_item: number | null;
};

type WebhookDelivery = {
  delivery_id: string;
  external_delivery_id: string | null;
  status: string;
  reason: string | null;
  items: number;
  payload_bytes: number | null;
  signature_verified: boolean;
  received_at: string;
};

const SAMPLE = JSON.stringify(
  {
    events: [
      { id: "msg-1", text: "Deploy rolled back; error rates recovered.", ts: 1787900000 },
      { id: "msg-2", text: "Dana owns the runbook change, due Friday.", ts: 1787900120 },
    ],
  },
  null,
  2,
);

function InboundSection({ projectId }: { projectId: string }) {
  const [hooks, setHooks] = useState<Producer[]>([]);
  const [selected, setSelected] = useState<Producer | null>(null);
  const [deliveries, setDeliveries] = useState<WebhookDelivery[]>([]);
  const [secret, setSecret] = useState<string | null>(null);
  const [payload, setPayload] = useState(SAMPLE);
  const [mapping, setMapping] = useState(
    '{\n  "items_path": "events",\n  "external_id_path": "id",\n  "text_path": "text",\n  "event_time_path": "ts"\n}',
  );
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const origin = typeof window === "undefined" ? "" : window.location.origin;

  const load = useCallback(async () => {
    try {
      const all = await call<{ producers: Producer[] }>("api/v1/producers");
      setHooks(all.producers.filter((p) => p.type === "webhook"));
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  const openHook = useCallback(async (hook: Producer) => {
    setSelected(hook);
    setSecret(null);
    setResult(null);
    setError(null);
    try {
      setDeliveries(
        (
          await call<{ deliveries: WebhookDelivery[] }>(
            `api/v1/producers/${hook.producer_id}/deliveries?limit=25`,
          )
        ).deliveries,
      );
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  async function act(message: string, run: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      await run();
      setNote(message);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1>Inbound webhooks</h1>
      <p className="lede">
        The provider-facing surface. A delivery is translated onto items and goes through the same
        write path as everything else — one admission check, one place the ACL is derived, one set
        of events.
      </p>
      {error && <p className="err">{error}</p>}
      {note && <p className="empty">{note}</p>}

      <section className="panel">
        <h2>Endpoints</h2>
        {hooks.length === 0 ? (
          <p className="empty">None yet.</p>
        ) : (
          <div className="excluded">
            {hooks.map((h) => (
              <div
                className={`item memrow${selected?.producer_id === h.producer_id ? " chosen" : ""}`}
                key={h.producer_id}
                onClick={() => void openHook(h)}
              >
                <span className={`chip ${h.status === "enabled" ? "on" : ""}`}>{h.status}</span>
                <code>{h.producer_id}</code>
                <span className="chip">{h.inbound_auth}</span>
                <span className="empty">
                  {h.seconds_since_last_item === null
                    ? "never received anything"
                    : `last delivery ${Math.round(h.seconds_since_last_item)}s ago`}
                </span>
              </div>
            ))}
          </div>
        )}
        <div className="row end" style={{ marginTop: 10 }}>
          <button
            disabled={busy}
            onClick={() =>
              act("Endpoint created. Give it a signing secret next.", async () => {
                const created = await call<{ producer_id: string }>("api/v1/producers", {
                  project_id: projectId,
                  type: "webhook",
                });
                await load();
                await openHook({
                  producer_id: created.producer_id,
                  type: "webhook",
                  status: "enabled",
                  inbound_auth: "none",
                  connection_scope: null,
                  seconds_since_last_item: null,
                });
              })
            }
          >
            Create an endpoint
          </button>
        </div>
        <p className="empty">
          <code>seconds_since_last_item</code> is the highest-value detector here: it catches a
          provider that stopped sending, which otherwise looks exactly like a quiet week.
        </p>
      </section>

      {selected && (
        <>
          <section className="panel">
            <h2>Give this to the provider</h2>
            <table className="kv">
              <tbody>
                <tr>
                  <td>URL</td>
                  <td>
                    <code>
                      {origin}/hooks/{selected.producer_id}
                    </code>
                  </td>
                </tr>
                <tr>
                  <td>method</td>
                  <td><code>POST</code> · <code>application/json</code></td>
                </tr>
                <tr><td>auth</td><td><code>{selected.inbound_auth}</code></td></tr>
                {selected.inbound_auth === "signature" && (
                  <>
                    <tr><td>signature header</td><td><code>X-Signature: &lt;hex&gt;</code></td></tr>
                    <tr>
                      <td>signed payload</td>
                      <td><code>HMAC-SHA256(secret, &quot;&#123;timestamp&#125;.&quot; + raw_body)</code></td>
                    </tr>
                    <tr>
                      <td>timestamp header</td>
                      <td><code>X-Signature-Timestamp</code> — 5 minute window</td>
                    </tr>
                  </>
                )}
                <tr>
                  <td>idempotency</td>
                  <td><code>X-Delivery-Id</code> — a retry with the same id is a no-op</td>
                </tr>
              </tbody>
            </table>

            <h3>Provider</h3>
            <div className="row">
              {["generic", "slack", "github", "stripe", "linear", "shopify", "twilio",
                "zoom", "microsoft_graph"].map((name) => (
                <button
                  key={name}
                  className="secondary"
                  disabled={busy}
                  onClick={() =>
                    act(`Preset applied: ${name}.`, async () => {
                      await call(
                        `api/v1/producers/${selected.producer_id}/inbound`,
                        {
                          inbound_auth: name === "microsoft_graph" ? "signature" : "signature",
                          mapping: { provider: name },
                        },
                        "PATCH",
                      );
                      setSelected({ ...selected, inbound_auth: "signature" });
                      await load();
                    })
                  }
                >
                  {name}
                </button>
              ))}
            </div>
            <p className="empty">
              A preset carries that provider&rsquo;s signature scheme, its handshake, its retry id
              and a sensible field mapping. Each signs a different string — getting it wrong fails
              closed and looks exactly like a bad secret.
            </p>

            <h3>Authentication</h3>
            <div className="row" style={{ marginTop: 4 }}>
              {["signature", "api_key", "url_secret", "none"].map((method) => (
                <button
                  key={method}
                  className={selected.inbound_auth === method ? "" : "secondary"}
                  disabled={busy}
                  onClick={() =>
                    act(`Authentication set to ${method}.`, async () => {
                      await call(
                        `api/v1/producers/${selected.producer_id}/inbound`,
                        { inbound_auth: method },
                        "PATCH",
                      );
                      await load();
                      setSelected({ ...selected, inbound_auth: method });
                    })
                  }
                >
                  {method}
                </button>
              ))}
              <button
                className="secondary"
                disabled={busy}
                onClick={() =>
                  act("New signing secret issued.", async () => {
                    const r = await call<{ signing_secret: string }>(
                      `api/v1/producers/${selected.producer_id}/signing-secret`,
                      {},
                    );
                    setSecret(r.signing_secret);
                    setSelected({ ...selected, inbound_auth: "signature" });
                    await load();
                  })
                }
              >
                Rotate signing secret
              </button>
            </div>
            {secret && (
              <div className="notice" style={{ marginTop: 12 }}>
                <strong>Shown once.</strong> <code>{secret}</code>
                <span className="empty">
                  {" "}
                  The previous secret keeps verifying until the next rotation, so rotating is not an
                  outage for deliveries already in flight.
                </span>
              </div>
            )}
            <p className="empty">
              Prefer <code>signature</code> where the provider supports it. A URL secret is
              unrevocable without re-registering the endpoint, and it leaks through logs and
              referrer headers.
            </p>
          </section>

          <section className="panel">
            <h2>Payload mapping</h2>
            <textarea value={mapping} onChange={(e) => setMapping(e.target.value)} />
            <div className="row end" style={{ marginTop: 10 }}>
              <button
                disabled={busy}
                onClick={() =>
                  act("Mapping saved.", async () => {
                    await call(
                      `api/v1/producers/${selected.producer_id}/inbound`,
                      { inbound_auth: selected.inbound_auth, mapping: JSON.parse(mapping) },
                      "PATCH",
                    );
                  })
                }
              >
                Save mapping
              </button>
            </div>
            <p className="empty">
              With no mapping the whole body is stored as one JSON item — which loses nothing and is
              always correct. A mapping that misses a field keeps the record anyway, so a schema
              change on the provider&rsquo;s side is not an outage on ours.
            </p>
          </section>

          <section className="panel">
            <h2>Send a test delivery</h2>
            <textarea value={payload} onChange={(e) => setPayload(e.target.value)} />
            <div className="row end" style={{ marginTop: 10 }}>
              <button
                disabled={busy}
                onClick={() =>
                  act("Delivery sent.", async () => {
                    setResult(
                      await call<Record<string, unknown>>(
                        `api/v1/producers/${selected.producer_id}/test-delivery`,
                        { payload: JSON.parse(payload) },
                      ),
                    );
                    await openHook(selected);
                  })
                }
              >
                {busy ? "Sending…" : "Send as the provider would"}
              </button>
            </div>
            {result && (
              <table className="kv" style={{ marginTop: 12 }}>
                <tbody>
                  <tr><td>status</td><td><code>{String(result.status)}</code></td></tr>
                  <tr><td>items created</td><td>{String(result.items)}</td></tr>
                  <tr><td>signed</td><td>{result.signed ? "yes — verified" : "no signature required"}</td></tr>
                  {result.reason ? <tr><td>reason</td><td>{String(result.reason)}</td></tr> : null}
                </tbody>
              </table>
            )}
            <p className="empty">
              It signs with the stored secret and goes through the real receive path, verification
              included — a test that skipped verification would pass for an endpoint whose signing
              is broken, which is the case worth catching.
            </p>
          </section>

          <section className="panel">
            <h2>Deliveries</h2>
            {deliveries.length === 0 ? (
              <p className="empty">Nothing has arrived yet.</p>
            ) : (
              <div className="excluded">
                {deliveries.map((d) => (
                  <div className="item" key={d.delivery_id}>
                    <span className={`chip${d.status === "accepted" ? " on" : d.status === "rejected" ? " warnchip" : ""}`}>
                      {d.status}
                    </span>
                    <code>{d.external_delivery_id ?? d.delivery_id}</code>
                    <span className="empty">{d.items} item{d.items === 1 ? "" : "s"}</span>
                    {d.signature_verified && <span className="chip">signature verified</span>}
                    {d.reason && <span className="why">{d.reason}</span>}
                    <span className="empty">{new Date(d.received_at).toLocaleTimeString()}</span>
                  </div>
                ))}
              </div>
            )}
            <p className="empty">
              Every delivery is recorded, including the ones that were rejected or dropped — which
              is what answers &ldquo;we sent it, did you get it?&rdquo; regardless of whether it
              worked.
            </p>
          </section>
        </>
      )}
    </>
  );
}
