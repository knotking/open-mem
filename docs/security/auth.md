# Auth & Credentials

## The requirement

Login/password **and** API keys, on a pluggable identity layer.

## A conflict to resolve

A hosted identity provider is a managed external service. The product pillar promises full
offline and air-gapped capability — and an air-gapped deployment cannot reach an external JWKS
endpoint, so login simply fails.

Auth has to be **pluggable by deployment posture**:

| Posture | Identity provider | Why |
|---------|------------------|-----|
| Hosted / multi-tenant | Managed hosted auth | managed MFA, recovery, abuse handling, scale |
| Self-hosted / air-gapped | **Local password auth** | zero external dependency — the pillar |
| Enterprise (later) | SAML / OIDC | expected at team scale |

## The blocker

Token verification is hardcoded at two sites in the API, both doing **HS256 with a shared
secret**. Two problems:

1. **No seam.** A hosted provider needs RS256 against rotating JWKS with `iss`/`aud` checks. Every
   new IdP means editing both call sites.
2. **Symmetric signing is a weakness in itself.** Any service holding the secret to *verify* can
   also **mint** tokens — gateway, workers and MCP server all become token forgers. Asymmetric
   signing splits that: private key signs in one place, everything else holds only the public key.

## Identity model — which also solves the migration

Hosted-provider UIDs are opaque strings; existing IDs are UUIDs. Make the mapping table the
**permanent design**, not migration scaffolding:

```
users                          ← canonical, internal, never changes
  user_id                        (existing UUIDs preserved)

identities                     ← many-to-one
  (provider, external_id) → user_id
  provider: local | hosted | google | saml
  UNIQUE(provider, external_id)

credentials                    ← local password auth only
  user_id, password_hash (Argon2id), updated_at
```

One user, many identities. Migration becomes *inserting rows*; a user can log in by password
on-prem and hosted auth in cloud; adding SAML later touches no existing data. Making the IdP's ID
the primary key is what forces painful migrations.

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
