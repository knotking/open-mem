# Onboarding and Seeding

Two things that arrive together at first run: **how the first tenant comes to exist**, and **who is
allowed to become the second**.

Decided: **a seeded demo org, team, project and user for testing, and invite-only registration for
everyone else.**

---

## The seed is the Phase 1 exit criterion, made runnable

[Phase 1 exits](../roadmap.md) when *an item is written into a project owned by an org with an
access level, found by scoped search, cited, audited, and the row records which model embedded it.*

**That is exactly what seeding a demo tenant does.** So the seed is not a convenience script beside
the tests — run through the real path, a successful seed **is** an end-to-end verification, and a
failing one localises the break before any human has logged in.

Which gives the rule that shapes everything else here:

> **Seeding uses the same API a real signup uses. There is no fixture path.**

A seed that inserts rows directly tests the seed. Whatever it proves is not transferable, and it
diverges the moment the real path changes — the same argument the [sandbox](../ui-sandbox.md) makes
for not being a special mode.

Concretely: the seeder creates the org through the org endpoint, the project through the project
endpoint, and writes its sample items through `POST /api/v1/write` with a registered producer,
exactly as an external client would.

## The demo ships working use cases, not a scaffold

The demo is not fifty assorted rows. It is **six worked domains that answer real questions on first
login** — and each one exists to demonstrate something the others cannot.

| Domain | Chosen because it is the only one that shows… |
|--------|-----------------------------------------------|
| **Sales** — an Acme renewal | Multi-source correlation on a **deal id**, recording **fan-out** into four assets, and commitments as structured fields with actors and due dates |
| **Clinical** — a patient timeline | **`event_time` ≠ `ingested_at`** — a backdated 2019 report ingested today · **`sensitivity: phi`** making cloud models non-candidates · identifier join on MRN |
| **Legal** — a matter | **Asserted vs inferred** membership rendered differently · **legal hold beating erasure** with partial completion · deadlines as date facets |
| **Support** — a ticket history | The **question index** — the ticket says *"it just spins forever"*, the engineer searches *"performance regression"* · sentiment as a bounded facet |
| **Telemetry** — sensor readings | The **`enrich: false`** path · facet-only retrieval · volume that never reaches a model |
| **Personal** — one user's own mail | **Connection scope** `personal` vs `shared` producing opposite ACLs in the same org · the per-user default memory |

**Six, not twelve.** The selection principle is coverage of *mechanisms*, not breadth of industries.
A seventh domain that demonstrates nothing the first six do not is corpus weight without
information — it makes the demo slower to seed and no more convincing.

### Each domain ships its configuration, and that is half the value

A demo that only contains data shows what the product stores. A demo that contains **the
configuration that made the data useful** shows how to use it — and configuration is the part new
users get wrong.

So each domain arrives with:

| Shipped | Example |
|---------|---------|
| **Memory types** with TTL and expiry policy | `matter` — no TTL, `keep_members` |
| **A `data_type_profile`** | `medical_record` — `sensitivity: phi`, requires `domain:medical` |
| **Model assignment** for its types | `enrich · medical_record → medgemma` |
| The **shipped prompt**, unmodified | So the user can see what a default produces before overriding |
| **Saved queries that work** | *"What did we promise Acme?"* · *"What is due in the next 30 days?"* |

The saved queries matter more than they look: **a demo corpus with no questions attached is a
corpus.** The questions are what make it a demonstration, and they double as the acceptance
assertions — if *"what did we promise Acme?"* stops returning the commitment, something broke.

## The tension this creates, and how it resolves

Two rules already written point in opposite directions here:

> *"Seeding uses the same API a real signup uses. There is no fixture path."*
>
> *"The demo must answer real questions on first login."*

Honouring the first means **enriching six domains through real model calls at seed time** — on a
laptop with local models, that is not minutes. Honouring the second by shipping pre-computed
artifacts means the seed no longer exercises the pipeline, and proves nothing.

### The resolution: they are two different fixtures with opposite requirements

| | **Test fixture** | **Demo tenant** |
|---|---|---|
| Goal | Determinism | Authenticity |
| Model calls | **None** — recorded responses | **Real**, through the pipeline |
| Enrichment | Pre-computed, checked in | Produced at seed time |
| Used by | CI, every test run | A human, once per deployment |
| If it drifts from reality | The test is wrong | The demo is wrong |

**Tests must not depend on a model.** A test suite whose assertions move when a provider updates a
model is a test suite people learn to ignore — so the test fixture carries recorded responses, and
the pipeline is exercised against them.

**The demo must not lie.** Its whole purpose is showing what this system does with data, so it runs
the real path.

### And the demo's seeding time is a feature, not a cost

Seeding enriches in the background with the **[readiness staircase](../ui-sandbox.md) visible**:

```
uploaded    ████████████████████████  312
stored      ████████████████████████  312
searchable  ████████████████░░░░░░░░  198
enriched    ███████░░░░░░░░░░░░░░░░░   94   ~6 min remaining
```

This is the most honest possible first impression, and it teaches the mental model the product
actually needs the user to hold: **content arrives immediately, becomes searchable shortly after,
and becomes *understood* later.** A demo that hid that would set an expectation the user's own data
will not meet.

Two tiers keep it practical:

| Command | Contents | Time |
|---------|----------|------|
| `seed --demo` | **One domain** (sales), ~40 items, enriched synchronously | Under a minute |
| `seed --demo --full` | All six domains, ~300 items, enriched in the background | Minutes, watchable |

## Synthetic data has to be obviously synthetic

This matters far more in clinical and legal than anywhere else, and it is easy to get wrong by
trying to make a demo look impressive.

| Rule | Why |
|------|-----|
| **Reserved names only** — Acme, Contoso, Northwind | A demo company that is a real company is a problem someone else did not agree to |
| **Documented fake identifier ranges** — `MRN-DEMO-*`, `MATTER-DEMO-*` | A realistic-format MRN could collide with a real one, and a demo record that looks real may be treated as real |
| **A visible marker** on demo records | Someone eventually screenshots a demo patient timeline into a deck |
| **Generated, never anonymised** | Anonymised real data is real data that has been processed. Generated data was never anyone's |

> **The goal is a demo nobody can mistake for production data**, including at a glance, in a
> screenshot, six months later, by someone who was not there when it was seeded.

## A demo that only shows success teaches a false expectation

The instinct is to curate: every record enriched cleanly, every timeline complete, every query
answered. **That demo is a bad demo**, because the user's own corpus will not look like it, and the
gap will read as the product failing rather than as normal.

So the fixture deliberately includes:

- **One item that failed normalization** — stored raw with `normalization_status: failed` and a
  reason, showing that a mapping bug loses nothing
- **One item still enriching** when the others are done, so the staircase is visible in a steady
  state, not only during seeding
- **One item the demo member cannot see** — the admin's `personal` connection data, in the same
  project, so ACL behaviour is demonstrable rather than asserted
- **One case with an inferred member** alongside asserted ones, rendered subordinate and excluded
  from the count

Each of these is a mechanism from these documents that is otherwise invisible until it matters. The
demo is where they can be *shown* rather than described.

## Reset is the ordinary cascade

`seed --demo --reset` purges the demo org and re-seeds — through
`DELETE /organizations/{id}?purge=true`, the same path as any offboarding.

**A demo people can break needs a reset, and the reset needs to be the real one.** If resetting the
demo requires bespoke cleanup, the purge cascade is incomplete — and the demo has found the bug
before a customer did.

## Demo credentials are the classic backdoor

A known demo user with a known password, present in every deployment, is a shipped default
credential — the failure mode that shows up in breach write-ups more reliably than any other.

So:

- **Credentials are generated per deployment**, never fixed in the seeder
- The generated password is **printed once, at seed time**, and not stored in recoverable form
- The demo user is an **ordinary user** with ordinary rights, not an escalated one
- `seed --demo` is **explicit**. Production installs are not seeded by accident

## The demo tenant is ordinary data, and reporting knows the difference

The tempting shortcut is an `is_demo` flag consulted throughout. Resist it: a flag checked in the
data path becomes a branch in retrieval, in deletion, in the ACL predicate — a special case in
exactly the places that must not have one.

**The data path never knows.** The demo org has a known id; *reporting* excludes it from tenant
counts, usage aggregates and billing. One filter, in the layer that summarises, not in the layer
that serves.

And it is **deleted by the ordinary path** — `DELETE /organizations/{id}?purge=true`, the same
cascade as any other offboarding. If removing the demo needs bespoke cleanup, the cascade is
incomplete and the demo has just found the bug.

---

## What is built

`python -m memdog seed --demo` ships the **sales domain**, forty records about one Acme renewal,
enriched synchronously, in a few seconds against local engines. `--reset` purges and re-seeds.

**It runs the real path.** The corpus goes in through `POST /api/v1/write` with a registered
producer, the case is created through `PUT /api/v1/cases`, the questions are asked through
`POST /api/v1/retrieve`, and the reset is `POST /api/v1/deletions` — the ordinary selector-delete,
narrowed by the `demo` tag because a project id alone is deliberately not a selector.

**And it verifies itself**, clause by clause against the Phase 1 exit criterion:

| Clause | How the seed checks it |
|--------|------------------------|
| written into a project owned by an org | the write returns `207` with no failed items |
| with an access level | a second member's search does **not** return the record written through a personal connection |
| found by scoped search, and cited | five saved questions each name the record that answers them |
| the read is audited | the access log has rows after those searches — the *read* half, not the write half |
| recording which model embedded it | no embedding row is missing `model_id` or `generator_version` |

A failing seed raises with the step that broke rather than a stack trace. Both of the defects found
while writing it were of that shape: a question that had stopped finding its record, and the read
audit asserted against the wrong half of the trail.

`tests/test_seed.py` runs the whole thing on every commit, so the exit criterion is a gate rather
than something someone remembers to check.

### Three things are still created directly

An **organization**, a **connection** and a **second member's credential** have no endpoint behind
them: orgs and admin-issued keys because the control plane's admin half is a later slice,
connections because they are what an OAuth flow produces and that is not built. They are the
bootstrap, not the data path, and they are marked at the point of use.

The reset has the mirror of this. Records go through the cascade, but the **org row is deleted
directly** because `DELETE /organizations/{id}?purge=true` does not exist — and **users are removed
by name**, because a user does not cascade from an organization and should not: a person is not
owned by an org.

### Not built, and why

| Deferred | Reason |
|----------|--------|
| **`seed --demo --full`** — the other five domains | Clinical, legal, support, telemetry and personal each demonstrate a mechanism the sales domain cannot, but four of those mechanisms — `sensitivity: phi` candidacy, legal hold, the question index, connection-scope ACLs — are only partly built. Seeding a domain to demonstrate something absent produces a demo that lies |
| **The item that failed normalization** | `normalize.project()` **is never called from the write path** — only `create_schema` and `list_schemas` are wired up. A normalization schema can be registered today and will never be applied, so the failure this record exists to show cannot be produced honestly. This is a real gap in the pipeline, not a gap in the seed |
| **The item still enriching** | Approximated by three usage exports written with `enrich: false`, which leaves the staircase at two heights in a steady state. A genuinely mid-flight item needs the seed to return before the queue drains, and a seed that returns before it is ready cannot verify itself |

---

## Registration is invite-only

```
registration_mode:  invite_only  (default)  |  open  |  disabled
```

**Invite-only is the default and the intended posture for now.** `open` exists for deployments that
want self-service; `disabled` for a closed appliance where accounts are provisioned out of band.

### An invite is a credential, and gets credential treatment

An invite grants org membership at a stated role. That makes it a bearer token, not a hint, so it
carries what any credential carries:

| Property | Rule |
|----------|------|
| **Single-use** | Redeemed once, then dead |
| **Expiry** | Bounded — 7 days by default |
| **Scoped** | To one org, one role, and optionally one email address |
| **Revocable** | Before redemption, by any org admin |
| **Audited** | On creation *and* on redemption — see [audit](../security/audit.md) |

Auditing redemption matters as much as creation: *"who invited them"* and *"who actually walked
through the door"* are different questions, and an invite forwarded to someone else answers only the
second.

### Two failure modes worth designing against

**An invalid token must not reveal whether an org exists.** *"That invite is invalid"* and
*"that invite expired"* are acceptable; anything that distinguishes *"no such org"* from *"wrong
token for this org"* turns the invite endpoint into an org-enumeration oracle.

**Email-scoped invites are the stronger form and should be the default.** An unscoped invite link is
transferable — forwarded, pasted into a channel, screenshotted — and whoever redeems it becomes a
member. Binding the invite to an address means a forwarded link fails, which is the behaviour
someone sending it actually intended.

### The bootstrap is the one exception, and must stay one

Invite-only has a chicken-and-egg problem: **someone must exist before anyone can be invited.**

The seed creates that first admin. That is the only account in the system created without an invite,
and it must be a **one-time operation** rather than a standing capability — a seeder that can run
twice is an unauthenticated account-creation endpoint wearing an operations script's clothes.

So bootstrap **refuses to run if any user already exists**, and says so rather than silently doing
nothing.

---

## Where this lands

| Phase | Work |
|-------|------|
| **1** | `seed --demo` through the real API · bootstrap-once admin · `registration_mode` defaulting to `invite_only` · invite create and redeem, audited |
| **2** | Demo data extended as agents arrive, so the sandbox has enriched content on first login |
| **7** | Invite **UI**, role management, resend and revoke — the surface, after the mechanism |

The mechanism is Phase 1 because **the alternative to invites is open registration**, and shipping
with open registration then closing it later means anyone who signed up in between is already inside.

## Requirements

- **FR-ONB-1** Seeding MUST use the same API path as real signup and ingestion. There MUST NOT be a
  fixture-only write path.
- **FR-ONB-2** The demo tenant MUST be ordinary data. No flag consulted in the data path; exclusion
  from reporting only.
- **FR-ONB-3** The demo tenant MUST be removable by the ordinary purge cascade.
- **FR-ONB-4** Demo credentials MUST be generated per deployment, shown once, and never fixed in the
  seeder.
- **FR-ONB-5** Seeding MUST be explicit and MUST NOT run by default.
- **FR-ONB-6** Sample data MUST be generated, never derived from or anonymised from real content.
- **FR-ONB-12** The demo MUST ship worked domains that answer real questions on first login,
  selected for mechanism coverage rather than industry breadth.
- **FR-ONB-13** Each demo domain MUST ship its configuration — memory types, data-type profile,
  model assignment — and saved queries that double as acceptance assertions.
- **FR-ONB-14** The test fixture and the demo tenant MUST be separate. Tests MUST NOT depend on live
  model output; the demo MUST run the real pipeline.
- **FR-ONB-15** Demo entities MUST use reserved names and documented fake identifier ranges, and
  demo records MUST carry a visible marker.
- **FR-ONB-16** The demo fixture MUST include a normalization failure, an item mid-enrichment, an
  item the demo member cannot see, and an inferred case member.
- **FR-ONB-17** Demo reset MUST use the ordinary purge cascade.
- **FR-ONB-7** `registration_mode` MUST default to `invite_only`.
- **FR-ONB-8** Invites MUST be single-use, expiring, org- and role-scoped, revocable, and audited on
  both creation and redemption.
- **FR-ONB-9** Invites SHOULD be bound to an email address by default.
- **FR-ONB-10** Invite validation MUST NOT disclose whether an organisation exists.
- **FR-ONB-11** Bootstrap MUST refuse to run when any user exists.
