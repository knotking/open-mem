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

export type Trace = {
  query_id: string;
  results: Citation[];
  model_id: string;
  generator_version: string | null;
  corpus: Stair | null;
  excluded: Excluded[];
};

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

export async function call<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api/proxy/${path}`, {
    method: body ? "POST" : "GET",
    headers: body ? { "content-type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload?.detail ?? `request failed (${response.status})`);
  return payload as T;
}
