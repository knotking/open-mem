# Plan — sync state, live run status, and the connectors that are missing

**Goal.** Make crawlers remember where they got to *per scope*, know what each
API allows, be schedulable against those limits, and be watchable while they
run. Then add the connectors the project-signals work needs and does not have.

Status: **shipped.** Sync state is per scope, a run reports itself while it
runs, and the heartbeat defect this plan opens with is fixed. See
`crawling.py` and `crawlers.py` (1,072 lines), 45 tests in `test_crawlers.py`
plus `test_source_shapes.py` and `test_connector_salesforce.py`, which crawl
simulators rather than reading templates.

---

## 1 · A defect first, because it will bite before any of this ships

`STALE_HEARTBEAT_SECONDS = 300`, and `reap()` marks any `running` row whose
heartbeat is older than that as **`interrupted`**. The heartbeat is written at
exactly three points: when the run starts, after discovery finishes, and when
the run completes.

**Nothing writes it during emission**, which is the long phase. So a crawl whose
emit takes more than five minutes is reaped as interrupted *while it is still
running* — and `max_items` defaults to **1000**, so this is not a large-corpus
edge case, it is the ordinary case for any real source.

Worse, it presents as noise rather than as a bug: crawls "randomly" marked
interrupted, a watermark that does not advance, and a next run that re-fetches
everything. Fix before anything else here — a heartbeat inside the emit loop,
and the run counters written with it, which §4 needs anyway.

---

## 2 · What already exists

More than a plan for this would assume, so the work is narrower than it looks.

| | |
|---|---|
| `crawlers.watermark` | Advanced **only on a completed run** |
| `crawl_runs.watermark_before` / `_after` | Every move auditable |
| `crawl_runs.checkpoint` | Resumable within a run |
| `crawl_frontier` | Per-run queue, per-item status |
| `crawl_seen` | Per-item `version_hash` — etag, version field or content hash |
| `discovered · emitted · skipped · failed · bytes_seen` | Columns exist |
| `heartbeat_at` + `reap()` | Dead workers detected |
| `CrawlerConfig.incremental` + `{{ watermark }}` | **The mechanism is built** |
| `Budget` (`max_items`, `max_depth`) | Bounds one run |

**And what does not exist:** a watermark per *scope*, any notion of what an API
allows, rate-limit state anywhere, counters that move during a run, and — in
all 37 templates — any use of the incremental mechanism that is already there.

---

## 3 · Sync state: three separate things that get conflated

### 3a · The cursor is per scope, not per crawler

One Slack crawler over forty channels has **one** watermark. A single busy
channel drags it forward and the thirty quiet ones are re-scanned from that
point forever; or the reverse, and the busy one is skipped.

```
crawl_cursors
  crawler_id, scope        -- "#eng-platform", "owner/repo", "PROJ"
  cursor      text         -- opaque: a timestamp, an etag, a page token
  kind        text         -- watermark | etag | page
  items_seen  bigint
  last_ok_at, updated_at
  PRIMARY KEY (crawler_id, scope)
```

`crawlers.watermark` stays as the single-scope case (`scope = ''`), so nothing
existing changes shape. **Opaque on purpose** — a Jira cursor is a timestamp, a
GitHub one is an etag, a Salesforce one is `nextRecordsUrl`. The moment this
column tries to be a timestamp it stops fitting half the catalog.

The same rule carries over: **advanced only when that scope completes.**

### 3b · Limits belong to the API; the remaining budget belongs to the token

The tier is a property of the API, so it goes in the **template**, beside the
URL that knows it — nobody configuring a Jira crawler should have to go and read
Atlassian's rate-limit page:

```jsonc
"limits": { "requests_per_minute": 50,
            "requests_per_day": 10000,
            "retry_after_header": "Retry-After",
            "reset_header": "X-RateLimit-Reset" }
```

But what is *left* belongs to the credential, because two crawlers sharing one
Slack token draw on the same quota and neither can see the other:

```sql
ALTER TABLE connections ADD COLUMN limited_until timestamptz;
ALTER TABLE connections ADD COLUMN spent_today int NOT NULL DEFAULT 0;
ALTER TABLE connections ADD COLUMN spend_reset_at timestamptz;
```

### 3c · The scheduler reads them

A tick that fires while a token is cooling should record **`skipped: rate
limited`** rather than burning a run — the same shape `overlap: skip` already
uses for a live run, and for the same reason: a run that cannot succeed should
not consume the slot that says it tried.

A `429` sets `limited_until` from `Retry-After` and ends the run as **`partial`**,
which is already a valid status and already means *the watermark did not move*.

---

## 4 · Watching a run while it runs

The columns are there; nothing writes them until the end. A run emitting nine
hundred items shows `discovered=1000, emitted=0` for its whole life.

- **Update counters and heartbeat every N items** inside the emit loop — same
  write, which is also the §1 fix.
- **`GET /api/v1/crawl-runs/{id}`** already exists; it becomes useful once the
  numbers move.
- **Per-scope progress** from `crawl_cursors`: which scopes are done, which are
  in flight, which have not started.
- **A rate-limit reason on the row.** "Waiting on Slack until 14:32" is a state,
  and without it a stalled crawl and a rate-limited one look identical.

### And the number that matters most downstream

**Lag per source.** Every project signal computed over a source that stopped
syncing is confidently wrong, and the worst case is exactly the one the signals
plan leads with: *"stalled — no activity for 7 days"* is indistinguishable from
*"the connector broke 7 days ago"*. So `last_ok_at` per scope is surfaced, and a
signal over a stale source **declines to compute** rather than computing.

---

## 5 · Templates: fill in the two blocks that are blank

The mechanism exists and **not one of the 37 templates uses it**, so every
crawler built from one re-fetches everything each run and leans on `crawl_seen`
to suppress the duplicates. Correct, and against a rate-limited API the
difference between viable and not.

| | Incremental clause |
|---|---|
| Jira | `jql: "updated >= '{{ watermark }}'"` |
| GitHub | `since={{ watermark }}` |
| Salesforce | `WHERE LastModifiedDate > {{ watermark }}` |
| Confluence | `cql` with `lastmodified >=` |
| Slack | `oldest={{ watermark }}` |

Do these **one at a time with a dry run against a real credential**. All 37 are
`verified: false`, and adding an incremental clause makes a template more useful
without making it any more true.

---

## 6 · The connectors that are missing

The stated goal names *"github, devops systems"*, and the catalog has **no CI, no
incidents, no deploys, no errors**. That is the gap worth closing, and three of
them cost almost nothing because they reuse auth that already works.

### First, because they answer "is this shipping and staying up"

| | Auth | Note |
|---|---|---|
| **GitHub Actions** | bearer | Same token as the GitHub connector already shipped |
| **Sentry** | bearer | Issues and release health, cursor paginated |
| **PagerDuty** | bearer | Incidents, offset paginated |

### Then, in rough order of value to the same use case

GitLab · Bitbucket · Azure DevOps (boards and pipelines) · CircleCI ·
Datadog monitors · Snyk. **Slack as a crawler** rather than only a webhook —
today new messages arrive but history cannot be backfilled, and a project's
context lives in its history. **Gong / Fireflies / Otter**, because decisions
happen in meetings and UC12 is about exactly that.

### A different axis, and a separate decision

Reddit · Hacker News (Algolia, **no auth at all**) · SerpAPI or Brave ·
Stack Overflow · G2 / Trustpilot · SEC EDGAR.

These are not project-status sources; they are **external signal** — what the
world says about you. That is UC11 Media Monitoring, which the catalog marks as
a **Gap** and describes as *only* a push product: it needs the standing-query
worker before external sources are worth much. Worth choosing deliberately
rather than drifting into.

> **Adding entries is cheap and proves nothing.** Zero of thirty-seven are
> verified, and the changelog is blunt that *"the dry run is where an entry
> stops being a researched guess."* Three added and verified beats twenty added.

---

## 7 · Sequencing

1. **The heartbeat fix** (§1). It is small and everything else runs on top of a
   crawler that is not being reaped mid-run.
2. **Live counters** in the emit loop — the same write.
3. **`crawl_cursors`**, with `scope = ''` preserving today's behaviour.
4. **`limits` in templates, rate-limit state on connections**, and the scheduler
   skipping a cooling token.
5. **Incremental clauses**, one connector at a time, each with a dry run.
6. **GitHub Actions, Sentry, PagerDuty** — and verify them, which no connector
   has ever been.

Steps 1–2 are one commit. Step 3 is a migration and a read-path change. Steps
5–6 are per-connector and never end.

---

## 8 · Open questions

1. **What is a scope, generically?** Channel, repo, Jira project, Drive folder.
   It is whatever the template's `{placeholder}` binds — so it may be derivable
   rather than configured, which would be better.
2. **Does a rate-limited run count against `overlap: skip`?** It ended without
   doing anything, so arguably the next tick should proceed rather than treating
   it as a live run.
3. **Does a stale source block a signal, or annotate it?** Blocking is safer and
   noisier; annotating is honest and easy to ignore. The signals plan assumes
   blocking.
