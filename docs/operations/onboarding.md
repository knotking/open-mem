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

## What the demo tenant contains

Enough to exercise the pipeline, and no more:

| Fixture | Why it is there |
|---------|-----------------|
| One org, one project | The tenancy boundary every query is scoped to |
| **Two users** — an admin and a member | One-user demos never reveal ACL bugs |
| A `personal` and a `shared` connection | The two scopes that produce opposite ACL defaults |
| ~50 items across **four types** | Text, a document, a structured record, a chat thread — enough for the classification cascade to have work to do |
| One memory with a routing rule | Proves upsert on `memory_key` |
| One item in **two** memories | Proves effective expiry is computed, not stored |
| One case with two members | Identifier-join correlation |
| One item shared, one private | Makes an ACL failure visible instead of theoretical |

**The sample data is generated, never borrowed.** No scraped corpus, no anonymised real mail, no
customer document with the names changed. A fixture ships everywhere the software ships, and
anything real in it is a disclosure with a very long tail.

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
- **FR-ONB-6** Sample data MUST be generated, never derived from real content.
- **FR-ONB-7** `registration_mode` MUST default to `invite_only`.
- **FR-ONB-8** Invites MUST be single-use, expiring, org- and role-scoped, revocable, and audited on
  both creation and redemption.
- **FR-ONB-9** Invites SHOULD be bound to an email address by default.
- **FR-ONB-10** Invite validation MUST NOT disclose whether an organisation exists.
- **FR-ONB-11** Bootstrap MUST refuse to run when any user exists.
