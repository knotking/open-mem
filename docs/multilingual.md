# Multi-Language

> **An open translation model keeps foreign-language content local.** TranslateGemma covers 55
> languages as open weights, so multilingual ingestion does not require routing content a
> deployment considers sensitive to a frontier API — which matters most for exactly the deployments
> that are multilingual *and* regulated. See [model-catalog.md](operations/model-catalog.md).

Absent from the design until now, and **Phase 1 relevant** — because two of the decisions it forces
are made when the first row is written, and both are corpus migrations afterwards.

A system ingesting mailboxes and chat across an international organisation gets multilingual on day
one, whether or not it was designed for.

## The two Phase-1 decisions

### 1. The lexical index needs a language per row

Postgres full-text search takes a **language configuration** — it determines stemming and stop
words. Index German text as `english` and stemming is wrong, stop words are wrong, and BM25 quietly
underperforms in a way no error reveals.

So `language` is a **column, set at ingest**, before the lexical index is built. Detected
per item, overridable, and defaulting to a configured project language rather than to `english`.

Retrofitting means re-indexing the corpus.

### 2. Embedding model choice determines cross-lingual retrieval

A monolingual embedder places "invoice" and "Rechnung" in unrelated regions. A multilingual one
places them near each other, so a query in one language retrieves documents in another.

That is a product decision disguised as a model choice — and per
[versioning](retrieval/versioning.md), **changing the embedding model is a corpus-wide migration**,
not a setting. It is made in Phase 1 whether deliberately or by default.

| Approach | Cross-lingual retrieval | Cost |
|----------|------------------------|------|
| **Multilingual embedder** | Works | Usually slightly weaker monolingual quality |
| Per-language embedders | **Fails across languages** — separate vector spaces | Better per-language quality |
| Translate then embed | Works | Extra inference per item, translation loss, and the original is what you must cite |

**Recommend a multilingual embedder** unless a deployment is genuinely single-language. The
per-language option is the [vector-space trap](retrieval/versioning.md) in a new costume: separate
spaces that cannot be compared, arrived at deliberately this time.

## What else changes

| Area | Consideration |
|------|--------------|
| **Chunking** | CJK has no word spaces; sentence boundaries differ. A splitter tuned for English produces bad chunks elsewhere |
| **Extraction prompts** | Does the agent answer in the source language or a canonical one? **Canonical for structured fields, source language for quoted content** — otherwise facets are unfilterable |
| **Normalization** | Dates (`03/04` is ambiguous), numbers (decimal comma), name order, addresses |
| **Entity resolution** | The same organisation across scripts. Transliteration is a real matching problem |
| **Retrieval** | Query language may differ from corpus language — the reason the embedder choice matters |
| **Citations** | Cite the original, never a translation. The user must be able to check it |

## Detection

Deterministic first, as everywhere else: source metadata (an email declares a charset and often a
language), then a fast statistical detector, then the model only for genuinely ambiguous short
text. Store confidence alongside, and mark `unknown` rather than guessing `english` — a wrong
language label is worse than an absent one, because it silently mis-stems.

## Requirements

- **FR-LANG-1** Every item MUST carry a detected language with confidence, set at ingest, before
  lexical indexing.
- **FR-LANG-2** Language MUST default to a configured project language, never to a hardcoded one.
- **FR-LANG-3** The lexical index MUST use the item's language configuration.
- **FR-LANG-4** Undetectable language MUST be recorded as `unknown`, not guessed.
- **FR-LANG-5** Structured extraction output MUST use canonical values; quoted content MUST retain
  its source language.
- **FR-LANG-6** Citations MUST reference the original text, never a translation.