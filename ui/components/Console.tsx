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

import { Fragment, useCallback, useEffect, useState } from "react";

import Capture, { humanBytes } from "./Capture";
import ThemeToggle from "./ThemeToggle";
import { WriteProgress, useTracked } from "./Progress";
import {
  ARMS,
  ArmKey,
  AuditTrail,
  Item,
  Membership,
  Alert,
  AlertRun,
  Memory,
  MemoryMember,
  MemoryTree,
  MemoryType,
  Algorithm,
  Backtest,
  CompactionJob,
  CompactionRun,
  FullVersion,
  ObservedEvent,
  Setting,
  Subscription,
  describeEvent,
  humanChars,
  isApproved,
  Stair,
  Trace,
  Version,
  call,
  describeTtl,
} from "@/lib/types";

/** `GET /data/{id}/erasure` — the artifact that proves a deletion completed. */
type Certificate = {
  data_id: string;
  purged_at: string | null;
  content_cleared: boolean;
  remaining: Record<string, number>;
  complete: boolean;
};

type Group = {
  group_id: string;
  name: string;
  managed_by: string | null;
  members: string[];
};

/** An organization member, as `GET /organizations/members` returns them. */
type Member = { user_id: string; email: string | null; role: string };

type Section =
  | "overview"
  | "add" | "update" | "search" | "ask" | "inbound" | "crawlers" | "mcp"
  | "memory" | "cases" | "entities" | "compaction" | "reprocess" | "workflows"
  | "alerts" | "standing"
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
/**
 * Grouped by the question being asked, and ordered by when it is asked.
 *
 * The earlier arrangement grew rather than being designed: `Data` was doing
 * four jobs at once — is it working, put it in, find it, and configure where it
 * comes from — `Alerts` was a heading holding one item, ingestion was split
 * between `Data` and `Admin`, and Compaction sat under `Organize` two groups
 * away from Deletion despite being the other half of the same lifecycle.
 *
 * Two rules hold it together now. **One heading, one question** — if a group
 * needs "and" to describe it, it is two groups. And **the order is the order
 * somebody arrives in**: *is this working* is what people open the console
 * with, so it is first rather than fifth inside something collapsed.
 */
const GROUPS: { title: string; items: { key: Section; label: string; hint: string }[] }[] = [
  {
    title: "Monitor",
    items: [
      { key: "overview", label: "Overview", hint: "is this working?" },
      { key: "alerts", label: "Alerts", hint: "tell me when this happens" },
      { key: "standing", label: "Standing queries", hint: "tell me when this arrives" },
    ],
  },
  {
    // Inbound, crawlers and producers answer one question -- is data still
    // arriving, and from where -- and used to sit in two different groups.
    title: "Sources",
    items: [
      { key: "inbound", label: "Inbound", hint: "webhooks providers post to" },
      { key: "crawlers", label: "Crawlers", hint: "pull what won't push" },
      { key: "producers", label: "Producers", hint: "freshness and status" },
    ],
  },
  {
    title: "Data",
    items: [
      { key: "add", label: "Add data", hint: "paste, upload or record" },
      // "Browse", not "Update": after the drill-down this screen is mostly
      // reading, and a label promising an edit makes people who want to look
      // skip it. Search finds by query; this walks by container.
      { key: "update", label: "Browse", hint: "by memory, down to one revision" },
      { key: "search", label: "Search", hint: "retrieve, with the trace" },
      { key: "ask", label: "Chat", hint: "ask your data, with citations" },
    ],
  },
  {
    title: "Organize",
    items: [
      { key: "memory", label: "Memories", hint: "lifecycle containers" },
      { key: "cases", label: "Cases", hint: "subjects and timelines" },
      { key: "workflows", label: "Workflows", hint: "where a long process is" },
      { key: "entities", label: "Entities", hint: "who and what, with evidence" },
    ],
  },
  {
    // Both halves of the same question -- how does a corpus stop growing, and
    // how does something leave for good. One folds and keeps; one erases and
    // proves it.
    title: "Lifecycle",
    items: [
      { key: "reprocess", label: "Interpret & rebuild", hint: "what is behind, in bulk" },
      { key: "compaction", label: "Compaction", hint: "fold a memory down, keep it all" },
      { key: "deletion", label: "Deletion", hint: "dry-run, then erase" },
    ],
  },
  {
    // Narrowed to proof. Deletion moved out: erasing is an operation you
    // perform, and the certificate it produces is what belongs here.
    title: "Governance",
    items: [
      { key: "audit", label: "Audit", hint: "who read and wrote what" },
      { key: "sharing", label: "Sharing", hint: "what is public, and revoke" },
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
  // Groups the reader can have open at once, not one.
  //
  // A single-open accordion hid two whole features: Compaction sits under
  // Organize and Alerts is its own group, and with only Data open neither
  // existed as far as anybody looking could tell. A nav that hides a feature
  // until you guess which heading it is behind is a nav that has not been
  // navigated.
  const [openGroups, setOpenGroups] = useState<string[]>(
    () => GROUPS.map((g) => g.title));
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
            const open = openGroups.includes(group.title);
            const current = group.items.some((i) => i.key === section);
            return (
              <div className="navgroup" key={group.title}>
                <button
                  className={`navtitle${current ? " current" : ""}`}
                  aria-expanded={open}
                  onClick={() => setOpenGroups(open
                    ? openGroups.filter((t) => t !== group.title)
                    : [...openGroups, group.title])}
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
              setOpenGroups((g) => g.includes("Organize") ? g : [...g, "Organize"]);
            }}
          />
        )}
        {section === "ask" && <AskSection projectId={projectId} />}
        {section === "inbound" && <InboundSection projectId={projectId} />}
        {section === "crawlers" && <CrawlersSection projectId={projectId} />}
        {section === "audit" && <Audit projectId={projectId} />}
        {section === "memory" && <MemorySection projectId={projectId} />}
        {section === "cases" && <CasesSection projectId={projectId} />}
        {section === "workflows" && <WorkflowsSection projectId={projectId} />}
        {section === "alerts" && <AlertsSection projectId={projectId} />}
        {section === "standing" && <StandingSection projectId={projectId} />}
        {section === "compaction" && <CompactionSection projectId={projectId} />}
        {section === "reprocess" && <ReprocessSection projectId={projectId} />}
        {section === "entities" && (
          <EntitiesSection
            projectId={projectId}
            focus={focusEntity}
            onSearchFor={(name) => {
              setSeededQuery(name);
              setSection("search");
              setOpenGroups((g) => g.includes("Data") ? g : [...g, "Data"]);
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
        {section === "producers" && <ProducersSection projectId={projectId} />}
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

/* The tracker moved to `Progress.tsx`, where it can be read by the sandbox
 * too. The loop that lived here polled sixty times, rendered nothing while it
 * did, and returned silently whether the item had arrived or never would. */

/* --------------------------------------------------------------- 1. add */

/** Ordered least to most visible — the same order `acl.py` ranks them by. */
const LEVELS = [
  { key: "private", rank: 0, label: "private — only me" },
  { key: "restricted", rank: 1, label: "restricted — only these principals" },
  { key: "shared", rank: 2, label: "shared — me and these principals" },
  { key: "org", rank: 3, label: "org — everyone in the organization" },
  { key: "public", rank: 4, label: "public — everyone in the org, and share links" },
] as const;

/**
 * How far a producer may widen, given its connection.
 *
 * A connection is a credential to somebody else's system and its scope is a
 * **ceiling**, not a default: a personal one caps its writes at `private`,
 * because personal data in a team organisation stays personal whatever the
 * project says. A producer with no connection is a direct client write and is
 * unrestricted — the caller is the owner, deciding about their own record.
 */
function ceilingFor(scope: string | null | undefined): number {
  if (scope === "personal") return 0;
  if (scope === "shared") return 3;
  return 4;
}

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
  // Enrichment is off by default at the API, and a write from here that does
  // not say otherwise stops at `stored` — durable, and invisible to search.
  // That is right for a producer pushing ten thousand records and wrong for a
  // person adding one item by hand and then looking for it, so this screen
  // asks, visibly, with the cost said out loud next to the switch.
  const [enrich, setEnrich] = useState(true);
  const [embed, setEmbed] = useState(true);
  const [summarize, setSummarize] = useState(true);
  // Per-request overrides. The API accepts both and persists neither, which is
  // the whole point of them -- a saved override would change what a project
  // does with no audit trail on the setting that appears to control it. That is
  // also why they are here rather than on Prompts, which is where a *saved*
  // instruction block belongs.
  const [promptOverride, setPromptOverride] = useState("");
  const [modelOverride, setModelOverride] = useState("");
  // Who can see it, decided at write time -- which is the only time it can be
  // decided, since the ACL is sealed before any other phase runs and there is
  // no endpoint that changes it afterwards. Re-writing the same external_id
  // with a different level is the only path, and it raises `acl.changed`.
  const [level, setLevel] = useState("");
  const [principals, setPrincipals] = useState<string[]>([]);
  // A transcript is not a document like the others: it is speech four people
  // did not publish to the company. Naming the room here is what lets one be
  // uploaded by hand without a provider integration at all.
  const [attendees, setAttendees] = useState("");
  const [room, setRoom] = useState<{ level: string; principals: string[];
                                     resolved: string[]; unresolved: string[] } | null>(null);
  const [audience, setAudience] = useState<{ groups: Group[]; members: Member[] }>(
    { groups: [], members: [] });
  // What this producer's connection permits. A connection's scope is a ceiling
  // on what its writes may publish, so offering a level the API will refuse
  // would turn a choice into a failed item and a message nobody expected.
  const [scope, setScope] = useState<string | null | undefined>(undefined);

  useEffect(() => {
    void Promise.all([
      call<{ groups: Group[] }>("api/v1/groups").catch(() => ({ groups: [] as Group[] })),
      call<{ members: Member[] }>("api/v1/organizations/members")
        .catch(() => ({ members: [] as Member[] })),
      call<{ producers: { producer_id: string; connection_scope: string | null }[] }>(
        "api/v1/producers").catch(() => ({ producers: [] })),
    ]).then(([g, m, p]) => {
      setAudience({ groups: g.groups, members: m.members });
      setScope(p.producers.find((x) => x.producer_id === producerId)?.connection_scope ?? null);
    });
  }, [producerId]);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { item, versions, climb, watching, elapsed, track, resume, enrichNow } =
    useTracked(onChange);

  // Said beside every control that causes the spend, because the panel that
  // configures it is further down the page and a person writes before they
  // scroll. "Stored only" is the sentence that would otherwise be discovered
  // afterwards, in a search that finds nothing.
  const ceiling = ceilingFor(scope);

  const willDo = !enrich
    ? "stored only — not searchable"
    : embed && summarize
      ? "will embed and summarise"
      : embed
        ? "will embed, no summary"
        : summarize
          ? "will summarise, not searchable"
          : "stored only — both steps unchecked";

  async function submit(content: Record<string, unknown>, externalId: string, label: string) {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      // A named room decides both the container and the visibility. Set here
      // rather than left to the general controls below, because a transcript
      // filed as `default` and visible to the project is the failure the
      // meeting type and its ninety-day retention exist to prevent.
      const meeting = room !== null && attendees.trim() !== "";
      const memory = meeting
        ? { type: "meeting", key: memoryKey || externalId }
        : memoryType || memoryKey
          ? { type: memoryType || "default", key: memoryKey || null }
          : undefined;
      const response = await call<{
        results: { data_id: string; memories: string[]; events: string[] }[];
      }>("api/v1/write", {
        producer_id: producerId,
        // `access` is a property of the item, not of the request: one write can
        // carry five hundred items with five hundred different ACLs.
        items: [{
          external_id: externalId, content, memory,
          access: meeting
            ? { level: room.level, principals: room.principals }
            : level
              ? { level, principals: level === "shared" || level === "restricted"
                                     ? principals : [] }
              : undefined,
        }],
        options: {
          enrich,
          enrichment: {
            embed,
            summarize,
            prompt: promptOverride.trim() || null,
            model_id: modelOverride.trim() || null,
          },
        },
      });
      const first = response.results[0];
      setNote(
        `${label} committed as ${first.data_id}, mapped into ${first.memories.length} memory(ies).`,
      );
      // The write is done the moment it returns — the button goes back to
      // being a button, and the climb is watched below rather than behind a
      // disabled control that looks like a hang.
      track(first.data_id);
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
          <span className={enrich ? "empty" : "warntext"}>{willDo}</span>
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
        <p className={enrich ? "empty" : "warntext"} style={{ marginTop: 0 }}>{willDo}</p>

        <div className="row">
          <label style={{ flex: 1 }}>
            Was this a meeting? Name the room
            <input
              type="text"
              value={attendees}
              placeholder="dana@example.com, priya@example.com — leave blank for anything else"
              onChange={(e) => { setAttendees(e.target.value); setRoom(null); }}
              onBlur={async () => {
                const list = attendees.split(/[,;\s]+/).map((a) => a.trim()).filter(Boolean);
                if (list.length === 0) { setRoom(null); return; }
                try {
                  setRoom(await call("api/v1/meetings/attendees", { attendees: list }));
                } catch (e) {
                  setError((e as Error).message);
                }
              }}
            />
          </label>
        </div>
        {room && (
          <>
            <p className="empty" style={{ marginTop: 0 }}>
              Filed as a <code>meeting</code> memory — <strong>ninety days, then archived</strong> —
              and written <strong>{room.level}</strong>
              {room.principals.length > 0
                ? ` to ${room.resolved.join(", ")}`
                : ", because nobody in the room is a member here"}
              . A transcript is unedited speech nobody reviewed before it was stored, which is why
              the retention is a default rather than a preference.
            </p>
            {room.unresolved.length > 0 && (
              <p className="warned">
                <strong>{room.unresolved.length} of {room.resolved.length + room.unresolved.length}{" "}
                did not resolve</strong> — {room.unresolved.join(", ")}. They are not members here,
                so no principal exists for them and they will not see this. That is the
                conservative direction; the alternative is inventing a principal for somebody
                outside the organisation.
              </p>
            )}
          </>
        )}

        <Capture
          busy={busy}
          onSubmit={(name, mime, base64, size) =>
            submit({ kind: "inline", bytes_b64: base64 }, name, `${name} (${humanBytes(size)})`)
          }
        />
      </section>

      <section className="panel">
        <h2>Interpretation</h2>
        <p className="empty" style={{ marginTop: 0 }}>
          Off, an item is stored and durable and <strong>nothing can find it</strong> — search runs
          on embeddings and there would be none. On, it costs one model call per chunk to embed and
          one more to summarise, metered against this project&rsquo;s budget.
        </p>
        <div className="row">
          <label className="check">
            <input type="checkbox" checked={enrich} onChange={(e) => setEnrich(e.target.checked)} />
            Interpret this write
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={embed}
              disabled={!enrich}
              onChange={(e) => setEmbed(e.target.checked)}
            />
            Embed — makes it searchable
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={summarize}
              disabled={!enrich}
              onChange={(e) => setSummarize(e.target.checked)}
            />
            Summarise — title, keywords, entities
          </label>
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          <input
            type="text"
            placeholder="prompt override — this write only, never saved"
            value={promptOverride}
            disabled={!enrich || !summarize}
            onChange={(e) => setPromptOverride(e.target.value)}
            style={{ flex: 2, minWidth: 240 }}
          />
          <input
            type="text"
            placeholder="model override — e.g. gemini-2.5-flash"
            value={modelOverride}
            disabled={!enrich || !summarize}
            onChange={(e) => setModelOverride(e.target.value)}
            style={{ flex: 1, minWidth: 180 }}
          />
        </div>
        <p className="empty" style={{ marginBottom: 0 }}>
          Both apply to this write alone and are never persisted as configuration. A locked org
          prompt still wins over either — otherwise a lock would be advisory. Saved instruction
          blocks live under <strong>Prompts</strong>; assignment per purpose lives under{" "}
          <strong>Models</strong>.
        </p>
        {!enrich && (
          <p className="warned" style={{ marginBottom: 0 }}>
            This write will stop at <code>stored</code>. You can ask for interpretation later, from
            the progress panel below or from Browse.
          </p>
        )}
      </section>

      <section className="panel">
        <h2>Who can see it</h2>
        <p className="empty" style={{ marginTop: 0 }}>
          Decided here because this is the only place it can be: the ACL is <strong>sealed before
          any other phase runs</strong>, and no endpoint changes it afterwards — re-writing the same
          external id with a different level is the only path, and it raises <code>acl.changed</code>.
        </p>
        {scope !== undefined && scope !== null && (
          <p className="warned">
            This producer writes through a <strong>{scope}</strong> connection, which is a{" "}
            <strong>ceiling</strong> rather than a default: it can narrow visibility and never
            widen it past <code>{LEVELS[ceiling].key}</code>. Personal data in a team organisation
            stays personal whatever the project says, and a wider level is refused rather than
            quietly stored as something narrower.
          </p>
        )}
        <div className="row">
          <label>
            Level
            <select value={level} onChange={(e) => { setLevel(e.target.value); setPrincipals([]); }}>
              <option value="">the producer&rsquo;s default</option>
              {LEVELS.map((l) => (
                <option key={l.key} value={l.key} disabled={ceiling < l.rank}>
                  {l.label}{ceiling < l.rank ? " — not allowed by this connection" : ""}
                </option>
              ))}
            </select>
          </label>
        </div>
        {(level === "restricted" || level === "shared") && (
          <>
            <div className="row" style={{ marginTop: 8 }}>
              {audience.groups.map((g) => (
                <label className="check" key={g.group_id}
                       title={`${g.members.length} member${g.members.length === 1 ? "" : "s"}`}>
                  <input
                    type="checkbox"
                    /* `group:` and `user:` prefixes, because that is the shape
                     * `acl_principals()` builds and the predicate compares
                     * against. A bare id matches nothing -- including for the
                     * person who wrote the record, who then cannot see their
                     * own item and has no way to tell why. */
                    checked={principals.includes(`group:${g.group_id}`)}
                    onChange={(e) => setPrincipals(e.target.checked
                      ? [...principals, `group:${g.group_id}`]
                      : principals.filter((x) => x !== `group:${g.group_id}`))}
                  />
                  {g.name} <span className="empty">· group of {g.members.length}</span>
                </label>
              ))}
              {audience.members.map((m) => (
                <label className="check" key={m.user_id}>
                  <input
                    type="checkbox"
                    checked={principals.includes(`user:${m.user_id}`)}
                    onChange={(e) => setPrincipals(e.target.checked
                      ? [...principals, `user:${m.user_id}`]
                      : principals.filter((x) => x !== `user:${m.user_id}`))}
                  />
                  {m.email ?? m.user_id}
                </label>
              ))}
            </div>
            {principals.length === 0 && (
              <p className="warned">
                <strong>{level}</strong> needs at least one principal, and the write is refused
                without one rather than quietly stored as something narrower.
              </p>
            )}
            <p className="empty" style={{ marginBottom: 0 }}>
              A group resolves inside the ACL query like a person does, so adding somebody to it
              later gives them this record too — which is the reason to prefer one over naming
              three people. Create groups under <strong>Projects &amp; members</strong>.
            </p>
          </>
        )}
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

      {error && <p className="err">{error}</p>}
      {(watching || item) && (
        <section className="panel">
          <h2>What happened to it</h2>
          {note && <p className="empty" style={{ marginTop: 0 }}>{note}</p>}
          <WriteProgress
            climb={climb}
            watching={watching}
            elapsed={elapsed}
            dataId={item?.data_id ?? null}
            onEnrich={enrichNow}
            onResume={resume}
          />
          {item && <ItemDetail item={item} versions={versions} />}
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

/**
 * Browse and update — four levels, one at a time.
 *
 * It used to load fifty items with every revision expanded, which answers a
 * question nobody asked: *show me everything*. What people actually do is
 * narrow, then look, then look closer. So the screen is a path —
 * **scope → page of items → one item → one revision** — and each step shows only
 * what is needed to choose the next.
 *
 * The scope is a memory rather than a filter box because that is the container
 * people already think in, and it is the one axis the corpus is organised on.
 */
function UpdateData({
  projectId,
  producerId,
  onChange,
}: {
  projectId: string;
  producerId: string;
  onChange: () => Promise<void>;
}) {
  const PAGE = 15;

  const [memories, setMemories] = useState<Memory[]>([]);
  const [scope, setScope] = useState<string>("");          // "" = everything
  const [stateFilter, setStateFilter] = useState<string>("");
  const [rows, setRows] = useState<Item[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [more, setMore] = useState(false);

  const [selected, setSelected] = useState<Item | null>(null);
  const [versions, setVersions] = useState<Version[]>([]);
  const [showing, setShowing] = useState<FullVersion | null>(null);
  const [loadingVersion, setLoadingVersion] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState(false);

  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void call<{ memories: Memory[] }>(`api/v1/projects/${projectId}/memories`)
      .then((r) => setMemories(r.memories))
      .catch(() => setMemories([]));
  }, [projectId]);

  const loadPage = useCallback(async (append: boolean) => {
    setError(null);
    try {
      if (scope) {
        // A memory's members are its own listing; there is no cursor on it, so
        // the page size is applied here rather than pretended at.
        const page = await call<{ members: MemoryMember[] }>(
          `api/v1/memories/${scope}/members`);
        const ids = page.members.map((m) => m.data_id);
        const items = await Promise.all(
          ids.slice(0, PAGE).map((id) => call<Item>(`api/v1/data/${id}`).catch(() => null)));
        setRows(items.filter(Boolean) as Item[]);
        setMore(false);
        setCursor(null);
        return;
      }
      const qs = new URLSearchParams({ limit: String(PAGE) });
      if (append && cursor) qs.set("before", cursor);
      if (stateFilter) qs.set("state", stateFilter);
      const page = await call<{ items: Item[] }>(
        `api/v1/projects/${projectId}/data?${qs.toString()}`);
      const next = append ? [...rows, ...page.items] : page.items;
      setRows(next);
      setCursor(page.items.length ? page.items[page.items.length - 1].data_id : null);
      // A full page means there is probably another; an empty or short one is
      // the end. Said plainly rather than left for a button that does nothing.
      setMore(page.items.length === PAGE);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [projectId, scope, stateFilter, cursor, rows]);

  useEffect(() => {
    setSelected(null); setShowing(null); setCursor(null);
    void loadPage(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, scope, stateFilter]);

  async function open(dataId: string) {
    setError(null); setNote(null); setShowing(null); setEditing(false);
    try {
      const item = await call<Item>(`api/v1/data/${dataId}`);
      setSelected(item);
      setDraft(item.content_text ?? item.extracted_text ?? "");
      setVersions((await call<{ versions: Version[] }>(
        `api/v1/data/${dataId}/versions`)).versions);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function openVersion(v: Version) {
    if (showing?.version_id === v.version_id) { setShowing(null); return; }
    setLoadingVersion(v.version_id); setError(null);
    try {
      // A second request on purpose: the listing previews at 400 characters, so
      // the full text is fetched only for the revision actually chosen.
      setShowing(await call<FullVersion>(
        `api/v1/data/${selected!.data_id}/versions/${v.version_id}`));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoadingVersion(null);
    }
  }

  async function save() {
    if (!selected) return;
    setBusy(true); setError(null);
    try {
      // The same write endpoint: an external_id that already exists updates in
      // place. There is no separate update path to drift from the write path.
      const response = await call<{ results: { data_id: string; status: string }[] }>(
        "api/v1/write",
        { producer_id: producerId,
          items: [{ external_id: selected.external_id,
                    content: { kind: "inline", text: draft } }],
          // Without this the revision lands and nothing re-reads it: the
          // embedding still indexes the text this edit replaced, so search
          // keeps matching on words the item no longer contains.
          options: { enrich: true } },
      );
      const first = response.results[0];
      setNote(first.status === "updated"
        ? "Saved as a new revision. The previous text is still readable below."
        : "That key did not exist, so this created a new item.");
      setEditing(false);
      await open(first.data_id);
      await onChange();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  /**
   * Ask for the interpretation the write did not.
   *
   * The state alone says where an item stopped, never why, so the note says
   * what was asked for rather than claiming what will happen — a refusal or an
   * exhausted budget lands the same way and neither is this screen's to promise.
   */
  async function interpret() {
    if (!selected) return;
    setBusy(true); setError(null);
    try {
      await call(`api/v1/data/${selected.data_id}/enrich`,
                 { embed: true, summarize: true });
      setNote("Interpretation requested. It runs off the write path, so reopen the item in a "
              + "moment to see whether it reached enriched.");
      await onChange();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const scopeName = scope
    ? memories.find((m) => m.memory_id === scope)
    : null;

  return (
    <>
      <h1>Browse and update</h1>
      <p className="lede">
        Narrow to a memory, open an item, then open a revision. Each step shows
        only what you need to choose the next one.
      </p>
      {error && <p className="err">{error}</p>}
      {note && <p className="note">{note}</p>}

      {/* ---- 1. scope ---- */}
      <section className="panel">
        <h2>Where to look</h2>
        <div className="row">
          <label>Memory
            <select value={scope} onChange={(e) => setScope(e.target.value)}>
              <option value="">everything in this project</option>
              {memories.map((m) => (
                <option key={m.memory_id} value={m.memory_id}>
                  {m.title || m.memory_key || m.memory_id} · {m.type} ({m.members})
                </option>
              ))}
            </select>
          </label>
          {!scope && (
            <label>Readiness
              <select value={stateFilter} onChange={(e) => setStateFilter(e.target.value)}>
                <option value="">any</option>
                <option value="stored">stored — not searchable yet</option>
                <option value="searchable">searchable</option>
                <option value="enriched">enriched</option>
              </select>
            </label>
          )}
        </div>
      </section>

      {/* ---- 2. a page of items ---- */}
      {!selected && (
        <section className="panel">
          <h2>
            {rows.length} item{rows.length === 1 ? "" : "s"}
            {scopeName ? ` in ${scopeName.title || scopeName.memory_key}` : ""}
            {more ? " so far" : ""}
          </h2>
          {rows.length === 0 ? (
            <p className="empty">
              {scope ? "This memory has nothing in it yet."
               : stateFilter ? `Nothing is ${stateFilter}.`
               : "This project has no data yet — add some first."}
            </p>
          ) : (
            <>
              <div className="excluded">
                {rows.map((r) => (
                  <button className="memrow" key={r.data_id} onClick={() => void open(r.data_id)}>
                    <span>{r.external_id ?? r.data_id}</span>
                    <span className={`chip ${r.state}`}>{r.state}</span>
                    <span className="empty far">{r.data_type}</span>
                  </button>
                ))}
              </div>
              {more && (
                <button disabled={busy} onClick={() => void loadPage(true)}>
                  Load {PAGE} more
                </button>
              )}
            </>
          )}
        </section>
      )}

      {/* ---- 3. one item ---- */}
      {selected && (
        <>
          <button className="backlink" onClick={() => { setSelected(null); setShowing(null); }}>
            ← Back to the list
          </button>
          <section className="panel">
            <h2>{selected.external_id ?? selected.data_id}</h2>
            <ItemDetail item={selected} versions={versions} />
            <StoredMedia item={selected} />

            {!editing ? (
              <>
                <h3>Current text</h3>
                <pre className="excerpt">
                  {selected.content_text ?? selected.extracted_text ?? "—"}
                </pre>
                <div className="row">
                  <button onClick={() => setEditing(true)}>Edit</button>
                  {/* Enrichment is opt-in, so a corpus contains items that were
                    * recorded and never interpreted -- stored, durable, and
                    * invisible to search. Until now the console could see that
                    * state and do nothing about it. */}
                  {selected.state !== "enriched" && (
                    <button
                      disabled={busy}
                      title="Embeds and summarises this item. One model call per chunk, plus one for the summary."
                      onClick={() => void interpret()}
                    >
                      {selected.state === "stored"
                        ? "Interpret it — it is not searchable yet"
                        : "Summarise it"}
                    </button>
                  )}
                </div>
              </>
            ) : (
              <>
                <h3>Edit</h3>
                <p className="hint">
                  Saving writes a new revision through the same write endpoint —
                  the previous text stays readable below.
                </p>
                <textarea rows={10} value={draft}
                          onChange={(e) => setDraft(e.target.value)} />
                <div className="row">
                  <button disabled={busy} onClick={() => void save()}>Save as a revision</button>
                  <button disabled={busy} onClick={() => setEditing(false)}>Cancel</button>
                </div>
              </>
            )}
          </section>

          {/* ---- 4. one revision ---- */}
          <section className="panel">
            <h2>{versions.length} revision{versions.length === 1 ? "" : "s"}</h2>
            <p className="hint">
              An item&rsquo;s text is produced by a pipeline, and the pipeline
              changes. Open one to read exactly what it said.
            </p>
            <div className="excluded">
              {versions.map((v) => (
                <button className="memrow" key={v.version_id}
                        onClick={() => void openVersion(v)}>
                  <span>#{v.revision} · {v.source}</span>
                  <span className="empty">{v.model_id ?? "no model"}</span>
                  <span className="empty">{v.content_chars} chars</span>
                  <span className="empty far">{new Date(v.created_at).toLocaleString()}</span>
                </button>
              ))}
            </div>
            {loadingVersion && <p className="hint">Reading revision…</p>}
            {showing && (
              <>
                <h3>Revision {showing.revision} — {showing.source}</h3>
                <p className="hint">
                  {showing.model_id ? `Produced by ${showing.model_id}. ` : ""}
                  This is the whole text as that revision stored it, not the
                  preview.
                </p>
                <pre className="excerpt">
                  {showing.content_text ?? "(this revision stored no text)"}
                </pre>
              </>
            )}
          </section>
        </>
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
      <h1>Entities</h1>
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

type ConnectorScope = {
  key: string; label: string; placeholder: string; help: string;
};
type Connector = {
  key: string; label: string; category: string; pulls: string;
  auth_style: string; auth_name: string | null; auth_help: string;
  available: boolean; requires: string | null; verified: boolean;
  exercised_against: string | null; notes: string;
  scopes: ConnectorScope[];
};

type Connection = {
  connection_id: string;
  provider: string;
  scope: string;
  auth_style: string;
  auth_name: string | null;
  has_credential: boolean;
};

type Crawler = {
  crawler_id: string;
  name: string;
  strategy: string;
  enabled: boolean;
  connection_id?: string | null;
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

const PRESETS: Record<string,
  { label: string; blurb: string; placeholder: string; build: (v: string) => object }> = {
  feed: {
    label: "Feed",
    placeholder: "Feed URL — https://example.com/rss.xml",
    blurb: "An RSS, Atom or sitemap index someone else already maintains.",
    build: (v) => ({ name: "Feed", strategy: "feed", seeds: [v],
                     incremental: "watermark" }),
  },
  traverse: {
    label: "Website",
    placeholder: "Seed page — https://docs.example.com/",
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
    placeholder: "Endpoint — https://api.example.com/v1/items",
    blurb: "A templated request with declared pagination — no adapter needed.",
    build: (v) => ({ name: "API", strategy: "http", request: { method: "GET", url: v },
                     extract: { items_path: "@" } }),
  },
  tree: {
    label: "Drive folder",
    placeholder: "Folder ID — 1AbCdEf… (from the folder's URL)",
    blurb:
      "Walk a Google Drive folder and everything under it. Needs a service-account " +
      "credential below. For SharePoint or OneDrive, use the app catalog.",
    build: (v) => ({
      name: "Drive", strategy: "tree",
      tree: { api: "google_drive", root: v },
      // Shallower and smaller than the default: a drive nobody has pruned is
      // where an unbounded first run finds forty thousand files, and the dry
      // run is meant to tell you that before the live one does.
      limits: { max_depth: 3, max_items: 200 },
      incremental: "watermark",
    }),
  },
};

type Cond = { field: string; op: string; values: string };

const OPS: [string, string][] = [
  ["in", "is one of"], ["not_in", "is not one of"], ["eq", "equals"],
  ["ne", "does not equal"], ["contains", "contains"], ["gt", "is greater than"],
  ["lt", "is less than"], ["exists", "is present"], ["absent", "is absent"],
];

/** The stored shape back into rows, so editing shows what was actually saved. */
function condsFrom(where: Record<string, unknown>): Cond[] {
  const rows = Object.entries(where ?? {}).map(([field, c]) => {
    if (c && typeof c === "object" && !Array.isArray(c)) {
      const o = c as { op?: string; value?: unknown };
      if (o.op === "exists") {
        return { field, op: o.value ? "exists" : "absent", values: "" };
      }
      return { field, op: o.op ?? "in",
               values: (Array.isArray(o.value) ? o.value : [o.value]).join(", ") };
    }
    return { field, op: "in", values: (c as unknown[]).join(", ") };
  });
  return rows.length ? rows : [{ field: "", op: "in", values: "" }];
}

/**
 * One form for creating and for editing, because they are the same decision.
 *
 * A separate create form drifts from the edit form, and then the two disagree
 * about what an alert can even be — which is how a screen ends up unable to
 * express something the API has always accepted.
 */
function AlertEditor({
  alert, surfaces, vocabulary, scopeOptions, busy, runs, events, backtest, detailTab, onTab,
  onBack, onSave, onBacktest, onToggle, onDelete,
}: {
  alert: Alert | null;
  surfaces: Record<string, string[]>;
  vocabulary: { predicates: string[]; single_valued: string[] };
  scopeOptions: { memories: Memory[]; cases: { case_id: string; title: string | null;
                  external_id: string }[]; producers: { producer_id: string;
                  type: string }[] };
  busy: boolean;
  runs: AlertRun[];
  events: ObservedEvent[];
  backtest?: Backtest;
  detailTab: "definition" | "firings";
  onTab: (t: "definition" | "firings") => void;
  onBack: () => void;
  onSave: (body: Record<string, unknown>) => void;
  onBacktest: () => void;
  onToggle: () => void;
  onDelete: () => void;
}) {
  const [name, setName] = useState(alert?.name ?? "");
  const [surface, setSurface] = useState(alert?.surface ?? "fact.superseded");
  const [mode, setMode] = useState<"rule" | "llm">(alert?.mode ?? "rule");
  const [describe, setDescribe] = useState(alert?.describe ?? "");
  const [conds, setConds] = useState<Cond[]>(
    alert ? condsFrom(alert.where) : [{ field: "predicate", op: "in", values: "located_in" }]);
  const [scopeKind, setScopeKind] = useState<string>(
    Object.keys(alert?.scope ?? {})[0] ?? "");
  const [scopeValue, setScopeValue] = useState<string>(
    Object.values(alert?.scope ?? {})[0] ?? "");
  const [debounce, setDebounce] = useState(alert?.debounce_seconds ?? 5);
  const [cap, setCap] = useState(alert?.batch_cap ?? 500);
  const [advanced, setAdvanced] = useState(false);

  const fields = surfaces[surface] ?? [];
  const approved = alert ? isApproved(alert) : false;

  const body = () => {
    const where: Record<string, unknown> = {};
    for (const c of conds) {
      if (!c.field) continue;
      const values = c.values.split(",").map((v) => v.trim()).filter(Boolean);
      if (c.op === "exists") where[c.field] = { op: "exists", value: true };
      else if (c.op === "absent") where[c.field] = { op: "exists", value: false };
      else if (values.length) where[c.field] = { op: c.op, value: values };
    }
    return {
      name: name || `${surface} watcher`, surface, mode, where,
      describe: mode === "llm" ? describe : null,
      scope: scopeKind && scopeValue ? { [scopeKind]: scopeValue } : {},
      debounce_seconds: debounce, batch_cap: cap,
    };
  };

  return (
    <div className="stack">
      <button className="backlink" onClick={onBack}>← All alerts</button>

      {alert && (
        <>
          <div className="toolbar">
            <h2 className="grow" style={{ margin: 0 }}>
              <span className={`dot ${alertState(alert)}`} />{" "}{alert.name}
            </h2>
            <button disabled={busy} onClick={onBacktest}>Replay against history</button>
            <button disabled={busy || (!alert.enabled && !approved)}
                    title={!approved
                      ? "Replay it against history first — an alert that has never been run is a guess"
                      : undefined}
                    onClick={onToggle}>
              {alert.enabled ? "Stop" : "Start"}
            </button>
            <button disabled={busy} onClick={onDelete}>Delete</button>
          </div>
          {!approved && (
            <p className="warn">
              Edited since it was last replayed, so it is stopped and cannot start
              until you replay it again. That gate is the only thing between a
              changed definition and a page at three in the morning.
            </p>
          )}
          <div className="subtabs">
            <button className={`subtab ${detailTab === "definition" ? "on" : ""}`}
                    onClick={() => onTab("definition")}>Definition</button>
            <button className={`subtab ${detailTab === "firings" ? "on" : ""}`}
                    onClick={() => onTab("firings")}>
              Firing history <span className="count">{events.length}</span>
            </button>
          </div>
        </>
      )}

      {(!alert || detailTab === "definition") && (
        <div className="card">
          <h2>{alert ? "Definition" : "New alert"}</h2>
          {!alert && (
            <p className="hint">
              It watches from now on, not backwards — one that fired a hundred
              times about last month the moment you saved it is one you would turn
              straight back off. Replay it against history to look back instead.
            </p>
          )}

          <label>Name
            <input value={name} placeholder="a person relocates"
                   onChange={(e) => setName(e.target.value)} />
          </label>

          <label>When
            <select value={surface} onChange={(e) => {
              setSurface(e.target.value);
              setConds([{ field: (surfaces[e.target.value] ?? [])[0] ?? "",
                          op: "in", values: "" }]);
            }}>
              {Object.keys(surfaces).map((k) => <option key={k} value={k}>{k}</option>)}
            </select>
          </label>

          <h3>and all of these hold</h3>
          <p className="hint">
            A field, or a dotted path into the event — <code>detail.score</code>
            {" "}reaches a value this console has never seen, so a new kind of
            event needs no change here.
          </p>
          {conds.map((c, i) => (
            <div key={i} className="row">
              <input value={c.field} list="alert-fields" placeholder="field or a.dotted.path"
                     onChange={(e) => {
                       const n = [...conds]; n[i] = { ...c, field: e.target.value }; setConds(n);
                     }} />
              <select value={c.op} onChange={(e) => {
                const n = [...conds]; n[i] = { ...c, op: e.target.value }; setConds(n);
              }}>
                {OPS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
              {c.op !== "exists" && c.op !== "absent" && (
                <input value={c.values} placeholder="one or more, comma separated"
                       /* The predicate vocabulary is closed and the server
                        * serves it, so it is offered here rather than typed
                        * twice -- the editor shipped with `located_in` written
                        * into it, which is the copy that goes stale. A datalist
                        * rather than a select because a dotted path can reach a
                        * value this console has never seen. */
                       list={c.field === "predicate" ? "graph-predicates" : undefined}
                       onChange={(e) => {
                         const n = [...conds]; n[i] = { ...c, values: e.target.value };
                         setConds(n);
                       }} />
              )}
              <button disabled={conds.length === 1}
                      title={conds.length === 1 ? "At least one condition is required" : undefined}
                      onClick={() => setConds(conds.filter((_, j) => j !== i))}>
                Remove
              </button>
            </div>
          ))}
          <datalist id="alert-fields">
            {fields.map((f) => <option key={f} value={f} />)}
          </datalist>
          <datalist id="graph-predicates">
            {vocabulary.predicates.map((pd) => <option key={pd} value={pd} />)}
          </datalist>
          {conds.some((c) => c.field === "predicate") && vocabulary.single_valued.length > 0 && (
            <p className="hint">
              <strong>Single-valued:</strong> {vocabulary.single_valued.join(", ")}. A second one of
              these closes the first, so it fires <code>fact.superseded</code>; every other
              predicate accumulates and fires <code>fact.asserted</code>.
            </p>
          )}
          <button onClick={() => setConds([...conds, { field: "", op: "in", values: "" }])}>
            Add condition
          </button>

          <h3>and only within</h3>
          <p className="hint">
            Optional. This is a different question from the conditions above —
            they ask about the event, this asks whether the subject is one you
            care about at all. Leave it as everything and the alert watches the
            whole project.
          </p>
          <div className="row">
            <select value={scopeKind} onChange={(e) => {
              setScopeKind(e.target.value); setScopeValue("");
            }}>
              <option value="">everything in this project</option>
              <option value="memory_id">one memory</option>
              <option value="case_id">one case</option>
              <option value="producer_id">one source</option>
              <option value="entity_id">one entity</option>
            </select>
            {scopeKind === "memory_id" && (
              <>
                <select value={scopeValue} onChange={(e) => setScopeValue(e.target.value)}>
                  <option value="">choose a memory…</option>
                  {scopeOptions.memories.map((m) => (
                    <option key={m.memory_id} value={m.memory_id}>
                      {m.title || m.memory_key || m.memory_id} · {m.type}
                    </option>
                  ))}
                </select>
                {/* A scope that silently means more than it says is this
                  * screen's original sin repeated, so a hierarchical one says
                  * so before it is saved rather than after it fires. */}
                {scopeValue && <ScopeReach memoryId={scopeValue} />}
              </>
            )}
            {scopeKind === "case_id" && (
              <select value={scopeValue} onChange={(e) => setScopeValue(e.target.value)}>
                <option value="">choose a case…</option>
                {scopeOptions.cases.map((c) => (
                  <option key={c.case_id} value={c.case_id}>
                    {c.title || c.external_id}
                  </option>
                ))}
              </select>
            )}
            {scopeKind === "producer_id" && (
              <select value={scopeValue} onChange={(e) => setScopeValue(e.target.value)}>
                <option value="">choose a source…</option>
                {scopeOptions.producers.map((pr) => (
                  <option key={pr.producer_id} value={pr.producer_id}>
                    {pr.producer_id} · {pr.type}
                  </option>
                ))}
              </select>
            )}
            {scopeKind === "entity_id" && (
              <input value={scopeValue} placeholder="ent_…"
                     onChange={(e) => setScopeValue(e.target.value)} />
            )}
          </div>
          {scopeKind && !scopeValue && (
            <p className="warn">Pick one, or set it back to everything.</p>
          )}

          <h3>How it decides</h3>
          <div className="row">
            <label><input type="radio" checked={mode === "rule"}
                          onChange={() => setMode("rule")} /> Rules only — no model, no cost</label>
            <label><input type="radio" checked={mode === "llm"}
                          onChange={() => setMode("llm")} /> Rules, then judge the rest in words</label>
          </div>
          {mode === "llm" && (
            <>
              <label>Only alert me when
                <textarea rows={3} value={describe}
                          placeholder="a customer signals they may leave"
                          onChange={(e) => setDescribe(e.target.value)} />
              </label>
              <p className="hint">
                The conditions above still run first and decide what the model is
                shown, which is why they stay required. Everything that survives is
                judged in <strong>one call per run</strong>, not one per event. If
                no model is available the run defers rather than guessing.
              </p>
            </>
          )}

          <p>
            <button className="backlink" onClick={() => setAdvanced(!advanced)}>
              {advanced ? "Hide" : "Show"} timing and limits
            </button>
          </p>
          {advanced && (
            <div className="stack">
              <label>Wait before evaluating (seconds)
                <input type="number" min={1} value={debounce}
                       onChange={(e) => setDebounce(Number(e.target.value))} />
              </label>
              <p className="hint">
                A burst of a thousand writes becomes one evaluation rather than a
                thousand.
              </p>
              <label>Most events looked at per run
                <input type="number" min={1} value={cap}
                       onChange={(e) => setCap(Number(e.target.value))} />
              </label>
              <p className="hint">
                Anything over this is carried to the next run and reported as left
                over — never dropped quietly.
              </p>
            </div>
          )}

          <button disabled={busy} onClick={() => onSave(body())}>
            {alert ? "Save changes" : "Create alert"}
          </button>
        </div>
      )}

      {backtest && (!alert || detailTab === "definition") && (
        <div className="card">
          <h2>Replay — nothing was recorded and nothing was sent</h2>
          <p>
            <strong>{backtest.matches}</strong> of {backtest.candidates} past
            {" "}<code>{surface}</code> event
            {backtest.candidates === 1 ? "" : "s"} would have fired this.
            {backtest.deferred > 0 && ` ${backtest.deferred} more were not examined.`}
          </p>
          {backtest.matches === 0 ? (
            <p className="hint">
              Nothing matched — either it has not happened yet, or the conditions
              are narrower than you meant.
            </p>
          ) : (
            <table className="kv">
              <thead><tr><th>When</th><th>What would have fired</th></tr></thead>
              <tbody>
                {backtest.samples.map((sm) => (
                  <tr key={sm.sequence}>
                    <td>{new Date(sm.occurred_at).toLocaleString()}</td>
                    <td>{describeEvent(surface, sm.payload)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {backtest.matches > backtest.sampled && (
            <p className="hint">Showing the first {backtest.sampled}.</p>
          )}
        </div>
      )}

      {alert && detailTab === "firings" && (
        <>
          <div className="card">
            <h2>What it caught</h2>
            {events.length === 0 ? (
              <p className="hint">
                Nothing yet. {alert.enabled
                  ? "It is running — it reports only what happens from now on."
                  : "It is stopped, so nothing is being watched."}
              </p>
            ) : (
              <table className="kv">
                <thead><tr><th>When</th><th>What happened</th><th>Told</th></tr></thead>
                <tbody>
                  {events.slice().reverse().map((e) => (
                    <tr key={e.event_id}>
                      <td>{new Date(e.occurred_at).toLocaleString()}</td>
                      <td>{describeEvent(e.surface, e.payload)}</td>
                      <td>
                        {e.deliveries === 0 ? <span className="hint">no endpoint</span>
                         : e.undeliverable > 0 ? <span className="warn">undeliverable</span>
                         : e.delivered > 0 ? <span className="ok">sent</span> : "queued"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          <div className="card">
            <h2>Every time it ran</h2>
            <p className="hint">
              A run that looked at nothing is normal — the sweep wakes every
              minute whether or not anything happened.
            </p>
            {runs.length === 0 ? <p className="hint">It has not run yet.</p> : (
              <table className="kv">
                <thead>
                  <tr><th>When</th><th>Ran because</th><th>Looked at</th><th>Fired</th>
                      <th>Left over</th><th>Cost</th></tr>
                </thead>
                <tbody>
                  {runs.map((r) => (
                    <tr key={r.run_id}>
                      <td>{new Date(r.started_at).toLocaleString()}</td>
                      <td>{r.trigger === "tick" ? "the sweep"
                         : r.trigger === "consumer" ? "something happened" : "a replay"}</td>
                      <td>{r.candidates}</td>
                      <td>{r.matches > 0 ? <strong>{r.matches}</strong> : "0"}</td>
                      <td>{r.deferred > 0
                        ? <span className="warn">{r.deferred}</span> : "—"}</td>
                      <td>{r.model_calls > 0
                        ? `${r.model_calls} model call${r.model_calls === 1 ? "" : "s"}`
                        : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </div>
  );
}


/**
 * Compaction — schedule a memory to be folded down, and watch what it freed.
 *
 * The screen has one job beyond configuring a job: making it obvious that
 * **nothing is deleted**. A person about to schedule something that removes
 * records from their working set needs that stated where they are looking, not
 * in a document — so the preview is a gate rather than a courtesy, and every
 * run reports what it archived rather than what it removed.
 */
function CompactionSection({ projectId }: { projectId: string }) {
  const [jobs, setJobs] = useState<CompactionJob[]>([]);
  const [algorithms, setAlgorithms] = useState<Record<string, Algorithm>>({});
  const [memories, setMemories] = useState<Memory[]>([]);
  const [runs, setRuns] = useState<Record<string, CompactionRun[]>>({});
  const [preview, setPreview] = useState<Record<string, CompactionRun & {
    samples?: { external_id: string; chars: number }[]; note?: string }>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [composing, setComposing] = useState(false);
  const [open, setOpen] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [memoryId, setMemoryId] = useState("");
  const [algorithm, setAlgorithm] = useState("dedupe");
  const [every, setEvery] = useState(86400);
  const [scheduled, setScheduled] = useState(false);

  const load = useCallback(async () => {
    try {
      const [list, algos, mem] = await Promise.all([
        call<{ jobs: CompactionJob[] }>(`api/v1/projects/${projectId}/compaction/jobs`),
        call<{ algorithms: Record<string, Algorithm> }>("api/v1/compaction/algorithms"),
        call<{ memories: Memory[] }>(`api/v1/projects/${projectId}/memories`),
      ]);
      setJobs(list.jobs);
      setAlgorithms(algos.algorithms);
      setMemories(mem.memories);
      if (!memoryId && mem.memories.length) setMemoryId(mem.memories[0].memory_id);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [projectId, memoryId]);

  useEffect(() => { void load(); }, [load]);

  const act = async (fn: () => Promise<unknown>, message?: string) => {
    setBusy(true); setError(null); setNote(null);
    try { await fn(); if (message) setNote(message); await load(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  const approved = (j: CompactionJob) => j.dry_run_version === j.config_version;
  const chosen = algorithms[algorithm];

  return (
    <section className="stack">
      <h1>Compaction</h1>
      <p className="lede">
        Fold a memory down so the working set stops growing. <strong>Nothing is
        deleted</strong> — members are archived, which takes them out of the
        default view and leaves them readable and searchable when asked for.
      </p>
      {error && <p className="err">{error}</p>}
      {note && <p className="note">{note}</p>}

      <div className="toolbar">
        <span className="grow" />
        <button onClick={() => setComposing(!composing)}>
          {composing ? "Cancel" : "New compaction"}
        </button>
      </div>

      {composing && (
        <div className="card">
          <h2>New compaction</h2>
          <label>Name
            <input value={name} placeholder="nightly de-duplication"
                   onChange={(e) => setName(e.target.value)} />
          </label>
          <label>Memory
            <select value={memoryId} onChange={(e) => setMemoryId(e.target.value)}>
              {memories.map((m) => (
                <option key={m.memory_id} value={m.memory_id}>
                  {m.title || m.memory_key || m.memory_id} · {m.type} ({m.members})
                </option>
              ))}
            </select>
          </label>
          <label>How
            <select value={algorithm} onChange={(e) => setAlgorithm(e.target.value)}>
              {Object.entries(algorithms).map(([k, a]) => (
                <option key={k} value={k}>{a.label}</option>
              ))}
            </select>
          </label>
          {chosen && (
            <p className="hint">
              {chosen.describe}{" "}
              {chosen.needs_model
                ? <strong>Needs a model, so it costs one call per run.</strong>
                : <strong>Needs no model.</strong>}
            </p>
          )}
          <label>
            <input type="checkbox" checked={scheduled}
                   onChange={(e) => setScheduled(e.target.checked)} />
            {" "}Run it on a schedule
          </label>
          {scheduled && (
            <label>Every
              <select value={every} onChange={(e) => setEvery(Number(e.target.value))}>
                <option value={3600}>hour</option>
                <option value={86400}>day</option>
                <option value={604800}>week</option>
              </select>
            </label>
          )}
          <p className="hint">
            It is created stopped either way. A compaction nobody has previewed
            is one that empties a memory quietly, so scheduling is refused until
            you have seen what it would do.
          </p>
          <button disabled={busy || !memoryId} onClick={() => void act(async () => {
            await call("api/v1/compaction/jobs", {
              project_id: projectId, name: name || "compaction",
              memory_id: memoryId, algorithm,
              schedule: scheduled
                ? { type: "interval", every_seconds: every }
                : { type: "manual" },
            });
            setComposing(false); setName("");
          }, "Created. Preview it to see what it would fold away.")}>
            Create
          </button>
        </div>
      )}

      {jobs.length === 0 ? (
        <div className="card">
          <h2>Nothing is being compacted</h2>
          <p className="hint">
            A memory that only grows eventually stops being a working set. A
            compaction folds it down without losing anything — start with
            de-duplication, which costs nothing and is usually most of it.
          </p>
        </div>
      ) : jobs.map((j) => {
        const pv = preview[j.job_id];
        const list = runs[j.job_id] ?? [];
        return (
          <div className="card" key={j.job_id}>
            <h2>{j.name}</h2>
            <p>
              <code>{algorithms[j.algorithm]?.label ?? j.algorithm}</code> ·{" "}
              {j.memory_title || j.memory_key} ({j.members} member
              {j.members === 1 ? "" : "s"}) ·{" "}
              {j.schedule.type === "interval"
                ? `every ${Math.round((j.schedule.every_seconds ?? 0) / 3600)}h`
                : "when you run it"}
            </p>
            <p>
              {j.enabled ? <span className="ok">scheduled</span>
                : approved(j) ? <span>ready, not scheduled</span>
                : <span className="warn">needs a preview</span>}
              {j.archived_total > 0 && <> · {j.archived_total} archived so far</>}
              {j.last_run_at && <> · last ran {new Date(j.last_run_at).toLocaleString()}</>}
            </p>
            <p>
              <button disabled={busy} onClick={() => void act(async () => {
                const r = await call<CompactionRun>(
                  `api/v1/compaction/jobs/${j.job_id}/preview`, {});
                setPreview((prev) => ({ ...prev, [j.job_id]: r }));
              })}>Preview</button>
              <button disabled={busy} onClick={() => void act(
                () => call(`api/v1/compaction/jobs/${j.job_id}/run`, {}),
                "Run finished. Members were archived, not deleted.")}>Run now</button>
              <button disabled={busy || (!j.enabled && !approved(j))}
                      title={!approved(j) ? "Preview this version first" : undefined}
                      onClick={() => void act(() => call(
                        `api/v1/compaction/jobs/${j.job_id}/enabled`,
                        { enabled: !j.enabled }))}>
                {j.enabled ? "Unschedule" : "Schedule"}
              </button>
              <button disabled={busy} onClick={() => void act(async () => {
                const page = await call<{ runs: CompactionRun[] }>(
                  `api/v1/compaction/jobs/${j.job_id}/runs`);
                setRuns((prev) => ({ ...prev, [j.job_id]: page.runs }));
                setOpen(open === j.job_id ? null : j.job_id);
              })}>History</button>
              <button disabled={busy} onClick={() => void act(
                () => call(`api/v1/compaction/jobs/${j.job_id}`, undefined, "DELETE"))}>
                Delete
              </button>
            </p>

            {pv && (
              <div className="card">
                <h3>Preview — nothing was archived</h3>
                <p>
                  Would fold <strong>{pv.archived}</strong> of {pv.considered} member
                  {pv.considered === 1 ? "" : "s"}, freeing{" "}
                  {humanChars(pv.bytes_before - pv.bytes_after)} from the working set.
                </p>
                {pv.note && <p className="hint">{pv.note}</p>}
                {pv.samples && pv.samples.length > 0 && (
                  <table className="kv">
                    <thead><tr><th>Would archive</th><th>Size</th></tr></thead>
                    <tbody>
                      {pv.samples.map((sm) => (
                        <tr key={sm.external_id}>
                          <td><code>{sm.external_id}</code></td>
                          <td>{humanChars(sm.chars)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            )}

            {open === j.job_id && (
              <div className="card">
                <h3>Every run</h3>
                {list.length === 0 ? <p className="hint">It has not run yet.</p> : (
                  <table className="kv">
                    <thead>
                      <tr><th>When</th><th>Kind</th><th>Looked at</th><th>Archived</th>
                          <th>Freed</th><th>Cost</th></tr>
                    </thead>
                    <tbody>
                      {list.map((r) => (
                        <tr key={r.run_id}>
                          <td>{new Date(r.started_at).toLocaleString()}</td>
                          <td>{r.mode === "dry" ? "preview"
                             : r.trigger === "schedule" ? "scheduled" : "run now"}</td>
                          <td>{r.considered}</td>
                          <td>{r.archived}</td>
                          <td>{humanChars(r.bytes_before - r.bytes_after)}</td>
                          <td>{r.model_calls > 0
                            ? `${r.model_calls} model call${r.model_calls === 1 ? "" : "s"}`
                            : "free"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            )}
          </div>
        );
      })}
    </section>
  );
}


/** Which state an alert is in, and it is the first thing the screen shows. */
function alertState(a: Alert): "firing" | "running" | "blocked" | "idle" {
  if (!isApproved(a)) return "blocked";
  if (!a.enabled) return "idle";
  return (a.matches_24h ?? 0) > 0 ? "firing" : "running";
}

const STATE_LABEL: Record<string, string> = {
  firing: "firing", running: "quiet", blocked: "needs a backtest", idle: "stopped",
};

/** Recent runs as shape: height is how much fired, colour is whether any did. */
function RunStrip({ runs }: { runs: AlertRun[] }) {
  const recent = runs.slice(0, 20).reverse();
  if (recent.length === 0) return <span className="strip empty">not run yet</span>;
  const peak = Math.max(1, ...recent.map((r) => r.matches));
  return (
    <span className="strip">
      {recent.map((r) => (
        <i key={r.run_id}
           className={r.matches > 0 ? "hit" : undefined}
           style={{ height: `${Math.max(3, (r.matches / peak) * 22)}px` }}
           title={`${new Date(r.started_at).toLocaleString()} — looked at ${
             r.candidates}, fired ${r.matches}${
             r.deferred ? `, ${r.deferred} left over` : ""}`} />
      ))}
    </span>
  );
}

/**
 * Alerts — a monitoring surface, not a form.
 *
 * State first: counts by state double as filters, every row leads with a dot,
 * and each carries a strip of its recent runs so *is this working* is answered
 * by shape before anyone reads a number. That distinction matters more here than
 * on most screens, because an alert that has stopped and an alert with nothing
 * to say produce identical silence.
 *
 * Three tabs, along the seam of what you are actually doing: managing the
 * definitions, reading what they caught, and wiring up where it gets sent.
 */
function AlertsSection({ projectId }: { projectId: string }) {
  const [tab, setTab] = useState<"alerts" | "activity" | "endpoints">("alerts");
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [surfaces, setSurfaces] = useState<Record<string, string[]>>({});
  // Served rather than typed twice: the editor shipped with `located_in`
  // written into its default condition and no way to discover the rest.
  const [vocabulary, setVocabulary] = useState<{ predicates: string[];
                                                 single_valued: string[] }>(
    { predicates: [], single_valued: [] });
  const [events, setEvents] = useState<ObservedEvent[]>([]);
  const [subs, setSubs] = useState<Subscription[]>([]);
  const [runsByAlert, setRunsByAlert] = useState<Record<string, AlertRun[]>>({});
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [secret, setSecret] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [selected, setSelected] = useState<string | null>(null);
  const [detailTab, setDetailTab] = useState<"definition" | "firings">("definition");
  const [composing, setComposing] = useState(false);
  const [filter, setFilter] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [backtests, setBacktests] = useState<Record<string, Backtest>>({});
  const [subUrl, setSubUrl] = useState("https://");
  const [scopeOptions, setScopeOptions] = useState<{
    memories: Memory[];
    cases: { case_id: string; title: string | null; external_id: string }[];
    producers: { producer_id: string; type: string }[];
  }>({ memories: [], cases: [], producers: [] });

  const load = useCallback(async () => {
    try {
      const [list, vocab, predicates, feed, subscriptions] = await Promise.all([
        call<{ alerts: Alert[] }>(`api/v1/projects/${projectId}/alerts`),
        call<{ surfaces: Record<string, string[]> }>("api/v1/alerts/surfaces"),
        call<{ predicates: string[]; single_valued: string[] }>("api/v1/graph/predicates"),
        call<{ events: ObservedEvent[] }>("api/v1/alert-events?since=0&limit=200"),
        call<{ subscriptions: Subscription[] }>(
          `api/v1/projects/${projectId}/event-subscriptions`),
      ]);
      setAlerts(list.alerts);
      setSurfaces(vocab.surfaces);
      setVocabulary({ predicates: predicates.predicates,
                      single_valued: predicates.single_valued });
      setEvents(feed.events.slice().reverse());
      setSubs(subscriptions.subscriptions);
      // Strips need history for every row, so this is part of the list, not a
      // detail nobody opens.
      const runs = await Promise.all(list.alerts.map((a) =>
        call<{ runs: AlertRun[] }>(`api/v1/alerts/${a.alert_id}/runs?limit=20`)
          .then((r) => [a.alert_id, r.runs] as const)
          .catch(() => [a.alert_id, [] as AlertRun[]] as const)));
      setRunsByAlert(Object.fromEntries(runs));

      // What an alert can be scoped to. Fetched here rather than in the editor
      // so opening the form is instant and the lists are already right.
      const [mem, cas, prod] = await Promise.all([
        call<{ memories: Memory[] }>(`api/v1/projects/${projectId}/memories`)
          .catch(() => ({ memories: [] })),
        call<{ cases: { case_id: string; title: string | null; external_id: string }[] }>(
          `api/v1/projects/${projectId}/cases`).catch(() => ({ cases: [] })),
        call<{ producers: { producer_id: string; type: string }[] }>("api/v1/producers")
          .catch(() => ({ producers: [] })),
      ]);
      setScopeOptions({ memories: mem.memories, cases: cas.cases,
                        producers: prod.producers });
    } catch (e) {
      setError((e as Error).message);
    }
  }, [projectId]);

  useEffect(() => { void load(); }, [load]);

  const act = async (fn: () => Promise<unknown>, message?: string) => {
    setBusy(true); setError(null); setNote(null);
    try {
      await fn();
      if (message) setNote(message);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const counts = {
    firing: alerts.filter((a) => alertState(a) === "firing").length,
    running: alerts.filter((a) => alertState(a) === "running").length,
    blocked: alerts.filter((a) => alertState(a) === "blocked").length,
    idle: alerts.filter((a) => alertState(a) === "idle").length,
  };

  const visible = alerts
    .filter((a) => !filter || alertState(a) === filter)
    .filter((a) => !query || a.name.toLowerCase().includes(query.toLowerCase())
                          || a.surface.includes(query));

  const current = alerts.find((a) => a.alert_id === selected) ?? null;
  const eventsFor = (id: string) => events.filter((e) => e.alert_id === id);

  return (
    <section className="stack">
      <h1>Alerts</h1>

      <div className="subtabs">
        <button className={`subtab ${tab === "alerts" ? "on" : ""}`}
                onClick={() => { setTab("alerts"); setSelected(null); }}>
          Alerts <span className="count">{alerts.length}</span>
        </button>
        <button className={`subtab ${tab === "activity" ? "on" : ""}`}
                onClick={() => setTab("activity")}>
          Activity <span className="count">{events.length}</span>
        </button>
        <button className={`subtab ${tab === "endpoints" ? "on" : ""}`}
                onClick={() => setTab("endpoints")}>
          Endpoints <span className="count">{subs.length}</span>
        </button>
      </div>

      {error && <p className="error">{error}</p>}
      {note && <p className="note">{note}</p>}

      {/* ------------------------------- ALERTS ------------------------------ */}
      {tab === "alerts" && !current && !composing && (
        <>
          <div className="statgrid">
            {(["firing", "running", "blocked", "idle"] as const).map((k) => (
              <button key={k}
                      className={`stattile ${k} ${filter === k ? "on" : ""}`}
                      onClick={() => setFilter(filter === k ? null : k)}>
                <span className="n">{counts[k]}</span>
                <span className="k">
                  {k === "firing" ? "firing (24h)"
                   : k === "running" ? "running, quiet"
                   : k === "blocked" ? "needs a backtest" : "stopped"}
                </span>
              </button>
            ))}
          </div>

          <div className="toolbar">
            <input className="grow" value={query} placeholder="Filter by name or event kind"
                   onChange={(e) => setQuery(e.target.value)} />
            <button onClick={() => { setComposing(true); setSelected(null); }}>
              New alert
            </button>
          </div>

          {alerts.length === 0 ? (
            <div className="card">
              <h2>Nothing is being watched yet</h2>
              <p className="hint">
                An alert says what is worth knowing about — a person relocating, a
                record becoming org-visible, a guess being confirmed. Create one,
                replay it against history to see what it would have caught, then
                start it.
              </p>
              <button onClick={() => setComposing(true)}>Create the first one</button>
            </div>
          ) : visible.length === 0 ? (
            <p className="hint">
              No alert matches that filter. {filter && (
                <button className="backlink" onClick={() => setFilter(null)}>
                  Show all {alerts.length}
                </button>
              )}
            </p>
          ) : (
            <div className="card">
              <div className="alerthead">
                <span />
                <span>Alert</span>
                <span className="hide-narrow">Recent runs</span>
                <span className="hide-narrow num">24h</span>
                <span className="hide-narrow num">Unseen</span>
                <span className="num">State</span>
              </div>
              {visible.map((a) => {
                const st = alertState(a);
                return (
                  <button key={a.alert_id} className="alertrow"
                          onClick={() => { setSelected(a.alert_id); setDetailTab("definition"); }}>
                    <span className={`dot ${st}`} />
                    <span>
                      <span className="nm">{a.name}</span><br />
                      <span className="sub">
                        {a.surface}
                        {a.mode === "llm" ? " · judged in words" : ""}
                        {Object.keys(a.scope ?? {}).length > 0
                          ? ` · scoped to ${Object.keys(a.scope)[0].replace("_id", "")}`
                          : ""}
                      </span>
                    </span>
                    <span className="hide-narrow">
                      <RunStrip runs={runsByAlert[a.alert_id] ?? []} />
                    </span>
                    <span className="hide-narrow num">{a.matches_24h ?? 0}</span>
                    <span className="hide-narrow num"
                          title="transitions this alert has not looked at yet">
                      {a.behind ? <span className="warn">{a.behind}</span> : "0"}
                    </span>
                    <span className="num sub">{STATE_LABEL[st]}</span>
                  </button>
                );
              })}
            </div>
          )}
        </>
      )}

      {/* ------------------------- CREATE / EDIT ----------------------------- */}
      {tab === "alerts" && (composing || current) && (
        <AlertEditor
          key={current?.alert_id ?? "new"}
          alert={current}
          surfaces={surfaces}
          vocabulary={vocabulary}
          scopeOptions={scopeOptions}
          busy={busy}
          runs={current ? runsByAlert[current.alert_id] ?? [] : []}
          events={current ? eventsFor(current.alert_id) : []}
          backtest={current ? backtests[current.alert_id] : undefined}
          detailTab={detailTab}
          onTab={setDetailTab}
          onBack={() => { setComposing(false); setSelected(null); }}
          onSave={(body) => act(async () => {
            if (current) await call(`api/v1/alerts/${current.alert_id}`, body, "PATCH");
            else await call("api/v1/alerts", { project_id: projectId, ...body });
            setComposing(false);
          }, current
            ? "Saved. Changing what it matches drops its approval, so backtest it again."
            : "Created. Replay it against history before starting it.")}
          onBacktest={() => act(async () => {
            const r = await call<Backtest>(
              `api/v1/alerts/${current!.alert_id}/backtest`, {});
            setBacktests((prev) => ({ ...prev, [current!.alert_id]: r }));
          })}
          onToggle={() => act(() => call(
            `api/v1/alerts/${current!.alert_id}/enabled`, { enabled: !current!.enabled }))}
          onDelete={() => act(async () => {
            await call(`api/v1/alerts/${current!.alert_id}`, undefined, "DELETE");
            setSelected(null);
          }, "Deleted.")}
        />
      )}

      {/* ------------------------------ ACTIVITY ----------------------------- */}
      {tab === "activity" && (
        <div className="card">
          <h2>{events.length} event{events.length === 1 ? "" : "s"}</h2>
          {events.length === 0 ? (
            <p className="hint">
              Nothing has fired. Alerts only report what happens after they were
              created — replay one against history to see what came before.
            </p>
          ) : (
            <table className="kv">
              <thead>
                <tr><th>When</th><th>Alert</th><th>What happened</th><th>Told</th></tr>
              </thead>
              <tbody>
                {events.map((e) => (
                  <tr key={e.event_id}>
                    <td>{new Date(e.occurred_at).toLocaleString()}</td>
                    <td>{e.alert_name}</td>
                    <td>
                      {describeEvent(e.surface, e.payload)}<br />
                      <span className="sub"><code>{e.surface}</code></span>
                    </td>
                    <td>
                      {e.deliveries === 0 ? <span className="hint">no endpoint</span>
                       : e.undeliverable > 0 ? <span className="warn">undeliverable</span>
                       : e.delivered > 0 ? <span className="ok">sent</span> : "queued"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* ----------------------------- ENDPOINTS ----------------------------- */}
      {tab === "endpoints" && (
        <div className="card">
          <h2>Where events get sent</h2>
          <p className="hint">
            HTTPS only, and the address is re-checked on every send. Signed the
            same way memdog asks providers to sign theirs. Retried, then held as
            undeliverable rather than dropped — and replayable once fixed.
          </p>
          <div className="toolbar">
            <input className="grow" value={subUrl}
                   onChange={(e) => setSubUrl(e.target.value)} />
            <button disabled={busy} onClick={() => void act(async () => {
              const created = await call<{ signing_secret: string }>(
                "api/v1/event-subscriptions", { project_id: projectId, url: subUrl });
              setSecret(created.signing_secret);
            })}>Register</button>
          </div>
          {secret && (
            <p className="warn">
              Signing secret — <strong>copy it now; it cannot be shown again</strong>:{" "}
              <code>{secret}</code>
            </p>
          )}
          {subs.length === 0 ? (
            <p className="hint">
              No endpoint yet. Without one, alerts still record everything — you
              read them under Activity instead of being pushed.
            </p>
          ) : (
            <table className="kv">
              <thead><tr><th>URL</th><th>Queued</th><th>Undeliverable</th><th /></tr></thead>
              <tbody>
                {subs.map((s) => (
                  <tr key={s.subscription_id}>
                    <td><code>{s.url}</code></td>
                    <td>{s.pending}</td>
                    <td>{s.dead > 0 ? <span className="warn">{s.dead}</span> : "0"}</td>
                    <td>
                      <button disabled={busy} onClick={() => void act(async () => {
                        const r = await call<{ signing_secret: string }>(
                          `api/v1/event-subscriptions/${s.subscription_id}/rotate`, {});
                        setSecret(r.signing_secret);
                      }, "Rotated. The previous secret still verifies briefly.")}>
                        Rotate
                      </button>
                      <button disabled={busy || s.dead === 0}
                              title={s.dead === 0 ? "Nothing is undeliverable" : undefined}
                              onClick={() => void act(() => call(
                                `api/v1/event-subscriptions/${s.subscription_id}/replay`, {}),
                                "Re-armed.")}>
                        Retry {s.dead > 0 ? `(${s.dead})` : ""}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </section>
  );
}


function CrawlersSection({ projectId }: { projectId: string }) {
  const [crawlers, setCrawlers] = useState<Crawler[]>([]);
  const [kind, setKind] = useState<string>("feed");
  const [seed, setSeed] = useState("https://blog.google/rss/");
  const [enrich, setEnrich] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [conns, setConns] = useState<Connection[]>([]);
  const [newConn, setNewConn] = useState({
    provider: "", credential: "", auth_style: "bearer", auth_name: "",
    token_url: "", scope: "", subject: "",
  });
  const [apps, setApps] = useState<Connector[]>([]);
  const [chosenApp, setChosenApp] = useState<Connector | null>(null);
  const [appScope, setAppScope] = useState<Record<string, string>>({});
  const [appConn, setAppConn] = useState("");

  // The two styles whose stored secret is traded for a token rather than sent.
  // They need somewhere to trade it, which is the only reason the form changes
  // shape at all.
  const exchanged =
    newConn.auth_style === "client_credentials" ||
    newConn.auth_style === "google_service_account";

  const load = useCallback(async () => {
    try {
      const [page, connections, catalog] = await Promise.all([
        call<{ crawlers: Crawler[] }>(`api/v1/projects/${projectId}/crawlers`),
        call<{ connections: Connection[] }>(
          `api/v1/connections?project_id=${projectId}`, undefined, "GET"),
        call<{ connectors: Connector[] }>("api/v1/connectors", undefined, "GET"),
      ]);
      setConns(connections.connections);
      setApps(catalog.connectors);
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
      <h1>Crawlers</h1>
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
            placeholder={PRESETS[kind].placeholder}
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
        <h2>Pull from an app</h2>
        <p className="empty">
          Each one carries the endpoint, the pagination and the field mapping, so what you supply
          is the part only you know. None has been run against a live account — the dry run every
          crawler must pass is where an entry stops being a guess.
        </p>

        {Object.entries(
          apps.reduce((byCat, app) => {
            (byCat[app.category] ||= []).push(app);
            return byCat;
          }, {} as Record<string, Connector[]>),
        ).map(([category, entries]) => (
          <div key={category} style={{ marginTop: 14 }}>
            <div className="muted" style={{ fontSize: 12, letterSpacing: "0.06em",
                                            textTransform: "uppercase", marginBottom: 7 }}>
              {category}
            </div>
            <div className="row">
              {entries.map((app) => (
                <button
                  key={app.key}
                  className={chosenApp?.key === app.key ? "" : "secondary"}
                  disabled={busy || !app.available}
                  title={app.available ? app.pulls : app.notes}
                  onClick={() => {
                    setChosenApp(app);
                    setAppScope({});
                  }}
                >
                  {app.label}
                  {!app.available && " ·"}
                </button>
              ))}
            </div>
          </div>
        ))}

        {chosenApp && (
          <div style={{ marginTop: 18, paddingTop: 16, borderTop: "1px solid var(--line)" }}>
            <p className="what">{chosenApp.label} — {chosenApp.pulls}</p>
            {chosenApp.auth_help && (
              <p className="empty" style={{ marginTop: 4 }}>{chosenApp.auth_help}</p>
            )}
            {chosenApp.notes && (
              <p className="empty" style={{ marginTop: 4 }}>{chosenApp.notes}</p>
            )}
            {/* The catalog's honesty is stated here or it is not stated at all.
                An entry that looks like a supported integration and has never
                been run is the thing a person needs told before they wire it
                to a credential and trust the count it returns. */}
            <p className={chosenApp.verified ? "ok" : "hint"} style={{ marginTop: 6 }}>
              {chosenApp.verified
                ? "Run against a live account."
                : chosenApp.exercised_against
                  ? `Never run against a live account. Paging, field mapping and the incremental
                     pull are exercised end to end against ${chosenApp.exercised_against} — which
                     catches the mechanical mistakes, but cannot tell you the permissions or the
                     rate limits are right.`
                  : `Never run against a live account — written from ${chosenApp.label}'s published
                     API. The dry run below is what turns it from a researched guess into a fact.`}
            </p>

            {chosenApp.scopes.map((scope) => (
              <div className="row" style={{ marginTop: 9 }} key={scope.key}>
                <span className="muted" style={{ fontSize: 13, minWidth: 118 }}>
                  {scope.label}
                </span>
                <input
                  type="text"
                  value={appScope[scope.key] ?? ""}
                  onChange={(e) =>
                    setAppScope({ ...appScope, [scope.key]: e.target.value })
                  }
                  placeholder={scope.placeholder}
                  style={{ flex: 1 }}
                />
              </div>
            ))}

            <div className="row" style={{ marginTop: 12 }}>
              <span className="muted" style={{ fontSize: 13, minWidth: 118 }}>Credential</span>
              <select value={appConn} onChange={(e) => setAppConn(e.target.value)}>
                <option value="">Choose a connection…</option>
                {conns.map((c) => (
                  <option key={c.connection_id} value={c.connection_id}>
                    {c.provider} ({c.auth_style})
                  </option>
                ))}
              </select>
              <button
                onClick={() =>
                  act(`${chosenApp.label} crawler created — run a dry run next`, async () => {
                    await call("api/v1/crawlers/from-connector", {
                      project_id: projectId,
                      connector: chosenApp.key,
                      scope: appScope,
                      connection_id: appConn || null,
                      enrich,
                    });
                    setChosenApp(null);
                    setAppScope({});
                    setAppConn("");
                  })
                }
                disabled={
                  busy ||
                  chosenApp.scopes.some((sc) => !(appScope[sc.key] ?? "").trim())
                }
              >
                Create
              </button>
            </div>
            <p className="empty" style={{ marginTop: 8 }}>
              It arrives disabled, like every crawler. A dry run walks the same code a live run
              does and tells you what it would have written before anything is.
            </p>
          </div>
        )}
      </section>

      <section className="panel">
        <h2>Credentials</h2>
        <p className="empty">
          A crawler reaches an authenticated source through a connection, never through its
          config — a config is stored, versioned and readable, so a token in one is a token in
          the clear. Registered here, it is encrypted at rest and never shown again.
        </p>
        <div className="row" style={{ marginTop: 10 }}>
          <input
            type="text"
            value={newConn.provider}
            onChange={(e) => setNewConn({ ...newConn, provider: e.target.value })}
            placeholder="Provider — jira, notion, github…"
            style={{ flex: 1 }}
          />
          <select
            value={newConn.auth_style}
            onChange={(e) => setNewConn({ ...newConn, auth_style: e.target.value })}
          >
            <option value="bearer">Bearer token</option>
            <option value="header">Custom header</option>
            <option value="query">Query parameter</option>
            <option value="basic">Basic (user:password)</option>
            <option value="client_credentials">Client credentials (Microsoft, Salesforce)</option>
            <option value="google_service_account">Google service account</option>
          </select>
        </div>
        {exchanged && (
          <p className="empty" style={{ marginTop: 8 }}>
            {newConn.auth_style === "client_credentials"
              ? "The app registration's own credential, as client_id:client_secret. It is traded for a token good for an hour — there is no consent screen and no browser step."
              : "The service account's JSON key file, pasted whole. It signs an assertion that is traded for a token; a subject is domain-wide delegation, and means this credential acts as that person."}
          </p>
        )}
        <div className="row" style={{ marginTop: 8 }}>
          {(newConn.auth_style === "header" || newConn.auth_style === "query") && (
            <input
              type="text"
              value={newConn.auth_name}
              onChange={(e) => setNewConn({ ...newConn, auth_name: e.target.value })}
              placeholder={newConn.auth_style === "header" ? "X-Api-Key" : "api_key"}
              style={{ width: 200 }}
            />
          )}
          {newConn.auth_style === "client_credentials" && (
            <input
              type="text"
              value={newConn.token_url}
              onChange={(e) => setNewConn({ ...newConn, token_url: e.target.value })}
              placeholder="Token endpoint — https://login.microsoftonline.com/<tenant>/oauth2/v2.0/token"
              style={{ flex: 1 }}
            />
          )}
          {exchanged && (
            <input
              type="text"
              value={newConn.scope}
              onChange={(e) => setNewConn({ ...newConn, scope: e.target.value })}
              placeholder={
                newConn.auth_style === "client_credentials"
                  ? "Scope — https://graph.microsoft.com/.default"
                  : "Scope — https://www.googleapis.com/auth/drive.readonly"
              }
              style={{ flex: 1 }}
            />
          )}
          {newConn.auth_style === "google_service_account" && (
            <input
              type="text"
              value={newConn.subject}
              onChange={(e) => setNewConn({ ...newConn, subject: e.target.value })}
              placeholder="Act as (optional) — someone@acme.com"
              style={{ width: 220 }}
            />
          )}
        </div>
        <div className="row" style={{ marginTop: 8 }}>
          {newConn.auth_style === "google_service_account" ? (
            <textarea
              value={newConn.credential}
              onChange={(e) => setNewConn({ ...newConn, credential: e.target.value })}
              placeholder={'{ "type": "service_account", "client_email": …, "private_key": … }'}
              rows={4}
              style={{ flex: 1, fontFamily: "var(--mono)", fontSize: 12 }}
            />
          ) : (
            <input
              type="password"
              value={newConn.credential}
              onChange={(e) => setNewConn({ ...newConn, credential: e.target.value })}
              placeholder={
                newConn.auth_style === "client_credentials"
                  ? "client_id:client_secret — sent once, stored encrypted"
                  : "Credential — sent once, stored encrypted"
              }
              style={{ flex: 1 }}
            />
          )}
          <button
            onClick={() =>
              act("Connection registered", async () => {
                // Only what this style actually needs. An empty token_url on a
                // Google connection would be stored, read back by nobody, and
                // read as configuration that had been supplied.
                const authConfig: Record<string, string> = {};
                if (newConn.auth_style === "client_credentials" && newConn.token_url.trim())
                  authConfig.token_url = newConn.token_url.trim();
                if (exchanged && newConn.scope.trim()) authConfig.scope = newConn.scope.trim();
                if (newConn.auth_style === "google_service_account" && newConn.subject.trim())
                  authConfig.subject = newConn.subject.trim();
                await call("api/v1/connections", {
                  project_id: projectId,
                  provider: newConn.provider,
                  credential: newConn.credential,
                  auth_style: newConn.auth_style,
                  auth_name: newConn.auth_name || null,
                  auth_config: authConfig,
                });
                setNewConn({ provider: "", credential: "", auth_style: "bearer",
                             auth_name: "", token_url: "", scope: "", subject: "" });
              })
            }
            disabled={
              busy ||
              !newConn.provider.trim() ||
              !newConn.credential.trim() ||
              (newConn.auth_style === "client_credentials" && !newConn.token_url.trim()) ||
              (exchanged && !newConn.scope.trim())
            }
          >
            Register
          </button>
        </div>
        {conns.length === 0 ? (
          <p className="empty" style={{ marginTop: 12 }}>
            None yet. Public sources — sitemaps, RSS — need none of this.
          </p>
        ) : (
          <div style={{ marginTop: 12 }}>
            {conns.map((c) => (
              <div className="hit" key={c.connection_id}>
                <div className="meta">
                  <span className="chip on">{c.provider}</span>
                  <span className="chip">{c.auth_style}{c.auth_name ? `: ${c.auth_name}` : ""}</span>
                  <span className={`chip ${c.has_credential ? "enriched" : "stored"}`}>
                    {c.has_credential ? "credential held" : "no credential"}
                  </span>
                  <span className="chip">{c.scope}</span>
                </div>
              </div>
            ))}
          </div>
        )}
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
                {crawler.connection_id && (
                  <span className="chip on">authenticated</span>
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
              <div className="row" style={{ marginTop: 8 }}>
                <span className="muted" style={{ fontSize: 12.5 }}>Credential</span>
                <select
                  value={crawler.connection_id ?? ""}
                  onChange={(e) =>
                    act("Crawler credential updated", async () => {
                      await call(
                        `api/v1/crawlers/${crawler.crawler_id}/connection`,
                        { connection_id: e.target.value || null },
                        "PATCH",
                      );
                    })
                  }
                  disabled={busy}
                >
                  <option value="">None — public source</option>
                  {conns.map((c) => (
                    <option key={c.connection_id} value={c.connection_id}>
                      {c.provider} ({c.auth_style})
                    </option>
                  ))}
                </select>
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
      <h1>Chat</h1>
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
      <h1>Search</h1>
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

/**
 * Audit — numbers first, then pick, then rows.
 *
 * An audit log is scanned, not read, and printing two hundred entries answers
 * no question anybody arrived with. Nobody opens this to look at row 147; they
 * open it asking *how much happened*, *what kind*, *by whom*, and only then
 * *show me those*.
 *
 * So the shape is summary → selection → detail. The table appears when an
 * action is chosen, which is also the moment it becomes small enough to read.
 */
function Audit({ projectId }: { projectId: string }) {
  const PAGE = 25;
  const [trail, setTrail] = useState<AuditTrail | null>(null);
  const [tab, setTab] = useState<"writes" | "reads">("writes");
  const [action, setAction] = useState<string | null>(null);
  const [shown, setShown] = useState(PAGE);
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setTrail(await call<AuditTrail>(`api/v1/audit?project_id=${projectId}&limit=200`));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [projectId]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { setShown(PAGE); setOpen(null); setAction(null); }, [tab]);

  type Row = { id: string; action: string; at: string; who: string | null;
               target: string | null; detail?: Record<string, unknown> };

  const rows: Row[] =
    !trail ? []
    : tab === "writes"
      ? trail.writes.map((w) => ({
          id: w.id, action: w.action, at: w.at, target: w.target_id,
          who: w.actor_user_id ?? w.actor_key_id, detail: w.detail }))
      : trail.reads.map((r) => ({
          id: r.id, action: r.action, at: r.at, target: r.data_id,
          who: r.user_id ?? r.key_id }));

  const byAction = rows.reduce<Record<string, { n: number; last: string }>>((acc, r) => {
    const seen = acc[r.action];
    acc[r.action] = { n: (seen?.n ?? 0) + 1,
                      last: !seen || r.at > seen.last ? r.at : seen.last };
    return acc;
  }, {});

  const people = new Set(rows.map((r) => r.who).filter(Boolean));
  const targets = new Set(rows.map((r) => r.target).filter(Boolean));
  const times = rows.map((r) => r.at).sort();
  const since = times[0];

  const filtered = action ? rows.filter((r) => r.action === action) : [];
  const page = filtered.slice(0, shown);

  return (
    <>
      <h1>Audit</h1>
      <p className="lede">
        Two stores, because they are two different things. Writes are low-volume and never purged.
        Reads are written on every access, never updated, and must survive the deletion of what they
        describe — which is why they carry no foreign keys.
      </p>
      {error && <p className="err">{error}</p>}

      <div className="subtabs">
        <button className={`subtab ${tab === "writes" ? "on" : ""}`}
                onClick={() => setTab("writes")}>
          Writes <span className="count">{trail?.writes.length ?? 0}</span>
        </button>
        <button className={`subtab ${tab === "reads" ? "on" : ""}`}
                onClick={() => setTab("reads")}>
          Reads <span className="count">{trail?.reads.length ?? 0}</span>
        </button>
      </div>

      {rows.length === 0 ? (
        <p className="empty">
          {tab === "writes"
            ? "Nothing has been written in this project yet."
            : "Nothing has been read yet — reads are logged the moment somebody looks."}
        </p>
      ) : (
        <>
          {/* ---- 1. how much, of what kind, by whom ---- */}
          <div className="statgrid">
            <div className="stattile">
              <span className="n">{rows.length}</span>
              <span className="k">{tab === "writes" ? "writes" : "reads"} recorded</span>
            </div>
            <div className="stattile">
              <span className="n">{Object.keys(byAction).length}</span>
              <span className="k">kinds of action</span>
            </div>
            <div className="stattile">
              <span className="n">{people.size}</span>
              <span className="k">{people.size === 1 ? "actor" : "actors"}</span>
            </div>
            <div className="stattile">
              <span className="n">{targets.size}</span>
              <span className="k">records touched</span>
            </div>
          </div>
          {since && (
            <p className="hint">
              The most recent {rows.length}, back to{" "}
              {new Date(since).toLocaleString()}. Older entries are kept and not
              shown here.
            </p>
          )}

          {/* ---- 2. pick one ---- */}
          <section className="panel">
            <h2>By action</h2>
            <table className="kv">
              <thead>
                <tr><th>Action</th><th className="num">Entries</th><th>Last</th><th /></tr>
              </thead>
              <tbody>
                {Object.entries(byAction)
                  .sort((a, b) => b[1].n - a[1].n)
                  .map(([name, v]) => (
                    <tr key={name}>
                      <td><code>{name}</code></td>
                      <td className="num">{v.n}</td>
                      <td>{new Date(v.last).toLocaleString()}</td>
                      <td>
                        <button className="backlink"
                                onClick={() => {
                                  setAction(action === name ? null : name);
                                  setShown(PAGE); setOpen(null);
                                }}>
                          {action === name ? "hide" : "show"}
                        </button>
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </section>

          {/* ---- 3. and only then the rows ---- */}
          {action && (
            <section className="panel">
              <h2>
                {filtered.length} × <code>{action}</code>
                {page.length < filtered.length ? ` — showing ${page.length}` : ""}
              </h2>
              <table className="kv">
                <thead>
                  <tr><th>When</th><th>Who</th><th>Target</th><th /></tr>
                </thead>
                <tbody>
                  {page.map((r) => {
                    const detailed = r.detail && Object.keys(r.detail).length > 0;
                    return (
                      <Fragment key={r.id}>
                        <tr>
                          <td>{new Date(r.at).toLocaleString()}</td>
                          <td><code>{r.who ?? "—"}</code></td>
                          <td><code>{r.target ?? "—"}</code></td>
                          <td>
                            {detailed ? (
                              <button className="backlink"
                                      onClick={() => setOpen(open === r.id ? null : r.id)}>
                                {open === r.id ? "hide" : "detail"}
                              </button>
                            ) : (
                              /* Said rather than left blank: nothing further was
                                 recorded, which is not the same as hidden. */
                              <span className="empty">—</span>
                            )}
                          </td>
                        </tr>
                        {open === r.id && detailed && (
                          <tr>
                            <td colSpan={4}>
                              <pre className="excerpt">
                                {JSON.stringify(r.detail, null, 2)}
                              </pre>
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
              {page.length < filtered.length && (
                <button onClick={() => setShown(shown + PAGE)}>
                  Show {Math.min(PAGE, filtered.length - page.length)} more
                </button>
              )}
            </section>
          )}
        </>
      )}
    </>
  );
}
type ExpiryDue = {
  due: { data_id: string; memory_id: string; type: string; on_expiry: string;
         expired_at: string }[];
  capped: boolean;
};

type SweepResult = {
  considered: number; deleted: number; archived: number; refiled: number;
  applied: boolean; run_id: string | null; capped: boolean;
};

/** The policy, as the thing it does rather than as its column value. */
const POLICY_VERB: Record<string, string> = {
  orphan_delete: "deleted, unless another memory holds it",
  keep_members: "re-filed into the default",
  archive: "archived — out of the working set, still readable",
};

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
  const [due, setDue] = useState<ExpiryDue | null>(null);
  const [sweep, setSweep] = useState<SweepResult | null>(null);
  const [tree, setTree] = useState<MemoryTree | null>(null);
  const [parentOf, setParentOf] = useState("");
  // What can be made from this memory's members, served rather than typed here
  // — a list in a console is a second copy of a vocabulary.
  const [generators, setGenerators] = useState<{ name: string; label: string;
                                                 describe: string; archivable: boolean }[]>([]);
  const [generator, setGenerator] = useState("summary");
  const [derived, setDerived] = useState<{ artifact_id: string; kind: string;
                                           title: string | null; summary: string | null;
                                           sources: number; access_level: string;
                                           created_at: string }[]>([]);

  const load = useCallback(async () => {
    try {
      const [m, t, d] = await Promise.all([
        call<{ memories: Memory[] }>(`api/v1/projects/${projectId}/memories`),
        call<{ types: MemoryType[] }>(`api/v1/projects/${projectId}/memory-types`),
        call<ExpiryDue>(`api/v1/projects/${projectId}/expiring?limit=200`),
      ]);
      setGenerators((await call<{ generators: typeof generators }>("api/v1/generators")
        .catch(() => ({ generators: [] }))).generators);
      setMemories(m.memories);
      setTypes(t.types);
      setDue(d);
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
        setTree(await call<MemoryTree>(`api/v1/memories/${memory.memory_id}/tree`));
        setDerived((await call<{ artifacts: typeof derived }>(
          `api/v1/memories/${memory.memory_id}/artifacts`)).artifacts);
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
      <h1>Memories</h1>
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
        <h2>Past its TTL</h2>
        <p className="empty" style={{ marginTop: 0 }}>
          A TTL is only a retention policy if something acts on it. The sweep runs with the
          scheduled reconcile; this is the same pass, on demand and previewable — and{" "}
          <strong>it only ever sees what you can see</strong>, so a colleague&rsquo;s private
          records are counted by the scheduled run and not here.
        </p>
        {due === null ? (
          <p className="empty">Loading…</p>
        ) : due.due.length === 0 ? (
          <p className="empty">
            Nothing is due. An item is due only when <strong>every</strong> memory holding it has
            expired — one permanent membership keeps it, which is why a record in an hour-long
            conversation and in a factual memory is not an hour-old record.
          </p>
        ) : (
          <>
            <div className="row">
              {Object.entries(
                due.due.reduce<Record<string, number>>((acc, d) => {
                  acc[d.on_expiry] = (acc[d.on_expiry] ?? 0) + 1;
                  return acc;
                }, {}),
              ).map(([policy, count]) => (
                <span className="chip on" key={policy}>{count} {POLICY_VERB[policy] ?? policy}</span>
              ))}
            </div>
            <div className="excluded" style={{ marginTop: 10 }}>
              {due.due.slice(0, 10).map((d) => (
                <div className="item" key={d.data_id}>
                  <span className="chip">{d.type}</span>
                  <code>{d.data_id}</code>
                  <span className="why">{POLICY_VERB[d.on_expiry] ?? d.on_expiry}</span>
                  <span className="empty far">
                    due since {new Date(d.expired_at).toLocaleString()}
                  </span>
                </div>
              ))}
            </div>
            {due.capped && (
              <p className="warned">
                <strong>We stopped counting at 200.</strong> There are more; this is not the total.
              </p>
            )}
            <div className="row end" style={{ marginTop: 10 }}>
              <button
                className="secondary"
                disabled={busy}
                title="Runs the sweep with its writes withheld and reports what it would do."
                onClick={() =>
                  act("Previewed. Nothing was changed.", async () => {
                    setSweep(await call<SweepResult>("api/v1/expiry/sweep", {
                      project_id: projectId, dry_run: true,
                    }));
                  })
                }
              >
                Preview the sweep
              </button>
              <button
                disabled={busy}
                title="Deletes, archives or re-files each record according to its memory type. Deletion is the ordinary cascade — tombstone, then reclamation."
                onClick={() =>
                  act("Swept.", async () => {
                    setSweep(await call<SweepResult>("api/v1/expiry/sweep", {
                      project_id: projectId, dry_run: false,
                    }));
                    await load();
                  })
                }
              >
                Sweep now
              </button>
            </div>
            {sweep && (
              <p className={sweep.applied ? "ok" : "empty"}>
                {sweep.applied ? "Swept" : "Would sweep"} {sweep.considered} record
                {sweep.considered === 1 ? "" : "s"}: <strong>{sweep.deleted}</strong> deleted,{" "}
                <strong>{sweep.archived}</strong> archived, <strong>{sweep.refiled}</strong>{" "}
                re-filed into the default.
                {sweep.run_id && <> Deletion run <code>{sweep.run_id}</code>.</>}
              </p>
            )}
          </>
        )}
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
                {/* A rollup nobody can tell is out of date is one people keep
                  * quoting. It is a flag rather than a queue entry, so this is
                  * a state to read rather than progress to watch. */}
                {m.stale_since && (
                  <span className="chip warnchip far"
                        title={`${m.stale_reason ?? "a source changed"} — since `
                               + new Date(m.stale_since).toLocaleString()}>
                    out of date
                  </span>
                )}
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

            {selected.stale_since && (
              <div className="notice caution">
                <strong>This rollup is out of date.</strong> Something it is derived from changed
                {selected.stale_reason ? ` — ${selected.stale_reason}` : ""}, on{" "}
                {new Date(selected.stale_since).toLocaleString()}. Nothing recomputed it, and that
                is deliberate: recomputing on every write turns one bulk import into thousands of
                model calls nobody asked for. <strong>Run its compaction job</strong> to rebuild it
                — a live run clears this, a preview does not.
              </div>
            )}

            <h3>Where it sits</h3>
            {tree === null ? (
              <p className="empty">Loading…</p>
            ) : tree.ancestors.length === 0 && tree.descendants.length === 0 ? (
              <p className="empty">
                Not part of anything, and nothing is part of it. Two memories converge into a third
                by making each <code>part_of</code> it — the parent then has no separate contents to
                keep in sync, because its members <em>are</em> theirs.
              </p>
            ) : (
              <>
                <div className="excluded">
                  {tree.ancestors.map((a) => (
                    <div className="item" key={a.memory_id}>
                      <span className="chip">{"↑".repeat(a.depth)} contained by</span>
                      <code>{a.title || a.memory_key || a.memory_id}</code>
                      <span className="empty">{a.type} · {a.members} member{a.members === 1 ? "" : "s"}</span>
                    </div>
                  ))}
                  {tree.descendants.map((d) => (
                    <div className="item" key={d.memory_id}>
                      <span className="chip on">{"↓".repeat(d.depth)} contains</span>
                      <code>{d.title || d.memory_key || d.memory_id}</code>
                      <span className="empty">{d.type} · {d.members} member{d.members === 1 ? "" : "s"}</span>
                      {d.stale_since && <span className="chip warnchip far">out of date</span>}
                    </div>
                  ))}
                </div>
                {tree.descendants.length > 0 && (
                  <p className="empty">
                    An alert scoped here sees changes in all {tree.descendants.length} of them —
                    the scope is resolved through the hierarchy when it matches, so nothing is
                    duplicated and nothing has to be kept in sync.
                  </p>
                )}
                {tree.truncated && (
                  <p className="warned">
                    <strong>The walk stopped at {tree.max_depth} levels.</strong> There is more
                    below; this is not the whole tree.
                  </p>
                )}
              </>
            )}
            <div className="row">
              <select value={parentOf} onChange={(e) => setParentOf(e.target.value)}>
                <option value="">make it part of…</option>
                {memories
                  .filter((m) => m.memory_id !== selected.memory_id)
                  .map((m) => (
                    <option key={m.memory_id} value={m.memory_id}>
                      {m.title || m.memory_key || m.memory_id} · {m.type}
                    </option>
                  ))}
              </select>
              <button
                disabled={busy || !parentOf}
                title={parentOf
                  ? "Records a part_of link. A link that would close a cycle is refused."
                  : "Choose the memory this one rolls up into."}
                onClick={() =>
                  act("Linked.", async () => {
                    // The path carries the child; the body carries the parent.
                    // `part_of` points from the part to the whole, and reversing
                    // it says the programme is part of its workstream.
                    await call(`api/v1/memories/${selected.memory_id}/links`, {
                      to_memory: parentOf, relation: "part_of",
                    });
                    setParentOf("");
                    setTree(await call<MemoryTree>(
                      `api/v1/memories/${selected.memory_id}/tree`));
                  })
                }
              >
                Link
              </button>
            </div>

            <h3>Make something from it</h3>
            <div className="row">
              <label style={{ flex: 1 }}>
                Generator
                <select value={generator} onChange={(e) => setGenerator(e.target.value)}>
                  {generators.map((g) => (
                    <option key={g.name} value={g.name}>{g.label}</option>
                  ))}
                </select>
              </label>
              <button
                disabled={busy}
                title="Reads the members and produces one artifact. Nothing is archived — that is what compaction adds, and only a summary may do it."
                onClick={() =>
                  act("Derived.", async () => {
                    await call(`api/v1/memories/${selected.memory_id}/derive`,
                               { generator });
                    setDerived((await call<{ artifacts: typeof derived }>(
                      `api/v1/memories/${selected.memory_id}/artifacts`)).artifacts);
                  })
                }
              >
                Derive
              </button>
            </div>
            <p className="empty">
              {generators.find((g) => g.name === generator)?.describe}
              {" "}It reads <strong>through the hierarchy</strong> — a parent covers what its
              children hold — and takes the <strong>strictest access level among its
              sources</strong>, so one built over a private record is private. Nothing is
              archived: that is compaction&rsquo;s policy, and <strong>only a summary is allowed
              to do it</strong> — a flashcard deck that folded the course away would leave itself
              as the only remaining copy of it.
            </p>
            {derived.length > 0 && (
              <div className="excluded">
                {derived.map((a) => (
                  <div className="item" key={a.artifact_id}>
                    <span className="chip on">{a.kind}</span>
                    <strong>{a.title}</strong>
                    <span className="chip">{a.access_level}</span>
                    <span className="empty">{a.sources} source{a.sources === 1 ? "" : "s"}</span>
                    <span className="empty far">
                      {new Date(a.created_at).toLocaleString()}
                    </span>
                  </div>
                ))}
              </div>
            )}

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
  const [certId, setCertId] = useState("");
  const [cert, setCert] = useState<Certificate | null>(null);
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

      <section className="panel">
        <h2>Prove it</h2>
        <p className="empty" style={{ marginTop: 0 }}>
          A certificate is <strong>evidence rather than a claim</strong>: it re-queries every table
          that holds item-scoped data instead of trusting that the cascade ran — the cascade being
          the thing under test. It answers <em>after</em> the record is gone, which is the whole
          point, and it is issued against <code>purged_at</code> rather than <code>deleted_at</code>
          — a tombstone is a promise, and the purge is the thing that kept it.
        </p>
        <div className="row">
          <input type="text" value={certId} placeholder="data_… — a record you deleted"
                 onChange={(e) => setCertId(e.target.value)} style={{ flex: 1, minWidth: 260 }} />
          <button
            className="secondary"
            disabled={busy || !certId.trim()}
            onClick={async () => {
              setBusy(true); setError(null); setCert(null);
              try {
                setCert(await call<Certificate>(
                  `api/v1/data/${certId.trim()}/erasure`, undefined, "GET"));
              } catch (e) {
                setError((e as Error).message);
              } finally { setBusy(false); }
            }}
          >
            Check the erasure
          </button>
        </div>
        {cert && (
          <>
            <p className={cert.complete ? "ok" : "warned"}>
              {cert.complete
                ? "Complete. Nothing item-scoped survives, the content is cleared, and the purge is recorded."
                : "Not complete. This is the honest answer rather than a certificate that says what it was asked to say."}
            </p>
            <table className="kv">
              <tbody>
                <tr><td>record</td><td><code>{cert.data_id}</code></td></tr>
                <tr>
                  <td>purged</td>
                  <td>{cert.purged_at
                    ? new Date(cert.purged_at).toLocaleString()
                    : "not yet — tombstoned, and the cascade has not finished"}</td>
                </tr>
                <tr>
                  <td>content</td>
                  <td>{cert.content_cleared
                    ? "text, extraction and stored bytes all gone"
                    : "still present"}</td>
                </tr>
                <tr>
                  <td>what remains</td>
                  <td>
                    {Object.keys(cert.remaining).length === 0 ? "nothing" : (
                      <code>{Object.entries(cert.remaining)
                        .map(([table, n]) => `${table}: ${n}`).join(" · ")}</code>
                    )}
                  </td>
                </tr>
              </tbody>
            </table>
            <p className="empty" style={{ marginBottom: 0 }}>
              The tables checked are chunks, embeddings, versions, artifact sources, query sources,
              memory and case membership, entity mentions and edges, normalized records and share
              links — plus one invariant: <strong>no derived fact may still be open with nothing
              supporting it</strong>.
            </p>
          </>
        )}
      </section>

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

/**
 * Settings — read the effective value, and change it.
 *
 * It listed eleven settings and could set none of them, which made the one
 * switch that governs whether writes are interpreted at all
 * (`enrich_by_default`) reachable only by calling the API by hand. A screen
 * that shows a value, its source and its lock state and then cannot write it
 * is half a screen, and the missing half is the one people came for.
 *
 * Two things it refuses to hide. **The scope is chosen, never assumed** — the
 * same key means different things set for a user and set for an org, and
 * picking one silently would make the wrong one the easy one. And **a refusal
 * is shown as the server phrased it**: locked above, not one of these three
 * values, not a whole number. Those are the sentences that say what to do next.
 */
function SettingsSection({ projectId }: { projectId: string }) {
  const [rows, setRows] = useState<Setting[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(() => {
    call<{ settings: Setting[] }>(`api/v1/settings/effective?project_id=${projectId}`)
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
      {note && <p className="empty">{note}</p>}

      <section className="panel">
        <h2>Effective values, and where each came from</h2>
        {rows.length === 0 && <p className="empty">Loading…</p>}
        {rows.map((s) => (
          <SettingRow
            key={s.key}
            setting={s}
            projectId={projectId}
            open={editing === s.key}
            onToggle={() => setEditing(editing === s.key ? null : s.key)}
            onSaved={(message) => {
              setNote(message);
              setError(null);
              setEditing(null);
              load();
            }}
            onError={(message) => {
              setError(message);
              setNote(null);
            }}
          />
        ))}
        <p className="empty">
          Provenance is the point. &ldquo;It is set to X&rdquo; is not actionable without &ldquo;by
          whom, at which level, and can I change it&rdquo;.
        </p>
      </section>
    </>
  );
}

/** How a value reads at a glance. `null` is a value here, not an absence. */
function showValue(value: unknown): string {
  if (value === null) return "null";
  if (Array.isArray(value)) return value.length === 0 ? "[] — everything" : value.join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** The value, in whatever form its control edits. */
function asDraft(setting: Setting): string {
  if (setting.kind === "bool") return setting.value === true ? "true" : "false";
  if (setting.kind === "list") {
    return Array.isArray(setting.value) ? (setting.value as string[]).join(", ") : "";
  }
  if (setting.kind === "object") return JSON.stringify(setting.value ?? {});
  return setting.value === null ? "" : String(setting.value);
}

function SettingRow({
  setting,
  projectId,
  open,
  onToggle,
  onSaved,
  onError,
}: {
  setting: Setting;
  projectId: string;
  open: boolean;
  onToggle: () => void;
  onSaved: (message: string) => void;
  onError: (message: string) => void;
}) {
  // The most specific scope this setting is allowed at, which is the one a
  // person on a project screen almost always means.
  const preferred =
    ["project", "user", "org", "platform"].find((s) => setting.allowed_scopes.includes(s)) ??
    setting.allowed_scopes[0];
  const [scope, setScope] = useState(preferred);
  const [draft, setDraft] = useState(() => asDraft(setting));
  const [lock, setLock] = useState(false);
  const [busy, setBusy] = useState(false);

  // The row stays mounted after a save, so without this the editor would
  // reopen showing the value the setting used to have -- which is the reading
  // people trust least and check hardest.
  useEffect(() => setDraft(asDraft(setting)), [setting]);

  // Locked above the scope being written, so the write would be refused with a
  // 409. Said before the attempt rather than after it.
  const order = ["user", "project", "org", "platform"];
  const blocked =
    setting.locked_by !== null &&
    order.indexOf(scope) < order.indexOf(setting.locked_by);

  async function save() {
    setBusy(true);
    try {
      let value: unknown;
      if (setting.kind === "bool") value = draft === "true";
      else if (setting.kind === "int") value = draft === "" ? null : Number(draft);
      else if (setting.kind === "list") {
        value = draft.split(",").map((v) => v.trim()).filter(Boolean);
      } else if (setting.kind === "object") value = JSON.parse(draft);
      else if (setting.nullable && draft === "") value = null;
      else value = draft;

      const body: Record<string, unknown> = { value, lock };
      if (scope === "project") body.project_id = projectId;
      const saved = await call<{ value: unknown; source: string; locked_by: string | null }>(
        `api/v1/settings/${scope}/${setting.key}`, body, "PUT",
      );
      onSaved(
        `${setting.key} is now ${showValue(saved.value)}, from ${saved.source}` +
          (saved.locked_by ? `, locked at ${saved.locked_by}.` : "."),
      );
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="setrow">
      <button className="setline" onClick={onToggle} aria-expanded={open}>
        <code className="setkey">{setting.key}</code>
        <span className="chip on">{showValue(setting.value)}</span>
        <span className="chip">from {setting.source}</span>
        {setting.locked_by && <span className="why">locked at {setting.locked_by}</span>}
        <span className="empty far">{open ? "close" : "change"}</span>
      </button>
      <p className="setdesc">{setting.description}</p>
      {open && (
        <div className="setedit">
          <div className="row">
            <label>
              Set at
              <select value={scope} onChange={(e) => setScope(e.target.value)}>
                {setting.allowed_scopes.map((s: string) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </label>

            {setting.kind === "bool" && (
              <label>
                Value
                <select value={draft} onChange={(e) => setDraft(e.target.value)}>
                  <option value="true">true</option>
                  <option value="false">false</option>
                </select>
              </label>
            )}
            {setting.kind === "enum" && (
              <label>
                Value
                <select value={draft} onChange={(e) => setDraft(e.target.value)}>
                  {setting.choices.map((c: unknown) => (
                    <option key={String(c)} value={String(c)}>{String(c)}</option>
                  ))}
                </select>
              </label>
            )}
            {(setting.kind === "int" || setting.kind === "string" || setting.kind === "list" ||
              setting.kind === "object") && (
              <label style={{ flex: 1 }}>
                Value
                <input
                  type={setting.kind === "int" ? "number" : "text"}
                  value={draft}
                  placeholder={
                    setting.kind === "list"
                      ? "comma separated — empty means every provider"
                      : setting.nullable
                        ? "empty means null"
                        : ""
                  }
                  onChange={(e) => setDraft(e.target.value)}
                />
              </label>
            )}

            {setting.lockable && scope !== "user" && (
              <label className="check">
                <input type="checkbox" checked={lock} onChange={(e) => setLock(e.target.checked)} />
                Lock it here
              </label>
            )}
            <button
              disabled={busy || blocked}
              title={
                blocked
                  ? `Locked at ${setting.locked_by} scope. A write below a lock is refused, not quietly ignored.`
                  : `Writes ${setting.key} at ${scope} scope and records who did it.`
              }
              onClick={() => void save()}
            >
              {busy ? "Saving…" : "Save"}
            </button>
          </div>
          {blocked && (
            <p className="warned">
              Locked at <strong>{setting.locked_by}</strong>, so a value written at{" "}
              <strong>{scope}</strong> would be refused rather than silently ignored — which is what
              makes the lock a control. Change it at {setting.locked_by} scope, or unlock it there.
            </p>
          )}
          {setting.kind === "bool" && setting.key === "enrich_by_default" && (
            <p className="empty" style={{ marginBottom: 0 }}>
              On, a write that says nothing either way is embedded and summarised. Off, it is stored
              and durable and <strong>cannot be found by search</strong> — which is the right default
              for a producer pushing volume and the wrong one for a project whose data people expect
              to query.
            </p>
          )}
          {!setting.lockable && (
            <p className="empty" style={{ marginBottom: 0 }}>
              Not lockable — it is a preference rather than a policy, so a level below can always
              override it.
            </p>
          )}
          <p className="empty" style={{ marginBottom: 0 }}>
            Every write here is audited as <code>settings.set</code>, with the scope and the value.
          </p>
        </div>
      )}
    </div>
  );
}

/**
 * What a memory scope actually covers.
 *
 * An alert on a parent now matches writes to every memory `part_of` it, which
 * is the behaviour people expect and is nowhere visible in a dropdown that
 * shows one name. Read at edit time from the same walk the matcher uses, so
 * the sentence cannot drift from the behaviour.
 */
function ScopeReach({ memoryId }: { memoryId: string }) {
  const [tree, setTree] = useState<MemoryTree | null>(null);
  useEffect(() => {
    let live = true;
    void call<MemoryTree>(`api/v1/memories/${memoryId}/tree`)
      .then((t) => live && setTree(t))
      .catch(() => live && setTree(null));
    return () => { live = false; };
  }, [memoryId]);

  if (tree === null || tree.descendants.length === 0) return null;
  return (
    <span className="chip on" title={tree.descendants
      .map((d) => d.title || d.memory_key || d.memory_id).join(", ")}>
      includes {tree.descendants.length} child memor
      {tree.descendants.length === 1 ? "y" : "ies"}
    </span>
  );
}

/* ----------------------------------------------------------- workflows */

type WorkflowDefinition = {
  definition_id: string;
  external_id: string;
  name: string;
  description: string | null;
  config: {
    initial: string;
    states: Record<string, { ttl_seconds?: number; on_timeout?: string; terminal?: boolean }>;
    transitions: { from: string; on: string; to: string; requires_actor_kind?: string }[];
  };
  config_version: number;
  max_transitions: number;
  running: number;
};

type Instance = {
  instance_id: string;
  external_id: string;
  current_state: string;
  current_seq: number;
  status: string;
  deadline_at: string | null;
  transition_count: number;
  overdue: boolean;
  definition: string;
  definition_name: string;
};

type Transition = {
  transition_id: string;
  seq: number;
  from_state: string;
  to_state: string;
  trigger: string;
  actor_kind: string;
  data_id: string | null;
  occurred_at: string;
};

type Verification = {
  folded_state: string | null;
  cached_state: string;
  agrees: boolean;
  sequence_gaps: number[];
  chain_breaks: number[];
  transitions: number;
};

const SAMPLE_WORKFLOW = JSON.stringify(
  {
    initial: "draft",
    states: {
      draft: {},
      review: { ttl_seconds: 172800, on_timeout: "escalated" },
      escalated: {},
      approved: { terminal: true },
    },
    transitions: [
      { from: "draft", on: "submit", to: "review" },
      { from: "review", on: "approve", to: "approved", requires_actor_kind: "human" },
      { from: "review", on: "reject", to: "draft" },
      { from: "escalated", on: "approve", to: "approved" },
    ],
  },
  null,
  2,
);

/**
 * Workflows — state a long-running process can be asked about.
 *
 * The engine lives outside; this is the system of record. So the screen shows
 * the two things a record owes anyone: **where each instance is**, and **how it
 * got there** — the history rather than the current state, because a graph that
 * may contain cycles can visit `blocked` four times and "when did it enter
 * blocked" then has four answers.
 *
 * Overdue is the number the dashboard exists for. With no topological order,
 * *stuck* is not derivable from position: a deadline is the only thing that can
 * say it, which is why a state with a TTL and no `on_timeout` is refused at
 * definition time rather than firing forever.
 */
function WorkflowsSection({ projectId }: { projectId: string }) {
  const [definitions, setDefinitions] = useState<WorkflowDefinition[] | null>(null);
  const [instances, setInstances] = useState<Instance[]>([]);
  const [onlyOverdue, setOnlyOverdue] = useState(false);
  const [selected, setSelected] = useState<Instance | null>(null);
  const [log, setLog] = useState<Transition[]>([]);
  const [proof, setProof] = useState<Verification | null>(null);

  const [config, setConfig] = useState(SAMPLE_WORKFLOW);
  const [externalId, setExternalId] = useState("approval");
  const [startId, setStartId] = useState("");
  const [startFrom, setStartFrom] = useState("");
  const [trigger, setTrigger] = useState("");

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [defs, live] = await Promise.all([
        call<{ workflows: WorkflowDefinition[] }>(`api/v1/projects/${projectId}/workflows`),
        call<{ instances: Instance[] }>(
          `api/v1/projects/${projectId}/instances?limit=100${onlyOverdue ? "&overdue=true" : ""}`),
      ]);
      setDefinitions(defs.workflows);
      setInstances(live.instances);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [projectId, onlyOverdue]);
  useEffect(() => { void load(); }, [load]);

  async function act(message: string, run: () => Promise<unknown>) {
    setBusy(true); setError(null); setNote(null);
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

  async function open(instance: Instance) {
    setSelected(instance);
    setProof(null);
    setTrigger("");
    try {
      setLog((await call<{ transitions: Transition[] }>(
        `api/v1/instances/${instance.instance_id}/history?limit=50`)).transitions);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  // What this instance can be told next, read from its own definition rather
  // than typed by the reader. An input the state does not accept is a 409, and
  // offering one is offering a mistake.
  const accepted = (() => {
    if (!selected) return [];
    const def = definitions?.find((d) => d.external_id === selected.definition);
    return (def?.config.transitions ?? [])
      .filter((t) => t.from === selected.current_state)
      .map((t) => t.on);
  })();

  return (
    <>
      <h1>Workflows</h1>
      <p className="lede">
        A long-running process, recorded rather than run: the engine lives outside and calls in.
        The state graph <strong>may contain cycles</strong> — <code>review → reject → draft</code>{" "}
        is a workflow, not a bug — so progress cannot be measured as depth and a deadline is the
        only thing that can say an instance is stuck.
      </p>
      {error && <p className="err">{error}</p>}
      {note && <p className="empty">{note}</p>}

      <section className="panel">
        <h2>Define one</h2>
        <div className="row">
          <label>
            Key
            <input type="text" value={externalId} placeholder="approval"
                   onChange={(e) => setExternalId(e.target.value)} />
          </label>
          <button
            disabled={busy || !externalId.trim()}
            title="Redefining bumps the version and leaves running instances where they are — each pinned the version it started on."
            onClick={() =>
              act("Saved. Running instances stay on the version they started on.", async () => {
                await call("api/v1/workflows", {
                  project_id: projectId, external_id: externalId.trim(),
                  name: externalId.trim(), config: JSON.parse(config),
                }, "PUT");
              })
            }
          >
            Save definition
          </button>
        </div>
        <textarea rows={14} value={config} onChange={(e) => setConfig(e.target.value)} />
        <p className="empty" style={{ marginBottom: 0 }}>
          Refused at definition time, because none of it can be fixed later for an instance
          already sitting in it: a state nothing reaches, a transition to a state that does not
          exist, a terminal state with a way out, and <strong>a TTL with no{" "}
          <code>on_timeout</code></strong> — a deadline with nowhere to go fires forever. Cycles
          pass, deliberately.
        </p>
      </section>

      {definitions !== null && definitions.length > 0 && (
        <section className="panel">
          <h2>Defined here</h2>
          <div className="excluded">
            {definitions.map((d) => (
              <div className="item" key={d.definition_id}>
                <span className="chip on">{d.external_id}</span>
                <span className="empty">
                  {Object.keys(d.config.states).length} states ·{" "}
                  {d.config.transitions.length} transitions
                </span>
                <span className="chip">v{d.config_version}</span>
                <span className="empty">{d.running} running</span>
                <span className="empty far">budget {d.max_transitions}</span>
              </div>
            ))}
          </div>
          <div className="row" style={{ marginTop: 10 }}>
            <select value={startFrom} onChange={(e) => setStartFrom(e.target.value)}>
              <option value="">start an instance of…</option>
              {definitions.map((d) => (
                <option key={d.definition_id} value={d.definition_id}>{d.external_id}</option>
              ))}
            </select>
            <input type="text" value={startId} placeholder="its id — po-4471"
                   onChange={(e) => setStartId(e.target.value)} />
            <button
              disabled={busy || !startFrom || !startId.trim()}
              title="Idempotent on the id: starting twice returns the same instance, because an engine retrying after a timeout is the ordinary case."
              onClick={() =>
                act("Started.", async () => {
                  await call(`api/v1/workflows/${startFrom}/instances`,
                             { external_id: startId.trim() });
                  setStartId("");
                })
              }
            >
              Start
            </button>
          </div>
        </section>
      )}

      <section className="panel">
        <h2>Running</h2>
        <div className="row">
          <label className="check">
            <input type="checkbox" checked={onlyOverdue}
                   onChange={(e) => setOnlyOverdue(e.target.checked)} />
            Only what is past its deadline
          </label>
        </div>
        {instances.length === 0 ? (
          <p className="empty" style={{ marginBottom: 0 }}>
            {onlyOverdue
              ? "Nothing is overdue. That is the answer this filter exists to give quickly."
              : "No instances yet."}
          </p>
        ) : (
          <div className="excluded">
            {instances.map((i) => (
              <button
                className={`item memrow${selected?.instance_id === i.instance_id ? " chosen" : ""}`}
                key={i.instance_id}
                onClick={() => void open(i)}
              >
                <span className={`chip ${i.status === "running" ? "on" : ""}`}>
                  {i.current_state}
                </span>
                <strong>{i.external_id}</strong>
                <span className="empty">{i.definition}</span>
                <span className="empty">seq {i.current_seq}</span>
                {i.overdue && <span className="chip warnchip">overdue</span>}
                {i.status !== "running" && <span className="chip">{i.status}</span>}
                <span className="empty far">
                  {i.deadline_at
                    ? `due ${new Date(i.deadline_at).toLocaleString()}`
                    : "no deadline in this state"}
                </span>
              </button>
            ))}
          </div>
        )}
      </section>

      {selected && (
        <>
          <section className="panel">
            <h2>{selected.external_id} · {selected.current_state}</h2>
            <div className="row">
              <select value={trigger} onChange={(e) => setTrigger(e.target.value)}>
                <option value="">what happened…</option>
                {accepted.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
              <button
                disabled={busy || !trigger}
                title={`Sends the input naming sequence ${selected.current_seq}. If somebody else moved it first this comes back 409 with where it actually is.`}
                onClick={() =>
                  act("Moved.", async () => {
                    await call(
                      `api/v1/instances/${selected.instance_id}/input`,
                      { trigger, expected_seq: selected.current_seq });
                    // Re-read rather than assume. Constructing the new state
                    // client-side would be right until a deadline fired between
                    // the two, and then the screen would be confidently wrong
                    // about an instance somebody is acting on.
                    const now = await call<Instance>(
                      `api/v1/instances/${selected.instance_id}`);
                    await open({ ...selected, ...now });
                  })
                }
              >
                Apply
              </button>
              <button
                className="secondary"
                disabled={busy}
                title="Re-folds the transition log and compares it to the cached state."
                onClick={() =>
                  act("Verified.", async () => {
                    setProof(await call<Verification>(
                      `api/v1/instances/${selected.instance_id}/verify`));
                  })
                }
              >
                Prove the state
              </button>
            </div>
            {accepted.length === 0 && (
              <p className="empty">
                Nothing is accepted here — a terminal state, or a state whose only way out is a
                deadline.
              </p>
            )}
            {proof && (
              <p className={proof.agrees ? "ok" : "err"}>
                {proof.agrees
                  ? `Folded ${proof.transitions} transitions and got ${proof.folded_state} — the cached state agrees.`
                  : `The log folds to ${proof.folded_state} and the cache says ${proof.cached_state}.`}
                {proof.sequence_gaps.length > 0 &&
                  ` Sequence gaps at ${proof.sequence_gaps.join(", ")} — a transition was lost.`}
                {proof.chain_breaks.length > 0 &&
                  ` Chain breaks at ${proof.chain_breaks.join(", ")} — two transitions disagree about where it was.`}
              </p>
            )}
          </section>

          <section className="panel">
            <h2>How it got here</h2>
            <div className="excluded">
              {log.map((t) => (
                <div className="item" key={t.transition_id}>
                  <span className="chip">{t.seq}</span>
                  <code>
                    {t.from_state || "—"} → {t.to_state}
                  </code>
                  <span className={t.trigger.startsWith("@") ? "chip warnchip" : "chip on"}>
                    {t.trigger}
                  </span>
                  <span className="empty">{t.actor_kind}</span>
                  {t.data_id && <span className="empty">{t.data_id}</span>}
                  <span className="empty far">
                    {new Date(t.occurred_at).toLocaleString()}
                  </span>
                </div>
              ))}
            </div>
            <p className="empty" style={{ marginBottom: 0 }}>
              The history is the record and the state is a cache of it. A state can be entered
              many times, so <em>when did this enter review</em> has as many answers as there are
              rows — which is why the log is stored well and the column is only checked against it.
              A <code>@</code> trigger is the clock, and it is not something a caller can send.
            </p>
          </section>
        </>
      )}
    </>
  );
}

/* ----------------------------------------------------- standing queries */

type StandingQuery = {
  query_id: string;
  name: string;
  kind: string;
  date_field: string | null;
  offset_days: number | null;
  window_days: number;
  selector: { query?: string; data_type?: string; tags?: string[]; producer_id?: string };
  delivery: { kind: string; memory_key?: string; memory_type?: string };
  enabled: boolean;
  approved: boolean;
  watermark: number;
  matches: number;
  last_match_at: string | null;
  last_run_at: string | null;
};

type StandingMatch = {
  match_id: string;
  data_id: string;
  sequence: number;
  matched_at: string;
  visible: boolean;
  external_id: string | null;
  data_type: string | null;
  preview: string | null;
};

type StandingBacktest = {
  candidates: number;
  matches: number;
  withheld: number;
  deferred: number;
  samples: { data_id: string; external_id: string | null; data_type: string | null;
             preview: string | null; visible: boolean }[];
};

/**
 * Standing queries — the one thing here that speaks first.
 *
 * Everything else answers when asked. This screen exists to make the three
 * rules of that visible rather than documented: it never re-scans, so a query
 * only ever sees what arrives after it was registered; delivery is a read, so a
 * match the owner cannot see is withheld and *counted*; and time is not a
 * selector, which is why there is no "in thirty days" field here and a sentence
 * saying where that lives instead.
 *
 * The backtest shows what it caught, not how much. A selector that matches
 * everything and one that works produce the same number.
 */
function StandingSection({ projectId }: { projectId: string }) {
  const [queries, setQueries] = useState<StandingQuery[] | null>(null);
  const [selected, setSelected] = useState<StandingQuery | null>(null);
  const [feed, setFeed] = useState<{ matches: StandingMatch[]; withheld: number } | null>(null);
  const [subs, setSubs] = useState<Subscription[]>([]);
  const [hookUrl, setHookUrl] = useState("");
  const [secret, setSecret] = useState<string | null>(null);
  const [backtest, setBacktest] = useState<StandingBacktest | null>(null);

  const [name, setName] = useState("");
  const [text, setText] = useState("");
  const [dataType, setDataType] = useState("");
  const [tags, setTags] = useState("");
  const [deliverTo, setDeliverTo] = useState("");
  // Two mechanisms, one surface. `arrival` matches new writes and never
  // re-scans; `date` fires when the calendar reaches a record, which no
  // predicate over new writes can do because nothing arrives that day.
  const [kind, setKind] = useState<"arrival" | "date">("arrival");
  const [dateField, setDateField] = useState("event_time");
  const [offsetDays, setOffsetDays] = useState("30");
  const [windowDays, setWindowDays] = useState("3");

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setQueries((await call<{ queries: StandingQuery[] }>(
        `api/v1/projects/${projectId}/standing-queries`)).queries);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [projectId]);
  useEffect(() => { void load(); }, [load]);

  async function act(message: string, run: () => Promise<unknown>) {
    setBusy(true); setError(null); setNote(null);
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

  async function open(q: StandingQuery) {
    setSelected(q);
    setBacktest(null);
    setError(null);
    try {
      setFeed(await call<{ matches: StandingMatch[]; withheld: number }>(
        `api/v1/standing-queries/${q.query_id}/matches?limit=50`));
      const all = await call<{ subscriptions: Subscription[] }>(
        `api/v1/projects/${projectId}/event-subscriptions`);
      setSubs(all.subscriptions.filter((sub) => sub.standing_query_id === q.query_id));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const selector = () => {
    const s: Record<string, unknown> = {};
    if (text.trim()) s.query = text.trim();
    if (dataType.trim()) s.data_type = dataType.trim();
    if (tags.trim()) s.tags = tags.split(",").map((t) => t.trim()).filter(Boolean);
    return s;
  };
  const narrows = text.trim() !== "" || dataType.trim() !== "" || tags.trim() !== "";

  return (
    <>
      <h1>Standing queries</h1>
      <p className="lede">
        Everything else here answers when asked. This is the one thing that speaks first: say once
        what you want to be told about, and each new record is checked against it as it arrives.
      </p>
      {error && <p className="err">{error}</p>}
      {note && <p className="empty">{note}</p>}

      <section className="panel">
        <h2>Watch for something</h2>
        <div className="scopes">
          <button className={`scope${kind === "arrival" ? " active" : ""}`}
                  onClick={() => setKind("arrival")}>
            <span className="scope-label">When it arrives</span>
            <span className="scope-blast">
              Each new record is checked as it is written. Never re-scans, so it costs nothing
              per record however many rules you register.
            </span>
          </button>
          <button className={`scope${kind === "date" ? " active" : ""}`}
                  onClick={() => setKind("date")}>
            <span className="scope-label">When it comes due</span>
            <span className="scope-blast">
              Fires when the calendar reaches a record — a renewal, a deadline, or something
              old enough to retire. No predicate over new writes can do this: nothing arrives
              on the day a deadline approaches.
            </span>
          </button>
        </div>
        {kind === "date" && (
          <div className="row">
            <label>
              Which date
              <select value={dateField} onChange={(e) => setDateField(e.target.value)}>
                <option value="event_time">event_time — the record&rsquo;s own time</option>
                <option value="ingested_at">ingested_at — when it arrived</option>
                <option value="metadata.deadline">metadata.deadline</option>
                <option value="metadata.renewal_date">metadata.renewal_date</option>
                <option value="metadata.due_date">metadata.due_date</option>
              </select>
            </label>
            <label>
              Days from now
              <input type="number" value={offsetDays} style={{ width: 110 }}
                     onChange={(e) => setOffsetDays(e.target.value)} />
            </label>
            <label>
              Window (± days)
              <input type="number" min="1" value={windowDays} style={{ width: 110 }}
                     onChange={(e) => setWindowDays(e.target.value)} />
            </label>
          </div>
        )}
        {kind === "date" && (
          <p className="empty" style={{ marginTop: 0 }}>
            <strong>Negative is the past</strong> — <code>-2555</code> is &ldquo;older than seven
            years&rdquo;, which is what retention ageing means. Retention and deadlines are the
            same rule pointed opposite ways. The window matters: a rule matching a date exactly N
            days away <em>to the second</em> fires never. Model-extracted dates are not selectable
            yet — nothing normalises them to a date, so a rule over them would compare strings and
            silently match nothing.
          </p>
        )}
        <div className="row">
          <label style={{ flex: 1 }}>
            Name
            <input type="text" value={name} placeholder="what this is for"
                   onChange={(e) => setName(e.target.value)} />
          </label>
          <label style={{ flex: 2 }}>
            Words
            <input type="text" value={text}
                   placeholder={'acme  ·  "data breach" -rumour  ·  outage or incident'}
                   onChange={(e) => setText(e.target.value)} />
          </label>
        </div>
        <div className="row">
          <label>
            Type
            <input type="text" value={dataType} placeholder="email · issue · note"
                   onChange={(e) => setDataType(e.target.value)} />
          </label>
          <label style={{ flex: 1 }}>
            Tags
            <input type="text" value={tags} placeholder="source:acme, priority"
                   onChange={(e) => setTags(e.target.value)} />
          </label>
          <label style={{ flex: 1 }}>
            Collect into a memory
            <input type="text" value={deliverTo} placeholder="optional — a memory key"
                   onChange={(e) => setDeliverTo(e.target.value)} />
          </label>
          <button
            disabled={busy || (kind === "arrival" && !narrows) || !name.trim()}
            title={kind === "arrival" && !narrows
              ? "Narrow it first. A selector matching everything makes the feed a copy of the project."
              : "Created disabled — it has to be backtested before it can start."}
            onClick={() =>
              act("Created. Backtest it, then enable it.", async () => {
                await call("api/v1/standing-queries", {
                  project_id: projectId, name: name.trim(), selector: selector(),
                  kind,
                  ...(kind === "date"
                    ? { date_field: dateField, offset_days: Number(offsetDays),
                        window_days: Number(windowDays) }
                    : {}),
                  delivery: deliverTo.trim()
                    ? { kind: "memory", memory_key: deliverTo.trim() }
                    : { kind: "poll" },
                });
                setName(""); setText(""); setDataType(""); setTags(""); setDeliverTo("");
              })
            }
          >
            Create
          </button>
        </div>
        <p className="empty" style={{ marginBottom: 0 }}>
          Quoted phrases, <code>or</code> and <code>-exclusion</code> all work — it is the same
          lexical matching search uses, so a standing query and a search agree about what the words
          mean. <strong>No model is called anywhere in this path</strong>, so a query costs nothing
          per record however many you register.
        </p>
        <p className="warned" style={{ marginBottom: 0 }}>
          <strong>&ldquo;Thirty days before a due date&rdquo; is not one of these.</strong> Nothing
          arrives on that day, so no predicate over new writes can catch it — that needs a scheduled
          sweep over dates, which is what expiry does and this deliberately does not.
        </p>
      </section>

      <section className="panel">
        <h2>What is being watched</h2>
        {queries === null ? (
          <p className="empty">Loading…</p>
        ) : queries.length === 0 ? (
          <p className="empty">
            Nothing yet. A standing query earns its place when you would otherwise be searching for
            the same thing on a schedule.
          </p>
        ) : (
          <div className="excluded">
            {queries.map((q) => (
              <button
                className={`item memrow${selected?.query_id === q.query_id ? " chosen" : ""}`}
                key={q.query_id}
                onClick={() => void open(q)}
              >
                <span className={`chip ${q.enabled ? "on" : ""}`}>
                  {q.enabled ? "watching" : q.approved ? "ready" : "not backtested"}
                </span>
                <strong>{q.name}</strong>
                <code>
                  {q.kind === "date"
                    ? `${q.date_field} ${(q.offset_days ?? 0) >= 0 ? "+" : ""}${q.offset_days}d ±${q.window_days}`
                    : q.selector.query ?? Object.keys(q.selector).join(" · ")}
                </code>
                {q.delivery.kind === "memory" && (
                  <span className="chip">→ {q.delivery.memory_key}</span>
                )}
                <span className="empty">{q.matches} match{q.matches === 1 ? "" : "es"}</span>
                <span className="empty far">
                  {q.last_match_at
                    ? `last ${new Date(q.last_match_at).toLocaleString()}`
                    : q.enabled ? "nothing yet" : "not started"}
                </span>
              </button>
            ))}
          </div>
        )}
      </section>

      {selected && (
        <>
          <section className="panel">
            <h2>{selected.name}</h2>
            <div className="row">
              <button
                className="secondary"
                disabled={busy}
                title="Runs the selector over everything already here and shows what it would have caught. Writes nothing."
                onClick={() =>
                  act("Backtested — nothing was recorded.", async () => {
                    setBacktest(await call<StandingBacktest>(
                      `api/v1/standing-queries/${selected.query_id}/backtest`,
                      { from_sequence: 0 }));
                  })
                }
              >
                Backtest
              </button>
              <button
                disabled={busy || (!selected.enabled && !selected.approved)}
                title={!selected.enabled && !selected.approved
                  ? "Backtest this version first — a selector that matches everything looks exactly like one that works until you read what it caught."
                  : selected.enabled ? "Stops it watching." : "Starts it. It sees what arrives from now on."}
                onClick={() =>
                  act(selected.enabled ? "Stopped." : "Watching from now on.", async () => {
                    await call(`api/v1/standing-queries/${selected.query_id}/enabled`,
                               { enabled: !selected.enabled });
                    setSelected({ ...selected, enabled: !selected.enabled });
                  })
                }
              >
                {selected.enabled ? "Stop" : "Start watching"}
              </button>
              <button
                className="linkish far"
                disabled={busy}
                onClick={() =>
                  act("Deleted.", async () => {
                    await call(`api/v1/standing-queries/${selected.query_id}`,
                               undefined, "DELETE");
                    setSelected(null);
                    setFeed(null);
                  })
                }
              >
                Delete
              </button>
            </div>
            <p className="empty" style={{ marginBottom: 0 }}>
              It sees records written <strong>after it was registered</strong> — a query is a
              statement about what arrives next, and starting one at the beginning of the corpus
              would replay the whole history into its feed. Use the backtest to look backwards.
            </p>
          </section>

          {backtest && (
            <section className="panel">
              <h2>It would have caught {backtest.matches}</h2>
              <div className="row">
                <span className="chip on">{backtest.matches} matched</span>
                {backtest.withheld > 0 && (
                  <span className="chip warnchip">{backtest.withheld} withheld</span>
                )}
                <span className="chip">{backtest.candidates} considered</span>
                {backtest.deferred > 0 && (
                  <span className="chip">{backtest.deferred} not yet looked at</span>
                )}
              </div>
              {backtest.withheld > 0 && (
                <p className="warned">
                  {backtest.withheld} of these matched records <strong>you cannot see</strong>.
                  They are counted and never shown: a match delivered to somebody without rights to
                  the record would be a leak through the notification channel.
                </p>
              )}
              {backtest.samples.length === 0 ? (
                <p className="empty">
                  Nothing matched. That is a result, not a failure — check the words before
                  assuming the corpus is empty.
                </p>
              ) : (
                <div className="excluded">
                  {backtest.samples.filter((s) => s.visible).map((s) => (
                    <div className="item" key={s.data_id}>
                      <code>{s.external_id ?? s.data_id}</code>
                      <span className="chip">{s.data_type ?? "untyped"}</span>
                      <span className="empty">{s.preview}</span>
                    </div>
                  ))}
                </div>
              )}
              <p className="empty" style={{ marginBottom: 0 }}>
                The matches themselves, rather than a number: a selector that caught the whole
                project and one that works report the same count.
              </p>
            </section>
          )}

          <section className="panel">
            <h2>Be told, rather than asked</h2>
            <p className="empty" style={{ marginTop: 0 }}>
              A match is POSTed to your endpoint, signed with a secret shown once, retried with
              backoff and dead-lettered when it will not go — <strong>the same sender the alerts
              use</strong>, because four controls are only worth something when they are the same
              four everywhere. The body carries a preview and the ids, never the record: a webhook
              body is the least controlled copy of anything here.
            </p>
            <div className="row">
              <input type="text" value={hookUrl} placeholder="https://…"
                     style={{ flex: 1, minWidth: 280 }}
                     onChange={(e) => setHookUrl(e.target.value)} />
              <button
                disabled={busy || !hookUrl.trim()}
                title="https only, and the URL is re-checked on every send — a host that resolves publicly today can resolve to a private address tomorrow."
                onClick={() =>
                  act("Subscribed. The secret is shown once.", async () => {
                    const r = await call<{ signing_secret: string }>(
                      "api/v1/event-subscriptions", {
                        project_id: projectId, url: hookUrl.trim(),
                        standing_query_id: selected.query_id,
                      });
                    setSecret(r.signing_secret);
                    setHookUrl("");
                    await open(selected);
                  })
                }
              >
                Send matches here
              </button>
            </div>
            {secret && (
              <div className="notice" style={{ marginTop: 12 }}>
                <strong>Shown once.</strong> <code>{secret}</code>
                <span className="empty"> Sign with it to verify the delivery came from here;
                it cannot be read back, only rotated.</span>
              </div>
            )}
            {subs.length === 0 ? (
              <p className="empty" style={{ marginBottom: 0 }}>
                Nothing subscribed — matches wait in the feed below until something asks.
              </p>
            ) : (
              <div className="excluded">
                {subs.map((sub) => (
                  <div className="item" key={sub.subscription_id}>
                    <span className={`chip ${sub.enabled ? "on" : ""}`}>
                      {sub.enabled ? "live" : "paused"}
                    </span>
                    <code>{sub.url}</code>
                    {sub.dead > 0 && (
                      <span className="chip warnchip">{sub.dead} undeliverable</span>
                    )}
                    <span className="empty far">{sub.delivered} delivered</span>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="panel">
            <h2>What it has caught</h2>
            {feed === null ? (
              <p className="empty">Loading…</p>
            ) : feed.matches.length === 0 ? (
              <p className="empty">
                {selected.enabled
                  ? "Nothing has matched yet. It is watching — this is what quiet looks like."
                  : "Not started, so nothing has been checked against it."}
              </p>
            ) : (
              <div className="excluded">
                {feed.matches.map((m) => (
                  <div className="item" key={m.match_id}>
                    <code>{m.external_id ?? m.data_id}</code>
                    <span className="chip">{m.data_type ?? "untyped"}</span>
                    <span className="empty">{m.preview}</span>
                    <span className="empty far">
                      {new Date(m.matched_at).toLocaleString()}
                    </span>
                  </div>
                ))}
              </div>
            )}
            {feed && feed.withheld > 0 && (
              <p className="warned">
                {feed.withheld} match{feed.withheld === 1 ? " is" : "es are"} withheld — the query
                matched records you cannot read. Said rather than hidden, because a silently
                incomplete feed is one nobody can explain.
              </p>
            )}
          </section>
        </>
      )}
    </>
  );
}

/* ------------------------------------------------- interpret and rebuild */

type ReprocessPreview = {
  run_id: string;
  items: number;
  stage: string;
  mode: string;
  by_state: Record<string, number>;
  samples: {
    data_id: string;
    external_id: string | null;
    state: string;
    data_type: string | null;
    preview: string | null;
  }[];
  capped: boolean;
};

type StaleArtifact = {
  artifact_id: string;
  kind: string;
  purpose: string;
  model_id: string;
  data_id: string;
  generator_version: string;
};

const STAGES: { key: string; label: string; blurb: string; cost: string }[] = [
  {
    key: "interpret",
    label: "Interpret",
    blurb:
      "For records nobody ever asked to interpret. They sit at stored — durable, and invisible " +
      "to search, because no embedding exists for a query to match.",
    cost: "one model call per chunk to embed, plus one per item to summarise",
  },
  {
    key: "embed",
    label: "Re-embed",
    blurb:
      "Rebuild the vectors. What a changed embedding model or chunk size needs — and until it " +
      "finishes these records drop back to stored, because claiming searchable while the " +
      "vectors are being replaced would be a lie.",
    cost: "one model call per chunk",
  },
  {
    key: "enrich",
    label: "Re-summarise",
    blurb:
      "Rebuild the envelope — title, summary, keywords, entities — from text that is already " +
      "embedded. What a changed prompt or extraction model needs.",
    cost: "one model call per item",
  },
];

/**
 * Interpret a corpus, or rebuild one.
 *
 * Two endpoints existed for months with no caller: `POST /reprocess` and
 * `GET /artifacts/stale`. Between them they answer *what is behind* and *do
 * something about it* — and without a screen, the answer to "I turned
 * enrichment on, what about the fifty thousand records already here" was an
 * API call typed by hand.
 *
 * The preview is a gate rather than a courtesy, for the same reason
 * compaction's is. A selector is one missing key away from every record in the
 * project, the cost is a model call per item, and **a count cannot tell those
 * apart** — 4,212 reads identically whether it caught the crawl you meant or
 * the whole corpus. So the run button stays disabled until this exact selector
 * has been previewed, and editing the selector drops the approval.
 */
function ReprocessSection({ projectId }: { projectId: string }) {
  const [stage, setStage] = useState("interpret");
  const [dataType, setDataType] = useState("");
  const [tags, setTags] = useState("");
  const [runId, setRunId] = useState("");
  const [staleOnly, setStaleOnly] = useState(false);

  const [stair, setStair] = useState<Stair | null>(null);
  const [stale, setStale] = useState<StaleArtifact[] | null>(null);
  const [preview, setPreview] = useState<ReprocessPreview | null>(null);
  const [approved, setApproved] = useState<string | null>(null);
  const [ran, setRan] = useState<ReprocessPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // What was previewed, as a string. If the selector no longer matches it, the
  // approval is stale and the run button closes again.
  const shape = JSON.stringify({ stage, dataType, tags, runId, staleOnly });

  const load = useCallback(async () => {
    try {
      setStair(await call<Stair>(`api/v1/projects/${projectId}/staircase`));
      // The endpoint answers under `stale`, not `artifacts`. Reading the wrong
      // key is the failure that renders an empty screen with a 200 behind it.
      setStale((await call<{ stale: StaleArtifact[] }>(
        "api/v1/artifacts/stale?limit=100")).stale);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [projectId]);
  useEffect(() => {
    void load();
  }, [load]);

  function selector(): Record<string, unknown> {
    const s: Record<string, unknown> = { project_id: projectId };
    if (dataType.trim()) s.data_type = dataType.trim();
    if (tags.trim()) s.tags = tags.split(",").map((t) => t.trim()).filter(Boolean);
    if (runId.trim()) s.run_id = runId.trim();
    if (staleOnly) s.stale_only = true;
    return s;
  }

  // The API refuses a selector that narrows nothing, because "reprocess
  // everything" should not be one empty field away. Said here rather than
  // discovered as a 400.
  const narrows = dataType.trim() !== "" || tags.trim() !== "" || runId.trim() !== "" || staleOnly;

  async function run(dryRun: boolean) {
    setBusy(true);
    setError(null);
    try {
      const result = await call<ReprocessPreview>("api/v1/reprocess", {
        selector: selector(), stage, dry_run: dryRun,
      });
      if (dryRun) {
        setPreview(result);
        setApproved(shape);
        setRan(null);
      } else {
        setRan(result);
        setApproved(null);
        setPreview(null);
        await load();
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const notInterpreted = stair === null ? 0 : stair.total - stair.searchable - stair.enriched;
  const staleCount = stale?.length ?? 0;

  return (
    <>
      <h1>Interpret and rebuild</h1>
      <p className="lede">
        Enrichment is opt-in, and a changed model or prompt does not reach back. So a corpus holds
        two kinds of record that need work: <strong>never interpreted</strong>, which is stored and
        unfindable, and <strong>built by something that is no longer current</strong>.
      </p>
      {error && <p className="err">{error}</p>}

      <section className="panel">
        <h2>What is behind</h2>
        <div className="statgrid">
          <div className={`stattile${notInterpreted > 0 ? " blocked" : ""}`}>
            <div className="hero-value">{notInterpreted}</div>
            <div className="tile-label">never interpreted</div>
            <div className="tile-note">stored and durable · not findable by search</div>
          </div>
          <div className="stattile">
            <div className="hero-value">{stair?.searchable ?? 0}</div>
            <div className="tile-label">embedded, not summarised</div>
            <div className="tile-note">findable, with no title, keywords or entities</div>
          </div>
          <div className={`stattile${staleCount > 0 ? " blocked" : ""}`}>
            <div className="hero-value">{staleCount === 100 ? "100+" : staleCount}</div>
            <div className="tile-label">built by an old generator</div>
            <div className="tile-note">the model or prompt has changed since</div>
          </div>
          <div className="stattile on">
            <div className="hero-value">{stair?.enriched ?? 0}</div>
            <div className="tile-label">fully enriched</div>
            <div className="tile-note">nothing to do</div>
          </div>
        </div>
        {notInterpreted === 0 && staleCount === 0 && (
          <p className="empty">
            Nothing is behind. This screen is meant to be boring — it is only interesting after a
            crawl that ran with interpretation off, or a model change.
          </p>
        )}
      </section>

      <section className="panel">
        <h2>What to do</h2>
        <div className="scopes">
          {STAGES.map((s) => (
            <button
              key={s.key}
              className={`scope${stage === s.key ? " active" : ""}`}
              onClick={() => setStage(s.key)}
            >
              <span className="scope-label">{s.label}</span>
              <span className="scope-blast">{s.blurb}</span>
            </button>
          ))}
        </div>
        <p className="empty">
          Costs <strong>{STAGES.find((s) => s.key === stage)?.cost}</strong>, metered against this
          project&rsquo;s budget. Work already done is redone: an artifact is rewritten for its
          generator rather than appended to, so running twice costs twice and changes nothing.
        </p>
      </section>

      <section className="panel">
        <h2>Which records</h2>
        <div className="row">
          <label>
            Data type
            <input type="text" placeholder="email · issue · note"
                   value={dataType} onChange={(e) => setDataType(e.target.value)} />
          </label>
          <label>
            Tags
            <input type="text" placeholder="source:acme, crawler:crw_…"
                   value={tags} onChange={(e) => setTags(e.target.value)} />
          </label>
          <label>
            Run
            <input type="text" placeholder="run_… — everything one crawl emitted"
                   value={runId} onChange={(e) => setRunId(e.target.value)} />
          </label>
          <label className="check">
            <input type="checkbox" checked={staleOnly}
                   onChange={(e) => setStaleOnly(e.target.checked)} />
            Only what is already marked stale
          </label>
        </div>
        <p className="empty">
          Selectors compose and every one of them narrows. <strong>Tags match on overlap</strong> —
          any of these, not all of them. <code>stale_only</code> matches on an existing artifact, so
          it can never reach a record that was never interpreted: that corpus is reached by run or
          by tag, which is why both are here.
        </p>
        <div className="row end">
          <button
            className="secondary"
            disabled={busy || !narrows}
            title={narrows
              ? "Runs the selection and reports what it would touch. Writes nothing."
              : "Narrow by type, tag, run or staleness first — a selector that matches everything is one empty field away from reprocessing the project."}
            onClick={() => void run(true)}
          >
            {busy ? "Working…" : "Preview"}
          </button>
          <button
            disabled={busy || approved !== shape || (preview?.items ?? 0) === 0}
            title={
              approved !== shape
                ? "Preview this exact selection first. The cost is a model call per item and a count alone cannot tell a crawl from a corpus."
                : `Requests ${stage} for ${preview?.items ?? 0} records.`
            }
            onClick={() => void run(false)}
          >
            Run it
          </button>
        </div>
      </section>

      {preview && (
        <section className="panel">
          <h2>It would touch {preview.items} record{preview.items === 1 ? "" : "s"}</h2>
          {preview.items === 0 ? (
            <p className="empty">
              Nothing matched this selector. That is different from nothing needing work — check the
              tag or run id, since both are exact.
            </p>
          ) : (
            <>
              <div className="row">
                {Object.entries(preview.by_state).map(([state, count]) => (
                  <span className={`chip ${state}`} key={state}>{count} {state}</span>
                ))}
              </div>
              {stage === "interpret" && (preview.by_state.enriched ?? 0) > 0 && (
                <p className="warned">
                  {preview.by_state.enriched} of these are already enriched. They will be
                  interpreted again, at full cost, for the same result — narrow by run or tag if you
                  meant only the records that were never interpreted.
                </p>
              )}
              {preview.capped && (
                <p className="warned">
                  <strong>We stopped looking at 10,000.</strong> There may be more that match; this
                  is not &ldquo;nothing else matched&rdquo;. Run it, then preview again.
                </p>
              )}
              <h3>A few of them</h3>
              <div className="excluded">
                {preview.samples.map((s) => (
                  <div className="item" key={s.data_id}>
                    <span className={`chip ${s.state}`}>{s.state}</span>
                    <code>{s.external_id ?? s.data_id}</code>
                    <span className="chip">{s.data_type ?? "untyped"}</span>
                    <span className="empty">{s.preview ?? "no text"}</span>
                  </div>
                ))}
              </div>
              <p className="empty">
                Eight at most, so the selector can be recognised rather than trusted. Previewing
                writes nothing and is recorded as its own dry run.
              </p>
            </>
          )}
        </section>
      )}

      {ran && (
        <section className="panel">
          <h2>Requested for {ran.items} record{ran.items === 1 ? "" : "s"}</h2>
          <p className="empty">
            Run <code>{ran.run_id}</code>. The work happens off the write path, so this screen does
            not wait for it — the counts above move as it lands, and the request for each record is
            in its event log whether or not the message survived.
          </p>
        </section>
      )}

      {stale !== null && stale.length > 0 && (
        <section className="panel">
          <h2>Built by a generator that is no longer current</h2>
          <div className="excluded">
            {stale.slice(0, 12).map((a) => (
              <div className="item" key={a.artifact_id}>
                <span className="chip">{a.purpose}</span>
                <code>{a.data_id}</code>
                <span className="empty">produced by {a.model_id}</span>
                <span className="why far">{a.generator_version.slice(0, 12)}…</span>
              </div>
            ))}
          </div>
          <p className="empty">
            Staleness is a join rather than a flag somebody remembered to set: an artifact whose
            fingerprint is not the one currently assigned for its purpose is stale by construction.
            That is what catches a prompt, schema, parser or chunker change nobody thought to
            version.
            {stale.length > 12 && ` ${stale.length - 12} more not shown.`}
          </p>
        </section>
      )}
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

type Invite = {
  invite_id: string; prefix: string; role: string; email: string | null;
  created_at: string; expires_at: string; status: string;
};

function ProjectsSection() {
  const [projects, setProjects] = useState<Record<string, unknown>[]>([]);
  const [members, setMembers] = useState<Record<string, unknown>[]>([]);
  const [invites, setInvites] = useState<Invite[] | null>(null);
  const [groups, setGroups] = useState<Group[] | null>(null);
  const [groupName, setGroupName] = useState("");
  const [openGroup, setOpenGroup] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const [days, setDays] = useState("7");
  const [transferable, setTransferable] = useState(false);
  const [token, setToken] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [p, m] = await Promise.all([
        call<{ projects: Record<string, unknown>[] }>("api/v1/projects"),
        call<{ members: Record<string, unknown>[] }>("api/v1/organizations/members"),
      ]);
      setProjects(p.projects);
      setMembers(m.members);
      // Admin-only, and a member opening this screen should see the rest of it
      // rather than one failed request taking the page down.
      setInvites((await call<{ invites: Invite[] }>("api/v1/invites")
        .catch(() => ({ invites: [] as Invite[] }))).invites);
      setGroups((await call<{ groups: Group[] }>("api/v1/groups")
        .catch(() => ({ groups: [] as Group[] }))).groups);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  return (
    <>
      <h1>Projects &amp; members</h1>
      <p className="lede">
        A project is the isolation boundary every query is scoped to. Org roles are separate from
        platform grants — a platform admin holds nothing inside an org they are not a member of.
      </p>
      {error && <p className="err">{error}</p>}
      {note && <p className="empty">{note}</p>}
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
        <h2>Groups</h2>
        <p className="empty" style={{ marginTop: 0 }}>
          A group is a <strong>principal</strong>, exactly like a person: the ACL predicate resolves
          it inside the query, so <code>restricted</code> to a group is enforced where the rows are
          selected rather than filtered afterwards. That has always worked — and there was no way to
          create one, which made the whole of group-based sharing unreachable.
        </p>
        <div className="row">
          <input type="text" value={groupName} placeholder="name — oncall, clinicians, legal"
                 onChange={(e) => setGroupName(e.target.value)} />
          <button
            disabled={busy || !groupName.trim()}
            onClick={async () => {
              setBusy(true); setError(null); setNote(null);
              try {
                await call("api/v1/groups", { name: groupName.trim() });
                setGroupName("");
                setNote("Group created. It grants nothing until somebody shares with it.");
                await load();
              } catch (e) {
                setError((e as Error).message);
              } finally { setBusy(false); }
            }}
          >
            Create a group
          </button>
        </div>
        {groups === null ? (
          <p className="empty">Loading…</p>
        ) : groups.length === 0 ? (
          <p className="empty" style={{ marginBottom: 0 }}>
            None yet. A group is worth creating when the same set of people needs the same records
            more than once — otherwise sharing with them individually says the same thing.
          </p>
        ) : (
          <div className="excluded" style={{ marginTop: 12 }}>
            {groups.map((g) => (
              <Fragment key={g.group_id}>
                <button
                  className={`item memrow${openGroup === g.group_id ? " chosen" : ""}`}
                  onClick={() => setOpenGroup(openGroup === g.group_id ? null : g.group_id)}
                >
                  <span className="chip on">{g.name}</span>
                  <code>{g.group_id}</code>
                  <span className="empty">
                    {g.members.length} member{g.members.length === 1 ? "" : "s"}
                  </span>
                  {g.managed_by && <span className="chip">from {g.managed_by}</span>}
                  <span className="empty far">{openGroup === g.group_id ? "close" : "members"}</span>
                </button>
                {openGroup === g.group_id && (
                  <div className="setedit">
                    {members.map((m) => {
                      const uid = String(m.user_id);
                      const inside = g.members.includes(uid);
                      return (
                        <label className="check" key={uid}>
                          <input
                            type="checkbox"
                            checked={inside}
                            disabled={busy}
                            onChange={async (e) => {
                              // The endpoint replaces the whole set, so the
                              // whole set is sent. Posting only the box that
                              // moved would empty the group -- the same trap as
                              // a producer's defaults.
                              const next = e.target.checked
                                ? [...g.members, uid]
                                : g.members.filter((x) => x !== uid);
                              setBusy(true); setError(null);
                              try {
                                await call(`api/v1/groups/${g.group_id}/members`,
                                           { user_ids: next }, "PUT");
                                await load();
                              } catch (err) {
                                setError((err as Error).message);
                              } finally { setBusy(false); }
                            }}
                          />
                          {String(m.email ?? uid)}
                        </label>
                      );
                    })}
                    <p className="empty" style={{ marginBottom: 0 }}>
                      Only members of this organization can be added — a group is a sharing
                      principal, so an outsider in one would be a grant to an outsider. Membership
                      is replaced wholesale on every change, which is why the list is checkboxes
                      rather than an add button.
                    </p>
                  </div>
                )}
              </Fragment>
            ))}
          </div>
        )}
      </section>

      <section className="panel">
        <h2>Invites</h2>
        <p className="empty" style={{ marginTop: 0 }}>
          Registration is <code>invite_only</code> by default, so this is the whole path by which a
          second person joins — and it had no surface at all. An invite is <strong>single-use,
          expiring and bound to the address it was issued for</strong> unless you say otherwise, and
          both issuing and redeeming are audited.
        </p>
        <div className="row">
          <label>
            Email
            <input type="text" value={email} placeholder="who it is for"
                   onChange={(e) => setEmail(e.target.value)} />
          </label>
          <label>
            Role
            <select value={role} onChange={(e) => setRole(e.target.value)}>
              <option value="member">member</option>
              <option value="admin">admin</option>
            </select>
          </label>
          <label>
            Expires in
            <input type="number" min="1" value={days} style={{ width: 90 }}
                   onChange={(e) => setDays(e.target.value)} />
          </label>
          <label className="check"
                 title="An invite not bound to one address can be forwarded and redeemed by anyone holding it.">
            <input type="checkbox" checked={transferable}
                   onChange={(e) => setTransferable(e.target.checked)} />
            Transferable
          </label>
          <button
            disabled={busy || (!transferable && !email.trim())}
            title={!transferable && !email.trim()
              ? "An invite is bound to an address unless it is transferable."
              : "Issues an invite. The token is shown once."}
            onClick={async () => {
              setBusy(true); setError(null); setNote(null);
              try {
                const r = await call<{ token: string; prefix: string }>("api/v1/invites", {
                  email: email.trim() || null, role,
                  expires_in_days: Number(days) || 7, transferable,
                });
                setToken(r.token);
                setEmail("");
                await load();
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? "Issuing…" : "Invite"}
          </button>
        </div>
        {token && (
          <div className="notice" style={{ marginTop: 12 }}>
            <strong>Shown once.</strong> <code>{token}</code>
            <span className="empty"> Send it to them yourself — nothing here emails it, and it
            cannot be retrieved again, only revoked and reissued.</span>
          </div>
        )}
        {invites === null ? (
          <p className="empty">Loading…</p>
        ) : invites.length === 0 ? (
          <p className="empty" style={{ marginBottom: 0 }}>
            None outstanding. If you are not an admin, you would not see them either — this list is
            empty in both cases and that is worth knowing before you conclude nothing was sent.
          </p>
        ) : (
          <div className="excluded" style={{ marginTop: 12 }}>
            {invites.map((i) => (
              <div className="item" key={i.invite_id}>
                <code>{i.prefix}</code>
                <span className={`chip ${i.status === "pending" ? "on" : ""}`}>{i.status}</span>
                <span className="empty">{i.email ?? "transferable"}</span>
                <span className="chip">{i.role}</span>
                <span className="empty far">
                  expires {new Date(i.expires_at).toLocaleDateString()}
                </span>
                {i.status === "pending" && (
                  <button
                    className="linkish"
                    title="Kills it before it is redeemed. Anyone holding the token gets nothing."
                    onClick={async () => {
                      setError(null);
                      try {
                        await call(`api/v1/invites/${i.invite_id}`, undefined, "DELETE");
                        setNote(`${i.prefix} revoked.`);
                        await load();
                      } catch (e) {
                        setError((e as Error).message);
                      }
                    }}
                  >
                    Revoke
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
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

/**
 * API keys — issued, and revocable.
 *
 * The screen could create a key and not revoke one, which is the half that
 * matters after a laptop goes missing: `DELETE /users/me/api-keys/{id}` existed
 * from the first release and had no caller anywhere. It could also only issue
 * `data:read`, so every other capability the endpoint accepts was reachable by
 * curl and nowhere else.
 */
function KeysSection() {
  const [keys, setKeys] = useState<Record<string, unknown>[]>([]);
  const [issued, setIssued] = useState<string | null>(null);
  const [name, setName] = useState("console");
  const [caps, setCaps] = useState<string[]>(["data:read"]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  // What a key can be granted. A key can never hold more than the credential
  // that issued it, so the API refuses the rest -- this list is what to offer,
  // not what will be allowed.
  const CAPABILITIES = [
    { key: "data:read", blurb: "retrieve, read an item, read the trace" },
    { key: "data:write", blurb: "write, update, delete" },
    { key: "config:write", blurb: "producers, crawlers, alerts, settings" },
    { key: "admin:*", blurb: "everything, including other people's" },
  ];

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
      {error && <p className="err">{error}</p>}
      {note && <p className="empty">{note}</p>}

      <section className="panel">
        <h2>Issue one</h2>
        <div className="row">
          <label>
            Name
            <input type="text" value={name} onChange={(e) => setName(e.target.value)}
                   placeholder="what will use it" />
          </label>
          {CAPABILITIES.map((c) => (
            <label className="check" key={c.key} title={c.blurb}>
              <input
                type="checkbox"
                checked={caps.includes(c.key)}
                onChange={(e) => setCaps(e.target.checked
                  ? [...caps, c.key]
                  : caps.filter((x) => x !== c.key))}
              />
              <code>{c.key}</code>
            </label>
          ))}
          <button
            disabled={busy || caps.length === 0 || !name.trim()}
            title={caps.length === 0
              ? "A key with no capabilities can do nothing at all."
              : "Issues a key. The token is shown once and cannot be retrieved again."}
            onClick={async () => {
              setBusy(true); setError(null); setNote(null);
              try {
                const r = await call<{ token: string }>("api/v1/users/me/api-keys", {
                  name: name.trim(), capabilities: caps,
                });
                setIssued(r.token);
                load();
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? "Issuing…" : "Issue a key"}
          </button>
        </div>
        <p className="empty" style={{ marginBottom: 0 }}>
          {CAPABILITIES.map((c) => `${c.key} — ${c.blurb}`).join(" · ")}. A capability this
          credential does not itself hold is refused rather than granted quietly.
        </p>
        {issued && (
          <div className="notice" style={{ marginTop: 12 }}>
            <strong>Shown once.</strong> <code>{issued}</code>
            <span className="empty"> There is no way to ask for it again — only to revoke it and
            issue another.</span>
          </div>
        )}
      </section>

      <section className="panel">
        <h2>Live keys</h2>
        {keys.length === 0 ? (
          <p className="empty">None yet.</p>
        ) : (
          <div className="excluded">
            {keys.map((k) => {
              const revoked = Boolean(k.revoked_at);
              return (
                <div className="item" key={String(k.key_id)}>
                  <code>{String(k.prefix)}</code>
                  {k.name ? <span className="empty">{String(k.name)}</span> : null}
                  <span className="chip">{(k.capabilities as string[]).join(" ")}</span>
                  <span className="empty">
                    {revoked ? "revoked" : k.last_used_at
                      ? `used ${new Date(String(k.last_used_at)).toLocaleString()}`
                      : "never used"}
                  </span>
                  {!revoked && (
                    <button
                      className="linkish far"
                      title="Stops this key working immediately. Anything it wrote stays, attributed to it."
                      onClick={async () => {
                        setError(null); setNote(null);
                        try {
                          await call(`api/v1/users/me/api-keys/${k.key_id}`,
                                     undefined, "DELETE");
                          setNote(`${k.prefix} revoked. It stops working immediately; what it `
                                  + `wrote stays, attributed to it.`);
                          load();
                        } catch (e) {
                          setError((e as Error).message);
                        }
                      }}
                    >
                      Revoke
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        )}
        <p className="empty">
          A revoked key is listed rather than removed: it is the answer to <em>what was this key
          allowed to do while it worked</em>, which is the question asked after it leaks.
        </p>
      </section>
    </>
  );
}

type SourceLag = {
  crawler_id: string;
  name: string | null;
  scope: string;
  last_ok_at: string | null;
  last_error: string | null;
  behind_seconds: number | null;
  cooling_until: string | null;
  cooling_reason: string | null;
};

/**
 * Producers — freshness, and the two things that were only readable.
 *
 * `seconds_since_last_item` is one number per producer, and a crawler over
 * forty Slack channels is forty sources behind one number: `source-lag` breaks
 * it out per scope and separates *cooling* from *broken*, which is the whole
 * point — a rate-limited source is not a failed one and does not need a person.
 * It shipped with no reader.
 *
 * And a producer's status was displayed and could not be changed, so the way to
 * stop a misbehaving webhook was a curl.
 */
function ProducersSection({ projectId }: { projectId: string }) {
  const [rows, setRows] = useState<Record<string, unknown>[]>([]);
  const [lag, setLag] = useState<SourceLag[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRows((await call<{ producers: Record<string, unknown>[] }>(
        "api/v1/producers")).producers);
      setLag((await call<{ sources: SourceLag[] }>(
        `api/v1/projects/${projectId}/source-lag`)).sources);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [projectId]);
  useEffect(() => { void load(); }, [load]);

  async function setStatus(producerId: string, status: string) {
    setBusy(true); setError(null); setNote(null);
    try {
      await call(`api/v1/producers/${producerId}`, { status }, "PATCH");
      setNote(status === "enabled"
        ? "Enabled. It writes again from the next delivery."
        : "Disabled. Deliveries are still accepted and are dropped — a provider that "
          + "gets a 4xx retries forever or gives up silently, and neither is what you meant.");
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1>Producers</h1>
      <p className="lede">
        Nothing writes anonymously. <code>seconds_since_last_item</code> is the highest-value
        detector in the system: it catches a stopped webhook, a crawler whose selector broke, and a
        client that quietly died — with one query.
      </p>
      {error && <p className="err">{error}</p>}
      {note && <p className="empty">{note}</p>}

      <section className="panel">
        <h2>Who writes here</h2>
        <div className="excluded">
          {rows.map((p) => {
            const enabled = p.status === "enabled";
            return (
              <div className="item" key={String(p.producer_id)}>
                <code>{String(p.producer_id)}</code>
                <span className="chip on">{String(p.type)}</span>
                <span className={`chip ${enabled ? "on" : ""}`}>{String(p.status)}</span>
                {p.connection_scope ? <span className="chip">{String(p.connection_scope)}</span> : null}
                <span className="empty">
                  {p.seconds_since_last_item === null
                    ? "never written"
                    : `last item ${Math.round(Number(p.seconds_since_last_item))}s ago`}
                </span>
                <button
                  className="linkish far"
                  disabled={busy}
                  title={enabled
                    ? "Stops it writing. A webhook keeps accepting deliveries and drops them, because a 4xx makes a provider retry forever or give up silently."
                    : "Lets it write again."}
                  onClick={() => void setStatus(String(p.producer_id),
                                                enabled ? "disabled" : "enabled")}
                >
                  {enabled ? "Disable" : "Enable"}
                </button>
              </div>
            );
          })}
        </div>
      </section>

      <section className="panel">
        <h2>How far behind each source is</h2>
        <p className="empty" style={{ marginTop: 0 }}>
          Per <strong>scope</strong>, not per crawler: one crawler over forty channels is forty
          sources, and a single busy one keeps the producer&rsquo;s freshness looking healthy while
          thirty quiet ones go unread. <strong>Cooling is not broken</strong> — a rate-limited
          credential is waiting exactly as long as it was told to, and does not need a person.
        </p>
        {lag === null ? (
          <p className="empty">Loading…</p>
        ) : lag.length === 0 ? (
          <p className="empty">
            No crawler has recorded a position in this project yet. That is different from being
            behind: nothing has run.
          </p>
        ) : (
          <div className="excluded">
            {lag.map((s) => {
              const cooling = s.cooling_until !== null
                && new Date(s.cooling_until).getTime() > Date.now();
              const stale = staleness(s.behind_seconds);
              return (
                <div className="item" key={`${s.crawler_id}-${s.scope}`}>
                  <code>{s.name || s.crawler_id}</code>
                  <span className="chip">{s.scope || "whole source"}</span>
                  {cooling ? (
                    <span className="chip warnchip">
                      cooling until {new Date(s.cooling_until as string).toLocaleTimeString()}
                    </span>
                  ) : s.last_error ? (
                    <span className="why">{s.last_error}</span>
                  ) : null}
                  <span className={stale?.warn ? "why far" : "empty far"}>
                    {s.last_ok_at === null ? "never succeeded" : stale?.label}
                  </span>
                </div>
              );
            })}
          </div>
        )}
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
  /** What this endpoint's writes ask for. `enrich` is off unless it says so. */
  defaults: { enrich?: boolean; embed?: boolean; summarize?: boolean } | null;
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
  const [theirSecret, setTheirSecret] = useState("");
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

  /**
   * Change one flag without clearing the others.
   *
   * `defaults` is a jsonb column the API replaces wholesale, so sending the one
   * box that moved would silently unset the rest -- which is how a screen
   * teaches people not to trust it.
   */
  async function saveDefaults(change: Record<string, boolean>) {
    if (!selected) return;
    const defaults = { enrich: false, embed: true, summarize: true,
                       ...(selected.defaults ?? {}), ...change };
    await act("Saved. It applies to the next delivery.", async () => {
      await call(
        `api/v1/producers/${selected.producer_id}/inbound`,
        { inbound_auth: selected.inbound_auth, defaults },
        "PATCH",
      );
      setSelected({ ...selected, defaults });
      await load();
    });
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
                {/* Two endpoints receiving identically differ entirely in
                  * whether anything can find what they received, and the
                  * listing said nothing about it. */}
                <span className={h.defaults?.enrich ? "chip on far" : "chip far"}>
                  {h.defaults?.enrich ? "interpreted" : "stored only"}
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
                  defaults: null,
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
                title="Mints one for a provider that will accept ours. Zoom, Stripe, GitHub and Slack generate their own — paste theirs instead."
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
              {/* The other direction, and the one the presets need. Zoom,
                * Stripe, GitHub and Slack each generate their own secret, so a
                * producer holding one we minted is configured, looks correct,
                * and rejects every real delivery. */}
              <input
                type="password"
                value={theirSecret}
                placeholder="or paste the provider's own secret"
                style={{ flex: 1, minWidth: 220 }}
                onChange={(e) => setTheirSecret(e.target.value)}
              />
              <button
                className="secondary"
                disabled={busy || theirSecret.trim().length < 8}
                title="Stores the secret the provider generated. It is never shown again and never returned."
                onClick={() =>
                  act("Stored. It is not readable back — only replaced.", async () => {
                    await call(
                      `api/v1/producers/${selected.producer_id}/signing-secret`,
                      { secret: theirSecret.trim() },
                    );
                    setTheirSecret("");
                    setSelected({ ...selected, inbound_auth: "signature" });
                    await load();
                  })
                }
              >
                Store theirs
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
            <h2>What happens to what arrives</h2>
            <p className="empty" style={{ marginTop: 0 }}>
              Deliveries are <strong>stored and not interpreted</strong> unless this says otherwise,
              so by default a provider can post all day and <strong>nothing it sent is findable by
              search</strong>. Off is right for a chatty feed nobody queries and wrong for
              everything else, and it is per endpoint because that is where the volume differs.
            </p>
            <div className="row">
              <label className="check">
                <input
                  type="checkbox"
                  checked={selected.defaults?.enrich ?? false}
                  disabled={busy}
                  onChange={(e) => void saveDefaults({ enrich: e.target.checked })}
                />
                Interpret deliveries
              </label>
              <label className="check">
                <input
                  type="checkbox"
                  checked={selected.defaults?.embed ?? true}
                  disabled={busy || !(selected.defaults?.enrich ?? false)}
                  onChange={(e) => void saveDefaults({ embed: e.target.checked })}
                />
                Embed — one call per chunk, makes it searchable
              </label>
              <label className="check">
                <input
                  type="checkbox"
                  checked={selected.defaults?.summarize ?? true}
                  disabled={busy || !(selected.defaults?.enrich ?? false)}
                  onChange={(e) => void saveDefaults({ summarize: e.target.checked })}
                />
                Summarise — one call per item
              </label>
            </div>
            <p className="empty" style={{ marginBottom: 0 }}>
              The two halves are separately priced, which is why they are separately settable: a
              high-volume feed usually wants embedding and not a summary of every message. A change
              here applies to the <strong>next</strong> delivery; what already arrived stays as it
              landed — interpret those from Browse.
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
            <p className="empty" style={{ marginTop: 0 }}>
              <strong>For meetings, add <code>attendees_path</code></strong> — and{" "}
              <code>attendee_email_key</code> when the addresses are not under{" "}
              <code>email</code>. The transcript is then written{" "}
              <strong>restricted to the people in the room</strong> rather than inheriting this
              connection&rsquo;s visibility: four people in a room did not publish to the company.
              An attendee outside the organisation resolves to nothing, which is the conservative
              direction, and a meeting where nobody resolves is written private rather than
              falling back to the default.
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
