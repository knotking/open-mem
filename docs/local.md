# Running mem-dog locally

The whole product runs on one machine with **no cloud account, no model key and
no billing**: Postgres with pgvector, the API, and the console. That is not a
degraded mode kept alive for tests — `LocalHashEmbedder` and
`LocalHeuristicExtractor` are registered engines like any other, and their rows
carry a `model_id`, so a corpus embedded locally is identifiable and
re-embeddable the day a real engine is assigned rather than quietly mixed in.

## The four commands

```bash
cd api
docker compose up -d                                   # Postgres 16 + pgvector on :54329
uv venv --python 3.12 .venv && uv pip install -e ".[dev]"
EMBED_DIM=64 .venv/bin/python -m memdog seed --demo     # 42 records, and see below
EMBED_DIM=64 .venv/bin/uvicorn memdog.app:app --port 8200
```

```bash
cd ui && npm install
MEMDOG_API_URL=http://localhost:8200 \
MEMDOG_API_KEY=<printed by the seed> \
MEMDOG_PROJECT_ID=<printed by the seed> \
MEMDOG_PRODUCER_ID=<from GET /api/v1/producers> \
npm run dev                                            # console on :3000
```

Those four variables are the only ones the console's server-side proxy reads.
`FIREBASE_WEB_API_KEY` is separate and only gates sign-in; without it the console
still works, because it reaches the API as a service identity rather than as a
browser session.

## `EMBED_DIM` has to match the index, everywhere

A database whose index was built at one dimension refuses to serve another:

```
index holds vector(64) but local-hash-v1 is configured for dim 768.
Re-embedding a corpus is not a migration -- set EMBED_DIM=64 or rebuild the index deliberately.
```

That check is doing its job. Two embedding models write vectors from different
spaces into one index and nothing fails — ranking just gets quietly worse — so
the mismatch is refused at startup instead.

**It applies to every process, not just the server.** The seed, the reconciler
and any `python -m memdog` command open the same pool and hit the same guard, so
an `EMBED_DIM` exported for `uvicorn` and forgotten for the CLI produces a
failure that looks like a broken seed. Set it in the environment, or set it
nowhere and rebuild the index deliberately.

## `bootstrap` runs once, and says so

```
bootstrap refused: 1 user(s) already exist. Bootstrap creates the first admin and
only the first; everyone after them arrives by invite.
```

Expected on any database that has been used before. To get a working tenant on
one that is already occupied, run the seed — it creates its own org, project,
producers and credentials.

## The seed is the acceptance test

It is not a fixture loader beside the tests. It drives the same API a real client
drives — no fixture path, no direct inserts — so a successful seed **is** the
end-to-end verification, and a failing one names the step that broke:

```
records     42 written, 39 enriched
case        1 asserted, 41 inferred members
questions   5/5 answered by the corpus
acl         the private record is hidden from the second member: True
```

Write, embed, enrich, retrieve, cite and enforce an ACL — proven in one command
before anyone opens a browser.

The corpus is deliberately, visibly synthetic: reserved names, documented fake
identifier ranges, and a `[DEMO — synthetic data, generated, not real]` marker on
every record. It is generated rather than anonymised, because anonymised real
data is real data that has been processed.

**The credentials it prints are shown once and are not recoverable.** They are
local and the data behind them is invented, but they are still keys — do not
paste them anywhere that keeps logs.

## What is not local

The seams with cloud implementations, and what stands in for each:

| Seam | Local | Cloud |
|---|---|---|
| `EmbeddingEngine` | `local-hash-v1`, offline, deterministic | Gemini, Ollama |
| Extraction | `LocalHeuristicExtractor` | Gemini |
| `BlobStore` | filesystem, GCS-shaped paths | GCS |
| `Queue` | in-process, at-least-once | Pub/Sub |
| `TokenVerifier` | API keys, SHA-256 | Firebase |

Only the first two cost money in the cloud, and both have local implementations
that are good enough to develop against — the heuristic extractor is the right
answer for most of a corpus, where a model adds nothing.
