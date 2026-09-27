"""Connecting a Google Drive folder by sharing it, not by pasting a key.

The machinery to *read* a Drive folder was already here: `google_drive_tree` in
the connector catalog walks a folder and its subfolders, exports Docs, Sheets
and Slides as text, downloads everything else and parses it, and the ordinary
enrichment path takes it from there. What was missing was the connecting, and
what stood in the way was never the API -- it was the credential.

**The old shape of "connect".** Create a service account in a different
product's console, grant it the Drive scope, download its JSON key, paste the
key here, then share the folder with the key's email address. Five steps, three
of them elsewhere, and the last one is both the easiest to skip and the only one
whose failure is silent: an unshared folder is not an error, it is an empty
folder.

**The shape here.** The deployment holds one Drive reader of its own. Connecting
is: copy an address, share the folder with it, paste the folder's link. The
first two steps happen in Drive, where the person already is, and the step that
used to fail silently is now the whole of the interaction.

## What this trades, and what pays for it

One identity reads every tenant's folders. That is the cost of not asking
anybody for a key, and it is only safe because of two things.

**It reaches nothing it was not given.** The address holds no Drive of its own
and no domain-wide delegation; a folder is readable exactly when a human shared
it, which is an act of consent in Google's own interface rather than one
modelled here.

**A folder belongs to the first project that claims it.** Without that, a folder
id -- which appears in a URL, gets pasted into chat, and is not a secret -- would
be enough for any tenant to read another tenant's documents through the shared
reader. `connect` refuses a folder another project already connected rather than
attaching a second crawler to it. The refusal names nothing about who holds it:
that a folder is taken is the answer, and by whom is not this caller's business.

The per-user alternative -- interactive OAuth, where each person connects their
own Drive and reaches only their own files -- is a different feature and a
larger one; `connections.py` records why it is not what an organization
connecting its own data needs. This does not close the door on it: a seventh
auth style would sit beside the six rather than replacing them.
"""

from __future__ import annotations

import json
import re

import asyncpg

from .auth import CONFIG_WRITE, Principal
from .crypto import Envelope

# The scope the assertion is minted for. Read-only and stated here rather than
# taken from the caller: a connect flow that let the browser choose the scope
# would be a connect flow that could ask for write access.
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"

CONNECTOR = "google_drive_tree"

# `https://drive.google.com/drive/folders/<id>`, with or without a query string,
# and `.../drive/u/0/folders/<id>` for anyone signed into more than one account.
# The bare id is accepted too, because it is what the Drive API's own
# documentation shows and somebody will paste it.
_FOLDER_URL = re.compile(
    r"drive\.google\.com/drive/(?:u/\d+/)?folders/(?P<id>[A-Za-z0-9_-]{8,})")
_BARE_ID = re.compile(r"^[A-Za-z0-9_-]{8,}$")


class DriveError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def folder_id(value: str) -> str:
    """The folder id from whatever somebody pasted.

    Refused rather than guessed. A *file* link (`/file/d/<id>/view`) and a
    *shared drive* link (`/drive/folders/` is absent) both look close enough to
    a folder link to paste by mistake, and either would produce a crawler that
    authenticates, walks nothing and reports zero items -- which reads as an
    empty folder rather than as the wrong link.
    """
    text = (value or "").strip()
    if not text:
        raise DriveError("paste the folder's link, or its id")
    found = _FOLDER_URL.search(text)
    if found:
        return found.group("id")
    if "drive.google.com" in text or text.startswith("http"):
        raise DriveError(
            "that is a Google Drive link, but not to a folder. Open the folder "
            "itself -- the address contains /folders/ -- and copy that link")
    if _BARE_ID.match(text):
        return text
    raise DriveError("that is not a Drive folder link or id")


def share_address(service_account: str) -> str | None:
    """The address a folder has to be shared with, or None if unconfigured.

    The key's `client_email` and nothing else. There is no endpoint that returns
    any other part of it, for the same reason there is none that reads a
    connection's credential back.
    """
    if not service_account.strip():
        return None
    try:
        return json.loads(service_account).get("client_email") or None
    except json.JSONDecodeError:
        # Misconfigured is not the same as absent, and a deployment that pasted
        # something that is not a key should be told so rather than shown an
        # empty panel that looks like a feature nobody switched on.
        raise DriveError(
            "DRIVE_SERVICE_ACCOUNT is set but is not a service-account JSON key",
            status=500)


async def claimed_by_another(
    pool: asyncpg.Pool, *, project_id: str, folder: str
) -> bool:
    """Whether some other project already connected this folder.

    Read off the crawlers themselves rather than a second table: the config
    already records which folder a tree crawler walks, and a claim kept
    somewhere else could disagree with it after a crawler is deleted.

    **`crawlers.config` is jsonb that no jsonb operator can read.** `db.py`
    registers a codec that encodes with `json.dumps`, and `create_crawler`
    hands it `config.model_dump_json()` -- a string that is already JSON -- so
    it is serialised twice and stored as a jsonb *string* rather than an
    object. `jsonb_typeof` says `string`, `config ? 'tree'` is false, and every
    `->` returns null. Nothing has noticed because the decoder hands the string
    back and the Python readers parse it a second time; this is the first query
    to use an operator on the column.

    Unwrapped here rather than fixed at the insert, deliberately: the fix
    touches `create_crawler` and every reader of the column
    (`model_validate_json` wants a string and would get a dict), which is a
    change to the crawler core and does not belong inside a Drive feature. The
    CASE reads both shapes, so it keeps working the day that is put right.
    """
    return bool(await pool.fetchval(
        """
        SELECT 1
        FROM crawlers c,
             LATERAL (SELECT CASE WHEN jsonb_typeof(c.config) = 'string'
                                  THEN (c.config #>> '{}')::jsonb
                                  ELSE c.config END AS cfg) AS j
        WHERE c.project_id <> $2
          AND j.cfg -> 'tree' ->> 'api' = 'google_drive'
          AND j.cfg -> 'tree' ->> 'root' = $1
        LIMIT 1
        """,
        folder, project_id,
    ))


async def connect(
    pool: asyncpg.Pool,
    principal: Principal,
    envelope: Envelope,
    *,
    project_id: str,
    folder: str,
    service_account: str,
    enrich: bool = True,
) -> dict:
    """Turn a shared folder into a crawler, created disabled.

    Disabled for the reason every crawler is: the dry run walks the identical
    code and stops short of the write, so its count is what a live run would
    fetch. Here it is also the only check that the folder was actually shared --
    an unshared folder authenticates perfectly and returns nothing, so a run
    that found zero documents is the signal, and it should arrive before
    anything is ingested rather than after.
    """
    from .connections import attach, create as create_connection
    from .connectors import ConnectorError, build
    from .crawlers import CrawlerConfig
    from .crawling import create_crawler

    principal.require(CONFIG_WRITE)
    address = share_address(service_account)
    if not address:
        raise DriveError(
            "this deployment has no Drive reader configured, so there is no "
            "address to share a folder with. Set DRIVE_SERVICE_ACCOUNT.",
            status=503)

    root = folder_id(folder)

    # Connecting the same folder twice returns the first one. The repo already
    # settled this shape for cases -- *"`PUT` rather than `POST`, because the
    # host system's identifier is the source of truth and re-sending it must not
    # create a second case"* -- and here it has a second reason: every extra
    # connection is another encrypted copy of the same private key, stored for
    # a crawler nobody asked for.
    existing = await pool.fetchrow(
        """
        SELECT c.crawler_id, c.enabled, c.connection_id
        FROM crawlers c,
        LATERAL (SELECT CASE WHEN jsonb_typeof(c.config) = 'string'
                             THEN (c.config #>> '{}')::jsonb
                             ELSE c.config END AS cfg) AS j
        WHERE c.project_id = $2
          AND j.cfg -> 'tree' ->> 'api' = 'google_drive'
          AND j.cfg -> 'tree' ->> 'root' = $1
        LIMIT 1
        """,
        root, project_id,
    )
    if existing is not None:
        return {
            "crawler_id": existing["crawler_id"],
            "connection_id": existing["connection_id"],
            "enabled": existing["enabled"],
            "folder": root,
            "share_address": address,
            # Said rather than implied: somebody who pressed the button twice
            # should not be left wondering which of two crawlers is theirs.
            "reused": True,
        }

    if await claimed_by_another(pool, project_id=project_id, folder=root):
        # Deliberately says nothing about who holds it. A folder id is not a
        # secret -- it is in a URL -- so the only thing that keeps one tenant
        # out of another's documents is this refusal, and a refusal that named
        # the holder would leak the thing it is protecting.
        raise DriveError(
            "that folder is already connected to another project. A folder "
            "belongs to the first project that connects it.",
            status=409)

    # Built before anything is stored. A connector that refuses the scope would
    # otherwise leave behind a connection holding its own encrypted copy of the
    # private key, attached to nothing.
    try:
        config = build(CONNECTOR, {"folder": root},
                       name=f"Drive folder {root[:8]}", enrich=enrich)
    except ConnectorError as exc:
        raise DriveError(str(exc), status=exc.status) from exc

    connection = await create_connection(
        pool, principal, envelope,
        project_id=project_id,
        provider=CONNECTOR,
        credential=service_account,
        auth_style="google_service_account",
        auth_config={"scope": DRIVE_SCOPE},
        # Shared, not personal: the folder was shared with the deployment, not
        # with whoever happened to press the button, and a personal connection
        # would bind the crawler to that one person -- which is the failure
        # `cloudrun.sh` passes `shared` to avoid.
        scope="shared",
    )

    created = await create_crawler(
        pool, principal, project_id=project_id,
        config=CrawlerConfig.model_validate(config),
    )

    await attach(pool, principal, created["crawler_id"], connection["connection_id"])

    return {
        **created,
        "connection_id": connection["connection_id"],
        "folder": root,
        "share_address": address,
    }
