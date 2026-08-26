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

| Entry point | Path | Enriched? |
|-------------|------|-----------|
| Messaging channel | Gateway → API → queue → pipeline | Yes |
| Third-party webhook | Gateway → API → queue → pipeline | Yes |
| **Crawler** | Discovery → fetch/API → pipeline | Yes |
| Direct REST, SDK, MCP | API directly | Optional |
| Upload | Presigned → storage → API | Yes |
| Conversational agent | API directly, per-user credentials | **Bypasses pipeline** |
