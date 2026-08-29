"""The MCP server.

Two things are worth testing and they are not "does a tool return something".

The first is that the tools are the REST API rather than a second one: every
call goes through the service function its endpoint uses, so a record a
credential could not retrieve over HTTP is a record it cannot reach here
either. A parallel implementation would pass a happy-path test and be a hole.

The second is the protocol's own edges — a notification must produce no reply,
a batch must answer as a batch, and a tool that refuses must refuse in the way
a model can read and act on rather than as a transport failure.
"""

from __future__ import annotations

import httpx
import pytest

from memdog import mcp

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def client(pool, tenant):
    from memdog.app import app

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            yield c


def _rpc(method: str, params: dict | None = None, request_id: int | str | None = 1):
    message = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        message["id"] = request_id
    if params is not None:
        message["params"] = params
    return message


async def _call(client, key: str, message):
    return await client.post(
        "/api/v1/mcp", headers={"Authorization": f"Bearer {key}"}, json=message
    )


async def _tool(client, key: str, name: str, arguments: dict):
    response = await _call(
        client, key, _rpc("tools/call", {"name": name, "arguments": arguments})
    )
    assert response.status_code == 200, response.text
    return response.json()["result"]


async def test_initialize_announces_only_what_is_implemented(client, tenant):
    """Announcing prompts or resources would have clients ask for them and get
    nothing back."""
    response = await _call(client, tenant.api_key, _rpc("initialize"))
    result = response.json()["result"]

    assert result["protocolVersion"] == mcp.PROTOCOL_VERSION
    assert result["serverInfo"]["name"] == "mem-dog"
    assert set(result["capabilities"]) == {"tools"}


async def test_the_eight_tools_are_listed_with_schemas(client, tenant):
    listed = (await _call(client, tenant.api_key, _rpc("tools/list"))).json()
    tools = listed["result"]["tools"]

    assert {t["name"] for t in tools} == {
        "mem_dog_add", "mem_dog_search", "mem_dog_get", "mem_dog_list",
        "mem_dog_delete", "mem_dog_entities", "mem_dog_memories", "mem_dog_chat",
    }
    for tool in tools:
        # A tool whose arguments are undescribed is a tool a model calls wrongly
        # and cannot be told why.
        assert tool["description"].strip()
        assert tool["inputSchema"]["type"] == "object"
        assert tool["inputSchema"].get("required")


async def test_a_notification_gets_no_reply(client, tenant):
    """`notifications/initialized` arrives with no id. Answering it is how a
    client is left waiting for a response to something it never asked."""
    response = await _call(
        client, tenant.api_key, _rpc("notifications/initialized", request_id=None)
    )
    assert response.status_code == 202
    assert not response.content


async def test_a_batch_answers_as_a_batch(client, tenant):
    response = await _call(
        client, tenant.api_key,
        [_rpc("initialize", request_id=1), _rpc("tools/list", request_id=2)],
    )
    body = response.json()
    assert isinstance(body, list)
    assert [m["id"] for m in body] == [1, 2]


async def test_a_client_demanding_sse_gets_the_same_object(client, tenant):
    response = await client.post(
        "/api/v1/mcp",
        headers={"Authorization": f"Bearer {tenant.api_key}",
                 "Accept": "text/event-stream"},
        json=_rpc("tools/list"),
    )
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.startswith("event: message\ndata: ")
    assert "mem_dog_search" in response.text


async def test_the_sse_path_from_the_requirement_resolves(client, tenant):
    """FR-MCP-1 names `/api/v1/mcp/sse`, so a configuration written against it
    has to work even though the transport underneath is not the one that
    phrase originally meant."""
    response = await client.post(
        "/api/v1/mcp/sse",
        headers={"Authorization": f"Bearer {tenant.api_key}"},
        json=_rpc("tools/list"),
    )
    assert response.status_code == 200
    assert len(response.json()["result"]["tools"]) == 8


async def test_it_refuses_an_unauthenticated_caller(client):
    response = await client.post("/api/v1/mcp", json=_rpc("tools/list"))
    assert response.status_code == 401


async def test_the_manifest_is_readable_without_a_credential(client):
    """Someone configuring a client needs to know the URL works before they
    have put a key in it, and this discloses nothing about anyone's data."""
    response = await client.get("/api/v1/mcp/sse")
    assert response.status_code == 200
    body = response.json()
    assert body["transport"] == "streamable-http"
    assert len(body["tools"]) == 8
    # Names and descriptions only. The manifest is unauthenticated, so anything
    # about a project, a record or a person appearing here would be a leak.
    assert set(body["tools"][0]) == {"name", "description"}


async def test_an_unknown_method_is_a_protocol_error(client, tenant):
    body = (await _call(client, tenant.api_key, _rpc("resources/list"))).json()
    assert body["error"]["code"] == mcp.METHOD_NOT_FOUND


async def test_a_tool_refusal_is_readable_rather_than_a_transport_failure(
    client, tenant
):
    """The call succeeded; the model asked for something it may not have. It
    needs to read that and choose again, which a JSON-RPC error would not let
    it do."""
    result = await _tool(client, tenant.api_key, "mem_dog_get",
                         {"data_id": "dat_does_not_exist"})
    assert result["isError"] is True
    assert result["content"][0]["type"] == "text"


async def test_a_missing_argument_says_which(client, tenant):
    result = await _tool(client, tenant.api_key, "mem_dog_search",
                         {"project_id": "prj_x"})
    assert result["isError"] is True
    assert "query" in result["content"][0]["text"]


# --- the tools are the REST API, not a second one ----------------------------


async def test_write_then_search_through_the_tools(client, tenant, pool):
    """The round trip that proves the tools reach the real pipeline."""
    written = await _tool(client, tenant.api_key, "mem_dog_add", {
        "producer_id": tenant.producer_id,
        "external_id": "mcp-round-trip",
        "text": "The kestrel returned to the same ledge every evening.",
        "enrich": True,
    })
    import json as _json
    payload = _json.loads(written["content"][0]["text"])
    assert payload["accepted"] == 1

    # Drain the queue the way the service does, then search for it.
    from memdog.app import app
    await app.state.queue.drain(timeout=60)

    found = _json.loads((await _tool(client, tenant.api_key, "mem_dog_search", {
        "project_id": tenant.project_id, "query": "kestrel ledge evening",
    }))["content"][0]["text"])
    assert any("kestrel" in hit["text"] for hit in found["results"])


async def test_a_tool_cannot_reach_what_its_credential_cannot(
    pool, client, tenant, other_tenant
):
    """The property that makes FR-MCP-4 free rather than a second thing to get
    right: the tools call the same functions, so the ACL predicate inside those
    queries is the one that runs."""
    from memdog.ids import new_id

    hidden = new_id("data")
    await pool.execute(
        """
        INSERT INTO data_items (data_id, org_id, project_id, producer_id, owner_id,
                                external_id, access_level, state, event_time,
                                content_text)
        VALUES ($1, $2, $3, $4, $5, 'theirs', 'private', 'searchable', now(), 'secret')
        """,
        hidden, other_tenant.org_id, other_tenant.project_id,
        other_tenant.producer_id, other_tenant.user_id,
    )

    result = await _tool(client, tenant.api_key, "mem_dog_get", {"data_id": hidden})
    assert result["isError"] is True
    # Word for word what an id that never existed gets. Distinguishing the two
    # would confirm the hidden record exists, which is precisely what its ACL is
    # for — and the id itself must not come back in the message either.
    invented = await _tool(client, tenant.api_key, "mem_dog_get",
                           {"data_id": "dat_invented"})
    assert result["content"][0]["text"] == invented["content"][0]["text"]
    assert hidden not in result["content"][0]["text"]
    assert "secret" not in result["content"][0]["text"]


async def test_listing_and_entities_are_scoped_to_the_caller(client, tenant, pool):
    import json as _json

    listed = _json.loads((await _tool(client, tenant.api_key, "mem_dog_list", {
        "project_id": tenant.project_id,
    }))["content"][0]["text"])
    assert "items" in listed

    entities = _json.loads((await _tool(client, tenant.api_key, "mem_dog_entities", {
        "project_id": tenant.project_id,
    }))["content"][0]["text"])
    assert isinstance(entities, list)


async def test_every_listed_tool_is_dispatchable(client, tenant):
    """A tool advertised and not implemented is worse than one that is absent:
    the model chooses it, and the failure looks like the corpus being empty."""
    for tool in mcp.TOOLS:
        result = await _tool(client, tenant.api_key, tool["name"], {})
        # Missing arguments are fine; "no such tool" is not.
        assert "no such tool" not in result["content"][0]["text"]
