"""Long-running state machines, and the two cases the primitive exists for.

The CRUD is cheap. What matters is concurrency -- two actors moving one
instance -- and cycles, because a graph that may contain them is strictly
harder than a DAG in ways that show up as corruption rather than as errors.
"""

from __future__ import annotations

import asyncio

import pytest

from open_mem import workflows
from open_mem.workflows import Conflict, WorkflowError

pytestmark = pytest.mark.asyncio


APPROVAL = {
    "initial": "draft",
    "states": {
        "draft": {},
        "review": {"ttl_seconds": 172800, "on_timeout": "escalated"},
        "escalated": {},
        "approved": {"terminal": True},
    },
    "transitions": [
        {"from": "draft", "on": "submit", "to": "review"},
        {"from": "review", "on": "approve", "to": "approved",
         "requires_actor_kind": "human"},
        # The cycle, which is the whole point of the primitive.
        {"from": "review", "on": "reject", "to": "draft"},
        {"from": "escalated", "on": "approve", "to": "approved"},
        # A self-transition: valid in `review` and leaving it in `review`. It is
        # what makes a pure sequence conflict testable -- with any other
        # trigger the loser is rejected for being in the wrong *state*, which is
        # a different check passing for a different reason.
        {"from": "review", "on": "comment", "to": "review"},
    ],
}


async def _define(pool, actor, tenant, config=None, **kw):
    return await workflows.upsert_definition(
        pool, actor, project_id=tenant.project_id, external_id="approval",
        name="Approval", config=config or APPROVAL, **kw)


async def _start(pool, actor, definition_id, external_id="po-1"):
    return await workflows.start_instance(
        pool, actor, definition_id, external_id=external_id)


# ---------------------------------------------------------------- validation


@pytest.mark.parametrize("broken,because", [
    ({"initial": "nowhere", "states": {"draft": {}}, "transitions": []},
     "an initial state that is not a state"),
    ({"initial": "draft", "states": {"draft": {}},
      "transitions": [{"from": "draft", "on": "go", "to": "elsewhere"}]},
     "a transition to a state that does not exist"),
    ({"initial": "draft", "states": {"draft": {}, "done": {"terminal": True}},
      "transitions": [{"from": "draft", "on": "go", "to": "done"},
                      {"from": "done", "on": "again", "to": "draft"}]},
     "a terminal state with a way out"),
    ({"initial": "draft", "states": {"draft": {"ttl_seconds": 60}}, "transitions": []},
     "a deadline with nowhere to go"),
    ({"initial": "draft", "states": {"draft": {}, "orphan": {}}, "transitions": []},
     "a state nothing reaches"),
])
async def test_a_graph_that_would_strand_an_instance_is_refused(broken, because):
    """Cheap now, unfixable later: an instance pins the version it started on,
    so editing the definition cannot rescue one already sitting in a dead end."""
    with pytest.raises(WorkflowError):
        workflows.validate_config(broken)


async def test_cycles_are_allowed_because_that_is_the_point():
    """`review -> reject -> draft -> submit -> review` is a workflow, not a bug,
    and a DAG check would refuse the primitive's whole reason for existing."""
    config = workflows.validate_config(APPROVAL)
    assert any(t.to == "draft" for t in config.transitions)


# ------------------------------------------------------------------ the fold


async def test_input_moves_state_and_the_log_is_the_record(pool, tenant, principal_for):
    actor = await principal_for(tenant.api_key)
    definition = await _define(pool, actor, tenant)
    started = await _start(pool, actor, definition["definition_id"])

    moved = await workflows.apply_input(pool, actor, started["instance_id"], trigger="submit")
    assert (moved["from_state"], moved["current_state"]) == ("draft", "review")
    assert moved["current_seq"] == 1

    log = await workflows.history(pool, actor, started["instance_id"])
    # Newest first, and the start is seq 0 rather than an implicit before-time.
    assert [t["seq"] for t in log["transitions"]] == [1, 0]
    assert log["transitions"][-1]["trigger"] == "@start"


async def test_an_input_the_state_does_not_accept_says_what_it_would(
    pool, tenant, principal_for
):
    """A 409 naming the accepted inputs, because "no" alone sends the caller to
    read the definition to learn something the server already knew."""
    actor = await principal_for(tenant.api_key)
    definition = await _define(pool, actor, tenant)
    started = await _start(pool, actor, definition["definition_id"])

    with pytest.raises(WorkflowError) as exc:
        await workflows.apply_input(pool, actor, started["instance_id"], trigger="approve")
    assert exc.value.status == 409
    assert "submit" in str(exc.value)


async def test_two_actors_racing_produce_one_winner_and_one_conflict(
    pool, tenant, principal_for
):
    """The test this feature exists to pass.

    Both name the sequence they believed they were acting on. Exactly one
    update matches, `seq` advances by exactly one, and the log has exactly one
    new row -- two transitions out of the same state is the corruption a state
    machine cannot survive and cannot detect afterwards.
    """
    actor = await principal_for(tenant.api_key)
    definition = await _define(pool, actor, tenant)
    started = await _start(pool, actor, definition["definition_id"])
    await workflows.apply_input(pool, actor, started["instance_id"], trigger="submit")

    # Both send the same input, which is the ordinary version of this race: a
    # double submit, a retried request, two engine replicas. A guarded trigger
    # would fail at the guard and never reach the conditional update, proving
    # nothing about concurrency.
    async def _move():
        return await workflows.apply_input(
            pool, actor, started["instance_id"], trigger="comment", expected_seq=1)

    results = await asyncio.gather(_move(), _move(), return_exceptions=True)
    winners = [r for r in results if not isinstance(r, Exception)]
    losers = [r for r in results if isinstance(r, Conflict)]
    assert len(winners) == 1 and len(losers) == 1
    assert losers[0].current_seq == 2, "the loser is told what it lost to"

    state = await workflows.get_state(pool, actor, started["instance_id"])
    assert state["current_seq"] == 2
    log = await workflows.history(pool, actor, started["instance_id"])
    assert len(log["transitions"]) == 3, "start, submit, and exactly one comment"


async def test_a_caller_whose_state_moved_is_told_what_it_can_do_now(
    pool, tenant, principal_for
):
    """The commoner half of the same race. When the winner changed the *state*
    rather than only the sequence, the loser's input is no longer accepted at
    all -- and being told which inputs are is what lets it recover without
    reading the definition."""
    actor = await principal_for(tenant.api_key)
    definition = await _define(pool, actor, tenant)
    started = await _start(pool, actor, definition["definition_id"])
    await workflows.apply_input(pool, actor, started["instance_id"], trigger="submit")
    await workflows.apply_input(pool, actor, started["instance_id"], trigger="reject")

    with pytest.raises(WorkflowError) as exc:
        await workflows.apply_input(
            pool, actor, started["instance_id"], trigger="reject", expected_seq=1)
    assert exc.value.status == 409 and "submit" in str(exc.value)


async def test_a_guard_keeps_a_key_from_doing_a_humans_job(pool, tenant, principal_for):
    """`requires_actor_kind` is the difference between a workflow and a script."""
    actor = await principal_for(tenant.api_key)
    definition = await _define(pool, actor, tenant)
    started = await _start(pool, actor, definition["definition_id"])
    await workflows.apply_input(pool, actor, started["instance_id"], trigger="submit")

    # An API key is a `key`, and approval was declared human-only.
    with pytest.raises(WorkflowError) as exc:
        await workflows.apply_input(pool, actor, started["instance_id"], trigger="approve")
    assert exc.value.status == 403


async def test_the_clocks_trigger_is_not_addressable_from_a_request(
    pool, tenant, principal_for
):
    """Otherwise a caller could claim a deadline fired, and the history could
    never tell the difference between the clock and somebody impatient."""
    actor = await principal_for(tenant.api_key)
    definition = await _define(pool, actor, tenant)
    started = await _start(pool, actor, definition["definition_id"])

    with pytest.raises(WorkflowError) as exc:
        await workflows.apply_input(pool, actor, started["instance_id"], trigger="@timeout")
    assert exc.value.status == 403


async def test_a_deadline_moves_it_and_a_real_input_beats_the_clock(
    pool, tenant, principal_for
):
    actor = await principal_for(tenant.api_key)
    definition = await _define(pool, actor, tenant)
    started = await _start(pool, actor, definition["definition_id"])
    await workflows.apply_input(pool, actor, started["instance_id"], trigger="submit")

    # Backdate the deadline rather than waiting two days for it.
    await pool.execute(
        "UPDATE workflow_instances SET deadline_at = now() - interval '1 hour' "
        "WHERE instance_id = $1", started["instance_id"])

    result = await workflows.tick(pool)
    assert (result["due"], result["fired"]) == (1, 1)
    state = await workflows.get_state(pool, actor, started["instance_id"])
    assert state["current_state"] == "escalated"

    # And the deadline is gone with the state that had it: `escalated` has no
    # ttl, so a second tick finds nothing rather than firing forever.
    assert state["deadline_at"] is None
    assert (await workflows.tick(pool))["due"] == 0


async def test_a_terminal_state_closes_the_instance(pool, tenant, principal_for):
    actor = await principal_for(tenant.api_key)
    definition = await _define(pool, actor, tenant)
    started = await _start(pool, actor, definition["definition_id"])
    await workflows.apply_input(pool, actor, started["instance_id"], trigger="submit")
    await workflows.apply_input(pool, actor, started["instance_id"], trigger="reject")
    await workflows.apply_input(pool, actor, started["instance_id"], trigger="submit")
    # `escalated -> approve` has no actor guard, and neither does this path;
    # approve from review does, so go around the cycle to the unguarded one.
    await pool.execute(
        "UPDATE workflow_instances SET current_state = 'escalated' WHERE instance_id = $1",
        started["instance_id"])
    done = await workflows.apply_input(
        pool, actor, started["instance_id"], trigger="approve")
    assert done["status"] == "completed"

    with pytest.raises(WorkflowError) as exc:
        await workflows.apply_input(pool, actor, started["instance_id"], trigger="submit")
    assert "completed" in str(exc.value)


async def test_a_cycle_that_does_not_terminate_hits_its_budget(pool, tenant, principal_for):
    """An infinite loop is indistinguishable from a long legitimate one except
    by this number -- and the instance is marked `errored` rather than quietly
    refusing, because an instance that stops moving for no stated reason is the
    state nobody can diagnose."""
    actor = await principal_for(tenant.api_key)
    definition = await _define(pool, actor, tenant, max_transitions=4)
    started = await _start(pool, actor, definition["definition_id"])

    with pytest.raises(WorkflowError) as exc:
        for _ in range(10):
            await workflows.apply_input(pool, actor, started["instance_id"], trigger="submit")
            await workflows.apply_input(pool, actor, started["instance_id"], trigger="reject")
    assert exc.value.status == 409 and "budget" in str(exc.value)
    assert (await workflows.get_state(pool, actor, started["instance_id"]))["status"] \
        == "errored"


async def test_the_cached_state_can_be_proved_against_the_log(pool, tenant, principal_for):
    """A denormalisation nobody can check is one people stop trusting the first
    time something looks wrong."""
    actor = await principal_for(tenant.api_key)
    definition = await _define(pool, actor, tenant)
    started = await _start(pool, actor, definition["definition_id"])
    await workflows.apply_input(pool, actor, started["instance_id"], trigger="submit")

    proof = await workflows.verify_state(pool, actor, started["instance_id"])
    assert proof["agrees"] and proof["folded_state"] == "review"
    assert proof["sequence_gaps"] == [] and proof["chain_breaks"] == []

    # Corrupt the cache the way a bad migration would, and watch it be caught.
    await pool.execute(
        "UPDATE workflow_instances SET current_state = 'approved' WHERE instance_id = $1",
        started["instance_id"])
    proof = await workflows.verify_state(pool, actor, started["instance_id"])
    assert not proof["agrees"] and proof["folded_state"] == "review"


async def test_starting_twice_returns_the_same_instance(pool, tenant, principal_for):
    """An engine retrying a start after a timeout is the ordinary case, not the
    exception, and making every caller deduplicate is making each of them solve
    it separately."""
    actor = await principal_for(tenant.api_key)
    definition = await _define(pool, actor, tenant)
    first = await _start(pool, actor, definition["definition_id"])
    again = await _start(pool, actor, definition["definition_id"])
    assert again["instance_id"] == first["instance_id"] and again["created"] is False


async def test_redefining_does_not_move_running_instances(pool, tenant, principal_for):
    """Each instance pinned the version it started on. Moving a thousand live
    instances onto a graph they never entered is what the pin prevents."""
    actor = await principal_for(tenant.api_key)
    definition = await _define(pool, actor, tenant)
    started = await _start(pool, actor, definition["definition_id"])

    changed = {**APPROVAL, "states": {**APPROVAL["states"], "hold": {}},
               "transitions": APPROVAL["transitions"] + [
                   {"from": "draft", "on": "pause", "to": "hold"}]}
    redefined = await _define(pool, actor, tenant, config=changed)
    assert redefined["config_version"] == 2 and redefined["created"] is False

    pinned = await pool.fetchval(
        "SELECT definition_version FROM workflow_instances WHERE instance_id = $1",
        started["instance_id"])
    assert pinned == 1


async def test_a_transition_is_announced_in_the_same_transaction(
    pool, tenant, principal_for
):
    """"Transitioned but never announced" is the failure the outside engine
    cannot recover from, since it is waiting to be told."""
    from open_mem.events import list_events

    actor = await principal_for(tenant.api_key)
    definition = await _define(pool, actor, tenant)
    started = await _start(pool, actor, definition["definition_id"])
    await workflows.apply_input(pool, actor, started["instance_id"], trigger="submit")

    events = [e for e in await list_events(pool, tenant.org_id)
              if e["event_type"] == "workflow.transitioned"]
    assert len(events) == 1
    assert events[0]["payload"]["to_state"] == "review"
    assert events[0]["payload"]["seq"] == 1
