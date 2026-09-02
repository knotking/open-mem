# Running and using mem-dog locally

The whole product runs on one machine with **no cloud account, no model key and
no billing**: Postgres with pgvector, the API, and the console. Write, embed,
enrich, search, cite, and the knowledge graph — all of it, offline.

That is not a degraded mode kept alive for tests. `LocalHashEmbedder` and
`LocalHeuristicExtractor` are registered engines like any other, and their rows
carry a `model_id`. A corpus embedded locally is therefore *identifiable and
re-embeddable* the day a real engine is assigned, rather than quietly mixed into
vectors from a different space. It is also what makes an air-gapped install
possible rather than aspirational.

[usage.md](../usage.md) covers the same surface against a deployed system. This
document is its local counterpart: the same API, the same console, no cloud.

---

## 1 · Bring it up

```bash
cd api
docker compose up -d                                   # Postgres 16 + pgvector on :54329
uv venv --python 3.12 .venv && uv pip install -e ".[dev]"
EMBED_DIM=64 .venv/bin/python -m memdog seed --demo     # 42 records; see §3
EMBED_DIM=64 .venv/bin/uvicorn memdog.app:app --port 8200
```

The seed prints an org, a project and two credentials. Keep them — they are
**shown once and are not recoverable**, and the console needs them.

```bash
cd ui && npm install
MEMDOG_API_URL=http://localhost:8200 \
MEMDOG_API_KEY=<printed by the seed> \
MEMDOG_PROJECT_ID=<printed by the seed> \
MEMDOG_PRODUCER_ID=<from GET /api/v1/producers> \
npm run dev                                            # console on :3000
```

Those four variables are the only ones the console's server-side proxy reads.
`FIREBASE_WEB_API_KEY` is separate and gates **sign-in only** — without it the
console still works, because it reaches the API as a service identity rather
than as a browser session. That is also why the console never holds a user's
API key in the browser.

---

## 2 · `EMBED_DIM` must match the index, in every process

The single thing most likely to cost you an afternoon.

```
index holds vector(64) but local-hash-v1 is configured for dim 768.
Re-embedding a corpus is not a migration -- set EMBED_DIM=64 or rebuild the index deliberately.
```

The guard is doing its job. Two embedding models writing into one index do not
fail — ranking just gets quietly worse — so the mismatch is refused at startup
instead of discovered months later as "search got bad".

**It binds every process, not just the server.** The seed, the reconciler, and
any `python -m memdog …` command open the same pool and hit the same check. An
`EMBED_DIM` exported for `uvicorn` and forgotten for the seed produces a failure
*inside the seed*, which reads as a broken seed rather than a mismatched
environment.

Set it once in your shell, or set it nowhere and rebuild the index deliberately:

```bash
export EMBED_DIM=64      # must match whatever the index was built at
```

---

## 3 · The seed is the acceptance test

It is not a fixture loader living beside the tests. It drives the same API a
real client drives — no fixture path, no direct inserts — so **a successful seed
is the end-to-end verification**, and a failing one names the step that broke:

```
records     42 written, 39 enriched
case        1 asserted, 41 inferred members
questions   5/5 answered by the corpus
acl         the private record is hidden from the second member: True
```

Write → embed → enrich → retrieve → cite → enforce an ACL, proven in one command
before anyone opens a browser. The five questions are the acceptance assertions:
if *"what did we promise Acme?"* stops returning the commitment, something broke.

`--reset` re-runs it through the ordinary delete cascade rather than truncating
tables, so the reset path is exercised too:

```bash
EMBED_DIM=64 .venv/bin/python -m memdog seed --demo --reset
```

**The corpus is deliberately, visibly synthetic**: reserved names, documented
fake identifier ranges, and a `[DEMO — synthetic data, generated, not real]`
marker on every record. It is generated rather than anonymised, because
anonymised real data is real data that has been processed.

---

## 4 · Using it

Everything below is the same API the cloud deployment serves. `$KEY` and `$PRJ`
come from the seed.

```bash
BASE=http://localhost:8200
```

**Ask a question and get a cited answer.** The scope lives in `filter`, not at
the top level — asking is scoped exactly the way searching is, so the two share
one shape:

```bash
curl -s -X POST "$BASE/api/v1/ask" -H "X-API-Key: $KEY" \
  -H 'content-type: application/json' -d "{
  \"question\": \"what did we promise Acme?\",
  \"filter\": {\"project_id\": \"$PRJ\"}
}"
```

> **Locally, an answer is quoted rather than written.** With no model configured,
> the answerer returns *"The closest passages, quoted rather than summarised"*
> followed by the passages themselves. It still reports `grounded: true` and
> still carries citations, because grounding and citation are properties of
> retrieval rather than of the model. Assign a generation engine and the same
> call returns prose — the envelope does not change, which is the point of the
> seam.

**Write a record.** `content` is a discriminated union, so it needs its `kind`;
omitting it fails with `Unable to extract tag using discriminator 'kind'`.
Enrichment is opt-in because it costs money in the cloud — locally it costs a
little CPU, but the flag behaves identically, so what you test is what ships.

```bash
curl -s -X POST "$BASE/api/v1/write" -H "X-API-Key: $KEY" \
  -H 'content-type: application/json' -d "{
  \"producer_id\": \"$PRODUCER\",
  \"items\": [{
    \"external_id\": \"note-1\",
    \"content\": {\"kind\": \"inline\", \"text\": \"...\"}
  }],
  \"options\": {\"enrich\": true}
}"
```

A write returns **207**, not 200: each item succeeds or fails on its own, and
the response carries a per-item `status`, `data_id`, `state`, and the `memories`
it joined. A partial batch is a normal outcome rather than an error.

**Browse the graph and the entities** the corpus mentions:

```bash
curl -s -H "X-API-Key: $KEY" "$BASE/api/v1/projects/$PRJ/entities?limit=20"
curl -s -H "X-API-Key: $KEY" "$BASE/api/v1/entities/$ENT/graph"
```

**Point an MCP client at it.** The local server speaks the same streamable-HTTP
transport as the deployed one, so any MCP-capable agent works unchanged:

```
url:     http://localhost:8200/api/v1/mcp
header:  Authorization: Bearer <key>
```

If the client runs in a container, `localhost` is *the container's* localhost —
use `http://host.docker.internal:8200/api/v1/mcp`, or run it with
`--network host`. This is the single most common local MCP failure.

---

## 5 · The console

`http://localhost:3000`, against the same corpus.

The browser never talks to the API directly: `lib/types.ts` sends every call to
`/api/proxy/<path>`, which is a Next.js route that attaches the credential
server-side. So `curl http://localhost:3000/api/v1/projects` returns the app
shell, not JSON — the proxied form is what answers:

```bash
curl -s http://localhost:3000/api/proxy/api/v1/projects
```

Worth knowing when debugging: a 404 from the console's API is usually a missing
`/api/proxy/` prefix, not a broken route.

---

## 6 · What differs from the cloud

Every seam has a local implementation; only the first two cost money in the
cloud.

| Seam | Local | Cloud |
|---|---|---|
| `EmbeddingEngine` | `local-hash-v1` — offline, deterministic | Gemini, Ollama |
| Extraction | `LocalHeuristicExtractor` | Gemini |
| `BlobStore` | filesystem, GCS-shaped paths | GCS |
| `Queue` | in-process, at-least-once, dead letters visible | Pub/Sub |
| `TokenVerifier` | API keys, SHA-256, capability-scoped | Firebase |
| `Envelope` | AES-GCM, root key from env, **fails closed** | Cloud KMS |

The heuristic extractor is not a stand-in for the model path — it is the right
answer for the majority of a corpus, where a model adds nothing. It always
produces a title, and returns **nulls where nothing can be determined** rather
than inventing a plausible description or language.

`Envelope` failing closed matters locally too: without `MEMDOG_MASTER_KEY`,
anything that stores a signing secret returns `503 encryption is not configured`
rather than storing it in the clear.

---

## 7 · When it will not start

| Symptom | Cause |
|---|---|
| `bootstrap refused: 1 user(s) already exist` | Expected on a database used before. Bootstrap creates the *first* admin only; run the seed instead, which makes its own tenant. |
| `index holds vector(N) but … configured for dim M` | `EMBED_DIM` disagrees with the index. See §2 — and set it for the CLI too. |
| `column "…" does not exist` after a schema change | A migration was edited after being applied. `schema_migrations` records the version and the runner skips it, so an edit reaches only databases that never saw it. Add the next number instead. |
| Console renders but every panel is empty | `MEMDOG_PROJECT_ID` points at a project the key cannot see, or the seed ran against a different tenant. |
| MCP client cannot connect from a container | `localhost` is the container's own. Use `host.docker.internal`. |

Logs are plain uvicorn output; the queue's dead letters are visible in the
database rather than swallowed, which is usually where a stuck enrichment shows
up first.
