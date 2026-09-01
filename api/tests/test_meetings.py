"""Meetings, and the ACL that makes them unlike every other source.

Everything else inherits visibility from its connection scope, which is right
for a ticket and wrong for a recording: four people in a room did not publish
to the company.
"""

from __future__ import annotations

import json

import pytest

from memdog import meetings
from memdog.contracts import Inline, ItemAccess, WriteItem, WriteOptions, WriteRequest
from memdog.parsers import parse
from memdog.write import write_items

pytestmark = pytest.mark.asyncio

VTT = b"""WEBVTT

NOTE recording started

1
00:00:01.000 --> 00:00:04.000
<v Dana>The deploy went out at fourteen hundred.

2
00:00:04.500 --> 00:00:07.000
<v Dana>Error rates recovered about two minutes later.

3
00:00:07.500 --> 00:00:11.000
<v Priya>Do we know what caused it? I saw the rollback in PROJ-88.
"""


async def test_a_transcript_is_turns_rather_than_cues():
    """Indexing cues is the wrong unit twice: a sentence is split across three
    of them, so no chunk holds a whole thought, and the timestamps outnumber
    the words."""
    out = parse(VTT, mime="text/vtt", name="standup.vtt")
    assert out.structure["cues"] == 3 and out.structure["turns"] == 2
    assert out.structure["speakers"] == ["Dana", "Priya"]
    assert "Dana: The deploy went out at fourteen hundred. Error rates" in out.text
    assert "00:00" not in out.text, "a citation wants an offset, not a clock reading"


async def test_a_transcript_with_no_attribution_is_still_worth_having():
    """Neither convention is guaranteed, and text without speakers beats no
    text -- but it says so rather than inventing a speaker."""
    plain = b"WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nthe meeting began late\n"
    out = parse(plain, mime="text/vtt", name="x.vtt")
    assert out.text == "the meeting began late"
    assert out.structure["speakers"] == []
    assert out.warnings


async def test_a_sentence_containing_a_colon_is_not_a_speaker():
    """The heuristic that turns half a sentence into a speaker reads as data
    corruption rather than as a guess."""
    from memdog.parsers import _voice

    assert _voice("Dana: it shipped") == ("Dana", "it shipped")
    assert _voice("Priya Sharma: it shipped")[0] == "Priya Sharma"
    assert _voice("One thing was clear: it shipped")[0] is None

    # The trade, stated: a four-word name is read as unattributed speech, which
    # loses attribution. The alternative invents it -- and invented attribution
    # in a transcript is a quote put in somebody's mouth.
    assert _voice("Maria de la Cruz: it shipped")[0] is None


async def test_attendees_resolve_to_principals_and_outsiders_do_not(
    pool, tenant, principal_for
):
    """No principal is invented for somebody outside the organisation, and the
    ones that did not resolve are reported: a transcript that resolved one of
    six attendees is technically correct and practically wrong."""
    resolved = await meetings.attendee_principals(
        pool, tenant.org_id, ["a@example.com", "customer@elsewhere.test", "Nobody@example.com"])

    assert resolved["principals"] == [f"user:{tenant.user_id}"]
    assert resolved["resolved"] == ["a@example.com"]
    assert resolved["unresolved"] == ["customer@elsewhere.test", "nobody@example.com"]


async def test_a_meeting_is_restricted_to_its_attendees_not_to_the_org(
    pool, queue, blobs, settings, tenant, principal_for
):
    """The failure that matters. A shared connection makes a Jira ticket
    org-visible, which is right; the same default on a performance conversation
    is a disclosure nothing downstream would flag."""
    from memdog.retrieval import get_item

    actor = await principal_for(tenant.api_key)
    access = await meetings.meeting_access(pool, tenant.org_id, ["a@example.com"])
    assert access["level"] == "restricted"

    response = await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(
            producer_id=tenant.producer_id,
            items=[WriteItem(
                external_id="meeting-1",
                content=Inline(text=parse(VTT, mime="text/vtt", name="s.vtt").text),
                access=ItemAccess(level=access["level"], principals=access["principals"]),
            )],
            options=WriteOptions(enrich=False),
        ),
    )
    data_id = response.results[0].data_id
    row = await get_item(pool, actor, data_id)
    assert row["access_level"] == "restricted"

    # And an attendee can still read it -- a restriction nobody in the room can
    # read is a different bug wearing the same shape.
    assert row["data_id"] == data_id


async def test_a_meeting_nobody_internal_attended_is_private_not_org(pool, tenant):
    """`restricted` with no principals is refused, correctly -- it means
    restricted to nobody. Falling back to the connection default instead would
    be the exact disclosure this exists to prevent."""
    access = await meetings.meeting_access(
        pool, tenant.org_id, ["someone@elsewhere.test"])
    assert access["level"] == "private" and access["principals"] == []


async def test_narrowing_below_a_shared_connection_is_honoured(
    pool, queue, blobs, settings, connected_tenant, principal_for
):
    """The interaction the plan said to verify before building any of this: a
    connection is a ceiling, so a *narrower* request must be honoured while a
    wider one is refused. A meeting on a shared connection depends on it."""
    from memdog.acl import acl_for_write

    narrowed = acl_for_write(
        connection_scope="shared", requested_level="restricted",
        requested_principals=["user:someone"])
    assert narrowed.access_level == "restricted"

    with pytest.raises(ValueError):
        acl_for_write(connection_scope="shared", requested_level="public",
                      requested_principals=None)


async def test_the_meeting_type_expires_and_archives(pool, tenant):
    """Ninety days, archived rather than deleted -- and it actually runs now,
    which it did not when this was planned."""
    row = await pool.fetchrow(
        "SELECT ttl_seconds, on_expiry FROM memory_types WHERE project_id = $1 AND name = $2",
        tenant.project_id, "meeting")
    assert row is not None, "the meeting type ships with the others"
    assert (row["ttl_seconds"], row["on_expiry"]) == (7_776_000, "archive")


async def test_a_meeting_delivery_is_restricted_to_the_room(
    pool, queue, blobs, settings, tenant, principal_for
):
    """The provider-agnostic half. Zoom, Meet and Teams all send an attendee
    list and disagree only about where it sits, so the three of them are a
    mapping rather than three code paths -- and a fourth nobody has heard of
    works on the day it arrives.
    """
    import os

    from memdog.crypto import Envelope
    from memdog.ids import new_id
    from memdog.retrieval import get_item
    from memdog.webhooks import receive

    envelope = Envelope(os.urandom(32))
    producer_id = new_id("whk")
    await pool.execute(
        """
        INSERT INTO producers (producer_id, type, user_id, org_id, project_id,
                               status, inbound_auth, inbound_mapping, defaults)
        VALUES ($1, 'webhook', $2, $3, $4, 'enabled', 'url_secret', $5, '{}')
        """,
        producer_id, tenant.user_id, tenant.org_id, tenant.project_id,
        {
            "items_path": "recording",
            "text_path": "transcript",
            "external_id_path": "uuid",
            "attendees_path": "recording.participants",
            "attendee_email_key": "email",
            "memory_type": "meeting",
            "memory_key_path": "uuid",
        },
    )

    body = json.dumps({
        "recording": {
            "uuid": "zoom-9981",
            "transcript": "Dana: the deploy went out at fourteen hundred.",
            "participants": [
                {"email": "a@example.com"},
                {"email": "customer@elsewhere.test"},
            ],
        }
    }).encode()
    result = await receive(pool, queue, blobs, settings, envelope,
                           producer_id=producer_id, raw_body=body, headers={})
    assert result.items == 1

    actor = await principal_for(tenant.api_key)
    row = await get_item(pool, actor, result.data_ids[0])
    assert row["access_level"] == "restricted", "not the connection's default"
    # `get_item` does not project `shared_with` -- the principals are the ACL's
    # own business and the read path applies them rather than reporting them.
    shared = await pool.fetchval(
        "SELECT shared_with FROM data_items WHERE data_id = $1", result.data_ids[0])
    assert json.loads(shared) if isinstance(shared, str) else shared \
        == [f"user:{tenant.user_id}"], "the room, and only the room"


async def test_a_delivery_with_no_attendees_path_is_untouched(
    pool, queue, blobs, settings, tenant
):
    """Every other source keeps inheriting its connection scope. This must be
    opt-in per integration, or one mapping key would silently re-ACL a
    project's whole ingestion."""
    import os

    from memdog.crypto import Envelope
    from memdog.ids import new_id
    from memdog.webhooks import receive

    envelope = Envelope(os.urandom(32))
    producer_id = new_id("whk")
    await pool.execute(
        """
        INSERT INTO producers (producer_id, type, user_id, org_id, project_id,
                               status, inbound_auth, inbound_mapping, defaults)
        VALUES ($1, 'webhook', $2, $3, $4, 'enabled', 'url_secret', $5, '{}')
        """,
        producer_id, tenant.user_id, tenant.org_id, tenant.project_id,
        {"text_path": "text"},
    )
    result = await receive(pool, queue, blobs, settings, envelope,
                           producer_id=producer_id,
                           raw_body=json.dumps({"text": "an ordinary ticket"}).encode(),
                           headers={})
    level = await pool.fetchval(
        "SELECT access_level FROM data_items WHERE data_id = $1", result.data_ids[0])
    assert level == "private", "the producer's default, unchanged"
