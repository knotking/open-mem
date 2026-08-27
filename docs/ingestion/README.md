# Ingestion

Two pipelines, not one. The boundary between them is a durable queue, and that split is the
whole design.

- **The ingestion path** — getting data in and durably stored. Synchronous, must not fail.
- **The enrichment path** — making that data useful. Asynchronous, allowed to lag.

| Document | Covers |
|----------|--------|
| [write-api.md](write-api.md) | **The single write path** — one endpoint, registered producers |
| [workers.md](workers.md) | Eight worker classes, the content contract, failure policy |
| [crawlers.md](crawlers.md) | The pull half — scheduled, configurable discovery |
| [uploads.md](uploads.md) | Direct uploads via presigned storage |
| [sources.md](sources.md) | Source coverage, capability profiles, support tiers |
| [connectors.md](connectors.md) | The catalog — what is live, deployed, documented, reachable |
| [normalization.md](normalization.md) | Provider record → canonical object, and customization |
| [formats.md](formats.md) | Format support tiers and the gotchas that decide them |

## Entry points

| Entry point | Path | Who runs it | Enriched? |
|-------------|------|-------------|-----------|
Every one of these is a **registered producer** calling `POST /api/v1/write`.

| Producer | Preconfigured by | Content shape |
|----------|-----------------|---------------|
| Messaging channel / app webhook | the system, at connect time | `Inline`; `Pending` for attachments |
| **Managed crawler** | the user, dry-run gated | `Inline` for records, `Pending` for files |
| **External crawler / ETL** | a project-scoped key | same — no privileged path |
| Client SDK / MCP | key issuance | `Inline` |
| Upload | presign session | `Stored` |
| Conversational agent | per-user credentials | `Inline` |

**One write path.** Managed crawlers, the gateway and external systems all call it, and none
holds a privileged shortcut. That is what makes an ETL platform, a scheduled script or a
customer's internal job a first-class producer rather than a workaround — see
[write-api.md](write-api.md).
