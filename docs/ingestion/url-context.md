# Reading a page the fetcher cannot get

A URL that answers 403, sits behind a bot wall, or arrives as a shell that fills
itself in with JavaScript lands as a stored record with **no text**. It is
versioned, findable by filename and context, and content-searchable by nothing.

Gemini's URL Context tool fetches the page itself, from Google's network rather
than this deployment's, so those pages become readable without getting past
whatever stopped us.

---

## It is a fallback, and the order is the design

An HTTP GET returns **the bytes somebody published**: stored, versioned,
re-parseable, and a claim made about them can be checked against them later. URL
Context returns a **model's reading of a page** and no bytes at all.

Preferring it would trade an artifact for an opinion about an artifact, which is
the wrong direction for a record store. So:

```
GET the page ──success──> bytes stored, parsed, enriched      (unchanged)
      │
    failure  (403, bot wall, JS-only shell)
      │
      ├─ URL_CONTEXT off ──> record stored with no text        (prior behaviour)
      │
      └─ on ──> Gemini fetches it
                  ├─ retrieval SUCCESS ──> an account, stored as text
                  └─ ERROR / no metadata ─> refused; the *original* fetch
                                            error is what propagates
```

**Only for a plain web URL.** A connector download failing is a credential or
permission problem, and handing that URL to a third party to fetch would ask
them to retrieve something private — which they cannot do and should not be
asked to.

**When the reading also fails, the original error wins.** "403 from the site" is
the fact somebody needs; replacing it with "the model could not read it either"
hides the cause behind the fallback.

## The retrieval status is the whole safety property

**The model answers whether or not it reached the page.**

This is not a theoretical risk. The first probe of this API returned a
confident, accurate-sounding paragraph about `example.com` — *alongside*
`URL_RETRIEVAL_STATUS_ERROR` for that exact URL:

```jsonc
{"urlContextMetadata": {"urlMetadata": [
   {"retrievedUrl": "https://example.com", "urlRetrievalStatus": "URL_RETRIEVAL_STATUS_ERROR"},
   {"retrievedUrl": "https://httpbin.org/html", "urlRetrievalStatus": "URL_RETRIEVAL_STATUS_SUCCESS"}]}}
```

Nothing in the prose says which happened. So the text is never evidence of
retrieval — only the metadata is:

- A non-success status **refuses** the account rather than storing a
  recollection as a retrieval.
- A response carrying **no metadata at all** is also refused. Silence is not
  success, and treating an absent field as a pass skips the one check that
  matters in exactly the case it was written for.

What is stored says so in its own first lines:

```
https://example.com/article

[Account of this page produced by a model with URL Context. The page's own
bytes were not retrievable by this deployment, so this is a reading of the
page rather than the page itself.]
```

A record that reads like the page while being a summary of it is the one
outcome worth avoiding here. Everything downstream — classification, chunking,
embedding, enrichment, the graph — runs unchanged.

## Switching it on

```bash
URL_CONTEXT=true            # off by default
URL_CONTEXT_MODEL=          # empty means multimodal_model
```

**Off by default on purpose.** A page that fetches normally costs an HTTP GET;
this costs a model call whose input includes the whole page. It earns that only
where the alternative is a record with no text.

It rides the existing `GEMINI_API_KEY`. With the key unset it logs a warning and
stays off rather than failing later at fetch time.

## Using it

There is nothing new to call. Ingest a URL exactly as before:

```jsonc
{"external_id": "https://example.com/article",
 "content": {"kind": "pending", "provider": "url",
             "resource_id": "https://example.com/article"}}
```

The fetch worker tries the ordinary GET first, so on a normal page you will
never see this run.

## Limits and cost

| | |
|---|---|
| URLs per request | 20 |
| Content per URL | 34 MB |
| Reachability | public web only — localhost, private networks and tunnels (ngrok, pinggy) are rejected by the tool |
| Billing | the page's bytes count as input, reported separately as `toolUsePromptTokenCount` |

That last number is the one that scales with the page rather than the prompt,
and it is what makes a large page expensive. It is logged per read.

## A note on the API contract

The published documentation describes a newer request shape than
`v1beta/…:generateContent` accepts. Verified against the live API, the form is:

```jsonc
"tools": [{"url_context": {}}]     // empty object; the URLs come from the prompt text
```

and the response key is `urlContextMetadata` — camelCase, unlike the snake_case
in the docs. Both are asserted in `tests/test_urlcontext.py` so a change is a
failing test rather than a silent fallback.
