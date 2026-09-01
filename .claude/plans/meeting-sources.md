# Plan — Zoom, Google Meet and Microsoft Teams meetings

**Requirement.** Pull what happens in meetings — who attended, what was said,
what was decided — into the corpus, so it correlates with the project it is
about.

Status: **the credential-independent core is shipped** (31 Aug 2026) — steps 2,
3 and 6. The three provider integrations (1, 4, 5) are not, and are the part
that needs OAuth apps nobody here can create.

Two corrections the build made to this plan:

- **§5's caveat is obsolete.** TTL is enforced now — the sweep landed the same
  day — so the `meeting` type's ninety days with `archive` is a policy that
  runs rather than one that is declared.
- **The attendee ACL did not need the providers.** It is derived in the webhook
  path from an `attendees_path` in the producer's mapping, so Zoom, Meet and
  Teams differ only in where their attendee list sits and what the key is
  called — configuration rather than three code paths, and a fourth provider
  works on the day it arrives. What remains genuinely provider-specific is the
  `Pending` recording reference and its fetch, which is step 1.

---

## 1 · Yes, all three allow it. They differ in shape, not in whether.

| | How you learn a meeting happened | What you can fetch | Auth |
|---|---|---|---|
| **Zoom** | `recording.completed` webhook — **already a provider here** | `/v2/meetings/{id}/recordings` → the `TRANSCRIPT` file (VTT) | Server-to-Server OAuth = `client_credentials`, **already supported** |
| **Google Meet** | Calendar event, or poll `conferenceRecords` | Meet API v2 `conferenceRecords.transcripts.entries` — **speaker-attributed, already structured** | Service account with domain delegation = `google_service_account`, **already supported** |
| **Microsoft Teams** | Graph `callRecords`, or the calendar | `/onlineMeetings/{id}/transcripts` | `client_credentials`, **already supported** — plus an application access policy |

**Every auth style needed is one this system already speaks.** Nothing here
needs a new credential mechanism, which is the usual reason a connector stalls.

Google Meet is the best of the three to build against: its transcript endpoint
returns **entries with a speaker per line**, where Zoom returns a VTT blob and
Teams returns a file to parse. Speaker attribution is what turns a transcript
into edges rather than text.

## 2 · The mechanism is already built, and it is `Pending`

A meeting webhook says *a recording exists* — it does not carry the recording.
That is precisely what `Pending` is for, and the crawler already uses it for the
same reason:

> *"A listing found a reference, not a document. The bytes are a second request
> needing the same credential, so it names the connection and the fetch worker
> makes it — which is where the byte cap and the blob store already are."*

So the flow needs no new worker:

```
recording.completed  →  webhook maps to an item with Pending{provider, resource_id, connection}
                     →  W2 fetch pulls the transcript with the stored credential
                     →  parse → enrich → entities → the graph
```

The one genuinely new part is a **transcript parser** — VTT and Teams' format
into speaker-attributed text. `parsers.py` already handles 54 formats; this is
one more, and Meet needs none because its API returns structure.

## 3 · The attendee list is the ACL

This is the part that makes meetings different from every other source in the
catalogue, and getting it wrong is the failure that matters.

A Jira issue's visibility follows the **connection scope** — a shared connection
produces org-visible items, which is right for a ticket. **A meeting transcript
must not.** Four people in a room did not publish to the company, and a
performance conversation ingested at `org` is a serious disclosure.

`acl.py` already has the shape: `access_level: restricted` with `shared_with[]`,
and `acl_for_write` accepts a requested level and principals. So a meeting is
written as **`restricted` to its attendees**, resolved to internal user ids.

Two consequences worth stating before building:

- **An external attendee has no principal here.** A meeting with a customer
  resolves to the internal attendees only, which is the conservative direction
  and the right one.
- **A summary of several meetings takes the strictest of them** —
  `acl.strictest` already does this, and it means a weekly digest across four
  meetings is visible only to whoever was in all four. That will surprise
  people. It is also correct.

> **Verify before building:** `acl_for_write` seals the ACL before any
> customizable phase, and the interaction between a `shared` connection scope
> and a *narrower* requested level needs checking. Requesting **more** restrictive
> than the connection must be honoured; requesting broader must not.

## 4 · Two objects, not one

A meeting produces two things and conflating them loses the more useful one:

| | Is | Carries |
|---|---|---|
| **The meeting** | An event | Title, time, attendees, the project it names |
| **The transcript** | A document | What was said, by whom |

Write both, link the transcript to the meeting by `external_id`, and put the
meeting in a **case** if its title or invite names a project key. That is the
existing identifier join — `PROJ-123` in a calendar title correlates the same
way it does in a Slack message.

## 5 · Retention is not optional here

Meeting transcripts are the most sensitive thing in this catalogue — arguably
more than Workday, because a transcript is unedited speech and nobody reviewed
it before it was stored.

- **A `meeting` memory type with a TTL, not `default`.** `memory_types` already
  carries `ttl_seconds` and `on_expiry`; ninety days with `archive` is a
  defensible default and *keeping forever* is not.
- **Recording consent is jurisdictional** and is not a thing this system can
  verify. It can record that the source said a recording existed, which is the
  honest limit.
- **TTL is still not enforced** (see `compaction.md` §Not built), so a retention
  policy declared today does not run. Say so rather than implying it does.

## 6 · What it unlocks

Closes **UC12 Meeting Intelligence**, which the catalog marks *"Designed —
media gated on a v1 decision"*.

And for project signals, one that nothing else can compute:

> **Meeting-to-action leakage** — a decision was made in a meeting and no
> tracked item exists for it. It needs the transcript on one side and Jira on
> the other, correlated to the same case. No single tool can see both.

Two more that fall out: **who decided what** (speaker attribution → `entity_edges`)
and **attendance drift** (the people in the room stopped matching the people on
the project).

## 7 · Implementation steps

1. **Zoom first** — the webhook provider exists, so this is a mapping plus a
   Pending, and it proves the whole path with the least new surface.
2. **A VTT parser** in `parsers.py`, speaker-attributed.
3. **Attendee ACL** — resolve attendees to principals, write `restricted`;
   verify the §3 interaction first.
4. **Google Meet** — a crawler over `conferenceRecords`, incremental on
   `start_time`, transcripts fetched as structured entries. No parser needed.
5. **Teams** — Graph `onlineMeetings/transcripts`; note the application access
   policy, which is a tenant-admin step and the usual place this stalls.
6. **A `meeting` memory type** with a TTL, and the case correlation from §4.

Steps 1–3 are one vertical slice and worth stopping at to look at a real
transcript before building the other two.

## 8 · Open questions

1. **Recordings as well as transcripts?** The transcript is the signal; the
   media is bytes. `MEDIA_INTERPRETATION` and the multimodal path exist, so
   audio *could* be transcribed here rather than fetched — which matters where a
   provider gives recordings but no transcript.
2. **Does a meeting become a `case`, or join one?** A recurring project standup
   is arguably a subject of its own. My assumption: it joins the project's case,
   and a series is a memory rather than a case.
3. **What happens to a meeting with no project reference?** Most meetings name
   no key. They still belong in the corpus, but they correlate to nothing — and
   a large body of uncorrelated transcripts is exactly the pile that makes
   retrieval worse rather than better.
