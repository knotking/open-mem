/**
 * The ceiling, held against the server's rule.
 *
 * A wrong answer here is not a rendering bug. If the console is more permissive
 * than `acl.py`, it offers a level the API rejects and the write fails for a
 * reason nobody can see. If it is less permissive, it silently hides a level
 * somebody meant to use, and the record ends up narrower than intended.
 *
 * `acl.py`'s rule, transcribed so the correspondence is checkable rather than
 * remembered:
 *
 *     default = ORG if connection_scope == "shared" else PRIVATE
 *     if connection_scope is not None and level is wider than default: reject
 *
 * The two things that follow, and that these tests pin:
 *   - **no connection means no ceiling** — a direct client write has no scope
 *     to exceed, and the caller owns the record;
 *   - **every other non-null scope caps at private**, `shared` excepted. Not
 *     just `personal`. The server's condition is about `default`, not about the
 *     word "personal".
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { CONNECTION_SCOPES, LEVELS, ceilingFor } from "./acl.ts";

const rank = (key: string) => LEVELS.find((l) => l.key === key)!.rank;

test("a personal connection caps a write at private", () => {
  // Personal data in a team organisation stays personal whatever the project
  // default says. This is the leak connection-scoped ACLs exist to prevent.
  assert.equal(ceilingFor("personal"), rank("private"));
});

test("a shared connection caps a write at org, not public", () => {
  assert.equal(ceilingFor("shared"), rank("org"));
  assert.ok(ceilingFor("shared") < rank("public"),
    "a shared connection must not be able to publish a share link");
});

test("no connection is no ceiling", () => {
  // The rule is about a *connection's* scope. A direct client write has none,
  // so there is nothing to exceed — the caller is the owner deciding about
  // their own record. Treating this as restrictive would be a different bug:
  // it would hide levels from the person entitled to choose them.
  for (const absent of [null, undefined, ""]) {
    assert.equal(ceilingFor(absent), rank("public"), `${String(absent)} was capped`);
  }
});

test("an unrecognised scope fails closed, because the server does", () => {
  // The regression this file was written for. `ceilingFor` used to return
  // `public` for any string it did not recognise, which agreed with the server
  // only because `connections.scope` is CHECK-constrained to exactly two
  // values. `acl.py` caps *every* non-null scope that is not "shared" at
  // private, so a third scope would have made the console offer `public` while
  // the API rejected it — visible only as a write that failed for no stated
  // reason.
  for (const unknown of ["team", "project", "PERSONAL", "Shared", "unknown"]) {
    assert.equal(ceilingFor(unknown), rank("private"),
      `${unknown} was not capped at private`);
  }
});

test("the scope list matches what the database will accept", () => {
  // `connections.scope` is `CHECK (scope IN ('personal', 'shared'))` and
  // `control.set_connection_scope` refuses anything else. If a scope is added
  // server-side, this fails and sends someone to `ceilingFor` — which is the
  // whole point of writing the enumeration down here.
  assert.deepEqual([...CONNECTION_SCOPES], ["personal", "shared"]);
  for (const scope of CONNECTION_SCOPES) {
    assert.ok(ceilingFor(scope) <= rank("org"),
      `${scope} must not reach public through a connection`);
  }
});

test("the levels are ordered the way the server ranks them", () => {
  // The ceiling is a number compared against `LEVELS[].rank`, so a reordering
  // here silently changes which options the picker disables.
  assert.deepEqual(
    LEVELS.map((l) => l.key),
    ["private", "restricted", "shared", "org", "public"],
  );
  LEVELS.forEach((level, index) => {
    assert.equal(level.rank, index, `${level.key} is ranked out of order`);
  });
});

test("a ceiling permits everything at or below it and nothing above", () => {
  // How the picker actually uses this: `disabled={ceiling < l.rank}`.
  for (const scope of [null, "personal", "shared", "team"]) {
    const ceiling = ceilingFor(scope);
    const offered = LEVELS.filter((l) => ceiling >= l.rank).map((l) => l.key);
    const refused = LEVELS.filter((l) => ceiling < l.rank).map((l) => l.key);

    assert.ok(offered.includes("private"),
      `${String(scope)}: private must always be offerable — narrowing is always allowed`);
    for (const key of refused) {
      assert.ok(rank(key) > ceiling, `${String(scope)}: ${key} refused below the ceiling`);
    }
  }
});
