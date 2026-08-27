# API Contract & Surfaces

Everything reaches the platform through `/api/v1/` — UI, SDKs, MCP server, gateway,
conversational agent and host applications alike. That uniformity is a strength; the problem is
that **privilege is not expressed in the surface**.

## The finding

`md_*` keys bind to a **user** and inherit *all* that user's rights. If you are an org owner, the
key you paste into an MCP client can delete your organization — and the MCP tool list includes a
delete tool.

Keys must be **capability-scoped**, not only identity-scoped:

```
identity   →  who am I acting as       (exists today)
capability →  what may this key do     (missing)
```

## Two planes

| | Control plane | Data plane |
|---|---|---|
| Volume | low | high |
| Privilege | high | per-item ACL |
| Audit | mandatory | sampled |
| Latency | irrelevant | sub-second for agents |
| Surfaces | UI, CLI, REST | UI, SDK, MCP, chat agent, REST |

## Endpoint groups by plane

| Group | Prefix | Plane | Capability scope |
|-------|--------|-------|------------------|
| **Write** | `/write` | data | `data:write` — **the single write path, every producer** |
| Data items | `/data` | data | `data:read` — reads and mutations of existing items |
| Producers | `/producers` | control | `config:write` — register webhooks, crawlers, keys |
| Retrieval | `/search` · `/ai/query` | data | `data:read` |
| Memories | `/memories` | data | `data:read` · `data:write` |
| Graph | `/graph` | data | `data:read` · `data:write` |
| Uploads | `/uploads` | data | `data:write` |
| **Crawlers** | `/crawlers` | control | `config:write` |
| Webhooks | `/webhooks` | control | `config:write` |
| Integrations | `/integrations` | control | `config:write` |
| AI config | `/ai/users/{uid}/…` | control | `config:write` |
| Organizations | `/organizations` | control | `admin:*` |
| API keys | `/api-keys` | control | `admin:*` |
| Infrastructure | `/pods` | control | `admin:*` |
| MCP | `/mcp/sse` | **data only** | `data:read` · `data:write` |

Capability scope becomes a property of the **key**, checked at the router. An MCP key is
*structurally incapable* of reaching `/organizations` — not because the caller lacks a role, but
because the credential does not carry the scope.

## Conventions

| Concern | Today | Needs to be |
|---------|-------|-------------|
| Identifiers | ULID with type prefix — `data_`, `mem_`, `whk_`, `org_`, `proj_` | keep — time-sortable and self-describing |
| Pagination | `?limit=&offset=` | **cursor-based** — deep offsets scan; at 50M rows this is the wrong primitive |
| Errors | **two formats** — `{detail, status_code}` and `{error:{code,message,details,request_id}}` | **converge** on the structured envelope; keep `detail` as a deprecated mirror with a stated removal version |
| Correlation | `X-Request-Id` echoed | **extend** — propagate into the pipeline, not just the API |
| Idempotency | none on writes | **idempotency key** — providers redeliver; so do retrying clients |
| Quota responses | `429` + `Retry-After` + structured code | keep |
| Spec | OpenAPI + Swagger/ReDoc | keep — drives SDK codegen |

## Endpoints the design adds

| Endpoint | Why |
|----------|-----|
| `POST /uploads`, `POST /uploads/{id}/complete` | Presigned direct-to-storage flow |
| `CRUD /crawlers`, `POST /crawlers/{id}/runs`, `POST /crawlers/{id}/dry-run`, `PATCH /runs/{id}` | Crawler configs and run control |
| **`POST /write`** | The one write endpoint. `items[]` always, `207` always — batch is not a separate verb, just the same verb with more items |
| **`/agents/{id}/config`** — get effective + provenance, set, revert, **test**, **impact**, lock | Extraction prompts, schemas, tiers and flags per data type — defaults shipped, overridable per org and project |
| **`PUT /cases`** · `/cases/{id}/members` · `/timeline` · `/retrieve` · `/similar` | Subject correlation — patient timelines, legal matters, asset histories |
| `POST /retrieve` | Composable retrieval; the five modes become presets over it |
| `POST /reprocess` | W7 — rebuild derived artifacts by selector |
| `GET /artifacts/stale` | What needs rebuilding, and why |
| `CRUD /schemas`, `/mappings` | Normalization customization |
| `PATCH /connections/{id}` | Set `personal` / `shared` scope — the ACL-inheritance root |
| `POST /tokens/ephemeral` | Short-lived project-bound token for embeddable widgets |
| **`DELETE /data/{id}`** · **`POST /deletions`** with a selector · project and org purge | Cleanup and erasure. Beyond one item it is a job — see [deletion](operations/deletion.md) |

## Six personas, not four roles

| Persona | Scope | Gap today |
|---------|-------|-----------|
| **Platform operator** | deployment, infra, secrets | **missing** — the unscoped global key does this job, unattributably |
| **Org owner** | billing, delete org, all members | — |
| **Org admin** | members, projects, quotas, policy | — |
| **Member** | own data, connections, AI config | the "simple user" |
| **Viewer** | read-only | — |
| **Service identity** | host app, CI, agent | **missing** — every key is a person today |

### Surface × persona

| Surface | Operator | Owner / Admin | Member | Service |
|---------|----------|---------------|--------|---------|
| **Web UI** | infra only | full control plane | own settings only | — |
| **CLI** | primary | scripting | rare | CI |
| **SDK** | — | some config | data plane | primary |
| **MCP** | — | **no admin tools** | data plane | agent |
| **Chat agent** | — | — | data plane | — |
| **REST** | platform creds | scoped by role | scoped by role | scoped key |

## Config precedence, with locking

| Level | Owns | Persona |
|-------|------|---------|
| **Platform** | storage backend, encryption keys, deployment | operator |
| **Org** | quotas, allowed providers, sharing policy | admin |
| **User** | own engines, agent configs, connections | member |

Model Garden is per-user today. An org admin will need to mandate "only our approved provider", so
precedence needs a **lock** flag: org sets policy, user customizes within it, admin can pin.

## SDK layering

Three layers, not one flat client, so the capability boundary is visible at the call site:

| Layer | Contents |
|-------|----------|
| **Simple facade** | `add()` / `search()` / `chat()` — the 90% case |
| **Full client** | Typed CRUD, scoping, pagination, crawler config builders |
| **Admin client** | Org, members, quotas, keys, run control — **separate import, separate key** |

## Embeddable UI

The host contract forbids end-user browsers holding durable secrets. That rules out shipping a
widget with an embedded key — but not embeddable UI:

```
host backend ──mints──▶ short-lived scoped token ──▶ browser widget
                        (project-bound, read-only, minutes not days)
```

Without it, every host rebuilds retrieval UI from scratch.

## Stability policy

Hosts pin against this surface. Write down what may change inside `/api/v1` — additive fields, new
optional parameters, new endpoints — versus what forces `/api/v2`: removed fields, changed types,
altered defaults, narrowed enums.

## Handle with care

**Conversational admin is appealing and dangerous.** "Delete the marketing project", sent over a
messaging app, executed by an LLM that resolved identity from a phone number, is a bad failure
mode. Keep the chat agent strictly data-plane; gate destructive actions behind confirmation in an
authenticated surface.
