import Link from "next/link";

import { groups } from "@/lib/docs";

export const metadata = {
  title: "Documentation · open-mem",
  description: "How the memory layer works, and how to get data into and out of it.",
};

/**
 * The index of what is published.
 *
 * Built from the published set rather than from `docs/README.md`, which indexes
 * the whole repository — most of it internal design record — and would arrive
 * here as a contents page whose entries mostly cannot be followed.
 */
export default function DocsIndex() {
  return (
    <main className="doc-page">
      <div className="doc-shell">
        <h1>Documentation</h1>
        <p className="lede">
          How the memory layer works, what it does to a record on the way in, and how to get
          it back. Written for the repository, published as it stands.
        </p>

        {groups().map(({ group, docs }) => (
          <section key={group} className="doc-group">
            <h2>{group}</h2>
            <div className="doc-list">
              {docs.map((doc) => (
                <Link key={doc.slug} href={`/docs/${doc.slug}`} className="doc-link">
                  <span className="doc-link-title">{doc.title}</span>
                  <span className="doc-link-file">{doc.file}</span>
                </Link>
              ))}
            </div>
          </section>
        ))}
      </div>
    </main>
  );
}
