# Direct Uploads

## Where the current path breaks

Today uploads put content in the request body of an ordinary write. Three limits:

1. **The load balancer allows 120s** on the API path. Anything slower dies at the edge.
2. **The API is the wrong place for bytes.** It is the sole writer of record running min 2 / max
   10 replicas, and autoscaling triggers on request rate — a six-minute upload counts the same as
   a 20 ms write, so uploads starve the pool without triggering a scale-up.
3. **No resumability.** A dropped connection at 90% means starting over.

## Bytes must not touch the API

```
1. POST /api/v1/uploads          → check quota, issue presigned URL + upload_id
2. PUT  <presigned URL>          → client streams DIRECTLY to object store
3. POST /api/v1/write            → completion is an ordinary write carrying `Stored`
                                   content; the API validates and sniffs MIME
```

The API handles two small JSON calls; the object store handles the bytes. Resumable and multipart
come from the object store rather than being built.

**An upload is just another producer of `Stored(...)`** — the same `ContentRef` a fetch worker
emits, so no new pipeline path is needed. A bucket notification on step 2 gives bulk file-drop
ingestion for free.

## Controls

| Risk | Control |
|------|---------|
| Leaked presigned URL | Scope to one object path encoding `{org}/{project}/{user}/{id}`; short TTL; content-length cap; method-restricted |
| Quota evasion | Enforce **before issuing** the URL — an upload cannot be stopped mid-flight |
| Orphans | URLs issued but never completed strand objects; lifecycle rules reclaim them |
| Archive bombs | Cap expansion ratio, entry count and depth before recursive fan-out turns a 2 GB zip into 50k jobs |
| Malware | Scanning becomes required once *sharing* exists — one member's upload is another's download |
| MIME spoofing | Sniff server-side at completion; treat the declared type as a hint only |

## Two shapes that are not uploads

- **URL import** is a W2 fetch job
- **"Ingest my S3 bucket"** is a [crawler](crawlers.md) with the `tree` strategy — those adapters
  already exist

## Open question

**Default ACL for a file dropped into a team project.** Private-by-default is consistent with
everything else, but users dragging a file into a *team* space often expect team visibility. Very
hard to change once habits form.
