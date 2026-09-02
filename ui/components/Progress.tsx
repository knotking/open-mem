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
// The reading of the staircase lives in `lib/progress.ts`, where a test can
// import it without a DOM. This file is the rendering of it.
import { assess, type Climb, type Step, type StepState } from "@/lib/progress";

export { assess };
export type { Climb, Step, StepState };

const CEILING_MS = 120_000;


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
