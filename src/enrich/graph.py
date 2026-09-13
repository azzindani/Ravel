"""The citation graph, built once over the whole corpus.

Why this is a separate module from `entities.py`
------------------------------------------------
`entities.py` runs per chunk and produces references. This runs after every chunk exists
and produces the metrics that are properties of the *corpus*: how often an instrument is
cited, and how central it is to the network of citations.

That split is the finding `ABSORPTION.md` §6 records. The incumbent corpus has
`kg_pagerank` and `kg_degree_centrality` columns, NULL on all 748,558 rows, and the reason
is not that the code was missing. `06_ID_Legal/core/knowledge_graph/relationship_graph.py`
implements `nx.pagerank` and `nx.degree_centrality` correctly over a real `nx.DiGraph`,
and betweenness lives beside it. Grepping the whole package for `RelationshipGraph`,
`add_document` or `build_graph` outside the module returns exactly one caller: its own unit
test. Nothing ever built the graph from the corpus.

It is a **phase error**, the same shape as clustering (`CLAUDE.md` §3). A whole-corpus
artifact cannot be produced by a per-chunk pass, because there is no point during that
pass at which the graph is complete — so a correct implementation bolted to the wrong
stage simply never runs. Naming the phase is the fix; the algorithm was never the problem.

What is deliberately not here
-----------------------------
Weights. `kg_core.py` hardcodes twelve `kg_weights` and five weight tables in its
`__init__`. This module produces counts and centralities, and `SCORING.md` fits weights
against an eval set — a number chosen in a constructor cannot be wrong, because nothing
can disagree with it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np

__all__ = ["CitationGraph", "GraphMetrics", "build_graph"]

DAMPING = 0.85
"""Standard. Kept as a named constant because it belongs in the manifest: two corpora
scored with different damping are not comparable, and the number is invisible otherwise."""


@dataclass(frozen=True, slots=True)
class GraphMetrics:
    """Per-document graph properties, computed once over the finished corpus."""

    pagerank: dict[str, float]
    in_degree: dict[str, int]
    out_degree: dict[str, int]
    degree_centrality: dict[str, float]

    def of(self, doc_id: str) -> dict[str, Any]:
        """The row for one document, for the signals sidecar.

        ! Chunks inherit their document's values. That is a denormalisation and it is the
        right one — centrality is a property of the instrument, not of the paragraph — but
        it has to be understood as one, or somebody will later average `kg_pagerank` over
        chunks and read the result as a fact about documents.
        """
        return {
            "kg_pagerank": round(self.pagerank.get(doc_id, 0.0), 9),
            "kg_in_degree": self.in_degree.get(doc_id, 0),
            "kg_out_degree": self.out_degree.get(doc_id, 0),
            "kg_degree_centrality": round(self.degree_centrality.get(doc_id, 0.0), 9),
        }


@dataclass
class CitationGraph:
    """A directed graph of documents citing documents."""

    nodes: tuple[str, ...]
    src: np.ndarray
    dst: np.ndarray
    dangling: dict[str, int] = field(default_factory=dict)
    """Cited identifiers that are not in this corpus, and how often each was cited.

    ! Not nodes. A phantom node for an instrument the corpus does not hold would collect
    rank it can never be returned for, and — worse — would push that rank around the graph
    as though it were evidence. Recorded instead, because the share of citations that point
    outside the corpus is a real measurement of coverage: the honest answer to "what is
    missing from this collection" is largely written in what it cites.
    """

    ambiguous: dict[str, int] = field(default_factory=dict)
    """Identifiers held by more than one document. A corpus defect, reported rather than
    resolved — picking one arbitrarily would put a confident wrong edge in the graph."""

    @property
    def n(self) -> int:
        return len(self.nodes)

    @property
    def edge_count(self) -> int:
        return int(self.src.shape[0])

    def pagerank(
        self, *, damping: float = DAMPING, tol: float = 1e-10, max_iter: int = 200
    ) -> dict[str, float]:
        """Power iteration, with the dangling mass redistributed.

        ! The redistribution is the part that is usually wrong. A document that cites
        nothing — a constitution, a repealing instrument, anything at the root — is a sink,
        and without redistributing its share each iteration the total rank *leaks away*:
        the vector still sorts plausibly, so the bug survives review, but the values are no
        longer a probability distribution and are not comparable between corpora with
        different sink counts. Roughly a third of this corpus cites nothing.
        """
        n = self.n
        if n == 0:
            return {}
        if self.edge_count == 0:
            return dict.fromkeys(self.nodes, 1.0 / n)

        out_degree = np.bincount(self.src, minlength=n).astype(np.float64)
        share = np.where(out_degree > 0, 1.0 / np.maximum(out_degree, 1.0), 0.0)
        sinks = out_degree == 0

        rank = np.full(n, 1.0 / n, dtype=np.float64)
        for _ in range(max_iter):
            contribution = rank[self.src] * share[self.src]
            incoming = np.bincount(self.dst, weights=contribution, minlength=n)
            leaked = float(rank[sinks].sum())
            updated = (1.0 - damping) / n + damping * (incoming + leaked / n)
            if float(np.abs(updated - rank).sum()) < tol:
                rank = updated
                break
            rank = updated
        return {node: float(value) for node, value in zip(self.nodes, rank, strict=True)}

    def metrics(self, *, damping: float = DAMPING) -> GraphMetrics:
        n = self.n
        in_degree = np.bincount(self.dst, minlength=n) if self.edge_count else np.zeros(n, int)
        out_degree = np.bincount(self.src, minlength=n) if self.edge_count else np.zeros(n, int)
        # Normalised by the maximum possible degree, so the number means the same thing in
        # a corpus of 30,000 documents as in one of 300.
        divisor = max(n - 1, 1)
        return GraphMetrics(
            pagerank=self.pagerank(damping=damping),
            in_degree={node: int(in_degree[i]) for i, node in enumerate(self.nodes)},
            out_degree={node: int(out_degree[i]) for i, node in enumerate(self.nodes)},
            degree_centrality={
                node: float((in_degree[i] + out_degree[i]) / divisor)
                for i, node in enumerate(self.nodes)
            },
        )

    def report(self) -> dict[str, Any]:
        """What this graph says about the corpus that built it."""
        cited_out = sum(self.dangling.values())
        total = self.edge_count + cited_out
        return {
            "documents": self.n,
            "edges": self.edge_count,
            "dangling_citations": cited_out,
            "distinct_dangling_targets": len(self.dangling),
            "coverage": round(self.edge_count / total, 6) if total else 0.0,
            "ambiguous_identifiers": len(self.ambiguous),
            "damping": DAMPING,
        }


def build_graph(
    identifiers: Mapping[str, str] | Iterable[tuple[str, str]],
    citations: Iterable[tuple[str, str]],
) -> CitationGraph:
    """Assemble the graph from document identifiers and the citations found in bodies.

    `identifiers` maps `doc_id -> identifier` (`"UU 40/2007"`), which is what the profile's
    citation pattern produces for a document's own title. `citations` is
    `(citing doc_id, cited identifier)` — the output of `extract_references`, one pair per
    reference.

    Three things happen here and each is a decision:

    - **Edges are deduplicated.** A document citing one law in forty clauses is one edge.
      Counting it forty times would make in-degree a measure of how verbose a drafter was.
    - **Self-citations are dropped.** A regulation's own internal cross-references are
      `ReferenceKind.INTERNAL` and belong to the document, not to the network; an
      instrument that amends itself would otherwise rank as its own authority.
    - **Nodes are sorted.** Iteration order over a dict is insertion order, so an unsorted
      node list makes the pagerank vector depend on the order shards happened to be read.
      The values would be the same and the *column* would differ between runs.
    """
    mapping = dict(identifiers)
    by_identifier: dict[str, list[str]] = {}
    for doc_id, identifier in mapping.items():
        if identifier:
            by_identifier.setdefault(identifier, []).append(doc_id)

    nodes = tuple(sorted(mapping))
    index = {doc_id: i for i, doc_id in enumerate(nodes)}

    ambiguous = {
        identifier: len(docs) for identifier, docs in by_identifier.items() if len(docs) > 1
    }
    dangling: dict[str, int] = {}
    seen: set[tuple[int, int]] = set()

    for citing, target in citations:
        if citing not in index:
            continue
        targets = by_identifier.get(target)
        if not targets or target in ambiguous:
            dangling[target] = dangling.get(target, 0) + 1
            continue
        cited = targets[0]
        if cited == citing:
            continue
        seen.add((index[citing], index[cited]))

    ordered = sorted(seen)
    return CitationGraph(
        nodes=nodes,
        src=np.array([a for a, _ in ordered], dtype=np.int64),
        dst=np.array([b for _, b in ordered], dtype=np.int64),
        dangling=dangling,
        ambiguous=ambiguous,
    )
