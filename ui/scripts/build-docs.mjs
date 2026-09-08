/**
 * Bake the published docs into a file the image can carry.
 *
 * `ui/deploy.sh` builds with `ui/` as the Docker context, so `../docs` does not
 * exist inside the image and cannot be read at build time there. Rather than
 * widen the context to the whole repository — which uploads `api/`, `.git` and
 * everything else on every UI deploy — the content is generated here, committed,
 * and read as an ordinary import.
 *
 * The obvious hazard of a generated-and-committed file is that it goes stale.
 * `--check` compares what is on disk with what this would produce and fails if
 * they differ; `npm run verify` runs it, so an edited doc that was never
 * regenerated is a failed build rather than a page quietly serving last week.
 *
 * **Curated, not everything.** `docs/` is largely an internal design record —
 * competitive assessments, roadmaps, working analysis — written for maintainers.
 * Publishing it wholesale puts all of that on a page anyone can reach. The list
 * below is the user-facing subset, and adding to it is a deliberate act.
 */

import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";

const ROOT = path.resolve(import.meta.dirname, "..", "..");
const DOCS = path.join(ROOT, "docs");
// A `.ts` module rather than `.json`: a JSON import needs an import attribute
// under bare node, which would put the content out of reach of `node --test`
// and leave `lib/docs.ts` -- including the link resolution, the part that
// actually breaks -- untestable.
const OUT = path.join(ROOT, "ui", "lib", "docs-content.ts");

/** Published, in reading order, grouped as the section presents them. */
const PUBLISHED = [
  // `docs/README.md` is deliberately absent. It indexes the *whole* repository's
  // documentation, most of which is not published here, so it arrives as a
  // contents page whose entries mostly cannot be followed. The section builds
  // its own index from what it actually publishes.
  { group: "Start here", files: [
    ["use-cases.md", "The five families"],
    ["use-cases-catalog.md", "Twelve use cases"],
    ["use-cases/README.md", "Memory shape per use case", "use-cases/shape"],
  ]},
  { group: "How it works", files: [
    ["architecture.md", "Architecture"],
    ["design-principles.md", "Design principles"],
    ["memories.md", "Memories"],
    ["cases.md", "Cases"],
    ["graph.md", "The entity graph"],
    ["compaction.md", "Compaction"],
  ]},
  { group: "Getting data in", files: [
    ["ingestion/README.md", "Ingestion"],
    ["ingestion/write-api.md", "The write API"],
    ["ingestion/crawlers.md", "Crawlers"],
    ["ingestion/connectors.md", "Connectors"],
    ["ingestion/formats.md", "Formats"],
    ["ingestion/uploads.md", "Uploads"],
  ]},
  // Published deliberately. This group states, by name, where competitors are
  // ahead -- Onyx on permissions, self-hosting as table stakes rather than a
  // moat -- and each document carries the date it was researched, because the
  // claims are about other people's products as they documented them then.
  { group: "How it compares", files: [
    ["competition/README.md", "The landscape", "compare"],
    ["competition/comparison-onyx.md", "vs Onyx", "compare/onyx"],
    ["competition/comparison-glean.md", "vs Glean", "compare/glean"],
    ["competition/comparison-company-brain.md", "vs company brains", "compare/company-brain"],
  ]},
  { group: "Getting it back", files: [
    ["retrieval/README.md", "Retrieval"],
    ["alerts.md", "Alerts"],
    ["usage.md", "Usage"],
    ["api.md", "API reference"],
    ["settings.md", "Settings"],
  ]},
];

/** `ingestion/write-api.md` → `ingestion/write-api`; a README is its folder. */
export function routeFor(file) {
  const withoutExt = file.replace(/\.md$/i, "");
  if (withoutExt === "README") return "";
  return withoutExt.replace(/\/README$/i, "");
}

function build() {
  const docs = [];
  const missing = [];
  const taken = new Map();
  for (const { group, files } of PUBLISHED) {
    for (const [file, title, explicit] of files) {
      const full = path.join(DOCS, file);
      if (!fs.existsSync(full)) {
        missing.push(file);
        continue;
      }
      const slug = explicit ?? routeFor(file);
      // Two files can reduce to one route -- `use-cases.md` and
      // `use-cases/README.md` both want `use-cases` -- and the loser would
      // simply never be reachable. Refused here rather than discovered as a
      // page serving the wrong document.
      if (taken.has(slug)) {
        console.error(
          `${file} and ${taken.get(slug)} both publish at "${slug}"; `
          + "give one an explicit slug");
        process.exit(1);
      }
      taken.set(slug, file);
      docs.push({ file, slug, title, group, body: fs.readFileSync(full, "utf8") });
    }
  }
  if (missing.length) {
    // A published list naming a file that is not there is the one failure that
    // produces a docs section with a hole in it.
    console.error(`docs listed for publication and not found: ${missing.join(", ")}`);
    process.exit(1);
  }
  return { generated_from: "docs/", docs };
}

const next = "// Generated by scripts/build-docs.mjs from docs/. Do not edit.\n"
  + "// Run `npm run docs` after changing a published document.\n"
  + `export default ${JSON.stringify(build(), null, 2)} as {\n`
  + "  generated_from: string;\n"
  + "  docs: { file: string; slug: string; title: string; group: string; body: string }[];\n"
  + "};\n";

if (process.argv.includes("--check")) {
  const current = fs.existsSync(OUT) ? fs.readFileSync(OUT, "utf8") : "";
  const same = createHash("sha256").update(current).digest("hex")
            === createHash("sha256").update(next).digest("hex");
  if (!same) {
    console.error(
      "docs-content.ts is out of date with docs/. Run `npm run docs`.\n"
      + "It is committed because the Docker build context is `ui/` and cannot "
      + "read ../docs, so a stale file serves last week's documentation.");
    process.exit(1);
  }
  // Curation drops some cross-references, which is the design -- but a number
  // nobody prints is a number nobody notices growing.
  const { docs } = build();
  let dropped = 0;
  const slugs = new Set(docs.map((d) => d.file));
  for (const d of docs) {
    for (const m of d.body.matchAll(/\[[^\]]*\]\(([^)\s]+)\)/g)) {
      const href = m[1];
      if (href.startsWith("#") || /^(https?:|mailto:)/.test(href)) continue;
      if (!/\.md$/i.test(href)) continue;
      const base = d.file.split("/").slice(0, -1);
      const stack = [...base];
      for (const part of href.split("#")[0].split("/")) {
        if (part === "." || part === "") continue;
        if (part === "..") stack.pop();
        else stack.push(part);
      }
      if (!slugs.has(stack.join("/"))) dropped += 1;
    }
  }
  console.log(
    `docs-content.ts matches docs/ (${docs.length} published, `
    + `${dropped} cross-references outside the published set render as text)`);
} else {
  fs.writeFileSync(OUT, next);
  console.log(`wrote ${path.relative(ROOT, OUT)} — ${build().docs.length} documents`);
}
