# Legal — A Matter

Filings, correspondence, discovery exports and privileged analysis, over years, with a strict
distinction between what is *in* the matter and what merely mentions it.

## Setup

```
producer   key_01J…  DMS sync            enumerate
producer   whk_01J…  mail journaling     inbound_auth: api_key
producer   upl_01J…  discovery export    multi-GB mbox, streamed

case type  matter     identifiers[]  docket number, internal matter id
memory     matter-file ttl null      on_expiry keep_members
```

## The distinction that matters

```json
{ "external_id": "DOC-9912", "event_time": "2023-11-02T00:00:00Z",
  "content": { "kind": "stored", "storage_ref": "gs://…", "mime_type": "application/pdf" },
  "case": { "external_id": "M-2291", "case_type": "matter" },
  "membership": { "added_by": "explicit" },
  "metadata": { "tags": ["privileged", "counsel:external"] } }
```

versus one the system *suggested*:

```json
{ "case_id": "cas_01J…", "data_id": "data_01J…",
  "added_by": "inferred", "confidence": 0.71,
  "reason": "identifier match on docket 2291 in body text" }
```

**"This document is in the matter" and "this document appears related" are categorically
different claims.** A system that collapses them is useless for either purpose — you cannot produce
an inferred set in discovery, and you cannot rely on an asserted set that quietly includes guesses.

Inferred membership is reviewable and promotable; it never drives an access decision.

## Discovery export

A 4 GB `mbox` containing 60,000 messages:

```
upload  →  Stored(gs://…, 4.1 GB)
        →  W2 streams, never buffers
        →  fan-out: 60,000 messages, each its own enrich job
        →  each independently retryable; one corrupt message fails alone
```

Caps apply — expansion ratio, entry count, depth — because the same machinery that handles this
legitimately would also happily process an archive bomb.

## Legal hold beats erasure

A custodian leaves and requests erasure. Part of their mail is in a matter under hold.

```json
POST /api/v1/deletions
{ "mode": "erasure", "selector": { "subject": "person_01J…" } }

{ "status": "partial",
  "deleted": 11890,
  "withheld": [ { "case_id": "cas_01J…", "items": 513,
                  "hold": "hold_01J…", "authority": "Matter M-2291",
                  "applied_by": "user_01J…", "applied_at": "2026-02-11" } ],
  "requeued_on_release": true }
```

Silently deleting held data destroys evidence someone is legally obliged to preserve. Silently
ignoring the request is a compliance failure dressed as success. **The system does neither** — it
does what it can and names precisely what it did not, then re-queues automatically when the hold
lifts.

## What the design contributes

| Mechanism | Why it matters here |
|-----------|--------------------|
| **Asserted vs inferred membership** | The distinction the whole practice depends on |
| **Ethical walls** | Case-level deny lists that survive membership changes |
| **Legal hold + partial completion** | Both silent behaviours are indefensible |
| **Audit on every read** | Who saw privileged material, and when |
| **`event_time`** | "When did we first learn about this?" is a date question, not an ingestion question |
| **Streaming `mbox`** | A discovery export is one file containing tens of thousands of items |
| **Provenance as a list** | A summary spanning forty documents can be rebuilt when one is withdrawn |
