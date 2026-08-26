# Crawlers

Every other ingestion path waits to be told: a webhook fires, a user uploads, an SDK calls. But
most data does not announce itself. A Salesforce org has three years of opportunities nobody will
re-save. A documentation site has four hundred pages and no webhook. A review platform has no
push mechanism at all.

## This simplifies the taxonomy rather than extending it

Backfill and polling are **two schedules of the same thing**. A backfill is a crawl with
full-history scope run once; a poll is a crawl with watermark scope run on an interval; a web
crawl is the same machinery with link traversal as its discovery strategy. One configurable
worker, several discovery strategies — the taxonomy shrinks from nine classes to eight.

## The crawler does not fetch, and does not enrich

It **discovers** and **emits**. Everything downstream already exists.

```
crawler config (stored, versioned)
      ↓
  scheduler (cron · interval · manual)
      ↓
  crawl run ──── discover → frontier
      │              ↓
      │        dedupe store (external_id · etag · hash)
      │              ↓
      └── emit ──┬──▶ W2 fetch      (files, binaries)
                 └──▶ POST /data    (records)
                              ↓
                        W1 enrich (unchanged)
```

Because crawlers write through the same contracts as every other producer, a crawled Salesforce
record and a webhook-delivered one are indistinguishable downstream — and a crawler can run
outside the cluster entirely.

## Six discovery strategies

| Strategy | How it enumerates | Example | Incremental by |
|----------|-------------------|---------|----------------|
| **enumerate** | Paginate a collection endpoint | Salesforce Opportunities, Jira issues | modified-since field |
| **query** | Run a query on a schedule | Warehouse table, SOQL, saved search | cursor column |
| **traverse** | Follow links from seeds | Website, wiki, docs site | etag / last-modified |
| **tree** | Walk a hierarchy | Drive folder, S3 prefix, SFTP dir | path + mtime |
| **feed** | Read an index or feed | RSS, sitemap, changelog | entry id / pubdate |
| **search** | Repeat a query, collect results | Social search, review platforms | result id + seen set |

The pull-only connectors in the catalog — review platforms, app stores, warehouses — are all
`search` or `query` crawlers. They stop needing bespoke code and become configuration.

## Anatomy of a config

Declarative and stored, following the pattern already used for agent configs and normalization
schemas — database-resident, versioned, read per run, no redeploy.

| Field | Purpose |
|-------|---------|
| `source` | Connection reference, or `public` for unauthenticated web |
| `strategy` | enumerate · query · traverse · tree · feed · search |
| `scope` | Object types, URL patterns, folder roots, filters — **what is in bounds** |
| `schedule` | cron · interval · once · manual |
| `incremental` | Watermark field, cursor, etag mode, or full-refresh |
| `limits` | Max items · max depth · rate · concurrency · **token budget** · wall-clock cap |
| `mapping` | Normalization schema + field mapping to apply |
| `output` | Project, memory type, tags, **ACL — inherited from the connection** |
| `politeness` | Respect robots · crawl-delay · user-agent identity *(traverse only)* |
| `priority` | live · normal · **bulk** — bulk never starves event-driven work |

## Run lifecycle

A three-day backfill must survive a pod restart, so a run is a first-class checkpointed entity.

| Concern | Behaviour |
|---------|-----------|
| Run states | `pending → running → completed \| failed \| cancelled \| partial` |
| Frontier | `queued → in-flight → done \| error \| skipped` |
| Checkpoint | Cursor plus frontier snapshot — resume, never restart |
| Control | Pause, resume, cancel, dry-run |
| Overlap | A run must not start while the previous is live — skip or queue, declared per config |
| Partial success | An error on one item does not fail the run; it is recorded and the run continues |

### Dry-run is not optional

A misconfigured `traverse` crawler with a loose URL pattern will happily ingest the public
internet on the customer's inference budget. Every config must be runnable in **dry-run** —
enumerate and report counts, fetch nothing, write nothing, spend nothing — before it is scheduled.

## Deduplication and change detection

Without change detection, a nightly crawl of 50,000 records re-embeds 50,000 records every night.
Three layers:

- **`external_id` upsert** — preserves `data_id` so a re-crawl updates rather than duplicates
- **Etag / last-modified** — skip before fetching, the cheapest possible check
- **Content hash** — skip enrichment when bytes are unchanged even if metadata moved

When content *has* changed, that is a revision — which routes into **W8 mutate**: new version,
re-embed, and invalidate the facts derived from the superseded version.

## Web crawling has obligations the others don't

| Requirement | Why |
|-------------|-----|
| **Honour `robots.txt` and crawl-delay** | Non-negotiable; also the cheapest way to avoid being blocked |
| **Identifying user-agent with a contact URL** | Operators need a way to reach you rather than blackhole you |
| Per-host concurrency cap and backoff | One crawler must not degrade someone else's site |
| Scope allowlist, not blocklist | Link traversal escapes any blocklist eventually |
| No authenticated crawling of third-party sites | Crawl what the tenant owns or what is public |
| Provenance tagging | Publicly-sourced content carries different licensing exposure and must be distinguishable at retrieval |

## Agentic crawlers: propose, don't execute

An agent that decides what to crawl next is a runaway loop with a budget attached. The safer
shape is the one already adopted for normalization mappings: **the agent authors the config, a
human approves it, the declarative crawler runs it.**

| | |
|---|---|
| **Good** | Point an agent at an undocumented API; it proposes strategy, scope, pagination and mapping as a reviewable config |
| **Also good** | Agent-assisted extraction *within* a fetched page — that is enrichment, not discovery |
| **Dangerous** | An agent choosing the frontier at runtime. Nondeterministic, unbudgetable, unreproducible, impossible to dry-run |

If runtime-adaptive crawling is wanted later it needs a hard step cap, a hard token budget, a
domain allowlist and a full decision log.

## Support across surfaces

| Surface | Capability |
|---------|------------|
| **API** | CRUD on configs; trigger, pause, resume, cancel runs; list runs; run detail and errors; dry-run |
| **SDK** | Config builders per strategy in the full client; run control in the admin client |
| **UI** | Config editor with live scope preview, **dry-run before save**, run history, progress, per-run errors, spend |
| **Infrastructure** | Own worker pool with its own ceiling — never colocated with enrich |
| **Scheduling** | Per variant: cron container locally, cluster CronJob on GKE, managed scheduler in cloud — behind one interface |

## Telemetry

Discovery rate · **dedupe hit rate** · frontier depth and size · **run duration against schedule
interval** (a run longer than its interval will overlap forever) · per-host politeness compliance ·
spend per run · **items-discovered trending to zero**, which is the crawler equivalent of a dead
connection and the single most valuable alert.
