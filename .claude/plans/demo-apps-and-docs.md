# Demo apps and a docs section on the public page

**One sentence.** Turn the single public demo corpus into a gallery of use-case
"apps" — each with synthetic data, a description of what it demonstrates, and a
chat anyone can try without logging in — and add a docs section beside it.

---

## 1 · What already exists

Most of this is generalisation, not new construction.

| Piece | Where | State |
|---|---|---|
| A public, login-free ask | `public_demo.py`, `GET /api/v1/public/demo`, `POST /api/v1/public/ask` | Built. **One** corpus, named in env |
| The demo corpus itself | `seed.py`, 1143 lines | Built. **One** use case — Acme renewal, family B |
| Questions as acceptance assertions | `seed.QUESTIONS` | Built, and the pattern to keep |
| Synthetic-data honesty discipline | `seed.MARKER`, reserved names, `.example` domains | Built |
| The landing-page demo panel | `Landing.tsx` → `PublicDemo` (line 673) | Built, single corpus |
| Write-then-find walkthrough | `Sandbox.tsx`, `docs/ui-sandbox.md` | Built, separate surface |
| The use cases themselves | `docs/use-cases-catalog.md` | Twelve, documented |

So: **one app exists**. The request is a gallery of them, plus docs.

## 2 · The tension this has to survive

`public_demo.py` opens by stating exactly why it is safe, and every clause is a
narrowing:

> *"Everything else in this system authenticates first and decides what you may
> read second. This surface inverts that — it has no caller to identify — so it
> cannot borrow any of the machinery that makes the rest of the API safe. What
> replaces that is narrowness."*
>
> *"One project, one memory, named in configuration. Not 'whatever the token
> says', not a parameter — an environment variable set by whoever deployed it.
> **A request cannot ask for a different corpus, so there is no scope to
> escalate.**"*

The request asks for many corpora on that surface. Done carelessly, "the corpus
is not a parameter" becomes "the corpus is a parameter", and the one sentence
holding the whole thing up is gone.

**Resolution: a registry, not a parameter.** The request names a demo *app* by
key; the key is looked up in a server-side registry of seeded demo corpora and
resolved to a project and memory. An unknown key is 404, not a lookup. The
invariant survives in its true form — *a request cannot reach a corpus that was
not deliberately published as a demo* — and the config gains a list where it had
a single value.

Three properties that must not be relaxed while doing it:

- **Read-only, one verb.** Still ask. No write, no memory listing, no graph.
- **Answers are never stored.** There is no user to attribute them to.
- **Off unless configured.** An empty registry disables the whole section.

## 3 · Cost, which is the other thing that bites

Every demo question is a model call on the deployment's key, from an
unauthenticated caller. Today one hard daily cap covers one corpus.

**The cap stays global, not per app.** Twelve apps with the existing per-corpus
cap is twelve times the bill for the same feature. A shared budget across the
gallery keeps the ceiling where it is, and the honest failure — *"the demo said
come back tomorrow"* — is already built and already the right behaviour: a hard
stop rather than a throttle, because *"the bill grew slowly overnight is not a
better outcome"*.

Per-app rate limiting is worth adding on top so one app cannot drain the day.

## 4 · Which apps, and the mistake to avoid

**Do not build twelve.** Twelve apps that each demonstrate "chat over documents"
teach the same thing twelve times and cost twelve corpora of authoring. The use
cases differ by **mechanism**, and the gallery is worth building only if the apps
differ the same way.

Proposed four, chosen because each shows something the others cannot:

| App | Use case | The mechanism it exists to show |
|---|---|---|
| **Acme renewal** | 10 · Sales | Already built. Cross-source correlation on a deal, and a private record that stays invisible |
| **A matter** | 7 · Legal | **Asserted vs inferred membership** — the distinction the practice depends on, and one nothing else demonstrates |
| **A meeting series** | 12 · Meeting Intelligence | A recurring session and *what changed since last time* — checkpoints, the timeline shape |
| **A sensor fleet** | 6 · IoT | **Facets with zero model calls.** The one app that proves not everything goes through an LLM — and the cheapest to run |

The fourth is deliberately chosen to cost nothing per question: a facet range
query needs no model, so the gallery has at least one app that cannot be
rate-limited into uselessness.

If more are wanted later the registry takes them; the authoring is the cost, not
the plumbing.

## 5 · The honesty rule, which gets sharper here

`seed.py` already establishes it and the reason:

> *"Reserved names, and identifier ranges documented as fake. A demo company
> that … a demo record that looks real may be treated as real."*
> `MARKER = "[DEMO — synthetic data, generated, not real]"`

Two of the proposed apps make this acute rather than routine:

- **A legal matter** means generating contracts and correspondence. A synthetic
  contract that reads as real is a document somebody can misuse.
- **Anything clinical is out of scope for this plan.** Fake medical records are
  the highest-risk synthetic data in the catalog and there is no demo value that
  justifies authoring them. Use case 8 is not in the gallery.

Every record in every corpus carries the marker, reserved identifiers
(`M-DEMO-…`, `.example` domains, documented-as-fake ranges), and a visible
banner in the app itself — not only in the data.

## 6 · Implementation

### [ ] Step 1 — corpora as data, not as code  *(deferred: authoring)*

`seed.py` is 1143 lines for one use case, with the corpus inline. Three more in
that style is four thousand lines of Python holding what is really content.

- Move the corpus body to `api/src/memdog/demos/<key>.py` (or a data file),
  keeping the existing `Item` / `Question` dataclasses.
- `seed.py` keeps the *sequence* — write, enrich, verify, report — and takes a
  corpus as an argument. **That sequence is the valuable part** and must not be
  duplicated per corpus: it is what makes a failed seed say which step failed.
- Every corpus ships `QUESTIONS`, each naming the record that must answer it.
  This is already the rule: *"a demo corpus with no questions attached is a
  corpus; the questions are what make it a demonstration"* — and they double as
  the acceptance test, so a corpus that stops demonstrating fails the seed.

### [x] Step 2 — the registry

- `config.py`: `PUBLIC_DEMOS` — a list of `key:project_id:memory_id` (or a JSON
  blob), replacing the single `PUBLIC_PROJECT_ID` / `PUBLIC_MEMORY_ID` pair.
  **Keep the old pair working** so a deployment configured today does not break;
  it becomes a one-entry registry.
- `public_demo.py`: resolve a key through the registry. Unknown key → 404.
  Nothing else about the module's contract changes.

### [x] Step 3 — the API

- `GET /api/v1/public/demos` — the gallery. Per app: key, title, description,
  what it demonstrates, its sample questions, record count. Unauthenticated, and
  `{"demos": []}` on a deployment with none, which is the ordinary answer and
  not an error.
- `POST /api/v1/public/ask` — gains `demo` (the key). Absent, it resolves to the
  first registry entry so existing callers keep working.
- `GET /api/v1/public/demos/{key}` — one app's detail, including its questions.

### [x] Step 4 — the apps section on the landing page

`Landing.tsx` already has `PublicDemo` rendering one corpus. It becomes:

- a gallery of app cards — title, one line on what it demonstrates, record count
- selecting one opens the existing chat panel against that app, with its sample
  questions as one-click starters (they already exist and are already known to
  work, because the seed asserts them)
- the answer renders with its citations, as it does today
- the synthetic-data banner is part of the app, not only the data
- the shared daily allowance is shown **beside the ask button**, not discovered
  when it runs out

### [x] Step 5 — the docs section

The ask is *"add docs section so that people can follow the documents"*.

- `docs/` is 40+ markdown files with a `README.md` index, cross-linked by
  relative path. Rendering them needs a markdown renderer and, more awkwardly,
  **link rewriting** — `[..](use-cases.md)` has to resolve to a docs route, and
  a link that silently 404s is worse than no docs section.
- Simplest honest version: a `/docs` route, files read at **build time** (they
  are in the repo, so no runtime filesystem dependency and nothing to keep in
  sync), the `README.md` table of contents as the index, relative links
  rewritten to route paths.
- **Decision needed:** all of `docs/`, or a curated subset? Much of `docs/` is
  internal design record — `competition/`, `analysis/`, roadmap — written for
  maintainers, not for someone trying the product. Publishing it wholesale
  exposes the honest internal assessments to anyone who lands on the page. My
  recommendation is a curated list, named in one place, defaulting to the
  user-facing subset.

## 7 · Tests

- **Registry**: an unknown demo key is 404; a key not in the registry cannot be
  reached even if the project exists; an empty registry disables the section.
- **Isolation**: a demo ask cannot return a record from another demo's corpus,
  and cannot return a private record from its own.
- **Cap**: the shared cap is shared — spending it in one app closes the others,
  and the message says so.
- **The seed's own assertions**, per corpus: every question finds the record it
  names, or the seed fails.
- **Docs**: every rendered link resolves to a route that exists. This is the one
  that will actually catch something.

## 8 · Open questions

1. **Login UI or landing page?** The request says "on login UI". `/` renders
   `Landing` when signed out and `Console` when signed in, so I read this as the
   signed-out landing page. Confirm — putting it in the console instead is a
   different screen with different constraints.
2. **Four apps or a different set?** Sales exists; legal, meetings and sensors
   are my picks for showing distinct mechanisms. Say if the priority is
   different — the authoring is the expensive part, so this choice is worth
   making before any of it is written.
3. **Docs: all or curated?** See step 5. Recommend curated.
4. **Is this for production, or the sandbox deployment?** An unauthenticated,
   model-spending gallery on a production domain is a different risk posture
   from the same thing on the sandbox.


---

## Built, and what is left

Steps 2–5 are done, tested and verified in a browser. **Step 1 is deliberately
last**: the plumbing takes any number of corpora now, and the remaining work is
authoring — which was always the expensive part, and is the part worth doing
against a real decision about which corpora earn their place.

Resequenced from the plan's order for one reason: the registry, the API and the
gallery could all be built and proven against the corpus that already exists, so
the mechanism was demonstrable before any new corpus was written. Adding one is
now a configuration entry plus its records.
