# Source Coverage

Bespoke adapters do not scale to a 900-provider catalog. But sorting providers by *retrieval
shape* rather than by vendor shows the work concentrates in a minority — the majority need no
fetch logic at all.

## Providers by retrieval shape

| Shape | Share | Examples | Work needed |
|-------|-------|----------|-------------|
| **Record-only (JSON)** | ~60% | Stripe, Salesforce, HubSpot, Jira, Linear, Zendesk, Shopify | **none** — straight to enrich |
| **File-bearing** | ~20% | Gmail, Drive, Dropbox, Box, OneDrive, S3, Zoom | materialise + fan-out |
| **Stream / chat** | ~12% | Slack, Discord, Telegram, WhatsApp, Teams | persistent connection |
| **Pull-only** | ~8% | G2, Capterra, Trustpilot, Yelp, App Store | [crawler](crawlers.md) config |

## Capability profiles, not adapters

Replace per-provider code with declarative profiles interpreted by generic workers. Precedent
exists: `api/app/nango_provider_meta.py` is a static local mapping that synthesises
`app_category`, `capabilities` and `channel_key` because the credential broker does not track
them. This widens an established pattern.

```
ProviderProfile (data, not code)
  arrival        push | poll | stream | pull-only
  reference      inline | id | url | query
  content        record | file | export-only | mixed
  cursor         field name + type          (poll)
  fanout         none | shallow | deep
  export_formats [...]                      (Docs-like sources)
  limits         rps, burst, concurrency
  dedupe_key     which fields identify a resource version
```

### Four verbs

| Verb | Purpose |
|------|---------|
| `resolve(ref)` | Expand a pointer into concrete resources — this is where fan-out lives |
| `materialise(res)` | Stream bytes to the object store without buffering |
| `export(res, fmt)` | Server-side render — Google Docs have no raw bytes to download |
| `read(res)` | Fetch a structured API record — the cheapest verb, and what most providers need |

**`export` is the verb most likely to be missed.** An adapter model with only `download` cannot
express Google Docs at all.

## Support tiers

| Tier | Count | Promise to the user | What we build |
|------|-------|---------------------|---------------|
| **Tier 1** | ~10 | Full fidelity — attachments, exports, fan-out, incremental sync, deletions tracked | Bespoke adapter with all four verbs |
| **Tier 2** | ~50 | Records and files ingest reliably; normalized to canonical types; searchable by facet | Capability profile + mapping |
| **Tier 3** | ~240 | Connects via OAuth; records ingest as JSON; searchable semantically and by keyword | Nothing per-provider — the default path |

**Tier 3 carries 80% of the catalog, so the generic path must be genuinely good.** If it is an
afterthought, "300+ integrations" is a marketing claim rather than a capability. The good news: a
JSON record already classifies as `structured_json`, routes to the JSON agent, embeds and
entity-extracts on the existing path.

## Verify before building

The credential broker ships its own sync engine — scheduled incremental pulls with cursor state
and change webhooks. That overlaps substantially with the [crawler](crawlers.md). Confirm what the
deployed version supports *before* writing a crawler framework; it may collapse into writing a
consumer.
