"""The settings chain, and the two things that make it a control.

Precedence alone is a suggestion. Locks and scope restrictions are what turn it
into something a compliance officer can rely on.
"""

from __future__ import annotations

import pytest

from memdog.settings_store import SettingError, effective, put, resolve

pytestmark = pytest.mark.asyncio


async def test_most_specific_wins(pool, tenant):
    await put(pool, "enrich_by_default", False, scope="org", scope_id=tenant.org_id,
              set_by=tenant.user_id, org_id=tenant.org_id)
    r = await resolve(pool, "enrich_by_default", org_id=tenant.org_id)
    assert (r.value, r.source) == (False, "org")

    await put(pool, "enrich_by_default", True, scope="project", scope_id=tenant.project_id,
              set_by=tenant.user_id, org_id=tenant.org_id, project_id=tenant.project_id)
    r = await resolve(pool, "enrich_by_default", org_id=tenant.org_id,
                      project_id=tenant.project_id)
    assert (r.value, r.source) == (True, "project")

    await put(pool, "enrich_by_default", False, scope="user", scope_id=tenant.user_id,
              set_by=tenant.user_id, org_id=tenant.org_id, project_id=tenant.project_id)
    r = await resolve(pool, "enrich_by_default", org_id=tenant.org_id,
                      project_id=tenant.project_id, user_id=tenant.user_id)
    assert (r.value, r.source) == (False, "user")


async def test_the_default_applies_when_nothing_is_set(pool, tenant):
    r = await resolve(pool, "media_interpretation", org_id=tenant.org_id)
    # Off by platform default: the expensive tier is never on because nobody
    # said otherwise.
    assert (r.value, r.source) == (False, "default")


async def test_a_lock_beats_specificity(pool, tenant):
    """Without this, org policy is advisory -- and a compliance control a
    project can switch off is not a control."""
    # media_interpretation is settable at project scope, so the lock is what
    # is being tested here rather than the scope rule.
    await put(pool, "media_interpretation", False, scope="org", scope_id=tenant.org_id,
              set_by=tenant.user_id, lock=True, org_id=tenant.org_id)

    r = await resolve(pool, "media_interpretation", org_id=tenant.org_id,
                      project_id=tenant.project_id)
    assert r.value is False and r.locked_by == "org"

    # Writing below it is refused rather than silently accepted: a value the UI
    # shows but which has no effect is how people conclude a control is broken.
    with pytest.raises(SettingError) as exc:
        await put(pool, "media_interpretation", True, scope="project",
                  scope_id=tenant.project_id, set_by=tenant.user_id,
                  org_id=tenant.org_id, project_id=tenant.project_id)
    assert exc.value.status == 409

    # The org can still change its own locked value -- a lock binds levels
    # below it, not the level that set it.
    await put(pool, "media_interpretation", True, scope="org", scope_id=tenant.org_id,
              set_by=tenant.user_id, lock=True, org_id=tenant.org_id)
    assert (await resolve(pool, "media_interpretation", org_id=tenant.org_id)).value is True


async def test_a_setting_a_user_owns_cannot_be_set_for_them(pool, tenant):
    """Refused outright rather than accepted-and-ignored."""
    with pytest.raises(SettingError) as exc:
        await put(pool, "default_project", "prj_x", scope="org", scope_id=tenant.org_id,
                  set_by=tenant.user_id, org_id=tenant.org_id)
    assert exc.value.status == 403
    assert "cannot be set at 'org' scope" in str(exc.value)


async def test_policy_settings_do_not_reach_down_to_the_user(pool, tenant):
    """A user must not be able to enable public sharing for themselves."""
    with pytest.raises(SettingError) as exc:
        await put(pool, "public_sharing", True, scope="user", scope_id=tenant.user_id,
                  set_by=tenant.user_id, org_id=tenant.org_id)
    assert exc.value.status == 403


async def test_a_user_cannot_lock_a_setting_against_themselves(pool, tenant):
    with pytest.raises(SettingError) as exc:
        await put(pool, "enrich_by_default", True, scope="user", scope_id=tenant.user_id,
                  set_by=tenant.user_id, lock=True, org_id=tenant.org_id)
    assert exc.value.status == 403


async def test_unlockable_settings_refuse_the_lock(pool, tenant):
    with pytest.raises(SettingError) as exc:
        await put(pool, "enrich_by_default", True, scope="org", scope_id=tenant.org_id,
                  set_by=tenant.user_id, lock=True, org_id=tenant.org_id)
    assert exc.value.status == 403


async def test_unknown_settings_are_refused_not_stored(pool, tenant):
    """An open settings surface cannot be reasoned about, and a typo that
    silently becomes a new setting is a bug that never surfaces."""
    with pytest.raises(SettingError) as exc:
        await put(pool, "enable_everything", True, scope="org", scope_id=tenant.org_id,
                  set_by=tenant.user_id, org_id=tenant.org_id)
    assert exc.value.status == 404


async def test_effective_reports_provenance_for_every_setting(pool, tenant):
    await put(pool, "media_interpretation", True, scope="org", scope_id=tenant.org_id,
              set_by=tenant.user_id, lock=True, org_id=tenant.org_id)
    rows = {r["key"]: r for r in await effective(
        pool, org_id=tenant.org_id, project_id=tenant.project_id, user_id=tenant.user_id
    )}
    assert rows["media_interpretation"]["value"] is True
    assert rows["media_interpretation"]["locked_by"] == "org"
    assert rows["answer_storage"]["value"] == "metadata"     # the safe default
    assert rows["default_project"]["allowed_scopes"] == ["user"]


async def test_a_value_no_reader_recognises_is_refused(pool, tenant):
    """`registration_mode: "opne"` used to store cleanly.

    It then matched none of the three branches that read it, so the deployment
    stopped admitting anyone and nothing anywhere said so. A stored value no
    reader recognises is worse than a rejected one: the rejection is visible.
    """
    with pytest.raises(SettingError) as exc:
        await put(pool, "registration_mode", "opne", scope="org", scope_id=tenant.org_id,
                  set_by=tenant.user_id, org_id=tenant.org_id)
    assert exc.value.status == 400
    assert "invite_only" in str(exc.value)          # it names what is allowed
    # And nothing was written: the previous value still resolves.
    assert (await resolve(pool, "registration_mode", org_id=tenant.org_id)).value == "invite_only"


async def test_a_string_that_looks_like_a_boolean_is_still_a_string(pool, tenant):
    """Refused rather than coerced. `"true"` and `True` are different values in
    a JSON column, and a reader doing `if value:` would treat `"false"` as on --
    so accepting either would make behaviour depend on which client wrote it."""
    with pytest.raises(SettingError) as exc:
        await put(pool, "enrich_by_default", "true", scope="project",
                  scope_id=tenant.project_id, set_by=tenant.user_id,
                  org_id=tenant.org_id, project_id=tenant.project_id)
    assert exc.value.status == 400


async def test_a_boolean_is_not_a_number(pool, tenant):
    """In Python `True` is an `int`, so an int check written the obvious way
    accepts it and stores `true` in a credit ceiling."""
    with pytest.raises(SettingError):
        await put(pool, "max_concurrent_requests", True, scope="org",
                  scope_id=tenant.org_id, set_by=tenant.user_id, org_id=tenant.org_id)


async def test_null_is_a_value_where_it_means_something(pool, tenant):
    """No ceiling is a ceiling setting people need to express. Everywhere else
    null is a mistake, and the difference is declared per setting."""
    await put(pool, "budget_daily_credits", None, scope="project",
              scope_id=tenant.project_id, set_by=tenant.user_id,
              org_id=tenant.org_id, project_id=tenant.project_id)
    with pytest.raises(SettingError):
        await put(pool, "max_concurrent_requests", None, scope="org",
                  scope_id=tenant.org_id, set_by=tenant.user_id, org_id=tenant.org_id)


async def test_effective_publishes_the_vocabulary_it_enforces(pool, tenant):
    """So an editor builds its control from the server's rule rather than a
    second copy of it -- the copy being the one that goes stale."""
    rows = {r["key"]: r for r in await effective(
        pool, org_id=tenant.org_id, project_id=tenant.project_id, user_id=tenant.user_id
    )}
    assert rows["registration_mode"]["kind"] == "enum"
    assert rows["registration_mode"]["choices"] == ["invite_only", "open", "disabled"]
    assert rows["enrich_by_default"]["kind"] == "bool"
    assert rows["budget_daily_credits"]["nullable"] is True
    # Every choice the register offers is one `put` accepts. A vocabulary the
    # server would refuse is worse than none, because the UI offers it.
    for value in rows["registration_mode"]["choices"]:
        await put(pool, "registration_mode", value, scope="org", scope_id=tenant.org_id,
                  set_by=tenant.user_id, org_id=tenant.org_id)
