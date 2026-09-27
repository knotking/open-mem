"""Entity resolution -- graph layer 1.

The hard part of a knowledge graph is not the edges, it is deciding that
"Priya", "Priya Raman" and priya@example.com are one node and that the other
Priya in a different thread is not. Get that wrong and every edge built on top
inherits the mistake.

So the resolver is deliberately timid. It merges on a shared strong identifier,
or on an exact normalized name within one project, and otherwise creates a new
entity. Under-merging leaves two nodes that a person can join later; over-merging
silently fuses two people's records into one, and once their mentions are
interleaved nobody can tell which fact belonged to whom. Those errors are not
symmetric, and the resolver is tuned for the recoverable one.

Everything keeps its evidence. Every mention records the surface form, the
record it came from, and why it resolved the way it did -- so a resolution can
be inspected, corrected and undone rather than merely trusted.

It all lives in Postgres because the traversal has to carry the same visibility
predicate as retrieval. A separate graph store means re-implementing that rule
or post-filtering, and post-filtering a graph leaks structure: "three nodes are
hidden here" discloses that they exist.
"""

from __future__ import annotations

import re
import unicodedata

import asyncpg

from .acl import visibility_params, visibility_sql
from .auth import CONFIG_WRITE, DATA_READ, Principal
from .ids import new_id
from .telemetry import record, span

TYPES = ("person", "organization", "location", "product", "event", "topic", "other")


class EntityError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


_PUNCT = re.compile(r"[^\w\s]+", re.UNICODE)
_SPACE = re.compile(r"\s+")
# Honorifics and suffixes carry no identity. "Dr. Priya Raman" and "Priya
# Raman" are the same person, and leaving the title in makes them two.
_TITLES = {"mr", "mrs", "ms", "miss", "dr", "prof", "sir", "dame", "rev"}
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "phd", "md", "esq"}


def normalize(name: str) -> str:
    """Casefold, strip accents and punctuation, drop titles, collapse space.

    Aggressive enough to join real spelling variation, conservative enough not
    to join different names. It does NOT strip initials or reorder words -- that
    is where "J. Smith" starts matching "Smith, Jane" and two people become one.
    """
    folded = unicodedata.normalize("NFKD", name)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = _PUNCT.sub(" ", folded).casefold()
    parts = [p for p in _SPACE.split(folded) if p]
    while parts and parts[0] in _TITLES:
        parts.pop(0)
    while parts and parts[-1] in _SUFFIXES:
        parts.pop()
    return " ".join(parts)


def normalize_identifier(value: str) -> str:
    """An identifier is only useful if two spellings of it compare equal."""
    cleaned = value.strip().casefold()
    if cleaned.startswith("mailto:"):
        cleaned = cleaned[7:]
    return cleaned.rstrip("/")


async def resolve_mentions(
    conn: asyncpg.Connection,
    *,
    data_id: str,
    org_id: str,
    project_id: str,
    candidates: list[dict],
    generator_version: str | None = None,
) -> list[dict]:
    """Resolve extracted names into entities, inside the caller's transaction.

    Runs in the same transaction as the artifact write, so an item never ends
    up enriched with entities that were not recorded, or vice versa.
    """
    resolved = []
    seen: set[tuple[str, str]] = set()

    for candidate in candidates or []:
        name = (candidate.get("name") or "").strip()
        kind = candidate.get("type") or "other"
        if not name or kind not in TYPES:
            continue
        normalized = normalize(name)
        if not normalized:
            continue
        # One mention per (entity, surface) per record: a name repeated twenty
        # times in a document is one piece of evidence, not twenty.
        key = (kind, normalized)
        if key in seen:
            continue
        seen.add(key)

        identifier = candidate.get("identifier")
        identifier = normalize_identifier(identifier) if identifier else None

        entity, why = await _find_or_create(
            conn, org_id=org_id, project_id=project_id, kind=kind,
            display_name=name, normalized=normalized, identifier=identifier,
        )
        await conn.execute(
            """
            INSERT INTO entity_mentions (mention_id, entity_id, data_id, org_id,
                project_id, surface, resolved_by, generator_version)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (entity_id, data_id, surface) DO NOTHING
            """,
            new_id("men"), entity["entity_id"], data_id, org_id, project_id,
            name, why, generator_version,
        )
        await conn.execute(
            """
            UPDATE entities
               SET mention_count = (SELECT count(*) FROM entity_mentions
                                     WHERE entity_id = $1),
                   last_seen_at = now(),
                   -- Keep the fullest surface form seen. "Priya Raman" is a
                   -- better label than "Priya", and which arrives first is an
                   -- accident of ingestion order.
                   display_name = CASE WHEN length($2) > length(display_name)
                                       THEN $2 ELSE display_name END
             WHERE entity_id = $1
            """,
            entity["entity_id"], name,
        )
        record("entity_mentions", 1, type=kind, resolved_by=why)
        resolved.append({"entity_id": entity["entity_id"], "name": name,
                         "type": kind, "resolved_by": why})
    return resolved


async def _find_or_create(
    conn: asyncpg.Connection, *, org_id: str, project_id: str, kind: str,
    display_name: str, normalized: str, identifier: str | None,
) -> tuple[asyncpg.Record, str]:
    if identifier:
        # A shared strong identifier is the only evidence good enough to join
        # two different names. It is checked first and across types, because
        # an email belongs to one thing regardless of how it was labelled.
        row = await conn.fetchrow(
            """
            SELECT * FROM entities
             WHERE project_id = $1 AND $2 = ANY(identifiers) AND merged_into IS NULL
             LIMIT 1
            """,
            project_id, identifier,
        )
        if row is not None:
            return row, "identifier"

    row = await conn.fetchrow(
        """
        SELECT * FROM entities
         WHERE project_id = $1 AND type = $2 AND normalized_name = $3
        """,
        project_id, kind, normalized,
    )
    if row is not None:
        target = row
        if row["merged_into"]:
            # Follow the merge so mentions land on the surviving entity rather
            # than accumulating on one nobody looks at.
            target = await conn.fetchrow(
                "SELECT * FROM entities WHERE entity_id = $1", row["merged_into"]
            ) or row
        if identifier and identifier not in (target["identifiers"] or []):
            await conn.execute(
                "UPDATE entities SET identifiers = array_append(identifiers, $2) "
                "WHERE entity_id = $1",
                target["entity_id"], identifier,
            )
        return target, "exact_name"

    entity_id = new_id("ent")
    row = await conn.fetchrow(
        """
        INSERT INTO entities (entity_id, org_id, project_id, type, display_name,
                              normalized_name, identifiers)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (project_id, type, normalized_name) DO UPDATE
            SET last_seen_at = now()
        RETURNING *
        """,
        entity_id, org_id, project_id, kind, display_name, normalized,
        [identifier] if identifier else [],
    )
    return row, "new" if row["entity_id"] == entity_id else "exact_name"


# ---------------------------------------------------------------- reading

async def list_entities(
    pool: asyncpg.Pool, principal: Principal, project_id: str, *,
    kind: str | None = None, query: str | None = None, limit: int = 50,
) -> list[dict]:
    """Entities the caller can actually see evidence for.

    An entity is visible only through its mentions, and a mention inherits the
    ACL of the record it came from. An entity nobody can see a mention of must
    not appear at all -- listing it would disclose that a record exists, which
    is exactly what the record's ACL forbids.
    """
    principal.require(DATA_READ)
    org_id, user_id, principals = visibility_params(principal)
    predicate = visibility_sql("d", 4, 5, 6)
    rows = await pool.fetch(
        f"""
        SELECT e.entity_id, e.type, e.display_name, e.identifiers,
               count(DISTINCT m.data_id) AS visible_mentions,
               max(m.created_at) AS last_seen_at
          FROM entities e
          JOIN entity_mentions m ON m.entity_id = e.entity_id
          JOIN data_items d ON d.data_id = m.data_id
         WHERE e.project_id = $1 AND e.merged_into IS NULL
           AND ($2::text IS NULL OR e.type = $2)
           AND ($3::text IS NULL OR e.normalized_name LIKE '%' || $3 || '%')
           AND d.deleted_at IS NULL
           AND {predicate}
         GROUP BY e.entity_id, e.type, e.display_name, e.identifiers
         ORDER BY count(DISTINCT m.data_id) DESC, e.display_name
         LIMIT $7
        """,
        project_id, kind, normalize(query) if query else None,
        org_id, user_id, principals, limit,
    )
    return [dict(r) for r in rows]


async def get_entity(pool: asyncpg.Pool, principal: Principal, entity_id: str) -> dict:
    """The entity and the records that mention it -- only those the caller can
    read, and the count reflects that rather than the true total."""
    principal.require(DATA_READ)
    org_id, user_id, principals = visibility_params(principal)
    predicate = visibility_sql("d", 2, 3, 4)

    entity = await pool.fetchrow(
        "SELECT * FROM entities WHERE entity_id = $1 AND org_id = $2",
        entity_id, principal.org_id,
    )
    if entity is None:
        raise EntityError("entity not found", status=404)

    mentions = await pool.fetch(
        f"""
        SELECT m.data_id, m.surface, m.resolved_by, m.created_at,
               d.external_id, d.state, d.data_type
          FROM entity_mentions m
          JOIN data_items d ON d.data_id = m.data_id
         WHERE m.entity_id = $1 AND d.deleted_at IS NULL AND {predicate}
         ORDER BY m.created_at DESC
         LIMIT 100
        """,
        entity_id, org_id, user_id, principals,
    )

    result = dict(entity)
    result["mentions"] = [dict(m) for m in mentions]
    # Reported from what the caller can see, never from the stored counter --
    # "42 mentions" against a list of three is a disclosure.
    result["visible_mention_count"] = len({m["data_id"] for m in mentions})
    return result


# ---------------------------------------------------------------- merging

async def merge(
    pool: asyncpg.Pool, principal: Principal, *, source_id: str, target_id: str,
    reason: str | None = None,
) -> dict:
    """Declare two entities the same thing, reversibly.

    The source is kept and marked rather than deleted, so the decision can be
    undone and so anything already pointing at it still resolves. A destructive
    merge would make "these are the same person" an irreversible claim, and
    people are reluctant to make irreversible claims -- so they would not
    correct the graph at all.
    """
    principal.require(CONFIG_WRITE)
    if source_id == target_id:
        raise EntityError("an entity cannot be merged into itself")

    async with pool.acquire() as conn, conn.transaction():
        rows = await conn.fetch(
            "SELECT * FROM entities WHERE entity_id = ANY($1::text[]) AND org_id = $2",
            [source_id, target_id], principal.org_id,
        )
        found = {r["entity_id"]: r for r in rows}
        if len(found) != 2:
            raise EntityError("entity not found", status=404)
        if found[source_id]["project_id"] != found[target_id]["project_id"]:
            raise EntityError("entities are in different projects", status=409)
        if found[target_id]["merged_into"]:
            raise EntityError("the target is itself merged elsewhere", status=409)

        await conn.execute(
            "UPDATE entity_mentions SET entity_id = $2 WHERE entity_id = $1",
            source_id, target_id,
        )
        await conn.execute(
            """
            UPDATE entities
               -- COALESCE because array_agg over zero rows is NULL, not an
               -- empty array: merging two entities that both lack identifiers
               -- would otherwise violate the NOT NULL.
               SET identifiers = COALESCE((
                     SELECT array_agg(DISTINCT i)
                       FROM unnest(identifiers || $2::text[]) AS i), '{}'),
                   mention_count = (SELECT count(*) FROM entity_mentions
                                     WHERE entity_id = $1),
                   last_seen_at = now()
             WHERE entity_id = $1
            """,
            target_id, list(found[source_id]["identifiers"] or []),
        )
        await conn.execute(
            "UPDATE entities SET merged_into = $2, mention_count = 0 WHERE entity_id = $1",
            source_id, target_id,
        )
        merge_id = new_id("mrg")
        await conn.execute(
            """
            INSERT INTO entity_merges (merge_id, org_id, source_id, target_id,
                                       reason, merged_by)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            merge_id, principal.org_id, source_id, target_id, reason,
            principal.user_id,
        )
    return {"merge_id": merge_id, "source_id": source_id, "target_id": target_id,
            "reversible": True}


async def unmerge(pool: asyncpg.Pool, principal: Principal, merge_id: str) -> dict:
    """Undo a merge, returning the mentions that came from the source.

    Only the mentions this merge moved are returned -- identified by the
    entity they were originally resolved against, not by everything currently
    on the target, which may include later arrivals that belong there.
    """
    principal.require(CONFIG_WRITE)
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "SELECT * FROM entity_merges WHERE merge_id = $1 AND org_id = $2",
            merge_id, principal.org_id,
        )
        if row is None:
            raise EntityError("merge not found", status=404)
        if row["undone_at"]:
            raise EntityError("this merge was already undone", status=409)

        source = await conn.fetchrow(
            "SELECT * FROM entities WHERE entity_id = $1", row["source_id"]
        )
        if source is None:
            raise EntityError("the source entity no longer exists", status=409)

        # Mentions whose surface normalizes to the source's name go back. This
        # is a heuristic and is honest about being one: a mention that arrived
        # after the merge under the target's name stays where it is.
        moved = await conn.fetch(
            """
            UPDATE entity_mentions SET entity_id = $1
             WHERE entity_id = $2 AND regexp_replace(lower(surface), '[^a-z0-9 ]', '', 'g') = $3
            RETURNING mention_id
            """,
            row["source_id"], row["target_id"], source["normalized_name"],
        )
        await conn.execute(
            "UPDATE entities SET merged_into = NULL, mention_count = "
            "(SELECT count(*) FROM entity_mentions WHERE entity_id = $1) WHERE entity_id = $1",
            row["source_id"],
        )
        await conn.execute(
            "UPDATE entities SET mention_count = "
            "(SELECT count(*) FROM entity_mentions WHERE entity_id = $1) WHERE entity_id = $1",
            row["target_id"],
        )
        await conn.execute(
            "UPDATE entity_merges SET undone_at = now() WHERE merge_id = $1", merge_id
        )
    return {"merge_id": merge_id, "restored": row["source_id"],
            "mentions_returned": len(moved)}
