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
};

export type AlertRun = {
  run_id: string;
  trigger: string;
  status: string;
  candidates: number;
  matches: number;
  /** Non-zero means a batch hit its cap. Never left implicit. */
  deferred: number;
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
