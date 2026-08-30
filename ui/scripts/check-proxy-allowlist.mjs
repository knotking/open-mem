/**
 * Every path the console calls must be reachable through the proxy.
 *
 * Four panels shipped this week that could not load their own data: the
 * connector catalog, the credentials list, the MCP manifest and the entity
 * filter. In each case the endpoint existed, the panel existed, and the
 * browser's only route to the API refused the request with a 403 the panel had
 * no way to report — so "no connectors" was indistinguishable from a catalog
 * that was genuinely empty.
 *
 * Typechecking and building both passed every time. Neither can see this,
 * because the allow-list is a list of strings and the call sites are template
 * literals: nothing connects them but a person remembering.
 *
 * Run in CI, and before any deploy.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

function sources(dir, out = []) {
  for (const name of readdirSync(dir)) {
    if (name === "node_modules" || name === ".next") continue;
    const path = join(dir, name);
    if (statSync(path).isDirectory()) sources(path, out);
    else if (/\.tsx?$/.test(name)) out.push(path);
  }
  return out;
}

const route = readFileSync("app/api/proxy/[...path]/route.ts", "utf8");
function patterns(from, to) {
  const body = route.slice(route.indexOf(from), route.indexOf(to));
  return [...body.matchAll(/\/\^(.+?)\$\/,/g)].map((m) => new RegExp("^" + m[1] + "$"));
}
const ALLOWED = patterns("const ALLOWED", "const GET_ONLY");
const GET_ONLY = patterns("const GET_ONLY", "function allowed");

// A template hole stands for an id. The substitute is deliberately ULID-shaped
// with a prefix, because that is what the patterns have to match in practice.
const FILL = "typ_01ABCDEF23GHJKMNPQRSTUV";
const CALL = /call(?:<[^>]*>)?\(\s*(["'`])/g;

/**
 * Read one string literal starting at `open`, tracking `${...}` nesting.
 *
 * A naive character class stops at the first quote inside a template hole, so
 * `entities${kind ? "?kind=" + kind : ""}` came back truncated and every path
 * with a conditional query string looked broken whether it was or not.
 */
function literalAt(text, quote, open) {
  let out = "";
  for (let i = open; i < text.length; i++) {
    const c = text[i];
    if (c === "\\") { out += c + text[++i]; continue; }
    if (c === quote) return out;
    if (quote === "`" && c === "$" && text[i + 1] === "{") {
      let depth = 1;
      let j = i + 2;
      for (; j < text.length && depth > 0; j++) {
        if (text[j] === "{") depth++;
        else if (text[j] === "}") depth--;
      }
      // Every hole is an id or a rendered query fragment. Both are stood in
      // for below; which one it is cannot be known without running the code.
      out += "${}";
      i = j - 1;
      continue;
    }
    out += c;
  }
  return null;   // unterminated: not our problem to diagnose
}

const failures = [];
for (const file of sources(".")) {
  if (file.includes("scripts/")) continue;
  const text = readFileSync(file, "utf8");
  for (const match of text.matchAll(CALL)) {
    const raw = literalAt(text, match[1], match.index + match[0].length);
    if (!raw || !raw.startsWith("api/")) continue;
    // A hole after a slash is an id. A trailing hole after anything else is a
    // conditional query string — `entities${kind ? "?kind=..." : ""}` — and
    // must be checked in its widened form, because a pattern without `(\?.*)?`
    // refuses the request only once a filter is applied. That is the version
    // that is hard to notice: the panel works until someone uses it.
    const path = raw
      .replace(/([^/])\$\{\}$/, "$1?x=1")
      .replace(/\$\{\}/g, FILL)
      .replace(/\?.+$/, "?x=1");
    if (!ALLOWED.some((r) => r.test(path)) && !GET_ONLY.some((r) => r.test(path))) {
      failures.push(`${file}\n    calls  ${raw}\n    which the proxy refuses`);
    }
  }
}

if (failures.length) {
  console.error(
    `\n${failures.length} call site(s) the proxy will answer with 403 ` +
      `"path not permitted":\n\n  ${failures.join("\n\n  ")}\n\n` +
      `Add the path to ALLOWED in app/api/proxy/[...path]/route.ts — and if the ` +
      `call carries a query string, the pattern needs (\\?.*)? or it is refused ` +
      `only when a filter is applied, which is the harder version to notice.\n`,
  );
  process.exit(1);
}
console.log("proxy allow-list covers every console call site");
