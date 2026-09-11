/**
 * Render every ```mermaid block in the published docs to SVG, once, and commit
 * the result.
 *
 * **Why this is not done at request time, or even at build time.** Mermaid
 * measures text to lay a diagram out, and measuring text needs a browser — in
 * jsdom every shape collapses because `getBBox` does not exist, which fails as
 * a stack trace rather than as a bad picture. The alternatives were shipping
 * mermaid to the browser (a fourth runtime dependency for a UI that has three,
 * rendering after paint) or bundling Chromium as a dependency of `npm ci`.
 *
 * So this is a *tool*, not a build step: run it when a diagram changes, commit
 * what it writes. `npm run verify` does not need a browser — `build-docs.mjs`
 * only checks that every diagram in the published docs has a rendering here,
 * and says to run this when one does not.
 *
 * **Keyed by the diagram's own source.** No hash file to keep in step and no
 * ids to allocate: if the mermaid changes at all the key changes, the lookup
 * misses, and the check fails loudly. A stale rendering cannot be served,
 * because a stale rendering is not reachable.
 *
 * Requires Google Chrome, which is the only thing here that is not in
 * `package.json`. It is used headlessly through `--dump-dom` rather than
 * through a driver library, because the whole interaction is: load a page,
 * print the DOM.
 */

import { createHash } from "node:crypto";
import { execFile } from "node:child_process";
import fs from "node:fs";
import { promisify } from "node:util";
import http from "node:http";
import path from "node:path";

import { diagramsIn, publishedDocs } from "./build-docs.mjs";

const UI = path.resolve(import.meta.dirname, "..");
const OUT = path.join(UI, "lib", "docs-diagrams.ts");
const PAGE = path.join(UI, ".diagrams-render.html");

const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find((p) => fs.existsSync(p));

/**
 * The palette of the slab these land on — near-black, in both themes.
 *
 * Hardcoded rather than read from `globals.css`, and that is the point of the
 * slab: a diagram baked at author time cannot respond to a theme toggle, so the
 * ground it sits on must not change underneath it. One set of colours, always
 * correct. `--d-*` in `globals.css` are the same values for the hand-drawn
 * diagrams; the two lists are meant to be read together.
 */
const THEME = {
  background: "transparent",
  fontFamily: 'ui-sans-serif, -apple-system, "Segoe UI", Roboto, sans-serif',
  fontSize: "12.5px",

  primaryColor: "#191b16",
  primaryBorderColor: "#6b7263",
  primaryTextColor: "#f1eee7",
  secondaryColor: "#1d2a24",
  secondaryBorderColor: "#7fb8a4",
  secondaryTextColor: "#f1eee7",
  tertiaryColor: "#15160f",
  tertiaryBorderColor: "#3c4038",
  tertiaryTextColor: "#cfcbc1",

  mainBkg: "#191b16",
  nodeBorder: "#6b7263",
  nodeTextColor: "#f1eee7",
  lineColor: "#9aa08e",
  textColor: "#cfcbc1",
  titleColor: "#f1eee7",
  clusterBkg: "#131510",
  clusterBorder: "#3c4038",
  edgeLabelBackground: "#0e0f0c",

  actorBkg: "#191b16",
  actorBorder: "#7fb8a4",
  actorTextColor: "#f1eee7",
  actorLineColor: "#4a4f44",
  signalColor: "#cfcbc1",
  signalTextColor: "#cfcbc1",
  labelBoxBkgColor: "#1d2a24",
  labelBoxBorderColor: "#7fb8a4",
  labelTextColor: "#f1eee7",
  loopTextColor: "#cfcbc1",
  noteBkgColor: "#23281d",
  noteBorderColor: "#6b7263",
  noteTextColor: "#f1eee7",
  sequenceNumberColor: "#0e0f0c",

  transitionColor: "#9aa08e",
  transitionLabelColor: "#cfcbc1",
  stateBkg: "#191b16",
  stateLabelColor: "#f1eee7",
  labelColor: "#f1eee7",
  altBackground: "#15160f",
  compositeBackground: "#131510",
  compositeBorder: "#3c4038",
  compositeTitleBackground: "#191b16",
  innerEndBackground: "#191b16",
  specialStateColor: "#f1eee7",
};

/** A stable element id, so re-running this produces a byte-identical file. */
export function idFor(source) {
  return "mmd-" + createHash("sha256").update(source).digest("hex").slice(0, 10);
}

function renderPage(sources) {
  return `<!doctype html><meta charset="utf-8"><body><div id="out"></div>
<script type="module">
import mermaid from "/node_modules/mermaid/dist/mermaid.esm.min.mjs";
mermaid.initialize({
  startOnLoad: false,
  securityLevel: "strict",
  theme: "base",
  themeVariables: ${JSON.stringify(THEME)},
  // Natural size, not shrink-to-fit. \`useMaxWidth\` scales a wide diagram down
  // to whatever column it lands in, and the first render of these put a
  // fifteen-node flowchart in a 620px column — every label technically present
  // and none of them readable. The slab scrolls sideways instead.
  flowchart: { useMaxWidth: false, htmlLabels: false, curve: "basis",
              nodeSpacing: 34, rankSpacing: 46, padding: 10 },
  sequence: { useMaxWidth: false, mirrorActors: false,
             boxMargin: 8, width: 140, height: 40 },
  state: { useMaxWidth: false },
});
const sources = ${JSON.stringify(sources)};
const out = document.getElementById("out");
const failed = [];
for (const [id, source] of sources) {
  // Every render is caught. \`--dump-dom\` prints the DOM at the load event, and
  // a module with top-level await does not fire load until it finishes — so one
  // diagram throwing does not lose one diagram, it hangs the whole command with
  // no output and nothing to read. Ask any of that and you get a stack trace at
  // the bottom of this page instead.
  try {
    const { svg } = await mermaid.render(id, source);
    const box = document.createElement("div");
    box.setAttribute("data-id", id);
    box.innerHTML = svg;
    out.appendChild(box);
  } catch (e) {
    failed.push(id + ": " + (e && e.message ? e.message : String(e)).split("\\n")[0]);
  }
}
const report = document.createElement("pre");
report.id = "failures";
report.textContent = failed.join("\\n");
document.body.appendChild(report);
</script></body>`;
}

function serve(root) {
  const types = {
    ".mjs": "text/javascript", ".js": "text/javascript",
    ".html": "text/html", ".json": "application/json", ".css": "text/css",
  };
  const server = http.createServer((req, res) => {
    const file = path.join(root, decodeURIComponent(req.url.split("?")[0]));
    if (!file.startsWith(root) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
      res.writeHead(404);
      return res.end("not found");
    }
    res.writeHead(200, { "content-type": types[path.extname(file)] ?? "application/octet-stream" });
    fs.createReadStream(file).pipe(res);
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => resolve({ server, port: server.address().port }));
  });
}

/**
 * Pull each rendered `<svg>` back out of the dumped DOM.
 *
 * String scanning rather than a parser, because the only structure that matters
 * is `<div data-id="..">` wrapping one `<svg>`, and adding a DOM library to read
 * back what a DOM library just wrote is a dependency for nothing.
 */
export function extract(dom) {
  const found = new Map();
  // `mmd-` scoped: mermaid's own output carries data-id attributes too.
  const opens = [...dom.matchAll(/<div data-id="(mmd-[^"]+)">/g)];
  for (const open of opens) {
    const from = dom.indexOf("<svg", open.index);
    const to = dom.indexOf("</svg>", from);
    if (from === -1 || to === -1) continue;
    found.set(open[1], dom.slice(from, to + "</svg>".length));
  }
  return found;
}

/** `execFile`, resolved rather than thrown on a non-zero exit — Chrome's stdout is the product. */
const run = (cmd, args, opts) =>
  promisify(execFile)(cmd, args, opts).catch((e) => ({ stdout: e.stdout ?? "", stderr: e.stderr ?? "" }));

async function main() {
  if (!CHROME) {
    console.error(
      "no Chrome found. Mermaid needs a browser to measure text; install Google "
      + "Chrome, or render on a machine that has it and commit lib/docs-diagrams.ts.");
    process.exit(1);
  }

  // Deduplicated by source: the same diagram in two documents is one rendering,
  // and the key is what makes that automatic.
  const sources = [];
  const seen = new Set();
  for (const doc of publishedDocs()) {
    for (const source of diagramsIn(doc.body)) {
      if (seen.has(source)) continue;
      seen.add(source);
      sources.push([idFor(source), source]);
    }
  }
  if (!sources.length) {
    console.log("no mermaid diagrams in the published docs");
    return;
  }

  fs.writeFileSync(PAGE, renderPage(sources));
  const { server, port } = await serve(UI);
  let dom = "";
  try {
    // `execFile`, not `spawnSync`. The server serving mermaid to this page lives
    // in *this* process, and a synchronous spawn blocks the event loop that
    // would answer it — so Chrome waits forever for a request that cannot be
    // served, which presents as a diagram that will not render.
    const chrome = await run(CHROME, [
      "--headless", "--disable-gpu",
      // Big, and it is virtual rather than wall-clock time: every mermaid render
      // burns some, and when it runs out Chrome prints the DOM wherever the loop
      // had got to — eleven diagrams in, one diagram out, with no error anywhere.
      // Nothing waits 600s for this; the whole run takes about three.
      "--virtual-time-budget=600000",
      // No --user-data-dir and no --no-sandbox. Both were tried and both hang
      // headless Chrome on macOS indefinitely, which reads as a broken diagram.
      "--dump-dom", `http://127.0.0.1:${port}/.diagrams-render.html`,
    ], { encoding: "utf8", maxBuffer: 64 * 1024 * 1024, timeout: 120_000 });
    dom = chrome.stdout ?? "";
    // A killed Chrome has printed nothing, and the useful thing to say is that
    // it never finished rather than that every diagram is missing.
    if (!dom.includes("<svg")) {
      console.error("Chrome printed no SVG. Check the source parses: "
                  + "npm run lint:mermaid -- ../docs/<file>.md");
      process.exit(1);
    }
  } finally {
    server.close();
    fs.rmSync(PAGE, { force: true });
  }

  const rendered = extract(dom);
  const failures = /<pre id="failures">([\s\S]*?)<\/pre>/.exec(dom)?.[1]?.trim();
  const missing = sources.filter(([id]) => !rendered.has(id));
  if (failures) console.error(failures.replace(/&quot;/g, '"').replace(/&amp;/g, "&"));
  if (missing.length) {
    // A diagram that failed to parse renders as nothing at all, and writing the
    // file anyway would commit a hole. `npm run lint:mermaid` names the block.
    console.error(`${missing.length} of ${sources.length} diagrams did not render.`);
    console.error("Check the source parses: npm run lint:mermaid -- ../docs/<file>.md");
    process.exit(1);
  }

  const bySource = {};
  for (const [id, source] of sources) bySource[source] = rendered.get(id);
  fs.writeFileSync(
    OUT,
    "// Generated by scripts/render-diagrams.mjs. Do not edit.\n"
    + "// Keyed by the mermaid source itself: change the diagram and the key\n"
    + "// changes, so a stale rendering can never be served. Run `npm run diagrams`.\n"
    + `export default ${JSON.stringify(bySource, null, 2)} as Record<string, string>;\n`,
  );
  console.log(`wrote lib/docs-diagrams.ts — ${sources.length} diagrams`);
}

if (import.meta.filename === process.argv[1]) await main();
