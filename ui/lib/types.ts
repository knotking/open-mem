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

export type MemoryType = {
  type_id: string;
  name: string;
  ttl_seconds: number | null;
  on_expiry: string;
  locked: boolean;
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
  enabled: boolean;
  dead: number;
  pending: number;
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
