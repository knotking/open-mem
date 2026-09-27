import Link from "next/link";
import { notFound } from "next/navigation";

import DIAGRAMS from "@/lib/docs-diagrams";
import { BY_SLUG, DOCS, linkFor } from "@/lib/docs";
import { render } from "@/lib/markdown";

/** Every published document is a static route; there is nothing dynamic here. */
export function generateStaticParams() {
  return DOCS.filter((d) => d.slug).map((d) => ({ slug: d.slug.split("/") }));
}

export async function generateMetadata(
  { params }: { params: Promise<{ slug: string[] }> },
) {
  const doc = BY_SLUG.get((await params).slug.join("/"));
  return { title: doc ? `${doc.title} · open-mem` : "Not found · open-mem" };
}

export default async function DocPage({ params }: { params: Promise<{ slug: string[] }> }) {
  const doc = BY_SLUG.get((await params).slug.join("/"));
  if (!doc) notFound();

  // Links are resolved against the *published* set, so a reference to a
  // document this section does not carry renders as its own label rather than
  // as a link to nothing.
  const html = render(doc.body, (href) => linkFor(doc, href), DIAGRAMS);
  const index = DOCS.findIndex((d) => d.slug === doc.slug);
  const previous = index > 0 ? DOCS[index - 1] : null;
  const next = index < DOCS.length - 1 ? DOCS[index + 1] : null;

  return (
    <main className="doc-page">
      <div className="doc-shell">
        {/* The group, not a breadcrumb: the shell already says where you are
            and the index beside it shows the rest. */}
        <p className="eyebrow">{doc.group}</p>
        <article className="doc-body" dangerouslySetInnerHTML={{ __html: html }} />

        {/* Reading order, because these documents were written as a sequence
            and a reader who finishes one has an obvious next question. */}
        <nav className="doc-next">
          {previous ? (
            <Link href={`/docs/${previous.slug}`}>← {previous.title}</Link>
          ) : <span />}
          {next ? <Link href={`/docs/${next.slug}`}>{next.title} →</Link> : <span />}
        </nav>
        <p className="doc-source">
          Source: <code>docs/{doc.file}</code>
        </p>
      </div>
    </main>
  );
}
