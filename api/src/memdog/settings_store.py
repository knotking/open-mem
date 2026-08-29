"""The settings chain.

Thirteen documents state a precedence rule; this implements the one that
governs:

    per-request option -> user -> project -> org -> platform default
                          most specific wins

Two qualifications make it a control rather than a suggestion, and both live
here:

**Admin locks beat specificity.** An org that locks a setting makes it
non-overridable below. Without locks, org policy is advisory, and a compliance
control a project can switch off is not a control.

**Not every setting belongs at every level.** The interesting design work is not
the chain -- it is deciding where each setting is *allowed* to live. A setting
an admin must not be able to set for a user is refused at org scope outright,
rather than being overridable-in-practice and forbidden-in-prose.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import asyncpg

from .ids import new_id

SCOPES = ("platform", "org", "project", "user")
# Most specific first: the order the resolver walks.
PRECEDENCE = ("user", "project", "org", "platform")


@dataclass(frozen=True)
class Definition:
    key: str
    default: Any
    # Where this setting may be written. Absent from the tuple means an
    # administrator at that level cannot set it.
    allowed_scopes: tuple[str, ...]
    lockable: bool
    description: str


# The register. Kept small and explicit rather than open: a settings surface
# where anything can be set anywhere cannot be reasoned about, and "where may
# this live?" is the question that actually protects users.
REGISTER: dict[str, Definition] = {
    "media_interpretation": Definition(
        "media_interpretation", False, ("platform", "org", "project"), True,
        "Transcribe audio and video, describe images. The expensive tier -- "
        "opt-in per org, never on by platform default.",
    ),
    "answer_storage": Definition(
        "answer_storage", "metadata", ("platform", "org", "project"), True,
        "Whether stored answers keep their text. Defaults to metadata-only "
        "because an answer corpus is often more sensitive than the source.",
    ),
    "allowed_providers": Definition(
        "allowed_providers", [], ("platform", "org"), True,
        "Model providers this org permits. Locked, this is how "
        "'only our approved providers' is enforced rather than suggested.",
    ),
    "public_sharing": Definition(
        "public_sharing", False, ("platform", "org"), True,
        "Genuinely external sharing. Off by default; some orgs never enable it.",
    ),
    "enrich_by_default": Definition(
        "enrich_by_default", True, ("platform", "org", "project", "user"), False,
        "Whether writes enqueue enrichment unless told otherwise.",
    ),
    "budget_daily_credits": Definition(
        # Null means no ceiling. Settable per user as well as per project --
        # one person's sandbox must not be able to spend the team's month --
        # and safe to expose there only because `quota` composes caps as a
        # minimum down the hierarchy rather than resolving them by precedence.
        # Most-specific-wins is right for a preference and wrong for a ceiling:
        # it would let the person being limited raise their own limit.
        "budget_daily_credits", None, ("platform", "org", "project", "user"), True,
        "Credits that may be spent on model calls per day. Every level binds: "
        "a user is held to the tightest of their own value, their "
        "organization's and the platform's, so setting a larger number lower "
        "down cannot raise a ceiling set above it.",
    ),
    "rate_limit_credits_per_minute": Definition(
        "rate_limit_credits_per_minute", 6000, ("platform", "org", "project"), True,
        "Burst ceiling per credential, in cost-weighted credits rather than "
        "requests -- a hundred searches and a hundred generations are the same "
        "number to a request counter and a thousand times apart in cost. "
        "0 disables the limit.",
    ),
    "max_concurrent_requests": Definition(
        "max_concurrent_requests", 8, ("platform", "org", "project"), True,
        "Requests in flight per credential. A rate limit bounds arrival; this "
        "is what stops one client occupying the whole model tier. 0 disables.",
    ),
    "default_project": Definition(
        # A user setting an admin cannot set for them: it is a preference about
        # how one person works, not a policy about what they may do.
        "default_project", None, ("user",), False,
        "The project a user's tools act on by default.",
    ),
    "notifications": Definition(
        "notifications", {}, ("user",), False,
        "Connection failures, quota warnings, share access.",
    ),
}


class SettingError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Resolved:
    key: str
    value: Any
    source: str             # which scope supplied it
    locked_by: str | None   # the scope that locked it, if any


async def resolve(
    pool: asyncpg.Pool,
    key: str,
    *,
    org_id: str | None = None,
    project_id: str | None = None,
    user_id: str | None = None,
) -> Resolved:
    """Walk the chain, most specific first, honouring locks.

    A lock is not "ignore everything below" in general -- it is "this level's
    value wins". So the walk finds the locking level first and stops there,
    which is the same thing expressed as data rather than as a special case.
    """
    definition = REGISTER.get(key)
    if definition is None:
        raise SettingError(f"unknown setting {key!r}", status=404)

    rows = await pool.fetch(
        """
        SELECT scope, scope_id, value, locked FROM settings
        WHERE key = $1
          AND ((scope = 'platform')
            OR (scope = 'org'     AND scope_id = $2)
            OR (scope = 'project' AND scope_id = $3)
            OR (scope = 'user'    AND scope_id = $4))
        """,
        key, org_id, project_id, user_id,
    )
    by_scope = {r["scope"]: r for r in rows}

    # A lock at any level takes that level's value, whatever is set below it.
    for scope in ("platform", "org", "project"):
        row = by_scope.get(scope)
        if row is not None and row["locked"]:
            return Resolved(key, row["value"], scope, locked_by=scope)

    for scope in PRECEDENCE:
        row = by_scope.get(scope)
        if row is not None:
            return Resolved(key, row["value"], scope, locked_by=None)

    return Resolved(key, definition.default, "default", locked_by=None)


async def put(
    pool: asyncpg.Pool,
    key: str,
    value: Any,
    *,
    scope: str,
    scope_id: str | None,
    set_by: str,
    lock: bool = False,
    org_id: str | None = None,
    project_id: str | None = None,
) -> Resolved:
    definition = REGISTER.get(key)
    if definition is None:
        raise SettingError(f"unknown setting {key!r}", status=404)
    if scope not in SCOPES:
        raise SettingError(f"unknown scope {scope!r}")
    if scope not in definition.allowed_scopes:
        # Refused outright rather than accepted-and-ignored: a setting that
        # silently does nothing is worse than one that is not offered.
        raise SettingError(
            f"{key!r} cannot be set at {scope!r} scope; allowed: "
            f"{', '.join(definition.allowed_scopes)}",
            status=403,
        )
    if lock and not definition.lockable:
        raise SettingError(f"{key!r} is not lockable", status=403)
    if lock and scope == "user":
        raise SettingError("a user cannot lock a setting against themselves", status=403)

    # Writing below a lock is refused, not silently accepted. Otherwise the UI
    # shows a value that has no effect, which is how people conclude a control
    # is broken.
    current = await resolve(pool, key, org_id=org_id, project_id=project_id,
                            user_id=scope_id if scope == "user" else None)
    if current.locked_by is not None and PRECEDENCE.index(scope) < PRECEDENCE.index(current.locked_by):
        raise SettingError(
            f"{key!r} is locked at {current.locked_by} scope and cannot be overridden below it",
            status=409,
        )

    await pool.execute(
        """
        INSERT INTO settings (setting_id, scope, scope_id, key, value, locked, set_by)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (scope, scope_id, key)
        DO UPDATE SET value = EXCLUDED.value, locked = EXCLUDED.locked,
                      set_by = EXCLUDED.set_by, updated_at = now()
        """,
        new_id("set"), scope, scope_id, key, value, lock, set_by,
    )
    return await resolve(pool, key, org_id=org_id, project_id=project_id,
                         user_id=scope_id if scope == "user" else None)


async def effective(
    pool: asyncpg.Pool,
    *,
    org_id: str | None = None,
    project_id: str | None = None,
    user_id: str | None = None,
) -> list[dict]:
    """Every setting with its value, where it came from, and whether it is
    locked. Provenance is the whole point -- "it is set to X" is not actionable
    without "by whom, at which level"."""
    out = []
    for key, definition in REGISTER.items():
        r = await resolve(pool, key, org_id=org_id, project_id=project_id, user_id=user_id)
        out.append({
            "key": key,
            "value": r.value,
            "source": r.source,
            "locked_by": r.locked_by,
            "allowed_scopes": list(definition.allowed_scopes),
            "lockable": definition.lockable,
            "description": definition.description,
        })
    return out
