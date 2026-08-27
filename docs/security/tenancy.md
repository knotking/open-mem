# Tenancy & Privacy

## Two tenancy models are in play

The host-SaaS contract states that end-user RBAC is *enforced by the host*. That is coherent when
mem-dog is a backend behind someone else's product. It is **not** what a team model needs.

| | Host-SaaS model | Team model |
|---|---|---|
| Who enforces RBAC | the host application | **mem-dog** |
| Keys held by | host backend | per user |
| `project` means | host workspace | team space |
| Privacy unit | project boundary | **per item, per member** |

**Resolution: one enforcement path.** mem-dog always enforces; the host model becomes the case
where a service identity is a single broad principal. Two implementations kept in sync is the
failure mode to avoid.

## Hierarchy

```
Organization (org_<ulid>)          — team or company
  ├── Members (user_id + role)     — owner / admin / member / viewer
  └── Project (proj_<ulid>)        — team space or host workspace
        ├── Memory                 — scoped to project
        ├── Data                   — associated with memory
        └── Embedding              — scoped to project
```

Scoping is applied by passing `project_id` on create and `?project_id=` on list endpoints.
Omitting it returns everything the user owns, keeping single-tenant deployments unchanged.

## Privacy holes that only appear once orgs are teams

**Connection ownership.** The proxy takes `?user_id=` and fetches that user's credentials. If
authorization is "authenticated to the org" rather than "owns this connection", an admin can read
a member's mail through the proxy. Connections need `personal` vs `shared` scope enforced **at the
proxy**, not hidden in the UI.

**Derived-fact leakage.** A fact extracted from a private document, surfaced to a teammate through
the graph, is a leak with no audit trail.

**Compression leakage.** A summary spanning mixed-ACL items must take the *intersection*, or it
leaks by construction — and deleting the original does not remove it from the prose.

**Cross-tenant entity merging.** A single-database graph means isolation is property-filtering
only. Two orgs both holding "Acme Corp" must not merge — and LLM entity resolution is actively
trying to merge them.

**Retrieval filtering.** ACLs must be applied *in* the query. Post-filtering after ranking
silently breaks top-K and leaks existence.

## The unifying rule

**ACL inheritance follows the connection, not the container.** A connection carries a scope
(`personal` or `shared`) set at connect time. Personal mail connected inside a team org produces
private items regardless of project defaults.

This is what reconciles personal and team memory, and it closes the proxy hole in the same move.

## Access levels

| Level | Visibility |
|-------|-----------|
| `private` | Only the owner (default) |
| `shared` | Owner + users in `shared_with` |
| `public` | Any authenticated user **in the organization** — internal, not public |
| `restricted` | Only users in `shared_with` |

**The `public` level is renamed `org`**, and `public` becomes genuine external sharing — see
[access-model.md](access-model.md), which also covers principals, groups, share links and the
admin dual-role.

## Scale posture

Build on the existing capacity plan rather than replacing it — quotas before replicas,
`project_id` always in the vector filter path, temporal graph default-off for host workspaces, a
connection pooler, per-org metrics, and a soak harness from 100 to 1,000 projects.

| Dimension | Target |
|-----------|--------|
| Active workspaces | ~1,000 with traffic in the last 30 days |
| Ingest | 50–100 docs/min sustained; bursts to 300/min for ≤5 min |
| Corpus | Median workspace ≤50k embedding rows; p95 ≤500k; cluster ≤50M |
| Search | p95 semantic/hybrid **< 800 ms** excluding generation |
| Availability | API 99.5% monthly; memory soft-fail preferred over cascade |

**"The record store is the shared fate."** Colocating vector and lexical indexes with records is
what makes the low infrastructure floor possible — and it is why every workspace competes for the
same instance. Filtered ANN search over tens of millions of rows is the load-bearing risk.
