# Access Model — Principals, Sharing and Settings

## The rename that has to happen first

Today `public` means *"any authenticated user in the organization."* That is **internal**, not
public. Once genuine external sharing exists, the same word means two things and someone will make
a document world-readable believing they made it team-readable.

| Old | New | Means |
|-----|-----|-------|
| `private` | `private` | Owner only — default |
| `shared` | `shared` | Explicit principal list |
| **`public`** | **`org`** | Everyone in the organization |
| — | **`public`** | **Genuinely external, via a share link** |
| `restricted` | `restricted` | Principal list, owner excluded |

Rename before the second meaning exists. Afterwards it is a migration against a field people have
already reasoned about incorrectly.

## Principals, not user IDs

`shared_with` currently holds user identifiers. That does not survive contact with teams: every
membership change requires rewriting every shared item, and it silently fails to revoke when
someone leaves.

Share with a **principal**:

```
principal = user:<id> | group:<id> | project:<id> | org:<id> | public
```

Resolution happens at query time, so a group membership change takes effect immediately and
everywhere — including revocation, which is the direction that matters.

### Groups

A named set of members within an organization. Sharing with *engineering* rather than enumerating
eleven people is the difference between an access model people use correctly and one they route
around.

```
Group
  group_id     grp_<ulid>
  org_id
  name
  members      user_id[]          — direct
  managed_by   manual | scim | idp_claim
```

`managed_by` matters later: enterprise expects groups to arrive from the identity provider rather
than being maintained twice.

## Public sharing

Genuinely external sharing is the feature most likely to cause an accidental disclosure, so it
carries controls the others do not.

```
ShareLink
  share_id     shr_<ulid>
  data_id | case_id
  created_by, created_at
  expires_at            default: required, not optional
  password              optional
  revoked_at
  access_count, last_accessed_at
```

| Control | Behaviour |
|---------|-----------|
| **Org policy gate** | Public sharing is **disabled by default at org level**. An admin enables it; some orgs never will |
| **Expiry required** | A link with no expiry is a permanent disclosure nobody revisits |
| **Explicit confirmation** | The UI states plainly that the item becomes readable by anyone with the link |
| **Revocable** | Immediately, and revocation is audited |
| **Inventory** | Owner and admin can both list *everything currently shared publicly* — the view that catches the mistake made six months ago |
| **Audited** | Creation, each access, and revocation |

### Derived artifacts do not follow automatically

Sharing a document publicly must **not** publish its summary, its extracted claims, its entities or
its graph facts.

This is the derived-artifact ACL rule ([indexes](../retrieval/indexes.md)) meeting sharing: a
derived artifact carries the ACL of its **most restrictive source**, and a share widens the source
only. A summary spanning a public document and two private ones stays private — otherwise sharing
one item leaks two.

The practical consequence: a public share exposes the item and, optionally and explicitly, a
purpose-built public rendering. Never the internal derived layer.

## The admin who is also a user

One human, two modes — and conflating them is how admin tooling becomes a privacy hole.

```
identity        a normal user, in an org, with normal data
    +
platform grant  platform:read | platform:admin
```

### System view shows metadata, not content

| Admin can see | Admin cannot see |
|---------------|------------------|
| System health, queue depth, error rates | Item content |
| Org and project inventory, counts, storage | Search results across tenants |
| Usage, quota and budget consumption | Summaries, entities, extracted claims |
| Producer health, connection status | Case contents |
| Audit records | — |
| Feature flags, global limits | — |

**A platform admin does not get tenant data by default.** Support tooling that shows customer
content by default is a privacy violation that arrives disguised as a feature request.

Where content access is genuinely required — an escalated support case, a clinical emergency — it
goes through **break-glass**: explicit justification, scoped to a subject, time-boxed,
notified to the data owner, and written to the audit store as its own event type.

### Mode is explicit and separately audited

The same human acting as a tenant user and acting as a platform admin produces **different audit
records**. The session carries the active mode; switching is an auditable event. "Was this
read done as the user or as the operator?" must have an answer.

### Platform grants are not an org role

`owner`, `admin`, `member`, `viewer` are org-scoped. `platform:*` is orthogonal — a platform admin
holds no elevated rights inside any org they are not a member of.

This is also what finally retires the global unscoped `API_KEY`: system operations get a real
identity with real scopes, and every action is attributable.

## Settings taxonomy

Four levels, with the precedence and locking model already used for normalization and model
configuration.

| Level | Owns | Set by |
|-------|------|--------|
| **Platform** | Storage backend, encryption keys, global limits, feature flags | operator |
| **Org** | Members, groups, roles, quotas, allowed providers, **sharing policy**, retention, connection defaults | owner/admin |
| **Project** | Defaults, normalization schemas, crawlers, producers | admin/member |
| **User** | Profile, password, MFA, API keys, own engines, own agent configs, default project, notifications | the user |

**Precedence:** user → project → org → platform, most specific wins, **except where an admin has
locked a setting.** A locked org setting cannot be overridden below it — that is how "only our
approved model providers" and "public sharing disabled" are enforced rather than suggested.

### Account settings, concretely

| Group | Contains |
|-------|----------|
| **Identity** | Email, display name, password change, MFA enrolment, linked identities |
| **Credentials** | API keys — create, list with prefix and `last_used_at`, rotate, revoke. Never re-displayed |
| **Workspace** | Default org and project, project switcher |
| **AI** | Engines, model assignments, agent configs — within org policy |
| **Connections** | Connected sources, **personal vs shared scope**, reauthorise, disconnect |
| **Sharing** | What I have shared, with whom, and **what is public** |
| **Privacy** | Export my data, delete my data, view my access history |
| **Notifications** | Connection failures, quota warnings, share access |

That last privacy group is worth building early even in thin form: a user who can see their own
access history is a user who can catch a problem you cannot.

## API and UI parity

**Everything settable in the UI is settable through the API**, at the same granularity and with the
same validation. The UI is a client of the API, never a privileged path.

Two consequences: an embedding host can build its own settings surface, and the settings surface is
testable without a browser.

Control-plane endpoints:

```
/organizations  /organizations/{id}/members  /groups  /projects
/users/me  /users/me/api-keys  /users/me/identities  /users/me/connections
/shares                    ← inventory, revoke
/settings/{scope}          ← get/set with lock state
/platform/health  /platform/orgs  /platform/usage  /platform/audit
/platform/breakglass       ← justification required
```

## Requirements

- **FR-ACC-1** The access level currently named `public` MUST be renamed `org`, and `public` MUST
  mean externally shared.
- **FR-ACC-2** `shared_with` MUST hold principals — user, group, project, org or public — resolved
  at query time.
- **FR-ACC-3** Groups MUST be a first-class primitive within an organization, and MUST support
  external management for later identity-provider integration.
- **FR-ACC-4** Public sharing MUST be disabled by default at org level and enabled explicitly.
- **FR-ACC-5** Share links MUST require an expiry, be revocable, and record creation, each access
  and revocation.
- **FR-ACC-6** Owners and admins MUST be able to list everything currently shared publicly.
- **FR-ACC-7** Sharing an item MUST NOT change the access level of artifacts derived from it.
- **FR-ACC-8** Platform grants MUST be orthogonal to org roles and MUST NOT confer rights within an
  organization the holder is not a member of.
- **FR-ACC-9** A platform admin MUST NOT have access to tenant content by default; content access
  MUST require break-glass with justification, scope, time limit and audit.
- **FR-ACC-10** Acting as a platform admin MUST be an explicit, separately audited mode.
- **FR-ACC-11** Settings MUST resolve user → project → org → platform, and an administrator MUST be
  able to **lock** a setting against override.
- **FR-ACC-12** Every setting available in the UI MUST be available through the API at equal
  granularity.
- **FR-ACC-13** Users MUST be able to view their own access history.
