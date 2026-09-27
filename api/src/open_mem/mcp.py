"""The MCP server: this corpus, as tools an assistant can call.

Eight tools over JSON-RPC. Each one calls the same function the corresponding
REST endpoint calls, which is the whole reason FR-MCP-3 and FR-MCP-4 -- same
credentials, same access control -- come for free rather than needing a second
implementation. The ACL predicate lives inside those queries, so a tool cannot
return a record its caller could not have retrieved directly, and there is no
place here where that could be got wrong.

## Why the transport is stateless

FR-MCP-1 asks for SSE, and the transport that phrase named in 2024 works like
this: the client opens a long-lived `GET`, the server replies with a URL to
`POST` to, and every subsequent message is correlated back to that stream by
session id. It requires the process holding the stream to be the process that
receives the posts.

This deployment is Cloud Run, scaling to zero and out to four. A post can land
on an instance that has never heard of the stream, and the instance holding a
stream can be reaped mid-conversation. Session affinity would have to be bought
back with sticky routing or shared state -- infrastructure bought to support a
transport, rather than a transport chosen to suit the infrastructure.

So this implements the **streamable HTTP** transport instead, which the protocol
gained precisely for this shape: one endpoint, each `POST` self-contained, the
response returned directly. A client that asks for `text/event-stream` gets its
response as a single SSE event, which satisfies the clients that require that
content type without inventing a session to hold.

`/api/v1/mcp/sse` is kept as an alias of the same endpoint so a configuration
written against the requirement still resolves.

## Tools describe what they will not do

Every schema below names its required arguments, and every description says what
the tool is *for* rather than what it wraps. An assistant choosing between
`open_mem_search` and `open_mem_chat` needs to know that the first returns records
and the second returns prose with citations -- calling that "search the corpus"
twice would make the choice arbitrary.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .auth import DATA_READ, DATA_WRITE, AuthError, Principal
from .retrieval import NotFound

log = logging.getLogger(__name__)

# The revision this speaks. Reported at `initialize`; a client asking for
# something else is told what it got rather than being failed, which is what
# the specification asks for.
PROTOCOL_VERSION = "2025-03-26"

SERVER_INFO = {"name": "open-mem", "version": "0.1.0"}

# JSON-RPC error codes. The three the specification uses, and no invented ones:
# a client cannot act on a code it has never heard of.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def _text(payload: Any) -> dict:
    """A tool result, as the content block the protocol expects.

    Everything is returned as JSON text rather than a bespoke structure. An
    assistant reads it either way, and a shape that mirrors the REST response is
    one fewer thing that can disagree with the API it is a view of.
    """
    return {
        "content": [
            {"type": "text",
             "text": json.dumps(payload, indent=2, default=str, ensure_ascii=False)}
        ]
    }


def _failed(message: str) -> dict:
    """A tool that could not do its job.

    `isError` rather than a JSON-RPC error: the call itself succeeded, and the
    model needs to read the reason and choose again. A transport-level error
    would give it nothing to act on.
    """
    return {"content": [{"type": "text", "text": message}], "isError": True}


TOOLS: list[dict] = [
    {
        "name": "open_mem_search",
        "description": (
            "Search a project's records and return the passages that matched, "
            "each with the record it came from and why it matched. Returns "
            "evidence, not an answer -- use open_mem_chat for prose. Add 'graph' "
            "to match to also reach records connected to an entity the query "
            "names, which will not contain the query's words."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["project_id", "query"],
            "properties": {
                "project_id": {"type": "string"},
                "query": {"type": "string"},
                "match": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["vector", "lexical", "graph"]},
                    "description": "Defaults to vector and lexical.",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
        },
    },
    {
        "name": "open_mem_chat",
        "description": (
            "Ask a question of a project's records and get a written answer "
            "with citations. Every factual sentence carries the passage it came "
            "from, and the answer reports whether it was grounded -- an "
            "ungrounded answer means the corpus does not say."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["project_id", "question"],
            "properties": {
                "project_id": {"type": "string"},
                "question": {"type": "string"},
                "match": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["vector", "lexical", "graph"]},
                },
            },
        },
    },
    {
        "name": "open_mem_add",
        "description": (
            "Write a record into a project. Recording is immediate; enrichment "
            "-- summarising, embedding, entity extraction -- is opt-in through "
            "'enrich' because it costs money, and a record is searchable only "
            "once it has been embedded."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["producer_id", "external_id", "text"],
            "properties": {
                "producer_id": {"type": "string",
                                "description": "The registered producer to write through."},
                "external_id": {"type": "string",
                                "description": "Your identifier. Writing it twice updates."},
                "text": {"type": "string"},
                "data_type": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "enrich": {"type": "boolean"},
            },
        },
    },
    {
        "name": "open_mem_get",
        "description": "Fetch one record by its id, with its state and provenance.",
        "inputSchema": {
            "type": "object",
            "required": ["data_id"],
            "properties": {"data_id": {"type": "string"}},
        },
    },
    {
        "name": "open_mem_list",
        "description": (
            "Browse a project's records, newest first. Filter by state to find "
            "what is stored but not yet searchable."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["project_id"],
            "properties": {
                "project_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "state": {"type": "string",
                          "enum": ["stored", "searchable", "enriched"]},
            },
        },
    },
    {
        "name": "open_mem_delete",
        "description": (
            "Delete one record and everything derived from it -- chunks, "
            "vectors, artifacts. The deletion is audited and the audit record "
            "outlives what it describes."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["data_id"],
            "properties": {"data_id": {"type": "string"}},
        },
    },
    {
        "name": "open_mem_entities",
        "description": (
            "The people, organizations and things a project's records mention, "
            "with how many records evidence each. An entity nobody can see a "
            "mention of is not listed."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["project_id"],
            "properties": {
                "project_id": {"type": "string"},
                "type": {"type": "string",
                         "enum": ["person", "organization", "location", "product",
                                  "event", "topic", "other"]},
                "query": {"type": "string"},
            },
        },
    },
    {
        "name": "open_mem_memories",
        "description": (
            "The project's memories -- lifecycle containers that group records "
            "and decide when they expire -- with live member counts."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["project_id"],
            "properties": {"project_id": {"type": "string"}},
        },
    },
]

TOOL_NAMES = {tool["name"] for tool in TOOLS}


async def call_tool(state, principal: Principal, name: str, arguments: dict) -> dict:
    """Run one tool against the same functions the REST API runs.

    No tool reaches the database directly. Every one goes through the service
    function its endpoint uses, so the capability check, the ACL predicate and
    the audit write are the ones that already exist -- and a change to any of
    them cannot leave this surface behind.
    """
    from . import entities as entities_mod
    from .chat import ask
    from .contracts import (
        AskRequest, Inline, RetrieveFilter, RetrieveRequest,
        WriteItem, WriteOptions, WriteRequest,
    )
    from .deletion import request_deletion
    from .retrieval import get_item, list_items, list_memories, retrieve
    from .write import write_items

    pool = state.pool

    if name == "open_mem_search":
        found = await retrieve(
            pool, state.embedder, principal,
            RetrieveRequest(
                query=arguments["query"],
                filter=RetrieveFilter(project_id=arguments["project_id"]),
                match=arguments.get("match") or ["vector", "lexical"],
                limit=int(arguments.get("limit") or 10),
            ),
            embed_generator=state.current_generators["embedding"],
            graph=state.graph,
        )
        return _text(found.model_dump(mode="json"))

    if name == "open_mem_chat":
        answered = await ask(
            pool, state.embedder,
            await state.engines.answerer_for(pool, org_id=principal.org_id),
            principal,
            AskRequest(
                question=arguments["question"],
                filter=RetrieveFilter(project_id=arguments["project_id"]),
                match=arguments.get("match") or ["vector", "lexical"],
            ),
            embed_generator=state.current_generators["embedding"],
            graph=state.graph,
        )
        return _text(answered.model_dump(mode="json"))

    if name == "open_mem_add":
        written = await write_items(
            pool, state.queue, state.blobs, state.settings, principal,
            WriteRequest(
                producer_id=arguments["producer_id"],
                items=[WriteItem(
                    external_id=arguments["external_id"],
                    content=Inline(text=arguments["text"]),
                    data_type=arguments.get("data_type"),
                    tags=list(arguments.get("tags") or []),
                )],
                options=WriteOptions(enrich=arguments.get("enrich")),
            ),
            None,
        )
        return _text(written.model_dump(mode="json"))

    if name == "open_mem_get":
        return _text(dict(await get_item(pool, principal, arguments["data_id"])))

    if name == "open_mem_list":
        return _text(await list_items(
            pool, principal, arguments["project_id"],
            limit=int(arguments.get("limit") or 25),
            state=arguments.get("state"),
        ))

    if name == "open_mem_delete":
        principal.require(DATA_WRITE)
        result = await request_deletion(
            pool, state.queue, principal,
            selector={"data_ids": [arguments["data_id"]]},
            reason="deleted through MCP",
        )
        return _text({
            "run_id": result.run_id,
            "deleting": len(result.data_ids),
            "retained": [{"data_id": d, "reason": r} for d, r in result.retained],
        })

    if name == "open_mem_entities":
        return _text(await entities_mod.list_entities(
            pool, principal, arguments["project_id"],
            kind=arguments.get("type"), query=arguments.get("query"),
        ))

    if name == "open_mem_memories":
        return _text(await list_memories(pool, principal, arguments["project_id"]))

    return _failed(f"no such tool: {name}")


async def dispatch(state, principal: Principal, message: dict) -> dict | None:
    """One JSON-RPC message in, one response out -- or `None` for a notification.

    A notification has no `id` and takes no reply. Returning one anyway is the
    common way to make a client hang waiting for a response to something it
    never asked a question about.
    """
    if message.get("jsonrpc") != "2.0":
        return _error(message.get("id"), INVALID_REQUEST, "expected jsonrpc 2.0")

    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") or {}

    if request_id is None:
        # `notifications/initialized` and `notifications/cancelled` are the ones
        # that arrive. Neither needs anything of us.
        return None

    if method == "initialize":
        return _result(request_id, {
            "protocolVersion": PROTOCOL_VERSION,
            # Only what is actually implemented. Announcing prompts or resources
            # would have clients ask for them and get nothing.
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": (
                "Records live in projects. Search returns evidence; chat returns "
                "a written answer with citations. Writing needs a producer id, "
                "and enrichment is opt-in because it costs money."
            ),
        })

    if method == "ping":
        return _result(request_id, {})

    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})

    if method == "tools/call":
        name = params.get("name")
        if name not in TOOL_NAMES:
            return _error(request_id, INVALID_PARAMS, f"no such tool: {name}")
        try:
            return _result(request_id, await call_tool(
                state, principal, name, params.get("arguments") or {}
            ))
        except NotFound:
            # Expected, and deliberately indistinguishable from "not yours".
            # A record the caller cannot see and one that never existed get the
            # same sentence, because telling them apart is the disclosure the
            # ACL exists to prevent. Not logged as an error either -- asking for
            # something that is not there is a normal thing to do.
            return _result(request_id, _failed("no such record, or not visible"))
        except AuthError as exc:
            # A refusal the model should read and work around, not a transport
            # failure. It asked for something this credential may not do.
            return _result(request_id, _failed(str(exc)))
        except KeyError as exc:
            return _result(request_id, _failed(f"missing argument: {exc}"))
        except Exception as exc:  # noqa: BLE001
            log.exception("mcp tool %s failed", name)
            return _result(request_id, _failed(
                f"{name} failed: {exc.__class__.__name__}: {exc}"
            ))

    return _error(request_id, METHOD_NOT_FOUND, f"unknown method: {method}")


def _result(request_id, result) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": code, "message": message}}


def as_sse(payload: dict) -> str:
    """One response as a single server-sent event.

    Clients that insist on `text/event-stream` get the same object they would
    have got as JSON. There is no stream to keep open afterwards, because there
    is no session on this side to keep it for.
    """
    return f"event: message\ndata: {json.dumps(payload)}\n\n"
