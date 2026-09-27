"""Meetings, and the one thing that makes them unlike every other source.

Every other connector's visibility follows its **connection scope**: a shared
connection produces org-visible items, which is right for a Jira ticket and a
Confluence page. A meeting transcript must not follow it. **Four people in a
room did not publish to the company**, and a performance conversation ingested
at `org` is a serious disclosure that nothing downstream would flag, because
nothing downstream knows the difference between a ticket and a recording.

So a transcript is written `restricted` to its attendees. The machinery already
exists — `acl_for_write` takes a requested level and principals, and a request
*narrower* than the connection allows is honoured while a wider one is refused —
which is the interaction this module depends on and the reason it was worth
checking before writing any of it.

Two consequences worth stating rather than discovering:

- **An external attendee has no principal here.** A meeting with a customer
  resolves to the internal attendees only. That is the conservative direction
  and the correct one: the alternative is inventing a principal for somebody
  outside the organisation.
- **A digest across several meetings takes the strictest of them.**
  `acl.strictest` already does this, so a weekly summary of four meetings is
  visible only to whoever was in all four. That will surprise people, and the
  alternative is a summary that says out loud what one of its rooms was
  private about.
"""

from __future__ import annotations

import logging

import asyncpg

log = logging.getLogger(__name__)

# Ninety days, archived rather than deleted. A transcript is unedited speech
# that nobody reviewed before it was stored, which makes it the most sensitive
# thing in the catalogue -- arguably more than an HR record, which at least had
# an author. *Keeping forever* is not a defensible default for it, and now that
# the expiry sweep runs this is a policy rather than a wish.
MEETING_TYPE = ("meeting", 7_776_000, "archive")


async def attendee_principals(
    pool: asyncpg.Pool, org_id: str, attendees: list[str]
) -> dict:
    """Resolve a meeting's attendees to principals, and say who did not resolve.

    Matched on email, case-insensitively, against members of this organisation.
    An address that is not a member gets **nothing** -- no principal is invented
    for somebody outside, and the meeting is simply not visible to them here.

    The unresolved list is returned rather than dropped because it is the number
    that tells you whether the ACL is right: a transcript that resolved one of
    six attendees is technically correct and practically wrong, and the person
    configuring the connector needs to see that before the corpus fills up.
    """
    emails = [a.strip().lower() for a in attendees if a and "@" in a]
    if not emails:
        return {"principals": [], "resolved": [], "unresolved": [], "attendees": attendees}

    rows = await pool.fetch(
        """
        SELECT u.user_id, lower(u.email) AS email
          FROM users u JOIN memberships m ON m.user_id = u.user_id
         WHERE m.org_id = $1 AND lower(u.email) = ANY($2::text[])
        """,
        org_id, emails,
    )
    found = {r["email"]: r["user_id"] for r in rows}
    return {
        # Prefixed, because that is the shape the ACL predicate compares
        # against. A bare id matches nothing and the failure is silent: the
        # record is stored correctly and is invisible to everybody, including
        # whoever wrote it.
        "principals": sorted(f"user:{found[e]}" for e in emails if e in found),
        "resolved": sorted(e for e in emails if e in found),
        "unresolved": sorted(e for e in emails if e not in found),
        "attendees": attendees,
    }


async def meeting_access(
    pool: asyncpg.Pool, org_id: str, attendees: list[str]
) -> dict:
    """The `access` block a meeting item is written with.

    Falls back to `private` when nothing resolves, which is the only safe
    direction: `restricted` with an empty principal list is refused by
    `acl_for_write` -- correctly, since it would mean *restricted to nobody* --
    and the tempting alternative of falling back to the connection default is
    exactly the disclosure this module exists to prevent.
    """
    resolved = await attendee_principals(pool, org_id, attendees)
    if not resolved["principals"]:
        log.info("meeting has no resolvable internal attendees; writing it private")
        return {"level": "private", "principals": [], **resolved}
    return {"level": "restricted", **resolved}
