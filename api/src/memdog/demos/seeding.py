"""Put a gallery corpus into a project, and prove it answers.

One project per corpus, inside whichever org already exists. That separation is
the point rather than tidiness: `public_demo` resolves a key to a project id and
scopes the visitor's principal to it, so two corpora in one project would be one
corpus with two names on the gallery.

Everything goes through the public write verb, exactly as `seed.py` does and for
the same reason — a seeder that used a privileged path would be proving a route
nobody else can take.

**A corpus that stops answering fails here.** Each question names the record
that must answer it, so the last step is not "did it write" but "does it still
demonstrate what it claims". That is the only check that catches a corpus going
quietly useless.
"""

from __future__ import annotations

import asyncpg

from ..ids import new_id
from . import Corpus


class DemoSeedError(RuntimeError):
    """Says which step failed, because a seed that fails silently at step four
    is indistinguishable from one that never ran."""


async def _post(client, path: str, key: str, body: dict, *, expect: int = 200):
    response = await client.post(
        path, headers={"Authorization": f"Bearer {key}"}, json=body)
    if response.status_code != expect:
        raise DemoSeedError(
            f"{path} returned {response.status_code}, expected {expect}: "
            f"{response.text[:300]}")
    return response.json()


async def seed_corpus(
    pool: asyncpg.Pool, client, corpus: Corpus, *, org_id: str, admin_key: str, drain,
) -> dict:
    """Create the project, write the corpus, verify it, return its registry entry."""
    name = f"{corpus.title} (demo)"
    # Re-seeding replaces rather than accumulates. Without this a second run --
    # and a failed run is always followed by one -- leaves two projects with the
    # same name, and the registry then names whichever the operator's shell
    # happened to pick first.
    dropped = await pool.execute(
        "DELETE FROM projects WHERE org_id = $1 AND name = $2", org_id, name)
    if dropped and dropped != "DELETE 0":
        print(f"  {corpus.key:9} replacing an earlier seed ({dropped.lower()})")

    project_id = new_id("prj")
    await pool.execute(
        "INSERT INTO projects (project_id, org_id, name) VALUES ($1, $2, $3)",
        project_id, org_id, name,
    )

    # The type carries the policy the corpus needs -- a tracing type expires,
    # a matter file does not. A demo containing only data shows what the
    # product stores; one containing the configuration that made the data
    # useful shows how to use it, and configuration is the part people get
    # wrong.
    await _post(client, f"/api/v1/projects/{project_id}/memory-types", admin_key, {
        "name": corpus.memory_type,
        "ttl_seconds": corpus.ttl_seconds,
        "on_expiry": corpus.on_expiry,
    })

    # **Through the org's shared connection, or not at all.**
    #
    # ACL inheritance follows the connection, not the container. A producer
    # created without one writes `private` records, which are invisible to the
    # public visitor -- whose `user_id` matches no real user by design. The
    # corpus then seeds cleanly, answers every question for the *seeder*, and
    # returns "not supported by the text" to every actual visitor. That is the
    # failure this looks like from outside, and it is indistinguishable from a
    # bad corpus.
    #
    # Refused rather than defaulted: a demo nobody can read must not be
    # something a visitor discovers.
    shared = await pool.fetchval(
        "SELECT connection_id FROM connections"
        " WHERE org_id = $1 AND scope = 'shared' ORDER BY created_at LIMIT 1",
        org_id,
    )
    if shared is None:
        raise DemoSeedError(
            f"{corpus.key}: no shared connection in {org_id}. Records would be "
            "written private and the demo would answer nothing to anyone but "
            "the seeder.")
    producer = await _post(client, "/api/v1/producers", admin_key,
                           {"project_id": project_id, "type": "client",
                            "connection_id": shared})

    # Grouped by whether the item is enriched, then batched. `options.enrich`
    # is a property of the *write*, and the sensor corpus needs both in one
    # seed: raw readings that never reach a model, and the handful of derived
    # digests that do. One flag for the whole corpus would have forced the
    # choice that made the readings unanswerable.
    # A corpus that fetches its content does so now, so a source that is
    # unreachable fails the seed rather than producing an empty demo.
    loaded = await corpus.load()
    if not loaded:
        raise DemoSeedError(f"{corpus.key}: the corpus loaded no records at all")

    written = 0
    for wants_enrichment in (False, True):
        items = [i for i in loaded if i.enrich is wants_enrichment]
        for start in range(0, len(items), 25):
            chunk = items[start:start + 25]
            body = await _post(client, "/api/v1/write", admin_key, {
                "producer_id": producer["producer_id"],
                "items": [
                    {**item.payload(default_identifier=corpus.default_identifier,
                                    synthetic=corpus.synthetic),
                     "memory": {"key": corpus.memory_key, "type": corpus.memory_type}}
                    for item in chunk
                ],
                "options": {"enrich": wants_enrichment},
            }, expect=207)
            if body["failed"]:
                reasons = [r.get("error") for r in body["results"]
                           if r["status"] == "failed"]
                raise DemoSeedError(
                    f"{corpus.key}: {body['failed']} items failed: {reasons[:3]}")
            written += body["accepted"]

    await drain()

    memory_id = await pool.fetchval(
        "SELECT memory_id FROM memories WHERE project_id = $1 AND memory_key = $2",
        project_id, corpus.memory_key,
    )
    if memory_id is None:
        raise DemoSeedError(f"{corpus.key}: wrote {written} records into no memory")

    # The step that makes this a demonstration rather than a corpus.
    passed, failing = 0, []
    for question in corpus.questions:
        found = await _post(client, "/api/v1/retrieve", admin_key, {
            "query": question.question,
            "filter": {"project_id": project_id},
            "match": ["vector", "lexical"],
            "limit": 10,
        })
        ids = await _external_ids(pool, [hit["data_id"] for hit in found["results"]])
        if question.must_find in ids:
            passed += 1
        else:
            failing.append(question.question)

    if failing:
        raise DemoSeedError(
            f"{corpus.key}: {len(failing)} of {len(corpus.questions)} questions no "
            f"longer find their record — {failing[0]!r}. The corpus was written and "
            "does not demonstrate what its card claims.")

    # The check the first version did not have. Every question passed for the
    # seeder and none of them would have passed for a visitor.
    private = await pool.fetchval(
        "SELECT count(*) FROM data_items"
        " WHERE project_id = $1 AND access_level <> 'org'", project_id)
    if private:
        raise DemoSeedError(
            f"{corpus.key}: {private} of {written} records are not org-visible, so "
            "a public visitor would see an empty corpus and be told the text does "
            "not support an answer.")

    entry = corpus.registry_entry(project_id, memory_id)
    entry["_written"] = written
    entry["_questions"] = passed
    return entry


async def _external_ids(pool: asyncpg.Pool, data_ids: list[str]) -> set[str]:
    if not data_ids:
        return set()
    rows = await pool.fetch(
        "SELECT external_id FROM data_items WHERE data_id = ANY($1::text[])", data_ids)
    return {r["external_id"] for r in rows if r["external_id"]}
