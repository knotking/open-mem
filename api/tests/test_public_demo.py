"""The public demo, which is the only surface here with no caller to identify.

Every other route decides what you may read from who you are. This one cannot,
so what replaces that is narrowness -- and the tests below are almost entirely
about the limits, because the limits *are* the design. An unauthenticated
endpoint that makes a model call per request is an open tap on somebody's bill.
"""

from __future__ import annotations

import json

import pytest

from open_mem.public_demo import (
    ANSWER_TTL_SECONDS, DemoUnavailable, cached_answer, check_and_count,
    client_ip, hash_ip, question_key, release, store_answer,
)

pytestmark = pytest.mark.asyncio


class _Req:
    def __init__(self, headers=None, host=None):
        self.headers = headers or {}
        self.client = type("c", (), {"host": host})() if host else None


# ------------------------------------------------------------------ the IP


def test_the_client_ip_is_the_last_forwarded_entry_not_the_first():
    """`X-Forwarded-For` is client-controlled and its first entry is a lie
    anyone can tell -- trusting it would let a visitor rotate their own rate
    limit by inventing a header. The last entry is what the proxy appended."""
    r = _Req({"x-forwarded-for": "1.2.3.4, 5.6.7.8, 9.9.9.9"}, host="10.0.0.1")
    assert client_ip(r) == "9.9.9.9"


def test_without_a_proxy_header_the_socket_address_is_used():
    assert client_ip(_Req(host="10.0.0.1")) == "10.0.0.1"


def test_an_unknown_caller_still_gets_a_stable_bucket():
    """Returning something empty would put every unidentifiable caller in
    separate buckets, which is the opposite of a rate limit."""
    assert client_ip(_Req()) == "unknown"


def test_the_ip_is_hashed_and_salted_with_the_deployment_secret():
    """Rate limiting needs to know two requests came from the same place. It
    does not need to know where that is, and a table of addresses paired with
    the questions people asked is a liability nobody asked for."""
    a = hash_ip("1.2.3.4", "secret-one")
    b = hash_ip("1.2.3.4", "secret-two")
    assert a != b, "the salt is not being applied"
    assert "1.2.3.4" not in a
    assert hash_ip("1.2.3.4", "s") == hash_ip("1.2.3.4", "s")


# -------------------------------------------------------------- the limits


async def test_the_per_ip_limit_stops_one_visitor_monopolising_it(pool):
    await pool.execute("DELETE FROM public_asks")
    for _ in range(3):
        await check_and_count(pool, ip="1.1.1.1", secret="s", question="q",
                              rate_per_hour=3, daily_cap=100)
    with pytest.raises(DemoUnavailable) as caught:
        await check_and_count(pool, ip="1.1.1.1", secret="s", question="q",
                              rate_per_hour=3, daily_cap=100)
    assert "limit" in str(caught.value).lower()
    # And it is per visitor, not global.
    await check_and_count(pool, ip="2.2.2.2", secret="s", question="q",
                          rate_per_hour=3, daily_cap=100)


async def test_the_daily_cap_closes_the_demo_for_everyone(pool):
    """The cap is the bill. It is a hard stop rather than a throttle: a throttle
    still spends, and "the bill grew slowly overnight" is not a better outcome
    than "come back tomorrow"."""
    await pool.execute("DELETE FROM public_asks")
    for i in range(4):
        await check_and_count(pool, ip=f"9.9.9.{i}", secret="s", question="q",
                              rate_per_hour=50, daily_cap=4)
    with pytest.raises(DemoUnavailable) as caught:
        await check_and_count(pool, ip="8.8.8.8", secret="s", question="q",
                              rate_per_hour=50, daily_cap=4)
    assert "tomorrow" in str(caught.value).lower()


async def test_the_global_cap_is_reported_before_the_per_ip_one(pool):
    """Telling one visitor they are rate-limited when the real answer is that
    the demo is closed sends them away to try again in a minute."""
    await pool.execute("DELETE FROM public_asks")
    for i in range(3):
        await check_and_count(pool, ip="7.7.7.7", secret="s", question="q",
                              rate_per_hour=3, daily_cap=3)
    with pytest.raises(DemoUnavailable) as caught:
        await check_and_count(pool, ip="7.7.7.7", secret="s", question="q",
                              rate_per_hour=3, daily_cap=3)
    assert "tomorrow" in str(caught.value).lower()


async def test_a_refused_answer_is_not_charged_to_the_daily_cap(pool):
    """The failure mode of a public endpoint is a flood of requests that error.
    Without release() a burst of failures closes the demo for everybody."""
    await pool.execute("DELETE FROM public_asks")
    ask_id = await check_and_count(pool, ip="3.3.3.3", secret="s", question="q",
                                   rate_per_hour=10, daily_cap=1)
    await release(pool, ask_id)
    # The day's budget is intact, because nothing reached the model.
    await check_and_count(pool, ip="4.4.4.4", secret="s", question="q",
                          rate_per_hour=10, daily_cap=1)


async def test_a_released_ask_still_counts_against_the_per_ip_rate(pool):
    """Otherwise a caller whose requests all fail has no rate limit at all,
    which is exactly the caller a rate limit is for."""
    await pool.execute("DELETE FROM public_asks")
    ask_id = await check_and_count(pool, ip="5.5.5.5", secret="s", question="q",
                                   rate_per_hour=1, daily_cap=100)
    await release(pool, ask_id)
    with pytest.raises(DemoUnavailable):
        await check_and_count(pool, ip="5.5.5.5", secret="s", question="q",
                              rate_per_hour=1, daily_cap=100)


async def test_the_stored_question_is_truncated(pool):
    """The meter needs enough to recognise abuse, not a transcript of what
    anonymous people asked a religious text."""
    await pool.execute("DELETE FROM public_asks")
    await check_and_count(pool, ip="6.6.6.6", secret="s", question="x" * 400,
                          rate_per_hour=10, daily_cap=10)
    stored = await pool.fetchval("SELECT question FROM public_asks LIMIT 1")
    assert len(stored) == 200


# --------------------------------------------------------------- the registry
#
# The single-corpus form was safe because "a request cannot ask for a different
# corpus". A gallery keeps that only if a key the deployment never published
# reaches nothing -- so that is what these assert, rather than that the happy
# path works.

def _settings(**over):
    import dataclasses

    from open_mem.config import Settings

    return dataclasses.replace(Settings(), **over)


def test_no_configuration_publishes_nothing():
    """The default, and the only correct one: an unauthenticated endpoint that
    spends money per request is never inherited."""
    from open_mem.public_demo import registry

    assert registry(_settings()) == {}


def test_the_single_corpus_configuration_still_works():
    """A deployment set up before the gallery existed must be unaffected by it."""
    from open_mem.public_demo import registry, resolve

    settings = _settings(public_project_id="prj_one", public_memory_id="mem_one",
                         public_title="Ask the corpus")
    published = registry(settings)
    assert list(published) == ["default"]
    assert resolve(settings, None).project_id == "prj_one"
    assert resolve(settings, "default").memory_id == "mem_one"


def test_a_key_the_deployment_never_published_reaches_nothing():
    """The whole safety argument in one assertion.

    Not "the project is empty" and not "you may not read it" -- the name does
    not resolve, so there is nothing to escalate to.
    """
    from open_mem.public_demo import DemoUnavailable, resolve

    settings = _settings(public_demos=json.dumps([
        {"key": "legal", "title": "A matter", "project_id": "prj_legal"},
    ]))
    with pytest.raises(DemoUnavailable) as exc:
        resolve(settings, "prj_someone_elses")
    assert exc.value.status == 404
    with pytest.raises(DemoUnavailable):
        resolve(settings, "sales")


def test_an_absent_key_takes_the_first_entry():
    """So a caller written against the single-corpus endpoint keeps working."""
    from open_mem.public_demo import resolve

    settings = _settings(public_demos=json.dumps([
        {"key": "sales", "title": "Acme", "project_id": "prj_sales"},
        {"key": "legal", "title": "Matter", "project_id": "prj_legal"},
    ]))
    assert resolve(settings, None).key == "sales"


def test_a_malformed_registry_publishes_nothing_rather_than_guessing():
    """A typo in the configuration closes the gallery. It must not open a
    different one, and it must not raise on a page nobody has signed in to."""
    from open_mem.public_demo import registry

    assert registry(_settings(public_demos="{not json")) == {}
    # An entry missing the project cannot answer; one missing the key cannot be
    # asked for. Both are dropped rather than half-published.
    assert registry(_settings(public_demos=json.dumps([
        {"key": "broken"},
        {"project_id": "prj_x"},
        {"key": "good", "project_id": "prj_good"},
    ]))) .keys() == {"good"}


def test_the_registry_carries_what_the_gallery_needs_to_say():
    """An entry that cannot say what it demonstrates is an entry that teaches
    the same thing as the one beside it."""
    from open_mem.public_demo import resolve

    settings = _settings(public_demos=json.dumps([{
        "key": "sensors", "title": "A sensor fleet",
        "blurb": "Facet ranges over 40,000 readings, with no model call at all.",
        "project_id": "prj_iot", "memory_id": "mem_iot",
        "questions": ["Which freezer went above -18C last week?"],
    }]))
    demo = resolve(settings, "sensors")
    assert "no model call" in demo.blurb
    assert demo.questions == ("Which freezer went above -18C last week?",)


# ------------------------------------------------- the shared-answer cache
#
# A link is one question asked many times. These tests are about the property
# that makes sharing one safe: the second person through costs nothing.


async def test_a_stored_answer_comes_back_for_the_same_question(pool):
    await store_answer(pool, "gita", "Is it better to do your own duty?",
                       {"answer": "yes", "citations": []})
    hit = await cached_answer(pool, "gita", "Is it better to do your own duty?")
    assert hit is not None and hit["answer"] == "yes"


async def test_whitespace_and_case_do_not_buy_a_second_model_call(pool):
    """A link that has been through a chat client, an email and a paste comes
    back differing by case and spacing and nothing else. Each of those must not
    be a cache miss, because a miss is a model call."""
    await store_answer(pool, "gita", "What leads to ruin?", {"answer": "a"})
    assert await cached_answer(pool, "gita", "  what   LEADS to ruin?  ") is not None


def test_normalisation_stops_short_of_changing_the_question():
    """Deliberately shallow. Merging questions that merely look alike would
    serve the wrong answer with no way for a reader to tell."""
    assert question_key("Is it better") == question_key("  is  IT better ")
    # Punctuation and spelling are load-bearing and are left alone.
    assert question_key("is it better") != question_key("is it bettor")
    assert question_key("what leads to ruin") != question_key("what leads to ruin?")


async def test_one_corpus_answer_is_never_served_for_another(pool):
    """The key is the corpus *and* the question. Without the corpus in it, a
    question two corpora share would answer from whichever was asked first --
    citations into records the other does not contain."""
    await store_answer(pool, "gita", "What changed?", {"answer": "gita"})
    assert await cached_answer(pool, "legal", "What changed?") is None


async def test_a_stale_answer_is_not_served(pool):
    """Past the TTL a stored answer is a miss, so a corpus reseeded with better
    extraction stops serving its old answers rather than pinning them."""
    await store_answer(pool, "gita", "How old?", {"answer": "old"})
    await pool.execute(
        "UPDATE public_answers SET answered_at = now() - ($1 || ' seconds')::interval"
        " WHERE demo_key = 'gita'", str(ANSWER_TTL_SECONDS + 60))
    assert await cached_answer(pool, "gita", "How old?") is None


async def test_asking_again_replaces_a_stale_answer_rather_than_failing(pool):
    """Nothing deletes on read, so the stale row is still there when the
    question comes round again and the write has to displace it."""
    await store_answer(pool, "gita", "Again?", {"answer": "first"})
    await pool.execute(
        "UPDATE public_answers SET answered_at = now() - ($1 || ' seconds')::interval"
        " WHERE demo_key = 'gita'", str(ANSWER_TTL_SECONDS + 60))
    await store_answer(pool, "gita", "Again?", {"answer": "second"})
    hit = await cached_answer(pool, "gita", "Again?")
    assert hit is not None and hit["answer"] == "second"


async def test_hits_are_counted_so_the_cache_can_be_shown_to_work(pool):
    """A table of answers every one of which has been served zero times is a
    feature that costs a write and saves nothing, and the difference is
    invisible without this number."""
    await store_answer(pool, "gita", "Counted?", {"answer": "a"})
    await cached_answer(pool, "gita", "Counted?")
    await cached_answer(pool, "gita", "Counted?")
    assert await pool.fetchval(
        "SELECT hits FROM public_answers WHERE demo_key='gita' AND question_key=$1",
        question_key("Counted?")) == 2
