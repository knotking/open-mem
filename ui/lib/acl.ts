/**
 * How far a write may be widened, mirrored from the server.
 *
 * `acl.py` owns this rule and this file must agree with it, because the two
 * disagreeing has exactly two shapes and both are bad: the console offering a
 * level the API will reject, or hiding one it would have allowed. The first
 * wastes somebody's time; the second silently narrows what a person meant to
 * share.
 *
 * Lifted out of `Console.tsx` so a test can hold it against the server's rule.
 */

/** Ordered least to most visible — the same order `acl.py` ranks them by. */
export const LEVELS = [
  { key: "private", rank: 0, label: "private — only me" },
  { key: "restricted", rank: 1, label: "restricted — only these principals" },
  { key: "shared", rank: 2, label: "shared — me and these principals" },
  { key: "org", rank: 3, label: "org — everyone in the organization" },
  { key: "public", rank: 4, label: "public — everyone in the org, and share links" },
] as const;

/** The scopes a connection may have. `connections.scope` is CHECK-constrained
 *  to these two, and `control.set_connection_scope` refuses anything else. */
export const CONNECTION_SCOPES = ["personal", "shared"] as const;

/**
 * How far a producer may widen, given its connection.
 *
 * A connection is a credential to somebody else's system and its scope is a
 * **ceiling**, not a default: a personal one caps its writes at `private`,
 * because personal data in a team organisation stays personal whatever the
 * project says. A producer with no connection is a direct client write and is
 * unrestricted — the caller is the owner, deciding about their own record.
 *
 * **Anything else caps at private.** That is not defensive padding, it is what
 * `acl.py` does: its check is `connection_scope is not None and level wider
 * than default`, where `default` is org only for `shared` and private for every
 * other non-null value. This used to return `public` for an unrecognised scope,
 * which agreed with the server only because the database happens to permit
 * exactly two values — a third would have made the console offer a level the
 * API refuses, and the disagreement would show up as a rejected write nobody
 * could explain.
 */
export function ceilingFor(scope: string | null | undefined): number {
  if (scope === null || scope === undefined || scope === "") return 4;
  if (scope === "shared") return 3;
  return 0;
}
