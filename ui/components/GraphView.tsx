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

  /** How many claims are listed before the reader asks for more. */
  const reading = useMemo(
    () => (selected
      ? matching.filter(
          (e) => e.subject_id === selected || e.object_id === selected)
      : matching.slice(0, shown)),
    [matching, shown, selected],
  );

  const glosses = useMemo(
    () => new Map(data.predicates.map((p) => [p.predicate, p.gloss])),
    [data.predicates],
  );

  /** Every kind of relationship in the corpus, with how much of it there is --
   *  computed over all the edges rather than the listed ones, so narrowing does
   *  not rewrite the summary that explains it. */
  const kinds = useMemo(() => {
    const count = new Map<string, number>();
    for (const edge of data.edges) {
      count.set(edge.predicate, (count.get(edge.predicate) ?? 0) + 1);
    }
    return [...count.entries()]
      .map(([predicate, n]) => ({ predicate, count: n }))
      .sort((a, b) => b.count - a.count || a.predicate.localeCompare(b.predicate));
  }, [data.edges]);

  /**
   * The claims, gathered under the kind of relationship they are.
   *
   * **A flat list buries the thing the vocabulary exists to express.** Fifty
   * rows of "A — some phrase — B" reads as fifty unrelated facts, and the
   * question somebody brings to a graph is what *kinds* of relationship a
   * corpus asserts and how much of each.
   */
  const grouped = useMemo(() => {
    const by = new Map<string, GraphEdge[]>();
    for (const edge of reading) {
      const rows = by.get(edge.predicate);
      if (rows) rows.push(edge);
      else by.set(edge.predicate, [edge]);
    }
    return [...by.entries()]
      .map(([predicate, rows]) => ({ predicate, rows }))
      .sort((a, b) => b.rows.length - a.rows.length
                      || a.predicate.localeCompare(b.predicate));
  }, [reading]);

  /**
   * Every entity in play, with how many claims touch it.
   *
   * **This is the list the drawing could not be.** A name on a canvas has to
   * fit between the shapes around it, and on a dense graph most do not — which
   * left the most connected things, the ones worth reading first, as the ones
   * most likely to be anonymous. A list has room for all of them, in an order
   * that means something, and each is a way in: choosing one narrows the claims
   * beside it.
   *
   * Counted over the claims that pass the current filter rather than over the
   * whole corpus, so the number beside a name always explains the rows next to
   * it.
   */
  const ranked = useMemo(() => {
    const claims = new Map<string, number>();
    for (const edge of matching) {
      claims.set(edge.subject_id, (claims.get(edge.subject_id) ?? 0) + 1);
      claims.set(edge.object_id, (claims.get(edge.object_id) ?? 0) + 1);
    }
    return [...claims.entries()]
      .map(([id, n]) => ({
        id,
        name: byId.get(id)?.name ?? id,
        type: byId.get(id)?.type ?? "other",
        claims: n,
      }))
      .sort((a, b) => b.claims - a.claims || a.name.localeCompare(b.name));
  }, [matching, byId]);

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

      {/* **The vocabulary first, as counted controls.** What kinds of
          relationship a corpus asserts, and how much of each, is the thing a
          typed graph knows that a pile of documents does not. */}
      <p className="dgraph-kindlabel">
        Kinds of relationship here — choose one to narrow the lists below
      </p>
      <div className="dgraph-kinds">
        <button className={`dgraph-kindchip${predicate === "" ? " on" : ""}`}
                onClick={() => { setPredicate(""); setSelected(null); }}>
          all kinds <span>{data.edges.length}</span>
        </button>
        {kinds.map((k) => (
          <button key={k.predicate}
                  className={`dgraph-kindchip${predicate === k.predicate ? " on" : ""}`}
                  title={glosses.get(k.predicate) ?? k.predicate}
                  onClick={() => {
                    setPredicate(predicate === k.predicate ? "" : k.predicate);
                    setSelected(null);
                  }}>
            {k.predicate.replace(/_/g, " ")} <span>{k.count}</span>
          </button>
        ))}
      </div>

      <div className="dgraph-split">
        {/* **The entities, as a list.** They were only ever labels on a
            drawing, which meant that when a name did not fit there was nowhere
            else to read it -- and on a dense graph most names do not fit. A
            list has room for every one of them, sorts by how connected each is,
            and does not have to be squeezed between two arrows. */}
        <div className="dgraph-side">
          <div className="dgraph-kind">
            <code>entities</code>
            <span className="dgraph-gloss">what this corpus is about</span>
            <span className="dgraph-kind-n">{ranked.length}</span>
          </div>
          <div className="dgraph-scroll">
            {ranked.length === 0 ? (
              <p className="hint" style={{ padding: 12, margin: 0 }}>
                Nothing of that kind is asserted about anything here.
              </p>
            ) : ranked.map((entity) => (
              <button
                key={entity.id}
                className={`dgraph-entity${entity.id === selected ? " on" : ""}`}
                title={`${entity.type} · ${entity.claims} claim${
                  entity.claims === 1 ? "" : "s"}`}
                onClick={() => setSelected(entity.id === selected ? null : entity.id)}
              >
                <span className="dgraph-ename">{entity.name}</span>
                <span className="dgraph-etype">{entity.type}</span>
                <span className="dgraph-ecount">{entity.claims}</span>
              </button>
            ))}
          </div>
        </div>

        {/* The claims themselves, gathered under the kind of relationship they
            are -- so the shape of the corpus is legible from the text, which is
            the half that always fits. */}
        <div className="dgraph-side dgraph-wide">
          <div className="dgraph-kind">
            <code>relationships</code>
            <span className="dgraph-gloss">
              {chosen ? `everything touching ${chosen.name}` : "what it asserts"}
            </span>
            <span className="dgraph-kind-n">{reading.length}</span>
          </div>
          <div className="dgraph-scroll">
            {reading.length === 0 ? (
              <p className="hint" style={{ padding: 12, margin: 0 }}>
                Nothing of that kind touches {chosen?.name ?? "this selection"}.
              </p>
            ) : grouped.map((group) => (
              <div className="dgraph-group" key={group.predicate}>
                <div className="dgraph-sub">
                  <code>{group.predicate}</code>
                  <span className="dgraph-gloss">
                    {glosses.get(group.predicate)
                      ?? group.predicate.replace(/_/g, " ")}
                  </span>
                  <span className="dgraph-kind-n">{group.rows.length}</span>
                </div>
                {group.rows.map((edge, i) => (
                  <div className="dgraph-claim"
                       key={`${edge.subject_id}-${edge.object_id}-${i}`}>
                    <span className="dgraph-said">
                      {/* A sentence, not a triple. Either end is a way in:
                          clicking one narrows both lists to it. */}
                      <button className="linkish"
                              onClick={() => setSelected(edge.subject_id)}>
                        {byId.get(edge.subject_id)?.name ?? "—"}
                      </button>{" "}
                      <span className="dgraph-arrow" aria-hidden="true">→</span>{" "}
                      <button className="linkish"
                              onClick={() => setSelected(edge.object_id)}>
                        {byId.get(edge.object_id)?.name ?? "—"}
                      </button>
                    </span>
                    <span className="dgraph-meta">
                      {edge.confidence_class === "interpretive" && (
                        <span className="chip" title="A defensible reading of what the text argues, not something it states in so many words.">
                          a reading
                        </span>
                      )}
                      <span className="chip"
                            title={`How many separate ${many} assert this.`}>
                        {edge.evidence} {edge.evidence === 1 ? one : many}
                      </span>
                    </span>
                  </div>
                ))}
              </div>
            ))}
          </div>
        </div>
      </div>

      <p className="dgraph-count">
        {chosen ? (
          <>
            Showing everything that touches <strong>{chosen.name}</strong>.{" "}
            <button className="linkish" onClick={() => setSelected(null)}>
              Show all {matching.length} again
            </button>
          </>
        ) : (
          <>
            <strong>{reading.length}</strong> of {matching.length}
            {predicate ? " matching" : ""} claim{matching.length === 1 ? "" : "s"},
            best-attested first.
            {reading.length < matching.length && (
              <> <button className="linkish"
                         onClick={() => setShown((n) => n + GRAPH_PAGE)}>
                Show {Math.min(GRAPH_PAGE, matching.length - reading.length)} more
              </button></>
            )}
          </>
        )}
        {data.truncated && (
          <> This corpus holds more than the {data.limit} claims this view asks
            for.</>
        )}
      </p>


      {note && <p className="demo-note">{note}</p>}
    </div>
  );
}
