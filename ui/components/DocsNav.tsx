"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import type { Doc } from "@/lib/docs";

/**
 * The index, beside the document rather than behind it.
 *
 * Before this, a document had a breadcrumb and a next link and nothing else:
 * reaching any other page meant going back to `/docs` and starting again, which
 * is fine for a list of three and useless for twenty-four. The contents belong
 * on screen while you read.
 *
 * A client component only because of `usePathname` — knowing which entry you
 * are on is the difference between a list of links and a place you can tell you
 * are inside.
 */
export default function DocsNav({ groups }: {
  groups: { group: string; docs: Doc[] }[];
}) {
  const path = usePathname();
  const [open, setOpen] = useState(false);

  // Narrow screens get the contents as a panel rather than a column, and it
  // closes on navigation — a menu that stays open over the page you just asked
  // for is a menu you have to dismiss before you can read.
  useEffect(() => setOpen(false), [path]);

  const current = (doc: Doc) =>
    path === (doc.slug ? `/docs/${doc.slug}` : "/docs");

  return (
    <>
      <button
        className="docnav-toggle"
        aria-expanded={open}
        aria-controls="docnav"
        onClick={() => setOpen(!open)}
      >
        {open ? "Close" : "Contents"}
      </button>

      <nav id="docnav" className={`docnav${open ? " open" : ""}`}
           aria-label="Documentation">
        {groups.map(({ group, docs }) => (
          <div className="docnav-group" key={group}>
            <p className="docnav-heading">{group}</p>
            {docs.map((doc) => (
              <Link
                key={doc.slug}
                href={doc.slug ? `/docs/${doc.slug}` : "/docs"}
                className={`docnav-link${current(doc) ? " on" : ""}`}
                /* The state is "this is the page you are on", which is what
                   the attribute means, so it reaches a screen reader too. */
                aria-current={current(doc) ? "page" : undefined}
              >
                {doc.title}
              </Link>
            ))}
          </div>
        ))}
      </nav>
    </>
  );
}
