/**
 * What state is this project in, and what does the console say about it.
 *
 * These are judgements, not rendering: whether a corpus is "behind", whether a
 * rail should speak at all, and — the one that matters most — whether a state
 * that is *correct* gets described as a fault.
 *
 * Enrichment is opt-in and resolves off, so a corpus sitting at `stored` is
 * usually exactly what was asked for. A rail that called that broken would be
 * wrong more often than right, and a rail that is wrong more often than right
 * is one people stop reading. Several tests below exist only to hold that line.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { ARC_STEPS, readArc, type Counts } from "./arc.ts";

const NOTHING: Counts = {
  memories: 0, total: 0, stored: 0, searchable: 0, enriched: 0,
  awaitingFetch: 0, expiring: 0,
};

const stage = (c: Counts, key: string) =>
  readArc(c).stages.find((s) => s.key === key)!;

test("an empty project is told to make a place, not to add data", () => {
  const arc = readArc(NOTHING);
  assert.equal(stage(NOTHING, "place").state, "empty");
  assert.equal(stage(NOTHING, "place").go?.section, "memory");
  // And nothing further claims to be fine. A stage that reports `ready`
  // because it has nothing to be unready about is how an empty project looks
  // finished.
  assert.equal(stage(NOTHING, "climb").state, "unknown");
  assert.equal(stage(NOTHING, "back").state, "unknown");
  assert.equal(arc.quiet, false);
});

test("a project with a memory and no records is told to add data", () => {
  const c = { ...NOTHING, memories: 2 };
  assert.equal(stage(c, "place").state, "ready");
  assert.equal(stage(c, "arrive").state, "empty");
  assert.equal(stage(c, "arrive").go?.section, "add");
});

test("records that are mostly stored are reported as behind", () => {
  const c: Counts = { ...NOTHING, memories: 3, total: 435, stored: 423,
                      searchable: 4, enriched: 8, expiring: 0 };
  const climb = stage(c, "climb");
  assert.equal(climb.state, "behind");
  assert.equal(climb.count, 12);
  assert.match(climb.note!, /423 records of 435/);
  assert.equal(climb.go?.section, "update");
});

test("being stored is never described as an error", () => {
  const c: Counts = { ...NOTHING, memories: 1, total: 100, stored: 100,
                      searchable: 0, enriched: 0, expiring: 0 };
  const note = stage(c, "climb").note!;
  // The default that explains it has to be in the sentence, or a correct
  // state reads as a fault — and it is correct for every corpus nobody asked
  // to enrich, which is most of them.
  assert.match(note, /opt-in/);
  assert.match(note, /expected unless it was asked for/);
  assert.doesNotMatch(note, /fail|error|broken|wrong/i);
});

test("nothing searchable is called out where a question would be asked", () => {
  const c: Counts = { ...NOTHING, memories: 1, total: 100, stored: 100,
                      searchable: 0, enriched: 0, expiring: 0 };
  const back = stage(c, "back");
  assert.equal(back.state, "behind");
  // Says what the reader would otherwise conclude on their own, wrongly.
  assert.match(back.note!, /empty corpus rather than an unenriched one/);
  assert.equal(back.go, null, "there is nothing to ask yet");
});

test("a few records mid-flight is not worth a warning", () => {
  const c: Counts = { ...NOTHING, memories: 1, total: 900, stored: 3,
                      searchable: 400, enriched: 497, expiring: 0 };
  assert.equal(stage(c, "climb").state, "ready");
  assert.equal(readArc(c).quiet, true);
});

test("a healthy project makes the rail go quiet", () => {
  const c: Counts = { ...NOTHING, memories: 3, total: 50, stored: 0,
                      searchable: 10, enriched: 40, expiring: 0 };
  const arc = readArc(c);
  assert.equal(arc.quiet, true);
  assert.equal(arc.headline, null);
  assert.deepEqual([...new Set(arc.stages.map((s) => s.state))], ["ready"]);
  // Quiet means no calls to action at all, not quieter ones.
  assert.deepEqual(arc.stages.map((s) => s.go).filter(Boolean).map((g) => g!.section),
                   ["ask"]);
});

test("a record waiting on a fetch is named, not folded into stored", () => {
  const c: Counts = { ...NOTHING, memories: 1, total: 10, stored: 10,
                      searchable: 0, enriched: 0, awaitingFetch: 4, expiring: 0 };
  // It is neither stored-and-idle nor behind: it is waiting on work already
  // queued, and it reads as the first unless it is said.
  assert.match(stage(c, "climb").note!, /4 records still waiting on a fetch/);
});

test("an unmeasured count is unknown, not ready", () => {
  const c: Counts = { ...NOTHING, memories: null, total: 3, searchable: 3,
                      expiring: null };
  // "Nothing yet", "nothing matched" and "not measured" are three situations.
  // Collapsing the third into "fine" is how a broken load reads as a healthy
  // project.
  assert.equal(stage(c, "place").state, "unknown");
  assert.equal(stage(c, "keep").state, "unknown");
  assert.equal(readArc(c).quiet, true, "unknown is not a thing to shout about");
});

test("a project with no records does not divide by zero", () => {
  const arc = readArc(NOTHING);
  for (const s of arc.stages) {
    assert.doesNotMatch(s.note ?? "", /NaN/);
  }
});

test("the headline is the first thing that needs saying", () => {
  const c: Counts = { ...NOTHING, memories: 0, total: 0, expiring: 0 };
  assert.equal(readArc(c).headline, stage(c, "place").note);
});


test("the rail and the help panel describe the same five steps", () => {
  // A panel describing the console from a second copy is the drift the nav
  // hints are already careful about, and it arrives the same way: someone
  // edits one of them.
  const arc = readArc(NOTHING);
  assert.equal(ARC_STEPS.length, arc.stages.length);
  assert.deepEqual(ARC_STEPS.map((s) => s.n), arc.stages.map((s) => s.n));
  assert.deepEqual(ARC_STEPS.map((s) => s.title), arc.stages.map((s) => s.title));
});

test("the climb step tells a reader the thing that costs them a day", () => {
  const climb = ARC_STEPS.find((s) => s.n === "03")!;
  // Both halves, or the sentence is half a truth: resting at stored is often
  // correct, and it is also the commonest reason a search comes back thin.
  assert.match(climb.body, /opt-in/);
  assert.match(climb.body, /thin/);
});
