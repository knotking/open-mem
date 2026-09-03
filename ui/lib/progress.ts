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
    reason =
      "Still queued after two minutes. This screen stopped watching; the work did not stop. " +
      "The reconciler sweeps every ten minutes and re-enqueues anything that was dropped.";
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
    if (state === "done") return "title, summary, keywords and entities recorded";
    if (refusal) return "withheld by a sensitivity policy";
    if (request === null) return "not requested";
    if (request.payload.summarize === false) return "not asked for — this write chose embedding only";
    return state === "stopped" ? "did not run" : "a model is summarising it";
  }

  const done = steps.filter((s) => s.state === "done").length;
  const percent = enriched ? 100 : Math.max(8, Math.round((done / steps.length) * 100));

  function describe(): string {
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
