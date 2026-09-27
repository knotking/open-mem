# Permission-aware retrieval: inherit each document's access from its source

**One sentence.** A document ingested from Confluence, Slack or Drive carries the
permissions that source gave it, and retrieval filters on them before ranking —
so a teammate asking a question never sees a page they could not open themselves.

---

## 1 · Most of this is already built, which changes the shape of the work

The comparison document calls this *"designed"* and Onyx's advantage. Reading the
code, it is further along than that:

| Piece | Where | State |
|---|---|---|
| Per-document principal list | `data_items.shared_with`, `access_level = 'restricted'` | **Built** |
| Pre-retrieval filtering on it | `acl.visibility_sql` — `shared_with ?| $principals` composed into the query | **Built** |
| Caller's principals, resolved per query | `Principal.acl_principals()` → `user:`, `org:`, `group:`, `project:` | **Built** |
| Externally-managed groups | `groups.managed_by IN ('manual','scim','idp_claim')` | **Schema exists**, nothing populates the external cases |
| ACL from the producing connection | `acl.acl_for_write`, connection scope as a ceiling | **Built** |

`visibility_sql` already says the important thing in a comment:

> *"Principals resolved at query time — so revocation takes effect now."*

So the retrieval half is done. **This feature is not "build permission-aware
retrieval". It is "populate `shared_with` from the source, and map source
identities onto principals."** That is a much smaller and much more honest
statement of the work, and it moves the difficulty to where it actually is.

## 2 · The blocker that has to be settled first

`crawling.py` is explicit, and it is a policy rather than an oversight:

```python
# Reported, never accepted. A crawler cannot widen access to the data
# it produces.
"acl": "inherited:personal",
```

Every crawled record is `private` to whoever made the crawler. That is safe, and
it is incompatible with source inheritance: a Confluence page whose space is
readable by twelve people cannot be represented as `private` to one.

**This is a decision, not an implementation detail, and it is the first thing to
get agreement on.** Three options:

| | Rule | Cost |
|---|---|---|
| A | A crawler may write `restricted` **only** with principals it fetched from the source; it may never write `org` or `public` | The narrow answer. Widening is impossible except to exactly the set the source named. |
| B | A crawler on a `shared` connection writes `org`, as any shared producer does | Simple, and wrong for the case this feature exists for — it makes a private Confluence space org-visible. |
| C | Leave the policy; carry source principals in metadata and filter separately | Two ACL systems. The second one is the one that leaks. |

**Recommend A.** It preserves the existing sentence — a crawler cannot *widen*
access — because `restricted` to the source's own principal set is not widening,
it is transcription. And it fails closed: a connector that cannot fetch
permissions writes `private`, exactly as today.

## 3 · The part that is genuinely hard

Not the storage, and not the query. **Mapping a source identity to a principal.**

Confluence says a page is readable by group `engineering-all` and user
`557058:aa3f…`. Neither means anything to `acl_principals()`, which speaks
`user:usr_01J…` and `group:grp_01J…`. Getting this wrong in the permissive
direction is a leak, and it is silent.

Three sub-problems, in increasing difficulty:

1. **Users.** Usually resolvable by verified email. `users` has one; most
   providers expose one. The failure mode is a source account with no matching
   open-mem user — which must resolve to *nobody*, never to everybody.
2. **Groups.** `groups.managed_by = 'scim'` and `'idp_claim'` exist for this.
   A source group becomes a open-mem group with external provenance, and
   membership is synced rather than asserted. Two sources naming a group
   `engineering` must not collide into one.
3. **Inherited and computed permissions.** A Drive file inherits from its folder;
   a Slack message from its channel; a Confluence page from its space with
   per-page overrides. Fetching the effective set is a per-connector problem and
   several providers make it expensive.

**An unresolvable principal is dropped, and the drop is recorded.** Silently
widening is the failure this whole feature exists to prevent; silently narrowing
produces a document nobody can find, which is at least visible as a complaint.

## 4 · Staleness, which is where this feature usually rots

Permissions change after the sync. The two directions are not symmetric:

- **A stale allow is a leak.** Someone removed from a space keeps reading it.
- **A stale deny is an annoyance.** Someone added cannot see it yet.

So the re-sync policy is not "eventually" — it needs a bound the deployment can
state, and a query-time check on the *caller's* group membership (already
resolved per query, so removal from a open-mem group is instant). What is not
instant is removal at the source, and the honest answer is a documented sync
interval rather than a claim of live enforcement.

**Say what is true on the screen.** A memory whose source permissions were last
synced eleven days ago should say so where somebody can see it, exactly as the
crawler screen shows staleness today.

## 5 · Implementation

### Step 1 — the policy, and the ACL seam
- `CrawlerConfig` gains `access` restricted to `restricted` plus principals, or
  nothing. `acl_for_write` already treats a producer with no connection as
  unrestricted, so this is a narrowing of what a crawler may say, not a widening.
- The `"acl": "inherited:personal"` report becomes the truth of the new rule.

### Step 2 — identity mapping
- `source_identities` table: `(org_id, provider, external_id, kind, principal)`.
  Explicit and inspectable, because every leak in this feature is a wrong row here.
- Resolution by verified email for users; groups created with
  `managed_by = 'scim'` and namespaced per provider so two `engineering` groups
  from two systems stay distinct.
- Unresolved identities recorded, not dropped silently — a count on the screen.

### Step 3 — fetch, one connector at a time
Start with **one**, end to end, before generalising: **Confluence** (space plus
page restrictions, a documented API, and the case the comparison names). Then
Slack channel membership, then Drive.

The connector catalog already carries per-provider knowledge as data; permissions
belong there too rather than in code — a `permissions` block beside `template`.

### Step 4 — write and filter
- The crawler writes `restricted` with the mapped principals.
- **Retrieval needs no change.** Confirm that rather than assume it.

### Step 5 — the screen
Per the console standard: where a memory's permissions came from, when they were
last synced, and how many identities did not resolve.

## 6 · Tests

The ones that matter are all about the permissive direction:

1. A document restricted to group A is **not** returned to a member of group B —
   through `retrieve`, not by inspecting a row.
2. An unresolvable source identity resolves to **nobody**, never to `org`.
3. A connector that cannot fetch permissions writes `private` and says so.
4. Removing a user from a open-mem group takes effect on the **next query**, with
   no re-index — the property `acl_principals()` already claims.
5. Two providers with a group of the same name do not collide.
6. A crawler cannot write `org` or `public` under the new rule, even if asked.
7. The end-to-end: seed a Confluence-shaped fake with two spaces and two users;
   each sees exactly their own space through `ask`.

`tools/fake_sources.py` is the precedent for (7) — a simulator that speaks the
permission shape, since the interesting failures cannot be found by reading.

## 7 · What this does not do

- **Live enforcement.** Permissions are synced, not checked at read time against
  the source. Claiming otherwise would be false, and the interval belongs on the
  screen.
- **Every connector.** One, properly, then the catalog pattern.
- **Row-level permissions inside a record.** A page is the unit.

## 8 · Open questions

1. **Policy A confirmed?** Everything else depends on it.
2. **Confluence first, or Slack?** Confluence is the case the comparison names;
   Slack is likely the more common ask.
3. **What happens to already-crawled records?** They are `private` today. A
   backfill re-syncs them; the alternative is a corpus with two eras in it.
