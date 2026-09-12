"""Connecting a Drive folder by sharing it.

Two things carry the whole feature and neither is the happy path: what a pasted
link is allowed to be, and whether one tenant can reach another's folder through
the shared reader.
"""

from __future__ import annotations

import json

import pytest

from memdog.drive import DriveError, folder_id, share_address

# No module-level asyncio mark: `asyncio_mode = "auto"` already runs the async
# tests here, and marking the synchronous ones warns on every run.

KEY = json.dumps({
    "type": "service_account",
    "client_email": "memdog-drive@example.iam.gserviceaccount.com",
    "private_key": "-----BEGIN PRIVATE KEY-----\nnot-a-real-key\n-----END PRIVATE KEY-----\n",
})


def test_a_folder_link_is_read_in_the_shapes_people_actually_paste():
    wanted = "1AbCdEfGhIjKlMnOp"
    assert folder_id(f"https://drive.google.com/drive/folders/{wanted}") == wanted
    # Signed into more than one Google account -- the common case, and the one
    # that puts /u/0/ in every link somebody copies.
    assert folder_id(f"https://drive.google.com/drive/u/0/folders/{wanted}") == wanted
    assert folder_id(f"https://drive.google.com/drive/folders/{wanted}?usp=sharing") == wanted
    # The Drive API's own documentation shows the bare id, so somebody will
    # paste that.
    assert folder_id(wanted) == wanted
    assert folder_id(f"  {wanted}  ") == wanted


def test_a_file_link_is_refused_rather_than_walked():
    """A file link and a folder link differ by one path segment.

    Accepting one would build a crawler that authenticates, walks nothing and
    reports zero items -- indistinguishable from a folder nobody shared, which
    is the other failure this feature has. Two causes with one symptom is what
    makes a thing unsupportable, so one of them is refused at the door.
    """
    with pytest.raises(DriveError) as refused:
        folder_id("https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view")
    assert "not to a folder" in str(refused.value)

    with pytest.raises(DriveError):
        folder_id("")
    with pytest.raises(DriveError):
        folder_id("my documents")


def test_the_address_is_the_client_email_and_nothing_else():
    assert share_address(KEY) == "memdog-drive@example.iam.gserviceaccount.com"
    # Absent is a real state the console renders differently from broken.
    assert share_address("") is None
    assert share_address("   ") is None


def test_a_key_that_is_not_a_key_is_not_reported_as_unconfigured():
    """Misconfigured and absent must not look the same.

    An empty panel reads as a feature nobody switched on. A deployment that put
    something other than a service-account key in the variable has a mistake to
    fix and should be told.
    """
    with pytest.raises(DriveError) as broken:
        share_address("not json at all")
    assert broken.value.status == 500


async def test_a_folder_belongs_to_the_first_project_that_connects_it(
    pool, tenant, other_tenant, principal_for,
):
    """The guard that makes one shared reader safe.

    A folder id lives in a URL and is not a secret. With a single identity
    reading every tenant's folders, knowing an id would otherwise be enough to
    attach a crawler to somebody else's documents -- the read would succeed,
    because the folder really is shared with the reader that is doing the
    reading. So the second project is refused.
    """
    from memdog.crypto import Envelope
    from memdog.drive import connect

    envelope = Envelope(b"0" * 32)
    folder = "1AbCdEfGhIjKlMnOp"

    first = await connect(
        pool, await principal_for(tenant.api_key), envelope,
        project_id=tenant.project_id,
        folder=f"https://drive.google.com/drive/folders/{folder}",
        service_account=KEY,
    )
    assert first["folder"] == folder
    # Disabled, like every crawler. Here it is also the only check that the
    # folder was ever shared.
    assert first["enabled"] is False
    assert first["share_address"] == "memdog-drive@example.iam.gserviceaccount.com"

    with pytest.raises(DriveError) as taken:
        await connect(
            pool, await principal_for(other_tenant.api_key), envelope,
            project_id=other_tenant.project_id,
            folder=folder,
            service_account=KEY,
        )
    assert taken.value.status == 409
    # It says the folder is taken and not by whom: naming the holder would leak
    # the thing the refusal exists to protect.
    assert "another project" in str(taken.value)
    assert other_tenant.project_id not in str(taken.value)
    assert tenant.project_id not in str(taken.value)


async def test_the_same_project_is_not_blocked_by_its_own_claim(
    pool, tenant, principal_for,
):
    """Reconnecting your own folder returns the first one, and is not a claim.

    Two things at once. The claim check is deliberately `project_id <> $2`
    rather than "does any crawler walk this folder", or your own earlier crawler
    would lock you out of your own folder. And pressing the button twice must
    not leave two crawlers on one folder -- each extra one is another encrypted
    copy of the same private key, and the second is a crawler nobody asked for.
    """
    from memdog.crypto import Envelope
    from memdog.drive import connect

    envelope = Envelope(b"0" * 32)
    first = await connect(
        pool, await principal_for(tenant.api_key), envelope,
        project_id=tenant.project_id, folder="1ZzZzZzZzZzZzZzZz",
        service_account=KEY,
    )
    assert first.get("reused") is None

    again = await connect(
        pool, await principal_for(tenant.api_key), envelope,
        # The same folder by its URL rather than its id, because that is how
        # somebody would arrive at it the second time.
        project_id=tenant.project_id,
        folder="https://drive.google.com/drive/folders/1ZzZzZzZzZzZzZzZz",
        service_account=KEY,
    )
    assert again["reused"] is True
    assert again["crawler_id"] == first["crawler_id"]
    assert again["connection_id"] == first["connection_id"]

    only_one = await pool.fetchval(
        "SELECT count(*) FROM crawlers WHERE project_id = $1", tenant.project_id)
    assert only_one == 1
    # And no second copy of the key sitting behind it.
    assert await pool.fetchval(
        "SELECT count(*) FROM connections WHERE project_id = $1", tenant.project_id) == 1


async def test_no_reader_configured_is_503_and_says_so(pool, tenant, principal_for):
    from memdog.crypto import Envelope
    from memdog.drive import connect

    with pytest.raises(DriveError) as unset:
        await connect(
            pool, await principal_for(tenant.api_key), Envelope(b"0" * 32),
            project_id=tenant.project_id, folder="1AbCdEfGhIjKlMnOp",
            service_account="",
        )
    assert unset.value.status == 503
    assert "DRIVE_SERVICE_ACCOUNT" in str(unset.value)
