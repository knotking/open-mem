/**
 * Every path the console calls must be allowed by the proxy.
 *
 * The Alerts screen shipped, deployed and rendered while every request it made
 * was refused at `/api/proxy` — its paths were simply never added to the
 * allow-list. Nothing failed loudly: the screen looked like a design problem
 * rather than a routing one, and the same thing then happened to Compaction.
 *
 * So this reads the `call(...)` sites out of the components and tests each
 * against the real allow-list. It is a script rather than a unit test because
 * the console has no test runner, and a check nobody runs is not a check —
 * `npm run build` runs it.
 */
import { readFileSync, readdirSync } from "node:fs";
import { ALLOWED, CALLER_CREDENTIAL, GET_ONLY } from "../lib/proxy-allow.ts";
import { join } from "node:path";

// Imported, not scraped. This used to read the route file and pull `/^…$/` out
// with a regular expression, which cannot distinguish the three lists and
// silently misses any pattern written in a shape it does not anticipate -- and
// a missed pattern makes this check wrong in the direction nobody notices.
const patterns = [...ALLOWED, ...GET_ONLY, ...CALLER_CREDENTIAL];

const called = new Set();
for (const file of readdirSync("components")) {
  if (!file.endsWith(".tsx")) continue;
  const src = readFileSync(join("components", file), "utf8");
  // `call<T>("api/v1/…")` and the template-literal form.
  for (const m of src.matchAll(/call<[^>]*>\(\s*[`"]([^`"]+)[`"]/g)) called.add(m[1]);
  for (const m of src.matchAll(/call\(\s*[`"]([^`"]+)[`"]/g)) called.add(m[1]);
}

/**
 * A template into something the allow-list can be tested against.
 *
 * `${...}` can nest braces — `${kind ? `?type=${kind}` : ""}` is one expression
 * appending an optional query — so this matches braces rather than using a
 * regex, and drops an interpolation that is not its own path segment, since
 * those are query fragments rather than ids.
 */
function concrete(path) {
  let out = "", i = 0;
  while (i < path.length) {
    if (path[i] === "$" && path[i + 1] === "{") {
      let depth = 1, j = i + 2;
      while (j < path.length && depth > 0) {
        if (path[j] === "{") depth++;
        else if (path[j] === "}") depth--;
        j++;
      }
      const ownSegment = out.endsWith("/");
      out += ownSegment ? "PLACEHOLDER0" : "";
      i = j;
    } else {
      out += path[i++];
    }
  }
  return out;
}

const missing = [...called].filter((p) => !patterns.some((re) => re.test(concrete(p))));
if (missing.length) {
  console.error("These paths are called by the console and refused by the proxy:\n");
  for (const p of missing) console.error("  " + p);
  console.error("\nAdd them to ALLOWED in lib/proxy-allow.ts.");
  process.exit(1);
}
console.log(`proxy allow-list covers all ${called.size} console call sites`);
