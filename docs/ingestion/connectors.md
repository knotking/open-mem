# Connector Catalog

"What do we support" has **four honest answers**, and collapsing them is how coverage claims stop
being defensible.

| Layer | Count | Meaning |
|-------|-------|---------|
| **Live** | 4 | Connected and moving data now — Slack, Gmail, Drive; Zoom configured awaiting a test recording |
| **Adapter deployed** | 34 | Gateway code exists, whether or not anyone connected it |
| **Documented** | 84 | Setup guide written under `docs/apps/` |
| **Catalog** | 900+ | The credential broker has an OAuth template |

```
catalog          ████████████████████████████████████████  900+
documented       ████████                                   84
adapter deployed ███                                        34
live             ▌                                           4
```

**The gap between catalog and live is the roadmap.** Tier-3 generic handling closes the top band
cheaply; tier-1 adapter work moves things into the bottom one.

## Shape drives machinery

`record` needs nothing beyond the default path · `file` needs materialise and fan-out ·
`stream` needs a stateful persistent connection · `crawl` needs schedule and watermark state ·
`export` needs server-side rendering.

## Communication & messaging

| Connector | Shape | Tier | Status | Notes |
|-----------|-------|:----:|--------|-------|
| **Slack** | stream + file | 1 | **live** | Events API today; Socket Mode is the stateful path |
| **WhatsApp Business** | stream + file | 1 | adapter | Voice notes arrive as `opus`/`amr` |
| **Telegram** | stream + file | 1 | adapter | Long-polling — persistent connection |
| Discord | stream + file | 2 | adapter | Gateway socket, one per bot |
| Microsoft Teams | stream + file | 2 | adapter | Also a meetings source |
| Twilio | record | 2 | adapter | SMS/voice events |
| Front | record | 3 | documented | Shared inbox |

## Email & calendar

| Connector | Shape | Tier | Status | Notes |
|-----------|-------|:----:|--------|-------|
| **Gmail** | file + fan-out | 1 | **live** | `historyId` → messages → attachments; watch expires ~7 days |
| **Outlook** | file + fan-out | 1 | documented | Same shape as Gmail |
| **Google Calendar** | record | 2 | documented | **Underrated** — maps straight to canonical `Event`, no LLM |
| Mailchimp / Mailgun / SendGrid | record | 3 | documented | Campaign and delivery events |

## Storage, documents & meetings

| Connector | Shape | Tier | Status | Notes |
|-----------|-------|:----:|--------|-------|
| **Google Drive** | file + fan-out | 1 | **live** | Folder changes fan out deeply |
| **Google Docs** | export | 1 | documented | **Export only** — no raw bytes exist |
| **Notion** | export | 1 | adapter | Block tree, not a file |
| Dropbox / Box / OneDrive | file + fan-out | 2 | documented | Same verbs as Drive |
| AWS S3 / Azure Blob / GCS | file + crawl | 2 | documented | Bucket sync via the `tree` crawler, not upload |
| Google Sheets | export | 3 | documented | Row-count caps matter |
| **Zoom** | file + fan-out | 1 | configured | One recording → video, audio, transcript, chat |
| Google Meet | file | 2 | adapter | Arrives via Drive |
| Contentful / WordPress | record | 3 | documented | CMS content |

## Work tracking & development

| Connector | Shape | Tier | Status |
|-----------|-------|:----:|--------|
| **Jira** | record + file | 1 | adapter |
| **GitHub** | record + file | 1 | adapter |
| Linear | record | 2 | adapter |
| Asana | record | 2 | adapter |
| ClickUp / Monday / Trello / Basecamp / Todoist | record | 3 | documented |
| GitLab / Bitbucket | record + file | 3 | documented |
| Vercel / Netlify | record | 3 | documented |

## CRM, support, finance, HR

| Connector | Shape | Tier | Status | Notes |
|-----------|-------|:----:|--------|-------|
| Salesforce / HubSpot | record | 2 | adapter | Map to canonical `Person` + `Organization` |
| Pipedrive / Zoho CRM / Freshsales | record | 3 | documented | Same canonical targets |
| Zendesk / Freshdesk / Help Scout | record + file | 3 | documented | Ticket + thread |
| Stripe | record | 2 | adapter | Canonical `Transaction` |
| PayPal / Square / QuickBooks / Xero / Brex / Plaid | record | 3 | documented | Facet-searchable once normalized |
| Shopify / WooCommerce | record | 3 | documented | Orders, products |
| BambooHR / Gusto / Rippling / Workday | record | 3 | documented | **Sensitive PII** — privacy defaults matter most here |

## Observability, reviews, social, data

| Connector | Shape | Tier | Status | Notes |
|-----------|-------|:----:|--------|-------|
| Datadog / Sentry / PagerDuty / Opsgenie / Grafana | record | 2 | adapter | Payload usually complete — no fetch needed |
| Yelp / G2 / Capterra / Trustpilot / TripAdvisor / Google Business / App Store | **crawl** | 2 | adapter | No webhooks — these are why the [crawler](crawlers.md) exists |
| Twitter / Reddit / LinkedIn / Instagram / Facebook / YouTube | crawl + file | 3 | documented | Rate limits are the binding constraint |
| BigQuery / Snowflake | query | 3 | documented | Cursor on a column — a `query` crawler |
| Airtable | record | 3 | documented | |

## Three catalog corrections

**OpenAI, Anthropic and Pinecone are documented under `docs/apps/` but are not data connectors.**
The first two are model providers; the third is a vector store. Listing them as integrations
inflates the connector count and confuses the taxonomy.

**Google Calendar is documented but not deployed**, despite being one of the cheapest,
highest-value sources available — `ics` maps to a canonical `Event` with no inference call at all.

**HR connectors carry the most sensitive payloads** in the catalog — compensation, performance,
personal records. In a team-tenancy model their privacy defaults need deciding *before* they are
enabled.
