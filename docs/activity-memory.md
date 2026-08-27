# Activity Memory

**Optional. Off by default.** When a user switches it on, their actions on data are captured as
**records** — ordinary data items in a memory of their own — so *"what was I working on last
Tuesday?"* becomes a retrieval query rather than a question nobody can answer.

It is the personal-memory family pointed at the user's own behaviour, and it reuses machinery that
already exists: a memory type, the write path, the deletion cascade.

---

## It is not the audit log, and the difference is total

The same events feed both. Everything else about them is opposite.

| | [`audit_events`](security/audit.md) | Activity memory |
|---|---|---|
| **Purpose** | Governance — what the organisation must be able to prove | Recall — what the user wants to remember |
| **Optional** | No | **Yes, off by default** |
| **Owned by** | The organisation | **The user** |
| **Contains content** | **No** — identifiers only | **Yes** — it is a record like any other |
| **Searchable** | Queried by filter | **Retrievable, and part of answers** |
| **User can delete** | **No** | **Yes, entirely** |
| **Retention** | Never purged | The user's TTL |

> **A user may delete their memory of what they did. They may not delete the audit of it.**
>
> This has to be said plainly in the product, not only here, because *"delete my activity"* sounds
> like it should erase the trace. It erases the user's copy. The organisation's record of a read is
> a different object with different obligations, and a system that let one delete the other would
> have no audit at all.

## One instrumentation point, two sinks

Activity capture is a **subscriber to the audited action**, not a second instrumentation pass.

```
    an action occurs
          │
          ├──▶ audit_events        always, mandatory, ids only
          │
          └──▶ activity memory     only if the user enabled it
                                   as a record, with content
```

Instrumenting twice guarantees the two drift — an action audited but not captured, or captured with
a different name — and then *"my activity shows this but the audit does not"* has no explanation.
One emission, two subscribers.

## What gets captured

The same closed action vocabulary the audit log uses, filtered to the user's own actions:

| Category | Examples |
|----------|----------|
| **Read** | Opened an item, ran a query and what it returned |
| **Write** | Created, uploaded, imported |
| **Organise** | Added to a memory, re-typed, linked, tagged |
| **Share** | Shared with someone, revoked |
| **Delete** | Removed an item or a membership |

Each is a record carrying the [standard envelope](ingestion/extraction-prompts.md) — a generated
`title` (*"Opened 'Q3 security review'"*), a `description`, `keywords`, and the action's structured
fields as extensions. So it renders in the item inspector and appears in search like anything else.

### Two things it must never capture

- **The content the action touched.** The activity record references the item; it does not copy it.
  A user who later loses access to a document must not retain its text inside their own activity
  history — which is exactly what copying would produce.
- **Its own reads.** See below.

## The recursion, and why it is not the audit resolution

If activity records are data, and reading data is an action, then **reading your activity creates
activity** — and browsing a week of history generates a week of new history.

[Audit terminates this](security/audit.md) by classing the read of an audit record as the same
event, so the chain does not grow. **Activity cannot use that resolution**, because unlike audit it
is optional, content-bearing and paid for per record: even a terminating chain would mean a single
browse session multiplying the corpus.

So activity capture **excludes itself entirely**. Reads of activity records generate no activity
records. The audit log still records them, which is where that trace belongs.

## Cost, and why it is not embedded by default

Every action becoming an enriched, embedded record is expensive for output nobody searches
semantically — *"opened document X"* is a structured fact, not prose.

So the default is the same call [telemetry](operations/telemetry.md) makes: **`enrich: false`,
`embed: false`.** Activity is facet-searchable — by action, by date, by target — which is how these
questions are actually asked. Embedding is opt-in for users who want *"find when I was working on
something about pricing"* and are willing to pay for it.

## Who may turn it on

**The user, for themselves.** An administrator may not enable it on a member's behalf.

That constraint is the whole difference between a memory feature and employee monitoring. Activity
capture records what a person did, in their own words, retrievably — and a version an admin can
switch on for someone else is a surveillance tool wearing a recall feature's interface.

Where an organisation has a genuine obligation to track member activity, **the audit log already
does it**, under organisational ownership, without content, and without pretending to be the user's
own memory. If org policy requires activity capture specifically, it must be **visible to the user
as an org-enforced setting** rather than silently enabled — a lock they can see, per the
[settings precedence model](roadmap.md).

## API

Activity is a memory, so the standard memory endpoints already read and delete it. What it adds is
a capture setting:

```http
GET   /api/v1/users/{id}/activity-capture
PUT   /api/v1/users/{id}/activity-capture
      { "enabled": true,
        "actions": ["read", "write", "share"],
        "ttl_days": 90,
        "embed": false }

GET   /api/v1/users/{id}/activity          list and filter — a scoped retrieval
DELETE /api/v1/users/{id}/activity         purge everything, as a resumable run
```

Three properties follow from it being an ordinary memory rather than a special store:

- **Deletion is the standard cascade** — [async, tombstone-then-purge](operations/deletion.md),
  resumable, with `purged_at` proving completion. No bespoke deletion path.
- **TTL is a memory type setting.** `activity` ships with a TTL and `orphan_delete`, so history
  ages out without anyone running a cleanup job.
- **It is private by construction.** Activity records are `private` ACL, owned by the user — they
  are never org-visible, whatever the project default, for the same reason a personal connection's
  data is not.

## Turning it on does not fill in the past

Capture starts when it is enabled. There is no backfill, because the events were never recorded as
records — the audit log has them, but converting audit rows into activity records would import
identifiers without content and produce a history that looks complete and is not.

**The UI must say so at the moment of enabling**, or the first question will be why last month is
empty.

---

## Requirements

- **FR-ACT-1** Activity capture MUST be off by default and MUST be enabled per user.
- **FR-ACT-2** An administrator MUST NOT enable activity capture on another user's behalf. Where org
  policy enforces it, the setting MUST be visible to the user as locked.
- **FR-ACT-3** Activity records MUST be ordinary data items in a per-user memory, subject to the
  standard ACL, retrieval and deletion paths.
- **FR-ACT-4** Activity records MUST be `private` and owned by the user.
- **FR-ACT-5** Activity capture MUST be a subscriber to the audited action, not a second
  instrumentation point.
- **FR-ACT-6** Activity records MUST reference the item acted on and MUST NOT copy its content.
- **FR-ACT-7** Reads of activity records MUST NOT generate activity records.
- **FR-ACT-8** Activity MUST default to `enrich: false` and `embed: false`; embedding MUST be opt-in.
- **FR-ACT-9** Deleting activity MUST NOT delete audit records, and the product MUST state this
  where the deletion is offered.
- **FR-ACT-10** Enabling capture MUST NOT backfill, and the UI MUST say so at the point of enabling.
- **FR-ACT-11** Capture settings MUST be readable and writable through the API, and activity MUST be
  listable and deletable through the API.
