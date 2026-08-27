# `api/` — the Phase 1 spine

Write → store → read, end to end. This is slice 1 of [the roadmap](../docs/roadmap.md),
built in the order [implementation.md](../docs/operations/implementation.md#build-order--where-to-actually-start)
gives: schema, crypto, auth seam, model config, write, embed, read, invariant gate.

The milestone it exists to prove:

> Write an item into a project owned by an org with an access level → a search
> scoped to that project finds it → the response cites it → the read is audited
> → the row records which model embedded it.

That sentence is `tests/test_spine.py::test_write_then_retrieve_cites_the_item`.

## Run it

```bash
docker compose up -d                      # Postgres 16 + pgvector on :54329
uv venv --python 3.12 .venv && uv pip install -e ".[dev]"
.venv/bin/python -m pytest                # 24 tests, real database, no mocks
.venv/bin/python -m memdog bootstrap      # prints an org, project, producer and key
.venv/bin/uvicorn memdog.app:app --port 8200
```

Three endpoints: `POST /api/v1/write`, `GET /api/v1/data/{id}`, `POST /api/v1/retrieve`.

## The seams, and why each one exists now

Almost nothing here needs an interface. These five do, because the three
deployment variants genuinely differ underneath them — and because retrofitting
a seam means forking the request path.

| Seam | Phase 1 implementation | What arrives behind it |
|------|------------------------|------------------------|
| `TokenVerifier` (`auth.py`) | API keys, SHA-256, capability-scoped | Firebase, OIDC, local passwords |
| `Queue` (`queue.py`) | in-process, at-least-once, dead letters visible | NATS, Pub/Sub |
| `EmbeddingEngine` (`inference.py`) | `local-hash-v1`, offline and deterministic | Ollama (written), Gemini |
| `BlobStore` (`blobs.py`) | filesystem, GCS-shaped paths | GCS |
| `Envelope` (`crypto.py`) | AES-GCM, root key from env, **fails closed** | Cloud KMS |

`LocalHashEmbedder` is a registered model like any other, not a mock: its rows
carry `model_id = local-hash-v1`, so the day a real engine is assigned the old
vectors are identifiable and re-embeddable rather than quietly mixed in. It is
also what makes the air-gap acceptance test possible later.

## What the invariant gate actually checks

`tests/test_invariants.py`. Each of these is silent when it breaks, which is why
it is a test rather than a review comment.

- **Cross-tenant isolation on every retrieval path** — vector, lexical and
  hybrid, parametrised. A leak that exists only in the lexical arm is a leak.
- **ACL resolution at query time** — adding and removing a group member changes
  what the *same key* can retrieve, with no shared row rewritten. Revocation is
  the direction that matters.
- **`restricted` excludes the owner**, and a `shared`-scope connection produces
  org-visible items with nothing the caller sent reaching that decision.
- **Durability after 2xx** — the item survives a process that dies before
  enrichment; a worker started later picks it up from the same durable row.
- **One vector space per index** — `distinct(model_id) == 1`, and rows from a
  second engine are invisible to retrieval rather than silently ranked against
  incomparable neighbours.
- **Capability ≠ identity** — a `data:read` key belonging to a member cannot
  write; leaving the org revokes the key immediately.

## Deliberately not built yet

Named because "not present" and "overlooked" should not look the same:

| Not here | Where it belongs |
|----------|------------------|
| Fetch worker for `Pending` content | slice 3 — the item lands with `is_downloaded = false`, which is the correct state, not a gap |
| Memories, cases, `memory_members` | slice 2 / 7. The write API's `memory` and `case` fields parse and are ignored |
| Enrichment, artifacts, entities | slice 2. `artifacts` / `artifact_sources` are in the schema because the erasure reverse lookup cannot be retrofitted |
| Content-hash dedupe of the derived layer | slice 4, where the second copy first arrives |
| Presigned uploads, crawlers, connectors | slices 3–5 |
| Control-plane endpoints | `bootstrap.py` performs the same sequence they will |

## Two places the documents disagreed

Found by building, not by reading. Both are resolved in code with the reasoning
in a comment at the resolution site.

1. **The classification cascade contradicts the MIME precedence rule.**
   `schema.md` says `mime_type` outranks `source_type`; `workers.md` puts
   `source_type` at layer 2, *above* the MIME registry at layer 5. Taken
   literally, a caller declaring `source_type: pdf` over JSON bytes routes to
   the PDF agent — the exact defect the rule exists to prevent. Resolved in
   `classify.py`: `source_type` keeps layer 2, but a hint that *contradicts* the
   sniffed MIME is discarded.

2. **`state` has two vocabularies.** `data_items.state` is
   `stored | searchable | enriched`; the write-API response example shows
   `queued` and `fetch_pending`. The staircase is the one clients are told to
   poll on, so the column wins and the response reports `stored`. The example
   needs updating.

## One bug the live run found that the tests did not

The API silently ingested nothing when configured for a different embedding
dimension than the index was created with: every insert failed, the queue
retried, gave up, and the symptom was items stuck at `stored` — indistinguishable
from ordinary enrichment lag.

Two fixes, because the dimension mismatch was the bug and the silence was the
worse one: `verify_index_dimension` refuses to start the process against an
index it cannot write to, and `InProcessQueue` records and logs dead letters
instead of dropping them quietly.
