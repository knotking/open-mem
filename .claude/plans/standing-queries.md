# Plan — standing queries (W10), and the thing they must not become

**Requirement.** Say once what you want to be told about, and be told when it
arrives. Four published use cases need it; media monitoring (UC11) is *only*
this, so shipping it without delivery ships nothing.

Status: **shipped.** `api/src/open_mem/standing.py` (795 lines), 21 tests.
Arrival matching, date rules, backtest-gated enable, per-query sequence and
webhook delivery are all live, with a Standing queries section in the console.

---

## 1 · Retrieval is pull-only, and that is the whole gap

Everything in the platform answers when asked. A standing query is the one
primitive that speaks first, and the catalog names four use cases blocked on it:
media monitoring, IoT thresholds, legal deadlines and compliance retention.

The catalog also names the shape:

```
standing_query
  selector    the same bounded selector dynamic memories use
  delivery    webhook | email | channel | memory-promotion
  scope       evaluated against new writes only
  owner       a principal — matches are ACL-filtered like any read
```

## 2 · Two of those four are not this feature

**Time-based triggers are not selectors.** *"Thirty days before a due date"* is
not a predicate over new writes, because **nothing arrives on that day**. A
standing query that could answer it would have to re-scan the corpus on a
schedule, and a re-scanning standing query is a full-table scan somebody will
register a hundred of.

So legal deadlines and retention ageing are a **scheduled sweep over date
facets** — W4's shape, and the expiry sweeper is the working example of it.
Conflating them is how the engine quietly becomes a scanner. This plan builds
the write-triggered half and **states the deferral rather than letting it be
discovered**: two mechanisms, and only one of them is here.

## 3 · Where it goes wrong, before anything is built

**Delivery is a read.** A match delivered to a principal who cannot see the item
is a data leak through the notification channel — and a nastier one than a
retrieval bug, because the payload travels outside the system to a URL somebody
configured months ago. ACL-filtered at **delivery** time with the owner's rights
**at that moment**, never rights copied at registration. The expiry sweep
already establishes the pattern: act under a principal scoped to the owner,
because there is no actor here who can see everything.

**Never re-scan.** Each item is seen once, which is what makes a standing query
cheap. The alert system already solved this exactly: a watermark over
`domain_events.sequence`, a batch cap, and a run row recording
`from_sequence`/`to_sequence`. `data.recorded` is emitted for every write and
carries the `data_id`, so the same walk works unchanged.

**A selector that matches everything looks identical to one that works** until
you read what it caught. Alerts learned this the expensive way — a backtest that
returned a count was useless, and now it returns the matches. Same rule here,
same gate: enabling before a backtest is a `409`.

## 4 · The one real design decision

A standing query needs to match on the **item**, and alerts match on an
**event's payload**. Two ways to close that gap.

### Option A — a new alert surface, `data.recorded`

Put the item's `data_type`, `tags` and a text preview into the `data.recorded`
payload, and a standing query becomes an ordinary alert with
`{"text": {"op": "contains", "value": ["acme"]}}`.

Nothing new: conditions, scope, backtest, watermark, runs, `alert_events`,
delivery, poll, the console screen. All of it, today.

And it is **the wrong matcher**. `contains` is a substring test over a preview:
no stemming, no phrase handling, no ranking, and it silently misses *"Acme's"*
and matches *"acmeism"*. A media-monitoring product whose matcher cannot find a
plural is not one.

### Option B — a selector evaluated in SQL against the item

```sql
to_tsvector('english', d.indexable_text) @@ websearch_to_tsquery('english', $q)
```

Plus structured narrowing: `data_type`, `tags && …`, `producer_id`. Evaluated
over the candidate set the watermark produced, which is bounded by the batch
cap, so this is a predicate over at most a few hundred rows and never an index
scan of the corpus.

`websearch_to_tsquery` is the reason to prefer it: quoted phrases, `or`, and
`-exclusion` are what somebody monitoring a brand actually types, and it is the
same lexical engine retrieval already uses, so a standing query and a search
agree about what the words mean.

> **Recommend B**, with A's plumbing underneath it: a separate `standing_queries`
> table and evaluator, reusing the watermark discipline, the run entity, the
> backtest gate and the delivery pipeline rather than reimplementing any of them.

**Semantic matching is deliberately not in v1.** A cosine threshold nobody can
calibrate produces either silence or noise, and the honest version needs a
score distribution in the backtest before anyone can pick a number. Lexical is
exact, explainable, and wrong in ways a person can see and fix.

## 5 · Delivery, without a second universe

`event_delivery` is alert-shaped: subscriptions filter on `alert_id` and the
sender reads `poll_events` from `alerts`. Making it generic is a refactor with
its own blast radius, so v1 takes the smaller path:

- A match writes a row in `standing_matches` — the item, the query, the run, the
  sequence, and **whether it was visible to the owner at delivery time**.
- `GET /standing-queries/{id}/matches` polls from a cursor, ACL-filtered by the
  reader's rights now, exactly as `alert-events` does.
- Push reuses `event_subscriptions` by widening its filter column from
  `alert_id` to a nullable `(kind, source_id)`. One migration, and the sender
  stops being alert-specific without becoming generic-in-the-abstract.

**Memory promotion** — the catalog's fourth delivery target — is the cheapest
and most interesting: a match adds the item to a named memory. No network, no
secret, no retry, and it composes with everything already built: a rollup over
that memory, a compaction job on it, an alert scoped to it.

## 6 · What a person sees

A screen that reads like Alerts, because it is the same job: define, backtest,
enable, watch. The parts that differ and matter:

- **The backtest shows the matches, not the count** — with the text that matched,
  because that is the only way to tell a good selector from one that caught the
  whole corpus.
- **What it will cost**: nothing per item, which is worth saying out loud next
  to a feature people expect to be expensive. No model call is made anywhere in
  this path.
- **Delivery state per match** — delivered, undeliverable, or *withheld because
  the owner could not see the item*. The third is the one nobody would think to
  render and the one that explains a silent feed.

## 7 · Implementation steps

1. `standing_queries` + `standing_matches`, and `event_subscriptions` gaining
   `(kind, source_id)` alongside `alert_id`.
2. The evaluator: watermark walk over `data.recorded`, selector in SQL, matches
   recorded, ACL checked per match under the owner's principal.
3. Backtest — same path, writes withheld, samples returned; enable gated on it.
4. Ride the existing minute sweep, as compaction does. No fourth Cloud Run job.
5. Delivery: poll endpoint first, then push through the widened subscription.
6. Memory promotion as a delivery target.
7. Console screen.

Steps 1–4 are a complete slice: a query that matches new writes and records what
it caught, visible in the API. Delivery is what makes it a product, and is the
next commit rather than the same one.

## 8 · Open questions

1. **Does a standing query see items it cannot deliver?** I assume it records the
   match and marks it withheld, because *"your query matched something you are
   not allowed to see"* is itself information — and arguably a leak. The
   alternative is not recording it at all, which makes the feed silently
   incomplete and unexplainable.
2. **One query per project, or per org?** Per project, matching every other
   scoped thing, with the door open to a project-list later.
3. **Does a match promote into a memory that then expires?** If promotion is the
   delivery, the TTL of that memory type decides how long the feed lives. That
   is either elegant or surprising and should be decided before it ships.
