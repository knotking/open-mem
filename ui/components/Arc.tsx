"use client";

/**
 * Where this project has got to, above every screen.
 *
 * The sidebar is a good index and a bad first screen: it names what the system
 * has and never says where this corpus has reached. This is the other half —
 * five stages, read from real counts, sitting above whatever section is open so
 * that a gap which opens on day two hundred is seen on day two hundred rather
 * than the next time somebody happens to visit Overview.
 *
 * **It goes quiet when nothing is wrong**, and that is a feature rather than
 * politeness. A rail still calling for attention on a healthy project has
 * taught its reader to skip it, and then it is worse than absent — it occupies
 * the space where a real warning would have gone.
 *
 * **It links; it never spends.** The obvious next move for an unenriched corpus
 * is a button that enriches it, and a button in permanent chrome that fires
 * several hundred model calls is a footgun wherever it is placed. Enrichment
 * stays on the memory panel, where the scope of what is about to be paid for is
 * visible next to the control.
 *
 * All judgement lives in `lib/arc.ts` and is tested there. This file decides
 * only how it looks.
 */

import { readArc, type Counts } from "@/lib/arc";
import type { Section } from "@/lib/types";

export default function Arc({
  counts,
  section,
  onGo,
}: {
  counts: Counts;
  section: Section;
  onGo: (to: Section) => void;
}) {
  const arc = readArc(counts);

  return (
    <div className={arc.quiet ? "arc quiet" : "arc"}>
      <div className="arcrow">
        {arc.stages.map((stage, i) => (
          <button
            key={stage.key}
            type="button"
            className={`arcstage ${stage.state}`}
            // The rail is navigation as well as status: a stage that names a
            // problem and cannot be followed makes the reader hunt the sidebar
            // for the screen it was talking about.
            onClick={() => onGo(stage.go?.section ?? destinationFor(stage.key))}
            aria-current={
              destinationFor(stage.key) === section ? "page" : undefined
            }
            title={stage.body}
          >
            <span className="arcn">{stage.n}</span>
            <span className="arctitle">{stage.title}</span>
            <span className="arccount">
              {/* Null is not zero. "Not measured" and "none" are different
                  facts and an em dash is how this console already says the
                  first. */}
              {stage.count === null ? "—" : stage.count.toLocaleString()}
            </span>
            {i < arc.stages.length - 1 && (
              <span className="arcsep" aria-hidden="true">›</span>
            )}
          </button>
        ))}
      </div>

      {/* One note, not five. A rail that lists every imperfection at once is a
          list nobody reads; the first thing worth doing is the thing to say. */}
      {!arc.quiet && arc.headline && (
        <p className="arcnote">
          {arc.headline}{" "}
          {firstAction(arc) && (
            <button
              type="button"
              className="linkish"
              onClick={() => onGo(firstAction(arc)!.section)}
            >
              {firstAction(arc)!.label} →
            </button>
          )}
        </p>
      )}
    </div>
  );
}

function firstAction(arc: ReturnType<typeof readArc>) {
  return arc.stages.find((s) => s.note && s.go)?.go ?? null;
}

/** Where a stage sends you when it has nothing to complain about. */
function destinationFor(key: string): Section {
  switch (key) {
    case "place": return "memory";
    case "arrive": return "add";
    case "climb": return "overview";
    case "back": return "ask";
    default: return "memory";
  }
}
