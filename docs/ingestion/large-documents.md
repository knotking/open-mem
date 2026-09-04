# Things too big to hold

A gigabyte document, or a two-hour recording, is not a bigger version of the
current problem. The write path assumes throughout that the thing fits in memory
once and is processed in one pass, and past a certain size several of those
assumptions fail together. This is what would have to change, and — more
usefully — how much of it already exists.

The short version: **the transport is largely solved and unused; the processing
is not.** Bytes already have a path that never passes through the API. What
happens to them afterwards still assumes they are small.

Two sections: documents, then media. They share a shape — divide at ingestion,
one memory holding the parts — and differ in what it costs to cut them.

---

## What already works

**Bytes never need to go through the API.** `POST /uploads` grants a capability
rather than accepting data: permission to put bytes at one key, for a bounded
time. Completion is an ordinary write carrying a `Stored` ref, so uploads reuse
the admission path every producer already goes through rather than needing a
second one. GCS swaps a real signed URL in behind the same two calls.

**A write can carry a reference instead of content.** `Pending` is a first-class
content ref, and the fetch worker materialises it before anything else runs. So
"the bytes are somewhere else, go and get them" is already a supported shape.

**Splitting one input into many records is not new.** An archive is walked and
its members parsed individually, capped at `MAX_ARCHIVE_MEMBERS`. The precedent
for one upload becoming many records exists and is exercised.

**A container for the parts exists.** A memory groups records and decides when
they expire. Nothing new has to be invented to hold the pieces of a split
document together.

---

## What breaks

| Stage | Assumption | At 1 GB |
|---|---|---|
| Console upload | inline base64 in a JSON body | ~1.33 GB request; `Capture` caps far below this |
| Parse | the whole text is a `str` in memory | the API runs on 1 GiB |
| `MAX_TEXT_CHARS` | one ceiling per document | ~1 billion characters against a 4M ceiling |
| Chunking | a list of every chunk at once | ~950,000 chunks |
| Embedding | one sequential loop, 100 per call | ~9,500 calls, hours, in one asyncio task |
| The row | one record succeeds or fails whole | one failure at hour two loses everything |

The last row is the one that matters most. Everything else is a resource limit;
that one is a design property. A single record is atomic, so a gigabyte of work
has no partial success and no way to resume.

---

## The shape that fits

**Divide at ingestion, not at read.** One upload becomes *N* part-records inside
one memory. Each part parses, embeds and is searched independently. The memory
is what makes them one document again — which is what memories are already for.

That single change addresses most of the table:

- **Memory** — a part is bounded, so nothing holds a gigabyte at once.
- **Failure** — a failed part is a failed part. The other 199 are searchable.
- **Resumption** — the reconciler already re-enqueues dropped work, per record.
- **Parallelism** — parts are independent, so embedding fans out instead of
  queueing behind itself.
- **Ceilings** — `MAX_TEXT_CHARS` becomes a per-part bound, which is what it was
  always trying to be.

**Four pieces would have to be built.**

1. **The console uses upload sessions.** `Capture` posts inline bytes today,
   which is why it caps. The endpoint it should call already exists.
2. **A streaming parse.** Handlers return a `Parsed` with the whole text. A
   large-document path needs to yield spans as it reads, so the peak is a part
   rather than a document.
3. **Splitting on a boundary that means something.** Fixed-size cuts through the
   middle of a sentence make retrieval worse, and a citation that lands mid-word
   is not checkable. Split on the structure the parser already recovers —
   headings for a document, sheets for a workbook, messages for a mailbox.
4. **Parts that know they are parts.** Ordinal, source document, and a shared
   memory, so a result can say *"chapter 9 of this book"* rather than
   *"record 412"*.

**One thing not to build.** Do not raise `MAX_TEXT_CHARS` toward a gigabyte. It
is a per-document budget guard, and a limit large enough to admit a gigabyte
admits it into a pipeline that cannot carry it — which converts a clear refusal
into an hour of silent work and a failure with no partial result.

---

## Media that will not fit

**None of this is built.** What exists today is one number and an honest refusal:
`MAX_INLINE_BYTES` is 18 MB, the console's `Capture` matches it, and anything
larger is recorded as `needs_model` with the reason on the row. There is no
segmenting, no audio extraction, no downsampling, and duration is not modelled
at all.

Media is worth treating separately from text because **it is never divided**. A
long document is already chunked — length costs time, not success. A recording
is one payload in one call, so 17 MB works and 19 MB does not, and nothing in
between degrades.

### Two walls, and only one of them needs splitting

Conflating these is what makes the problem look bigger than it is.

**The transport wall — 18 MB — does not need divide-and-conquer.** It is the
provider's *inline* limit, not a limit on what the provider can read. Sending
media by reference instead of as base64 lifts it to whatever the provider's file
service allows, which is a different order of magnitude. `POST /uploads` already
grants a signed URL and mints a storage key, so the bytes already have somewhere
to live; what is missing is handing the provider a reference to them instead of
inlining. That is the cheap change, and it is the one that unlocks most real
files — a phone video, a meeting recording, a scanned report.

**The duration wall does.** A model has a bounded context however the bytes
arrive, so a two-hour recording has to become segments whatever the transport.
This is where the part-record shape from above applies unchanged: split on time,
one part per segment, all in one memory.

Doing the second without the first would be building the expensive half to solve
the cheap problem.

### What splitting media costs that splitting text does not

**Bytes cannot be cut arbitrarily.** A container is not a stream of independent
frames: a cut has to land on a packet boundary and be re-muxed, or the segment is
undecodable. That means a media toolchain — `ffmpeg` or equivalent — and the API
image is `python:3.12-slim` with no such thing. This is a real dependency, not a
function.

**Extracting audio is usually the better trade.** When only speech matters, a
500 MB video becomes a few MB of audio — often clearing the transport wall
outright without any splitting. It needs the same toolchain, and it is a
*policy* choice rather than an automatic one: discarding the visual track is
correct for a meeting recording and wrong for a screen capture whose content is
what is on screen.

**Time is the boundary, and it needs an overlap.** Chunked text overlaps so a
sentence spanning a cut is still retrievable; segmented audio needs the same, or
a word split across a boundary is lost from both transcripts.

**A segment is not a citation.** A transcript's value is that a claim points at a
passage. A part-record must carry its offset in the original, or a citation says
"segment 4" and the reader cannot find the moment.

### Duration should bound this, not bytes

Today the only guard is size, and size is a poor proxy: a 15 MB hour-long
low-bitrate recording passes the check and then meets the provider's token limit,
which surfaces as a provider error rather than a clear refusal. Whatever is built
here should bound what the model must actually process — minutes of media —
rather than what it costs to transfer.

---

## What the ceiling does today

`MAX_TEXT_CHARS` (default 2,000,000, set per deployment) bounds indexed text.
Past it, the text is **stored and durable but not searchable** — the record is
never lost, only partly findable.

Raising it does not re-index anything by itself: the parse worker skips any row
that already has text, which is correct for an at-least-once queue and cannot
distinguish "already done" from "done under a smaller ceiling". Use the `parse`
stage of `/reprocess`, which clears the derived text and re-reads the bytes. The
bytes and their checksum are untouched, so nothing is re-uploaded.

```bash
curl -X POST "$BASE/api/v1/reprocess" -H "X-API-Key: $KEY" \
  -H 'content-type: application/json' \
  -d '{"selector": {"data_ids": ["data_…"]}, "stage": "parse"}'
```
