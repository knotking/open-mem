"""The demo tenant.

This is not a convenience script beside the tests. [Phase 1 exits](../../docs/roadmap.md)
when an item is written into a project owned by an org with an access level,
found by scoped search, cited, audited, and the row records which model embedded
it -- and seeding a demo tenant is exactly that sequence. So a successful seed
**is** the end-to-end verification, and a failing one says which step broke
before anyone has logged in.

Which gives the rule that shapes the rest:

> **Seeding uses the same API a real client uses. There is no fixture path.**

A seed that inserts rows directly tests the seed. Whatever it proves is not
transferable, and it diverges the moment the real path changes. So the corpus
goes in through `POST /api/v1/write` with a registered producer, the project and
producers are created through their endpoints, the questions are asked through
`POST /api/v1/retrieve`, and the reset is the ordinary delete cascade.

**Three things are still created directly, and all three are the same gap.** An
organization, a connection and a second member's credential have no endpoint to
create them through -- orgs and keys because the control plane's admin half is a
later slice, connections because they are what an OAuth flow produces and that
does not exist. Each is marked below. They are the bootstrap, not the data path.

## The corpus is one worked domain, not assorted rows

Forty records about a single renewal, because the point is not volume. It is
that the questions at the bottom of this file **have real answers in it**, and
those questions double as the acceptance assertions: if *"what did we promise
Acme?"* stops returning the commitment, something broke.

## And it is obviously synthetic

Reserved names only, documented fake identifier ranges, a visible marker on
every record, and generated rather than anonymised -- anonymised real data is
real data that has been processed. The goal is a corpus nobody can mistake for
production data, including at a glance, in a screenshot, months later, by
someone who was not there when it was seeded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import asyncpg

from .auth import CONFIG_WRITE, DATA_READ, DATA_WRITE, issue_key
from .bootstrap import bootstrap_tenant, create_user
from .ids import new_id

# Reserved names, and identifier ranges documented as fake. A demo company that
# is a real company is a problem someone else did not agree to, and a
# realistic-format identifier can collide with a real one -- at which point a
# demo record that looks real may be treated as real.
DEMO_ORG_NAME = "Northwind Trading (demo)"
DEMO_PROJECT_NAME = "Acme renewal (demo)"
DEAL_ID = "DEAL-DEMO-4417"
ACCOUNT_ID = "ACCT-DEMO-ACME"
CASE_ID = "CASE-DEMO-ACME-RENEWAL"

# The demo's two people. Reserved `.example` domain, and named here rather than
# inline so the reset removes exactly who the bootstrap created -- the two
# halves disagreeing is how a reset leaves a user behind and the next seed dies
# on a unique constraint.
ADMIN_EMAIL = "dana.whitfield@northwind.example"
MEMBER_EMAIL = "priya.raman@northwind.example"

# On every record, in the content itself rather than only in metadata, because
# the failure this guards against is a screenshot.
MARKER = "[DEMO — synthetic data, generated, not real]"

# Everything is dated relative to one reference point so the corpus reads as a
# coherent few months whenever it is seeded, rather than as a pile of records
# that all arrived the day someone ran this.
REFERENCE = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)


@dataclass
class Item:
    external_id: str
    text: str
    data_type: str
    source_type: str
    days_ago: int
    tags: list[str] = field(default_factory=list)
    identifiers: list[str] = field(default_factory=list)
    # Only the first record of the renewal asserts the case. Everything else
    # joins it by identifier, which is what makes the asserted/inferred
    # distinction visible rather than described.
    asserts_case: bool = False
    enrich: bool = True
    private: bool = False

    def payload(self) -> dict:
        body: dict = {
            "external_id": self.external_id,
            "content": {"kind": "inline", "text": f"{MARKER}\n\n{self.text.strip()}"},
            "data_type": self.data_type,
            "source_type": self.source_type,
            "event_time": (REFERENCE - timedelta(days=self.days_ago)).isoformat(),
            "tags": [*self.tags, "demo"],
            "identifiers": self.identifiers or [DEAL_ID],
        }
        if self.asserts_case:
            body["case"] = {"external_id": CASE_ID, "case_type": "opportunity"}
        if self.private:
            # Narrowing, never widening. This is the record a second member
            # cannot see, and the seed verifies that rather than claiming it.
            body["access"] = {"level": "private"}
        return body


def _corpus() -> list[Item]:
    """One renewal, told across the sources it actually arrives on.

    The facts are planted deliberately: two commitments with owners and dates, a
    renewal date and value, a named risk, and a competitor. Those are what the
    saved questions ask about, so the corpus and the assertions cannot drift
    apart without the seed failing.
    """
    return [
        # --- the record that asserts the case ---------------------------------
        Item(
            "crm-opportunity-4417",
            f"""Opportunity {DEAL_ID} — Acme Corporation renewal.
Stage: Negotiation. Renewal date: 1 October 2026. Annual value: $148,000.
Owner: Dana Whitfield. Account: {ACCOUNT_ID}.
Previous term: 1 October 2025 to 30 September 2026 at $131,000.
Seats: 340 contracted, 372 in use.""",
            "crm_record", "crm", 61, ["renewal", "opportunity"], asserts_case=True,
        ),

        # --- the commitments the demo questions have to find ------------------
        Item(
            "email-sso-commitment",
            """From: Dana Whitfield <dana.whitfield@northwind.example>
To: Marcus Lee <marcus.lee@acme.example>
Subject: Re: SSO before we sign

Marcus — confirming what we agreed on the call. We will deliver SAML
single sign-on to Acme by 30 September 2026, ahead of the renewal date.
I own this commitment and I will send you a written status every second
Friday until it ships.

This is a firm commitment, not a roadmap item.""",
            "email", "gmail", 44, ["commitment", "sso"],
        ),
        Item(
            "email-overage-waiver",
            """From: Priya Raman <priya.raman@northwind.example>
To: Marcus Lee <marcus.lee@acme.example>
Subject: August overage

Marcus — we have agreed to waive the August overage charge of $4,200
covering the 32 seats above your contracted 340. I own this and it will
be credited on the invoice dated 31 August 2026.

We should talk about right-sizing the seat count at renewal rather than
doing this again in September.""",
            "email", "gmail", 21, ["commitment", "billing"],
        ),

        # --- the risk -------------------------------------------------------
        Item(
            "call-security-review-risk",
            """Call notes — Acme security review, 14 July.
Attendees: Dana Whitfield, Priya Raman, Marcus Lee, Sofia Alvarez (Acme security).

Sofia raised data residency as a blocking concern. Acme's own customers in
the EU require that records never leave the EU, and our current deployment
region is us-central1. Sofia was explicit that this is a risk to the renewal,
not a preference.

Action: confirm whether a EU region deployment is possible before the
30 September commitment date. Nobody in the room could answer.""",
            "call_notes", "manual", 37, ["risk", "security", "residency"],
        ),
        Item(
            "slack-residency-thread",
            """#acme-renewal

dana: has anyone got an answer on EU data residency for Acme? Sofia is
  blocking the security sign-off on it
priya: infra says a EU region is possible but it is a quarter of work, not
  a config change
dana: that is after the renewal date. I need to tell Marcus something real
priya: tell him the truth. the alternative is committing to a date we
  invented in a slack thread""",
            "chat", "slack", 35, ["risk", "residency"],
        ),

        # --- the competitor --------------------------------------------------
        Item(
            "call-competitive-contoso",
            """Call notes — Marcus Lee, 28 July.

Competitor named: Contoso. Marcus mentioned unprompted that Acme
procurement has asked him to run a comparison against Contoso before
signing a three-year term. Contoso is the only competitor in the
evaluation and we have not been asked about anyone else.

He was straightforward that this is procurement policy above a $100,000
threshold rather than dissatisfaction. He also asked for our data
residency position in writing, which is the same concern Sofia raised on
14 July -- and the one place a competitor could beat us on paper.""",
            "call_notes", "manual", 23, ["competitive", "procurement"],
        ),

        # --- the record a second member must not see -------------------------
        Item(
            "personal-note-dana",
            """Private note — Dana Whitfield.

Marcus told me off the record that Acme's budget for next year is being
cut by about 12% and that he would rather reduce seats than change vendor.
He asked me not to repeat this to procurement.

Do not share. This is in my own notes for a reason.""",
            "note", "manual", 19, ["private"], private=True,
        ),

        # --- the un-enriched tier -------------------------------------------
        # Written with enrich off. Recording is cheap and synchronous; spending
        # is opt-in. These stay short of the top of the staircase on purpose, so
        # the readiness model is visible in a steady state rather than only
        # during seeding.
        Item(
            "usage-daily-2026-08-17",
            "Daily usage export. Seats active 372. API calls 1,284,551. "
            "Storage 412 GB. Peak concurrency 91.",
            "usage_export", "telemetry", 3, ["usage"], enrich=False,
        ),
        Item(
            "usage-daily-2026-08-18",
            "Daily usage export. Seats active 369. API calls 1,190,204. "
            "Storage 414 GB. Peak concurrency 88.",
            "usage_export", "telemetry", 2, ["usage"], enrich=False,
        ),
        Item(
            "usage-daily-2026-08-19",
            "Daily usage export. Seats active 374. API calls 1,331,870. "
            "Storage 417 GB. Peak concurrency 96.",
            "usage_export", "telemetry", 1, ["usage"], enrich=False,
        ),

        # --- the rest of the account history ---------------------------------
        Item(
            "email-renewal-kickoff",
            """From: Dana Whitfield
To: Marcus Lee
Subject: Renewal planning for 1 October

Marcus — starting the renewal conversation early this year. Current term
ends 30 September. Happy to walk through usage before we talk numbers.""",
            "email", "gmail", 58, ["renewal"],
        ),
        Item(
            "meeting-qbr-july",
            """Quarterly business review — Acme, 2 July.

Adoption is up: 372 active seats against 340 contracted, and API volume has
roughly doubled year on year. Marcus called the ingestion reliability
"genuinely better than last year".

Open items: SSO, data residency, and the seat overage.""",
            "meeting_notes", "manual", 49, ["qbr"],
        ),
        Item(
            "ticket-4471-sso-blocker",
            """Support ticket 4471 — Acme Corporation.
Reported by: Marcus Lee.

"Our IT team will not approve rolling this out more widely until we can
put it behind our identity provider. Right now every new user is another
password to manage and our security team is counting them."

Priority: high. Linked to the SSO commitment.""",
            "ticket", "support", 47, ["sso", "support"],
        ),
        Item(
            "ticket-4488-slow-search",
            """Support ticket 4488 — Acme Corporation.
Reported by: an Acme analyst.

"Search just spins forever on our larger projects. It used to come back
straight away."

Investigation found the project had grown past the point where the old
index configuration performed. Resolved by reindexing.""",
            "ticket", "support", 40, ["support", "performance"],
        ),
        Item(
            "engineering-sso-plan",
            """SSO delivery plan — internal.

SAML 2.0 service-provider implementation, IdP-initiated and SP-initiated.
Estimated four weeks of engineering, starting 18 August, which lands
around 15 September and leaves two weeks of margin before the 30 September
commitment Dana made to Acme.

Risk: the same two engineers are on the EU region investigation.""",
            "document", "manual", 30, ["sso", "engineering"],
        ),
        Item(
            "email-status-sso-1",
            """From: Dana Whitfield
To: Marcus Lee
Subject: SSO status — first fortnightly update

As promised, a status every second Friday. SAML implementation started
18 August. On track for 30 September. Nothing to flag yet.""",
            "email", "gmail", 8, ["sso", "commitment"],
        ),
        Item(
            "contract-current-term",
            f"""Order form — Acme Corporation. Account {ACCOUNT_ID}.

Term: 1 October 2025 to 30 September 2026.
Contracted seats: 340. Annual fee: $131,000, paid annually in advance.
Overage: billed monthly at $131.25 per seat above the contracted count.
Governing terms: the Northwind master services agreement dated 4 June 2024.""",
            "contract", "manual", 320, ["contract"],
        ),
        Item(
            "invoice-august-overage",
            """Invoice NW-DEMO-20826, dated 31 August 2026.

Seat overage, August: 32 seats at $131.25 = $4,200.00.
Credit applied: -$4,200.00 (waiver agreed with Priya Raman).
Amount due: $0.00.""",
            "invoice", "billing", 0, ["billing"],
        ),
        Item(
            "slack-seat-growth",
            """#acme-renewal

priya: acme is at 372 seats against 340 contracted, third month running
dana: that is the renewal conversation making itself
priya: or it is three months of overage nobody has budgeted for. one of
  those is a better email than the other""",
            "chat", "slack", 26, ["usage", "renewal"],
        ),
        Item(
            "slack-champion-risk",
            """#acme-renewal

dana: marcus is our champion and he is the only one at acme who argues
  for us in a room we are not in
priya: what happens if he leaves
dana: then we are a line item in a procurement comparison. which is why
  the sso date matters more than it looks""",
            "chat", "slack", 15, ["risk", "champion"],
        ),
        Item(
            "email-procurement-intro",
            """From: procurement@acme.example
To: Dana Whitfield
Subject: Vendor comparison — renewal above threshold

Ms Whitfield — Acme procurement policy requires a documented comparison
for any renewal above $100,000. Please provide your security
questionnaire response and data residency statement by 12 September.""",
            "email", "gmail", 18, ["procurement", "residency"],
        ),
        Item(
            "document-security-questionnaire",
            """Security questionnaire response — draft.

Encryption at rest: yes, per-tenant keys.
Audit log: every read recorded, retained indefinitely.
Data residency: currently us-central1 only. EU region not available.
Subprocessors: listed in appendix A.

The residency line is the one procurement will stop on.""",
            "document", "manual", 12, ["security", "residency"],
        ),
        Item(
            "call-marcus-1on1-august",
            """Call notes — Marcus Lee, 11 August.

Marcus is supportive but wants the SSO date in writing again for his own
security team, and asked directly whether the EU region is real or
aspirational. Told him the honest answer: not before the renewal.

He did not react badly. He said he would rather hear that than a date we
would miss.""",
            "call_notes", "manual", 9, ["champion", "residency"],
        ),
        Item(
            "meeting-internal-deal-review",
            """Internal deal review — 13 August.

Position: $148,000 for a one-year renewal, or $139,000 per year on a
two-year term. Do not offer three years while the residency question is
open — a three-year term with an unresolved blocker is a three-year
argument.

Dana owns the SSO commitment. Priya owns the overage credit.""",
            "meeting_notes", "manual", 7, ["renewal", "pricing"],
        ),
        Item(
            "email-pricing-proposal",
            """From: Dana Whitfield
To: Marcus Lee
Subject: Renewal proposal

Two options attached. One year at $148,000, or two years at $139,000 per
year. Both include the 372 seats you are actually using rather than the
340 you are contracted for, so the overage stops.""",
            "email", "gmail", 5, ["renewal", "pricing"],
        ),
        Item(
            "ticket-4502-ingestion-lag",
            """Support ticket 4502 — Acme Corporation.

"Records from our warehouse connector are showing up hours late."

Root cause was a backlog on their own side rather than ours; their export
job had been failing silently for two days. Closed with an explanation and
a suggestion that they alert on export success rather than on failure.""",
            "ticket", "support", 33, ["support"],
        ),
        Item(
            "document-mutual-action-plan",
            """Mutual action plan — Acme renewal.

By 12 September: security questionnaire and residency statement to
  procurement (Dana).
By 30 September: SAML SSO delivered (Dana).
By 31 August: August overage credited (Priya).
By 1 October: signed order form.

Everything on this list has a name against it. An action plan without
owners is a wish list.""",
            "document", "manual", 16, ["plan", "commitment"],
        ),
        Item(
            "slack-residency-answer",
            """#acme-renewal

priya: infra confirmed. EU region is Q1 next year at the earliest
dana: then that is what marcus gets told. in writing, today
priya: agreed. the worst version of this is procurement finding out from
  contoso's questionnaire that we do not have it""",
            "chat", "slack", 11, ["residency", "risk"],
        ),
        Item(
            "email-residency-statement",
            """From: Dana Whitfield
To: procurement@acme.example
Subject: Data residency statement

To be direct: we do not offer an EU region today. Our roadmap has it in
the first quarter of next year and I will not offer a date sooner than
that, because I do not have one I believe.

Every other item in the questionnaire is answered in the attachment.""",
            "email", "gmail", 10, ["residency", "procurement"],
        ),
        Item(
            "call-sofia-followup",
            """Call notes — Sofia Alvarez (Acme security), 18 August.

Sofia acknowledged the residency statement and said the honesty helped
more than a hedged answer would have. Her position: she can sign off for
the coming year on the basis that Acme's EU customers are served from a
separate system today, and revisit at the next renewal.

This unblocks the security review.""",
            "call_notes", "manual", 2, ["security", "residency"],
        ),
        Item(
            "crm-activity-log",
            f"""Activity log — {DEAL_ID}.

61 days: opportunity created, stage Discovery.
49 days: QBR held, stage Qualification.
37 days: security review, risk logged.
23 days: competitive comparison requested, stage Negotiation.
7 days: internal deal review, pricing agreed.
2 days: security sign-off received.""",
            "crm_record", "crm", 2, ["renewal"],
        ),
        Item(
            "email-reference-request",
            """From: Marcus Lee
To: Dana Whitfield
Subject: Reference call

Would you be able to put me in touch with another customer of similar
size? Procurement asked and I would rather answer it than have them
ask you formally.""",
            "email", "gmail", 14, ["procurement"],
        ),
        Item(
            "document-adoption-summary",
            """Adoption summary — Acme, past twelve months.

Active seats: 210 to 372.
Monthly API calls: 640,000 to 1,290,000.
Projects in use: 4 to 19.
Departments: engineering only, to engineering, support, legal and finance.

The seat overage is not a billing problem. It is what growth looks like
before someone updates the contract.""",
            "document", "manual", 20, ["usage", "renewal"],
        ),
        Item(
            "slack-sso-progress",
            """#eng-sso

engineer: SP-initiated flow works end to end against the test IdP
engineer: IdP-initiated is the fiddly half, expect most of next week on it
dana: still comfortable with 30 september?
engineer: yes, with the margin we planned. ask me again if the EU region
  work pulls either of us off it""",
            "chat", "slack", 4, ["sso", "engineering"],
        ),
        Item(
            "ticket-4515-sso-question",
            """Support ticket 4515 — Acme Corporation.

"Can you confirm whether SSO will support our group mappings, or only
authentication?"

Answered: authentication at launch, group mapping in a later release.
Flagged to Dana in case it affects the commitment.""",
            "ticket", "support", 6, ["sso", "support"],
        ),
        Item(
            "email-marcus-thanks",
            """From: Marcus Lee
To: Dana Whitfield
Subject: Re: Data residency statement

Thank you for putting it plainly. I have forwarded it to procurement
with my recommendation. Sofia has signed off on her side.

Send me the one-year option and I will start the paperwork.""",
            "email", "gmail", 1, ["renewal", "champion"],
        ),
        Item(
            "meeting-forecast-review",
            f"""Forecast review — 19 August.

{DEAL_ID} moved to Commit. $148,000, one-year term, expected close
1 October. Champion is engaged, security has signed off, and the
competitive comparison is procurement policy rather than a real
evaluation.

Remaining risk is delivery: the SSO commitment is ours to miss.""",
            "meeting_notes", "manual", 1, ["forecast", "renewal"],
        ),
        Item(
            "document-renewal-checklist",
            """Renewal checklist — Acme.

[x] Usage reviewed
[x] Pricing approved internally
[x] Security questionnaire returned
[x] Residency statement sent
[x] Security sign-off received
[ ] Order form signed
[ ] SSO delivered""",
            "document", "manual", 0, ["plan", "renewal"],
        ),
        Item(
            "call-procurement-close",
            """Call notes — Acme procurement, 20 August.

Comparison complete. Procurement confirmed no objection on the one-year
term. They noted the residency gap in their record and asked that it be
revisited at the next renewal, which is a reasonable place to leave it.""",
            "call_notes", "manual", 0, ["procurement"],
        ),
        Item(
            "email-order-form-sent",
            f"""From: Dana Whitfield
To: Marcus Lee
Subject: Order form — {DEAL_ID}

Attached. One year, 1 October 2026 to 30 September 2027, 372 seats,
$148,000. The August overage credit is already applied.

The SSO date in the order form is the same 30 September I committed to
in July.""",
            "email", "gmail", 0, ["renewal", "contract"],
        ),
    ]


@dataclass(frozen=True)
class Question:
    """A saved query, and what it must find.

    A demo corpus with no questions attached is a corpus. The questions are what
    make it a demonstration -- and because each one names the record that
    answers it, they are also the acceptance assertions. If *"what did we
    promise Acme?"* stops returning the commitment, the seed fails rather than
    quietly producing a demo that no longer demonstrates anything.
    """

    question: str
    must_find: str


QUESTIONS = (
    Question("What did we promise Acme and who owns it?", "email-sso-commitment"),
    Question("Why is the Acme renewal at risk?", "call-security-review-risk"),
    Question("What is the renewal date and the annual value?", "crm-opportunity-4417"),
    Question("What did we agree about the August overage charge?", "email-overage-waiver"),
    Question("Which competitor is Acme comparing us against?", "call-competitive-contoso"),
)


@dataclass
class Seeded:
    org_id: str
    project_id: str
    admin_email: str
    admin_key: str
    member_email: str
    member_key: str
    written: int
    enriched: int
    questions_passed: int
    private_item_hidden: bool
    case_members: dict


class SeedError(RuntimeError):
    """The seed did not produce a working demo. Says which step."""


async def already_seeded(pool: asyncpg.Pool) -> str | None:
    return await pool.fetchval(
        "SELECT org_id FROM organizations WHERE name = $1", DEMO_ORG_NAME
    )


async def _bootstrap(pool: asyncpg.Pool) -> tuple[object, str, str, str]:
    """The org, the admin, and a second member to test visibility against.

    Everything here is a direct write, and that is the boundary: there is no
    endpoint that creates an organization, no endpoint that issues a key to
    somebody other than the caller, and no endpoint that creates a connection --
    connections are what an OAuth flow produces, and that is not built. The data
    path below uses the API for all of it.

    Credentials are generated per deployment and printed once. A known demo user
    with a known password, present in every install, is a shipped default
    credential -- the failure mode that appears in breach write-ups more
    reliably than any other.
    """
    tenant = await bootstrap_tenant(
        pool,
        org_name=DEMO_ORG_NAME,
        project_name=DEMO_PROJECT_NAME,
        email=ADMIN_EMAIL,
        # Shared, so the corpus is visible to the whole project. The one private
        # record gets its own personal connection below.
        connection_scope="shared",
    )

    member_id = await create_user(pool, MEMBER_EMAIL)
    await pool.execute(
        "INSERT INTO memberships (user_id, org_id, role) VALUES ($1, $2, 'member')",
        member_id, tenant.org_id,
    )
    member_key = await issue_key(
        pool,
        user_id=member_id,
        org_id=tenant.org_id,
        project_id=tenant.project_id,
        # An ordinary member, deliberately. A demo user with escalated rights
        # demonstrates the wrong thing.
        capabilities=[DATA_READ, DATA_WRITE],
        name="demo-member",
    )

    personal_connection = new_id("conn")
    await pool.execute(
        """
        INSERT INTO connections (connection_id, org_id, project_id, user_id, provider, scope)
        VALUES ($1, $2, $3, $4, 'manual', 'personal')
        """,
        personal_connection, tenant.org_id, tenant.project_id, tenant.user_id,
    )
    return tenant, member_id, member_key, personal_connection


async def _write(client, key: str, producer_id: str, items: list[Item], *, enrich: bool) -> dict:
    """One batch, through the public write verb.

    The batch is the ordinary verb rather than a bulk endpoint -- `items[]` with
    a `207` from the first commit -- so the seed exercises exactly what an
    external ETL client would, including the per-item results a partial success
    returns.
    """
    response = await client.post(
        "/api/v1/write",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "producer_id": producer_id,
            "items": [item.payload() for item in items],
            "options": {"enrich": enrich},
        },
    )
    if response.status_code != 207:
        raise SeedError(
            f"write returned {response.status_code}, expected 207: {response.text[:400]}"
        )
    body = response.json()
    if body["failed"]:
        reasons = [r["error"] for r in body["results"] if r["status"] == "failed"]
        raise SeedError(f"{body['failed']} items failed: {reasons[:3]}")
    return body


async def _ask(client, key: str, project_id: str, query: str, *, limit: int = 10):
    response = await client.post(
        "/api/v1/retrieve",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "query": query,
            "filter": {"project_id": project_id},
            "match": ["vector", "lexical"],
            "limit": limit,
        },
    )
    if response.status_code != 200:
        raise SeedError(f"retrieve returned {response.status_code}: {response.text[:300]}")
    return response.json()


async def seed_demo(pool: asyncpg.Pool, client, *, drain) -> Seeded:
    """Create the demo tenant and prove it works.

    `drain` waits for the enrichment queue to settle. The single-domain seed
    enriches synchronously for a reason: the point is to finish in under a
    minute with a corpus that answers questions, and an asynchronous seed that
    returns before it is ready cannot verify itself.
    """
    existing = await already_seeded(pool)
    if existing is not None:
        raise SeedError(
            f"the demo org already exists ({existing}). "
            "Re-seed with --reset, which purges it through the ordinary cascade."
        )

    tenant, member_id, member_key, personal_connection = await _bootstrap(pool)
    admin = {"Authorization": f"Bearer {tenant.api_key}"}

    # From here on, everything is the public API.
    made = await client.post(
        "/api/v1/producers", headers=admin,
        json={"project_id": tenant.project_id, "type": "client",
              "connection_id": personal_connection},
    )
    if made.status_code != 200:
        raise SeedError(f"could not create the personal producer: {made.text[:300]}")
    personal_producer = made.json()["producer_id"]

    # A memory type the domain actually needs, shipped with the domain. A demo
    # containing only data shows what the product stores; one containing the
    # configuration that made the data useful shows how to use it, and
    # configuration is the part new users get wrong.
    await client.post(
        f"/api/v1/projects/{tenant.project_id}/memory-types", headers=admin,
        json={"name": "opportunity", "ttl_seconds": None, "on_expiry": "keep_members"},
    )

    # The case is created up front and told which identifier correlates into it.
    # Without that the only members it can ever have are the ones that named it
    # explicitly -- correlation matches an item's identifiers against the
    # *case's*, so a case with none is a case nothing can be inferred into.
    created = await client.put(
        "/api/v1/cases", headers=admin,
        json={
            "project_id": tenant.project_id,
            "case_type": "opportunity",
            "external_id": CASE_ID,
            "title": "Acme Corporation renewal (demo)",
            "identifiers": [DEAL_ID, ACCOUNT_ID],
        },
    )
    if created.status_code != 200:
        raise SeedError(f"could not create the case: {created.text[:300]}")

    corpus = _corpus()
    # The case-asserting record goes first and alone. Everything after it joins
    # the case by identifier rather than by assertion, which is what makes the
    # asserted/inferred split real rather than staged.
    asserted = [i for i in corpus if i.asserts_case]
    shared = [i for i in corpus if not i.asserts_case and not i.private and i.enrich]
    unenriched = [i for i in corpus if not i.private and not i.enrich]
    private = [i for i in corpus if i.private]

    written = 0
    for batch in (asserted, *[shared[n:n + 10] for n in range(0, len(shared), 10)]):
        if batch:
            written += _W(await _write(client, tenant.api_key, tenant.producer_id,
                                       batch, enrich=True))
    if unenriched:
        written += _W(await _write(client, tenant.api_key, tenant.producer_id,
                                   unenriched, enrich=False))
    if private:
        written += _W(await _write(client, tenant.api_key, personal_producer,
                                   private, enrich=True))

    await drain()

    return await _verify(
        pool, client, tenant, member_key=member_key, member_id=member_id,
        written=written,
    )


def _W(body: dict) -> int:
    return body["accepted"]


async def _verify(pool, client, tenant, *, member_key, member_id, written) -> Seeded:
    """The seed's own acceptance run.

    Every assertion here is one clause of the Phase 1 exit criterion, checked
    through the same surface a user would check it through. A seed that reported
    success without this would be a fixture that happened to run.
    """
    admin_key = tenant.api_key
    project_id = tenant.project_id

    staircase = await client.get(
        f"/api/v1/projects/{project_id}/staircase",
        headers={"Authorization": f"Bearer {admin_key}"},
    )
    if staircase.status_code != 200:
        raise SeedError(f"staircase unavailable: {staircase.text[:200]}")
    steps = staircase.json()
    enriched = steps.get("enriched", 0)
    if not enriched:
        raise SeedError(
            "nothing reached `enriched`. The corpus is stored but the enrichment "
            "pipeline did not run -- which is the break this seed exists to find."
        )

    # ... found by scoped search, and cited.
    passed = 0
    failures = []
    for question in QUESTIONS:
        found = await _ask(client, admin_key, project_id, question.question)
        ids = await _external_ids(pool, [c["data_id"] for c in found["results"]])
        if question.must_find in ids:
            passed += 1
        else:
            failures.append(f"{question.question!r} did not find {question.must_find!r}")
        if not found["model_id"]:
            raise SeedError("retrieval did not report which model embedded the corpus")
    if failures:
        raise SeedError(
            "the demo no longer answers its own questions:\n  " + "\n  ".join(failures)
        )

    # ... with an access level that actually excludes.
    member_hits = await _ask(client, member_key, project_id, "budget cut seats vendor")
    member_ids = await _external_ids(pool, [c["data_id"] for c in member_hits["results"]])
    private_hidden = "personal-note-dana" not in member_ids
    if not private_hidden:
        raise SeedError(
            "a second member can read the record written through a personal "
            "connection. That is the ACL failing, not a demo defect."
        )

    # ... the read is audited. Specifically the *read*: the trail reports writes
    # and reads from two different stores, and it is the read half that the exit
    # criterion is about. Asserting on the combined trail would pass on the
    # write records alone, which are never the ones that go missing.
    audited = await client.get(
        f"/api/v1/audit?project_id={project_id}",
        headers={"Authorization": f"Bearer {admin_key}"},
    )
    if audited.status_code != 200:
        raise SeedError(f"the audit trail is unavailable: {audited.text[:200]}")
    if not audited.json().get("reads"):
        raise SeedError(
            "the searches above left no rows in the access log. Retrieval works "
            "and is unaudited, which is the failure that cannot be reconstructed "
            "later."
        )

    # ... and the row records which model embedded it.
    unattributed = await pool.fetchval(
        """
        SELECT count(*) FROM embeddings e
        JOIN data_items d ON d.data_id = e.data_id
        WHERE d.project_id = $1 AND (e.model_id IS NULL OR e.generator_version IS NULL)
        """,
        project_id,
    )
    if unattributed:
        raise SeedError(f"{unattributed} embeddings do not record their model")

    case_members = await _case_summary(client, admin_key, project_id)

    return Seeded(
        org_id=tenant.org_id,
        project_id=project_id,
        admin_email=ADMIN_EMAIL,
        admin_key=admin_key,
        member_email=MEMBER_EMAIL,
        member_key=member_key,
        written=written,
        enriched=enriched,
        questions_passed=passed,
        private_item_hidden=private_hidden,
        case_members=case_members,
    )


async def _external_ids(pool, data_ids: list[str]) -> set[str]:
    if not data_ids:
        return set()
    rows = await pool.fetch(
        "SELECT external_id FROM data_items WHERE data_id = ANY($1::text[])", data_ids
    )
    return {r["external_id"] for r in rows}


async def _case_summary(client, key: str, project_id: str) -> dict:
    listed = await client.get(
        f"/api/v1/projects/{project_id}/cases",
        headers={"Authorization": f"Bearer {key}"},
    )
    cases = listed.json().get("cases", []) if listed.status_code == 200 else []
    match = next((c for c in cases if c.get("external_id") == CASE_ID), None)
    if match is None:
        return {"asserted": 0, "inferred": 0}
    timeline = await client.get(
        f"/api/v1/cases/{match['case_id']}/timeline",
        headers={"Authorization": f"Bearer {key}"},
    )
    if timeline.status_code != 200:
        return {"asserted": 0, "inferred": 0}
    body = timeline.json()
    return {"asserted": body.get("asserted", 0), "inferred": body.get("inferred", 0)}


async def reset_demo(pool: asyncpg.Pool, client, *, drain) -> dict:
    """Purge the demo through the ordinary cascade, then remove the shell.

    A demo people can break needs a reset, and the reset needs to be the real
    one: if resetting requires bespoke cleanup, the purge cascade is incomplete
    and the demo has found the bug before a customer did. So the records go
    through `POST /api/v1/deletions` -- the same selector-delete any offboarding
    uses, tombstones and all.

    The org row itself is then deleted directly, because
    `DELETE /organizations/{id}?purge=true` does not exist yet. That is the one
    part of reset which is not the real path, and it is a gap in the control
    plane rather than a shortcut taken here.
    """
    org_id = await already_seeded(pool)
    if org_id is None:
        # No org, but its people may still be here: an interrupted reset, or a
        # seed that died partway through bootstrap. Reset has to be able to
        # finish a job it started, or the only way out is a hand-written
        # DELETE -- which is exactly the bespoke cleanup this is supposed to
        # prove unnecessary.
        return {"purged": 0, "org_id": None, "users_removed": await _drop_users(pool)}

    row = await pool.fetchrow(
        """
        SELECT k.key_id, k.user_id, p.project_id
        FROM api_keys k JOIN projects p ON p.org_id = k.org_id
        WHERE k.org_id = $1 AND k.name = 'bootstrap' AND k.revoked_at IS NULL
        LIMIT 1
        """,
        org_id,
    )
    purged = 0
    if row is not None:
        # The stored key is hashed, so the reset cannot present the original
        # credential. It issues itself a fresh one for the same admin -- an
        # ordinary key through the ordinary issuing path, revoked with the org a
        # moment later.
        token = await issue_key(
            pool, user_id=row["user_id"], org_id=org_id,
            project_id=row["project_id"],
            capabilities=[DATA_READ, DATA_WRITE, CONFIG_WRITE], name="demo-reset",
        )
        response = await client.post(
            "/api/v1/deletions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                # A project id alone is not a selector: `_selected` refuses to
                # narrow on scope only, so "delete everything here" cannot be
                # expressed by accident. Every seeded record carries the `demo`
                # tag, which is both a real narrowing clause and the honest
                # description of what is being purged.
                "selector": {"project_id": row["project_id"], "tags": ["demo"]},
                "reason": "demo reset",
            },
        )
        if response.status_code != 200:
            raise SeedError(f"the delete cascade refused: {response.text[:300]}")
        purged = response.json()["deleting"]
        await drain()

    # Projects, producers, connections, keys and memberships cascade from the
    # org row. **Users do not** -- a person is not owned by an organization, and
    # deleting one because an org went away would be wrong for every real
    # tenant. So the demo's own users are removed explicitly, and only those
    # left with no membership at all: someone who also belongs elsewhere is
    # somebody else's member and stays.
    await pool.execute("DELETE FROM organizations WHERE org_id = $1", org_id)
    return {"purged": purged, "org_id": org_id, "users_removed": await _drop_users(pool)}


async def _drop_users(pool: asyncpg.Pool) -> int:
    """Remove the demo's own people, once nothing holds them.

    **Users do not cascade from an organization**, and should not: a person is
    not owned by an org, and deleting one because an org went away would be
    wrong for every real tenant. So the two demo addresses are removed by name,
    and only when no membership remains -- if either address somehow belongs to
    another org too, it is that org's member and it stays.
    """
    removed = await pool.execute(
        """
        DELETE FROM users u
        WHERE u.email = ANY($1::text[])
          AND NOT EXISTS (SELECT 1 FROM memberships m WHERE m.user_id = u.user_id)
        """,
        [ADMIN_EMAIL, MEMBER_EMAIL],
    )
    return int(removed.split()[-1]) if removed else 0
