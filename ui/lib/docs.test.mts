import assert from "node:assert/strict";
import { test } from "node:test";
import { BY_SLUG, DOCS, groups, linkFor, unresolved } from "./docs.ts";

const doc = (file: string) => {
  const found = DOCS.find((d) => d.file === file);
  assert.ok(found, `${file} is not published`);
  return found!;
};

test("every published document is reachable at a distinct route", () => {
  // Two files can reduce to one slug -- `use-cases.md` and
  // `use-cases/README.md` both wanted `use-cases` -- and the loser would
  // simply never render. The generator refuses that; this is the second lock.
  assert.equal(BY_SLUG.size, DOCS.length);
  for (const d of DOCS) assert.ok(d.slug, `${d.file} has no route`);
});

test("a relative link becomes a route", () => {
  const ingestion = doc("ingestion/README.md");
  assert.equal(linkFor(ingestion, "write-api.md"), "/docs/ingestion/write-api");
  // Up and out of the folder, which is how half these cross-references are
  // written.
  assert.equal(linkFor(ingestion, "../graph.md"), "/docs/graph");
});

test("a link to a document this section does not publish is dropped", () => {
  // Not rendered as a link to nothing: the docs are curated, most of `docs/`
  // is internal design record, and every reference to it would otherwise 404.
  assert.equal(linkFor(doc("ingestion/README.md"), "../competition/comparison-onyx.md"), null);
});

test("anchors and absolute URLs pass through untouched", () => {
  const d = doc("architecture.md");
  assert.equal(linkFor(d, "#the-write-path"), "#the-write-path");
  assert.equal(linkFor(d, "https://example.com/spec"), "https://example.com/spec");
  // An image or a path into the source tree is not a document link.
  assert.equal(linkFor(d, "../api/src/memdog/app.py"), null);
});

test("a fragment survives the rewrite", () => {
  assert.equal(
    linkFor(doc("use-cases.md"), "use-cases-catalog.md#coverage-at-a-glance"),
    "/docs/use-cases-catalog#coverage-at-a-glance",
  );
});

test("the index groups in reading order and loses nothing", () => {
  const grouped = groups();
  assert.deepEqual(grouped.map((g) => g.group),
    ["Start here", "How it works", "Getting data in", "Getting it back"]);
  assert.equal(grouped.reduce((n, g) => n + g.docs.length, 0), DOCS.length);
});

test("dropped links are countable, so the curation stays visible", () => {
  // Deliberately not asserted to be zero -- curation means some references
  // point outside the published set, and that is the design. What matters is
  // that the number can be seen rather than discovered by a reader.
  const broken = unresolved();
  assert.ok(Array.isArray(broken));
  for (const b of broken) {
    assert.ok(b.from && b.href);
    assert.equal(linkFor(doc(b.from), b.href), null);
  }
});
