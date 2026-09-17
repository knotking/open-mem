"use client";

/**
 * The landing page.
 *
 * It says what the thing is before asking anyone to sign in, and every claim
 * on it is one the system can actually back. The numbers are counted from the
 * running build rather than written into copy, because a number a maintainer
 * typed is wrong within a month and wrong in the flattering direction.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import ThemeToggle from "./ThemeToggle";

type Capabilities = {
  generators: number;
  registration_mode: string;
  formats: number;
  data_types: number;
  prompts: number;
  webhook_providers: number;
  crawler_strategies: number;
  connectors: number;
  alert_surfaces: number;
  embed_model: string;
  media_interpretation: boolean;
  /** Configuration, not code — claimed only where switched on. */
  url_context?: boolean;
  repo_analysis?: boolean;
};

/**
 * The bar, and the page it navigates — five beats, not eight anchors.
 *
 * This was cut to two once and restored to the whole page, on the reasoning
 * that the problem was never the count but that the bar said nothing about
 * *where you were*. That was half right. Position-tracking fixed the "seven
 * equivalent choices" problem; it did not fix eight labels reading as a list of
 * parts rather than an argument with an order.
 *
 * So each entry now covers a run of sections and is named for the beat it is:
 * how it works, what it connects, what it tells you, what it accepts, why to
 * trust it. Fewer things to choose between, and the labels say what the page
 * argues rather than which feature lives where.
 *
 * **Groups must cover *contiguous* sections.** The bar is a position as well as
 * a menu, so a group spanning a gap would light up, go dark, and light again as
 * the reader scrolled through it — which reads as a bug in the page rather than
 * a choice about the menu.
 *
 * **This list is the only source of truth for the bar, and dead links cannot
 * render.** A group is dropped if none of the sections it covers exists, and it
 * anchors to the first that does — so renaming a section id removes or retargets
 * its anchor rather than leaving one that scrolls nowhere.
 */
function sentence(detail: unknown): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const said = detail
      .map((d) => (typeof d === "string" ? d : (d as { msg?: string })?.msg))
      .filter(Boolean);
    if (said.length) return said.join("; ");
  }
  return "Something went wrong. Try again in a moment.";
}

/** One published corpus. Everything corpus-specific lives here rather than in
 *  the component: the starter questions, the placeholder and the attribution
 *  were all written for one text, and a second corpus made every one of them
 *  wrong. The server carries them so a new demo is configuration. */
type DemoApp = {
  key: string; title: string; blurb: string;
  questions: string[]; note: string; records: number;
  // How many claims the corpus holds. Carried so the card knows whether it has
  // a graph *before* offering one: a tab that opens onto "nothing here" reads
  // as a broken feature rather than an absent one, and three of these corpora
  // have no graph on purpose.
  claims: number;
};

/** The gallery, and the allowance shared across all of it. */
type DemoInfo = {
  demos: DemoApp[]; remaining_today: number; daily_cap: number;
};

/**
 * The whole system in one picture: where records come from, the single path
 * they take in, the three indexes that path builds, and what those indexes are
 * for.
 *
 * Inline SVG rather than an image so it takes the page's own colours in both
 * themes, and so the text in it is text. Deliberately the only diagram on the
 * page -- a second one would be an explanation, and the explanations are in the
 * docs now.
 */
function Flow() {
  const STAGES = [
    { x: 92, label: "Sources" },
    { x: 315, label: "One path in" },
    { x: 559, label: "Memory" },
    { x: 801, label: "Reached by" },
    { x: 1054, label: "Built on it" },
  ];

  // Bare, like the other three: the heading, the claim and the scroll container
  // belong to whatever is presenting the diagram, so all four can be panes of
  // one strip instead of one of them carrying its own furniture.
  return (
        <svg viewBox="0 0 1180 400" className="flow-svg" role="img"
             aria-label="Pull and inbound sources feed one write path. That path builds a
                         retrieval index, a knowledge graph and a reverse index, which together
                         are the memory. The memory is reached through chat, MCP or the API,
                         and those surfaces carry a company brain, agent memory, alerts,
                         pattern search and workflows.">
          {/* The five stages, named. Not decoration: without them the picture
              reads as fifteen boxes rather than as a direction of travel. */}
          {STAGES.map((s) => (
            <text key={s.label} x={s.x} y="20" className="flow-stage"
                  textAnchor="middle">{s.label.toUpperCase()}</text>
          ))}

          {/* Two enclosures, because these are not five and three loose boxes.
              The three indexes *are* the memory -- one thing with three shapes
              -- and the three surfaces are one question, "how do you reach it". */}
          <g className="flow-zone">
            <rect x="452" y="44" width="214" height="300" rx="14" />
            <rect x="726" y="44" width="150" height="300" rx="14" />
          </g>

          <g className="flow-box">
            <rect x="8" y="60" width="168" height="86" rx="10" />
            <text x="26" y="90" className="flow-h">Pull</text>
            <text x="26" y="112" className="flow-s">crawlers · repos</text>
            <text x="26" y="130" className="flow-s">connectors · feeds</text>
          </g>
          <g className="flow-box">
            <rect x="8" y="210" width="168" height="86" rx="10" />
            {/* "Push", not "Inbound": the pair is what carries the meaning --
                we go and get it, or it is sent to us. `Inbound` is the console's
                word for the screen and the API's for the auth mode, and both
                stay as they are; this is the diagram, where the contrast with
                Pull is the whole point. */}
            <text x="26" y="240" className="flow-h">Push</text>
            <text x="26" y="262" className="flow-s">webhooks · uploads</text>
            <text x="26" y="280" className="flow-s">email · SDK</text>
          </g>

          <g className="flow-box flow-spine">
            <rect x="240" y="118" width="150" height="120" rx="10" />
            <text x="258" y="152" className="flow-h">Write</text>
            <text x="258" y="174" className="flow-s">one endpoint</text>
            <text x="258" y="192" className="flow-s">commits, then</text>
            <text x="258" y="210" className="flow-s">enriches</text>
          </g>

          <g className="flow-box">
            <rect x="466" y="76" width="186" height="72" rx="10" />
            <text x="482" y="104" className="flow-h">Retrieval index</text>
            <text x="482" y="126" className="flow-s">chunks · embeddings</text>
          </g>
          <g className="flow-box">
            <rect x="466" y="158" width="186" height="72" rx="10" />
            <text x="482" y="186" className="flow-h">Knowledge graph</text>
            <text x="482" y="208" className="flow-s">entities · edges</text>
          </g>
          <g className="flow-box">
            <rect x="466" y="240" width="186" height="72" rx="10" />
            <text x="482" y="268" className="flow-h">Reverse index</text>
            <text x="482" y="290" className="flow-s">lexical · facets</text>
          </g>

          <g className="flow-box flow-surface">
            <rect x="738" y="90" width="126" height="60" rx="10" />
            <text x="754" y="116" className="flow-h">Chat</text>
            <text x="754" y="136" className="flow-s">with citations</text>
          </g>
          <g className="flow-box flow-surface">
            <rect x="738" y="164" width="126" height="60" rx="10" />
            <text x="754" y="190" className="flow-h">MCP</text>
            <text x="754" y="210" className="flow-s">for agents</text>
          </g>
          <g className="flow-box flow-surface">
            <rect x="738" y="238" width="126" height="60" rx="10" />
            <text x="754" y="264" className="flow-h">API</text>
            <text x="754" y="284" className="flow-s">retrieve · ask</text>
          </g>

          <g className="flow-box flow-app">
            <rect x="936" y="34" width="236" height="58" rx="10" />
            <text x="954" y="60" className="flow-h">Company brain</text>
            <text x="954" y="80" className="flow-s">what the team knows</text>
          </g>
          <g className="flow-box flow-app">
            <rect x="936" y="102" width="236" height="58" rx="10" />
            <text x="954" y="128" className="flow-h">Agent memory</text>
            <text x="954" y="148" className="flow-s">context across sessions</text>
          </g>
          <g className="flow-box flow-app">
            <rect x="936" y="170" width="236" height="58" rx="10" />
            <text x="954" y="196" className="flow-h">Alerts</text>
            <text x="954" y="216" className="flow-s">tell me when</text>
          </g>
          <g className="flow-box flow-app">
            <rect x="936" y="238" width="236" height="58" rx="10" />
            <text x="954" y="264" className="flow-h">Pattern search</text>
            <text x="954" y="284" className="flow-s">standing queries</text>
          </g>
          <g className="flow-box flow-app">
            <rect x="936" y="306" width="236" height="58" rx="10" />
            <text x="954" y="332" className="flow-h">Workflows</text>
            <text x="954" y="352" className="flow-s">state machines · cases</text>
          </g>

          <g className="flow-line">
            <path d="M 176 103 C 208 103 208 150 240 150" />
            <path d="M 176 253 C 208 253 208 206 240 206" />
            <path d="M 390 160 C 424 160 432 112 466 112" />
            <path d="M 390 178 L 466 194" />
            <path d="M 390 196 C 424 196 432 276 466 276" />

            <path d="M 652 112 L 692 112" />
            <path d="M 652 194 L 692 194" />
            <path d="M 652 276 L 692 276" />
            <path d="M 692 112 L 692 276" className="flow-bus" />
            <path d="M 692 120 L 738 120" />
            <path d="M 692 194 L 738 194" />
            <path d="M 692 268 L 738 268" />

            <path d="M 864 120 L 900 120" />
            <path d="M 864 194 L 900 194" />
            <path d="M 864 268 L 900 268" />
            <path d="M 900 63 L 900 335" className="flow-bus" />
            <path d="M 900 63 L 936 63" />
            <path d="M 900 131 L 936 131" />
            <path d="M 900 199 L 936 199" />
            <path d="M 900 267 L 936 267" />
            <path d="M 900 335 L 936 335" />
          </g>
        </svg>
  );
}

/**
 * Three pictures of what the memory is *for*, under the one picture of how it
 * works.
 *
 * `Flow` answers "what is this system"; nobody arrives asking that. These
 * answer "what would I use it for", and each is drawn around the one mechanism
 * that makes its case rather than around a row of nouns:
 *
 *   - the brain's claim is the **citation going back**, not the answer;
 *   - the timeline's claim is that **two readings of one span disagree**, which
 *     is the whole of change detection and cannot be said in a list;
 *   - the agent's claim is that **session three reads what session one wrote**,
 *     which is exactly what re-feeding a transcript does not do.
 *
 * They reuse `.flow-*` so the four read as one family. A second visual dialect
 * on the same page would say these are a different kind of thing.
 */
/** The one arrowhead, for every diagram on the page.
 *
 * Defined once and hoisted rather than repeated per `<svg>`, because
 * `marker-end` in the stylesheet names a single id: four diagrams with four
 * ids meant three of them referred to a marker that was not theirs, and drew
 * their connectors with no head at all. A shared def cannot drift from the
 * rule that points at it, and duplicate ids in one document cannot happen.
 */
function ArrowDefs() {
  return (
    <svg width="0" height="0" aria-hidden="true" focusable="false"
         style={{ position: "absolute" }}>
      <defs>
        <marker id="flow-arrow" viewBox="0 0 10 10" refX="9" refY="5"
                markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          {/* A literal, where everything else here is a token. A marker inherits
              from where it is *defined*, and this one is defined in a hidden
              svg outside every slab — so `var(--d-rule)` resolves to nothing
              and the arrowheads disappear. Same value as `--d-rule`. */}
          <path d="M 0 0 L 10 5 L 0 10 z" fill="#9aa08e" />
        </marker>
      </defs>
    </svg>
  );
}

function BrainDiagram() {
  return (
    <svg viewBox="0 0 940 284" className="flow-svg use-svg" role="img"
         aria-label="Chat, documents and tickets all write into one memory. A question
                     against that memory returns an answer whose every sentence carries
                     the passage behind it, and that citation opens the original record.">

      {[["Conversations", "chat · email · meetings", 24],
        ["Documents", "drive · wiki · uploads", 94],
        ["Tickets & records", "issues · CRM · repos", 164]].map(([h, s, y]) => (
        <g className="flow-box" key={h as string}>
          <rect x="8" y={y as number} width="252" height="52" rx="10" />
          <text x="24" y={(y as number) + 24} className="flow-h">{h}</text>
          <text x="24" y={(y as number) + 42} className="flow-s">{s}</text>
        </g>
      ))}

      <g className="flow-box flow-spine">
        <rect x="330" y="82" width="204" height="76" rx="10" />
        <text x="350" y="112" className="flow-h">One memory</text>
        <text x="350" y="132" className="flow-s">scoped, permissioned</text>
        <text x="350" y="149" className="flow-s">and searchable</text>
      </g>

      <g className="flow-box flow-app">
        <rect x="580" y="52" width="352" height="112" rx="10" />
        <text x="600" y="80" className="flow-h">&ldquo;What did we promise on renewal?&rdquo;</text>
        <text x="600" y="106" className="flow-s">Pricing was held at 400/seat through</text>
        <text x="600" y="124" className="flow-s">March, then raised to 550.</text>
      </g>
      {/* The citation is the point of the picture, so it is a mark of its own
          rather than a word inside the answer box. */}
      <g className="use-cite">
        <rect x="600" y="134" width="126" height="20" rx="5" />
        <text x="610" y="148">contract-v2.pdf</text>
      </g>

      <g className="flow-line">
        <path d="M 260 50 C 300 50 300 108 330 112" />
        <path d="M 260 120 L 330 120" />
        <path d="M 260 190 C 300 190 300 136 330 132" />
        <path d="M 534 120 L 580 108" />
      </g>

      {/* Back to the record it came from. Dashed and beneath everything,
          because it is a different kind of claim from the flow above it: not
          "then this happens" but "and this can be checked". */}
      <g className="use-return">
        <path d="M 640 168 C 500 246 320 250 152 220" />
        <text x="300" y="270" className="flow-s">every sentence opens the record behind it</text>
      </g>
    </svg>
  );
}

function TimelineDiagram() {
  const WEEKS = [
    { x: 8, week: "Monday 1", status: "green", incidents: "0" },
    { x: 330, week: "Monday 2", status: "red", incidents: "3" },
    { x: 652, week: "Monday 3", status: "green", incidents: "0" },
  ];
  return (
    <svg viewBox="0 0 940 316" className="flow-svg use-svg" role="img"
         aria-label="The same status report arrives three weeks running. Each version is
                     compared with the one before it, giving two changes. Read as a span,
                     the composed answer reports both changes because both happened, while
                     the net answer reports nothing, because the two ends are identical.">

      <text x="8" y="16" className="flow-stage">THE SAME DOCUMENT, ARRIVING AGAIN</text>

      {WEEKS.map((w) => (
        <g key={w.week}>
          <g className="flow-box">
            <rect x={w.x} y="30" width="280" height="86" rx="10" />
            <text x={w.x + 18} y="58" className="flow-h">{w.week}</text>
            <text x={w.x + 18} y="80" className="flow-s">status: {w.status}</text>
            <text x={w.x + 18} y="98" className="flow-s">open incidents: {w.incidents}</text>
          </g>
          <g className={`use-dot use-${w.status}`}>
            <circle cx={w.x + 258} cy="52" r="7" />
          </g>
        </g>
      ))}

      {/* Each step compared with the one before it — the only comparison the
          timeline actually performs. Everything below is a reading of these. */}
      <g className="flow-line">
        <path d="M 288 73 L 322 73" />
        <path d="M 610 73 L 644 73" />
      </g>
      <g className="use-delta">
        <rect x="222" y="126" width="184" height="26" rx="6" />
        <text x="234" y="144">green → red · 0 → 3</text>
        <rect x="544" y="126" width="184" height="26" rx="6" />
        <text x="556" y="144">red → green · 3 → 0</text>
      </g>
      <g className="flow-line">
        <path d="M 314 116 L 314 126" />
        <path d="M 636 116 L 636 126" />
      </g>

      <text x="8" y="192" className="flow-stage">ASK ABOUT THE WHOLE SPAN</text>

      {/* The two readings, side by side, because the number is the argument.
          Saying "composed reports churn" in prose is a sentence somebody skims;
          4 next to 0 is a thing they stop at. */}
      <g className="flow-box use-read">
        <rect x="8" y="206" width="452" height="94" rx="10" />
        <text x="28" y="234" className="flow-h">Composed &mdash; what happened</text>
        <text x="28" y="258" className="flow-s">the stored steps, concatenated. Free.</text>
        <text x="28" y="284" className="use-count">4 changes</text>
      </g>
      <g className="flow-box use-read">
        <rect x="480" y="206" width="452" height="94" rx="10" />
        <text x="500" y="234" className="flow-h">Net &mdash; what is different</text>
        <text x="500" y="258" className="flow-s">the two ends compared directly.</text>
        <text x="500" y="284" className="use-count">nothing</text>
      </g>

      {/* No connectors down to the two readings. They crossed the stage label,
          and they implied each reading came from the delta above it when both
          are readings of the whole span. The heading carries the relation. */}
    </svg>
  );
}

function AgentDiagram() {
  const SESSIONS = [
    { x: 8, when: "Monday", what: "learns the deploy runbook" },
    { x: 330, when: "Wednesday", what: "hits the same error" },
    { x: 652, when: "Friday", what: "a different agent entirely" },
  ];
  return (
    <svg viewBox="0 0 940 280" className="flow-svg use-svg" role="img"
         aria-label="Three agent sessions on different days write to and read from one
                     memory. Friday's session, a different agent, reads what Monday's wrote
                     without any transcript being replayed into its context.">

      <text x="8" y="16" className="flow-stage">SEPARATE SESSIONS, DIFFERENT AGENTS</text>

      {SESSIONS.map((s) => (
        <g className="flow-box flow-surface" key={s.when}>
          <rect x={s.x} y="30" width="280" height="74" rx="10" />
          <text x={s.x + 18} y="58" className="flow-h">{s.when}</text>
          <text x={s.x + 18} y="80" className="flow-s">{s.what}</text>
        </g>
      ))}

      <g className="flow-box flow-spine">
        <rect x="180" y="176" width="580" height="76" rx="10" />
        <text x="204" y="206" className="flow-h">One memory, outliving all of them</text>
        <text x="204" y="228" className="flow-s">
          facts, decisions and what was tried — not the transcripts
        </text>
      </g>

      {/* Down is writing, up is reading. Drawn as two directions rather than
          one double-headed line, because "it remembers" and "it recalls" are
          the two halves and a single arrow shows neither. */}
      <g className="flow-line">
        <path d="M 148 104 C 148 150 240 140 260 176" />
        <path d="M 470 104 L 470 176" />
        <path d="M 700 104 C 700 142 680 150 660 176" />
      </g>
      {/* Reading, as against writing — and kept well clear of the write line
          beside it, because the two directions are the whole claim and a
          single smudge shows neither. */}
      <g className="flow-line use-read-line">
        <path d="M 744 176 C 764 150 860 142 880 104" />
      </g>

      <text x="196" y="272" className="flow-s">
        Friday reads what Monday wrote — no transcript replayed, nothing re-summarised
      </text>
    </svg>
  );
}



function ZoomDiagram() {
  return (
    <svg viewBox="0 0 940 322" className="flow-svg use-svg" role="img"
         aria-label="When a Zoom meeting ends, Zoom posts a signed webhook. The transcript
                     is verified on arrival and joins the earlier instances of the same
                     recurring meeting. You then ask the series a question and get the
                     answer with the passage and the date it was said, rather than
                     re-watching a recording — and because the meeting recurs, the series
                     is also a timeline you can ask what changed since last time.">
      <text x="8" y="16" className="flow-stage">A MEETING ENDS</text>

      <g className="flow-box">
        <rect x="8" y="34" width="228" height="74" rx="10" />
        <text x="26" y="62" className="flow-h">Zoom</text>
        <text x="26" y="84" className="flow-s">posts the moment the</text>
        <text x="26" y="100" className="flow-s">recording is ready</text>
      </g>

      <g className="flow-box flow-spine">
        <rect x="282" y="34" width="228" height="74" rx="10" />
        <text x="300" y="62" className="flow-h">Verified on arrival</text>
        <text x="300" y="84" className="flow-s">Zoom&rsquo;s own signature,</text>
        <text x="300" y="100" className="flow-s">and a replay is refused</text>
      </g>

      <g className="flow-box flow-app">
        <rect x="556" y="34" width="376" height="74" rx="10" />
        <text x="576" y="62" className="flow-h">This meeting&rsquo;s series</text>
        <text x="576" y="84" className="flow-s">the transcript joins every earlier</text>
        <text x="576" y="100" className="flow-s">instance of the same standing meeting</text>
      </g>

      <g className="flow-box flow-surface">
        <rect x="8" y="168" width="548" height="118" rx="10" />
        <text x="28" y="196" className="flow-h">&ldquo;What did we decide about the cutover?&rdquo;</text>
        <text x="28" y="222" className="flow-s">Moved to 3 October, because the replica lag</text>
        <text x="28" y="240" className="flow-s">risk was still open.</text>
      </g>
      {/* The citation is the difference between recall and a summary: it says
          which meeting, and when in it. */}
      <g className="use-cite">
        <rect x="28" y="252" width="212" height="20" rx="5" />
        <text x="38" y="266">12 Sept standup · 14:03</text>
      </g>

      <g className="flow-box use-read">
        <rect x="592" y="168" width="340" height="118" rx="10" />
        <text x="612" y="196" className="flow-h">It recurs, so it is a timeline</text>
        <text x="612" y="220" className="flow-s">what changed since last week</text>
        <text x="612" y="252" className="use-count">3 changes</text>
        <text x="612" y="276" className="flow-s">the date moved twice, the owner once</text>
      </g>

      <g className="flow-line">
        <path d="M 236 71 L 282 71" />
        <path d="M 510 71 L 556 71" />
        <path d="M 700 108 C 700 140 400 136 300 168" />
        <path d="M 780 108 L 780 168" />
      </g>

      {/* Below the boxes, not between them. At y=150 it sat exactly where the
          connector from the series down to the question crosses, and the line
          struck the sentence out. */}
      <text x="8" y="314" className="flow-s">
        nobody re-watches the recording — the answer carries the passage and the day it was said
      </text>
    </svg>
  );
}

function PropertyDiagram() {
  const VISITS = [
    { x: 8, when: "Move-in · January", a: "faucet: fine", b: "carpet: clean", ok: true },
    { x: 330, when: "Quarterly · July", a: "faucet: leaking", b: "carpet: clean", ok: false },
    { x: 652, when: "Move-out · December", a: "faucet: fine", b: "carpet: stained", ok: false },
  ];
  return (
    <svg viewBox="0 0 940 348" className="flow-svg use-svg" role="img"
         aria-label="One rental unit is inspected at move-in, quarterly and at move-out.
                     Each report is a checkpoint. Read as churn the tenancy has three
                     changes, including a faucet that broke and was repaired; read as net
                     against move-in it has one, the stained carpet — because the faucet
                     ends where it started. The first is the maintenance history and the
                     second is the deposit conversation.">
      <text x="8" y="16" className="flow-stage">UNIT 4B — THE SAME INSPECTION, AGAIN AND AGAIN</text>

      {VISITS.map((v) => (
        <g key={v.when}>
          <g className="flow-box">
            <rect x={v.x} y="30" width="280" height="90" rx="10" />
            <text x={v.x + 18} y="58" className="flow-h">{v.when}</text>
            <text x={v.x + 18} y="82" className="flow-s">{v.a}</text>
            <text x={v.x + 18} y="102" className="flow-s">{v.b}</text>
          </g>
          <g className={`use-dot ${v.ok ? "use-green" : "use-red"}`}>
            <circle cx={v.x + 258} cy="52" r="7" />
          </g>
        </g>
      ))}

      <g className="flow-line">
        <path d="M 288 74 L 322 74" />
        <path d="M 610 74 L 644 74" />
        <path d="M 314 120 L 314 132" />
        <path d="M 636 120 L 636 132" />
      </g>
      <g className="use-delta">
        <rect x="186" y="132" width="256" height="26" rx="6" />
        <text x="198" y="150">faucet fine → leaking</text>
        <rect x="506" y="132" width="270" height="26" rx="6" />
        <text x="518" y="150">faucet fixed · carpet stained</text>
      </g>

      <text x="8" y="196" className="flow-stage">TWO QUESTIONS, AND THEY HAVE DIFFERENT ANSWERS</text>

      <g className="flow-box use-read">
        <rect x="8" y="210" width="452" height="112" rx="10" />
        <text x="28" y="238" className="flow-h">What happened during the tenancy?</text>
        <text x="28" y="268" className="use-count">3 changes</text>
        <text x="28" y="296" className="flow-s">the maintenance history — including a</text>
        <text x="28" y="313" className="flow-s">faucet that broke and was repaired</text>
      </g>
      <g className="flow-box use-read">
        <rect x="480" y="210" width="452" height="112" rx="10" />
        <text x="500" y="238" className="flow-h">What is different since move-in?</text>
        <text x="500" y="268" className="use-count">1 change</text>
        <text x="500" y="296" className="flow-s">the deposit conversation — the faucet</text>
        <text x="500" y="313" className="flow-s">ends where it started; the carpet does not</text>
      </g>

      {/* Muted, not the warning colour. This is how the feature behaves, not
          something going wrong — and red on an ordinary note spends the one
          colour that should mean "look at this". */}
      <text x="8" y="342" className="flow-s">
        each change carries how much it matters, so a scuffed skirting board sorts below a cracked window
      </text>
    </svg>
  );
}

function RepoDiagram() {
  const REPORTS = [
    ["Design", "what it is, structurally", 34],
    ["Code quality", "what is hard to change", 92],
    ["Functional bugs", "what looks wrong", 150],
    ["Dependencies", "and what OSV says about them", 208],
  ];
  return (
    <svg viewBox="0 0 940 296" className="flow-svg use-svg" role="img"
         aria-label="A public repository at one commit is cloned and parsed into a code
                     graph, which is then read four ways: design, code quality, functional
                     bugs and dependencies. Each snapshot is pinned to its commit and is
                     compared to nothing.">
      <text x="8" y="16" className="flow-stage">A REPOSITORY AT ONE COMMIT</text>

      <g className="flow-box">
        <rect x="8" y="34" width="212" height="76" rx="10" />
        <text x="26" y="62" className="flow-h">github.com/owner/repo</text>
        <text x="26" y="84" className="flow-s">a branch is resolved to</text>
        <text x="26" y="100" className="flow-s">the commit it points at</text>
      </g>

      <g className="flow-box flow-spine">
        <rect x="264" y="34" width="196" height="76" rx="10" />
        <text x="282" y="62" className="flow-h">A code graph</text>
        <text x="282" y="84" className="flow-s">cloned, parsed whole,</text>
        <text x="282" y="100" className="flow-s">37 languages</text>
      </g>

      {REPORTS.map(([h, sub, y]) => (
        <g className="flow-box flow-app" key={h as string}>
          <rect x="560" y={y as number} width="372" height="50" rx="10" />
          <text x="580" y={(y as number) + 22} className="flow-h">{h}</text>
          <text x="580" y={(y as number) + 40} className="flow-s">{sub}</text>
        </g>
      ))}

      <g className="flow-line">
        <path d="M 220 72 L 264 72" />
        <path d="M 460 72 L 508 72" />
        <path d="M 508 59 L 508 233" className="flow-bus" />
        <path d="M 508 59 L 560 59" />
        <path d="M 508 117 L 560 117" />
        <path d="M 508 175 L 560 175" />
        <path d="M 508 233 L 560 233" />
      </g>

      {/* The thing the feature deliberately does not do. Said on the picture,
          because "four reports" invites the assumption that two of them can be
          read against each other. */}
      <text x="8" y="150" className="flow-s">Each snapshot stands</text>
      <text x="8" y="168" className="flow-s">alone: pinned to its</text>
      <text x="8" y="186" className="flow-s">commit, and compared</text>
      <text x="8" y="204" className="flow-s">to nothing.</text>
      <text x="8" y="240" className="use-refused-note">there is no trend here</text>
    </svg>
  );
}

function ScholarDiagram() {
  return (
    <svg viewBox="0 0 940 292" className="flow-svg use-svg" role="img"
         aria-label="A Google Scholar profile URL is resolved to one researcher. If the
                     match cannot be confirmed against papers the profile actually listed
                     it is refused. Confirmed, OpenAlex supplies the full works list and
                     the open-access PDFs, which are downloaded, read and indexed into a
                     memory of their own — answerable with citations.">
      <text x="8" y="16" className="flow-stage">ONE URL, ONE RESEARCHER, ONE CORPUS</text>

      <g className="flow-box">
        <rect x="8" y="34" width="206" height="68" rx="10" />
        <text x="26" y="62" className="flow-h">A profile URL</text>
        <text x="26" y="84" className="flow-s">scholar.google.com</text>
      </g>

      {/* The gate is the spine of this picture, not the papers. */}
      <g className="flow-box flow-spine">
        <rect x="258" y="34" width="216" height="68" rx="10" />
        <text x="276" y="62" className="flow-h">Which researcher?</text>
        <text x="276" y="84" className="flow-s">checked against their</text>
        <text x="276" y="98" className="flow-s">own listed titles</text>
      </g>

      <g className="flow-box">
        <rect x="518" y="34" width="196" height="68" rx="10" />
        <text x="536" y="62" className="flow-h">Their papers</text>
        <text x="536" y="84" className="flow-s">works list · open</text>
        <text x="536" y="98" className="flow-s">access PDFs</text>
      </g>

      <g className="flow-box flow-app">
        <rect x="758" y="34" width="174" height="68" rx="10" />
        <text x="776" y="62" className="flow-h">A memory</text>
        <text x="776" y="84" className="flow-s">keyed to the</text>
        <text x="776" y="98" className="flow-s">author</text>
      </g>

      {/* The refusal, drawn because it is the feature. A corpus about the wrong
          person is coherent, checkable and completely wrong, and nothing
          downstream can tell — so an unconfirmed match produces nothing. */}
      <g className="flow-box use-refused">
        <rect x="258" y="186" width="216" height="66" rx="10" />
        <text x="276" y="214" className="flow-h">Not confirmed?</text>
        <text x="276" y="236" className="flow-s">nothing is ingested</text>
      </g>

      <g className="flow-box flow-surface">
        <rect x="600" y="186" width="332" height="66" rx="10" />
        <text x="618" y="214" className="flow-h">Ask their work a question</text>
        <text x="618" y="236" className="flow-s">answered from the PDFs, with citations</text>
      </g>

      <g className="flow-line">
        <path d="M 214 68 L 258 68" />
        <path d="M 474 68 L 518 68" />
        <path d="M 714 68 L 758 68" />
        <path d="M 366 102 L 366 186" />
        <path d="M 845 102 C 845 150 800 150 780 186" />
      </g>

      {/* Only the branch is labelled. "Confirmed" on the main line had to sit
          far from the arrow it named — the gap between two boxes is narrower
          than the word — and left-to-right already says it. */}
      <text x="382" y="150" className="use-refused-note">refused</text>
    </svg>
  );
}

function SchoolDiagram() {
  // The marks a teacher writes down anyway. Deliberately one student and one
  // skill: a dashboard of a whole class is the picture every school product
  // already draws, and it is not the claim here.
  const LESSONS = [
    { x: 8, when: "September · diagnostic", a: "fractions 4/10",
      b: "wrote: “guesses the denominator”", ok: false },
    { x: 330, when: "October · exit tickets", a: "fractions 7/10",
      b: "wrote: “checks it now, slowly”", ok: true },
    { x: 652, when: "December · unit test", a: "fractions 9/10",
      b: "ratios 8/10 — it carried", ok: true },
  ];
  return (
    <svg viewBox="0 0 940 396" className="flow-svg use-svg" role="img"
         aria-label="A teacher's own marks and comments — a diagnostic, exit tickets, a
                     unit test — become checkpoints on one student's timeline. Read
                     forwards by the teacher, the same denominator error appearing three
                     lessons running is visible in the third lesson rather than in
                     December's report card. Read back to the student, the same record is
                     a trajectory rather than a verdict: four out of ten to nine out of
                     ten, with the sentence that says what changed and the piece of work
                     it came from.">
      <text x="8" y="16" className="flow-stage">
        ONE STUDENT, ONE SKILL — THE MARKS A TEACHER WRITES DOWN ANYWAY
      </text>

      {LESSONS.map((l) => (
        <g key={l.when}>
          <g className="flow-box">
            <rect x={l.x} y="30" width="280" height="90" rx="10" />
            <text x={l.x + 18} y="58" className="flow-h">{l.when}</text>
            <text x={l.x + 18} y="82" className="flow-s">{l.a}</text>
            <text x={l.x + 18} y="102" className="flow-s">{l.b}</text>
          </g>
          <g className={`use-dot ${l.ok ? "use-green" : "use-red"}`}>
            <circle cx={l.x + 258} cy="52" r="7" />
          </g>
        </g>
      ))}

      <g className="flow-line">
        <path d="M 288 74 L 322 74" />
        <path d="M 610 74 L 644 74" />
        <path d="M 314 120 L 314 132" />
        <path d="M 636 120 L 636 132" />
      </g>
      <g className="use-delta">
        <rect x="169" y="132" width="290" height="26" rx="6" />
        <text x="181" y="150">4/10 → 7/10 · the guess stopped</text>
        <rect x="491" y="132" width="290" height="26" rx="6" />
        <text x="503" y="150">7/10 → 9/10 · and it held in ratios</text>
      </g>

      <text x="8" y="196" className="flow-stage">THE SAME RECORD, READ TWO WAYS</text>

      <g className="flow-box use-read">
        <rect x="8" y="210" width="452" height="146" rx="10" />
        <text x="28" y="238" className="flow-h">The teacher: who needs me this week?</text>
        <text x="28" y="270" className="use-count">the same error, 3 times</text>
        <text x="28" y="296" className="flow-s">one misconception recurring across three</text>
        <text x="28" y="313" className="flow-s">lessons is a thing to say something about</text>
        <text x="28" y="330" className="flow-s">in the third lesson, not in December</text>
      </g>

      {/* The half of the picture a grade never shows the student: not where
          they landed but which way they are travelling, and the evidence is
          their own work rather than an encouraging adjective. */}
      <g className="flow-box use-read">
        <rect x="480" y="210" width="452" height="146" rx="10" />
        <text x="500" y="238" className="flow-h">The student: how far have I come?</text>
        <text x="500" y="270" className="use-count">4/10 → 9/10</text>
        <text x="500" y="296" className="flow-s">and the sentence that says why — you</text>
        <text x="500" y="313" className="flow-s">stopped guessing the denominator</text>
      </g>
      <g className="use-cite">
        <rect x="500" y="322" width="176" height="20" rx="5" />
        <text x="510" y="336">4 Oct exit ticket · q7</text>
      </g>

      <text x="8" y="388" className="flow-s">
        a grade is a verdict; a term of checkpoints is a direction — and every claim opens the work it came from
      </text>
    </svg>
  );
}

function PatientDiagram() {
  // Three records that did attach, in the order the care happened. The dot
  // colour carries the basis rather than a verdict on the record: green is a
  // system saying whose this is, red is an identifier we read out of a page
  // and have not had confirmed.
  const POINTS = [
    { x: 150, when: "2019", what: "discharge summary",
      how: "inferred — matched MRN-4417", ok: false,
      note: "scanned last Tuesday — still 2019" },
    { x: 470, when: "2024", what: "clinic note", how: "asserted by the EHR", ok: true },
    { x: 790, when: "2026", what: "lab result", how: "asserted by the lab", ok: true },
  ];
  return (
    <svg viewBox="0 0 940 430" className="flow-svg use-svg" role="img"
         aria-label="A clinic note, a lab result and a scanned discharge summary arrive from
                     three systems that never talk to each other. All three are joined to the
                     patient on the medical record number MRN-4417 and never on the name, so a
                     letter carrying only the name J. Smith attaches to nothing — two patients
                     share a name and merging them cannot be undone. What a system asserted
                     stays marked apart from what was merely matched out of the text. The
                     history is then ordered by when the care happened rather than when the
                     record arrived: a discharge summary scanned last Tuesday sits in 2019.">
      <text x="8" y="16" className="flow-stage">
        FOUR THINGS ARRIVE — AND ONLY THREE OF THEM ARE THIS PATIENT
      </text>

      {[["Clinic note", "the EHR said: MRN-4417", 30],
        ["Lab result", "the lab said: MRN-4417", 90],
        ["Discharge summary", "MRN-4417, read off the page", 150],
        ["Letter for J. Smith", "a name, and nothing else", 210]].map(([h, s, y]) => (
        <g className="flow-box" key={h as string}>
          <rect x="8" y={y as number} width="252" height="50" rx="10" />
          <text x="26" y={(y as number) + 22} className="flow-h">{h}</text>
          <text x="26" y={(y as number) + 40} className="flow-s">{s}</text>
        </g>
      ))}

      <g className="flow-box flow-spine">
        <rect x="320" y="40" width="244" height="92" rx="10" />
        <text x="338" y="68" className="flow-h">Joined on the number</text>
        <text x="338" y="92" className="flow-s">never on the name — two</text>
        <text x="338" y="110" className="flow-s">patients share one</text>
      </g>

      {/* The refusal is the feature. A merge of two patients is coherent,
          checkable and catastrophic, and nothing downstream can detect it --
          so a name on its own buys nothing. */}
      <g className="flow-box use-refused">
        <rect x="320" y="196" width="244" height="64" rx="10" />
        <text x="338" y="224" className="flow-h">No number?</text>
        <text x="338" y="246" className="flow-s">it attaches to nothing</text>
      </g>

      <g className="flow-box flow-app">
        <rect x="624" y="40" width="308" height="92" rx="10" />
        <text x="644" y="68" className="flow-h">MRN-4417 — one subject</text>
        <text x="644" y="92" className="flow-s">2 asserted — a system said so</text>
        <text x="644" y="110" className="flow-s">1 inferred — and what it matched</text>
      </g>

      <g className="flow-line">
        <path d="M 260 55 C 292 55 292 86 320 86" />
        <path d="M 260 115 C 292 115 292 86 320 86" />
        <path d="M 260 175 C 292 175 292 86 320 86" />
        <path d="M 260 235 C 292 235 292 228 320 228" />
        <path d="M 564 86 L 624 86" />
      </g>
      <text x="338" y="278" className="use-refused-note">refused, not guessed</text>

      <text x="8" y="316" className="flow-stage">
        ORDERED BY WHEN THE CARE HAPPENED — NOT BY WHEN WE HEARD ABOUT IT
      </text>

      <g className="flow-line">
        <path d="M 24 380 L 916 380" />
      </g>
      {POINTS.map((p) => (
        <g key={p.when}>
          <text x={p.x} y="344" className="flow-h" textAnchor="middle">
            {p.when} · {p.what}
          </text>
          <text x={p.x} y="364" className="flow-s" textAnchor="middle">{p.how}</text>
          <g className={`use-dot ${p.ok ? "use-green" : "use-red"}`}>
            <circle cx={p.x} cy="380" r="7" />
          </g>
          {p.note && (
            <text x={p.x} y="402" className="use-refused-note" textAnchor="middle">
              {p.note}
            </text>
          )}
        </g>
      ))}

      <text x="8" y="426" className="flow-s">
        the other ordering — by arrival — renders perfectly and puts 2019 at the top of the chart
      </text>
    </svg>
  );
}

function WatchDiagram() {
  // The two things that can make a rule fire, and the one that gets it
  // refused. Three left-hand boxes rather than a list, because the refusal is
  // not an error case here -- it is the design, and it belongs at the same
  // weight as the two that work.
  const CAUSES = [
    { h: "A record arrives", s: "matched as it lands, once", y: 30 },
    { h: "A date comes into reach", s: "30 days before the deadline", y: 90 },
    { h: "A rule narrowing nothing", s: "every write would match it", y: 150 },
  ];
  // The second beat: what stands between a rule and the thing it says.
  const GUARDS = [
    // Lines are kept to about thirty characters. The box is 296 wide with 20 of
    // padding, and `flow-s` is 12px mono -- so a line in the high thirties
    // reaches the border and reads as clipped rather than as a sentence.
    { x: 8, h: "Backtested, then enabled",
      lines: ["created switched off, like a",
              "crawler — and editing what it",
              "matches drops that approval"] },
    { x: 322, h: "Delivery is a read",
      lines: ["checked under the owner's",
              "rights at match time, never",
              "rights copied when it was made"] },
    { x: 636, h: "A feed, or a corpus",
      lines: ["poll it, or land it in a memory",
              "— so what it caught is itself",
              "something you can question"] },
  ];
  return (
    <svg viewBox="0 0 940 456" className="flow-svg use-svg" role="img"
         aria-label="A standing query is written once and then watches forward. A record
                     that arrives and matches is matched as it lands, exactly once, from a
                     watermark — never a re-scan of the corpus. A deadline is a second kind
                     of rule over the record's own date, because nothing arrives on the day
                     a deadline approaches. A rule that narrows nothing is refused outright,
                     because its feed would be a copy of the project. Everything else in the
                     system answers when asked; this is the one primitive that speaks first.
                     Before it can speak it must be backtested and enabled, and editing what
                     it matches drops that approval. Every match is checked under the
                     owner's own visibility at match time rather than rights copied when the
                     rule was written, and it is delivered either as a feed you poll or into
                     a memory, which makes what it caught a corpus you can question.">
      <text x="8" y="16" className="flow-stage">
        SAID ONCE — AND THEN IT WATCHES FORWARD
      </text>

      {CAUSES.map((c) => (
        <g className="flow-box" key={c.h}>
          <rect x="8" y={c.y} width="264" height="50" rx="10" />
          <text x="26" y={c.y + 22} className="flow-h">{c.h}</text>
          <text x="26" y={c.y + 40} className="flow-s">{c.s}</text>
        </g>
      ))}

      <g className="flow-box flow-spine">
        <rect x="320" y="40" width="244" height="92" rx="10" />
        <text x="338" y="68" className="flow-h">Forward from a watermark</text>
        <text x="338" y="92" className="flow-s">each record seen exactly once</text>
        <text x="338" y="110" className="flow-s">— never a re-scan</text>
      </g>

      {/* Refused, not degraded. A selector that narrows nothing produces a feed
          indistinguishable from the corpus it watches, which is not a standing
          query at all -- so it is turned away rather than accepted and
          regretted at the hundredth one. */}
      <g className="flow-box use-refused">
        <rect x="320" y="180" width="244" height="64" rx="10" />
        <text x="338" y="208" className="flow-h">Refused</text>
        <text x="338" y="230" className="flow-s">the feed would be the corpus</text>
      </g>

      <g className="flow-box flow-app">
        <rect x="624" y="40" width="308" height="92" rx="10" />
        <text x="644" y="68" className="flow-h">It speaks first</text>
        <text x="644" y="92" className="flow-s">everything else answers when asked</text>
        <text x="644" y="110" className="flow-s">this is the one that does not wait</text>
      </g>

      <g className="flow-line">
        <path d="M 272 55 C 300 55 300 86 320 86" />
        <path d="M 272 115 C 300 115 300 86 320 86" />
        <path d="M 272 175 C 300 175 300 212 320 212" />
        <path d="M 564 86 L 624 86" />
      </g>
      <text x="338" y="262" className="use-refused-note">narrow it, or nothing is registered</text>

      <text x="8" y="302" className="flow-stage">AND CHECKED BEFORE IT IS ALLOWED TO SPEAK</text>

      {GUARDS.map((g) => (
        <g className="flow-box use-read" key={g.h}>
          <rect x={g.x} y="318" width="296" height="112" rx="10" />
          <text x={g.x + 20} y="346" className="flow-h">{g.h}</text>
          {g.lines.map((line, i) => (
            <text key={line} x={g.x + 20} y={372 + i * 18} className="flow-s">{line}</text>
          ))}
        </g>
      ))}

      <text x="8" y="448" className="flow-s">
        a rule matching everything looks exactly like one that works — until you read what it caught
      </text>
    </svg>
  );
}

/**
 * The diagrams tab: how it works, then what it is for.
 *
 * That order is deliberate and it is the reverse of the old page. `Flow` alone
 * answered a question nobody arrives with — *what is this system* — and left
 * *what would I use it for* to prose further down, which is the half people
 * actually skim. Now the mechanism is the opening frame and three use cases
 * follow it, each drawn around the one thing that makes its case.
 */
function Diagrams() {
  // Sub-tabs rather than a column. Four diagrams stacked is a long scroll on a
  // desktop and a very long one on a phone, and the reader has no idea how much
  // is left — so the fourth may as well not exist. One at a time makes each a
  // screen, and the strip says up front what the other three are.
  const [pane, setPane] = useState("system");

  const PANES = [
    { key: "system", label: "The system",
      title: "From a source to something somebody uses",
      claim: "Pull and push land on the same write path. What it builds — three "
           + "indexes — is the memory, and three surfaces reach it.",
      art: <Flow /> },
    { key: "brain", label: "Company brain",
      title: "A company brain",
      claim: "Everything the team writes, in one place — and every sentence of an "
           + "answer opens the record it came from.",
      art: <BrainDiagram /> },
    { key: "cdc", label: "Timeline memory",
      title: "A timeline memory",
      claim: "The same report arrives every week. Ask what happened across a span and "
           + "you get the churn; ask what is different and you get the net. They "
           + "disagree on purpose, and the answer says which one it is.",
      art: <TimelineDiagram /> },
    { key: "agent", label: "Agent memory",
      title: "Agent memory",
      claim: "What one session learned, the next one can read — without replaying a "
           + "transcript into a context window.",
      art: <AgentDiagram /> },
    { key: "scholar", label: "A researcher",
      title: "One researcher, one corpus",
      claim: "Paste a Google Scholar profile and their open-access papers are fetched, "
           + "read and indexed into a memory of their own — answerable with citations. "
           + "Two researchers share a name, so a match that cannot be confirmed against "
           + "the titles the profile itself lists is refused rather than guessed.",
      art: <ScholarDiagram /> },
    { key: "repo", label: "A repository",
      title: "A repository, read four ways",
      claim: "A public repo at one commit is cloned, parsed whole into a code graph, and "
           + "then read for design, code quality, functional bugs and dependencies. Each "
           + "snapshot is pinned to its commit and compared to nothing — a report "
           + "attributed to a moving branch is one nobody could reproduce later.",
      art: <RepoDiagram /> },
    { key: "property", label: "Rental property",
      title: "A rental unit, inspected again and again",
      claim: "What asset-mem.com is built on. Every inspection of a unit is a checkpoint "
           + "on that unit's timeline, and each one is compared with the one before it. "
           + "Ask what happened during the tenancy and you get the maintenance history; "
           + "ask what is different since move-in and you get the deposit conversation. "
           + "They are different numbers because they are different questions, and each "
           + "change is qualified by how much it matters — so fair wear sorts below damage "
           + "instead of arriving in the same undifferentiated list.",
      art: <PropertyDiagram /> },
    { key: "zoom", label: "Zoom recall",
      title: "Recall from meetings, without re-watching them",
      claim: "Zoom posts a signed webhook when a recording is ready; the transcript is "
           + "verified on arrival and joins every earlier instance of the same standing "
           + "meeting. Ask the series a question and the answer carries the passage and "
           + "the day it was said. And because the meeting recurs, the series is a "
           + "timeline as well — so “what changed since last week” is a question you can "
           + "ask of it.",
      art: <ZoomDiagram /> },
    { key: "school", label: "A student’s term",
      title: "A term of a teacher’s own notes, read back to the student",
      claim: "A teacher records marks, exit tickets and comments anyway. Each lesson "
           + "becomes a checkpoint on one student’s timeline, so the same misconception "
           + "three lessons running is visible in the third lesson rather than in "
           + "December’s report card. Read the other way, that record is the thing a "
           + "grade never shows the student: not a verdict but a direction — 4 out of "
           + "10 to 9 out of 10, with the sentence that says what changed and the piece "
           + "of their own work it came from.",
      art: <SchoolDiagram /> },
    { key: "patient", label: "Patient timeline",
      title: "One patient, and every system that ever wrote about them",
      claim: "A clinic note, a lab result and a scanned discharge summary arrive from three "
           + "systems that do not talk to each other. They are joined to the patient on the "
           + "record number and never on the name — two patients share a name, and merging "
           + "them produces a history that is coherent, checkable and somebody else’s — so a "
           + "letter carrying only a name attaches to nothing rather than to a guess. What a "
           + "system asserted stays marked apart from what was matched out of the text, and "
           + "the history is ordered by when the care happened rather than when the record "
           + "arrived: a discharge summary scanned last Tuesday sits in 2019.",
      art: <PatientDiagram /> },
    { key: "watch", label: "It tells you",
      title: "The one thing here that speaks first",
      claim: "Every other picture on this page is somebody asking. A standing query is "
           + "written once and then watches forward: each record is matched as it lands, "
           + "from a watermark, so it is never a re-scan of the corpus. A deadline cannot "
           + "be a predicate over new writes — nothing arrives on the day one approaches — "
           + "so that is a second kind of rule, over the record’s own date, rather than the "
           + "first one stretched to cover it. A rule that narrows nothing is refused "
           + "outright, because its feed would be a copy of the project. And since a match "
           + "handed to somebody who cannot see the record is a leak through the "
           + "notification channel, visibility is checked under the owner’s rights at the "
           + "moment of the match rather than rights copied when the rule was written.",
      art: <WatchDiagram /> },
  ];
  const shown = PANES.find((x) => x.key === pane) ?? PANES[0];

  return (
    <>
      <ArrowDefs />
      {/* `landsub`, not `subtab` — the console owns that one, and a shared class
          name is the mistake this stylesheet has already made once. */}
      <nav className="landsubs" role="tablist" aria-label="Diagrams">
        {PANES.map((x) => (
          <button
            key={x.key}
            role="tab"
            id={`sub-${x.key}`}
            aria-controls="sub-panel"
            aria-selected={x.key === shown.key}
            className={`landsub${x.key === shown.key ? " on" : ""}`}
            onClick={() => setPane(x.key)}
          >
            {x.label}
          </button>
        ))}
      </nav>

      <section className="use" id="sub-panel" role="tabpanel"
               aria-labelledby={`sub-${shown.key}`}>
        <h2 className="section-title">{shown.title}</h2>
        <p className="use-claim">{shown.claim}</p>
        <div className="slab">{shown.art}</div>
      </section>
    </>
  );
}


/** One published corpus's graph, as the public surface returns it.
 *
 *  `predicates` is the vocabulary in use, glossed by the server. It is carried
 *  in the payload rather than written here because the alternative is a second
 *  copy of `predicates.py` living in a component, and the way that fails is
 *  quiet: a label that has stopped matching what the server means by the edge
 *  underneath it. */
type GraphNode = { id: string; name: string; type: string };
type GraphEdge = {
  subject: string; predicate: string; object: string;
  evidence: number; confidence: string;
};
type PredicateSpec = { predicate: string; gloss: string; confidence: string };
type DemoGraphData = {
  demo: string; title: string;
  nodes: GraphNode[]; edges: GraphEdge[]; predicates: PredicateSpec[];
  truncated: boolean; limit: number;
};

/** How many claims are drawn before the reader asks for more.
 *
 *  Not the whole 300 the server will send. A node-link drawing stops being
 *  readable long before it stops being correct, and the failure is total --
 *  past a certain density every graph is the same grey disc. So the picture
 *  opens at a size somebody can actually read and grows on request. */
const GRAPH_PAGE = 40;

/**
 * Fruchterman–Reingold, run to completion before anything is painted.
 *
 * **Deterministic, which is the requirement a physics animation fails.** The
 * usual force layout seeds at random and settles live, so the same corpus is a
 * different picture on every visit and two people cannot talk about the same
 * drawing. Positions here start on a golden-angle spiral and the whole
 * simulation is a pure function of the edges, so a given set of claims is
 * always laid out the same way.
 *
 * Synchronous, too: a few hundred nodes is a few hundred thousand pair
 * comparisons, which costs less than the frame budget it would take to animate
 * the same result, and it means there is no settling wobble to watch.
 */
function layout(count: number, links: { s: number; t: number }[]) {
  const x = new Float64Array(count);
  const y = new Float64Array(count);
  if (count === 0) return { x, y };

  for (let i = 0; i < count; i++) {
    // The golden angle spreads the seed evenly without repeating, so the
    // simulation starts from a disc rather than from a ring or a clump.
    const angle = i * 2.399963229728653;
    const radius = Math.sqrt((i + 0.5) / count) * 0.45;
    x[i] = 0.5 + Math.cos(angle) * radius;
    y[i] = 0.5 + Math.sin(angle) * radius;
  }

  const k = Math.sqrt(1 / count);
  const dx = new Float64Array(count);
  const dy = new Float64Array(count);
  // How many claims touch each node. Used to damp attraction below, which is
  // the difference between a readable graph and a knot.
  const degree = new Float64Array(count);
  for (const link of links) { degree[link.s] += 1; degree[link.t] += 1; }
  // Fewer sweeps on a big graph: the cost is quadratic in nodes and the extra
  // precision is invisible at the point where the labels have already collided.
  const sweeps = count > 90 ? 180 : 320;
  let temperature = 0.12;

  for (let step = 0; step < sweeps; step++) {
    dx.fill(0);
    dy.fill(0);

    for (let i = 0; i < count; i++) {
      for (let j = i + 1; j < count; j++) {
        let ex = x[i] - x[j];
        let ey = y[i] - y[j];
        let d2 = ex * ex + ey * ey;
        if (d2 < 1e-9) {
          // Two nodes exactly on top of each other have no direction to push
          // apart in. Nudged by index rather than at random, so the tie is
          // broken the same way every time -- the whole point of this being
          // deterministic.
          ex = ((i * 7919) % 13 - 6) * 1e-4;
          ey = ((j * 7907) % 13 - 6) * 1e-4;
          d2 = ex * ex + ey * ey + 1e-9;
        }
        const d = Math.sqrt(d2);
        const force = (k * k) / d;
        dx[i] += (ex / d) * force; dy[i] += (ey / d) * force;
        dx[j] -= (ex / d) * force; dy[j] -= (ey / d) * force;
      }
    }

    for (const link of links) {
      const ex = x[link.s] - x[link.t];
      const ey = y[link.s] - y[link.t];
      const d = Math.hypot(ex, ey) || 1e-6;
      const force = (d * d) / k;
      // **Divided by the node's own degree, which is the whole difference
      // between a graph and a knot.** Undamped, every claim touching Krishna
      // pulls Krishna with full force, so the busiest node is dragged into the
      // middle of its own neighbours and the cluster collapses onto itself --
      // which is exactly what the first version drew. Damping by degree lets a
      // hub sit at the centre of its neighbourhood and lets the neighbourhood
      // spread out around it, and it costs the leaves nothing because their
      // degree is one.
      const ds = 1 + degree[link.s] * 0.6;
      const dt = 1 + degree[link.t] * 0.6;
      dx[link.s] -= (ex / d) * force / ds; dy[link.s] -= (ey / d) * force / ds;
      dx[link.t] += (ex / d) * force / dt; dy[link.t] += (ey / d) * force / dt;
    }

    for (let i = 0; i < count; i++) {
      // A pull to the middle. Without it the components that share no edge --
      // and a corpus always has some -- drift apart forever, and the drawing
      // becomes one dense clump beside a few specks at the edges, with the
      // scaling below stretching the gap between them across the whole box.
      dx[i] += (0.5 - x[i]) * 0.22;
      dy[i] += (0.5 - y[i]) * 0.22;
      const d = Math.hypot(dx[i], dy[i]) || 1e-9;
      const capped = Math.min(d, temperature);
      x[i] += (dx[i] / d) * capped;
      y[i] += (dy[i] / d) * capped;
    }
    temperature *= 0.985;
  }
  return { x, y };
}

const VIEW_W = 900;
const VIEW_H = 520;
const PAD = 46;

/**
 * What a node *is*, as a shape.
 *
 * **Shape rather than colour, and the split is the API's own.** `predicates.py`
 * types every relation against `AGENT` (a person or an organization) and `IDEA`
 * (a topic, an event, or something uncategorised), because that is the
 * distinction the vocabulary actually turns on -- a doctrine does not work for
 * a city. Drawing that same split is what makes "who teaches what" legible at a
 * glance: the round things are the ones that can teach.
 *
 * Colour was the other option and is the wrong one here. Every diagram on this
 * site is near-monochrome on a fixed slab, one accent and nothing else, and a
 * seven-colour categorical ramp would read as a different product. Shape also
 * survives greyscale, forced-colors and the eight percent of men who would have
 * found the ramp ambiguous.
 */
function NodeShape({ node, cx, cy, r }: {
  node: GraphNode; cx: number; cy: number; r: number;
}) {
  if (node.type === "person" || node.type === "organization") {
    return <circle cx={cx} cy={cy} r={r} />;
  }
  if (node.type === "location") {
    return (
      <rect x={cx - r * 0.8} y={cy - r * 0.8} width={r * 1.6} height={r * 1.6}
            transform={`rotate(45 ${cx} ${cy})`} />
    );
  }
  return (
    <rect x={cx - r * 1.05} y={cy - r * 0.78} width={r * 2.1} height={r * 1.56}
          rx={3} />
  );
}

/**
 * The corpus as a picture, and then as sentences.
 *
 * The drawing is the half that answers *what shape is this*; the list under it
 * is the half that answers *what does it actually say*, and neither is
 * sufficient. A node-link diagram with no reading is a decoration people nod at
 * -- the claim "Krishna teaches detachment, in nine verses" is only readable as
 * a sentence, and the fact that it sits in a dense cluster is only readable as
 * a picture.
 *
 * Selecting a node joins them: the drawing dims everything it does not touch
 * and the list narrows to its claims, so the two halves are always showing the
 * same thing.
 */
function DemoGraph({ app }: { app: DemoApp }) {
  const [data, setData] = useState<DemoGraphData | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [predicate, setPredicate] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [shown, setShown] = useState(GRAPH_PAGE);

  useEffect(() => {
    let live = true;
    setBusy(true);
    setError(null);
    setData(null);
    setPredicate("");
    setSelected(null);
    setShown(GRAPH_PAGE);
    // The key, never a project id -- the same rule the question path follows.
    // The server resolves it against the published registry and a name it did
    // not publish reaches nothing at all.
    fetch(`/api/proxy/api/v1/public/graph?demo=${encodeURIComponent(app.key)}`)
      .then(async (response) => {
        const body = await response.json();
        if (!response.ok) throw new Error(sentence(body?.detail));
        return body as DemoGraphData;
      })
      .then((body) => { if (live) setData(body); })
      .catch((e) => { if (live) setError((e as Error).message); })
      .finally(() => { if (live) setBusy(false); });
    return () => { live = false; };
  }, [app.key]);

  const byId = useMemo(
    () => new Map((data?.nodes ?? []).map((n) => [n.id, n])),
    [data],
  );

  // Ordered by how hard the corpus insisted, so "show more" reaches down the
  // list rather than sideways into an arbitrary slice of it.
  const matching = useMemo(() => {
    const all = [...(data?.edges ?? [])].sort((a, b) => b.evidence - a.evidence);
    return predicate ? all.filter((e) => e.predicate === predicate) : all;
  }, [data, predicate]);

  const drawn = useMemo(() => matching.slice(0, shown), [matching, shown]);

  /** Positions, degrees, and the node set the drawn claims imply. */
  const picture = useMemo(() => {
    const ids: string[] = [];
    const index = new Map<string, number>();
    const degree = new Map<string, number>();
    for (const edge of drawn) {
      for (const id of [edge.subject, edge.object]) {
        if (!index.has(id)) { index.set(id, ids.length); ids.push(id); }
        degree.set(id, (degree.get(id) ?? 0) + 1);
      }
    }
    const links = drawn.map((e) => ({
      s: index.get(e.subject)!, t: index.get(e.object)!,
    }));
    const { x, y } = layout(ids.length, links);

    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (let i = 0; i < ids.length; i++) {
      minX = Math.min(minX, x[i]); maxX = Math.max(maxX, x[i]);
      minY = Math.min(minY, y[i]); maxY = Math.max(maxY, y[i]);
    }
    const spanX = maxX - minX || 1;
    const spanY = maxY - minY || 1;
    // One scale per axis, but the two are not allowed to diverge by more than a
    // little. Locked together, a graph that settles tall sits in a narrow strip
    // with half the frame empty; free, the axes stretch independently and every
    // angle in the drawing is a lie. A capped ratio fills the box without
    // bending anything a reader would notice.
    const fitX = (VIEW_W - PAD * 2) / spanX;
    const fitY = (VIEW_H - PAD * 2) / spanY;
    const tightest = Math.min(fitX, fitY);
    const scaleX = Math.min(fitX, tightest * 1.4);
    const scaleY = Math.min(fitY, tightest * 1.4);
    const busiest = Math.max(1, ...ids.map((id) => degree.get(id) ?? 1));

    const px = new Float64Array(ids.length);
    const py = new Float64Array(ids.length);
    const radius = new Float64Array(ids.length);
    for (let i = 0; i < ids.length; i++) {
      px[i] = VIEW_W / 2 + (x[i] - (minX + maxX) / 2) * scaleX;
      py[i] = VIEW_H / 2 + (y[i] - (minY + maxY) / 2) * scaleY;
      radius[i] = 5 + ((degree.get(ids[i]) ?? 1) / busiest) * 9;
    }

    // **Then prise apart whatever is still touching.** A force layout balances
    // forces; it does not guarantee that two nodes are further apart than the
    // circles drawn for them, and the first version of this put Krishna on top
    // of bhakti yoga. This is a separate, deterministic pass that knows the
    // radii the force stage cannot, and it converges in a handful of rounds
    // because it only ever moves nodes that overlap.
    for (let pass = 0; pass < 120; pass++) {
      let moved = false;
      for (let i = 0; i < ids.length; i++) {
        for (let j = i + 1; j < ids.length; j++) {
          // Room for the shapes, plus air for the label under each.
          const need = radius[i] + radius[j] + 16;
          let ex = px[j] - px[i];
          let ey = py[j] - py[i];
          let d = Math.hypot(ex, ey);
          if (d >= need) continue;
          if (d < 1e-6) { ex = (j % 2 ? 1 : -1) * 0.5; ey = 0.5; d = 0.707; }
          const push = (need - d) / 2;
          px[i] -= (ex / d) * push; py[i] -= (ey / d) * push;
          px[j] += (ex / d) * push; py[j] += (ey / d) * push;
          moved = true;
        }
      }
      if (!moved) break;
    }

    const points = ids.map((id, i) => ({
      id,
      // Clamped last, so prising apart cannot push a node off the canvas.
      cx: Math.min(VIEW_W - PAD, Math.max(PAD, px[i])),
      cy: Math.min(VIEW_H - PAD, Math.max(PAD, py[i])),
      degree: degree.get(id) ?? 1,
      r: radius[i],
      label: false,
      // Where the name goes. Decided by the placement pass below rather than
      // fixed, because "under it" is only the best answer when there is room.
      at: "below" as "below" | "above" | "left" | "right",
    }));

    // **Labels placed greedily, busiest first, and skipped where one would
    // collide.** The rule this replaces was "label anything above a degree
    // threshold", which is a guess about crowding rather than a check of it --
    // it drew "loss of memory" straight through "delusion" while leaving space
    // elsewhere empty. Here a name is drawn only if it actually fits, so the
    // drawing never carries text nobody can read, and the count underneath is
    // what says the graph holds more than it names.
    type Box = { x0: number; y0: number; x1: number; y1: number };
    const overlaps = (a: Box, b: Box) =>
      a.x0 < b.x1 && a.x1 > b.x0 && a.y0 < b.y1 && a.y1 > b.y0;
    // **Seeded with the shapes, not only with the labels already placed.** The
    // first version tested a candidate against other labels alone, so a name
    // could be drawn cleanly through the node standing next to it -- which is
    // how "detachment" rendered as "etachment".
    const shapes: Box[] = points.map((point) => ({
      x0: point.cx - point.r - 1, x1: point.cx + point.r + 1,
      y0: point.cy - point.r - 1, y1: point.cy + point.r + 1,
    }));
    const boxes: Box[] = [];
    for (const point of [...points].sort((a, b) => b.degree - a.degree)) {
      const node = byId.get(point.id);
      if (!node) continue;
      // Approximate, and deliberately so: measuring text needs a laid-out DOM,
      // and being a few pixels generous costs a label that would have fitted
      // while being wrong the other way costs one that overlaps.
      const half = Math.max(12, node.name.length * 3.2);
      // Four places to try rather than one. Below is the default because a name
      // under its shape is the easiest to associate; but a dense middle has
      // room above and to the sides, and refusing to look there was leaving
      // nearly half the graph unnamed on a frame with space to spare.
      const spots: [number, number, "below" | "above" | "left" | "right"][] = [
        [point.cx, point.cy + point.r + 9, "below"],
        [point.cx, point.cy - point.r - 5, "above"],
        [point.cx + point.r + 4 + half, point.cy + 4, "right"],
        [point.cx - point.r - 4 - half, point.cy + 4, "left"],
      ];
      for (const [lx, ly, at] of spots) {
        const box = { x0: lx - half, x1: lx + half, y0: ly - 9, y1: ly + 3 };
        if (box.x0 < 2 || box.x1 > VIEW_W - 2) continue;
        if (box.y0 < 2 || box.y1 > VIEW_H - 2) continue;
        if (boxes.some((b) => overlaps(box, b))) continue;
        if (shapes.some((b) => overlaps(box, b))) continue;
        boxes.push(box);
        point.label = true;
        point.at = at;
        break;
      }
    }

    return {
      points, at: new Map(points.map((p) => [p.id, p])),
      labelled: boxes.length,
    };
  }, [drawn, byId]);

  const heaviest = useMemo(
    () => Math.max(1, ...drawn.map((e) => e.evidence)),
    [drawn],
  );

  const glosses = useMemo(
    () => new Map((data?.predicates ?? []).map((p) => [p.predicate, p.gloss])),
    [data],
  );

  // The claims under the drawing follow the selection, so the two halves never
  // disagree about what is being looked at.
  const reading = selected
    ? drawn.filter((e) => e.subject === selected || e.object === selected)
    : drawn;

  if (busy) return <p className="hint">Reading the graph…</p>;
  if (error) return <p className="err">{error}</p>;
  if (!data) return null;

  // Three different situations, three different sentences. A corpus that was
  // never read by a model has no graph *by design* and should say so; a filter
  // that matched nothing is the reader's own doing and is undone by changing
  // it back.
  if (data.edges.length === 0) {
    return (
      <p className="hint">
        Nothing has been extracted from this corpus. A graph exists only where
        records were read by a model under a declared schema — this one was
        stored and indexed, which is a deliberate choice and not a failure.
      </p>
    );
  }

  const chosen = selected ? byId.get(selected) : null;

  return (
    <div className="dgraph">
      <p className="hint dgraph-lede">
        Every line is a claim a model read out of the text under the{" "}
        <code>scripture</code> schema, and the corpus was read one verse at a
        time — so a thick line is a claim many verses make separately.{" "}
        <strong>Select anything to follow it.</strong>
      </p>

      <div className="dgraph-controls">
        <label>
          Relationship
          <select value={predicate}
                  onChange={(e) => { setPredicate(e.target.value); setSelected(null); }}>
            <option value="">All {data.edges.length} claims</option>
            {/* The vocabulary comes from the response, so this list cannot
                drift from what the server actually returned. */}
            {data.predicates.map((p) => (
              <option key={p.predicate} value={p.predicate}>
                {p.predicate.replace(/_/g, " ")} — {p.gloss}
              </option>
            ))}
          </select>
        </label>
        {chosen && (
          <button className="secondary" onClick={() => setSelected(null)}
                  title={`Stop following ${chosen.name}`}>
            Following {chosen.name} — clear
          </button>
        )}
      </div>

      <div className="slab dgraph-slab">
        <svg viewBox={`0 0 ${VIEW_W} ${VIEW_H}`} className="dgraph-svg" role="img"
             aria-label={`${picture.points.length} things joined by ${drawn.length} claims extracted from ${data.title}.`}>
          <defs>
            {/* `markerUnits="userSpaceOnUse"`, because the default is
                `strokeWidth` -- which scales the arrowhead with the line, so a
                claim drawn thick precisely because many verses make it arrived
                with a head three times the size of the node it pointed at. The
                direction is the information; its size is not. */}
            <marker id="dgraph-tip" viewBox="0 0 10 10" refX="8" refY="5"
                    markerWidth="8" markerHeight="8" markerUnits="userSpaceOnUse"
                    orient="auto-start-reverse">
              <path d="M0 0 L10 5 L0 10 z" fill="var(--d-rule)" />
            </marker>
            <marker id="dgraph-tip-on" viewBox="0 0 10 10" refX="8" refY="5"
                    markerWidth="8" markerHeight="8" markerUnits="userSpaceOnUse"
                    orient="auto-start-reverse">
              <path d="M0 0 L10 5 L0 10 z" fill="var(--d-accent)" />
            </marker>
          </defs>

          {drawn.map((edge, i) => {
            const from = picture.at.get(edge.subject);
            const to = picture.at.get(edge.object);
            if (!from || !to) return null;
            const touched = !selected
              || edge.subject === selected || edge.object === selected;
            // Direction is not decoration here. "Desire leads to anger" and
            // "anger leads to desire" are different claims, and an undirected
            // line asserts neither.
            const angle = Math.atan2(to.cy - from.cy, to.cx - from.cx);
            const stop = to.r + 3;
            return (
              <g key={`${edge.subject}-${edge.predicate}-${edge.object}-${i}`}
                 className={`dgraph-edge${touched ? " on" : ""}`}
                 opacity={selected && !touched ? 0.13 : 1}>
                <line
                  x1={from.cx} y1={from.cy}
                  x2={to.cx - Math.cos(angle) * stop}
                  y2={to.cy - Math.sin(angle) * stop}
                  strokeWidth={0.9 + Math.sqrt(edge.evidence / heaviest) * 2.6}
                  // Dashed for a reading, solid for something stated plainly --
                  // the same convention the flow diagram above already uses for
                  // its connectors, and the distinction that keeps a graph of a
                  // religious text from asserting its interpretation as text.
                  strokeDasharray={edge.confidence === "interpretive" ? "5 4" : undefined}
                  markerEnd={`url(#dgraph-tip${touched && selected ? "-on" : ""})`}
                />
              </g>
            );
          })}

          {picture.points.map((point) => {
            const node = byId.get(point.id);
            if (!node) return null;
            const near = !selected
              || point.id === selected
              || drawn.some((e) =>
                   (e.subject === selected && e.object === point.id)
                   || (e.object === selected && e.subject === point.id));
            const r = point.r;
            return (
              <g key={point.id}
                 className={`dgraph-node${point.id === selected ? " sel" : ""}`}
                 opacity={selected && !near ? 0.16 : 1}
                 onClick={() => setSelected(point.id === selected ? null : point.id)}
                 role="button" tabIndex={0}
                 aria-label={`${node.name} — ${point.degree} claims`}
                 onKeyDown={(e) => {
                   if (e.key === "Enter" || e.key === " ") {
                     e.preventDefault();
                     setSelected(point.id === selected ? null : point.id);
                   }
                 }}>
                <NodeShape node={node} cx={point.cx} cy={point.cy} r={r} />
                {/* Drawn where the placement pass found room for it, and
                    always for whatever is being followed -- a reader who has
                    just selected something must be able to see which one it
                    was, even if its name would otherwise have been skipped. */}
                {(point.label || point.id === selected
                  || (selected !== null && near)) && (
                  <text
                    x={point.at === "right" ? point.cx + r + 4
                       : point.at === "left" ? point.cx - r - 4 : point.cx}
                    y={point.at === "above" ? point.cy - r - 5
                       : point.at === "below" ? point.cy + r + 11 : point.cy + 4}
                    textAnchor={point.at === "right" ? "start"
                                : point.at === "left" ? "end" : "middle"}>
                    {node.name}
                  </text>
                )}
              </g>
            );
          })}
        </svg>

        <div className="dgraph-key">
          <span><i className="dk-round" /> a person or a group</span>
          <span><i className="dk-box" /> an idea or an event</span>
          <span><i className="dk-solid" /> stated plainly</span>
          <span><i className="dk-dash" /> a reading of the argument</span>
        </div>
      </div>

      {/* Truncation is never silent, and the two kinds of it are different
          sentences. One is the drawing holding back; the other is the server
          capping what it will send an anonymous caller. */}
      <p className="dgraph-count">
        Showing <strong>{drawn.length}</strong> of {matching.length}
        {predicate ? " matching" : ""} claim{matching.length === 1 ? "" : "s"}
        {" "}across {picture.points.length} things, best-attested first.
        {picture.labelled < picture.points.length && (
          <> {picture.points.length - picture.labelled} of them are drawn
            without a name because one would not fit — select any shape to read
            it.</>
        )}
        {drawn.length < matching.length && (
          <> <button className="linkish"
                     onClick={() => setShown((n) => n + GRAPH_PAGE)}>
            Draw {Math.min(GRAPH_PAGE, matching.length - drawn.length)} more
          </button></>
        )}
        {data.truncated && (
          <> The corpus holds more than the {data.limit} the public graph
            returns — signing in reads the whole of it.</>
        )}
      </p>

      <div className="dgraph-claims">
        {reading.length === 0 ? (
          // Not the same as an empty corpus, and not phrased as if it were.
          <p className="hint">
            Nothing of that kind touches {chosen?.name ?? "this selection"}.
          </p>
        ) : reading.map((edge, i) => (
          <div className="dgraph-claim" key={`${edge.subject}-${edge.predicate}-${edge.object}-${i}`}>
            <span className="dgraph-said">
              {/* A sentence, not a triple. `{"subject":"ent_01H…"}` is a
                  payload; "Krishna puts forward detachment" is the claim. */}
              <button className="linkish" onClick={() => setSelected(edge.subject)}>
                {byId.get(edge.subject)?.name ?? "—"}
              </button>{" "}
              {glosses.get(edge.predicate) ?? edge.predicate.replace(/_/g, " ")}{" "}
              <button className="linkish" onClick={() => setSelected(edge.object)}>
                {byId.get(edge.object)?.name ?? "—"}
              </button>
            </span>
            <span className="dgraph-meta">
              {edge.confidence === "interpretive" && (
                <span className="chip" title="A defensible reading of what the text argues, not something it states in so many words.">
                  a reading
                </span>
              )}
              <span className="chip" title="How many separate verses assert this.">
                {edge.evidence} verse{edge.evidence === 1 ? "" : "s"}
              </span>
            </span>
          </div>
        ))}
      </div>

      <p className="demo-note">
        {app.note && <>{app.note} </>}
        Reading the graph costs nothing and is not charged against the day's
        questions — no model is called to draw it.
      </p>
    </div>
  );
}

function PublicDemo({ app, info, setInfo }: {
  app: DemoApp;
  // Widened to carry the third state the gallery has: `undefined` for "not
  // asked yet", which is what stops the page painting a screen of diagrams and
  // then discarding them.
  info: DemoInfo;
  setInfo: (f: (i: DemoInfo | null | undefined) => DemoInfo | null | undefined) => void;
}) {
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<{
    question: string; answer: string; grounded: boolean;
    citations: { marker: number; text: string }[];
  }[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  // Which half of the corpus is showing. Only ever "graph" for a corpus that
  // has one -- see the reset below, which is what stops a visitor stranded on a
  // tab that is no longer offered.
  const [view, setView] = useState<"ask" | "graph">("ask");
  const foot = useRef<HTMLDivElement | null>(null);
  const hasGraph = app.claims > 0;

  useEffect(() => {
    if (turns.length) foot.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns.length, busy]);

  // Switching corpus clears the transcript. Leaving it would show one corpus's
  // answers under another's name, with citations into records the new corpus
  // does not contain -- which reads as the demo answering from the wrong data,
  // because it is.
  useEffect(() => {
    setTurns([]);
    setOpen(null);
    setError(null);
    // Back to the question, every time. Three of the five corpora have no
    // graph, so keeping the tab across a switch would leave somebody looking
    // at a panel the new card does not offer -- and the tab strip they would
    // use to get back has just stopped rendering.
    setView("ask");
  }, [app.key]);

  async function send(preset?: string) {
    const asked = (preset ?? question).trim();
    if (!asked || busy) return;
    setBusy(true);
    setError(null);
    setQuestion("");
    try {
      const response = await fetch("/api/proxy/api/v1/public/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        // The key, never a project id. The server resolves it against the
        // published registry; a name it did not publish reaches nothing.
        body: JSON.stringify({ question: asked, demo: app.key }),
      });
      const body = await response.json();
      if (!response.ok) {
        // The server's sentence, not a status code. A visitor who has run out
        // of questions is not looking at an error, and "429" reads as broken.
        // `detail` is not always a sentence: FastAPI answers a validation
        // failure with a *list* of objects, and throwing that renders the
        // useless "[object Object]" -- which is what a visitor saw when the
        // proxy forwarded this body without a content-type.
        throw new Error(sentence(body?.detail));
      }
      setTurns((previous) => [...previous, body]);
      setInfo((i) => (i ? { ...i, remaining_today: Math.max(0, i.remaining_today - 1) } : i));
    } catch (e) {
      setError((e as Error).message);
      setQuestion(asked);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="demo-card" id="demo">
      <h2>{app.title}</h2>

      {/* Offered only where there is something behind it. A corpus written
          without enrichment has no graph by design, and a tab that opens onto
          an empty box reads as the feature being broken rather than absent. */}
      {hasGraph && (
        <div className="demoview" role="tablist" aria-label="How to read this corpus">
          <button role="tab" id="demotab-ask" aria-controls="demopanel-ask"
                  aria-selected={view === "ask"}
                  className={`demoview-tab${view === "ask" ? " on" : ""}`}
                  onClick={() => setView("ask")}>
            Ask it
            <span className="demoview-sub">with citations</span>
          </button>
          <button role="tab" id="demotab-graph" aria-controls="demopanel-graph"
                  aria-selected={view === "graph"}
                  className={`demoview-tab${view === "graph" ? " on" : ""}`}
                  onClick={() => setView("graph")}>
            See the graph
            <span className="demoview-sub">{app.claims} claims it extracted</span>
          </button>
        </div>
      )}

      {hasGraph && view === "graph" ? (
        <div className="demo-box" id="demopanel-graph" role="tabpanel"
             aria-labelledby="demotab-graph">
          <DemoGraph app={app} />
        </div>
      ) : (
      <div className="demo-box" id="demopanel-ask" role="tabpanel"
           aria-labelledby={hasGraph ? "demotab-ask" : undefined}>
        <div className="demo-scroll">
        {turns.length === 0 && (
          <div className="demo-starters">
            {app.questions.map((example) => (
              <button key={example} className="secondary" disabled={busy}
                      onClick={() => void send(example)}>
                {example}
              </button>
            ))}
          </div>
        )}

        {turns.map((turn, i) => (
          <div className="chatturn" key={i}>
            <div className="bubble asked">{turn.question}</div>
            <div className={`bubble answered${turn.grounded ? "" : " ungrounded"}`}>
              {turn.answer}
            </div>
            <div className="turnfoot">
              {!turn.grounded && (
                <span className="chip warnchip">not supported by the text</span>
              )}
              {turn.citations.length > 0 && (
                <button className="linkish"
                        onClick={() => setOpen(open === i ? null : i)}>
                  {open === i ? "▾" : "▸"} {turn.citations.length} passage
                  {turn.citations.length === 1 ? "" : "s"}
                </button>
              )}
            </div>
            {open === i && (
              <div className="turndetail">
                {turn.citations.map((c) => (
                  <div className="hit" key={c.marker}>
                    <div className="meta"><span className="chip on">[{c.marker}]</span></div>
                    <div className="text">{c.text}</div>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}

        {busy && (
          <div className="chatturn">
            <div className="bubble answered thinking">
              <span className="dots"><i /><i /><i /></span> reading the text…
            </div>
          </div>
        )}
        <div ref={foot} />
        </div>

        <div className="demo-composer">
          <input
            type="text" value={question} disabled={busy}
            placeholder={`Ask ${app.title} a question…`}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void send(); } }}
          />
          <button onClick={() => void send()} disabled={busy || !question.trim()}>
            {busy ? "Reading…" : "Ask"}
          </button>
          {/* Clearing also brings the starter questions back, which is the
            * point: a transcript somebody is done with is the one thing on the
            * card standing between them and asking something else. The opened
            * citation goes with it -- an index into turns that no longer exist
            * would reopen an unrelated answer's passages. */}
          {turns.length > 0 && (
            <button
              className="secondary"
              disabled={busy}
              onClick={() => { setTurns([]); setOpen(null); setError(null); }}
            >
              Clear
            </button>
          )}
        </div>
        {error && <p className="err">{error}</p>}
        <p className="demo-note">
          {app.note && <>{app.note} </>}
          {app.records > 0 && <>{app.records} records, ingested exactly as your own documents
          would be. </>}
          {/* The allowance sits beside the control that spends it rather than
            * being discovered at zero, and it is shared across every demo here
            * -- one budget for the gallery, not one each. */}
          <strong>{info.remaining_today}</strong> of {info.daily_cap} questions left today,
          across every demo — a fixed daily budget is what keeps this free.
        </p>
      </div>
      )}
    </div>
  );
}

export default function Landing({ authEnabled }: { authEnabled: boolean }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [caps, setCaps] = useState<Capabilities | null>(null);
  // `undefined` means *not yet known*, `null` means *asked and there is none*.
  // They were one value, and the difference is a whole screen: with the gallery
  // unresolved the page took the no-demo branch, painted the hero and all four
  // diagrams, and then threw them away the moment the fetch landed.
  const [demo, setDemo] = useState<DemoInfo | null | undefined>(undefined);
  // Which app the chat is pointed at. Null until the gallery arrives; the
  // first entry once it does, so a visitor lands on something answerable
  // rather than on a chooser.
  const [picked, setPicked] = useState<string | null>(null);
  // Which half of the page is showing. The demo and the diagrams were stacked,
  // which on a phone meant scrolling past a full sandbox to reach the first
  // picture — and past three pictures to reach the sign-in. Two tabs make each
  // one a whole screen rather than a leg of a long one.
  const [view, setView] = useState<"demo" | "diagrams">("demo");
  // The sign-in form is a panel off the top bar rather than half the hero: the
  // first thing a visitor should be able to do here is ask the corpus a
  // question, and a form demanding an account they do not have is the opposite
  // of that. Signing in is still one click away, where a header keeps it.
  const [signinOpen, setSigninOpen] = useState(false);

  useEffect(() => {
    void fetch("/api/proxy/api/v1/public/demos")
      .then((r) => (r.ok ? r.json() : null))
      .then((body: DemoInfo | null) => {
        setDemo(body);
        setPicked(body?.demos?.[0]?.key ?? null);
      })
      .catch(() => setDemo(null));
  }, []);

  // Escape closes it, because a panel that can only be dismissed by finding
  // the button again is a trap for anyone not using a mouse.
  useEffect(() => {
    if (!signinOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setSigninOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [signinOpen]);

  useEffect(() => {
    fetch("/api/capabilities")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => d && d.formats && setCaps(d))
      .catch(() => undefined);
  }, []);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error ?? "Sign-in failed.");
      window.location.reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const numbers = caps
    ? [
        { value: caps.formats, label: "file formats parsed" },
        { value: caps.data_types, label: "data types routed" },
        { value: caps.prompts, label: "extraction prompts" },
        { value: caps.webhook_providers, label: "webhook providers" },
        { value: caps.connectors, label: "app connectors" },
        { value: caps.alert_surfaces, label: "kinds of change you can watch" },
        { value: caps.generators, label: "things derivable from a memory" },
      ]
    : [];


  // One definition, two homes: the top-bar panel when the demo holds the
  // hero, and the hero itself when there is no demo to put there. Never
  // both at once -- two elements carrying `id="signin"` would make the
  // anchor mean whichever the browser happened to find first.
  const apps = demo?.demos ?? [];
  const app = apps.find((a) => a.key === picked) ?? apps[0] ?? null;
  const heroHasDemo = Boolean(app);
  // Optimistic while the gallery is in flight: assume there is one, because on
  // any deployment that has published demos there is, and the cost of being
  // wrong is a moment of "loading" on a deployment with none — against the cost
  // of being right, which was rendering four diagrams and deleting them.
  const demoShell = demo === undefined || heroHasDemo;
  const signInCard = authEnabled ? (
          <form className="signin-card" id="signin" onSubmit={submit}>
            <h2>Sign in</h2>
            <p className="empty" style={{ marginTop: 0 }}>
              Opens the console: add data, search it, and inspect every step.
            </p>
            <label>
              Email
              <input
                type="email"
                value={email}
                autoComplete="username"
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </label>
            <label>
              Password
              <input
                type="password"
                value={password}
                autoComplete="current-password"
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </label>
            <button type="submit" disabled={busy}>
              {busy ? "Signing in…" : "Sign in"}
            </button>
            {error && <p className="err">{error}</p>}
            <p className="empty">
              Your session is an httpOnly cookie. The browser never holds a token, and requests are
              attributed to you rather than to a shared key.
            </p>
            {/* Read from the deployment rather than described in both
              * directions. The page used to say nothing at all about how a
              * person gets an account, which is the first question somebody
              * without one has. */}
            {caps?.registration_mode === "invite_only" && (
              <p className="empty">
                <strong>New accounts are invite-only here.</strong> Somebody already inside issues
                one; it is single-use, expiring, and bound to your address.
              </p>
            )}
            {caps?.registration_mode === "open" && (
              <p className="empty">
                Anyone who can authenticate may create an account on this deployment — which grants
                an identity and <strong>no access to anything</strong> until a member adds you.
              </p>
            )}
            {caps?.registration_mode === "disabled" && (
              <p className="empty">
                New accounts are closed on this deployment. Existing members can still sign in.
              </p>
            )}
            {/* On the card, not only in the page footer. When a demo is present
                this card is a popover off the header, so the footer is nowhere
                near the person signing in — and the card is the login UI in the
                sense that matters. */}
            <p className="empty copyright">
              © {new Date().getFullYear()}{" "}
              <a href="https://buildgeek.ai" rel="noopener">buildgeek.ai</a>
            </p>
          </form>
        ) : (
          <div className="signin-card" id="signin">
            <h2>Sign-in is not configured</h2>
            <p className="empty">
              This deployment has no identity provider set, so the console cannot be opened from a
              browser.
            </p>
            <p className="empty copyright">
              © {new Date().getFullYear()}{" "}
              <a href="https://buildgeek.ai" rel="noopener">buildgeek.ai</a>
            </p>
          </div>
        );

  return (
    <div className="landing" id="top">
      <header className="topbar">
        {/* A real anchor rather than a scroll handler: it works without
            JavaScript, is reachable by keyboard, and offers the usual
            open-in-new-tab affordances a wordmark is expected to have. */}
        <a className="wordmark" href="#top" aria-label="Back to the top">
          <span className="dot" aria-hidden="true" />
          mem-dog
        </a>
        {/* Docs only. The comparison moved down into the tab strip, where it
            sits beside the demo and the diagrams — it is one of the three
            things a visitor came to do, not a utility link, and a header is
            where things go to be ignored. */}
        <nav className="tabs" aria-label="Sections">
          <a href="/docs">Docs</a>
        </nav>
        <div className="row">
          <ThemeToggle />
          {/* `demoShell`, not `heroHasDemo`. They differ for the moment the
              gallery is in flight, and in that moment the old condition put a
              `#signin` anchor in the header while the hero that owns that id
              was suppressed — a Sign in link pointing at nothing, and the card
              rendered in neither place. Introduced by making the demo shell
              optimistic; the two conditions have to agree about which layout
              is being drawn. */}
          {demoShell ? (
            <div className="signin-menu">
              <button
                className="tab-cta"
                aria-expanded={signinOpen}
                onClick={() => setSigninOpen(!signinOpen)}
              >
                {signinOpen ? "Close" : "Sign in"}
              </button>
              {signinOpen && <div className="signin-pop">{signInCard}</div>}
            </div>
          ) : (
            <a className="tab-cta" href="#signin">Sign in</a>
          )}
        </div>
      </header>

      {/* The demo is the page's opening move, at full width and above the
          argument for it. Somebody who has already asked the corpus a question
          reads the copy below as an explanation of something they have seen;
          the other order asks them to take it on faith first. */}
      {/* The row is always here, because the comparison lives in it now and a
          deployment with no demo must not lose the link. The *tablist* inside
          it is conditional: a tab strip over a single panel is a control that
          decides nothing. */}
      <div className="landtabs">
        {demoShell && (
          <div className="landtabgroup" role="tablist" aria-label="What to look at">
            <button
              role="tab" id="tab-demo" aria-controls="panel-demo"
              aria-selected={view === "demo"}
              className={`landtab${view === "demo" ? " on" : ""}`}
              onClick={() => setView("demo")}
            >
              Try it
              <span className="landtab-sub">ask a real corpus, no account</span>
            </button>
            <button
              role="tab" id="tab-diagrams" aria-controls="panel-diagrams"
              aria-selected={view === "diagrams"}
              className={`landtab${view === "diagrams" ? " on" : ""}`}
              onClick={() => setView("diagrams")}
            >
              How it works
              <span className="landtab-sub">the path in, and what it is for</span>
            </button>
          </div>
        )}
        {/* Beside the tabs and deliberately not one of them. It leaves the
            page, and `role="tab"` promises a panel that switches in place —
            a link wearing that role is a promise the click breaks. The arrow
            is what says so before the click rather than after. */}
        <a className="landtab landtab-out" href="/docs/compare">
          How it compares <span aria-hidden="true">↗</span>
          <span className="landtab-sub">against Onyx, Glean and the rest</span>
        </a>
      </div>

      {demoShell && view === "demo" && (
        <section className="hero-demo" id="panel-demo" role="tabpanel"
                 aria-labelledby="tab-demo">
          <p className="eyebrow">Memory layer · sandbox · no account needed</p>
          {/* Only when there is a choice to make. A row of one card is a
            * control that decides nothing, and it pushes the thing a visitor
            * came to do further down the page. */}
          {apps.length > 1 && (
            <div className="demo-apps" role="tablist" aria-label="Demo corpora">
              {apps.map((a) => (
                <button
                  key={a.key}
                  role="tab"
                  aria-selected={a.key === app!.key}
                  className={`demo-app${a.key === app!.key ? " on" : ""}`}
                  onClick={() => setPicked(a.key)}
                >
                  <span className="demo-app-title">{a.title}</span>
                  {/* What this one shows that the others do not. Without it the
                    * gallery is a row of names and every card looks alike. */}
                  {a.blurb && <span className="demo-app-blurb">{a.blurb}</span>}
                  {a.records > 0 && (
                    <span className="demo-app-count">{a.records} records</span>
                  )}
                </button>
              ))}
            </div>
          )}
          {app ? (
            <PublicDemo app={app} info={demo!} setInfo={setDemo} />
          ) : (
            <p className="empty">Loading the sandbox…</p>
          )}
        </section>
      )}

      {/* One headline, one diagram. Everything that used to be argued here --
          nine sections of prose and a comparison matrix -- is in the docs,
          where somebody who wants it is already looking. */}
      {!demoShell && (
        <section className="hero-split">
          <div>
            <p className="eyebrow">Memory layer · sandbox</p>
            <h1>A memory layer that <em>shows its work</em>.</h1>
          </div>
          {signInCard}
        </section>
      )}

      {/* Without a demo there is no choice to present, so the diagrams are
          simply the page rather than one tab of it. */}
      {demoShell ? (
        view === "diagrams" && (
          <div id="panel-diagrams" role="tabpanel" aria-labelledby="tab-diagrams">
            <Diagrams />
          </div>
        )
      ) : (
        <Diagrams />
      )}

      <footer className="landfoot">
        {/* The year comes from the clock rather than a literal, because a
            hardcoded one is wrong every January and nobody notices until a
            visitor does. */}
        <span className="empty copyright">
          © {new Date().getFullYear()}{" "}
          <a href="https://buildgeek.ai" rel="noopener">buildgeek.ai</a>
        </span>
      </footer>
    </div>
  );
}
