import Link from "next/link";

import DocsNav from "@/components/DocsNav";
import ThemeToggle from "@/components/ThemeToggle";
import { groups } from "@/lib/docs";

/**
 * The shell every documentation page sits in.
 *
 * A bar that stays put and an index that stays beside you. Both were missing:
 * a document carried a breadcrumb and a next link, so moving between any two of
 * twenty-four meant returning to the index each time.
 *
 * The bar is sticky rather than fixed so it takes part in the page's own
 * scrolling and cannot end up overlapping content at small heights, and the
 * index is sticky under it with its own scroll — a contents list that scrolls
 * the page away as you read it is a contents list you have to hunt for.
 */
export default function DocsLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="docs">
      <header className="docs-bar">
        <Link className="wordmark" href="/">
          <span className="dot" aria-hidden="true" />
          open-mem
        </Link>
        <Link className="docs-bar-title" href="/docs">Documentation</Link>
        <div className="docs-bar-end">
          <ThemeToggle />
          <Link className="tab-cta" href="/">Back to the site</Link>
        </div>
      </header>

      <div className="docs-body">
        <aside className="docs-side">
          <DocsNav groups={groups()} />
        </aside>
        <div className="docs-main">{children}</div>
      </div>
    </div>
  );
}
