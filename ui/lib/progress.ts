/**
 * Reading the staircase.
 *
 * Lifted out of `Progress.tsx` so it can be tested. The function was always
 * written to be pure — its own docstring said so — but it sat in a `.tsx` that
 * imports React, so nothing could import it without a DOM, and "testable"
 * stayed an intention.
 *
 * Two screens share this. The console and the sandbox disagreeing about what
 * `searchable` means is how a support answer becomes wrong.
 */

import type { DomainEvent, Item } from "./types";

const REQUESTED = "enrichment.requested";
const REFUSED = "enrichment.refused";
/** `events.MAX_ATTEMPTS` — where the API itself stops retrying a dispatch. */
const MAX_ATTEMPTS = 5;

export type StepState = "done" | "running" | "waiting" | "stopped" | "skipped";

export type Step = {
  key: string;
  label: string;
  state: StepState;
  note: string;
};

export type Climb = {
  percent: number;
  steps: Step[];
  /** One line, in the reader's words, for what is happening now. */
  headline: string;
  /** Why it stopped short of the top. Null when it is still moving or done. */
  reason: string | null;
  /** Nothing further will happen on its own. */
  settled: boolean;
  /** The one action that unsticks it, when there is one. */
  offerEnrich: boolean;
};

function step(key: string, label: string, state: StepState, note: string): Step {
  return { key, label, state, note };
}

/**
 * Turn an item plus its events into something a person can read.
 *
 * Kept a pure function so the wording is testable and so the same reading is
 * used by the console and the sandbox — two screens disagreeing about what
 * `searchable` means is how a support answer becomes wrong.
 */
export function assess(
  item: Item | null,
  events: DomainEvent[],
  gaveUp: boolean,
): Climb {
  if (item === null) {
    return {
      percent: 4,
      steps: [step("commit", "committing", "running", "the write is synchronous")],
      headline: "Writing…",
      reason: null,
      settled: false,
      offerEnrich: false,
    };
  }

  const request = events.find((e) => e.event_type === REQUESTED) ?? null;
  const refusal = events.find((e) => e.event_type === REFUSED) ?? null;
  const needsParse = item.parse_status !== null;
  // `truncated` is a *success* with a warning: the handler read the bytes and
  // the text ran past the index ceiling, so everything up to it was extracted
  // and the rest is stored but not indexed. Treating it as a failure told
  // somebody with a long document that "the bytes could not be read" and that
  // "there is nothing to embed" -- of two million characters that were read,
  // and which the API does queue for embedding.
  const parsed = item.parse_status === "parsed" || item.parse_status === "truncated";
  const parseFailed = needsParse && !parsed && item.parse_status !== "pending";
  const parsePending = item.parse_status === "pending";
  const awaitingFetch = item.state === "awaiting_fetch";
  const searchable = item.state === "searchable" || item.state === "enriched";
  const enriched = item.state === "enriched";
  const dispatchDead =
    request !== null && request.status === "failed" && request.attempts >= MAX_ATTEMPTS;

  // Why it is not moving. Ordered by how early it stops the climb, so the
  // first true one is the one worth telling somebody about.
  let reason: string | null = null;
  let offerEnrich = false;
  // A reason the work itself is over, as against a reason this screen stopped
  // looking. Conflating them is how "we gave up watching" would render as
  // "it finished", and how the button offering to keep watching would never
  // appear on the one screen state that needs it.
  let terminal = false;
  if (parseFailed) {
    terminal = true;
    reason =
      `The bytes were stored but could not be read (${item.parse_status}). ` +
      "Nothing downstream runs on an item with no text — there is nothing to embed.";
  } else if (awaitingFetch) {
    terminal = true;
    reason =
      "The write carried a reference rather than the bytes, so the fetch worker " +
      "has to materialise them before anything else can run.";
  } else if (refusal) {
    terminal = true;
    reason =
      `Interpretation was refused: ${String(refusal.payload.reason ?? "no reason recorded")}. ` +
      "A sensitivity policy withheld the expensive tier — the record is stored and intact.";
  } else if (dispatchDead) {
    terminal = true;
    reason =
      `The interpretation request failed ${request!.attempts} times and the API stopped retrying` +
      (request!.last_error ? `: ${request!.last_error}` : ".");
  } else if (request === null) {
    terminal = true;
    reason =
      "Nothing asked for this to be interpreted, so it stops here — stored and durable, " +
      "but not embedded and therefore not findable by search. Enrichment is off by default " +
      "because it is the part that spends money.";
    offerEnrich = true;
  } else if (gaveUp && !enriched) {
    // Deliberately not phrased as a failure. Nothing has gone wrong here: this
    // screen has a budget for watching and the work has its own, longer one.
    // The previous wording led with "still queued" and read as a stall.
    reason =
      "This screen has stopped watching — the work has not stopped. Long documents are " +
      "thousands of chunks and take a while. Reopen this record to see where it got to, and " +
      "the reconciler re-enqueues anything genuinely dropped every ten minutes.";
  }

  const steps: Step[] = [
    step(
      "commit",
      "stored",
      "done",
      item.size_bytes ? `${item.size_bytes} bytes, durable` : "durable, and audited",
    ),
  ];
  if (needsParse || awaitingFetch) {
    steps.push(
      step(
        "parse",
        "read",
        parsed ? "done" : parseFailed ? "stopped" : "running",
        parsed
          ? "text extracted from the bytes"
          : parseFailed
            ? `parse ${item.parse_status}`
            : parsePending
              ? "a model is reading it"
              : "waiting on the bytes",
      ),
    );
  }
  const embedState: StepState = searchable
    ? "done"
    : terminal
      ? "stopped"
      : request !== null
        ? "running"
        : "waiting";
  steps.push(
    step("embed", "searchable", embedState, embedNote(embedState)));

  function embedNote(state: StepState): string {
    if (state === "done") return "chunked and embedded — retrieval can reach it";
    if (request === null) return "not requested";
    if (request.payload.embed === false) return "not asked for — this write chose the summary only";
    // A stopped step must not describe itself in the present tense. "chunking
    // and embedding" under a stop mark reads as work still in flight, which is
    // the one thing this panel exists to stop guessing about.
    return state === "stopped" ? "did not run" : "chunking and embedding";
  }
  const enrichState: StepState = enriched
    ? "done"
    : terminal
      ? "stopped"
      : searchable
        ? "running"
        : "waiting";
  steps.push(
    step("enrich", "enriched", enrichState, enrichNote(enrichState)));

  function enrichNote(state: StepState): string {
    if (state === "done") return "title, summary and keywords recorded";
    if (refusal) return "withheld by a sensitivity policy";
    if (request === null) return "not requested";
    if (request.payload.summarize === false) return "not asked for — this write chose embedding only";
    return state === "stopped" ? "did not run" : "a model is summarising it";
  }

  // The graph, as its own step.
  //
  // It was folded into the sentence above -- "title, summary, keywords and
  // entities recorded" -- which asserted entities on every successful
  // enrichment and reported no number. A record that produced eighteen
  // entities and one that produced none rendered identically, and the second
  // is the case somebody needs to know about: it is the difference between
  // "the graph is built" and "the graph is empty and nothing said so".
  //
  // Zero is shown as a finished step with a note explaining why, not as a
  // failure. Plenty of records legitimately name nothing, and a red mark on
  // every meeting reminder would train people to ignore the row.
  const entities = item.entity_count ?? null;
  const edges = item.edge_count ?? null;
  const lens = item.template ?? null;
  if (enriched && entities !== null) {
    steps.push(step("graph", "connected", entities > 0 ? "done" : "stopped", graphNote()));
  } else if (request !== null && request.payload.summarize !== false) {
    steps.push(step("graph", "connected", enrichState === "stopped" ? "stopped" : "waiting",
                    enrichState === "stopped" ? "did not run" : "entities and relationships"));
  }

  function graphNote(): string {
    if (entities === 0) {
      return lens
        ? `nothing to connect — no ${lens} relationships were found in this text`
        : "nothing to connect — the model named nothing in this text";
    }
    const e = `${entities} entit${entities === 1 ? "y" : "ies"}`;
    if (!edges) {
      // Entities without edges is the ordinary case, not a fault: a
      // relationship has to be *stated*, and most text names things without
      // asserting anything between them.
      return `${e}, no relationships stated between them`;
    }
    return `${e} and ${edges} relationship${edges === 1 ? "" : "s"}`
      + (lens ? `, read as ${lens}` : "");
  }

  const done = steps.filter((s) => s.state === "done").length;
  const percent = enriched ? 100 : Math.max(8, Math.round((done / steps.length) * 100));

  function describe(): string {
    if (enriched && entities === 0) {
      return "Enriched — but nothing was named, so it is not in the graph";
    }
    if (enriched) return "Enriched — it reached the top of the staircase";
    if (gaveUp && !terminal) {
      return searchable
        ? "Searchable — still summarising, longer than this screen waits"
        : "Still working, longer than this screen waits";
    }
    if (terminal) {
      return searchable ? "Searchable, and stopped there" : "Stored, and stopped there";
    }
    if (searchable) return "Searchable — summarising now";
    return request !== null ? "Embedding — not findable by search yet" : "Stored";
  }
  const headline = describe();

  return {
    percent,
    steps,
    headline,
    reason,
    settled: enriched || terminal,
    offerEnrich,
  };
}

/**
 * Reading a repository snapshot's climb.
 *
 * The sibling of `assess`, and here for the same reason: a snapshot sat at
 * `running` for minutes with one word to describe a clone, a full AST pass,
 * an OSV lookup, forty writes and four model calls. "Working" and "wedged"
 * looked identical, which is the failure the staircase exists to prevent.
 *
 * The stages come from the job, which narrates each one as it begins. A
 * snapshot from before the job narrated anything still reads correctly —
 * `running` with no stage means the work is somewhere in the middle and the
 * steps show as waiting rather than as wrong.
 */
export type RepoSnapshotLike = {
  status: "pending" | "running" | "complete" | "failed";
  reason?: string | null;
  stats?: {
    stage?: string;
    nodes?: number;
    edges?: number;
    files_selected?: number;
    dependencies?: number;
    osv_status?: string;
    osv_affected?: number;
    records_written?: number;
    no_code_graph?: string;
  } | null;
};

const REPO_STAGES: { key: string; label: string; waiting: string }[] = [
  { key: "cloning", label: "Clone the commit", waiting: "one commit, not the history" },
  { key: "graphing", label: "Parse it into a graph", waiting: "tree-sitter, no model" },
  { key: "dependencies", label: "Check dependencies", waiting: "manifests, then OSV" },
  { key: "writing", label: "Store what it read", waiting: "the digest and the chosen files" },
  { key: "deriving", label: "Write the four reports", waiting: "one model call each" },
];

export function readSnapshot(snapshot: RepoSnapshotLike): Climb {
  const stats = snapshot.stats ?? {};
  const done = snapshot.status === "complete";
  const failed = snapshot.status === "failed";
  const at = REPO_STAGES.findIndex((s) => s.key === stats.stage);

  const steps = REPO_STAGES.map((s, i) => {
    // Done reaches every step; failed stops where the stage says it stopped.
    if (done) return step(s.key, s.label, "done", noteFor(s.key, stats));
    if (at < 0) {
      return step(s.key, s.label, failed ? "stopped" : "waiting",
                  failed ? "did not get here" : s.waiting);
    }
    if (i < at) return step(s.key, s.label, "done", noteFor(s.key, stats));
    if (i === at) {
      return step(s.key, s.label, failed ? "stopped" : "running",
                  failed ? "stopped here" : "working…");
    }
    return step(s.key, s.label, failed ? "stopped" : "waiting",
                failed ? "did not get here" : s.waiting);
  });

  const percent = done ? 100
    : failed ? Math.max(6, ((at < 0 ? 0 : at) / REPO_STAGES.length) * 100)
    : at < 0 ? 6
    : Math.max(6, ((at + 0.5) / REPO_STAGES.length) * 100);

  const headline = done
    ? "Analysed"
    : failed
      ? "Stopped"
      : snapshot.status === "pending"
        ? "Queued — waiting for the job to pick it up"
        // `running` is set when the job is *started*, and Cloud Run queues an
        // execution behind any already in flight. So running-with-no-stage is
        // genuinely "not begun yet", and saying "Running" there would claim
        // work that has not started -- the exact confusion the stages fix.
        : at < 0
          ? "Starting — the job is queued behind any analysis already running"
          : REPO_STAGES[at].label;

  return {
    percent,
    steps,
    headline,
    reason: failed ? (snapshot.reason ?? "no reason was recorded, which is itself a bug") : null,
    // Pending and running both move on their own; the other two do not.
    settled: done || failed,
    offerEnrich: false,
  };
}

/** What a finished stage actually found, rather than that it finished. */
function noteFor(key: string, stats: NonNullable<RepoSnapshotLike["stats"]>): string {
  if (key === "graphing") {
    if (stats.no_code_graph) return "no code found to parse";
    return stats.nodes ? `${stats.nodes} symbols, ${stats.edges ?? 0} edges` : "parsed";
  }
  if (key === "dependencies") {
    if (stats.osv_status === "unavailable") return "OSV unreachable — advisories unchecked";
    if (stats.osv_status === "no_dependencies_found") return "no manifests found";
    return stats.dependencies !== undefined
      ? `${stats.dependencies} declared, ${stats.osv_affected ?? 0} with an advisory`
      : "checked";
  }
  if (key === "writing") {
    return stats.records_written
      ? `${stats.records_written} records, ${stats.files_selected ?? 0} files read`
      : "stored";
  }
  if (key === "deriving") return "design, quality, bugs, dependencies";
  return "done";
}
