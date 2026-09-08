"""Checkpoint timelines.

The tests worth having are the ones guarding properties that fail *quietly*,
which for a change detector is most of them. Its failure modes do not raise:

**Silence and "nothing changed" are the same screen.** A check that never ran,
one that failed, and one that ran and found nothing all leave a timeline entry
with no changes on it. So every exit is asserted to write a terminal status and
an outcome, and the empty answer is asserted to be *stored* rather than absent.

**A noisy detector looks like a working one.** Comparing two model summaries
finds something every time if nothing stops it, and "it found changes" is what
success looks like. The checksum short-circuit and the generator-drift guard are
what stop it, and both are asserted by their cost -- a fake extractor that raises
if it is called at all is the only way to prove a model was not asked.
"""

from __future__ import annotations

import json

import pytest

from memdog.checkpoints import CheckpointError, run_check, timeline
from memdog.extraction import Envelope

pytestmark = pytest.mark.asyncio


class Fake:
    """An extractor whose answers the test chooses, counting what it was asked.

    `state` and `changes` go in `fields` because that is where the real
    extractors put them -- they are not Envelope columns, and a fake that put
    them somewhere easier would be testing a shape nothing produces.
    """

    model_id = "fake-extractor-1"

    def __init__(self, *, state=None, changes=None) -> None:
        self._state = state if state is not None else ["it exists"]
        self._changes = changes if changes is not None else []
        self.calls: list[str] = []

    async def extract(self, text, *, data_type, prompt=None, template=None):
        self.calls.append(data_type)
        fields = ({"state": list(self._state)} if data_type == "checkpoint_state"
                  else {"changes": list(self._changes)})
        return Envelope(title=data_type, summary=" ".join(self._state), fields=fields)


class Refuses:
    """Fails the test if a model is asked for anything at all."""

    model_id = "never-called"

    async def extract(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("a model was called where the answer was already known")


async def _timeline_type(pool, tenant, *, name="vendor_feed", checkpoints=True):
    from memdog.ids import new_id

    await pool.execute(
        """
        INSERT INTO memory_types (type_id, org_id, project_id, name, ttl_seconds,
                                  on_expiry, checkpoints)
        VALUES ($1, $2, $3, $4, NULL, 'keep_members', $5)
        ON CONFLICT (project_id, name) DO UPDATE SET checkpoints = EXCLUDED.checkpoints
        """,
        new_id("mty"), tenant.org_id, tenant.project_id, name, checkpoints)
    return name


async def _write(pool, queue, blobs, settings, principal, tenant, *, type_name,
                 external_id, text):
    from memdog.contracts import Inline, WriteItem, WriteRequest
    from memdog.write import write_items

    await write_items(pool, queue, blobs, settings, principal, WriteRequest(
        producer_id=tenant.producer_id,
        items=[WriteItem(external_id=external_id,
                         memory={"key": "acme-feed", "type": type_name},
                         content=Inline(text=text))],
    ))
    return await pool.fetchval(
        "SELECT data_id FROM data_items WHERE project_id = $1 AND external_id = $2",
        tenant.project_id, external_id)


async def _checkpoints(pool, tenant):
    return await pool.fetch(
        "SELECT c.* FROM memory_checkpoints c JOIN memories m USING (memory_id) "
        "WHERE m.project_id = $1 ORDER BY c.seq",
        tenant.project_id)


# ------------------------------------------------------------------ capture

async def test_a_member_of_a_timeline_type_becomes_a_checkpoint(
    pool, queue, blobs, settings, tenant, principal_for
):
    principal = await principal_for(tenant.api_key)
    name = await _timeline_type(pool, tenant)
    await _write(pool, queue, blobs, settings, principal, tenant,
                 type_name=name, external_id="week-1.md", text="Status: green")

    rows = await _checkpoints(pool, tenant)
    assert len(rows) == 1
    assert rows[0]["seq"] == 1
    assert rows[0]["previous_id"] is None
    # Recorded before anything is described, so the row exists even if the
    # model never answers.
    assert rows[0]["outcome"] == "first"


async def test_an_ordinary_memory_type_captures_nothing(
    pool, queue, blobs, settings, tenant, principal_for
):
    """Off unless asked for. A default that starts spending on every memory in
    a project is the wrong default however useful it is on one."""
    principal = await principal_for(tenant.api_key)
    name = await _timeline_type(pool, tenant, name="plain", checkpoints=False)
    await _write(pool, queue, blobs, settings, principal, tenant,
                 type_name=name, external_id="week-1.md", text="Status: green")

    assert await _checkpoints(pool, tenant) == []


async def test_rewriting_a_record_does_not_add_a_second_checkpoint(
    pool, queue, blobs, settings, tenant, principal_for
):
    """A checkpoint is a *new* membership, not a write. Re-writing the same
    external_id updates the record in place and must not extend the timeline --
    otherwise a re-parse or a corrected upload reads as a change event."""
    principal = await principal_for(tenant.api_key)
    name = await _timeline_type(pool, tenant)
    for text in ("Status: green", "Status: amber"):
        await _write(pool, queue, blobs, settings, principal, tenant,
                     type_name=name, external_id="live.md", text=text)

    rows = await _checkpoints(pool, tenant)
    assert len(rows) == 1


async def test_each_new_record_extends_the_chain(
    pool, queue, blobs, settings, tenant, principal_for
):
    principal = await principal_for(tenant.api_key)
    name = await _timeline_type(pool, tenant)
    for n, text in ((1, "Status: green"), (2, "Status: amber"), (3, "Status: red")):
        await _write(pool, queue, blobs, settings, principal, tenant,
                     type_name=name, external_id=f"week-{n}.md", text=text)

    rows = await _checkpoints(pool, tenant)
    assert [r["seq"] for r in rows] == [1, 2, 3]
    assert rows[0]["previous_id"] is None
    assert rows[1]["previous_id"] == rows[0]["checkpoint_id"]
    assert rows[2]["previous_id"] == rows[1]["checkpoint_id"]


# ------------------------------------------------------------------- checks

async def test_the_first_checkpoint_describes_and_does_not_compare(
    pool, queue, blobs, settings, tenant, principal_for
):
    principal = await principal_for(tenant.api_key)
    name = await _timeline_type(pool, tenant)
    await _write(pool, queue, blobs, settings, principal, tenant,
                 type_name=name, external_id="week-1.md", text="Status: green")
    first = (await _checkpoints(pool, tenant))[0]

    fake = Fake(state=["status is green"])
    result = await run_check(pool, principal, fake, first["checkpoint_id"])

    assert result["outcome"] == "first"
    assert result["status"] == "complete"
    # Described, never compared: there is nothing behind it to compare against.
    assert fake.calls == ["checkpoint_state"]


async def test_identical_content_is_settled_without_asking_a_model(
    pool, queue, blobs, settings, tenant, principal_for
):
    """The guard that pays for itself.

    Byte-identical content cannot have changed, so no description is derived and
    no comparison is run. `Refuses` is the assertion -- an outcome of
    `unchanged` proves nothing on its own, because a model asked to compare two
    identical descriptions would usually say the same thing and cost money to
    do it.
    """
    principal = await principal_for(tenant.api_key)
    name = await _timeline_type(pool, tenant)
    same = "Status: green\nOwner: Priya"
    for n in (1, 2):
        await _write(pool, queue, blobs, settings, principal, tenant,
                     type_name=name, external_id=f"week-{n}.md", text=same)
    rows = await _checkpoints(pool, tenant)

    await run_check(pool, principal, Fake(state=["status is green"]),
                    rows[0]["checkpoint_id"])
    result = await run_check(pool, principal, Refuses(), rows[1]["checkpoint_id"])

    assert result["outcome"] == "unchanged"
    assert result["model_calls"] == 0
    # The description is shared rather than regenerated, which is what keeps the
    # *next* comparison at one generator version.
    after = await _checkpoints(pool, tenant)
    assert after[1]["state_artifact_id"] == after[0]["state_artifact_id"]


async def test_a_changed_record_reports_what_moved(
    pool, queue, blobs, settings, tenant, principal_for
):
    principal = await principal_for(tenant.api_key)
    name = await _timeline_type(pool, tenant)
    await _write(pool, queue, blobs, settings, principal, tenant,
                 type_name=name, external_id="week-1.md", text="Status: green")
    await _write(pool, queue, blobs, settings, principal, tenant,
                 type_name=name, external_id="week-2.md", text="Status: red")
    rows = await _checkpoints(pool, tenant)

    fake = Fake(state=["status is green"], changes=[
        {"kind": "changed", "statement": "status moved from green to red",
         "earlier_value": "green", "later_value": "red", "significance": "high"}])
    await run_check(pool, principal, fake, rows[0]["checkpoint_id"])
    result = await run_check(pool, principal, fake, rows[1]["checkpoint_id"])

    assert result["outcome"] == "changed"
    assert result["changes"] == 1
    change = await pool.fetchrow(
        "SELECT kind, fields FROM artifacts WHERE artifact_id = $1",
        result["change_artifact_id"])
    assert change["kind"] == "checkpoint_change"

    # The comparison read the two DESCRIPTIONS, never the two records. Handing a
    # generator its raw source is what made every repository report an echo of
    # its own input.
    assert fake.calls[-1] == "checkpoint_change"


async def test_finding_nothing_is_stored_as_an_answer(
    pool, queue, blobs, settings, tenant, principal_for
):
    """`[]` and "never checked" are different claims and must not render the
    same. This is the whole question the feature exists to answer, so an empty
    result has to be a recorded outcome rather than an absence."""
    principal = await principal_for(tenant.api_key)
    name = await _timeline_type(pool, tenant)
    await _write(pool, queue, blobs, settings, principal, tenant,
                 type_name=name, external_id="week-1.md", text="Status: green")
    await _write(pool, queue, blobs, settings, principal, tenant,
                 type_name=name, external_id="week-2.md", text="Status:  green")
    rows = await _checkpoints(pool, tenant)

    fake = Fake(state=["status is green"], changes=[])
    await run_check(pool, principal, fake, rows[0]["checkpoint_id"])
    result = await run_check(pool, principal, fake, rows[1]["checkpoint_id"])

    assert result["outcome"] == "unchanged"
    assert result["status"] == "complete"
    # Different text, so the checksum guard did not settle it -- a model really
    # was asked and really answered "nothing".
    assert "checkpoint_change" in fake.calls
    stored = (await _checkpoints(pool, tenant))[1]
    assert stored["outcome"] == "unchanged"
    assert stored["change_artifact_id"] is not None


async def test_a_predecessor_from_another_generator_is_not_compared(
    pool, queue, blobs, settings, tenant, principal_for
):
    """The drift guard.

    Two descriptions written by different prompts differ because the prompt
    moved. A diff of them reports that as content change, in exactly the shape a
    real finding has -- confident, plausible, and wrong.
    """
    principal = await principal_for(tenant.api_key)
    name = await _timeline_type(pool, tenant)
    await _write(pool, queue, blobs, settings, principal, tenant,
                 type_name=name, external_id="week-1.md", text="Status: green")
    await _write(pool, queue, blobs, settings, principal, tenant,
                 type_name=name, external_id="week-2.md", text="Status: red")
    rows = await _checkpoints(pool, tenant)

    await run_check(pool, principal, Fake(state=["green"]), rows[0]["checkpoint_id"])
    # A different model id is a different `generator_version`, which is what a
    # prompt or model change produces in the real system.
    moved = Fake(state=["red"])
    moved.model_id = "fake-extractor-2"
    result = await run_check(pool, principal, moved, rows[1]["checkpoint_id"])

    assert result["outcome"] == "incomparable"
    assert "different version" in result["reason"]
    assert result["status"] == "complete"


async def test_an_unreadable_record_fails_rather_than_reading_as_unchanged(
    pool, queue, blobs, settings, tenant, principal_for
):
    """A checkpoint over a record with no text must not settle as `unchanged`.
    Nothing was compared, and saying so is the difference between a gap and a
    clean bill of health."""
    principal = await principal_for(tenant.api_key)
    name = await _timeline_type(pool, tenant)
    await _write(pool, queue, blobs, settings, principal, tenant,
                 type_name=name, external_id="week-1.md", text="Status: green")
    row = (await _checkpoints(pool, tenant))[0]
    # The real shape of this: bytes stored, nothing parsed out of them yet --
    # a media file whose parse has not run, or one the model declined. Exactly
    # one content reference, which is what `one_content_ref` requires, and
    # `indexable_text` is generated from the two text columns so nulling them is
    # what leaves it with nothing to read.
    await pool.execute(
        "UPDATE data_items SET content_text = NULL, extracted_text = NULL, "
        "storage_ref = 'raw/never-parsed' WHERE data_id = $1", row["data_id"])

    result = await run_check(pool, principal, Fake(), row["checkpoint_id"])
    assert result["status"] == "failed"
    assert result["outcome"] != "unchanged"
    assert result["reason"]


# -------------------------------------------------------------------- reads

async def test_the_timeline_reads_newest_first(
    pool, queue, blobs, settings, tenant, principal_for
):
    principal = await principal_for(tenant.api_key)
    name = await _timeline_type(pool, tenant)
    for n in (1, 2, 3):
        await _write(pool, queue, blobs, settings, principal, tenant,
                     type_name=name, external_id=f"week-{n}.md", text=f"Status: {n}")
    memory_id = await pool.fetchval(
        "SELECT memory_id FROM memories WHERE project_id = $1 AND memory_key = $2",
        tenant.project_id, "acme-feed")

    entries = await timeline(pool, principal, memory_id)
    assert [e["seq"] for e in entries] == [3, 2, 1]
    assert entries[0]["external_id"] == "week-3.md"


async def test_another_organisation_sees_no_timeline(
    pool, queue, blobs, settings, tenant, other_tenant, principal_for
):
    principal = await principal_for(tenant.api_key)
    name = await _timeline_type(pool, tenant)
    await _write(pool, queue, blobs, settings, principal, tenant,
                 type_name=name, external_id="week-1.md", text="Status: green")
    memory_id = await pool.fetchval(
        "SELECT memory_id FROM memories WHERE project_id = $1 AND memory_key = $2",
        tenant.project_id, "acme-feed")

    intruder = await principal_for(other_tenant.api_key)
    assert await timeline(pool, intruder, memory_id) == []

    row = (await _checkpoints(pool, tenant))[0]
    with pytest.raises(CheckpointError) as caught:
        await run_check(pool, intruder, Fake(), row["checkpoint_id"])
    assert caught.value.status == 404


# ------------------------------------------------------------------ wiring

async def test_the_change_generators_are_registered():
    from memdog.derive import GENERATORS, validate

    for name in ("checkpoint_state", "checkpoint_change"):
        assert validate(name)["label"]
        # Neither may archive what it read. A timeline that ate its own records
        # would leave the change record as the only remaining copy of the thing
        # it claims changed.
        assert GENERATORS[name]["archivable"] is False


async def test_the_change_schema_has_no_nullable_unions():
    """Gemini 400s the whole request on `{"type": ["string","null"]}`, and three
    of those trip the breaker -- after which every artifact records `circuit
    open` while ordinary enrichment keeps working."""
    from memdog.extraction import changes_schema

    for spec in changes_schema()["items"]["properties"].values():
        assert not isinstance(spec.get("type"), list), spec


async def test_the_comparable_part_is_emitted_before_the_summary():
    """Property order is load-bearing: whatever is last is what gets clipped
    when the output budget runs out, and a summary already ate an entire graph
    once. The observations are the part that cannot be reconstructed."""
    from memdog.extraction import _gemini_schema

    order = _gemini_schema(state=True, changes=True)["propertyOrdering"]
    assert order.index("state") < order.index("summary")
    assert order.index("changes") < order.index("summary")
    # And required, or the key may simply be absent -- which is how the graph
    # came back empty rather than truncated.
    required = _gemini_schema(state=True, changes=True)["required"]
    assert "state" in required and "changes" in required


async def test_a_change_value_field_cannot_be_used_as_scratch_space():
    """The bound that stopped this generator destroying itself.

    Unnamed and unbounded, `before` became somewhere for the model to think: one
    comparison ran to 7,944 output tokens repeating a single word, hit
    MAX_TOKENS, and returned truncated JSON. The names now match the labels in
    the prompt, both are required, and both are capped.
    """
    from memdog.extraction import CHANGE_ORDER, CHANGE_REQUIRED, changes_schema

    props = changes_schema()["items"]["properties"]
    for name in ("earlier_value", "later_value"):
        assert props[name]["maxLength"] <= 200
        assert props[name]["description"]
        assert name in CHANGE_REQUIRED
    # The statement is produced before the values, so they are copied out of a
    # decision already made rather than being where it gets made.
    assert CHANGE_ORDER.index("statement") < CHANGE_ORDER.index("earlier_value")


async def test_running_out_of_output_budget_says_so():
    """`docs/limit.md` 6.1: the 8,192-token cap was enforced by the provider and
    `finishReason` was never inspected, so a full budget arrived as
    `ExtractionFailed('')` -- which reads as a broken model."""
    import inspect

    from memdog import extraction

    source = inspect.getsource(extraction.GeminiExtractor.extract)
    assert "MAX_TOKENS" in source, (
        "the failure path must name a truncated response, or running out of "
        "room is indistinguishable from the model returning nothing")


async def test_an_artifact_stores_its_fields_as_an_object(
    pool, queue, blobs, settings, tenant, principal_for
):
    """Encoding twice stores a jsonb *string* containing JSON.

    The pool's codec already encodes with `json.dumps`, so an insert that dumps
    first produced `'{"changes": [...]}'`. Nothing errored: the artifact existed,
    its summary rendered, and only the structured half was silently text — so a
    comparison that found three changes and one that found none looked the same.
    """
    principal = await principal_for(tenant.api_key)
    name = await _timeline_type(pool, tenant)
    await _write(pool, queue, blobs, settings, principal, tenant,
                 type_name=name, external_id="week-1.md", text="Status: green")
    row = (await _checkpoints(pool, tenant))[0]

    await run_check(pool, principal, Fake(state=["status is green"]),
                    row["checkpoint_id"])
    stored = await pool.fetchval(
        "SELECT jsonb_typeof(fields) FROM artifacts WHERE artifact_id = "
        "(SELECT state_artifact_id FROM memory_checkpoints WHERE checkpoint_id = $1)",
        row["checkpoint_id"])
    assert stored == "object", f"fields was stored as {stored}, not an object"

    # And it survives the read as a dict the console can index into.
    entries = await timeline(pool, principal, row["memory_id"])
    assert all(not isinstance(e["change_fields"], str) for e in entries)


# ------------------------------------------------- the transition it emits
#
# The read surface and the event surface are two different products of the same
# check, and only one of them was ever tested. That is how `memory_type` came to
# be declared, validated and never emitted: the timeline showed the change, the
# console showed the change, and the alert that was supposed to announce it
# matched nothing at all.


async def _transition(pool, tenant, event_type="checkpoint.changed"):
    """The most recent transition of this type, as a subscriber would see it."""
    row = await pool.fetchrow(
        "SELECT payload FROM domain_events WHERE org_id = $1 AND event_type = $2 "
        "ORDER BY sequence DESC LIMIT 1", tenant.org_id, event_type)
    if row is None:
        return None
    payload = row["payload"]
    return json.loads(payload) if isinstance(payload, str) else dict(payload)


async def _moved(pool, queue, blobs, settings, principal, tenant, *, changes):
    """Two records that differ, checked, so a `checkpoint.changed` is emitted."""
    name = await _timeline_type(pool, tenant)
    await _write(pool, queue, blobs, settings, principal, tenant,
                 type_name=name, external_id="week-1.md", text="Status: green")
    await _write(pool, queue, blobs, settings, principal, tenant,
                 type_name=name, external_id="week-2.md", text="Status: red")
    rows = await _checkpoints(pool, tenant)
    fake = Fake(state=["status is green"], changes=changes)
    await run_check(pool, principal, fake, rows[0]["checkpoint_id"])
    return await run_check(pool, principal, fake, rows[1]["checkpoint_id"])


async def test_the_transition_names_the_memory_type_its_selector_filters_on(
    pool, queue, blobs, settings, tenant, principal_for
):
    """`alerts.SURFACES` has declared `memory_type` on this surface since it
    shipped, and nothing put it in the payload. `create_alert` accepted the
    selector, `backtest` ran green against nothing, and the alert then matched
    nothing forever — which is indistinguishable from a feed that never moved.
    """
    principal = await principal_for(tenant.api_key)
    await _moved(pool, queue, blobs, settings, principal, tenant, changes=[
        {"kind": "changed", "statement": "status moved from green to red",
         "earlier_value": "green", "later_value": "red", "significance": "high"}])

    payload = await _transition(pool, tenant)
    assert payload["memory_type"] == "vendor_feed"


async def test_every_field_the_surface_declares_is_one_the_transition_emits(
    pool, queue, blobs, settings, tenant, principal_for
):
    """The drift guard, and the only test here that would have caught the bug.

    A declared-but-unemitted field cannot fail loudly: `dig` returns None, every
    operator is decidable against None, and the alert simply never fires. So the
    two lists are compared directly rather than trusted to stay in step.
    """
    from memdog.alerts import SURFACES

    principal = await principal_for(tenant.api_key)
    await _moved(pool, queue, blobs, settings, principal, tenant, changes=[
        {"kind": "added", "statement": "a new line item appeared",
         "earlier_value": "", "later_value": "freight", "significance": "low"}])

    payload = await _transition(pool, tenant)
    missing = sorted(SURFACES["checkpoint.changed"] - set(payload))
    assert not missing, (
        f"checkpoint.changed declares {missing} as selectable and emits none of "
        "them; an alert on any of these would match nothing, quietly, forever")


async def test_the_transition_carries_a_digest_a_selector_can_filter_on(
    pool, queue, blobs, settings, tenant, principal_for
):
    """Counts and significance as scalars, because `in` is `v in arg` and is
    therefore false for every list-valued field. A digest that nested these
    inside one object would be declared, emitted, and still unreachable."""
    principal = await principal_for(tenant.api_key)
    result = await _moved(pool, queue, blobs, settings, principal, tenant, changes=[
        {"kind": "changed", "statement": "status moved from green to red",
         "earlier_value": "green", "later_value": "red", "significance": "high"},
        {"kind": "added", "statement": "freight was added",
         "earlier_value": "", "later_value": "freight", "significance": "low"},
    ])

    payload = await _transition(pool, tenant)
    assert payload["changes"] == 2
    assert (payload["added"], payload["removed"], payload["changed"]) == (1, 0, 1)
    # The highest, not the first and not the last: an alert asking for
    # high-significance movement must see a batch that contains one.
    assert payload["highest_significance"] == "high"
    # And the whole delta is reachable without a second call to find out which
    # artifact to ask for.
    assert payload["change_artifact_id"] == result["change_artifact_id"]

    from memdog.alerts import matches_selector

    assert matches_selector({"memory_type": ["vendor_feed"]}, payload)
    assert matches_selector({"highest_significance": ["high"]}, payload)
    assert matches_selector({"added": {"op": "gt", "value": 0}}, payload)
    assert matches_selector({"statements": {"op": "contains", "value": ["freight"]}},
                            payload)
    assert not matches_selector({"memory_type": ["status_report"]}, payload)


async def test_the_digest_is_bounded_however_large_the_delta_is(
    pool, queue, blobs, settings, tenant, principal_for
):
    """A payload that grows with the document is a write-path regression nobody
    notices until `domain_events` is the largest table in the database. The
    count stays exact; only the sample is cut."""
    from memdog.checkpoints import DIGEST_STATEMENTS, STATEMENT_CHARS

    principal = await principal_for(tenant.api_key)
    await _moved(pool, queue, blobs, settings, principal, tenant, changes=[
        {"kind": "changed", "statement": f"line {n} moved " + "x" * 400,
         "earlier_value": "a", "later_value": "b", "significance": "medium"}
        for n in range(12)])

    payload = await _transition(pool, tenant)
    assert payload["changes"] == 12, "the count is of everything, not of the sample"
    assert len(payload["statements"]) == DIGEST_STATEMENTS
    assert all(len(s) <= STATEMENT_CHARS for s in payload["statements"])


async def test_an_alert_on_the_memory_type_reaches_a_real_change(
    pool, queue, blobs, settings, tenant, principal_for
):
    """End to end, because every layer of this passed on its own while the
    feature did not work: the check ran, the artifact stored, the event emitted,
    the alert evaluated — and matched nothing."""
    from memdog.alerts import backtest, create_alert, evaluate_gap, set_enabled

    principal = await principal_for(tenant.api_key)
    # Before the write. A new alert starts at the current head, so one created
    # afterwards would report nothing for the honest reason and hide the bug.
    alert = await create_alert(
        pool, principal, project_id=tenant.project_id, name="vendor feed moved",
        surface="checkpoint.changed", where={"memory_type": ["vendor_feed"]})
    await backtest(pool, principal, alert["alert_id"])
    await set_enabled(pool, principal, alert["alert_id"], True)

    await _moved(pool, queue, blobs, settings, principal, tenant, changes=[
        {"kind": "changed", "statement": "status moved from green to red",
         "earlier_value": "green", "later_value": "red", "significance": "high"}])

    result = await evaluate_gap(pool, alert["alert_id"], trigger="tick")
    assert result["candidates"] == 1
    assert result["matches"] == 1, "the selector was accepted at creation; it has to match"


# ------------------------------------------- the check that found nothing
#
# `memory_checkpoints` went to real trouble to keep "compared and found nothing"
# apart from "never compared" -- two columns, a constraint, and a comment saying
# why. The event stream collapsed that back down by emitting only on `changed`,
# so a consumer maintaining a watermark could not tell a quiet feed from a
# broken one. These are the tests that the stream now says as much as the row.


async def test_a_check_that_found_nothing_still_says_it_ran(
    pool, queue, blobs, settings, tenant, principal_for
):
    principal = await principal_for(tenant.api_key)
    name = await _timeline_type(pool, tenant)
    await _write(pool, queue, blobs, settings, principal, tenant,
                 type_name=name, external_id="week-1.md", text="Status: green")
    await _write(pool, queue, blobs, settings, principal, tenant,
                 type_name=name, external_id="week-2.md", text="Status:  green")
    rows = await _checkpoints(pool, tenant)

    fake = Fake(state=["status is green"], changes=[])
    await run_check(pool, principal, fake, rows[0]["checkpoint_id"])
    await run_check(pool, principal, fake, rows[1]["checkpoint_id"])

    checked = await _transition(pool, tenant, "checkpoint.checked")
    assert checked["outcome"] == "unchanged"
    assert checked["status"] == "complete"
    assert checked["changes"] == 0
    # And nothing claimed to have moved.
    assert await _transition(pool, tenant, "checkpoint.changed") is None


async def test_a_check_that_could_not_compare_says_why(
    pool, queue, blobs, settings, tenant, principal_for
):
    """`incomparable` is the outcome an operator most needs to hear about: the
    timeline is still accepting records and has quietly stopped answering the
    question it exists for. It was visible only by eye in the console."""
    principal = await principal_for(tenant.api_key)
    name = await _timeline_type(pool, tenant)
    await _write(pool, queue, blobs, settings, principal, tenant,
                 type_name=name, external_id="week-1.md", text="Status: green")
    await _write(pool, queue, blobs, settings, principal, tenant,
                 type_name=name, external_id="week-2.md", text="Status: red")
    rows = await _checkpoints(pool, tenant)

    # Two different models is two different generator versions, which is the
    # drift the guard refuses to diff through.
    first = Fake(state=["status is green"])
    first.model_id = "fake-extractor-1"
    second = Fake(state=["status is red"])
    second.model_id = "fake-extractor-2"
    await run_check(pool, principal, first, rows[0]["checkpoint_id"])
    await run_check(pool, principal, second, rows[1]["checkpoint_id"])

    checked = await _transition(pool, tenant, "checkpoint.checked")
    assert checked["outcome"] == "incomparable"
    assert checked["reason"], "an incomparable check with no reason is unactionable"
    assert await _transition(pool, tenant, "checkpoint.changed") is None


async def test_a_failed_check_is_announced_rather_than_swallowed(
    pool, queue, blobs, settings, tenant, principal_for
):
    """A record whose bytes were never parsed cannot be described. The row said
    `failed`; the stream said nothing at all, which is the same shape as a
    timeline nobody had got to yet."""
    principal = await principal_for(tenant.api_key)
    name = await _timeline_type(pool, tenant)
    await _write(pool, queue, blobs, settings, principal, tenant,
                 type_name=name, external_id="week-1.md", text="Status: green")
    row = (await _checkpoints(pool, tenant))[0]
    # `indexable_text` is generated from the two text columns, so nulling them
    # is what leaves it with nothing to read -- see the test above.
    await pool.execute(
        "UPDATE data_items SET content_text = NULL, extracted_text = NULL, "
        "storage_ref = 'raw/never-parsed' WHERE data_id = $1", row["data_id"])

    result = await run_check(pool, principal, Refuses(), row["checkpoint_id"])
    assert result["status"] == "failed"

    checked = await _transition(pool, tenant, "checkpoint.checked")
    assert checked["status"] == "failed"
    assert checked["reason"]


async def test_a_change_is_announced_twice_on_purpose(
    pool, queue, blobs, settings, tenant, principal_for
):
    """Both surfaces fire, and they answer different questions: one is "this ran"
    and the other is "this moved". A subscriber wanting only real movement must
    not have to write a selector to avoid being told about quiet weeks."""
    principal = await principal_for(tenant.api_key)
    await _moved(pool, queue, blobs, settings, principal, tenant, changes=[
        {"kind": "changed", "statement": "status moved from green to red",
         "earlier_value": "green", "later_value": "red", "significance": "high"}])

    changed = await _transition(pool, tenant, "checkpoint.changed")
    checked = await _transition(pool, tenant, "checkpoint.checked")
    assert changed is not None and checked is not None
    assert checked["outcome"] == "changed"
    # The same delta on both, so a consumer does not have to join them.
    assert changed["change_artifact_id"] == checked["change_artifact_id"]
    assert changed["statements"] == checked["statements"]

    # In one transaction with the status they describe. A checkpoint that is
    # complete in the table and absent from the stream is the lost-work shape
    # `domain_events` exists to refuse.
    settled = await pool.fetchrow(
        "SELECT status, outcome FROM memory_checkpoints WHERE checkpoint_id = $1",
        changed["checkpoint_id"])
    assert (settled["status"], settled["outcome"]) == ("complete", "changed")


async def test_the_checked_surface_declares_only_fields_it_emits(
    pool, queue, blobs, settings, tenant, principal_for
):
    from memdog.alerts import SURFACES

    principal = await principal_for(tenant.api_key)
    await _moved(pool, queue, blobs, settings, principal, tenant, changes=[])

    payload = await _transition(pool, tenant, "checkpoint.checked")
    missing = sorted(SURFACES["checkpoint.checked"] - set(payload))
    assert not missing, (
        f"checkpoint.checked declares {missing} as selectable and emits none of "
        "them; an alert on any of these would match nothing, quietly, forever")


async def test_an_alert_can_watch_for_a_timeline_that_stopped_comparing(
    pool, queue, blobs, settings, tenant, principal_for
):
    """The alert this surface exists for. A feed still accepting records while
    every comparison refuses is the failure that looks most like health."""
    from memdog.alerts import backtest, create_alert, evaluate_gap, set_enabled

    principal = await principal_for(tenant.api_key)
    alert = await create_alert(
        pool, principal, project_id=tenant.project_id, name="feed stopped comparing",
        surface="checkpoint.checked",
        where={"outcome": ["incomparable"], "memory_type": ["vendor_feed"]})
    await backtest(pool, principal, alert["alert_id"])
    await set_enabled(pool, principal, alert["alert_id"], True)

    name = await _timeline_type(pool, tenant)
    for n, text in ((1, "Status: green"), (2, "Status: red")):
        await _write(pool, queue, blobs, settings, principal, tenant,
                     type_name=name, external_id=f"week-{n}.md", text=text)
    rows = await _checkpoints(pool, tenant)
    first, second = Fake(state=["green"]), Fake(state=["red"])
    second.model_id = "fake-extractor-2"
    await run_check(pool, principal, first, rows[0]["checkpoint_id"])
    await run_check(pool, principal, second, rows[1]["checkpoint_id"])

    result = await evaluate_gap(pool, alert["alert_id"], trigger="tick")
    # More candidates than checkpoints, and that is correct: the queue fixture
    # registers an `EventWorker`, so every capture is also checked off the queue
    # exactly as the deployed service does. Each of those runs is a real check
    # and earns its own event. What matters is which of them matched.
    assert result["candidates"] >= 2, "the checks were considered"
    assert result["matches"] == 1, "and only the one that could not compare matched"


# ------------------------------------------------------------- ranges
#
# The comparison a checkpoint does is against the one immediately before it.
# The question people ask a feed is "what has changed since Monday", which is a
# range. These tests are mostly about the two ways a range can lie: by silently
# skipping the parts it could not read, and by presenting churn as net.


async def _built(pool, queue, blobs, settings, principal, tenant, steps):
    """A timeline of `(text, changes)` steps, each checked in order.

    Re-checked explicitly even though the queue fixture's `EventWorker` has
    already checked each one: the second pass rewrites every state at *this*
    extractor's generator version, which is what keeps consecutive pairs
    comparable. Checking them out of order would produce a run of `incomparable`
    and look like the drift guard misfiring.
    """
    name = await _timeline_type(pool, tenant)
    for n, (text, _) in enumerate(steps, start=1):
        await _write(pool, queue, blobs, settings, principal, tenant,
                     type_name=name, external_id=f"week-{n}.md", text=text)
    rows = await _checkpoints(pool, tenant)
    for row, (text, changes) in zip(rows, steps):
        await run_check(pool, principal, Fake(state=[text], changes=changes),
                        row["checkpoint_id"])
    return await _checkpoints(pool, tenant)


def _change(statement, *, kind="changed", earlier="a", later="b", significance="medium"):
    return {"kind": kind, "statement": statement, "earlier_value": earlier,
            "later_value": later, "significance": significance}


async def test_a_range_composes_the_deltas_inside_it(
    pool, queue, blobs, settings, tenant, principal_for
):
    from memdog.checkpoints import changes_between

    principal = await principal_for(tenant.api_key)
    rows = await _built(pool, queue, blobs, settings, principal, tenant, [
        ("Status: green", []),
        ("Status: amber", [_change("status went amber")]),
        ("Status: red", [_change("status went red")]),
    ])

    result = await changes_between(pool, principal, rows[0]["memory_id"])
    assert result["basis"] == "composed"
    assert [c["statement"] for c in result["changes"]] == [
        "status went amber", "status went red"]
    # Provenance on every row, so a claim assembled from other people's answers
    # can still be opened against the record behind it.
    assert [c["seq"] for c in result["changes"]] == [2, 3]
    assert all(c["external_id"] for c in result["changes"])
    assert result["gaps"] == []
    assert result["counts"]["changed"] == 2


async def test_a_range_reports_churn_and_says_that_is_what_it_did(
    pool, queue, blobs, settings, tenant, principal_for
):
    """Green, red, green is two changes composed and none net. Both readings are
    defensible; answering with one while the caller assumed the other is not,
    which is why `basis` is a field rather than an assumption."""
    from memdog.checkpoints import changes_between

    principal = await principal_for(tenant.api_key)
    rows = await _built(pool, queue, blobs, settings, principal, tenant, [
        ("Status: green", []),
        ("Status: red", [_change("green to red", earlier="green", later="red")]),
        ("Status: green", [_change("red to green", earlier="red", later="green")]),
    ])

    result = await changes_between(pool, principal, rows[0]["memory_id"])
    assert len(result["changes"]) == 2, "composed reports the churn, not the net"
    assert result["basis"] == "composed"


async def test_the_window_is_exclusive_of_from_and_inclusive_of_to(
    pool, queue, blobs, settings, tenant, principal_for
):
    """So two ranges chained together neither overlap nor skip — which is the
    whole of "what changed since I last synced" working more than once."""
    from memdog.checkpoints import changes_between

    principal = await principal_for(tenant.api_key)
    rows = await _built(pool, queue, blobs, settings, principal, tenant, [
        ("v1", []),
        ("v2", [_change("two")]),
        ("v3", [_change("three")]),
        ("v4", [_change("four")]),
    ])
    memory_id = rows[0]["memory_id"]

    first = await changes_between(pool, principal, memory_id, until="2")
    second = await changes_between(pool, principal, memory_id, since="2")
    whole = await changes_between(pool, principal, memory_id)

    assert [c["statement"] for c in first["changes"]] == ["two"]
    assert [c["statement"] for c in second["changes"]] == ["three", "four"]
    assert ([c["statement"] for c in first["changes"]]
            + [c["statement"] for c in second["changes"]]
            == [c["statement"] for c in whole["changes"]])


async def test_a_hole_in_the_range_is_reported_rather_than_skipped(
    pool, queue, blobs, settings, tenant, principal_for
):
    """A range that quietly omits what it could not read presents as complete.
    For a change detector that is the same lie as a failed check reading as
    "nothing changed"."""
    from memdog.checkpoints import changes_between

    principal = await principal_for(tenant.api_key)
    rows = await _built(pool, queue, blobs, settings, principal, tenant, [
        ("v1", []),
        ("v2", [_change("two")]),
        ("v3", [_change("three")]),
    ])
    # The middle one re-described by a different generator, so its comparison
    # against its predecessor is refused.
    drifted = Fake(state=["v2"], changes=[_change("two")])
    drifted.model_id = "fake-extractor-2"
    await run_check(pool, principal, drifted, rows[1]["checkpoint_id"])

    result = await changes_between(pool, principal, rows[0]["memory_id"])
    assert [g["why"] for g in result["gaps"]] == ["incomparable"]
    assert result["gaps"][0]["seq"] == 2
    assert result["gaps"][0]["detail"], "a gap with no reason is unactionable"
    # And the rest of the range still answers.
    assert [c["statement"] for c in result["changes"]] == ["three"]


async def test_a_checkpoint_nobody_has_checked_is_a_hole_not_a_quiet_week(
    pool, queue, blobs, settings, tenant, principal_for
):
    from memdog.checkpoints import changes_between

    principal = await principal_for(tenant.api_key)
    rows = await _built(pool, queue, blobs, settings, principal, tenant, [
        ("v1", []),
        ("v2", [_change("two")]),
    ])
    await pool.execute(
        "UPDATE memory_checkpoints SET status = 'pending', outcome = NULL, "
        "change_artifact_id = NULL WHERE checkpoint_id = $1",
        rows[1]["checkpoint_id"])

    result = await changes_between(pool, principal, rows[0]["memory_id"])
    assert result["changes"] == []
    assert [g["why"] for g in result["gaps"]] == ["not_checked"]


async def test_a_delta_the_reader_cannot_see_is_a_hole_too(
    pool, queue, blobs, settings, tenant, principal_for
):
    """An artifact takes the strictest level among its sources, so a reader can
    legitimately be unable to see one change in a range they can otherwise read.
    Reporting that as no-change would be the same lie as any other hole."""
    from memdog.checkpoints import changes_between

    principal = await principal_for(tenant.api_key)
    rows = await _built(pool, queue, blobs, settings, principal, tenant, [
        ("v1", []),
        ("v2", [_change("two")]),
    ])
    # `restricted` shared with a principal this reader is not, rather than
    # `private` owned by a stranger — `artifacts.owner_id` is a foreign key, so
    # inventing a user to own it fails before the test can make its point.
    await pool.execute(
        """UPDATE artifacts SET access_level = 'restricted',
                  shared_with = '["grp_nobody_here"]'::jsonb
            WHERE artifact_id = (SELECT change_artifact_id FROM memory_checkpoints
                                  WHERE checkpoint_id = $1)""",
        rows[1]["checkpoint_id"])

    result = await changes_between(pool, principal, rows[0]["memory_id"])
    assert result["changes"] == []
    assert [g["why"] for g in result["gaps"]] == ["not_visible"]


async def test_a_span_wider_than_the_limit_is_refused_not_truncated(
    pool, queue, blobs, settings, tenant, principal_for
):
    """Silent truncation reads as "nothing else moved", which is the one answer
    a change detector must never give by accident."""
    from memdog.checkpoints import RANGE_LIMIT, changes_between

    principal = await principal_for(tenant.api_key)
    rows = await _built(pool, queue, blobs, settings, principal, tenant, [
        ("v1", []), ("v2", [_change("two")])])
    await pool.execute(
        "UPDATE memory_checkpoints SET seq = $2 WHERE checkpoint_id = $1",
        rows[1]["checkpoint_id"], RANGE_LIMIT + 5)

    with pytest.raises(CheckpointError) as caught:
        await changes_between(pool, principal, rows[0]["memory_id"])
    assert str(RANGE_LIMIT) in str(caught.value)


async def test_a_time_lands_on_the_last_checkpoint_at_or_before_it(
    pool, queue, blobs, settings, tenant, principal_for
):
    """"Since Monday" has to mean something when nothing was captured on
    Monday."""
    from datetime import timedelta

    from memdog.checkpoints import changes_between

    principal = await principal_for(tenant.api_key)
    rows = await _built(pool, queue, blobs, settings, principal, tenant, [
        ("v1", []),
        ("v2", [_change("two")]),
        ("v3", [_change("three")]),
    ])
    between = rows[1]["created_at"] + timedelta(microseconds=1)

    result = await changes_between(pool, principal, rows[0]["memory_id"],
                                   since=between.isoformat())
    assert result["from"]["seq"] == 2
    assert [c["statement"] for c in result["changes"]] == ["three"]

    # Earlier than everything means "from the start", which is a real position.
    early = (rows[0]["created_at"] - timedelta(days=1)).isoformat()
    assert len((await changes_between(
        pool, principal, rows[0]["memory_id"], since=early))["changes"]) == 2


async def test_a_position_that_names_nothing_is_an_error_not_a_slide(
    pool, queue, blobs, settings, tenant, principal_for
):
    """A sync resuming from a checkpoint that has been erased must hear about
    it rather than quietly re-reporting a month."""
    from memdog.checkpoints import changes_between

    principal = await principal_for(tenant.api_key)
    rows = await _built(pool, queue, blobs, settings, principal, tenant, [
        ("v1", []), ("v2", [_change("two")])])

    with pytest.raises(CheckpointError) as caught:
        await changes_between(pool, principal, rows[0]["memory_id"], since="cpt_nope")
    assert caught.value.status == 404

    with pytest.raises(CheckpointError):
        await changes_between(pool, principal, rows[0]["memory_id"], since="not a date")


async def test_a_memory_that_is_not_a_timeline_says_so(
    pool, queue, blobs, settings, tenant, principal_for
):
    """Three different situations — no such memory, a memory that will never
    have a timeline, and one with nothing on it yet — must not share a
    rendering."""
    from memdog.checkpoints import changes_between

    principal = await principal_for(tenant.api_key)
    name = await _timeline_type(pool, tenant, name="plain", checkpoints=False)
    await _write(pool, queue, blobs, settings, principal, tenant,
                 type_name=name, external_id="week-1.md", text="Status: green")
    memory_id = await pool.fetchval(
        "SELECT memory_id FROM memories WHERE project_id = $1 AND memory_key = $2",
        tenant.project_id, "acme-feed")

    result = await changes_between(pool, principal, memory_id)
    assert result["changes"] == []
    assert "not a checkpoint timeline" in result["note"]

    with pytest.raises(CheckpointError) as caught:
        await changes_between(pool, principal, "mem_nope")
    assert caught.value.status == 404


async def test_another_organisation_cannot_read_a_range(
    pool, queue, blobs, settings, tenant, other_tenant, principal_for
):
    from memdog.checkpoints import changes_between

    principal = await principal_for(tenant.api_key)
    rows = await _built(pool, queue, blobs, settings, principal, tenant, [
        ("v1", []), ("v2", [_change("two")])])

    intruder = await principal_for(other_tenant.api_key)
    with pytest.raises(CheckpointError) as caught:
        await changes_between(pool, intruder, rows[0]["memory_id"])
    assert caught.value.status == 404


async def test_nothing_new_since_that_point_is_its_own_answer(
    pool, queue, blobs, settings, tenant, principal_for
):
    """The ordinary result for a poller. A consumer asking "what has changed
    since the checkpoint I last saw" lands here every time nothing new has
    arrived, so it must not read as a malformed window."""
    from memdog.checkpoints import changes_between

    principal = await principal_for(tenant.api_key)
    rows = await _built(pool, queue, blobs, settings, principal, tenant, [
        ("v1", []), ("v2", [_change("two")])])

    result = await changes_between(pool, principal, rows[0]["memory_id"],
                                   since=rows[1]["checkpoint_id"])
    assert result["changes"] == []
    assert result["gaps"] == []
    assert "since that point" in result["note"]
    # The window is still reported, so a caller can see it asked about nothing
    # rather than guessing.
    assert result["from"]["seq"] == 2 and result["to"]["seq"] == 2


async def test_an_inverted_window_is_refused_not_answered_empty(
    pool, queue, blobs, settings, tenant, principal_for
):
    """An empty answer here would report "nothing changed" about a span that was
    never examined — the lie every other guard in this file exists to prevent,
    arriving through the one door that looks like a valid answer."""
    from memdog.checkpoints import changes_between

    principal = await principal_for(tenant.api_key)
    rows = await _built(pool, queue, blobs, settings, principal, tenant, [
        ("v1", []), ("v2", [_change("two")]), ("v3", [_change("three")])])

    with pytest.raises(CheckpointError) as caught:
        await changes_between(pool, principal, rows[0]["memory_id"],
                              since="3", until="1")
    assert "ends before it starts" in str(caught.value)


async def test_resolving_a_position_does_not_read_the_whole_timeline(
    pool, queue, blobs, settings, tenant, principal_for
):
    """The span is capped; the timeline is not. A feed running for two years has
    thousands of entries that a range over last week has no business reading, so
    each endpoint is one indexed lookup rather than a walk."""
    from memdog.checkpoints import _locate

    principal = await principal_for(tenant.api_key)
    rows = await _built(pool, queue, blobs, settings, principal, tenant, [
        ("v1", []), ("v2", [_change("two")]), ("v3", [_change("three")])])
    memory_id, org_id = rows[0]["memory_id"], tenant.org_id

    assert (await _locate(pool, memory_id, org_id, "2"))["seq"] == 2
    assert (await _locate(pool, memory_id, org_id,
                          rows[2]["checkpoint_id"]))["seq"] == 3
    # A timestamp after everything lands on the head rather than running off it.
    from datetime import timedelta

    later = (rows[2]["created_at"] + timedelta(days=1)).isoformat()
    assert (await _locate(pool, memory_id, org_id, later))["seq"] == 3
