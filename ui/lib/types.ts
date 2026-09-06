export type Stair = {
  total: number;
  stored: number;
  searchable: number;
  enriched: number;
  awaiting_fetch: number;
};

export type Citation = {
  data_id: string;
  chunk_id: string;
  text: string;
  span_start: number;
  span_end: number;
  score: number;
  matched_by: string[];
  state: string;
};

export type Excluded = {
  data_id: string;
  reason: "threshold" | "not_yet_enriched";
  score: number | null;
  state: string | null;
};

export type GraphSeed = {
  entity_id: string;
  display_name: string;
  type: string;
  matched_on: "name" | "identifier";
};

export type Trace = {
  query_id: string;
  results: Citation[];
  model_id: string;
  generator_version: string | null;
  corpus: Stair | null;
  excluded: Excluded[];
  // The entities a query resolved to, when the graph arm was asked for. A
  // graph-only result contains none of the words searched for, so this is the
  // only thing that explains why it is in the list.
  graph_seeds: GraphSeed[];
};

// The retrieval arms, and the one place their names and labels live. The API
// models `match` as a list, so these are a multi-select and not a mode: the
// value of the graph arm is that its hits are fused with the others, and an
// exclusive control would throw that away.
export const ARMS = [
  { key: "vector", chip: "vec", label: "vector", hint: "meaning" },
  { key: "lexical", chip: "lex", label: "lexical", hint: "words" },
  { key: "graph", chip: "gph", label: "graph", hint: "connections" },
] as const;

export type ArmKey = (typeof ARMS)[number]["key"];

export type Version = {
  version_id: string;
  revision: number;
  source: string;
  content_chars: number;
  mime_type: string | null;
  model_id: string | null;
  tokens: number | null;
  preview: string | null;
  created_at: string;
};

/** One revision read whole. The listing only previews; this is the ask. */
export type FullVersion = Version & { content_text: string | null };

export type Item = {
  data_id: string;
  state: string;
  external_id?: string;
  mime_type: string | null;
  data_type: string | null;
  storage_ref: string | null;
  checksum: string | null;
  size_bytes: number | null;
  parse_status: string | null;
  parse_detail: Record<string, unknown> | null;
  content_text: string | null;
  extracted_text: string | null;
  access_level?: string;
  // What this record put into the graph. `null` on responses that predate the
  // field; zero is a real answer and must not render as "unknown".
  entity_count?: number | null;
  edge_count?: number | null;
  template?: string | null;
};

/** What extraction made of a fetched page.
 *
 * Present on the artifact's `fields.quality` for HTML records only — a page is
 * asked things a PDF is not. Every field is optional because an older artifact
 * predates the block entirely, and an absent reading is "not judged", never
 * "judged and found wanting".
 */
export type PageQuality = {
  page_kind?: string;
  purpose?: string;
  substance?: string;
  evidence?: string;
  authorship?: string;
  dated?: string;
  commercial?: string;
  reliability?: string[];
  missing?: string[];
  retrieval_value?: string;
  verdict?: string;
};

export type Memory = {
  memory_id: string;
  type: string;
  memory_key: string | null;
  title: string | null;
  owner_id: string | null;
  created_at: string;
  ttl_seconds: number | null;
  on_expiry: string | null;
  members: number;
  /** Set when a memory this one is `derived_from` changed. Cleared by a
   *  recompute — never by a preview, which would be a side effect. */
  stale_since: string | null;
  stale_reason: string | null;
};

export type MemoryMember = {
  data_id: string;
  state: string;
  mime_type: string | null;
  data_type: string | null;
  added_by: string;
  added_at: string;
  preview: string | null;
};

export type Membership = {
  memory_id: string;
  type: string;
  memory_key: string | null;
  added_by: string;
  added_at: string;
  ttl_seconds: number | null;
  on_expiry: string | null;
  expires_at: string | null;
};

export type AuditTrail = {
  writes: {
    id: string;
    action: string;
    target_type: string | null;
    target_id: string | null;
    actor_user_id: string | null;
    actor_key_id: string | null;
    detail: Record<string, unknown>;
    at: string;
  }[];
  reads: {
    id: string;
    action: string;
    data_id: string | null;
    query_id: string | null;
    user_id: string | null;
    key_id: string | null;
    at: string;
  }[];
};

// Which OpenAlex author a Scholar profile resolved to, and what the match
// rested on. `matched_on` is the load-bearing field: a name alone can resolve
// to the wrong researcher and produce a corpus that is coherent and wrong.
export type ResolvedAuthor = {
  openalex_id: string;
  name: string;
  affiliation: string | null;
  works: number;
  matched_on: string[];
};

// Every screen the console has. Here rather than in `Console.tsx` so that
// pure logic — which decides *where to send someone* — can name a destination
// without importing a ten-thousand-line component to do it.
export type Section =
  | "overview"
  | "add" | "update" | "search" | "ask" | "inbound" | "crawlers" | "repos" | "mcp"
  | "memory" | "cases" | "entities" | "compaction" | "reprocess" | "workflows"
  | "alerts" | "standing"
  | "audit" | "sharing" | "deletion"
  | "settings" | "models" | "prompts"
  | "projects" | "keys" | "producers" | "platform";

export type MemoryType = {
  type_id: string;
  name: string;
  ttl_seconds: number | null;
  on_expiry: string;
  locked: boolean;
  // How a URL in a memory of this type is read: "fetch" downloads it, "context"
  // asks the model to read it. The second exists because a JavaScript-rendered
  // page answers 200 with an empty shell, so a GET succeeds and stores nothing.
  url_reader: "fetch" | "context";
  // Whether records landing in a memory of this type are enriched without being
  // asked. Separate from url_reader on purpose: a memory of papers wants
  // enrichment and wants its PDFs downloaded, not read.
  enrich: boolean;
  // Whether every record added to a memory of this type becomes a checkpoint
  // on a change-tracked timeline. Off unless turned on: it puts a model call
  // behind every write into every memory of the type.
  checkpoints: boolean;
};

// One point on a checkpoint timeline. `outcome` is separate from `status` for
// the reason a change detector cannot afford to blur: "the check has not
// finished" and "the check found nothing" are the two answers that must never
// render the same.
export type Checkpoint = {
  checkpoint_id: string;
  seq: number;
  data_id: string;
  external_id: string;
  status: "pending" | "running" | "complete" | "failed";
  outcome: "first" | "changed" | "unchanged" | "incomparable" | null;
  reason: string | null;
  created_at: string;
  change_artifact_id: string | null;
  change_summary: string | null;
  change_fields: { changes?: Change[] } | null;
};

export type Change = {
  kind: "added" | "removed" | "changed";
  statement: string;
  // Named for the EARLIER/LATER labels the prompt uses, and empty rather than
  // null when the value is absent on that side — see CHANGE_PROPERTIES.
  earlier_value: string;
  later_value: string;
  significance: "high" | "medium" | "low";
};

export function describeTtl(seconds: number | null): string {
  if (seconds === null) return "never expires";
  if (seconds >= 86400) return `${Math.round(seconds / 86400)} day TTL`;
  if (seconds >= 3600) return `${Math.round(seconds / 3600)} hour TTL`;
  return `${seconds}s TTL`;
}

export async function call<T>(
  path: string,
  body?: unknown,
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE",
): Promise<T> {
  const response = await fetch(`/api/proxy/${path}`, {
    method: method ?? (body ? "POST" : "GET"),
    headers: body ? { "content-type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload?.detail ?? `request failed (${response.status})`);
  return payload as T;
}


/** An alert: what to watch, and whether it has been approved to run. */
export type Alert = {
  alert_id: string;
  name: string;
  mode: "rule" | "llm";
  surface: string;
  where: Record<string, string[]>;
  describe: string | null;
  enabled: boolean;
  /** Bumped by any edit that changes what matches. */
  config_version: number;
  /** Equal to config_version only when *this* wording has been backtested. */
  backtested_version: number | null;
  watermark: number;
  batch_cap: number;
  matches_24h?: number;
  last_run_at?: string | null;
  debounce_seconds: number;
  overlap: string;
  /** Bounds which subjects count at all — a join, not a payload field. */
  scope: Record<string, string>;
  /** Transitions this alert has not looked at yet. Climbing means the sweep stopped. */
  behind?: number;
};

/** What a backtest would have caught — the only way to judge a selector. */
export type Backtest = {
  candidates: number;
  matches: number;
  deferred: number;
  sampled: number;
  samples: { sequence: number; occurred_at: string; payload: Record<string, unknown> }[];
};

export type ObservedEvent = {
  event_id: string;
  sequence: number;
  alert_id: string;
  alert_name: string;
  config_version: number;
  surface: string;
  payload: Record<string, unknown>;
  matched_by: "selector" | "model";
  occurred_at: string;
  deliveries: number;
  delivered: number;
  undeliverable: number;
};

/**
 * A transition payload as a sentence.
 *
 * The feed used to print `JSON.stringify(payload).slice(0, 120)`, which is a
 * dump rather than an event — you could not tell at a glance what had happened,
 * which is the only thing the screen is for.
 */
export function describeEvent(surface: string, p: Record<string, unknown>): string {
  const s = (k: string) => (p[k] == null ? "?" : String(p[k]));
  switch (surface) {
    // Names, now that transitions carry them. "Priya Raman located_in Lisbon"
    // is an event; "person · located_in → location" is a schema.
    case "fact.asserted":
      return `${s("subject_name")} ${s("predicate")} ${s("object_name")}`;
    case "fact.superseded":
      return p.replaced_by_name
        ? `${s("subject_name")} ${s("predicate")} ${s("object_name")} → now ${s("replaced_by_name")}`
        : `${s("subject_name")} ${s("predicate")} ${s("object_name")} — no longer true`;
    case "fact.retracted":
      return `${s("subject_name")} ${s("predicate")} ${s("object_name")} withdrawn${
        p.reason ? ` — ${s("reason")}` : ""}`;
    case "data.revised":
      return `revision ${s("from_revision")} → ${s("revision")} via ${s("source")}`;
    case "acl.changed":
      return `${s("from_level")} → ${s("to_level")}`;
    case "memory.member_added":
      return `added to a ${s("memory_type")} memory (${s("added_by")})`;
    case "memory.retyped":
      return `${s("from_type")} → ${s("to_type")}`;
    case "case.member_promoted":
      return `confirmed on a ${s("case_type")} case`;
    default:
      return Object.entries(p).map(([k, v]) => `${k}=${v}`).join(" · ");
  }
}

export type AlertRun = {
  run_id: string;
  trigger: string;
  status: string;
  candidates: number;
  matches: number;
  /** Non-zero means a batch hit its cap. Never left implicit. */
  deferred: number;
  /** One per run in llm mode, zero in rule mode. Cost beside the control. */
  model_calls: number;
  started_at: string;
};

export type Subscription = {
  subscription_id: string;
  url: string;
  alert_id: string | null;
  /** Set when this endpoint receives a standing query's matches instead of
   *  alert events. `kind` decides which family it belongs to — a null
   *  `alert_id` has always meant *every alert*, and must not quietly start
   *  meaning every standing query too. */
  standing_query_id: string | null;
  kind: string;
  enabled: boolean;
  dead: number;
  pending: number;
  delivered: number;
  signing_secret_rotated_at: string | null;
};

/** An alert is approved only while its backtest matches its current wording. */
export function isApproved(alert: Alert): boolean {
  return alert.backtested_version === alert.config_version;
}


/** A scheduled compaction: one memory, one algorithm. */
export type CompactionJob = {
  job_id: string;
  name: string;
  memory_id: string;
  memory_title: string | null;
  memory_key: string | null;
  memory_type: string;
  members: number;
  algorithm: string;
  options: Record<string, unknown>;
  schedule: { type: string; every_seconds?: number };
  enabled: boolean;
  config_version: number;
  /** Equal to config_version only when *this* version has been previewed. */
  dry_run_version: number | null;
  last_run_at: string | null;
  archived_total: number;
};

export type CompactionRun = {
  run_id: string;
  mode: "dry" | "live";
  trigger: string;
  status: string;
  considered: number;
  archived: number;
  artifacts: number;
  bytes_before: number;
  bytes_after: number;
  model_calls: number;
  error: string | null;
  started_at: string;
};

export type Algorithm = {
  label: string;
  needs_model: boolean;
  describe: string;
  options: Record<string, string>;
};

/** Bytes as something a person reads, not a number they decode. */
export function humanChars(n: number): string {
  if (n < 1000) return `${n} chars`;
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k chars`;
  return `${(n / 1_000_000).toFixed(1)}M chars`;
}

/**
 * One entry from the domain event log, as `GET /events?data_id=…` returns it.
 *
 * This is what makes a stalled item explainable rather than merely stuck. A
 * `state` says where an item got to; only the log says whether anything was
 * ever asked to take it further, and what happened when it tried.
 */
export type DomainEvent = {
  event_id: string;
  sequence: number;
  event_type: string;
  data_id: string | null;
  caused_by: string | null;
  /** pending · dispatched · consumed · failed · no_consumer */
  status: string;
  attempts: number;
  last_error: string | null;
  payload: Record<string, unknown>;
  occurred_at: string;
  consumed_at: string | null;
};

/**
 * One row of `GET /settings/effective` — the value, where it came from, and
 * **what a valid value is**.
 *
 * `kind`, `choices` and `nullable` come from the server's register so an editor
 * builds its control from the rule that is actually enforced. A dropdown whose
 * options are typed out in the UI is a second copy of a vocabulary, and the
 * copy is the one that goes stale.
 */
export type Setting = {
  key: string;
  value: unknown;
  /** platform · org · project · user · default */
  source: string;
  locked_by: string | null;
  allowed_scopes: string[];
  lockable: boolean;
  description: string;
  kind: "bool" | "int" | "enum" | "string" | "list" | "object";
  choices: unknown[];
  nullable: boolean;
  default: unknown;
};

/**
 * `part_of`, both directions. A parent's members *are* its children's, so a
 * tree is a join rather than something kept in sync — and an alert scoped to a
 * memory resolves through exactly this walk.
 */
export type MemoryTree = {
  memory_id: string;
  relation: string;
  stale_since: string | null;
  stale_reason: string | null;
  ancestors: TreeNode[];
  descendants: TreeNode[];
  descendant_members: number;
  max_depth: number;
  truncated: boolean;
};

export type TreeNode = {
  depth: number;
  memory_id: string;
  type: string;
  memory_key: string | null;
  title: string | null;
  members: number;
  stale_since: string | null;
  stale_reason: string | null;
};

/**
 * A repository analysed at one commit.
 *
 * `stats` is deliberately loose: the job records what it found, and the shape
 * grows as it learns to find more. The console reads named keys and renders
 * nothing for the rest rather than failing on a key it has not met.
 */
export type RepoSnapshot = {
  snapshot_id: string;
  case_id?: string;
  memory_id?: string;
  repo_url?: string;
  repo?: string;
  commit_sha: string;
  ref: string | null;
  status: "pending" | "running" | "complete" | "failed";
  reason: string | null;
  stats: {
    nodes?: number;
    edges?: number;
    files_selected?: number;
    selection?: string[];
    dependencies?: number;
    osv_status?: "ok" | "unavailable" | "no_dependencies_found";
    osv_affected?: number;
    records_written?: number;
    graphify_version?: string;
    /** Set when extraction found no code to graph — a fact about the
     *  repository, not a failed run. */
    no_code_graph?: string;
    size_kb?: number;
    primary_language?: string;
    license?: string;
    stars?: number;
    subject?: string;
    committed_at?: string;
  };
  created_at: string;
  reused?: boolean;
};

/** One repository, carrying the state of its most recent snapshot. */
export type Repo = {
  case_id: string;
  repo: string;
  title: string | null;
  snapshots: number;
  last_analysed: string | null;
  last_status: RepoSnapshot["status"] | null;
  last_reason: string | null;
  last_sha: string | null;
  last_snapshot_id: string | null;
};

/** A saved report. The four repo generators produce these like any other. */
export type RepoFinding = {
  file: string;
  symbol?: string | null;
  severity: "high" | "medium" | "low";
  statement: string;
  trigger?: string | null;
};

export type RepoReport = {
  artifact_id: string;
  kind: string;
  title: string | null;
  summary: string | null;
  description: string | null;
  keywords: string[];
  model_id: string;
  generator_version: string;
  created_at: string;
  /** `findings` when the report asked for them, `fallback_*` when the model was
   *  not reached. An absent `findings` and an empty one are different answers:
   *  never reviewed versus reviewed and found nothing. */
  fields?: {
    findings?: RepoFinding[];
    fallback_depth?: number;
    fallback_reason?: string[];
  } | null;
};

/** The four reports, in the order they are read rather than alphabetically. */
export const REPO_REPORTS = [
  { key: "repo_design", label: "Design",
    hint: "layers, seams, and where the arrangement leaks" },
  { key: "repo_quality", label: "Code quality",
    hint: "duplication, oversized modules, dead code, test shape" },
  { key: "repo_bugs", label: "Functional bugs",
    hint: "located defects only — file, symbol, triggering input" },
  { key: "repo_deps", label: "Dependencies",
    hint: "advisories from OSV, plus pinning and licence problems" },
] as const;

export type RepoReportKey = (typeof REPO_REPORTS)[number]["key"];
