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

## Indicative catalog (August 2026)

Illustrative of the shape and of what "current" means. Expect this to be stale within months.

| Model | Capacity | Capabilities | Context | Notes |
|-------|----------|--------------|---------|-------|
| `gemma4:e2b` / `e4b` | small | text, vision, audio, tools | — | Nano variants — edge and low-RAM |
| `gemma4:12b` | medium | text, vision, audio, tools, thinking | — | Practical laptop model |
| `gemma4:26b` / `31b` | large | text, vision, audio, tools, thinking | — | Strong vision/multimodal |
| `qwen3.5:4b` | small | natively multimodal, tools, thinking | — | Multimodal at 4B |
| `qwen3.6:27b` | large | text, tools, thinking | — | Fits 24GB at Q4; strong agentic/coding |
| `qwen3.6:35b-a3b` | large | MoE, 3B active | — | Best all-round at 32GB |
| `qwen3-coder:30b` | large | code | 256K | Long-context coding |
| `llama4-scout` / `maverick` | large | text | up to 1M | Long-context retrieval leader |
| `deepseek-v4` | large | text, reasoning | — | High-end reasoning, serious hardware |
| `glm-5.2` | large | text | — | Strong all-round open-weight |
| `mistral-medium-3.5` | large | text | 256K | 128B dense |
| `phi-4-mini` | small | text | — | Very small footprint |

Embedding models are catalogued separately — see below, because they are not interchangeable in
the way these are.

## Selection → routing

Once a user assigns models, routing **validates** rather than trusting:

| Check | Failure mode prevented |
|-------|----------------------|
| **Capability match** | A vision agent assigned a text-only model — fails at ingest, not at config time |
| **Hardware feasibility** | A 70B model selected on a 16GB machine — pulls, then OOMs under load |
| **Context window** | Long documents silently truncated because the assigned model has a 32K window |
| **License** | A use-restricted model assigned in a commercial deployment |
| **Provider reachability** | A model assigned but not served by any configured engine |

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
