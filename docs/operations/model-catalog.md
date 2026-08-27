# Model Catalog

*Landscape surveyed August 2026. Model families move fast; the catalog is designed to be updated,
and the point of this document is the shape, not the specific version numbers.*

## What this adds to Model Garden

Model Garden today manages **providers**: add an API key, test connectivity, discover what models
that provider exposes. Discovery returns a flat list of model IDs — strings with no properties.

That is not enough to choose with. `qwen3.6:27b` and `gemma4:e4b` are both strings; one needs a
24GB card and one runs on a phone; one does vision and one does not. Users cannot make an informed
choice from a dropdown of identifiers, and smart routing cannot validate an assignment it knows
nothing about.

The addition is a **curated catalog of model cards** — models with declared properties — that
users browse, select, and assign. Once assigned, smart routing uses them.

## The finding that changes the tier design

The current five-tier model — small, medium, large, **multimodal**, **omni** — was designed
around a real constraint: text models were text-only, so vision and audio needed separate models
in separate tiers.

**That constraint has largely dissolved.** Gemma 4 is natively multimodal at every size (vision,
audio, tools, thinking). Qwen 3.5 spans roughly 0.8B to 122B with every size natively multimodal.
Modality is now a **capability most models have**, not a tier you route to.

Keeping `multimodal` and `omni` as sibling tiers to `small`/`medium`/`large` conflates two
independent axes:

```
            CAPACITY  ────────────────────────▶
            small        medium        large
CAPABILITY
  text        ●            ●             ●
  vision      ●            ●             ●      ← used to be one column
  audio       ●            ●             ●      ← used to be one column
  tools       ●            ●             ●
```

Routing should select on **capacity tier × required capabilities**, and the catalog must declare
capabilities so that selection can be validated. A vision task routed to a text-only model should
be a startup error, not a runtime surprise.

Migration is straightforward: keep `multimodal` and `omni` as deprecated aliases that resolve to
*(capacity tier, capability set)* pairs, so existing configs keep working.

## The model card

Each catalog entry declares:

| Field | Purpose |
|-------|---------|
| `id` | Canonical identifier, e.g. `qwen3.6:27b` |
| `family` / `version` / `variant` | Grouping and upgrade paths |
| `architecture` | `dense` or `moe` — with total and active parameters for MoE |
| **`license`** | Apache-2.0 · Gemma terms · community licenses with use restrictions. **Must be surfaced** — some restrict commercial use |
| `context_window` | 32K … 1M — governs chunking and long-document routing |
| **`capabilities`** | `text` · `vision` · `audio` · `tools` · `thinking` · `structured_output` · `embedding` |
| `hardware.min_vram_gb` | Per quantization: q4 / q8 / fp16 |
| `hardware.quantizations` | What's actually available to pull |
| `serving.providers` | Which configured engines can serve it — local, cloud, gateway |
| `serving.pricing` | Per-token cost, or free for local |
| `quality_signals` | Benchmark references, **with the date and caveat attached** |
| `recommended_for` | Suggested capacity tier and agent types |
| `status` | `recommended` · `available` · `deprecated` · `superseded_by: <id>` |
| `card_url` | Link to the upstream model card |

`status` matters more than it looks. mem-dog's defaults currently reference a model generation
that has been superseded — without a `superseded_by` field there is no mechanism to tell users
that, and defaults silently rot.

## Where the catalog comes from

Three sources, intersected:

```
  curated registry          what we ship and maintain
        ∩
  provider discovery        what your configured engines actually serve
        ∩
  hardware feasibility      what your machine can actually run
        ─────────────────────────────────────────────
        = models offered to this user
```

The curated registry follows the pattern already established by `nango_provider_meta.py`: a static
local mapping supplying metadata the upstream API does not provide. Providers tell you a model
exists; they do not tell you its VRAM requirements, its license restrictions, or whether it has
been superseded.

Models discovered from a provider but absent from the registry still appear — as
`status: available`, unvalidated, with a note that capabilities are undeclared. **Never hide a
model the user has access to**; just be honest about what is unknown.

## The catalog is data, and it is dated

Everything below is **a snapshot, verified August 2026**, and it is the fastest-rotting content in
these documents. It lives in the registry as rows, not in prose — what this section fixes is the
*shape* and the reasoning, not the values.

Every quality and price figure carries the date it was checked. A benchmark score with no date is a
claim about a model that may no longer exist.

---

## Tier 1 · Frontier — hosted API only

Not self-hostable. Selected when capability matters more than locality, and **excluded entirely by
the allow-list** on projects that cannot send data to a third party.

| Model | In / out per 1M | Context | Notes |
|-------|-----------------|---------|-------|
| **Claude Fable 5** | $10 / $50 | — | Top capability tier |
| **Claude Opus 5** | $5 / $25 | — | Strong general reasoning |
| **Claude Sonnet 5** | $2 / $10 | — | The workhorse rate |
| **Claude Haiku 4.5** | $1 / $5 | — | Cheapest first-party from a US frontier lab |
| **GPT-5.6 Sol** | $5 / $30 | — | Tiered pricing strategy |
| **Gemini 3.1 Pro** | $2 / $12 | **1M** | Long-context leader among hosted |
| **Grok 4.5** | $2 / $6 | — | Aggressive output pricing |
| **DeepSeek V4 Flash** | **$0.14 / $0.28** | — | Cheapest credible API by a wide margin |

### Price per million tokens is not comparable across providers

This is a real trap, and it sits directly under the
[token accounting](token-accounting.md) design.

> **Tokenizers differ between vendors.** The same document is a different number of tokens
> depending on who counts it — Anthropic's tokenizer change is a documented example of per-MTok
> comparisons quietly skewing.

So a catalog that ranks models by `$/MTok` is ranking them **in different units**. Two consequences:

- **Cost estimates must use the target provider's own tokenizer**, not a shared approximation. A
  budget that estimates with one tokenizer and is charged against another drifts in one direction
  and only shows up on an invoice.
- **Comparisons in the UI should be expressed per document, not per token** — "about $0.004 to
  enrich a typical email" is both comparable and meaningful, where `$2/MTok` is neither.

## Tier 2 · Open weight — self-hostable

The tier that makes air-gapped operation possible. **License is a first-class card field** because
the practical differences are large.

| Model | Params | License | Notes |
|-------|--------|---------|-------|
| **Kimi K3** | 2.8T total / 104B active | open weights | Top open benchmark scores; 1M context. Serious hardware |
| **DeepSeek V4 Pro** | large MoE | **MIT** | High-end reasoning and coding |
| **GLM-5.2** | large | **MIT** | Strongest under a plain MIT license |
| **Qwen3.6 27B** | 27B dense | Apache-2.0 | **Runs on one 24GB GPU at Q4.** The practical high-water mark for single-GPU |
| **Qwen3.6 35B-A3B** | 35B / 3B active | Apache-2.0 | Best all-round at 32GB — MoE keeps active params low |
| **Qwen3.7** | large | Apache-2.0 | Coding-focused |
| **Llama 4 Scout / Maverick** | large | community | Up to 1M context. **License restricts some commercial use — surface it** |
| **Gemma 4** · 12B / 26B / 31B | 12–31B | Gemma terms | Strong multimodal; 12B is the practical laptop fit, 26B/31B a single 4090 |
| **Gemma 4** · E2B / E4B | 2–4B | Gemma terms | Edge and low-RAM |
| **Gemma 3 270M** | 0.27B | Gemma terms | Hyper-efficient. See the classifier note below |
| **Mistral Medium 3.5** | 128B dense | — | 256K context, strong multilingual |
| **Phi-4-mini** | small | MIT | Very small footprint |

Two card fields do the work here that a benchmark score cannot:

- **`hardware.min_vram_gb` per quantization.** *"Runs at Q4 on 24GB"* is the fact that decides
  whether a user can use a model at all, and no provider API returns it.
- **`license`.** Apache-2.0 and MIT are unrestricted; Llama's community licence and Gemma's terms
  carry use restrictions that a commercial deployment must see **before** selecting, not after.

### Specialised open models solve specific problems here — several of them ones already open

General-purpose model size is the wrong axis for some of this pipeline's work. Google's Gemma
family in particular ships **task-specific open variants**, and four of them land directly on
constraints these documents already record as unresolved.

| Variant | What it is | The mem-dog problem it addresses |
|---------|-----------|----------------------------------|
| **MedGemma** / **MedGemma 1.5** | Medical text and imaging interpretation | **The HIPAA constraint** — see below. Also the one credible route to DICOM interpretation, currently out of v1 |
| **TranslateGemma** | Translation across 55 languages | [Multilingual](../multilingual.md) ingestion without routing foreign-language content to a frontier API |
| **EmbeddingGemma** | On-device embeddings | Embeddings in the **air-gapped** variant, where the Gemini embedding dependency currently breaks the story |
| **Gemma 3 270M** | 0.27B, hyper-efficient | **Classification layer 7.** A 270M model deciding a type is a rounding error next to sending the same content to a 27B one |
| **ShieldGemma 2** | Content-safety classifier | The `classification flag at ingest` that Phase 1 requires, as a deterministic step rather than a prompt |
| **FunctionGemma** | Function calling at the edge | Structured-output reliability on small hardware |
| **VaultGemma** | Differentially private LLM | Worth evaluating where enrichment output itself is a disclosure risk |
| **T5Gemma / T5Gemma 2** | Encoder–decoder | Extraction tasks where an encoder–decoder beats a decoder-only model of the same size |

Sizes and context windows for the specialised variants are not published alongside the core sizes;
the registry records what each provider actually returns rather than assuming parity with the base
family.

#### MedGemma changes the clinical story, not just the model list

[use-cases-catalog.md](../use-cases-catalog.md) records the sharpest legal constraint in this
system: a hosted deployment processing PHI needs a BAA with **every sub-processor touching it,
including the inference provider** — which is why the clinical use case is documented as
self-hosted-only unless the cloud inference choice changes.

**A capable open medical model run locally removes the sub-processor entirely.** There is no third
party to sign an agreement with, because the content never leaves the deployment. That converts the
clinical scenario from *"self-hosted only, and even then check your provider"* into *"self-hosted,
and the model is part of what you host"*.

Two things follow, and neither is a licence to relax anything:

- **The [per-project allow-list](technology.md) becomes the enforcement point that makes this
  real.** A clinical project allow-lists local engines only, and the chain **fails closed** rather
  than falling through to a cloud model. The model choice is what makes compliance *possible*; the
  allow-list is what makes it *true*.
- **Gemma terms carry use restrictions.** A medical open model is not automatically cleared for
  clinical use, and "open weights" is not "approved for diagnosis". The card surfaces the licence;
  the deployment decision remains a decision.

#### A 270M classifier is the right size for layer 7

The [classification cascade](../ingestion/workers.md) resolves roughly 80% of traffic
deterministically and sends the remainder to a model. That remainder is currently described as "a
small-tier model", which in practice means whatever the deployment configured — often a 27B
general model deciding whether something is a CSV.

**Gemma 3 270M is roughly a hundredth the size for a task that is classification, not reasoning.**
Reserving the large model for extraction and giving classification a purpose-sized one is the
cheapest quality-neutral saving available in the pipeline, and it makes the "80% never reach an
LLM" figure matter less, because the other 20% stops being expensive.

## Tier 3 · Embeddings — catalogued separately, because they are not interchangeable

| Model | Dims | Context | Notes |
|-------|------|---------|-------|
| **Qwen3-Embedding** 0.6B / 4B / 8B | **32–7168** (Matryoshka) | 32K | Open-source leader on MTEB multilingual; 100+ languages; task-prefix instructions gain 1–5% |
| **gemini-embedding-001** | 3072 / 1536 / 768 | **2048 input tokens** | The MVP choice. See the constraint below |
| **BGE-M3** | 1024 | 8K | **MIT.** Dense + sparse + multi-vector from one model — hybrid retrieval without two systems |
| **EmbeddingGemma-300M** | small | — | On-device, multilingual, tiny |

### Gemini embeddings cap input at 2,048 tokens, and that constrains the chunker

The MVP decision is Gemini embeddings for RAG. Its input limit is **2,048 tokens — the smallest
among flagship embedding models** — which is not a footnote:

> **Chunk size must be ≤ 2,048 tokens, and this is a Phase 1 constraint, not a tuning parameter.**

A chunker configured for 4,096-token chunks against this model does not fail loudly. It truncates,
and the second half of every long chunk is silently absent from the index — retrievable by keyword,
invisible to vector search, with nothing recording that it happened. Exactly the class of failure
these documents keep finding: **no error, a corrupted store, and no column that reveals it.**

`chunker_version` is already part of `generator_version`, so a chunk-size change is already a
versioning event. What this adds is a **validation at registration**: an embedding model declares
its input limit, and configuring a chunker beyond it is refused rather than discovered.

### Matryoshka dimensions soften the "changing dimension is a full re-embed" warning

An earlier statement in these documents — that choosing an embedding dimension is irreversible
without re-embedding the corpus — is **too strong for Matryoshka-trained models**, which both
leading options are.

Matryoshka training makes the **first N dimensions a valid embedding in their own right**. So:

| Direction | Cost |
|-----------|------|
| **Reducing** 3072 → 768 | **Truncate the stored vectors.** No API calls, no re-embedding |
| **Increasing** 768 → 3072 | **A full re-embed.** The information was never stored |

Which inverts the earlier recommendation: **store at the larger dimension if the budget allows**,
because reduction stays available and expansion does not. The storage cost is real and the
filtered-ANN latency cost is real — but they are recoverable decisions, and re-embedding a corpus
is not.

**Correcting the earlier guidance:** smaller-by-default was the wrong call. The right call is
**store large, index at whatever performs**, and keep the option.

## Cards are stored, and they drive the mapping

A card that only documents is a wiki page. A card the **system reads** is what turns assignment
from a decision someone has to research into one it can propose and check.

So cards are a **stored, queryable entity** — not prose, not a config file read at boot:

```
model_cards
  model_id · family · version · variant
  architecture · params_total · params_active
  license · license_restrictions[]
  context_window · max_input_tokens        ← the embedding constraint lives here
  capabilities        text · vision · audio · tools · thinking ·
                      structured_output · embedding · domain:{medical,code,legal}
  hardware            min_vram_gb per quantization · quantizations[]
  serving             providers[] · pricing_in · pricing_out · hosting {local,cloud}
  quality_signals     benchmark · score · as_of
  status              recommended | available | deprecated | superseded_by
  card_url
  verified_at
  declared_by         vendor | measured | benchmark        ← per capability
```

### Every capability claim carries who said so

`declared_by` is the field that stops the card being a marketing document.

> A vendor claiming `structured_output` and a model that honours it 95% of the time are different
> facts, and the second only becomes visible after a corpus has been processed.

So a capability is `vendor` until we have run it, then `measured`. The UI shows the difference, and
the [sandbox](../ui-sandbox.md) is where a vendor claim becomes a measured one — run the model
against a sample of the tenant's own data of that type and the claim is either confirmed on their
corpus or is not.

`verified_at` matters for the same reason `status` does: **a card with no date is a claim about a
model that may no longer exist**, and prices rot faster than capabilities.

## Data types declare requirements; models declare capabilities; the mapping is derived

The pairing in the next section is currently something a person authors by knowing the catalog. It
should be something the system **derives and proposes**, because the knowledge needed to author it
is exactly what the cards contain.

```
data_type_profiles
  data_type          medical_record
  requires           [vision, domain:medical, context>=32k]
  sensitivity        phi | pii | confidential | internal | public
  volume_class       low | medium | high
  quality_floor      the minimum acceptable tier
```

Matching is then mechanical:

```
candidates = cards
  WHERE capabilities ⊇ profile.requires
    AND context_window ≥ profile.context_min
    AND hosting ∈ allow_list(project, profile.sensitivity)
    AND hardware.min_vram_gb ≤ available_vram
    AND license acceptable for this deployment
  RANK BY quality_signals, then cost ascending
```

### Sensitivity in the profile is what makes the allow-list automatic

This is the part that changes behaviour rather than convenience.

The [fail-closed allow-list](technology.md) currently depends on someone remembering to configure a
clinical project correctly. With `sensitivity: phi` on the data-type profile, **a cloud engine is
never a candidate for that type in the first place** — not rejected at runtime, not caught by
review, simply absent from the list of models the system will offer.

Getting it wrong stops being possible through the normal path, which is a stronger guarantee than
getting it right being the documented practice.

### The system proposes; a person assigns

**Derivation never auto-applies.** Same rule as
[crawler routing and memory promotion](../memories.md): an agent may propose, a person or a rule
applies.

The reason is specific here. Cards are refreshed — new models appear, `status` changes, a benchmark
is updated. If assignment were derived live, **a catalog refresh would silently change which model
runs**, which changes `generator_version`, which makes the corpus stale without anyone deciding
anything. A model swap must always be an act.

So the surface is a ranked proposal with its reasoning shown:

```
medical_record · enrich

  ▸ medgemma                    recommended
    domain:medical ✓  vision ✓  local ✓ (required by sensitivity: phi)
    128K context ✓    16GB @ Q4 — fits    licence: Gemma terms ⚠ review

  ▸ qwen3.6-27b                 possible
    domain:medical ✗ — general model, no medical tuning
    local ✓  24GB @ Q4 — fits   licence: Apache-2.0 ✓

  ▸ gemini-3.1-pro              excluded
    hosting: cloud — not permitted for sensitivity: phi
```

**The exclusions are the useful part.** A recommendation with no visible reasoning is either
ignored or accepted blindly, and both are worse than no recommendation. Showing *why* Gemini is
absent is what stops someone adding it manually next week.

## Assignment is per (purpose, data type)

Assignment was previously keyed on **purpose alone** — one model for enrichment, one for chat, one
for embeddings. That is too coarse the moment the catalog contains specialised models: it forces a
single enrichment model to handle clinical notes, source code and chat messages equally badly, or
forces the whole deployment onto the most expensive option because one data type needs it.

The key is a pair, resolved most-specific-first:

```
(purpose, data_type)  →  model

classify · *                →  gemma-3-270m          a classifier, not a reasoner
enrich   · medical_record   →  medgemma              local, and no sub-processor
enrich   · code             →  qwen3-coder-30b       256K context, code-tuned
enrich   · transcript       →  qwen3.6-27b
enrich   · *                →  qwen3.6-27b           the default everything falls back to
translate· *                →  translategemma
chat     · *                →  gemini-3.1-pro
embed    · *                →  gemini-embedding-001  ← and only ever one, see below
```

Same precedence as every other setting: **project → org → shipped default**, with admin locks. A
`*` row is required at every purpose, so no data type can be unassigned.

### Prompt, schema and model are one object, not three settings

Prompt overrides are already keyed `(data_type, scope)`. Model assignment now uses the identical
key — and they should be **the same configuration object**, because all three feed the same
`generator_version`:

```
agent_config (data_type, scope)
  prompt          the type-specific block
  output_schema   extensions and disabled standard fields
  model           the assignment for this type
```

Keyed independently, they drift: a deployment ends up with a carefully tuned medical prompt running
against a code model, because two settings screens were edited three weeks apart. **One object, one
version, one fingerprint** — which also means the [test-before-save](../ingestion/workers.md)
sample run exercises the actual combination that will run in production, rather than a prompt
against whichever model happens to be assigned today.

### Embeddings are excluded from per-type assignment, deliberately

This is the guard that matters, because per-type assignment is **exactly the mechanism that would
let someone put two embedding models into one index through a settings page.**

> Assign `embed · medical_record → medgemma-embedding` and `embed · * → gemini-embedding-001`, and
> the index now contains **two incomparable vector spaces**. Nothing errors. Ranking degrades in a
> way that looks like poor retrieval quality rather than a configuration mistake.

So `embed` accepts **only** a `*` assignment. A per-type embedding row is refused at configuration
time with the reason stated, not accepted and then detected later by
`embed.distinct_models_per_index` — that metric is the backstop for drift, not a substitute for
refusing the setting.

The same restriction does not apply to enrichment, where a per-type model produces *differently
good* output rather than *incomparable* output.

### Per-type assignment makes model changes cheaper to make and to undo

An unexpected benefit worth naming. Under purpose-only assignment, switching the enrichment model
invalidates **every artifact in the corpus** — so W7 reprocess is a whole-corpus job and changing
your mind is expensive enough to discourage improving anything.

Keyed per type, changing `enrich · code` marks **only code artifacts stale**. Reprocess scopes to
the affected data type, which is a fraction of the corpus, and the
[staleness-impact preview](../ui-sandbox.md) can state the real number before the change is
applied: *"this affects 4,102 items of 380,000."*

**Precision in the key buys affordability in the change.** That is what makes per-type model tuning
something a team will actually do, rather than a capability that exists and is never touched.

## Selection → routing

Once a user assigns models, routing **validates** rather than trusting:

| Check | Failure mode prevented |
|-------|----------------------|
| **Capability match** | A vision agent assigned a text-only model — fails at ingest, not at config time |
| **Hardware feasibility** | A 70B model selected on a 16GB machine — pulls, then OOMs under load |
| **Context window** | Long documents silently truncated because the assigned model has a 32K window |
| **License** | A use-restricted model assigned in a commercial deployment |
| **Provider reachability** | A model assigned but not served by any configured engine |
| **Per-type embedding refused** | Two vector spaces in one index, arrived at through a settings page |
| **`*` row present per purpose** | A data type with no assignment, failing at ingest rather than at config |

Validation runs at **assignment time** with clear errors, and again at startup. The current failure
mode — discovering the mismatch when a document fails to process at 3am — is what this removes.

## Changing a model is a versioning event

This is the part most likely to be under-designed.

A model assignment is part of the **generator version** of every artifact that tier produces.
Switching the medium tier from one model to another does not just affect future work — it makes
every existing summary, entity extraction and classification from that tier **stale**.

So the selection UI has an obligation:

> Changing this model marks **2.1M artifacts** stale.
> Estimated rebuild: **~4.5 hours**, **~$0** (local) / **~$180** (cloud).
> [ Rebuild now ] [ Rebuild in background ] [ Leave stale ]

Without that, "improve the model" is a change users make casually and whose consequences they
discover months later when half the corpus reflects one model and half another. See
[retrieval/versioning.md](../retrieval/versioning.md).

## Embedding models are a different UI

**Embedding model selection must not be a dropdown.**

Vectors from different embedding models occupy incomparable spaces. Changing the embedding model
is a corpus-wide migration, not a configuration change — build a parallel index, backfill it,
verify, then swap.

The catalog carries embedding models with their own fields — `dimensions`, `max_input_tokens`,
`normalization` — and the UI must present the change as a **guided migration with a cost estimate
and a rollback path**, never as a setting.

Related and non-negotiable: **embedding calls must never use the fallback chain.** If the assigned
embedder is unavailable, defer with `embed_status = pending`. Substituting a different model
silently corrupts the index.

## Telemetry

Per-inference, record the **model id and version that actually served the request** — not the one
that was configured. Without it you cannot tell whether output came from the primary or the third
fallback, cannot attribute quality regressions, and cannot identify affected rows after a bad
assignment.

Also worth tracking: per-model latency and cost, fallback depth reached, capability-mismatch
rejections, and pull/warm status for local models.

## Surfaces

| Surface | Capability |
|---------|------------|
| **API** | `GET /ai/catalog` (with filters), `GET /ai/catalog/{id}`, assignment endpoints, `POST /ai/assignments/preview` returning the staleness estimate |
| **UI** | Browsable catalog with capability and hardware filters, model card detail, "runs on your hardware" indicator, assignment with impact preview |
| **SDK** | Catalog listing and assignment in the full client; policy locks in the admin client |
| **Admin** | Org-level allowlist — pin approved models, block others. Ties to the config precedence model |

## Open questions

- **Catalog freshness.** Ship it static and update with releases, or fetch a signed catalog
  periodically? Static is air-gap-friendly; fetched stays current. Probably static with an
  optional refresh.
- **Benchmark claims.** Publishing quality signals invites disagreement and dates badly. Cite with
  dates and link out, or omit and let users judge?
- **Auto-upgrade.** When a model is superseded, offer a one-click migration path with the
  staleness estimate attached — or stay silent and let users choose?

## Sources

- [Hugging Face — Best Open Source and Open-Weight LLMs to Run Locally (2026)](https://huggingface.co/blog/daya-shankar/open-source-llm-models-to-run-locally)
- [Codersera — Open-Source LLM Landscape 2026](https://codersera.com/blog/open-source-llms-landscape-2026/)
- [PromptQuorum — Ollama 2026: best models by use case](https://www.promptquorum.com/local-llms/top-open-source-models-ollama)
- [ComputingForGeeks — Ollama Models Cheat Sheet 2026](https://computingforgeeks.com/ollama-models-cheat-sheet/)
- [Till Freitag — Open-Source LLMs Compared 2026](https://till-freitag.com/en/blog/open-source-llm-comparison)
