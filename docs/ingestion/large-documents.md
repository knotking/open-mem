# Documents too big to hold

A gigabyte document is not a bigger version of the current problem. Every stage
of the write path assumes the thing fits in memory once, and at that size four
separate assumptions break at the same time. This is what would have to change,
and — more usefully — what already exists.

The short version: **the transport is already designed, and the processing is
not.** Bytes have a path that never touches the API. Text does not.

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
