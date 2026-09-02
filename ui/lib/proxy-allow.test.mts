/**
 * The proxy allow-list, which is the console's security boundary.
 *
 * Everything the browser can reach goes through `allowed()`. An open proxy in
 * front of an authenticated API hands the browser every endpoint the server can
 * reach, including ones this UI never uses — so the interesting tests here are
 * the refusals, not the permissions.
 *
 * The build check (`npm run check:proxy`) already proves every call site the
 * console makes is covered. That is the opposite question from this file: it
 * asks whether anything *else* is covered too.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { ALLOWED, CALLER_CREDENTIAL, GET_ONLY, allowed } from "./proxy-allow.ts";

test("a path nobody put on the list is refused", () => {
  for (const path of [
    "api/v1/admin/keys",
    "api/v1/internal/dump",
    "api/v2/write",
    "api/v1/write/../retrieve",
    "",
    "api/v1",
  ]) {
    assert.equal(allowed(path, "GET"), false, `${path} was permitted`);
  }
});

test("the list is a list, not a wildcard — most of the API is not on it", () => {
  // A sanity check on the shape of the thing. `api/v1/organizations/members`
  // *is* proxied, which an earlier version of this file assumed it was not —
  // so the assertion is about proportion rather than about any one path.
  const surface = [
    "api/v1/write", "api/v1/retrieve", "api/v1/health",       // on it
    "api/v1/admin/keys", "api/v1/internal/dump",              // not
    "api/v1/debug", "api/v1/config/raw", "api/v1/secrets",    // not
  ];
  const permitted = surface.filter((p) => allowed(p, "GET"));
  assert.equal(permitted.length, 3, `permitted more than the three real ones: ${permitted}`);
});

test("a traversal cannot walk out of the segment it was matched in", () => {
  // The id patterns are `[A-Za-z0-9_]+`, so a dot cannot appear in one. Worth
  // asserting rather than assuming: this is the whole reason those classes are
  // spelled out instead of `.+`, and a later edit widening one would be a very
  // quiet mistake.
  for (const path of [
    "api/v1/data/../../admin",
    "api/v1/data/..%2f..%2fadmin",
    "api/v1/memories/../../../etc/passwd",
    "api/v1/data/abc/../../write",
  ]) {
    assert.equal(allowed(path, "GET"), false, `${path} was permitted`);
  }
});

test("an id pattern does not accept a slash, which would swallow a second segment", () => {
  assert.equal(allowed("api/v1/data/abc", "GET"), true, "the real shape must pass");
  assert.equal(allowed("api/v1/data/abc/secret", "GET"), false,
    "a second segment matched into the id would reach unlisted endpoints");
});

test("MCP is GET-listed and POST-listed, and never a plain allow-list entry", () => {
  // The distinction that matters: on POST this proxy must forward the caller's
  // own key rather than the console's. If `api/v1/mcp` were in ALLOWED it would
  // be answered with the platform credential, publishing an unauthenticated MCP
  // server over whatever that key can reach.
  const inPlainList = ALLOWED.some((p) => p.test("api/v1/mcp"));
  assert.equal(inPlainList, false,
    "api/v1/mcp in ALLOWED would answer with the console's own credential");

  assert.equal(allowed("api/v1/mcp", "GET"), true);
  assert.equal(allowed("api/v1/mcp", "POST"), true);
  assert.equal(allowed("api/v1/mcp", "DELETE"), false,
    "only the two methods that were reasoned about");
});

test("a GET-only path is not reachable by a method that changes something", () => {
  for (const pattern of GET_ONLY) {
    const path = pattern.source
      .replace(/^\^/, "").replace(/\$$/, "").replace(/\\\//g, "/");
    if (/[[\](){}+*?]/.test(path)) continue;   // only the literal ones
    for (const method of ["PUT", "PATCH", "DELETE"]) {
      assert.equal(allowed(path, method), ALLOWED.some((p) => p.test(path)),
        `${method} ${path} was permitted by the GET-only list`);
    }
  }
});

test("a query string is refused unless the pattern said it was expected", () => {
  // Documented in the route and learned the hard way: the list is matched
  // against path *plus* query, so an endpoint whose behaviour is selected by a
  // parameter needs `(\\?...)?` or the request is refused with no clue why.
  assert.equal(allowed("api/v1/projects/prj_1/data?limit=50", "GET"), true,
    "this one declares the query and must keep working");
  assert.equal(allowed("api/v1/write?debug=1", "POST"), false,
    "a pattern that did not opt in must not accept a query — the refusal is " +
    "the signal that the pattern needs updating");
});

test("every pattern is anchored at both ends", () => {
  // An unanchored pattern matches anywhere in the path, so `api/v1/health`
  // without `^…$` would also permit `api/v1/health/../../anything`.
  for (const [name, list] of [
    ["ALLOWED", ALLOWED], ["GET_ONLY", GET_ONLY],
    ["CALLER_CREDENTIAL", CALLER_CREDENTIAL],
  ] as const) {
    for (const pattern of list) {
      assert.ok(pattern.source.startsWith("^"), `${name}: ${pattern} is not anchored at the start`);
      assert.ok(pattern.source.endsWith("$"), `${name}: ${pattern} is not anchored at the end`);
    }
  }
});

test("no pattern uses an unbounded wildcard for a path segment", () => {
  // `.+` or `.*` in a segment matches slashes, so one entry silently permits
  // every path beneath it. The id classes are spelled out for this reason.
  for (const pattern of [...ALLOWED, ...GET_ONLY, ...CALLER_CREDENTIAL]) {
    const body = pattern.source.replace(/\(\\\?\.\*\)\?\$$/, "");   // the query suffix is fine
    assert.doesNotMatch(body, /(?<!\\)\.[+*]/,
      `${pattern} uses an unbounded wildcard, which spans path separators`);
  }
});
