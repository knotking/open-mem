# Auth & Credentials

## The requirement

Login/password **and** API keys, on a pluggable identity layer.

## Decided: Firebase for login, keys at the gateway, married on identity

| Path | Mechanism |
|------|-----------|
| **Login / password** | **Firebase Auth** |
| **API keys** | Validated at the **API gateway**, created by users themselves |
| **Inbound (webhooks)** | The **same user-created keys**, configured onto a producer |

The three converge on one canonical identity — that is the marriage, and it happens in the
`identities` table below rather than at the edge.

### Consequence 1: Firebase and air-gap are incompatible

Firebase is a hosted service. An air-gapped deployment cannot reach its JWKS endpoint, so token
verification fails and nobody can log in.

This does **not** invalidate the air-gap claim — it means the claim belongs to the **local variant
only**, served by a different verifier behind the same seam:

| Variant | Login verifier |
|---------|----------------|
| Cloud, GKE | Firebase (RS256, Google JWKS) |
| **Local / air-gapped** | **Local password** (Argon2id, our own signing key) |

The `TokenVerifier` seam is what makes this two implementations rather than two products. It is
also why the seam is Phase 1 work even though Firebase does not arrive until later: build it now
and local costs an implementation; skip it and local costs a fork.

**What must be stated publicly:** air-gapped operation is a self-hosted capability, not a property
of the hosted product.

### Consequence 2: gateway API keys are not per-end-user keys

A managed API gateway validates **platform-level** API keys — created in the cloud project, bounded
in number, and not something an end user mints for themselves. They are the wrong primitive for
"every user creates their own key", and they do not scale to one per tenant.

Two ways to reconcile that with wanting validation at the edge:

| Option | How | Trade |
|--------|-----|-------|
| **A · Key exchange** *(recommended)* | User key → short-lived JWT from a token endpoint → gateway validates it against **our** JWKS, same as it validates Firebase | One verification mechanism at the edge, two ways to obtain a token. Costs one round trip, cacheable for the token's lifetime |
| **B · Pass-through** | Gateway handles routing, TLS and coarse rate limiting; the application validates `md_*` keys | Simpler, no exchange — but the gateway is no longer doing authentication, only transport |

**Option A is the one that actually marries them.** Both Firebase login and a user API key end up
as a JWT the gateway verifies against a JWKS, so the backend has exactly one code path for "who is
this" and the gateway has exactly one for "is this valid".

Under A the gateway still enforces its own platform key for coarse abuse control at the edge. That
is a different tier from user identity and should not be confused with it.

## Identity is where they marry

Whatever validated the credential, everything resolves to one canonical internal user:

```
users                          ← canonical, internal, never changes
  user_id                        (existing UUIDs preserved)

identities                     ← many-to-one
  (provider, external_id) → user_id
  provider: firebase | local | apikey | saml
  UNIQUE(provider, external_id)

credentials                    ← local password auth only
  user_id, password_hash (Argon2id)

api_keys                       ← user-created
  key_id, user_id, org_id, project_id
  key_hash                       SHA-256; high-entropy token, no slow KDF needed
  prefix                         for display; the key itself is never re-shown
  capabilities                   data:read · data:write · config:write · admin:*
  expires_at, last_used_at, revoked_at
```

A Firebase login and an API key belonging to the same person land on the same `user_id`, with the
same ACLs and the same tenancy scope. The difference is only what the credential is *permitted* to
do — which is the capability scope, not the identity.

**Firebase UIDs are 28-character strings and existing IDs are UUIDs.** Making `identities` the
permanent design rather than migration scaffolding is what turns that from a rewrite into inserting
rows.

## Inbound uses the same keys — with a caveat

A user-created key can be configured onto an inbound producer, so one credential mechanism serves
both directions.

The caveat is that **not every provider can present one.** Webhook senders differ:

| Provider capability | Producer auth method | Examples |
|--------------------|---------------------|----------|
| Sends custom headers | **`api_key`** — the user's own key | Generic webhooks, most internal systems, ETL |
| Signs the payload | `signature` — shared secret, HMAC verified | Slack, Stripe, GitHub |
| Neither — just POSTs | `url_secret` — the `whk_<ulid>` path is the credential | Simple integrations, legacy systems |

So the producer record declares it:

```
Producer
  ...
  inbound_auth   api_key | signature | url_secret | none
  api_key_id     ← when inbound_auth = api_key
  signing_secret ← when inbound_auth = signature
```

**Prefer `api_key` wherever the provider supports it** — it is revocable per key, attributable to a
user, capability-scoped, and shows up in `last_used_at`. A URL secret is none of those: revoking it
means re-registering the endpoint with the provider, and it leaks through logs and referrers.

Where signature verification is available it should be used **in addition**, not instead — it
authenticates the *payload*, which a bearer credential does not.

## Password auth requirements

| Concern | Requirement |
|---------|-------------|
| Hashing | **Argon2id** (or bcrypt cost ≥12). Never SHA-family |
| Brute force | Per-account **and** per-IP rate limiting, exponential lockout |
| Reset flow | Single-use, short-TTL, side-effect-free tokens |
| Verification | Email confirmation before first ingest |
| Enumeration | Identical response for unknown vs wrong password |
| Sessions | Short access token + revocable refresh token; **revocation list** — pure stateless JWT cannot log anyone out |
| MFA | TOTP — an expectation at team scale |
| Policy | Length-first, breach-list check where available |

Most of this comes free with hosted auth and must be **built** for local. That asymmetry is the
real cost of the air-gapped path.

## API keys under multi-tenancy

Today `md_*` binds to one user. A team system needs scope:

| Key type | Acts as | Use |
|----------|---------|-----|
| **User key** | that user, their ACLs | personal scripts, MCP, SDK |
| **Project key** | project service identity | CI, connectors, host-SaaS |
| **Org key** | org service identity | admin automation |

Required properties, several of which are gaps:

- **Hashed at rest.** Store a hash, indexed; keep a display prefix separately. "O(1) lookup" reads
  like a lookup on key value — if plaintext, one read exposes every tenant.
- **Membership-coupled.** Leaving the org must immediately revoke org access, or offboarding leaks.
- **Bounded.** Expiry, rotation with overlap, revocation, `last_used_at`.
- **Never in a browser.** Enforced, not documented.
- **Capability-scoped** — see [api.md](../api.md).

## Retire the global API key

`API_KEY` grants unscoped access with no `user_id` and bypasses all access control. Tolerable
single-tenant; in a team system with per-item privacy it is a master key that voids every ACL.
Anything that logs "who did this" as *nobody* is incompatible with the privacy model.

## Where credentials live — and five problems

| Class | Encryption | Stored in |
|-------|-----------|-----------|
| OAuth / integration | AES-256-GCM | credential broker's own database |
| AI provider keys | symmetric | `{user_id}/engines/{id}.json` — **the blob store** |
| `md_*` API keys | unspecified | record store, "O(1) lookup" |
| Global `API_KEY` | none | environment variable |
| Webhook signing secrets | unspecified | `webhooks` table |
| Infra credentials | none | orchestrator secrets |

1. **Encryption fails open.** Documented behaviour: if encryption is unavailable, keys are stored
   as plain text with a warning. At 1,000 orgs that is a breach with a log line. **Must fail
   closed.**
2. **One master key for all tenants.** Compromise is total. Needs envelope encryption — KEK in a
   KMS, per-tenant DEKs.
3. **"Set once, never rotate"** is documented for the broker encryption key. An operational dead
   end that fails rotation requirements.
4. **Secrets share a blast radius with user data** — encrypted provider keys sit in the same blob
   store as ingested content.
5. **`md_*` storage is unspecified.** If plaintext, one read yields every tenant's credentials.

> Where each of these physically lives per deployment variant — and why Secret Manager and KMS are
> not interchangeable — is in
> [operations/deployment-variants.md](../operations/deployment-variants.md).

## The fix is symmetry

Integration credentials already have the right pattern — a proxy injects them so the caller never
holds them. AI provider credentials do the opposite: workers fetch decrypted keys over the network
and cache them for minutes.

```
integration creds  →  /proxy/{provider}    →  worker never holds  ✓ exists
AI provider creds  →  /llm-proxy/{engine}  →  worker never holds  ✗ proposed
```

Mirror the pattern and no worker holds a secret of either class.

## Sequence

1. Identity abstraction + `identities` table — no behaviour change, unblocks everything
2. Asymmetric signing — removes the forge-anywhere weakness
3. Local password auth — Argon2id, lockout, reset, verification
4. API key hardening — hashing, scoping, membership coupling
5. Hosted provider — now just another implementation
6. Retire the global key → scoped platform credentials
7. MFA, then SAML/OIDC when enterprise demands it
