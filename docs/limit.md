# How much data can be put in

Every ceiling here is deliberate, and nearly every one of them announces itself
when it applies — the failure mode this document exists to prevent is a limit
discovered at the far end of a 500 MB transfer, or worse, a document that was
accepted, stored, and silently only half-read. There is exactly one ceiling that
still does that, and §6.1 names it.

Two rules hold across the whole surface:

**Nothing is rejected for being the wrong kind of thing.** A format with no
parser is stored, versioned and findable by filename and context; it just is not
content-searchable. See [formats](ingestion/formats.md). Limits below are about
*size*, never about type.

**Being over a processing ceiling is not being refused.** Past the admission
caps (§1–§4) a request gets a 413 and nothing is stored. Past the *processing*
ceilings (§5) the bytes are stored in full and durable — what is bounded is how
much gets indexed and understood, and the record says so in a warning (with the
one exception in §6.1).

---

## 1. Inline write — `POST /api/v1/write`

The default path. Content travels in the JSON body as `text` or base64 `bytes`.

| Limit | Default | Setting | On breach |
|---|---|---|---|
| Items per request | 500 | `MAX_ITEMS_PER_WRITE` | `413` — `item count exceeds 500` |
| Total payload | 32 MiB | `MAX_PAYLOAD_BYTES` | `413` — `payload exceeds the configured maximum` |
| Enrichment backlog | 10,000 queued | `MAX_QUEUE_DEPTH` | `429` with `Retry-After: 30` |

Enforced together in `_admit` (`api/src/memdog/write.py:112`), before anything is
stored, and after the credential and producer checks.

**How the payload is measured** (`api/src/memdog/write.py:100`), because the
number is not the request size:

- `Inline.text` — UTF-8 byte length.
- `Inline.bytes_b64` — the *decoded* size, estimated as `len(b64) * 3 // 4`. So
  32 MiB of admitted content is ~43 MiB on the wire. A client sizing against the
  transport rather than the content will be refused ~25% early.
- `Stored` — the declared `size`, or 0 if omitted. A `Stored` ref with no size
  costs nothing against the cap; the bytes were already admitted by the upload
  path, which has its own ceiling.
- `Pending` — 0. Nothing has been fetched yet; §3 bounds it.

The item count and the byte total are independent. 500 small items and one 32 MiB
item are both at a ceiling.

## 2. Upload sessions — `POST /api/v1/uploads`

The path for anything big. `POST /uploads` grants a capability to put bytes at
one key; the completion is an ordinary write carrying a `Stored` ref, so uploads
reuse the same admission path rather than adding a second one.

| Limit | Default | Setting |
|---|---|---|
| Bytes per upload | 512 MiB | `MAX_UPLOAD_BYTES` |
| Session lifetime | 1 hour | `uploads.DEFAULT_TTL_SECONDS` |

Higher than the inline cap on purpose: uploads skip base64 inflation and never
put bytes through the API's request body.

Checked twice. At session creation, against the *declared* `size` if one is
given (`api/src/memdog/uploads.py:69`) — rejecting before any bytes move is the
whole point of declaring a size. And again at `PUT .../bytes` against what
actually arrived (`api/src/memdog/app.py:1891`). If a size was declared it must
match exactly; a mismatch is a `400`, not a truncation.

## 3. Fetched content — URLs, connectors, YouTube

A write carrying `Pending` is materialised later by the fetch worker, which
downloads under the **same 512 MiB `MAX_UPLOAD_BYTES` ceiling**
(`api/src/memdog/fetching.py:378`). A pasted URL, a Drive file, a Slack export:
all one path.

The cap is enforced against bytes that actually arrive, streamed
(`fetching.py:148`), not against `Content-Length` — that header is the sender's
claim, not a fact. It is still checked first (`fetching.py:142`) so an honest
oversized response is refused without downloading it.

Also bounded: `MAX_REDIRECTS = 3`.

## 4. Crawlers

A crawl is the one input path that can enumerate its own work, so its limits are
per-run configuration rather than deployment settings (`api/src/memdog/crawlers.py:60`):

| Limit | Default | Range |
|---|---|---|
| `max_items` | 1,000 | 1 – 1,000,000 |
| `max_depth` | 2 | 0 – 10 |
| `max_bytes_per_item` | 5 MB | ≥ 1024 |
| `rate_per_sec` | 2.0 | 0 – 50 |
| `wall_clock_seconds` | 1,800 | 10 – 86,400 |

`max_bytes_per_item` is *much* lower than the fetch ceiling in §3, and
deliberately: a `traverse` crawler with a loose pattern will ingest the public
internet on the customer's inference budget. Items and wall clock are checked in
one `Budget` object (`crawlers.py:324`) rather than by each strategy, because a
strategy that checks limits itself is a strategy that eventually forgets one.

## 5. What happens after admission

These do not reject anything. The bytes are stored whole; these bound what is
read, indexed and sent to a model — and each one records that it applied.

| Ceiling | Default | Setting | Effect |
|---|---|---|---|
| Indexed text per document | 2,000,000 chars | `MAX_TEXT_CHARS` | Truncated with a warning; the tail is stored but not indexed |
| Spreadsheet rows | 20,000 | — | `stopped at 20000 rows` warning |
| Archive members | 200 | — | Members past 200 are not expanded |
| Extraction window | 40,000 chars | `EXTRACT_WINDOW_CHARS` | One model call per window |
| Extraction windows per record | 24 | `MAX_EXTRACT_WINDOWS` | ~960,000 chars understood per record; skipped windows are reported, not dropped silently |
| Media interpretation | 18 MiB | — (hardcoded, see §7) | `MediaTooLarge` — the item stores, uninterpreted |
| Chunk size / overlap | 1,200 / 150 chars | `CHUNK_CHARS`, `CHUNK_OVERLAP` | Embedding granularity |

Two of these interact and the interaction is the one worth knowing:
`MAX_TEXT_CHARS` (2M) is larger than what extraction reads (24 × 40k ≈ 960k). A
1.5M-character book is fully **searchable** and its graph is built from the first
~two thirds. The envelope carries `read` and `total` window counts so the gap is
visible rather than inferred.

The effective extraction window is the smaller of `EXTRACT_WINDOW_CHARS` and the
engine's own declared limit — Gemini 200,000, Ollama 100,000
(`api/src/memdog/extraction.py:510`) — so in practice the 40k window governs. It
is small on purpose: on a wall of text the constraint is the model's *behaviour*,
not its context window.

Media interpretation is additionally **off by default**
(`MEDIA_INTERPRETATION=false`). Off means media still stores, with the reason
recorded. Nothing is rejected either way.

## 6. By kind of input

§1–§5 are the same for every byte. What differs by *kind* is which of those
ceilings actually binds, and it is rarely the one people expect. Two rules
explain most of the table below:

- Anything a parser handles is bounded by **characters** (`MAX_TEXT_CHARS`).
- Anything needing a model is bounded twice: by **18 MiB going in**, and by
  **8,192 tokens coming out**. The second is the one that surprises people.

| Kind | The ceiling that actually binds | Over it |
|---|---|---|
| Text, code, HTML, JSON | 2M characters indexed | Truncated, warned |
| PDF (digital) | 2M characters; no page limit | Truncated, warned |
| PDF (scanned) | 8,192 output tokens ≈ 25–30 dense pages | Silently short — see below |
| Spreadsheets | 20,000 rows | Warned |
| Presentations, Word | 2M characters | Truncated, warned |
| Email (`.eml`) | 2M characters; attachments not read | Named, not parsed |
| Mailbox (`.mbox`) | 50 messages | Warned, count reported |
| Archives | 200 members | Members past 200 not expanded |
| Images | 18 MiB | `MediaTooLarge` — stored, uninterpreted |
| Audio | 18 MiB (~20–30 min at 96 kbps) **and** 8,192 output tokens | Stored, or transcript ends early |
| Uploaded video | 18 MiB — under a minute of 720p | Almost always stored, uninterpreted |
| YouTube URL | No byte ceiling; 8,192 output tokens, 600 s | An account, never a transcript |

### Documents

The well-served case. A parser runs, no model is involved, and the only ceiling
is `MAX_TEXT_CHARS`.

- **PDF** — every page's text layer, no page cap. An encrypted PDF is
  `ParseFailed(code="encrypted")` and terminal; an empty password is tried first
  because that is common and legitimately openable.
- **PDF, scanned** — detected rather than assumed: under
  `SCANNED_PDF_CHARS_PER_PAGE` (40) characters per page it is routed to OCR
  instead of returned as a near-empty success. That routing puts it under the
  media ceilings, not the document ones. **This is the sharpest per-kind limit
  in the system**: 8,192 output tokens is roughly 25–30 pages of dense text, so
  a scanned 200-page report yields roughly its first eighth and nothing marks
  the rest as missing (§6.1).
- **Word / PowerPoint / OpenDocument** — full text. `docx` keeps the first 50
  headings as structure; the *text* is uncapped below `MAX_TEXT_CHARS`.
  Password-protected files fail closed at `_zip_guard`.
- **Spreadsheets** — 20,000 rows, counted *across* sheets for `xlsx`, and the
  warning says so.
- **Email** — headers plus body. Attachments are listed by filename and
  deliberately not read inline: they are separate items with their own limits,
  and a parser must not speculate about contents it cannot see. An `.mbox`
  indexes its first 50 messages and reports the true count, because one item
  cannot honestly stand in for fifty thousand.
- **Macro-enabled Office** (`.docm .xlsm .pptm .dotm .xlam`) — stored, never
  expanded or executed. Not a size limit; a policy one.
- **Anything with no handler** — stored, versioned, findable by filename and
  context. Never rejected.

### Images

No parser exists and none is wanted: `parse()` sees the `image/*` family and
raises `NeedsModel("vision")` immediately.

- **Interpretation is off by default.** With `MEDIA_INTERPRETATION=false` an
  image stores with the reason recorded on the row and is findable by filename,
  producer and context — just not by what is in it.
- **On, the ceiling is 18 MiB** — comfortable for photographs and screenshots.
  Over it is `MediaTooLarge`, which is terminal and not retried.
- **No pixel or dimension limit** is imposed here; the provider's own apply.
- The 8,192-token output cap is not a practical constraint for a caption, with
  one exception: an image that is *primarily a document* is transcribed in full
  by the prompt, and a dense page can reach it.
- **HEIC is a known gap** — the iPhone default, and likely the highest-volume
  image format, needs `libheif`, which is absent from most base images. It
  stores as Tier C. See [formats](ingestion/formats.md).

### Audio

Same route as images — `NeedsModel("transcription")` — and the same 18 MiB
ceiling, which here is genuinely tight: roughly 20–30 minutes of 96 kbps MP3,
far less of anything lossless. A one-hour meeting recording will usually be
refused for interpretation and stored.

Two further bounds apply *under* the size ceiling:

- **8,192 output tokens** — about 6,000 words, or 40–50 minutes of speech at a
  normal rate. A recording small enough to send can still outrun its transcript.
- **600-second request timeout** on the interpretation call.

`TRANSCRIBE_MODEL` selects a different model for audio only. It deliberately does
not apply to video: a transcription model is audio-only by construction, and
pointing video at it made every recording fail with "Image input modality is not
enabled for this model" while images worked fine.

### Video

**Uploaded video is effectively not interpreted.** The 18 MiB inline ceiling is
under a minute of 720p, so all but the shortest clips are `MediaTooLarge` and
store as Tier C. This is a known boundary, named rather than worked around:
lifting it means resumable upload to the provider, which belongs with the uploads
slice, and downloading-and-transcoding locally would need `ffmpeg` in the runtime
image, which `pyproject.toml` deliberately rules out.

**A YouTube URL is the path that works, and it has no byte ceiling at all.** The
video is never downloaded; the URL is handed to the model as a `file_data` part
and the model watches it. What bounds it instead:

- **8,192 output tokens** and a **600-second** read timeout.
- **It is an account, not a transcript, by design.** Asked for a verbatim
  transcript the model stops with `finishReason: RECITATION` and returns nothing.
  That is the correct outcome rather than an obstacle — storing a whole video as
  text is reproducing it — so the prompt asks for a structured section-by-section
  account instead, and that is what lands in the record.
- Cost, not a limit but worth stating next to one: roughly a hundred thousand
  input tokens for twenty minutes of video, which is the most expensive single
  call the platform makes.

### 6.1 The one ceiling that does not announce itself

Every ceiling in §5 records that it applied — a `truncated` flag, a warning on
the row, `read`/`total` window counts. The **8,192-token output cap on media
interpretation does not.** `multimodal.py` reads the response text and never
inspects `finishReason`, so a transcript or OCR pass cut off at `MAX_TOKENS`
lands looking like a complete one.

It is *visible* if you know to look: `structure.output_tokens` is recorded on the
row, and a value at or near 8,192 means the model ran out of room rather than out
of content. Long audio and scanned PDFs are where this bites.

## 7. Console

The browser capture surface caps at **18 MiB** (`ui/components/Capture.tsx:18`),
checked at selection time rather than at submit, so a 40 MB recording is refused
with a number in front of the person who made it instead of failing deeper in the
stack. This is a client-side ceiling on the *inline* path only — it is well below
§1 and unrelated to §2.

## 8. Where the code and the configuration disagree

Worth knowing before someone sets an env var and expects an effect:

- **`MAX_MEDIA_BYTES` does nothing.** `Settings.max_media_bytes`
  (`api/src/memdog/config.py:73`) is defined and never read. The real media
  ceiling is `MAX_INLINE_BYTES = 18 * 1024 * 1024`, hardcoded at
  `api/src/memdog/multimodal.py:33`, because the limit belongs to the provider's
  inline-payload API rather than to this deployment. Changing the ceiling today
  means editing that constant.
- **`MAX_EXTRACT_WINDOWS` is 24, not 12.** `CHANGELOG.md:196` and the comment at
  `extraction.py:110` both still say 12 and quote the character coverage for 12.
- **`MAX_TEXT_CHARS` is 2M, not 4M.** `docs/ingestion/large-documents.md:46` says
  4M in its table; the same file states 2,000,000 correctly at line 164.

## 9. A different axis: quota

Everything above bounds *volume*. Separately, spend is bounded in credits — a
per-key in-process burst bucket and a durable per-scope daily budget
(`api/src/memdog/quota.py`), where one credit is roughly one plain vector search.
A write that is comfortably inside every limit here can still be refused because
the enrichment it would trigger has no budget left. The public demo surface has
its own hard caps: 20 questions per IP per hour, 500 per day across everyone.

## 10. Raising a limit

- **§1–§4 are per-deployment env vars** — set and restart, no data migration.
  Raising `MAX_PAYLOAD_BYTES` past ~32 MiB should be checked against the platform
  request-body limit in front of the API, which will refuse first and with a
  worse error.
- **`MAX_TEXT_CHARS` costs embedding calls in proportion** — roughly one per
  hundred chunks. Raising it re-indexes nothing by itself: existing records keep
  the text they were parsed with until reprocessed, and since the bytes were never
  discarded, reprocessing needs no re-upload.
- **`MAX_EXTRACT_WINDOWS` costs one model call per window**, which is the
  expensive kind. This is the setting that turns "add data" into a bill.
- **Genuinely large inputs** — a gigabyte document, a two-hour recording — are
  not a bigger version of this problem, and raising ceilings is the wrong answer.
  See [things too big to hold](ingestion/large-documents.md).
