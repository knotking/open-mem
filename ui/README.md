# `ui/` — the sandbox

The thin console the roadmap calls blocking for slice 1: *retrieval quality
cannot be judged from a JSON body.* Upload, the staircase, search, and the
retrieval trace. **No chat, deliberately** — the trace is what proves the spine,
and a conversational layer over a retrieval path nobody has inspected just hides
the thing worth looking at.

```bash
npm install && npm run build
MEMDOG_API_URL=http://localhost:8300 MEMDOG_API_KEY=... \
MEMDOG_PROJECT_ID=prj_... MEMDOG_PRODUCER_ID=key_... npm start

./deploy.sh [tag]      # Cloud Run
```

Live: `https://memdog-sandbox-266276359448.us-central1.run.app`

## Diagrams are rendered ahead of time, not in the browser

Every ```mermaid block in the published docs is rendered to SVG by
`npm run diagrams` and committed to `lib/docs-diagrams.ts`, keyed by its own
source text. **Run it whenever a diagram in `docs/` changes, and commit what it
writes** — `npm run verify` fails otherwise, naming the document, because a
diagram with no rendering serves as mermaid source text and that is exactly what
it looked like for as long as it was happening.

It needs Google Chrome: mermaid measures text to lay a diagram out, and in jsdom
every shape collapses. Rendering in the browser instead would make mermaid the
fourth runtime dependency of a UI that has three.

## Both credentials stay on the server

The browser talks only to `/api/proxy/*`. That route adds two headers and
neither is ever sent to the client:

- **`Authorization`** — a Google identity token minted from the metadata server,
  because the API is behind Cloud Run IAM.
- **`X-API-Key`** — the mem-dog credential, read from Secret Manager at runtime.

An API key in a browser is a key you have published, and the design says so
plainly: *never in a browser, enforced rather than documented.* There are no
`NEXT_PUBLIC_*` build args here for the same reason — anything baked into the
bundle is public.

The proxy carries an **allow-list** of paths. An open proxy in front of an
authenticated API hands the browser every endpoint the server can reach,
including ones this UI never uses.

## What the trace shows, and why each panel exists

| Panel | The question it answers |
|-------|------------------------|
| Ranked chunks with scores and which arm matched | Did retrieval find the right records — and by meaning or by words? |
| Character spans into the source | Is the chunk boundary sane, or did it split a claim from its subject? |
| Corpus counts on every answer | Was it answering over 2 enriched of 500? |
| Considered but not returned, **with the reason** | Was it excluded by score, or was it never searchable? |
| `model_id` and `generator_version` | Which configuration produced this, so the run is reproducible |

"The answer is missing something I know is in the data" is the most common
complaint, and it has several different causes with different fixes. Naming
which one applies turns an unfalsifiable impression into a diagnosis.

### ACL exclusions are absent, and cannot be added

Every other exclusion is named. ACL exclusions are not, and this is not an
oversight: reporting *"3 records were hidden from you"* discloses that they
exist, which is the thing the ACL is for. The predicate runs inside the
retrieval query, so the count does not exist to be reported.

The UI says this on the panel rather than leaving a silent gap, because a
diagnostic surface that is quietly incomplete is worse than one that states its
own boundary.

## Not built

Chat (slice 2), CSV/JSONL and folder upload with sample-first enrichment
(slice 4), and configuration A/B (slice 6). The write box takes pasted text —
the "quick look" row of the upload table, which is the one that exercises the
spine.

Sandbox projects do not yet carry a TTL, so the upload notice states what is
actually true — real ingestion under the project's real retention — rather than
naming an expiry date the system would not honour.
