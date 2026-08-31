"use client";

/**
 * Watching one item climb the staircase, and saying so out loud.
 *
 * The screen used to poll `GET /data/{id}` sixty times and show nothing but the
 * finished row, so the two minutes in between were indistinguishable from a
 * hang — and an item that was never going to move looked exactly like one that
 * was about to. Both cases are common: **enrichment is opt-in**, so a write
 * that does not ask for it stops at `stored` permanently, which is correct
 * behaviour and reads as a broken screen.
 *
 * So this does two things the old loop did not. It renders the climb while it
 * happens, rung by rung. And it **always terminates with a sentence**: reached
 * the top, refused for this reason, failed here, or nobody asked. Silence is
 * the one outcome it will not produce.
 *
 * The state alone cannot say which of those it is — `searchable` is the same
 * row whether the summary is queued, refused, or never requested. That answer
 * is in the event log, which is why this reads `GET /events?data_id=…` beside
 * the item rather than inferring from the rung.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { call, type DomainEvent, type Item, type Version } from "@/lib/types";

const REQUESTED = "enrichment.requested";
const REFUSED = "enrichment.refused";
/** `events.MAX_ATTEMPTS` — where the API itself stops retrying a dispatch. */
const MAX_ATTEMPTS = 5;
/** How long to watch before saying so. Long enough for a model call, short
 *  enough that nobody sits in front of a spinner wondering. */
const CEILING_MS = 120_000;

type StepState = "done" | "running" | "waiting" | "stopped" | "skipped";

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
  const parsed = item.parse_status === "parsed";
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

/**
 * Poll one item until it settles, and never merely stop.
 *
 * The cadence widens — a second at first, four seconds later — because the
 * interesting part happens early and a screen left open should not keep an API
 * that scales to zero awake.
 */
export function useTracked(onTick?: () => Promise<void>) {
  const [item, setItem] = useState<Item | null>(null);
  const [versions, setVersions] = useState<Version[]>([]);
  const [events, setEvents] = useState<DomainEvent[]>([]);
  const [watching, setWatching] = useState(false);
  const [gaveUp, setGaveUp] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const tracking = useRef<string | null>(null);
  // Bumped on every new track() so an older loop, already sleeping, notices it
  // has been replaced instead of writing its item over the newer one.
  const generation = useRef(0);

  const poll = useCallback(
    async (dataId: string, mine: number, startedAt: number) => {
      while (generation.current === mine) {
        const current = await call<Item>(`api/v1/data/${dataId}`);
        const log = await call<{ events: DomainEvent[] }>(
          `api/v1/events?data_id=${dataId}&limit=50`,
        ).catch(() => ({ events: [] as DomainEvent[] }));
        const revisions = await call<{ versions: Version[] }>(
          `api/v1/data/${dataId}/versions`,
        ).catch(() => ({ versions: [] as Version[] }));
        if (generation.current !== mine) return;
        setItem(current);
        setEvents(log.events);
        setVersions(revisions.versions);
        await onTick?.();

        // Assessed without the give-up flag, or the timeout would present as a
        // settled outcome — "stopped watching" and "finished" are different
        // facts and this is the line where they get confused.
        const age = Date.now() - startedAt;
        const climb = assess(current, log.events, false);
        if (climb.settled) {
          setWatching(false);
          return;
        }
        if (age > CEILING_MS) {
          setGaveUp(true);
          setWatching(false);
          return;
        }
        const wait = age < 10_000 ? 1_000 : age < 40_000 ? 2_000 : 4_000;
        await new Promise((r) => setTimeout(r, wait));
      }
    },
    [onTick],
  );

  const track = useCallback(
    (dataId: string) => {
      generation.current += 1;
      const mine = generation.current;
      tracking.current = dataId;
      setItem(null);
      setEvents([]);
      setVersions([]);
      setGaveUp(false);
      setElapsed(0);
      setWatching(true);
      void poll(dataId, mine, Date.now()).catch(() => setWatching(false));
    },
    [poll],
  );

  /** Keep watching after this screen gave up, or after an enrichment request. */
  const resume = useCallback(() => {
    if (tracking.current === null) return;
    generation.current += 1;
    setGaveUp(false);
    setWatching(true);
    void poll(tracking.current, generation.current, Date.now()).catch(() =>
      setWatching(false),
    );
  }, [poll]);

  /** Ask for the interpretation the write did not. */
  const enrichNow = useCallback(async () => {
    if (tracking.current === null) return;
    await call(`api/v1/data/${tracking.current}/enrich`, { embed: true, summarize: true });
    resume();
  }, [resume]);

  useEffect(() => {
    if (!watching) return;
    const started = Date.now();
    const timer = setInterval(() => setElapsed(Math.round((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [watching]);

  // A screen that navigates away should not leave a loop polling behind it.
  useEffect(() => () => {
    generation.current += 1;
  }, []);

  const climb = assess(item, events, gaveUp);
  return { item, versions, events, climb, watching, elapsed, track, resume, enrichNow };
}

/**
 * The climb, rendered.
 *
 * A bar for the glance, a rung per step for the question the glance raises, and
 * a sentence whenever it stopped anywhere but the top.
 */
export function WriteProgress({
  climb,
  watching,
  elapsed,
  dataId,
  onEnrich,
  onResume,
}: {
  climb: Climb;
  watching: boolean;
  elapsed: number;
  dataId: string | null;
  onEnrich?: () => void | Promise<void>;
  onResume?: () => void;
}) {
  const [asking, setAsking] = useState(false);

  return (
    <div className="climb">
      <div className="climb-head">
        <strong>{climb.headline}</strong>
        <span className="empty far">
          {watching ? `watching · ${elapsed}s` : climb.settled ? "settled" : "stopped watching"}
        </span>
      </div>
      <div className="bar">
        <span
          className={`fill ${climb.percent === 100 ? "step3" : "step2"}`}
          style={{ width: `${climb.percent}%` }}
        />
      </div>
      <ol className="climb-steps">
        {climb.steps.map((s) => (
          <li className={`climb-step ${s.state}`} key={s.key}>
            <span className="climb-mark" aria-hidden="true" />
            <span className="climb-label">{s.label}</span>
            <span className="climb-note">{s.note}</span>
          </li>
        ))}
      </ol>
      {dataId && (
        <p className="empty" style={{ marginBottom: 0 }}>
          <code>{dataId}</code>
        </p>
      )}
      {climb.reason && <p className="why climb-why">{climb.reason}</p>}
      {(climb.offerEnrich || (!watching && !climb.settled)) && (
        <div className="row" style={{ marginTop: 8 }}>
          {climb.offerEnrich && onEnrich && (
            <button
              disabled={asking}
              title="Embeds and summarises this item now. It costs a model call per chunk plus one for the summary."
              onClick={async () => {
                setAsking(true);
                try {
                  await onEnrich();
                } finally {
                  setAsking(false);
                }
              }}
            >
              {asking ? "Asking…" : "Interpret it now"}
            </button>
          )}
          {!watching && !climb.settled && onResume && (
            <button className="linkish" onClick={onResume}>
              Keep watching
            </button>
          )}
        </div>
      )}
    </div>
  );
}
