# Custom Worker Code

**Data workers may run customer-supplied code on a custom cluster.**

> **Not in MVP.** MVP ships no customization at all — no custom handlers, no registry, no
> extension surface. This document decides the *shape* so that the two things which cannot be
> retrofitted are present from the start: the **read-only security envelope** on the worker item,
> and **`handler_digest` in the `generator_version` input set**. Both are free when nothing plugs
> into them, and both are expensive once something does.


This corrects a claim in [write-customization.md](write-customization.md), which stated that
customization is always declarative and never customer code. That argument was about **tenancy**,
not about extensibility — and on a dedicated cluster the tenancy argument does not apply. The rule
was stated one level too absolutely.

The corrected position is a matrix, not a prohibition.

---

## Where custom code may run

|  | Shared cloud | **Custom cluster** |
|--|:------------:|:------------------:|
| Synchronous write phases (4–6, 8) | declarative only | declarative only |
| **Data workers** (W1–W3, W7, W8) | declarative only | **custom code** |

Two boundaries, and they are different in kind:

**The variant boundary is about tenancy.** A shared write path running one project's code makes
that code every project's latency, crash surface and security boundary. A dedicated cluster has one
tenant, so the operator running their own code in their own cluster is running their own risk. That
is a legitimate thing to allow.

**The phase boundary is about latency and blast radius, and it survives the change.** The write
path is synchronous and holds a client connection; workers are asynchronous, isolated, retryable
and independently resource-capped. Custom code belongs where a crash costs a retry, not where it
costs a timeout on someone's `POST`.

> **Enforcement, not documentation.** The shared cloud MUST *refuse to load* custom handlers, not
> merely omit them from the docs. A capability that is one configuration flag away from a
> multi-tenant breach is not a boundary — it is a habit.

---

## Why this matters more than it looks: custom fetch adapters

The obvious use is custom extraction in **W1 enrich**. The more valuable one is **W2 fetch**.

[Source adapters](workers.md) cover the families Nango reaches. They cannot cover the internal
system a customer built in 2011 that speaks a proprietary protocol and holds the records that
actually matter. Today that customer's only path is to run an external ETL and push through a
`key_` producer — which works, and which means they rebuild scheduling, retry, backoff,
idempotency and credential handling themselves, outside the platform that already has all five.

A custom fetch adapter implements the same four verbs as any other:

```
resolve(ref)      → [resource]        expand a pointer into concrete resources
materialise(res)  → ContentRef        stream bytes to the object store
export(res, fmt)  → ContentRef        server-side render, where no raw bytes exist
read(res)         → structured record an API object, not a file
```

**This is the strongest argument for the feature.** The adapter interface was designed as an
internal seam; opening it turns "we don't support your system" into "here is the interface." And
because the adapter returns a `ContentRef`, custom code cannot smuggle in a new provenance case —
downstream enrichment still cannot tell how the content arrived.

---

## Four invariants that do not relax

Allowing custom code changes who writes the logic. It changes nothing about what the platform
guarantees.

### 1 · The security envelope stays read-only

The [phase-3 rule](write-customization.md#the-finding--ordering-is-a-privilege-escalation-boundary)
was about the write path. It applies identically here, and for the same reason: **a worker that can
rewrite `access_level` is a privilege escalation regardless of who wrote it.**

Custom code receives content and metadata. It returns derived output. Security fields are
read-only on the way in and rejected on the way out — not sanitised, *rejected*, so a handler
attempting it fails loudly rather than having its intent silently discarded.

This is the invariant that holds even though the tenant owns the cluster. A single-tenant
deployment still has multiple users, projects and connection scopes inside it, and the whole
personal-vs-team ACL model depends on this field not being writable downstream.

### 2 · Credentials still do not reach worker code

[FR-ING-7](../functional-requirements.md) is not relaxed. Custom code calls upstream through the
same credential proxy every built-in adapter uses; it never holds a secret.

Handing credentials to customer code would defeat the proxy for the one class of code most likely
to log, serialise or exfiltrate them — and on a self-hosted cluster the operator would be doing it
to their own tenants' tokens.

**A custom adapter declares which connection it needs. The proxy injects.** Same contract, same
enforcement point.

### 3 · Custom code is part of the generator fingerprint

This one is easy to miss and corrupts data silently if missed.

```
generator_version = sha256(canonical_json({
  prompt, model_id, schema, parser_version, chunker_version,
  handler_digest          ← required once custom code exists
}))
```

Without `handler_digest`, deploying new handler code produces **different output under the same
generator version**. Every downstream guarantee that rests on the fingerprint — reproducibility,
staleness detection, [trend pinning](../use-cases-catalog.md), reprocess targeting — quietly stops
being true, and nothing indicates it.

The corollary is the usual one: **changing custom code is a config change and enqueues W7
reprocess**, or the corpus is knowingly mixed.

### 4 · Failures attribute to the code that caused them

Custom code crashes, hangs, allocates without bound and occasionally loops forever. The existing
[failure policy](workers.md#failure-policy-diverges-by-class) already separates terminal from
retryable, and custom code slots in with three additions:

| Control | Why |
|---------|-----|
| **Wall-clock timeout** | A hung handler must not hold a worker slot indefinitely |
| **Memory cap** | Enforced by the runtime, so the pod dies rather than the node |
| **Attribution** | Failures record the handler and its digest |

Attribution is the operationally important one. Without it, every support conversation opens with
*"is this your bug or ours?"* and neither side can answer. With it, the DLQ entry names the
handler, the digest and the input — and the question answers itself.

---

## The interface

Custom handlers register against a worker class and a scope. They are configuration, discovered at
startup, versioned like everything else.

```
CustomHandler
  worker_class      W1 enrich | W2 fetch | W3 crawl | W7 reprocess | W8 mutate
  scope             project | org
  digest            content hash of the handler artifact — enters generator_version
  entrypoint        module path or OCI image reference
  limits            timeout, memory, concurrency
  connections[]     which connections it may request through the proxy
  output_schema     validated on return, exactly as a built-in agent's is
```

**`output_schema` is not optional.** [The schema is the contract, not the prompt](workers.md#the-schema-is-the-contract-not-the-prompt)
— a built-in agent's output is validated before it is stored, and custom output is validated the
same way. Custom code that returns something unexpected produces a schema violation and a DLQ
entry with the raw output attached, exactly as a misbehaving built-in agent does. It does not
produce a malformed record.

### Testing before enabling

```http
POST /api/v1/custom-handlers/{id}:test
```

Same requirement as [prompt overrides](workers.md#test-before-save) and every other customization
surface: run it against a sample payload before it touches live data. For a fetch adapter this
matters more than usual, because the failure it catches is *"authenticates fine, returns the wrong
thing"* — which looks like success from every direction except the data.

---

## What this does to the deployment story

Extensibility becomes a **stated capability of the self-hosted variants** rather than an accident
of them.

| Capability | Local | Custom cluster / GKE | Shared cloud |
|------------|:-----:|:--------------------:|:------------:|
| Declarative customization | ● | ● | ● |
| **Custom worker code** | ● | ● | — |
| Custom fetch adapters | ● | ● | — |

That is worth naming in the [competitive position](../competition/README.md). Self-hosting itself
is table stakes — Onyx ships it under MIT. *"Self-hosted, and you can extend the ingestion pipeline
for the internal system nobody has a connector for"* is a different claim, and it is one a
multi-tenant SaaS structurally cannot match.

It also sharpens the variant trade honestly: the shared cloud is cheaper and managed, and it is
**less extensible on purpose**. Customers who need custom adapters need a cluster. Saying so is
better than letting them discover it after adopting the hosted version.

---

## Requirements

- **FR-CW-1** Data workers MUST support customer-supplied handler code on dedicated clusters.
- **FR-CW-2** The shared multi-tenant deployment MUST refuse to load custom handlers, enforced at
  runtime rather than by configuration convention.
- **FR-CW-3** Custom code MUST NOT execute in the synchronous write path, on any variant.
- **FR-CW-4** Custom handlers MUST receive security fields as read-only, and attempts to write them
  MUST be rejected rather than silently discarded.
- **FR-CW-5** Custom handlers MUST NOT receive credentials. Upstream calls MUST route through the
  credential proxy, and a handler MUST declare which connections it may use.
- **FR-CW-6** The handler digest MUST be part of `generator_version`.
- **FR-CW-7** Changing custom handler code MUST enqueue W7 reprocess for affected data, or MUST
  record that the corpus is knowingly mixed.
- **FR-CW-8** Custom handlers MUST run under an enforced wall-clock timeout, memory cap and
  concurrency limit.
- **FR-CW-9** Failures MUST record the handler identity and digest, so a failure is attributable
  without inspecting the platform.
- **FR-CW-10** Custom handler output MUST be validated against a declared schema before storage,
  exactly as built-in agent output is.
- **FR-CW-11** A custom handler MUST be testable against a sample payload before it is enabled.
- **FR-CW-12** A custom fetch adapter MUST return a `ContentRef`, so downstream enrichment remains
  unable to determine how content arrived.
