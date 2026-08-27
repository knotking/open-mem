# Blob Store Layout

What actually sits in object storage, and under what key.

This was previously unspecified — [write-api.md](../ingestion/write-api.md) shows
`gs://.../upl_01JQRS/img-4410` with the bucket elided, which is fine as an illustration and
insufficient as a contract. The key layout is not a naming preference: it decides whether tenant
isolation, erasure and lifecycle rules are cheap or impossible.

---

## Only bytes live here

The split is one line: **raw binary goes to the blob store, everything else goes to the record
store.**

| In object storage | In Postgres |
|-------------------|-------------|
| Original files as fetched or uploaded | Data items and all metadata |
| Large extracted text, above a threshold | Chunks, embeddings, lexical index |
| Derived media — thumbnails, page images, transcripts | Memories, cases, ACLs, versions, audit |

If something can be queried, filtered or joined, it is not in the blob store. The blob store
answers exactly one question: *give me the bytes for this reference.*

## The key

```
{bucket}/{org_id}/{project_id}/{data_id}/{kind}/{sha256}.{ext}
```

```
memdog-raw-prod/org_01J8.../prj_01J9.../data_01JQRS.../raw/9f2c8e….pdf
                                                      /text/4a17bb….txt
                                                      /derived/c81d02….webp
```

Each segment earns its place:

| Segment | Why it is at that depth |
|---------|------------------------|
| `org_id` first | **Bucket-policy and IAM boundaries can only be expressed on a prefix.** Anything above the tenant id makes per-tenant access control impossible |
| `project_id` second | Project purge becomes a **prefix delete** rather than an enumeration |
| `data_id` third | All artifacts of one item are colocated, so a single-item delete is one prefix |
| `kind` | `raw` / `text` / `derived` — lets lifecycle rules differ by kind |
| `sha256` as the filename | Content-addressed **within the item**, so a re-fetch that yields identical bytes writes the same key instead of a duplicate |

### Content addressing stops at the tenant boundary, deliberately

The tempting optimisation is a global content-addressed store — one copy of any given byte string,
deduplicated across the whole platform. **Do not.**

> Two tenants upload the same PDF. Global deduplication stores one object. Tenant A then exercises
> an erasure right. Either you delete the object and destroy tenant B's data, or you keep it and
> the erasure did not happen. **There is no third option**, and no amount of reference counting
> makes "we deleted your data" true while the bytes are still being served to someone else.

So deduplication is scoped: identical bytes within one item collapse to one key, and across
tenants they do not. The storage saved by global dedupe is not worth an unsatisfiable erasure
request.

## Objects are immutable

Written once, never modified. A revision writes a **new** object under a new checksum; the record
store's version row points at it. This matches the rule that
[mutation is a new version, never in place](../retrieval/versioning.md), and it makes the blob
store safe to cache and cheap to replicate.

Deletion is the only operation that removes an object.

## Every object carries its owning ids as metadata

```
x-goog-meta-org-id      org_01J8...
x-goog-meta-project-id  prj_01J9...
x-goog-meta-data-id     data_01JQRS...
x-goog-meta-checksum    sha256:9f2c8e...
x-goog-meta-ingested-at 2026-08-27T09:20:00Z
```

This is redundant with the key path, and it is redundant on purpose.

**The failure mode this exists for is orphaned blobs.** A delete that removes the database row but
fails before removing the object leaves bytes with no owner — invisible to every query, and
indistinguishable from a legitimate object during a reconciliation sweep unless the object itself
says who it belonged to. Without the metadata, the only way to find orphans is to enumerate the
entire bucket and check every key against the database, which stops being feasible long before it
stops being necessary.

With it, a periodic sweep can list a prefix, resolve `data_id`s in one query, and delete what has
no row. **`storage.orphaned_objects` is the metric**, and like everything else in this system the
failure it detects is silent — an orphan costs money and holds content that should be gone, and
nothing surfaces it.

## Two buckets, not one

| Bucket | Holds | Why separate |
|--------|-------|--------------|
| `{env}-raw` | Tenant data | Tenant-scoped IAM, lifecycle rules, CMEK |
| `{env}-secrets` | Encrypted provider credentials — [`{user_id}/engines/{id}.json`](../security/auth.md) | **Completely different access policy.** No tenant principal ever reads it, and it must not inherit a data-bucket lifecycle rule that expires objects |

Putting encrypted credentials in the data bucket means one over-broad prefix grant exposes both,
and one retention rule written for data can silently expire a key. They are different kinds of
thing with different lifetimes and different readers.

## What deletion can and cannot do with prefixes

Worth stating plainly, because it is the difference between a fast operation and a slow one:

| Deletion scope | Blob strategy |
|----------------|---------------|
| One item | Prefix delete on `…/{data_id}/` |
| A project purge | Prefix delete on `…/{project_id}/` |
| Org offboarding | Prefix delete on `{org_id}/` |
| **A selector** | **Enumerate from the record store** — a selector is a query, not a prefix |
| **[An account's data](deletion.md)** | **Enumerate from the record store** — an account's items live inside projects, so there is no prefix that means "this person" |

The last two matter for [Phase 1 scope](../roadmap.md). Selector and account deletion cannot be
prefix operations, so they walk the record store and delete object by object — which is precisely
why they are **resumable runs** rather than requests, and why the object metadata has to be right.

## Encryption

Customer-managed keys via KMS, not a key held in Secret Manager. Per-object encryption is the
platform's default; the KEK belongs in a key management service where it can be rotated and its use
audited. [Secret Manager and KMS are not interchangeable](deployment-variants.md).

## Per-variant realisation

Same layout, three fillings — the paths below the bucket root are byte-identical, so a corpus moves
between variants without rewriting references:

| Variant | Root |
|---------|------|
| Local | `./data/blobs/` on the filesystem |
| GKE | GCS bucket |
| Cloud | GCS bucket |

The `STORAGE_BACKEND` abstraction is what makes this a configuration rather than a fork, and it is
why the blob store is rated **low swap cost** in [technology.md](technology.md).

## Requirements

- **FR-BLOB-1** Object keys MUST begin with the tenant identifier, so access policy can be
  expressed as a prefix.
- **FR-BLOB-2** Deduplication MUST NOT cross a tenant boundary.
- **FR-BLOB-3** Objects MUST be immutable. A revision MUST write a new object.
- **FR-BLOB-4** Every object MUST carry its owning org, project, data id and checksum as object
  metadata, so orphans are detectable without a full-bucket scan against the database.
- **FR-BLOB-5** A reconciliation sweep MUST report orphaned objects as a metric.
- **FR-BLOB-6** Encrypted credentials MUST NOT share a bucket with tenant data.
- **FR-BLOB-7** Object encryption MUST use a KMS-managed key, not a secret-store value.
- **FR-BLOB-8** Paths below the bucket root MUST be identical across deployment variants.
