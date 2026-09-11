import assert from "node:assert/strict";
import { test } from "node:test";
import { render, slug } from "./markdown.ts";

const keep = (h: string) => h;

test("headings carry an anchor, so a #link resolves", () => {
  assert.equal(render("## The five families"),
    '<h2 id="the-five-families">The five families</h2>');
  assert.equal(slug("What `part_of` means"), "what-part_of-means");
});

test("tables render, because these docs are mostly tables", () => {
  const html = render(["| Relation | State |",
                       "|---|---|",
                       "| `part_of` | Load-bearing |"].join("\n"));
  assert.match(html, /<th>Relation<\/th><th>State<\/th>/);
  assert.match(html, /<td><code>part_of<\/code><\/td>/);
  // Wide tables scroll inside their own container; the page body must not.
  assert.match(html, /class="doc-scroll"/);
});

test("a line of pipes without a divider is a paragraph, not a table", () => {
  const html = render("this | that | the other");
  assert.match(html, /^<p>/);
  assert.doesNotMatch(html, /<table/);
});

test("code fences are not interpreted as anything else", () => {
  const html = render(["```bash", "# not a heading", "a | b", "```"].join("\n"));
  assert.match(html, /<pre class="lang-bash"><code># not a heading\na \| b<\/code><\/pre>/);
  assert.doesNotMatch(html, /<h1/);
  assert.doesNotMatch(html, /<table/);
});

test("underscores inside code stay underscores", () => {
  // `a_b_c` is a path. Reading its underscores as emphasis turns a command
  // into italics halfway through, which is the classic hand-rolled bug.
  const html = render("Set `max_media_bytes` and `on_expiry` first.");
  assert.match(html, /<code>max_media_bytes<\/code>/);
  assert.doesNotMatch(html, /<em>/);
});

test("relative links are rewritten, and an unmappable one is not a link", () => {
  const rewrite = (href: string) => (href === "use-cases.md" ? "/docs/use-cases" : null);
  const html = render("See [the families](use-cases.md) and [internals](private.md).", rewrite);
  assert.match(html, /<a href="\/docs\/use-cases">the families<\/a>/);
  // The label survives; the link does not. A docs page whose links 404 is
  // worse than one that does not link.
  assert.match(html, /and internals\./);
  assert.doesNotMatch(html, /private\.md/);
});

test("an external link opens away and cannot reach back", () => {
  const html = render("[spec](https://example.com/x)", keep);
  assert.match(html, /target="_blank"/);
  assert.match(html, /rel="noreferrer noopener"/);
});

test("html in the source is escaped", () => {
  // Trusted input today. The rule that survives someone pointing this at
  // something else is "escape, then re-emit only our own tags".
  const html = render("A <script>alert(1)</script> tag and 5 < 6.");
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /&lt;script&gt;/);
});

test("lists, including a wrapped continuation line", () => {
  const html = render(["- first item", "  continued here", "- second"].join("\n"));
  assert.match(html, /<li>first item continued here<\/li>/);
  assert.match(html, /<li>second<\/li>/);
  assert.match(render("1. one\n2. two"), /<ol><li>one<\/li><li>two<\/li><\/ol>/);
});

test("blockquotes keep their structure", () => {
  const html = render("> **Note.** A thing.\n> Another.");
  assert.match(html, /<blockquote><p><strong>Note\.<\/strong> A thing\. Another\.<\/p><\/blockquote>/);
});

test("bold and italic, without eating a bare asterisk", () => {
  assert.match(render("**loud** and *quiet*"),
    /<strong>loud<\/strong> and <em>quiet<\/em>/);
});

test("a mermaid block becomes its rendered diagram, keyed by its own source", () => {
  const source = "flowchart LR\n  A --> B";
  const md = ["```mermaid", source, "```"].join("\n");

  const html = render(md, (h) => h, { [source]: "<svg id='d'></svg>" });
  assert.match(html, /<figure class="slab slab-doc"><svg id='d'><\/svg><\/figure>/);
  assert.doesNotMatch(html, /<pre/);
});

test("a mermaid block with no rendering stays a code block rather than vanishing", () => {
  const md = ["```mermaid", "flowchart LR", "  A --> B", "```"].join("\n");

  // The whole point of keying on the source: an edited diagram misses the
  // lookup, and a miss has to degrade to what it was before -- readable text --
  // not to an empty figure.
  assert.match(render(md), /<pre class="lang-mermaid"><code>flowchart LR/);
  assert.match(render(md, (h) => h, { "flowchart LR\n  A --> C": "<svg/>" }),
    /<pre class="lang-mermaid">/);
});

test("a diagram lookup cannot be talked into emitting the document's own markup", () => {
  // The key comes from the document; the markup never does. A block whose
  // source is a script tag looks up a diagram that does not exist and is
  // escaped as text, exactly like any other unrendered block.
  const md = ["```mermaid", "<script>alert(1)</script>", "```"].join("\n");
  const html = render(md, (h) => h, {});
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /&lt;script&gt;/);
});
