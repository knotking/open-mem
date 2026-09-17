"use client";

/**
 * A corpus's claims, drawn and then read out.
 *
 * **One copy, two callers.** The landing page shows the published demo's graph
 * and the console shows the signed-in project's, and those differ only in where
 * the rows come from and what one piece of evidence is called. Everything that
 * is actually hard here — a deterministic layout, prising overlapping nodes
 * apart, deciding which labels fit — is the same work, and a second copy of it
 * is a second set of the three rendering bugs this one already has fixed.
 *
 * So this component takes rows and renders them. It does no fetching: the two
 * surfaces authenticate differently, cache differently, and fail differently,
 * and folding that in here would mean one component knowing about both.
 *
 * It holds a selection and a filter, and **it does not reset them itself**.
 * Give it a `key` that changes when the subject does — the corpus, the project —
 * and React discards the state with the instance. A component that tried to
 * notice would need to know what "different data" means to a caller it cannot
 * see.
 */

import { useMemo, useState, type ReactNode } from "react";

export type GraphNode = { id: string; name: string; type: string };
export type GraphEdge = {
  subject_id: string; predicate: string; object_id: string;
  evidence: number; confidence_class: string;
};
export type PredicateSpec = {
  predicate: string; gloss: string; confidence: string;
};
export type GraphData = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  predicates: PredicateSpec[];
  truncated: boolean;
  limit: number;
};

/**
 * How many claims are drawn before the reader asks for more.
 *
 * Not the whole cap the server will send. A node-link drawing stops being
 * readable long before it stops being correct, and the failure is total — past
 * a certain density every graph is the same grey disc. So the picture opens at
 * a size somebody can read and grows on request.
 */
const GRAPH_PAGE = 40;

/** Where a node's name sits relative to it. `centre` is the last resort — see
 *  the placement pass, which is where it earns its place. */
type Placement = "below" | "above" | "left" | "right" | "centre";

const VIEW_W = 900;
const VIEW_H = 520;
const PAD = 46;

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
export function layout(count: number, links: { s: number; t: number }[]) {
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
      // between a graph and a knot.** Undamped, every claim touching the
      // busiest node pulls it with full force, so it is dragged into the middle
      // of its own neighbours and the cluster collapses onto itself -- which is
      // exactly what the first version drew. Damping by degree lets a hub sit
      // at the centre of its neighbourhood and lets the neighbourhood spread
      // out around it, and it costs the leaves nothing because their degree
      // is one.
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

/**
 * A name short enough to draw.
 *
 * Entity names come from a model reading a document, and some of them are a
 * sentence — *"The Yoga of the Division Of the Three Gunas"* is 43 characters,
 * and one real project holds a 73. At the drawing's font that is half the
 * canvas wide, so the placement pass rejects it everywhere and the node is left
 * anonymous: the longest names, which are usually the most specific, become the
 * ones nobody can read.
 *
 * Truncating is the lesser loss, and only for the *drawing* — the claims under
 * it, the selection button and the accessible name all carry the whole thing.
 */
function shortName(name: string): string {
  return name.length > 30 ? `${name.slice(0, 29)}…` : name;
}

/**
 * What a node *is*, as a shape.
 *
 * **Shape rather than colour, and the split is the API's own.** `predicates.py`
 * types every relation against `AGENT` (a person or an organization) and `IDEA`
 * (a topic, an event, or something uncategorised), because that is the
 * distinction the vocabulary actually turns on -- a doctrine does not work for
 * a city. Drawing that same split is what makes "who does what" legible at a
 * glance: the round things are the ones that can act.
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

export default function GraphView({
  data, lede, note, evidenceUnit = ["record", "records"], emptyNote,
}: {
  data: GraphData;
  /** What the drawing is, in the caller's own terms. */
  lede?: ReactNode;
  /** Provenance or cost, under the claims. */
  note?: ReactNode;
  /** Singular and plural for one piece of evidence — a verse here, a record
   *  there. The chip says "9 verses", and "9 records" would be a different and
   *  wronger sentence about the same number. */
  evidenceUnit?: [string, string];
  /** What it means for *this* corpus to have no claims. A corpus nobody
   *  enriched and a corpus nothing was found in are different facts. */
  emptyNote?: ReactNode;
}) {
  const [predicate, setPredicate] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [shown, setShown] = useState(GRAPH_PAGE);

  const byId = useMemo(
    () => new Map(data.nodes.map((n) => [n.id, n])),
    [data.nodes],
  );

  /**
   * Which claims to draw first: how hard the corpus insisted, then how
   * connected the thing it insisted about is.
   *
   * **Evidence alone is not an ordering on real data.** It reads well on a
   * corpus where a few claims are made twenty times over, and on an ordinary
   * project almost every claim is made exactly once — 91 of 92, the first time
   * this was pointed at one. A sort whose key is constant is not a sort, so the
   * first forty were simply the first forty the server happened to return: a
   * random sample spread across the whole corpus, which draws as a dozen
   * disconnected islands rather than as a graph. Two thirds of the nodes had a
   * single edge and nothing touched anything else.
   *
   * Breaking the tie on the endpoints' degree pulls the dense parts forward
   * instead. What arrives is the corpus's actual clusters — the lab and its
   * people, the text and its chapters — because a claim about a well-connected
   * thing is one more edge in a picture that is already coherent, while a claim
   * between two things nothing else mentions is an island wherever it lands.
   *
   * Degree is measured over everything that passes the filter rather than over
   * what is drawn, so it ranks by the node's place in the *corpus* and does not
   * change as the reader draws more.
   */
  const matching = useMemo(() => {
    const all = predicate
      ? data.edges.filter((e) => e.predicate === predicate)
      : [...data.edges];
    const degree = new Map<string, number>();
    for (const edge of all) {
      degree.set(edge.subject_id, (degree.get(edge.subject_id) ?? 0) + 1);
      degree.set(edge.object_id, (degree.get(edge.object_id) ?? 0) + 1);
    }
    const pull = (e: GraphEdge) =>
      (degree.get(e.subject_id) ?? 0) + (degree.get(e.object_id) ?? 0);
    return all.sort((a, b) => b.evidence - a.evidence || pull(b) - pull(a));
  }, [data.edges, predicate]);

  const drawn = useMemo(() => matching.slice(0, shown), [matching, shown]);

  /** Positions, degrees, radii, and which names fit. */
  const picture = useMemo(() => {
    const ids: string[] = [];
    const index = new Map<string, number>();
    const degree = new Map<string, number>();
    for (const edge of drawn) {
      for (const id of [edge.subject_id, edge.object_id]) {
        if (!index.has(id)) { index.set(id, ids.length); ids.push(id); }
        degree.set(id, (degree.get(id) ?? 0) + 1);
      }
    }
    const links = drawn.map((e) => ({
      s: index.get(e.subject_id)!, t: index.get(e.object_id)!,
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
    // circles drawn for them, and the first version of this put one label's
    // node on top of another's. This is a separate, deterministic pass that
    // knows the radii the force stage cannot, and it converges quickly because
    // it only ever moves nodes that overlap.
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
    }));

    return {
      points,
      at: new Map(points.map((p) => [p.id, p])),
      shapes: new Map(points.map((point) => [point.id, {
        x0: point.cx - point.r - 1, x1: point.cx + point.r + 1,
        y0: point.cy - point.r - 1, y1: point.cy + point.r + 1,
      }])),
    };
  }, [drawn, byId]);

  /**
   * Which names and which predicates actually get drawn.
   *
   * **Separate from the layout, and that separation is what makes it work.**
   * Placement has to know the selection — the whole point is that following a
   * node promotes its edges — and the layout must not, because re-running a
   * force simulation on every click is both slow and disorienting, as the
   * picture would rearrange under the pointer. Positions are a pure function of
   * the claims; labels are a function of the positions *and* what is being
   * followed.
   *
   * **The order is the priority, and it is the whole design.** Space runs out
   * long before the labels do — 40 claims and 45 names in one frame, measured —
   * so what matters is who gets it first:
   *
   * 1. *the followed node's own name*, so a reader can see what they clicked;
   * 2. *the predicates on its edges*, because "related how?" is the question
   *    following something asks, and an unlabelled arrow does not answer it;
   * 3. *its neighbours' names*, which is the other half of that answer;
   * 4. *everything else by degree*, which is the unselected view's whole rule.
   *
   * Getting this order wrong is not subtle. With neighbour names placed before
   * edge predicates, following a hub labelled **three of its fourteen edges** —
   * the names had taken every wedge — which reads as most of the claims having
   * no predicate at all.
   */
  const labels = useMemo(() => {
    type Box = { x0: number; y0: number; x1: number; y1: number };
    const overlaps = (a: Box, b: Box) =>
      a.x0 < b.x1 && a.x1 > b.x0 && a.y0 < b.y1 && a.y1 > b.y0;
    const shapes = [...picture.shapes.values()];
    const boxes: Box[] = [];
    const placement = new Map<string, Placement>();
    const edges: { key: string; x: number; y: number; text: string }[] = [];

    /** A name, at the first of five positions that is free. */
    const placeNode = (point: typeof picture.points[number], force: boolean) => {
      const node = byId.get(point.id);
      if (!node) return;
      // Approximate, and deliberately so: measuring text needs a laid-out DOM,
      // and being a few pixels generous costs a label that would have fitted
      // while being wrong the other way costs one that overlaps.
      const half = Math.max(12, shortName(node.name).length * 3.2);
      // Below first because a name under its shape is the easiest to associate;
      // then above and the sides, because a dense middle has room there and
      // refusing to look left nearly half the graph unnamed on a frame with
      // space to spare.
      //
      // **`centre` last, and it is what stops a hub going unnamed.** A hub is
      // ringed by its own neighbours at a fixed separation, so a name wider
      // than that ring collides with every one of them and the busiest thing in
      // the picture ends up the only anonymous shape in it -- `BHAGAVAD-GITA`
      // at degree 14, while ten of its leaves were labelled. So `centre` is
      // checked against other labels only: overlapping a shape stays legible
      // through the halo, and overlapping another *name* never does.
      const spots: [number, number, Placement][] = [
        [point.cx, point.cy + point.r + 9, "below"],
        [point.cx, point.cy - point.r - 5, "above"],
        [point.cx + point.r + 4 + half, point.cy + 4, "right"],
        [point.cx - point.r - 4 - half, point.cy + 4, "left"],
        [point.cx, point.cy + 4, "centre"],
      ];
      for (const [lx, ly, at] of spots) {
        const box = { x0: lx - half, x1: lx + half, y0: ly - 9, y1: ly + 3 };
        if (box.x0 < 2 || box.x1 > VIEW_W - 2) continue;
        if (box.y0 < 2 || box.y1 > VIEW_H - 2) continue;
        if (boxes.some((b) => overlaps(box, b))) continue;
        if (at !== "centre" && !force && shapes.some((b) => overlaps(box, b))) continue;
        boxes.push(box);
        placement.set(point.id, at);
        return;
      }
    };

    /** A predicate, along its own line. */
    const placeEdge = (edge: GraphEdge, force: boolean) => {
      const from = picture.at.get(edge.subject_id);
      const to = picture.at.get(edge.object_id);
      if (!from || !to) return;
      const text = edge.predicate.replace(/_/g, " ");
      // Narrower than a name: 9.5px against 11.5.
      const half = Math.max(10, text.length * 2.8);
      // **Slid toward the less connected end rather than sitting at the
      // midpoint.** On a hub the spokes are short and every midpoint lands in
      // the same crowded ring beside it, so the labels queue for one patch of
      // canvas; 62% out along each spoke gives every one its own wedge.
      const hubFirst = from.degree >= to.degree;
      const a = hubFirst ? from : to;
      const z = hubFirst ? to : from;
      const x = a.cx + (z.cx - a.cx) * 0.62;
      const y = a.cy + (z.cy - a.cy) * 0.62;
      const box = { x0: x - half, x1: x + half, y0: y - 7, y1: y + 3 };
      if (box.x0 < 2 || box.x1 > VIEW_W - 2) return;
      if (box.y0 < 2 || box.y1 > VIEW_H - 2) return;
      // Never over another label: two strings on top of each other lose both.
      if (boxes.some((b) => overlaps(box, b))) return;
      // Over a shape is allowed for an edge being followed -- the halo carries
      // it, and a followed edge with no predicate is the thing this exists to
      // prevent.
      if (!force && shapes.some((b) => overlaps(box, b))) return;
      boxes.push(box);
      edges.push({
        key: `${edge.subject_id}-${edge.predicate}-${edge.object_id}`, x, y, text,
      });
    };

    const byDegree = [...picture.points].sort((a, b) => b.degree - a.degree);

    if (selected) {
      const root = picture.at.get(selected);
      if (root) placeNode(root, true);
      const touching = drawn.filter(
        (e) => e.subject_id === selected || e.object_id === selected);
      for (const edge of touching) placeEdge(edge, true);
      const neighbours = new Set(touching.flatMap(
        (e) => [e.subject_id, e.object_id]));
      for (const point of byDegree) {
        if (point.id !== selected && neighbours.has(point.id)) placeNode(point, false);
      }
    } else {
      for (const point of byDegree) placeNode(point, false);
      for (const edge of drawn) placeEdge(edge, false);
    }

    return { placement, edges, named: placement.size };
  }, [picture, drawn, selected, byId]);


  const heaviest = useMemo(
    () => Math.max(1, ...drawn.map((e) => e.evidence)),
    [drawn],
  );
  const glosses = useMemo(
    () => new Map(data.predicates.map((p) => [p.predicate, p.gloss])),
    [data.predicates],
  );

  // The claims under the drawing follow the selection, so the two halves never
  // disagree about what is being looked at.
  const reading = selected
    ? drawn.filter((e) => e.subject_id === selected || e.object_id === selected)
    : drawn;

  const [one, many] = evidenceUnit;

  // Three different situations, three different sentences. A corpus that was
  // never read by a model has no graph *by design*; a filter that matched
  // nothing is the reader's own doing and is undone by changing it back.
  if (data.edges.length === 0) {
    return (
      <p className="hint">
        {emptyNote ?? (
          <>Nothing has been extracted here yet. A graph exists only where
          records were read by a model — storing and indexing them does not
          produce one.</>
        )}
      </p>
    );
  }

  const chosen = selected ? byId.get(selected) : null;

  return (
    <div className="dgraph">
      {lede && <p className="hint dgraph-lede">{lede}</p>}

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
             aria-label={`${picture.points.length} things joined by ${drawn.length} extracted claims.`}>
          <defs>
            {/* `markerUnits="userSpaceOnUse"`, because the default is
                `strokeWidth` -- which scales the arrowhead with the line, so a
                claim drawn thick precisely because many records make it arrived
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
            const from = picture.at.get(edge.subject_id);
            const to = picture.at.get(edge.object_id);
            if (!from || !to) return null;
            const touched = !selected
              || edge.subject_id === selected || edge.object_id === selected;
            // Direction is not decoration here. "Desire leads to anger" and
            // "anger leads to desire" are different claims, and an undirected
            // line asserts neither.
            const angle = Math.atan2(to.cy - from.cy, to.cx - from.cx);
            const stop = to.r + 3;
            return (
              <g key={`${edge.subject_id}-${edge.predicate}-${edge.object_id}-${i}`}
                 className={`dgraph-edge${touched ? " on" : ""}`}
                 opacity={selected && !touched ? 0.13 : 1}>
                <line
                  x1={from.cx} y1={from.cy}
                  x2={to.cx - Math.cos(angle) * stop}
                  y2={to.cy - Math.sin(angle) * stop}
                  strokeWidth={0.9 + Math.sqrt(edge.evidence / heaviest) * 2.6}
                  // Dashed for a reading, solid for something stated plainly --
                  // the distinction that keeps a graph from asserting its own
                  // interpretation as the text.
                  strokeDasharray={edge.confidence_class === "interpretive" ? "5 4" : undefined}
                  markerEnd={`url(#dgraph-tip${touched && selected ? "-on" : ""})`}
                />
              </g>
            );
          })}

          <g className="dgraph-edgelabels">
            {labels.edges.map((label) => (
              <text key={label.key} x={label.x} y={label.y}>{label.text}</text>
            ))}
          </g>

          {picture.points.map((point) => {
            const node = byId.get(point.id);
            if (!node) return null;
            const near = !selected
              || point.id === selected
              || drawn.some((e) =>
                   (e.subject_id === selected && e.object_id === point.id)
                   || (e.object_id === selected && e.subject_id === point.id));
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
                {/* The whole value, for a name the drawing had to shorten and
                    for one it could not place at all. `aria-label` above serves
                    a screen reader; this serves a pointer. */}
                <title>{`${node.name} — ${node.type}, ${point.degree} claim${
                  point.degree === 1 ? "" : "s"}`}</title>
              </g>
            );
          })}

          {/* **Every label above every shape.** They used to live inside each
              node's own group, which paints them in node order -- so a node
              drawn later covered the name of one drawn earlier, and a centred
              hub label lost its first characters to whichever neighbour came
              next. `BHAGAVAD-GITA` rendered as `HAGAVAD-GITA`. A separate layer
              is the whole fix; the halo was never going to help, because the
              thing covering the text was a filled shape rather than a line. */}
          <g className="dgraph-labels">
            {picture.points.map((point) => {
              const node = byId.get(point.id);
              if (!node) return null;
              const near = !selected
                || point.id === selected
                || drawn.some((e) =>
                     (e.subject_id === selected && e.object_id === point.id)
                     || (e.object_id === selected && e.subject_id === point.id));
              // Only where the placement pass found room. With a selection
              // active that pass already prioritised the followed node and its
              // neighbours, so there is nothing to force here.
              const at = labels.placement.get(point.id);
              if (!at) return null;
              const r = point.r;
              return (
                <text key={point.id}
                      className={`${point.id === selected ? "sel" : ""}${
                        at === "centre" ? " over" : ""}`}
                      opacity={selected && !near ? 0.16 : 1}
                      x={at === "right" ? point.cx + r + 4
                         : at === "left" ? point.cx - r - 4 : point.cx}
                      y={at === "above" ? point.cy - r - 5
                         : at === "below" ? point.cy + r + 11 : point.cy + 4}
                      textAnchor={at === "right" ? "start"
                                  : at === "left" ? "end" : "middle"}>
                  {shortName(node.name)}
                </text>
              );
            })}
          </g>
        </svg>

        <div className="dgraph-key">
          <span><i className="dk-round" /> a person or a group</span>
          <span><i className="dk-box" /> an idea or an event</span>
          <span><i className="dk-solid" /> stated plainly</span>
          <span><i className="dk-dash" /> a reading of the argument</span>
        </div>
      </div>

      {/* Truncation is never silent, and the three kinds of it are different
          sentences. One is the drawing holding back, one is a name that would
          not fit, one is the server capping what it will return. */}
      <p className="dgraph-count">
        Showing <strong>{drawn.length}</strong> of {matching.length}
        {predicate ? " matching" : ""} claim{matching.length === 1 ? "" : "s"}
        {" "}across {picture.points.length} things, best-attested first.
        {labels.named < picture.points.length && (
          <> {picture.points.length - labels.named} of them are drawn without a
            name because one would not fit — select any shape to read it, and
            its own claims get labelled too.</>
        )}
        {drawn.length < matching.length && (
          <> <button className="linkish"
                     onClick={() => setShown((n) => n + GRAPH_PAGE)}>
            Draw {Math.min(GRAPH_PAGE, matching.length - drawn.length)} more
          </button></>
        )}
        {data.truncated && (
          <> This corpus holds more than the {data.limit} claims this view
            asks for.</>
        )}
      </p>

      <div className="dgraph-claims">
        {reading.length === 0 ? (
          // Not the same as an empty corpus, and not phrased as if it were.
          <p className="hint">
            Nothing of that kind touches {chosen?.name ?? "this selection"}.
          </p>
        ) : reading.map((edge, i) => (
          <div className="dgraph-claim"
               key={`${edge.subject_id}-${edge.predicate}-${edge.object_id}-${i}`}>
            <span className="dgraph-said">
              {/* A sentence, not a triple. `{"subject":"ent_01H…"}` is a
                  payload; "Krishna puts forward detachment" is the claim. */}
              <button className="linkish" onClick={() => setSelected(edge.subject_id)}>
                {byId.get(edge.subject_id)?.name ?? "—"}
              </button>{" "}
              {glosses.get(edge.predicate) ?? edge.predicate.replace(/_/g, " ")}{" "}
              <button className="linkish" onClick={() => setSelected(edge.object_id)}>
                {byId.get(edge.object_id)?.name ?? "—"}
              </button>
            </span>
            <span className="dgraph-meta">
              {edge.confidence_class === "interpretive" && (
                <span className="chip" title="A defensible reading of what the text argues, not something it states in so many words.">
                  a reading
                </span>
              )}
              <span className="chip" title={`How many separate ${many} assert this.`}>
                {edge.evidence} {edge.evidence === 1 ? one : many}
              </span>
            </span>
          </div>
        ))}
      </div>

      {note && <p className="demo-note">{note}</p>}
    </div>
  );
}
