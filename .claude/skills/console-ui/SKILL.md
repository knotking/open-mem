---
name: console-ui
description: The standard every screen in the mem-dog console has to meet, and the specific ways screens here have failed it before. Invoke before adding or changing any UI in ui/components, before wiring a new API surface into the console, and whenever a screen is described as confusing, ugly, thin or missing configuration.
---

# Building a screen in this console

A screen that renders without error is not a finished screen. This file exists
because one shipped that way — the alerts screen, August 2026 — and the feedback
was *"horrible … we had discussed a lot of configuration"*. Everything below is
that failure generalised.

## The test, before anything else

**Can the person do the whole job on this screen, and see whether it worked?**

Not *is the endpoint reachable*. Not *does the table render*. A screen that
exposes a third of a feature's configuration and none of its results is worse
than no screen, because it looks finished.

Three questions. If any answer is no, it is not ready:

1. **Everything configurable is configurable here.** Every field the API accepts
   that a person would reasonably set. If the API takes six and the form offers
   two, the other four are invisible defaults nobody can find.
2. **The thing it does is visible.** Not "it worked" — *what* it did. Counts,
   results, history, and what it cost.
3. **State that needs attention announces itself.** Not buried in a detail view
   nobody opens.

## The four ways this screen failed

Written concretely, because the general version of each is easy to nod at and
still repeat.

**One row where the model allows many.** The condition builder offered a single
field with a single operator, so *"any **person's** location changing"* — two
conditions — was unbuildable, though the schema had always supported it. If the
underlying shape is a list, a map, or an expression, **the UI is a builder, not
one input**. Add and remove rows; do not assume one is enough.

**A result reduced to a toast.** Backtest existed to answer *would this have
caught anything, and was it the right anything* — and the screen showed a
one-line confirmation. A count is not calibration: a selector that matches
everything looks identical to one that works until you read what it matched.
**If a call returns evidence, render the evidence.** If the API does not return
it yet, that is an API change, not a reason to show less.

**A payload dumped instead of an event described.**
`JSON.stringify(payload).slice(0, 120)` is not a rendering. Every domain object
gets a function that turns it into a sentence a person recognises — *"located_in
replaced — the previous value no longer holds"*, not `{"predicate":"located_...`.

**Nothing about whether it is working.** No lag, no run history, no delivery
state. For anything that runs on a schedule or in the background, **silence and
success look identical**, and the screen is the only place that difference can
be shown. Surface the lag, the last run, what it looked at, what it did, and
what it deferred.

## What every screen owes

- **An empty state that says what to do**, and distinguishes *nothing yet* from
  *nothing matched* from *not configured*. Three different situations; one
  blank table for all three is a bug.
- **Destructive and gated actions explain themselves.** A disabled button needs
  a `title` saying why. "Start" being greyed out with no reason is the screen
  refusing to talk.
- **Cost sits beside the control that causes it.** Model calls, row counts,
  spend — next to the switch, not in a bill next month.
- **A secret shown once says so, next to itself**, and offers no way to ask
  again.
- **Truncation is never silent.** If a batch, list or result was capped, say
  what was left — *"nothing else matched"* and *"we stopped looking"* are
  different sentences and must not share one rendering.
- **Order results the way they are read.** Newest first for a feed; oldest first
  for a sequence. Never insertion order because that is what the API returned.

## Fit the console, do not invent a dialect

`components/Console.tsx` has a grammar already. Read a neighbouring section —
`CrawlersSection` is the closest analogue for anything with configuration and
runs — and match it:

- `section.stack` → `h1` → `p.hint` → `div.card` per concern
- `call<T>()` from `@/lib/types` for every request; never bare `fetch`
- `useCallback` loader + `useEffect`, one `busy`, one `error`, one `note`
- `.row` for inline groups, `table.kv` for data, `.ok` / `.warn` / `.hint` for
  state
- Types in `lib/types.ts`, not inline
- **Vocabularies come from the API.** `GET /api/v1/alerts/surfaces` and
  `/graph/predicates` exist so a list is never hardcoded twice and cannot drift
  from what the server validates.

## The cascade does not report collisions

`globals.css` is one stylesheet shared by the console and the sign-in page, and
**two components using the same class name is not an error anything surfaces**.
The later rule simply wins, and the damage lands somewhere you were not looking.

That has happened once: the console's tab strip was given `.tabs`, which the
landing header's nav already owned. The nav lost its `gap` and `margin-right`
and gained a border, and it read as a design problem on a page nobody had
touched.

- **Grep for a class name before defining it.** `grep -n "^\.name" app/globals.css`
  is two seconds and is the whole check.
- **Prefer a name that says where it belongs** — `subtab`, not `tab`. Generic
  words are the ones already taken.
- **Suspect a collision when a page you did not edit changes.** That is the
  signature: the breakage is never in the component you were working on.

The same applies to token names. Adding `--bad` is safe because nothing else
defines it; redefining `--accent` would repaint every screen at once.

## Navigation is part of the feature

A screen nobody can find is not shipped. The sidebar groups were a
**single-open accordion** — one group's items rendered, the rest hidden — with
`Data` open by default. That hid two whole features: Compaction sits under
Organize and Alerts is its own group, so to anyone looking, neither existed.

Both were built, tested, deployed and verified in the served bundle. The report
was still *"I can't see it"*, twice, and both times the first suspicion was the
deploy.

- **After adding a section, open the console and find it the way a person
  would** — from the sidebar, not by remembering the state variable.
- **Prefer showing to hiding.** Collapsing is for a reader who asked for less,
  not a default that costs a feature its discoverability.
- **A new nav entry is part of the change**, and so is whichever container it
  lands in being visible.

## Write like a person, not like the schema

The console says *"tell me when this happens"*, not *"transition subscription
configuration"*. Name things as the reader recognises them:

| Not this | This |
|---|---|
| `trigger: tick` | ran because — the sweep |
| `deferred: 12` | 12 left for the next run |
| `matched_by: model` | judged in words |
| `status: dead` | undeliverable |
| `watermark behind: 400` | 400 transitions not yet seen |

A label that only makes sense if you have read the schema is a label that has
not been written yet.

## Before saying it is done

1. `npx tsc --noEmit` and `npm run build` — both clean.
2. **Re-read the feature's own plan or docs and list every configurable field.**
   Then find each one on the screen. This is the check that would have caught
   the alerts screen.
3. Walk the whole task as a person: create → verify → observe → fix. If any step
   needs a terminal, the screen is not finished.
4. **Deploying is not the same as shipping.** `ui/deploy.sh` needs
   `MEMDOG_PROJECT_ID`, `MEMDOG_PRODUCER_ID` and `API_URL`. It once built and
   pushed an image and then died before `gcloud run deploy`, so an exit code
   said success while the old revision kept serving. Confirm the served bundle
   contains the change — see `.claude/skills/deploy-gcp/`.

## When the answer is a real design

A dense configuration screen, a comparison, a dashboard — these are information
design, not form-filling. Summary before detail; encode state in shape as well
as number; make what needs attention readable at a glance. If it is worth
building at all, it is worth ten minutes deciding what the reader should see
first.
