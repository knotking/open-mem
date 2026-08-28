"""Access control, in the two places it belongs.

At write time the ACL is *assigned* from the producer's connection scope --
sealed before any customizable phase runs, because a hook that can edit metadata
is a hook that can edit visibility.

At read time the ACL is a *predicate inside the query*, never a filter over
results. Post-filtering returns the wrong twenty rows and then hides some of
them, which is a different and worse thing than filtering before (FR-SCH-8).
"""

from __future__ import annotations

from dataclasses import dataclass

from .auth import Principal

PRIVATE = "private"
ORG = "org"
SHARED = "shared"
RESTRICTED = "restricted"
PUBLIC = "public"

LEVELS = (PRIVATE, ORG, SHARED, RESTRICTED, PUBLIC)

# Ordered most restrictive first. A derived artifact takes the ACL of its most
# restrictive source, so a summary spanning a public and two private documents
# stays private -- otherwise sharing one item leaks two.
_RESTRICTIVENESS = {PRIVATE: 0, RESTRICTED: 1, SHARED: 2, ORG: 3, PUBLIC: 4}


@dataclass(frozen=True)
class Acl:
    access_level: str
    shared_with: list[str]


def strictest(acls: list[Acl]) -> Acl:
    """The rule that keeps every derived index from becoming a leak surface."""
    if not acls:
        return Acl(PRIVATE, [])
    winner = min(acls, key=lambda a: _RESTRICTIVENESS[a.access_level])
    if winner.access_level in (SHARED, RESTRICTED):
        # Intersect the principal lists: only those who can see every source.
        sets = [set(a.shared_with) for a in acls if a.access_level in (SHARED, RESTRICTED)]
        common = set.intersection(*sets) if sets else set()
        return Acl(winner.access_level, sorted(common))
    return Acl(winner.access_level, [])


def acl_for_write(
    *,
    connection_scope: str | None,
    requested_level: str | None,
    requested_principals: list[str] | None,
) -> Acl:
    """Derived from the producer's connection scope -- one rule, all paths.

    A `personal` connection produces private items; a `shared` one produces
    org-visible items. A producer with no connection (upload, direct client
    write) defaults to private, which is the direction that fails safe.
    """
    if connection_scope == "shared":
        default = ORG
    else:
        default = PRIVATE

    level = requested_level or default
    if level not in LEVELS:
        raise ValueError(f"unknown access level {level!r}")
    principals = sorted(set(requested_principals or []))
    if level in (SHARED, RESTRICTED) and not principals:
        raise ValueError(f"access level {level!r} requires principals")
    if level not in (SHARED, RESTRICTED):
        principals = []
    return Acl(level, principals)


def visibility_sql(alias: str, org_param: int, user_param: int, principals_param: int) -> str:
    """The ACL predicate, as SQL. Composed into the retrieval query itself.

    Tenancy is a separate, unconditional predicate: `public` here means shared
    with everyone *inside* the org. Genuinely external reads arrive through the
    share-link surface with its own principal, never through this path.
    """
    a = alias
    return f"""(
        {a}.org_id = ${org_param}
        AND {a}.deleted_at IS NULL
        AND (
               ({a}.access_level = 'private'    AND {a}.owner_id = ${user_param})
            OR ({a}.access_level = 'org')
            OR ({a}.access_level = 'public')
            OR ({a}.access_level = 'shared'     AND ({a}.owner_id = ${user_param}
                                                     OR {a}.shared_with ?| ${principals_param}::text[]))
            OR ({a}.access_level = 'restricted' AND {a}.shared_with ?| ${principals_param}::text[])
        )
    )"""


def visibility_params(principal: Principal) -> tuple[str, str, list[str]]:
    return principal.org_id, principal.user_id, principal.acl_principals()
