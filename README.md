# mem-dog

**A memory layer that can show its work.**

Write anything — documents, spreadsheets, calendars, email, audio, video — and get it back by
meaning, with a trace of exactly why each result was returned and what was considered and dropped.

Most memory systems answer *what do you remember?* This one also answers **why did you say that,
which model produced it, who is allowed to see it, and can you prove you deleted it.**

```
54 file formats   ·   24 data types   ·   18 extraction prompts
9 webhook providers   ·   3 crawler strategies   ·   12 graph predicates
344 tests, against a real database, no mocks
```

Every number above is counted from the build at request time, not written into this file. The
sign-in page reads them from `GET /api/v1/capabilities`.

---

## Run it

```bash
cd api
docker compose up -d                        # Postgres 16 + pgvector on :54329
uv venv --python 3.12 .venv && uv pip install -e ".[dev]"
.venv/bin/python -m pytest                  # real database, no mocks — and it drops the schema
.venv/bin/python -m memdog bootstrap        # prints an org, project, producer and key
.venv/bin/uvicorn memdog.app:app --port 8200
```

For something to look at rather than an empty database:

```bash
.venv/bin/python -m memdog seed --demo      # 42 records, one worked sales renewal
```

The seed goes in through the public write API with a registered producer, then asks the corpus five
questions and checks it answers them, checks a second member cannot read the private record, and
checks the audit log recorded the reads. **A failing seed names the step that broke** — which is the
point of it running the real path rather than inserting rows. `--reset` purges the demo through the
ordinary delete cascade and seeds again.

```bash
cd ui && npm install && npm run build
MEMDOG_API_URL=http://localhost:8200 MEMDOG_API_KEY=... \
MEMDOG_PROJECT_ID=prj_... MEMDOG_PRODUCER_ID=key_... npm start
```

**The suite drops and recreates the public schema on every test**, so it refuses to run against
anything but a database on `localhost` — an exported `DATABASE_URL` pointing at a real instance
would otherwise be exactly what it dropped. A disposable database on a remote host, such as a CI
service container, needs `I_KNOW_THIS_DATABASE_IS_DISPOSABLE=yes`.

No cloud account is needed to run the whole thing locally. The embedding engine, extractor and blob
store all have offline implementations, and they are registered models rather than mocks — their
rows carry a `model_id`, so the day a real engine is assigned the old ones are identifiable and
re-embeddable rather than quietly mixed in.

---

## The shape

```
producers ──▶ POST /api/v1/write ──▶ stored ┄▶ searchable ┄▶ enriched
                                       │
                                       └┄▶ parse → embed → enrich → entities → edges
```

**Solid is synchronous, dashed is not.** The write commits before it returns; everything after it
happens behind the request. That asymmetry is why ingest latency is a database write rather than a
model call, and why the pipeline being down delays enrichment without losing data.

Those three states are visible on every read, so *"I uploaded it and search cannot find it"* is a
state you can look at rather than a bug report.

---

## What it does

| | |
|---|---|
| **One write path** | Webhook, crawler, upload, SDK — all through `POST /api/v1/write`. A crawled record and a webhook-delivered one are indistinguishable downstream |
| **Broad ingestion** | 54 formats. Audio and video transcribed, images described. The handler is chosen from sniffed bytes, never the caller's claim about them |
| **Semantic retrieval** | Vector and lexical arms fused in one query, with asymmetric document/query embedding — a question finds the passage answering it in different words |
| **Answers with evidence** | Every claim cites a passage, the passages ship with the answer, and an unsupported question is refused rather than filled in |
| **A knowledge graph** | Entities resolved cautiously, typed edges carrying the records that assert them, plus co-mentions that need no model at all |
| **Inbound webhooks** | 9 providers, each signing a different string over a different encoding |
| **Crawlers** | Templated HTTP, feeds and bounded link traversal, with a mandatory dry run |
| **Deletion that completes** | Four blast radii, async reclamation, and a certificate re-queried from every table that could hold a trace |

---

## The four commitments

Everything above is a feature. These are the reasons to choose it.

**Retrieval reports what it excluded, and why.** Ranked results are ordinary. Returning the records
that were *considered and dropped* — below the threshold, or not searchable yet — is not.
*"Missing something I know is in there"* has several causes and they need different fixes.

**The access rule is a predicate inside the query.** Never a filter over results. Asking for ten and
hiding three is a different and worse thing than returning the right ten — and it leaks: reporting
that three were hidden discloses that they exist. The same predicate serves search, chat and graph
traversal, so there is one place for it to be wrong instead of three.

**Every derived row carries its provenance.** The model, the build that answered, and a fingerprint
of prompt + model + schema + parser + chunker. Nothing is mutated in place, so *"why does this say
something different than last week?"* has an answer, and changing a default makes everything it
produced detectably stale without anyone remembering to bump a number.

**Rows are the record of work; the queue only delivers.** A durable event log with a reconciler in
front of it. Every scale-to-zero recovery in this system falls out of that one decision.

---

## Use it from Claude

An MCP server at `/api/v1/mcp` exposes the corpus as eight tools — search, chat, add, get, list,
delete, entities, memories. Point Claude Desktop or Cursor at it with an ordinary API key:

```json
{"mcpServers": {"mem-dog": {"url": "https://<host>/api/v1/mcp",
                            "headers": {"Authorization": "Bearer <key>"}}}}
```

Each tool calls the same function its REST endpoint calls, so the credential check and the ACL
predicate are the ones that already exist — a record your key cannot fetch over HTTP is one it
cannot reach through a tool. The transport is streamable HTTP rather than the older
session-bearing SSE, because this service scales to zero and a stream and its posts would not
reliably land on the same instance.

---

## What is not built

Stated as plainly as the rest, because a README that only lists strengths is not read as confident.

- **No rating, invoicing or rollups.** Spend is metered and budgeted in cost-weighted credits, and
  credits are not currency — turning them into a bill needs a rate card, hourly rollups and
  reconciliation against provider invoices, none of which exist
- **Model spend is not attributable to a crawl run or a reprocess job.** `usage_events` has the
  column; nothing populates it, because a stored item carries no reference to the run that fetched
  it. So a dry-run's estimate still cannot be checked against what the run actually cost
- **Eight schema columns are declared and wired to nothing** — `memory_links` end to end, the
  model-proposal inputs, and an `allow_public_sharing` flag superseded by the setting that actually
  gates it. They are listed with reasons in `tests/test_wiring.py`, which fails if one is quietly
  added to that list or quietly removed from the schema
- **No OAuth connections**, so Gmail, Drive and Calendar are unreachable — and with them the crawler
  strategies that walk a folder or enumerate an object
- **No path finding between two named entities**, and graph retrieval walks one hop — only
  neighbourhoods, not the chain that connects two things
- **No point-in-time facts.** Edges have no validity interval, so *"who worked there in 2024"* is
  unanswerable. [Zep](https://www.getzep.com) does this natively and this does not
- **No users.** This is a prototype. [Mem0](https://mem0.ai) processes more API calls in a quarter
  than this has served in its life

---

## Where it sits

The agent-memory category — Mem0, Zep, Letta, Cognee, Supermemory — mostly optimises for adoption,
latency and time-to-first-memory. This optimises for whether you can prove what the system knew.

**Self-hosting is not the differentiator**, despite being the obvious thing to claim. Onyx is
MIT-licensed, air-gapped, SOC 2 Type II, and ships 40+ connectors; Khoj runs entirely on local
models. Private deployment is table stakes in this category, and
[the competitive research](docs/competition/README.md) says so at length.

The differentiator is the four commitments above, and they are unusual enough to be worth checking
rather than believing.

---

## The repository

| Path | What is in it |
|------|---------------|
| [`api/`](api/README.md) | The service. 52 modules, 97 endpoints, 54 tables across 25 migrations |
| [`ui/`](ui/README.md) | The console. Sign-in, ingestion, search, chat, entities, graph, governance |
| [`docs/`](docs/README.md) | The design, in eleven parts — requirements speak in roles, products appear only in the technology documents |
| [`docs/graph.md`](docs/graph.md) | Why the graph is not a graph database, and what it costs |
| [`TBD.md`](TBD.md) | Twelve decisions designed but not decided, ordered by how expensive each becomes if made late |
| [`CHANGELOG.md`](CHANGELOG.md) | What changed and why, one entry per commit that altered behaviour |

---

## The thing that surprised us

Nearly every defect found while building this was **silent**. The request succeeded, the response
looked right, and the result was quietly wrong: a proxy that failed *open* and promoted signed-in
users to org owner; a reconcile job that re-embedded with the old model and concluded nothing was
stale; a rate limit that consumed a retry budget so a re-embed reported success having embedded
almost nothing.

None of those had an error to notice. That is most of why this system reports its trace, its
provenance and its exclusions — not because auditors ask for it, but because it is the only way to
see the bugs that do not announce themselves.
