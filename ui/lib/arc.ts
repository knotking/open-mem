/**
 * Where this project has got to, as five stages.
 *
 * The sidebar names what the system *has*. It never says what to do, or where
 * this corpus has reached — and nearly every confusion worth fixing in this
 * console has been the same one: a record was somewhere in the staircase and no
 * screen said where. Repository memories that were "not findable" were `stored`
 * and never `searchable`. A chat that answered thinly was reading six enriched
 * records out of thirty-six. A hundred papers arrived and sat.
 *
 * `retrieval.py` puts the case better than this comment can, in the docstring
 * of the endpoint these counts come from: *"Someone uploads five hundred
 * records, immediately asks a question, gets a thin answer and concludes the
 * product does not work. They are wrong for a reason this endpoint can show
 * them."* The endpoint existed. Nothing put it where the person was.
 *
 * **The hard part is not detecting the gap, it is not lying about it.**
 * Enrichment is opt-in and resolves off, so a corpus sitting at `stored` is
 * very often exactly what was asked for. A rail that called that a fault would
 * be wrong most of the time, and a rail that is wrong most of the time is one
 * people learn to ignore — which costs more than never having built it.
 *
 * So this reports and does not scold: it says what is true, says the default
 * that explains it, and offers somewhere to look.
 */

import type { Section } from "./types";

export type StageState =
  /** Nothing here yet, and the next thing to do is obvious. */
  | "empty"
  /** There is a gap. Not necessarily a fault — see the note. */
  | "behind"
  /** Fine. Says nothing. */
  | "ready"
  /**
   * Not measured. Deliberately distinct from `ready`: "nothing yet", "nothing
   * matched" and "not configured" are three different situations, and one blank
   * rendering for all three is the bug the console-ui standard names first.
   */
  | "unknown";

export type Stage = {
  n: string;
  key: string;
  title: string;
  /** What this stage is for, in the reader's words. */
  body: string;
  state: StageState;
  /** The number worth showing beside the title. Null when nothing is counted. */
  count: number | null;
  /** The unit for that count, singular. */
  unit: string;
  /** Why it is `empty` or `behind`. Null when there is nothing to say. */
  note: string | null;
  /** Where to go about it, and what the link says. */
  go: { section: Section; label: string } | null;
};

export type Counts = {
  memories: number | null;
  total: number;
  stored: number;
  searchable: number;
  enriched: number;
  awaitingFetch: number;
  expiring: number | null;
};

export type Arc = {
  stages: Stage[];
  /**
   * Nothing needs saying. The rail collapses to counts — no warnings, no call
   * to action. A rail still shouting on day two hundred has taught its reader
   * to stop looking at it.
   */
  quiet: boolean;
  /** The one line worth reading when it is not quiet. */
  headline: string | null;
};

/**
 * Below this share of records unsearchable, the gap is not worth a sentence.
 *
 * A threshold rather than "any at all", because a live corpus always has a few
 * records mid-flight, and a rail that lights up for three items out of nine
 * hundred is noise wearing the costume of vigilance.
 */
const BEHIND_SHARE = 0.2;

function plural(n: number, unit: string): string {
  return `${n.toLocaleString()} ${unit}${n === 1 ? "" : "s"}`;
}

/**
 * The arc as prose, with no project attached.
 *
 * The help panel and the rail read this same list. A panel describing the
 * console from its own second copy is the drift `Console.tsx` already warns
 * about for the nav hints, and it arrives the same way: someone edits one.
 */
export const ARC_STEPS = [
  {
    n: "01",
    title: "Make a place",
    body:
      "A memory is the container, and its type is the policy for everything "
      + "that lands in it: how long it lives, whether what arrives is enriched, "
      + "how a URL in it is read, whether it tracks change between versions.",
  },
  {
    n: "02",
    title: "Get something in",
    body:
      "Add data takes a paste, a file or a recording. Producers, Inbound and "
      + "Crawlers are the same write path without a person: an SDK, a webhook a "
      + "provider posts to, and a puller for anything that will not push.",
  },
  {
    n: "03",
    title: "Watch it climb",
    body:
      "A write commits immediately and is durable at once, but it is not "
      + "findable yet. Stored becomes searchable when it is embedded, and "
      + "enriched when a model has read it. Enrichment is opt-in, so a corpus "
      + "resting at stored is often exactly what was asked for — and is also "
      + "the commonest reason a search comes back thin.",
  },
  {
    n: "04",
    title: "Get it back",
    body:
      "Search returns evidence and says what it excluded and why. Chat returns "
      + "prose with a citation behind every claim. Browse walks the corpus by "
      + "container when you would rather look than ask.",
  },
  {
    n: "05",
    title: "Keep it",
    body:
      "What expires and when, what folds down into a summary that keeps its "
      + "sources, and what leaves for good with a certificate that outlives the "
      + "record. Audit says who read and wrote what along the way.",
  },
];

export function readArc(counts: Counts): Arc {
  const searchable = counts.searchable + counts.enriched;
  const unsearchable = Math.max(counts.total - searchable, 0);
  // Guarded, because a project with no records must not produce NaN% and print
  // it at somebody.
  const share = counts.total > 0 ? unsearchable / counts.total : 0;

  const place: Stage = {
    n: "01",
    key: "place",
    title: "Make a place",
    body:
      "A memory is the container, and its type is the policy for everything "
      + "that lands in it — how long it lives, whether it is enriched, how a "
      + "URL in it is read.",
    state: counts.memories === null ? "unknown"
      : counts.memories === 0 ? "empty" : "ready",
    count: counts.memories,
    unit: "memory",
    note: counts.memories === 0
      ? "Nothing has a home yet. Records written without one land in a default "
        + "container, which works and tells you nothing."
      : null,
    go: counts.memories === 0
      ? { section: "memory", label: "Create a memory" } : null,
  };

  const arrive: Stage = {
    n: "02",
    key: "arrive",
    title: "Get something in",
    body:
      "Paste, upload or record it; or let a producer, a webhook or a crawler "
      + "write it without a person.",
    state: counts.total === 0 ? "empty" : "ready",
    count: counts.total,
    unit: "record",
    note: counts.total === 0 ? "No records yet." : null,
    go: counts.total === 0 ? { section: "add", label: "Add data" } : null,
  };

  // The stage this whole rail exists for.
  const climb: Stage = {
    n: "03",
    key: "climb",
    title: "Watch it climb",
    body:
      "A write is durable immediately and findable later. Stored becomes "
      + "searchable once it is embedded, and enriched once a model has read it.",
    state: counts.total === 0 ? "unknown"
      : share >= BEHIND_SHARE ? "behind" : "ready",
    count: searchable,
    unit: "searchable record",
    note: counts.total === 0 ? null
      : share >= BEHIND_SHARE
        // Stated, not scolded. This is the correct state for a corpus nobody
        // asked to enrich, and saying otherwise would be wrong far more often
        // than it would be right.
        ? `${plural(unsearchable, "record")} of ${counts.total.toLocaleString()} `
          + "are stored but not searchable. Enrichment is opt-in, so this is "
          + "expected unless it was asked for — a memory can be enriched from "
          + "its own panel."
        : null,
    go: share >= BEHIND_SHARE
      ? { section: "update", label: "Browse what is stored" } : null,
  };

  const back: Stage = {
    n: "04",
    key: "back",
    title: "Get it back",
    body:
      "Search returns evidence and says what it excluded. Chat returns prose "
      + "with a citation behind every claim.",
    state: counts.total === 0 ? "unknown"
      : searchable === 0 ? "behind" : "ready",
    count: searchable,
    unit: "record to answer from",
    note: counts.total > 0 && searchable === 0
      ? "Nothing is searchable yet, so a question here returns nothing — which "
        + "reads as an empty corpus rather than an unenriched one."
      : null,
    go: searchable > 0 ? { section: "ask", label: "Ask it something" } : null,
  };

  const keep: Stage = {
    n: "05",
    key: "keep",
    title: "Keep it",
    body:
      "What expires, what folds down, and what leaves for good with a "
      + "certificate behind it.",
    state: counts.expiring === null ? "unknown"
      : counts.expiring > 0 ? "behind" : "ready",
    count: counts.expiring,
    unit: "record expiring",
    note: counts.expiring
      ? `${plural(counts.expiring, "record")} due to expire.`
      : null,
    go: counts.expiring ? { section: "memory", label: "See what expires" } : null,
  };

  const stages = [place, arrive, climb, back, keep];

  // A fetch still outstanding is a fourth situation again: it is not stored,
  // not behind, and not the reader's to fix. It reads as `stored` unless it is
  // named, so it is named.
  if (counts.awaitingFetch > 0) {
    climb.note = [climb.note, `${plural(counts.awaitingFetch, "record")} `
      + "still waiting on a fetch."].filter(Boolean).join(" ");
  }

  const loud = stages.filter((s) => s.state === "empty" || s.state === "behind");
  return {
    stages,
    quiet: loud.length === 0,
    headline: loud.length ? loud[0].note : null,
  };
}
