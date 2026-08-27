# Ingestion

Two pipelines, not one. The boundary between them is a durable queue, and that split is the
whole design.

- **The ingestion path** — getting data in and durably stored. Synchronous, must not fail.
- **The enrichment path** — making that data useful. Asynchronous, allowed to lag.

| Document | Covers |
|----------|--------|
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
| Messaging channel | Gateway → API → queue → pipeline | us | Yes |
| Third-party webhook | Gateway → API → queue → pipeline | us | Yes |
| **Managed crawler** | Discovery → fetch/API → pipeline | us | Yes |
| **External crawler** | Their runtime → our API → pipeline | **anyone** | Yes |
| Direct REST, SDK, MCP | API directly | caller | Optional |
| Upload | Presigned → storage → API | user | Yes |
| Conversational agent | API directly, per-user credentials | us | **Bypasses pipeline** |

**The ingest API is the universal crawler interface.** Managed crawlers are a convenience layer
over it — they call the same endpoints an external system would and hold no special privileges.
So an ETL platform, a scheduled script or a customer's internal job is a first-class producer, not
a workaround.
