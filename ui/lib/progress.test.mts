/**
 * The first tests in `ui/`.
 *
 * There was no test runner here at all, and roughly four thousand lines of
 * console changed in a day. This starts with `assess` because it is the one
 * piece of the console that is a pure function over data, because its own
 * docstring claims it is kept that way "so the wording is testable", and
 * because the wording is the product: a person reads this panel to find out
 * whether their write worked.
 *
 * No dependency and no config — Node runs TypeScript directly. `npm test`.
 *
 * Each test below names a distinction the code goes out of its way to make.
 * If a test here fails, the panel has started lying about a state, which is
 * worse than the panel being absent.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { assess } from "./progress.ts";
import type { DomainEvent, Item } from "./types.ts";

/* Complete fixtures rather than casts. A partial object forced through `as`
 * compiles and then diverges from the real shape the moment a field is added,
 * which is how a test keeps passing against a type it no longer describes. */
function item(over: Partial<Item> = {}): Item {
  return {
    data_id: "data_1",
    state: "stored",
    mime_type: "text/plain",
    data_type: "document_text",
    storage_ref: null,
    checksum: null,
    size_bytes: 412,
    parse_status: null,
    parse_detail: null,
    content_text: "Procurement will not sign until the review closes.",
    extracted_text: null,
    ...over,
  };
}

function event(type: string, over: Partial<DomainEvent> = {}): DomainEvent {
  return {
    event_id: "evt_1",
    sequence: 1,
    event_type: type,
    data_id: "data_1",
    caused_by: null,
    status: "consumed",
    attempts: 1,
    last_error: null,
    payload: {},
    occurred_at: "2026-09-01T12:00:00Z",
    consumed_at: "2026-09-01T12:00:01Z",
    ...over,
  };
}

function requested(over: Partial<DomainEvent> = {}): DomainEvent {
  return event("enrichment.requested", {
    payload: { embed: true, summarize: true },
    ...over,
  });
}

test("a write nobody asked to interpret says so, and offers the fix", () => {
  // The defect this whole panel was built after: the item is stored, durable
  // and permanently unfindable, and the screen said nothing about why.
  const climb = assess(item(), [], false);

  assert.equal(climb.settled, true, "nothing further will happen on its own");
  assert.equal(climb.offerEnrich, true, "the one action that unsticks it");
  assert.match(climb.reason ?? "", /not embedded and therefore not findable/);
  // "Stored, and stopped there" rather than "Stored": the first says the climb
  // ended, the second reads like a step on the way to somewhere.
  assert.equal(climb.headline, "Stored, and stopped there");
});

test("giving up watching is not the work finishing", () => {
  // The distinction the file makes a point of. Conflating them renders "we
  // stopped looking" as "it finished", and hides the one control that helps.
  const climb = assess(item(), [requested()], true);

  assert.equal(climb.settled, false, "the work did not stop; this screen did");
  // Asserted on the distinction rather than the sentence. Pinning the exact
  // wording made a copy change look like a regression, which is the opposite of
  // what this test is for -- it exists to protect the *meaning*, that watching
  // ended and the work did not.
  assert.match(climb.reason ?? "", /stopped watching/i);
  assert.match(climb.reason ?? "", /has not stopped/i);
  assert.match(climb.reason ?? "", /reconciler/i);
});

test("a refusal is terminal and quotes the reason it was given", () => {
  const refusal = event("enrichment.refused", {
    event_id: "evt_2",
    payload: { reason: "clinical sensitivity policy" },
  });

  const climb = assess(item(), [refusal], false);

  assert.equal(climb.settled, true);
  assert.match(climb.reason ?? "", /clinical sensitivity policy/);
  assert.match(climb.reason ?? "", /stored and intact/,
    "a refusal must not read as data loss");
});

test("a dispatch the API gave up on reports how many times and why", () => {
  const dead = requested({
    status: "failed",
    attempts: 5,
    last_error: "gemini: circuit open",
  });
  const climb = assess(item(), [dead], false);

  assert.equal(climb.settled, true);
  assert.match(climb.reason ?? "", /failed 5 times/);
  assert.match(climb.reason ?? "", /circuit open/,
    "the upstream error is the actionable half");
});

test("bytes that could not be read stop the climb and explain the knock-on", () => {
  const climb = assess(
    item({ parse_status: "unsupported_encoding" }), [requested()], false);

  assert.equal(climb.settled, true);
  assert.match(climb.reason ?? "", /unsupported_encoding/);
  assert.match(climb.reason ?? "", /nothing to embed/,
    "why a parse failure matters is that everything downstream needs text");
});

test("a reference with no bytes yet waits on the fetch worker, and names it", () => {
  const climb = assess(item({ state: "awaiting_fetch" }), [requested()], false);

  assert.equal(climb.settled, true);
  assert.match(climb.reason ?? "", /fetch worker/);
});

test("a stopped step never describes itself in the present tense", () => {
  // Called out in a comment in the source: "chunking and embedding" under a
  // stop mark reads as work still in flight, which is the single thing this
  // panel exists to stop people guessing about.
  const climb = assess(item(), [], false);
  const stopped = climb.steps.filter((s) => s.state === "stopped");

  assert.ok(stopped.length > 0, "this fixture must produce a stopped step");
  for (const step of stopped) {
    assert.doesNotMatch(step.note, /ing\b(?!.*did not run)/,
      `"${step.label}" reads as in-flight while stopped: "${step.note}"`);
  }
});

test("a write that asked for embedding only says so rather than looking stuck", () => {
  const embedOnly = requested({ payload: { embed: true, summarize: false } });
  const climb = assess(item({ state: "searchable" }), [embedOnly], false);
  const enrich = climb.steps.find((s) => s.key === "enrich");

  assert.match(enrich?.note ?? "", /not asked for/,
    "a step nobody requested is not a step that failed");
});

test("reaching the top reports the top", () => {
  const climb = assess(item({ state: "enriched" }), [requested()], false);

  assert.equal(climb.percent, 100);
  assert.equal(climb.settled, true);
  assert.equal(climb.reason, null, "a finished climb has nothing to explain");
  assert.match(climb.headline, /Enriched/);
});

test("every state produces a headline and a percentage a bar can render", () => {
  // The panel's contract with the screen: it always terminates in a sentence.
  // A blank headline or a NaN width is the failure that looks like a hang.
  const cases: [string, ReturnType<typeof assess>][] = [
    ["writing", assess(null, [], false)],
    ["stored", assess(item(), [], false)],
    ["queued", assess(item(), [requested()], false)],
    ["searchable", assess(item({ state: "searchable" }), [requested()], false)],
    ["enriched", assess(item({ state: "enriched" }), [requested()], false)],
    ["gave up", assess(item(), [requested()], true)],
  ];

  for (const [name, climb] of cases) {
    assert.ok(climb.headline.length > 0, `${name}: no headline`);
    assert.ok(Number.isFinite(climb.percent), `${name}: percent is not a number`);
    assert.ok(climb.percent >= 0 && climb.percent <= 100, `${name}: ${climb.percent}%`);
    assert.ok(climb.steps.length > 0, `${name}: no steps`);
  }
});
