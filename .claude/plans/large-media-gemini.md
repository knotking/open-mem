# Large unstructured media: the Gemini Files API as the fallback above the inline ceiling

**One sentence.** When an org turns it on, media too large to send inline — video,
audio, and documents — is uploaded to the Gemini Files API and interpreted from
there instead of being refused, and the text that comes back joins the ordinary
RAG path while deliberately building no knowledge graph.

---

## 1. What the code already has

This is a seam being filled, not a system being added. `multimodal.py` already
names the exact gap:

```python
MAX_INLINE_BYTES = 18 * 1024 * 1024

class MediaTooLarge(Exception):
    """Beyond the inline ceiling. Terminal until resumable upload exists."""
```

and `workers.py:962` records it and stops:

```python
except MediaTooLarge as exc:
    await self._record(data_id, "needs_model", {"reason": str(exc)})
    return None
```

`tests/test_media.py::test_a_file_too_large_to_send_says_so_and_is_not_retried`
pins that behaviour today. So the shape of the change is: **`MediaTooLarge`
becomes a route rather than a refusal, when and only when the org has opted in.**

Everything downstream already works and must not be touched:

- `Interpreted` → `content_text`/`extracted_text` → `indexable_text`
- `EmbedWorker` chunks that text and embeds it → `chunks` + `embeddings`
- `retrieval.py` searches those chunks

**That is the RAG requirement, already satisfied by construction.** If the large
path returns the same `Interpreted` dataclass, nothing downstream can tell how
the bytes reached the model, and the record is retrievable like any other. The
job of the tests is to prove that claim rather than assume it.

## 2. One correction worth making before we build

The request names three things — "gemini video api", "audio gemini api",
"document gemini api". **There are not three APIs.** Gemini has one
`generateContent` endpoint that already takes all three modalities, which is
what `GeminiMultimodal` calls today. What changes above the inline ceiling is
only *how the bytes get to it*:

| | today (inline) | above the ceiling |
|---|---|---|
| transport | `inline_data` base64 in the request body | **Files API**: resumable upload, then a `file_data.file_uri` part |
| bound | ~18–20 MB of request | ~2 GB per file |

So this is **one upload mechanism plus three modality policies**, not three
integrations. That matters for effort (much smaller) and for correctness (one
state machine to get right, not three).

Note `youtube.py:184` already sends a `file_data.file_uri` part — the request
shape is proven in this codebase; what is new is producing a `file_uri` of our
own by uploading.

## 3. The distinction that decides whether this works

**There are two different ceilings and conflating them would reproduce the bug
we are fixing, one level up.**

1. **The transport ceiling** — ~18 MB inline. This is what the Files API lifts,
   to ~2 GB. This is the fallback the request describes.
2. **The model ceiling** — how much *content* the model can actually reason
   over: video duration, audio duration, PDF page count. The Files API does not
   lift this at all.

A 5-hour 4K video is under 2 GB and still beyond what the model will accept.
If we only implement (1), such an item uploads successfully, then fails at
`generateContent` with a provider error, having spent the upload. Worse, a
*partially* accepted input would return a confident transcript of the first
hour with nothing saying the rest was dropped — the exact "silent truncation"
failure just fixed in the crawlers this week.

**So the plan carries a second bound with an honest refusal**, and the refusal
names which ceiling was hit. "Too big to send" and "too long to understand" are
different sentences and must not share one rendering.

Provider limits to **confirm against the live API before shipping** (these move,
and a stale constant here is a silent truncation):

- Files API: ~2 GB per file, ~20 GB per project, **files deleted after 48 hours**
- Video: on the order of one hour at default resolution
- Audio: on the order of several hours
- PDF: on the order of 1000 pages

## 4. Decisions and risks

### 4.1 A file URI is not durable — 48 hours, then gone

An uploaded file expires. It is therefore **not** something to store on the row
as though it were a permanent reference. `reprocess.py` re-runs interpretation
over old items; if it found a stale `file_uri` it would fail in a way that looks
like a provider outage.

**Decision:** the `file_uri` lives only for the duration of one interpretation
call and is deleted immediately afterwards. The durable artifact is the bytes in
our own blob store, which we already have. Reprocessing re-uploads.

*Cost of this:* re-interpreting the same 1 GB video uploads it twice. Accepted —
the alternative is a cache with an expiry we do not control.

### 4.2 An uploaded file is not immediately usable

The Files API returns a file in `PROCESSING` state; video in particular takes
time to become `ACTIVE`. Calling `generateContent` before then fails. This needs
a poll with a bound, and a bound that is hit must be reported as such rather
than as a generic failure.

### 4.3 Where the "no knowledge graph" rule lives

The graph is built in `EnrichWorker` (`workers.py:588-611`): the extractor's
envelope yields `entities` → `resolve_mentions`, and `relations` →
`record_edges`, in one transaction with the artifact.

Three ways to skip it:

| | how | verdict |
|---|---|---|
| A | Skip enrichment entirely for these items | **No.** Loses the summary too, and the summary is useful. |
| B | Run enrichment, skip `resolve_mentions`/`record_edges` | **Yes.** Narrow, reversible, leaves the artifact intact. |
| C | New per-item column | Unnecessary — `data_items.parse_detail` is already the jsonb the parse worker writes its account into. |

**Decision: B, keyed off a marker the parse worker writes into `parse_detail`**
(e.g. `{"via": "files_api", "graph": false}`), read by `EnrichWorker` before the
mention/edge step.

Rationale for the rule itself, worth writing into the code: a three-hour
transcript produces a very large, low-precision entity set at high cost, and a
graph built from speech-to-text noise pollutes retrieval for every other record
in the project. Skipping it is the right default — **and it must be a default,
not a hard rule**, so a project that genuinely wants it can turn it on.

### 4.4 Cost is the main risk

This feature's whole purpose is to let much larger inputs through, and media is
already the most expensive call the platform makes. A single 2 GB video is a
very large number of tokens.

- `quota.check_budget` already gates media and raises `BudgetExhausted`, which
  the queue defers. The large path **must** go through the same gate, before the
  upload, not after.
- `usage.meter` / `usage.observe` already record spend per item. Same.
- The console must show the cost beside the switch that causes it, per the
  `console-ui` standard.

### 4.5 What this does not do

Streaming/chunked interpretation of media beyond the *model* ceiling — splitting
a five-hour video into segments and stitching transcripts — is a genuinely
larger feature (segment boundaries, speaker continuity, ordering). Out of scope
here; the honest refusal in §3 is the boundary.

## 5. Implementation

### [x] Step 1 — `api/src/open_mem/gemini_files.py` (new)

The Files API client, and nothing else.

- `upload(payload, mime, display_name) -> FileHandle` — resumable protocol:
  a start request that returns an upload URL, then the bytes.
- `await_active(handle, timeout)` — poll `state` until `ACTIVE`; raise a named
  error on `FAILED` and on timeout.
- `delete(handle)` — always called, in a `finally`.
- No prompt logic, no modality logic. Those stay in `multimodal.py`.

### [x] Step 2 — `api/src/open_mem/multimodal.py`

- Add module-level `MODEL_LIMITS` naming the *model* ceiling per modality, with
  the source of each number in a comment.
- `GeminiMultimodal.__init__` takes `large_media: bool` and a `files` client.
- In `interpret()`, replace the unconditional `MediaTooLarge` raise:
  - over the inline ceiling and `large_media` off → `MediaTooLarge` as today
    (behaviour preserved for every existing deployment);
  - over the inline ceiling and `large_media` on → `_interpret_large()`;
  - over the **model** ceiling → new `MediaBeyondModel`, always, naming the
    limit and the modality.
- `_interpret_large()` uploads, awaits ACTIVE, calls `generateContent` with a
  `file_data` part and the **same `PROMPTS[modality]`**, deletes the file, and
  returns the **same `Interpreted`** with `structure["via"] = "files_api"`.
- `build_multimodal(settings)` passes the new settings through.

### [x] Step 3 — `api/src/open_mem/config.py`

- `large_media: bool` (env `LARGE_MEDIA`, default `false`)
- `max_large_media_bytes: int` (env, default ~2 GB) — the transport bound
- Keep `max_media_bytes` meaning what it means today.

### [x] Step 4 — `api/src/open_mem/settings_store.py`

Two `Definition` entries, scopes `("platform", "org", "project")`, `lockable`:

- `large_media` (bool, default `False`) — "Send media too large to inline
  through the provider's file upload instead of refusing it. Lifts the ~18 MB
  ceiling to ~2 GB. The most expensive thing the platform can be asked to do."
- `large_media_graph` (bool, default `False`) — "Build entities and edges from
  large-media transcripts. Off by default: a multi-hour transcript yields a
  large, low-precision entity set at high cost."

**No console work is needed for these.** `SettingsSection` renders any setting
generically from `/settings/effective` by `kind`, which is why the ability is
already there. The `description` field is the whole UI, so it has to be written
for a reader, not for the schema.

### [x] Step 5 — `api/src/open_mem/workers.py`

In `_interpret()`:

- Resolve `large_media` for the org through `settings_store.resolve`, exactly as
  `media_interpretation` is resolved today, and pass it to the engine for this
  call. The org setting governs; the env var is the platform default.
- On success via the large path, write the marker into `parse_detail`.
- Add `except MediaBeyondModel` → `_record(..., "needs_model", reason=...)`,
  terminal and not retried, naming the modality and the limit.
- `check_budget` before the upload.

In `EnrichWorker`: read the marker; when set and `large_media_graph` is off,
skip `resolve_mentions` and `record_edges` and record on the artifact that the
graph was skipped and why. **The artifact and the summary are still written**,
and the item still reaches `enriched` — otherwise the staircase would report it
as stuck.

### [x] Step 6 — `api/tools/fake_gemini_files.py` (new)

A simulator, following the precedent of `tools/fake_sources.py` and
`tools/fake_salesforce.py` — those exist because reading a template proves
nothing, and the same is true here.

It speaks:
- the resumable upload handshake (start → upload URL → bytes → file resource)
- the `PROCESSING → ACTIVE` transition, **after N polls**, so the wait is
  exercised rather than assumed
- `FAILED`, so the failure branch is exercised
- `generateContent` accepting a `file_data.file_uri` part, refusing an unknown
  or deleted URI (this is what catches a delete-before-use ordering bug)
- `DELETE`, and a record of whether it was called

## 6. Tests  — [x] written and passing

### Unit — `api/tests/test_large_media.py` (new)

1. Under the inline ceiling → inline path, Files API never touched.
2. Over it with `large_media` off → `MediaTooLarge`, exactly as today.
3. Over it with `large_media` on → uploaded, awaited, interpreted, **deleted**.
4. The file is deleted even when `generateContent` fails.
5. `PROCESSING` is waited out; a file stuck processing raises a named timeout.
6. `FAILED` raises rather than hanging.
7. Beyond the *model* ceiling → `MediaBeyondModel` naming the modality and the
   limit, **and nothing is uploaded** — no spend on an input that cannot work.
8. The prompt sent on the large path is the same `PROMPTS[modality]` as inline,
   so a transcript does not change character with the size of its input.

### Pipeline — `api/tests/test_media.py`

9. Amend `test_a_file_too_large_to_send_says_so_and_is_not_retried` to assert it
   still holds **with the setting off**, and add its mirror with the setting on.

### End-to-end — `api/tests/test_e2e_large_media.py` (new)

The test the request actually asks for. Against the fake Files API, through the
real pipeline, from bytes to an answer:

10. Write a video over the inline ceiling with `large_media` on for the org →
    drain the queue → assert:
    - the item reaches `enriched`, not `needs_model`
    - `parse_detail` records it went via the files API
    - **`chunks` and `embeddings` rows exist** for it
    - `POST /api/v1/retrieve` for a phrase from the transcript **returns it**
      — this is the "it should be in our RAG system" requirement, proven
    - **`entity_mentions` and `graph_edges` are empty for that data_id** —
      the "we do not build knowledge graphs" requirement, proven
    - the artifact says the graph was skipped and why
11. The same, with `large_media_graph` on → entities and edges **do** appear.
    A default that cannot be turned off is not a default.
12. Audio and PDF as well as video — one parametrised case each, since the three
    modalities are the three things the request names.
13. Budget exhausted → deferred, not dead-lettered, and **nothing uploaded**.

### Not covered by tests, and say so

No test here proves the real provider accepts a 2 GB file or that the duration
limits in `MODEL_LIMITS` are right. A simulator is not a tenant. Those numbers
need one manual run against the live API before this is trusted, and
`MODEL_LIMITS` should carry a comment saying when it was last checked.

## 7. Documentation  — [ ] release phase

- `docs/ingestion/` — a section on the two ceilings and what happens at each.
- `.claude/skills/deploy-gcp/SKILL.md` — `LARGE_MEDIA` is a new env var, and the
  skill is the record of what a deployment needs.
- `CHANGELOG.md` — via the `changelog` skill after the commit. New settings and
  a new env var are exactly the lines someone deploying needs.

## 8. Open questions

1. **"When we default, we do not build knowledge graphs"** — I have read this as
   *"on the large-media path, the default is no graph."* The other reading is
   *"whenever we fall back to any default model, no graph"*, which is a much
   wider change. Plan assumes the first.
2. **Documents.** PDFs with a text layer are parsed without a model today and
   never reach `_interpret` at all; only scanned ones fall through to OCR. Is a
   1000-page scanned PDF actually in scope, or is the document case mainly about
   PDFs that already extract fine?
3. **The 2 GB transport bound** is the provider's. Should there be a lower
   deployment default — a first 2 GB video is a memorable bill — with the full
   ceiling available to those who raise it deliberately?
