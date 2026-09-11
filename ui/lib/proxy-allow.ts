/**
 * The browser's only route to the API, as data.
 *
 * Extracted from the route handler for two reasons, both of which had already
 * cost something.
 *
 * **It is testable.** `allowed()` is the security boundary of the whole
 * console — an open proxy in front of an authenticated API hands the browser
 * every endpoint the server can reach — and nothing exercised it.
 *
 * **There is now one copy.** `scripts/check-proxy-paths.mjs` used to scrape
 * this file for `/^…$/` with a regular expression, which cannot tell these
 * three lists apart and quietly misses any pattern written in a shape the
 * scraper does not anticipate. A missed pattern makes the build check wrong in
 * whichever direction is least visible. It imports these arrays now.
 */

export const ALLOWED = [
  /^api\/v1\/write$/,
  /^api\/v1\/retrieve$/,
  /^api\/v1\/ask$/,
  /^api\/v1\/crawlers$/,
  /^api\/v1\/crawlers\/[\w-]+$/,
  /^api\/v1\/crawlers\/[\w-]+\/(dry-run|run|runs)$/,
  // Point at a Scholar profile, get a crawler for that researcher's papers.
  /^api\/v1\/crawlers\/from-scholar$/,
  /^api\/v1\/crawl-runs\/[\w-]+$/,
  /^api\/v1\/crawl-tick$/,
  /^api\/v1\/prompts$/,
  /^api\/v1\/projects\/[\w-]+\/entities(\?.*)?$/,
  /^api\/v1\/graph\/predicates$/,
  /^api\/v1\/projects\/[\w-]+\/crawlers$/,
  /^api\/v1\/health$/,
  /^api\/v1\/data\/[A-Za-z0-9_]+(\?.*)?$/,
  /^api\/v1\/data\/[A-Za-z0-9_]+\/artifacts$/,
  /^api\/v1\/data\/[A-Za-z0-9_]+\/content$/,
  // The scope and the key are both variables at the call site -- the settings
  // editor picks the scope per row -- so the pattern cannot spell the four
  // scopes out. The API is the validator that matters: `put` refuses an unknown
  // scope, an unknown key, a scope the setting is not allowed at, and a write
  // below a lock. This is a route, not a second policy.
  /^api\/v1\/settings\/[A-Za-z0-9_]+\/[A-Za-z0-9_]+$/,
  /^api\/v1\/models\/assignments$/,
  /^api\/v1\/reprocess$/,
  // Expiry: the sweep, and the forecast of what it would take.
  /^api\/v1\/expiry\/sweep$/,
  // Who in the room is a principal here, answered before the write rather
  // than discovered after it.
  /^api\/v1\/meetings\/attendees$/,
  /^api\/v1\/projects\/[A-Za-z0-9_]+\/expiring(\?.*)?$/,
  /^api\/v1\/artifacts\/stale(\?.*)?$/,
  /^api\/v1\/projects\/[A-Za-z0-9_]+\/staircase$/,
  /^api\/v1\/projects\/[A-Za-z0-9_]+\/overview$/,
  /^api\/v1\/projects\/[A-Za-z0-9_]+\/data(\?.*)?$/,
  /^api\/v1\/projects\/[A-Za-z0-9_]+\/memory-types$/,
  /^api\/v1\/memories$/,
  /^api\/v1\/memories\/[A-Za-z0-9_]+(\?.*)?$/,
  /^api\/v1\/memories\/[A-Za-z0-9_]+\/members\/[A-Za-z0-9_]+$/,
  /^api\/v1\/projects\/[A-Za-z0-9_]+\/memories$/,
  // What the corpus is about, aggregated from model keywords.
  /^api\/v1\/projects\/[A-Za-z0-9_]+\/keywords(\?.*)?$/,
  /^api\/v1\/projects\/[A-Za-z0-9_]+\/tags(\?.*)?$/,
  /^api\/v1\/templates$/,
  // The public demo. Unauthenticated by design -- see `public_demo.py`.
  /^api\/v1\/public\/demos$/,
  /^api\/v1\/public\/ask$/,
  /^api\/v1\/memories\/[\w-]+\/context(\?.*)?$/,
  /^api\/v1\/memories\/[A-Za-z0-9_]+\/members$/,
  // Deriving: what can be made from a memory, and what has been.
  /^api\/v1\/generators$/,
  /^api\/v1\/memories\/[A-Za-z0-9_]+\/derive$/,
  /^api\/v1\/memories\/[A-Za-z0-9_]+\/artifacts$/,
  // Checkpoint timelines: the timeline, one recheck, and the memory-wide
  // enrich that is how a timeline written without enrichment is promoted
  // into the graph later.
  /^api\/v1\/memories\/[A-Za-z0-9_]+\/checkpoints$/,
  // What moved across a span of a timeline. The query string carries `from`,
  // `to` and `net`, so the pattern has to admit one.
  /^api\/v1\/memories\/[A-Za-z0-9_]+\/changes(\?.*)?$/,
  /^api\/v1\/memories\/[A-Za-z0-9_]+\/checkpoints\/[A-Za-z0-9_]+\/recheck$/,
  /^api\/v1\/memories\/[A-Za-z0-9_]+\/enrich$/,
  // Repository analysis. `snapshots/{id}` is listed before the `{case_id}`
  // form for the same reason the routes are declared in that order on the
  // server: the literal segment has to win, or a snapshot id is read as a case.
  // Upload sessions. Without these the console can only inline base64, which
  // caps what it can add at what fits in a JSON body -- and a book-sized PDF
  // does not. Their absence is why documents could not be added at all.
  /^api\/v1\/uploads$/,
  /^api\/v1\/uploads\/[A-Za-z0-9_]+\/bytes$/,
  /^api\/v1\/uploads\/[A-Za-z0-9_]+\/complete$/,
  /^api\/v1\/projects\/[A-Za-z0-9_]+\/repos$/,
  /^api\/v1\/repos\/analyze$/,
  /^api\/v1\/repos\/snapshots\/[A-Za-z0-9_]+$/,
  /^api\/v1\/repos\/[A-Za-z0-9_]+\/snapshots(\?.*)?$/,
  // Removing a repository. Bare `{case_id}` is last of the three, so the
  // two longer forms above claim their paths first.
  /^api\/v1\/repos\/[A-Za-z0-9_]+$/,
  // The hierarchy: `part_of` in both directions, and the link that builds it.
  /^api\/v1\/memories\/[A-Za-z0-9_]+\/tree(\?.*)?$/,
  /^api\/v1\/memories\/[A-Za-z0-9_]+\/links(\?.*)?$/,
  /^api\/v1\/data\/[A-Za-z0-9_]+\/versions$/,
  // One revision, read whole. The listing above only previews.
  /^api\/v1\/data\/[A-Za-z0-9_]+\/versions\/[A-Za-z0-9_]+$/,

  // Alerts. Absent until now, which meant every call the Alerts screen made was
  // refused here -- the screen rendered and never loaded anything, and that
  // looked like a design problem rather than a routing one.
  /^api\/v1\/alerts$/,
  /^api\/v1\/alerts\/surfaces$/,
  /^api\/v1\/alerts\/[A-Za-z0-9_]+$/,
  /^api\/v1\/alerts\/[A-Za-z0-9_]+\/(backtest|enabled|runs)(\?.*)?$/,
  /^api\/v1\/projects\/[A-Za-z0-9_]+\/alerts$/,
  /^api\/v1\/alert-events(\?.*)?$/,
  // Standing queries: the same shape as alerts, matching on the record rather
  // than on a transition.

  /^api\/v1\/standing-queries$/,
  /^api\/v1\/standing-queries\/[A-Za-z0-9_]+$/,
  /^api\/v1\/standing-queries\/[A-Za-z0-9_]+\/(backtest|enabled|matches)(\?.*)?$/,
  /^api\/v1\/projects\/[A-Za-z0-9_]+\/standing-queries$/,
  /^api\/v1\/event-subscriptions$/,
  /^api\/v1\/event-subscriptions\/[A-Za-z0-9_]+$/,
  /^api\/v1\/event-subscriptions\/[A-Za-z0-9_]+\/(rotate|replay|deliveries)(\?.*)?$/,
  /^api\/v1\/projects\/[A-Za-z0-9_]+\/event-subscriptions$/,

  // Compaction.
  /^api\/v1\/compaction\/algorithms$/,
  /^api\/v1\/compaction\/jobs$/,
  /^api\/v1\/compaction\/jobs\/[A-Za-z0-9_]+$/,
  /^api\/v1\/compaction\/jobs\/[A-Za-z0-9_]+\/(preview|run|enabled|runs)(\?.*)?$/,
  /^api\/v1\/projects\/[A-Za-z0-9_]+\/compaction\/jobs$/,
  /^api\/v1\/data\/[A-Za-z0-9_]+\/memories$/,
  // Cases -- a subject and its timeline. The console had no case screen when
  // these endpoints landed, which is why they were absent here; a screen
  // without them would have rendered an empty list against a project full of
  // subjects and looked like a project with no subjects in it.
  /^api\/v1\/cases$/,
  /^api\/v1\/cases\/[A-Za-z0-9_]+\/timeline$/,
  /^api\/v1\/projects\/[A-Za-z0-9_]+\/cases$/,
  /^api\/v1\/audit(\?.*)?$/,
  /^api\/v1\/events(\?.*)?$/,
  /^api\/v1\/data\/[A-Za-z0-9_]+\/enrich$/,
  /^api\/v1\/projects(\?.*)?$/,
  /^api\/v1\/shares$/,
  /^api\/v1\/shares\/[A-Za-z0-9_]+$/,
  /^api\/v1\/deletions$/,
  /^api\/v1\/users\/me\/deletion$/,
  /^api\/v1\/data\/[A-Za-z0-9_]+\/erasure$/,
  /^api\/v1\/settings\/effective(\?.*)?$/,
  /^api\/v1\/models$/,
  /^api\/v1\/agents\/[A-Za-z0-9_]+\/config(\?.*)?$/,
  /^api\/v1\/organizations\/members$/,
  /^api\/v1\/users\/me$/,
  /^api\/v1\/users\/me\/api-keys$/,
  /^api\/v1\/producers$/,
  // Enable and disable a producer, revoke a key, revoke an invite, and the
  // per-scope freshness the Producers screen reads.
  /^api\/v1\/producers\/[A-Za-z0-9_]+$/,
  /^api\/v1\/users\/me\/api-keys\/[A-Za-z0-9_]+$/,
  /^api\/v1\/invites$/,
  // Groups: a sharing principal the ACL predicate already resolves, with no way
  // to create one until now.
  /^api\/v1\/groups$/,
  /^api\/v1\/groups\/[A-Za-z0-9_]+\/members$/,
  // The certificate a deletion is answered for with.
  /^api\/v1\/data\/[A-Za-z0-9_]+\/erasure$/,
  /^api\/v1\/invites\/[A-Za-z0-9_]+$/,
  /^api\/v1\/projects\/[A-Za-z0-9_]+\/source-lag$/,
  /^api\/v1\/producers\/[A-Za-z0-9_]+\/inbound$/,
  /^api\/v1\/producers\/[A-Za-z0-9_]+\/signing-secret$/,
  /^api\/v1\/producers\/[A-Za-z0-9_]+\/test-delivery$/,
  /^api\/v1\/producers\/[A-Za-z0-9_]+\/deliveries(\?.*)?$/,
  /^api\/v1\/platform\/health$/,
  // The connector catalog and the credentials it is created against. Missing
  // these is why the "Pull from an app" panel rendered an empty category list:
  // the endpoints existed, the panel existed, and the browser's only route to
  // the API refused the request — so `apps` stayed empty and the failure looked
  // like a catalog with nothing in it.
  /^api\/v1\/connectors$/,
  /^api\/v1\/connections(\?.*)?$/,
  /^api\/v1\/crawlers\/[\w-]+\/connection$/,
];

// Reachable by GET, with no credential of any kind.
//
// `api/v1/mcp` is one path serving two things. GET is a manifest — the
// transport and the tool names, disclosing nothing about anyone's data, which
// is why the API leaves it unauthenticated too.
export const GET_ONLY = [
  /^api\/v1\/mcp$/,
];

// Paths that must carry the *caller's* credential and never the console's.
//
// POST on `api/v1/mcp` is a tool call. Everywhere else this proxy replaces the
// caller's headers with the console's own, falling back to the service key when
// nobody is signed in — which is right for a browser panel and catastrophic
// here: an MCP client has no browser session, so a plain allow-list entry would
// publish an unauthenticated MCP server over whatever that key can reach.
//
// So these forward the key the client sent and nothing else. A request without
// one is refused rather than downgraded, because the downgrade is exactly the
// hole: it would answer, with the platform's own access, and look like it
// worked.
export const CALLER_CREDENTIAL = [
  /^api\/v1\/mcp$/,
];

/**
 * Routes that must carry **no** console credential.
 *
 * The public demo is reachable without signing in, and `apiFetch` falls back to
 * the deployment's service key when nobody is signed in — so without this a
 * visitor's question would travel on the console's own credential. It would
 * work, and it would mean an anonymous request had been silently promoted to
 * whatever that key can do. The endpoint needs no credential at all: it decides
 * what it will answer from configuration, not from who is asking.
 *
 * Kept separate from CALLER_CREDENTIAL, which is the opposite case — a route
 * that demands the caller's own key.
 */
export const NO_CREDENTIAL = [
  /^api\/v1\/public\/demos$/,
  /^api\/v1\/public\/ask$/,
];

export function allowed(path: string, method: string): boolean {
  if (method === "GET" && GET_ONLY.some((pattern) => pattern.test(path))) return true;
  if (method === "POST" && CALLER_CREDENTIAL.some((pattern) => pattern.test(path))) {
    return true;
  }
  return ALLOWED.some((pattern) => pattern.test(path));
}
