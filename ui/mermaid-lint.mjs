// Parses every ```mermaid block in a markdown file with the real library.
//
// jsdom is not decoration: flowchart and stateDiagram sanitize labels through
// DOMPurify, which needs a window. Without it those three diagram types fail
// with "addHook is not a function" and look like syntax errors when they are
// not -- so the lint would pass exactly the diagrams it never checked.
import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><body></body>", { pretendToBeVisual: true });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.DOMPurify = (await import("dompurify")).default(dom.window);

const mermaid = (await import("mermaid")).default;
mermaid.initialize({ startOnLoad: false });

const md = readFileSync(process.argv[2], "utf8");
const blocks = [...md.matchAll(/^```mermaid\n([\s\S]*?)^```/gm)].map((m) => m[1]);
let bad = 0;
for (const [i, b] of blocks.entries()) {
  const head = b.trim().split("\n")[0].slice(0, 40);
  try {
    await mermaid.parse(b);
    console.log(`  ${i + 1}. ok   ${head}`);
  } catch (e) {
    bad++;
    console.log(`  ${i + 1}. FAIL ${head}`);
    console.log("       " + String(e.message ?? e).split("\n").slice(0, 3).join("\n       "));
  }
}
console.log(bad === 0 ? `\nall ${blocks.length} diagrams parse` : `\n${bad} of ${blocks.length} FAILED`);
process.exit(bad ? 1 : 0);
