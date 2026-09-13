"""The citation graph — the Phase C job that `06_ID_Legal` implemented and never ran.

`relationship_graph.py` computes pagerank and degree centrality correctly and is
instantiated only by its own unit test, which is why `kg_pagerank` is NULL on all 748,558
rows of the incumbent. The algorithm was never the problem; the phase was. These tests are
therefore mostly about *what the graph refuses to invent* — the part an implementation
bolted to the wrong stage never has to answer.
"""

from __future__ import annotations

import numpy as np
import pytest

from enrich import ReferenceKind, build_graph, extract_references
from spec import Registry

# A small, real-shaped network: a law cited by two regulations, one of which is cited back.
IDENTIFIERS = {
    "doc_uu40": "UU 40/2007",
    "doc_pp24": "PP 24/2018",
    "doc_perbup5": "Perbup 5/2020",
    "doc_orphan": "Pergub 7/2020",
}
CITATIONS = [
    ("doc_pp24", "UU 40/2007"),
    ("doc_perbup5", "UU 40/2007"),
    ("doc_perbup5", "PP 24/2018"),
]


@pytest.fixture
def graph():
    return build_graph(IDENTIFIERS, CITATIONS)


def test_pagerank_is_a_distribution_that_sums_to_one(graph) -> None:
    """! The dangling mass has to be redistributed each iteration or the total leaks away.
    The vector still sorts plausibly when it does, which is why the bug survives review —
    but the values stop being comparable between corpora with different sink counts, and
    roughly a third of this one cites nothing."""
    ranks = graph.pagerank()

    assert set(ranks) == set(IDENTIFIERS)
    assert sum(ranks.values()) == pytest.approx(1.0, abs=1e-9)


def test_the_most_cited_instrument_ranks_highest(graph) -> None:
    ranks = graph.pagerank()

    assert max(ranks, key=lambda k: ranks[k]) == "doc_uu40"
    assert ranks["doc_uu40"] > ranks["doc_perbup5"]


def test_a_document_nobody_cites_still_has_rank(graph) -> None:
    """The teleport term. Zero rank would mean "not in the corpus", which is a different
    claim from "in the corpus and uncited"."""
    assert graph.pagerank()["doc_orphan"] > 0


def test_a_citation_repeated_in_forty_clauses_is_one_edge() -> None:
    """! Otherwise in-degree measures how verbose the drafter was."""
    graph = build_graph(IDENTIFIERS, [("doc_pp24", "UU 40/2007")] * 40)

    assert graph.edge_count == 1
    assert graph.metrics().in_degree["doc_uu40"] == 1


def test_a_document_citing_itself_is_not_its_own_authority() -> None:
    graph = build_graph(IDENTIFIERS, [("doc_uu40", "UU 40/2007")])

    assert graph.edge_count == 0


def test_a_citation_outside_the_corpus_is_recorded_not_invented(graph) -> None:
    """! No phantom node. An instrument the corpus does not hold would collect rank it can
    never be returned for, and push that rank around the graph as though it were evidence.
    The share pointing outward is a real coverage measurement instead."""
    graph = build_graph(IDENTIFIERS, [*CITATIONS, ("doc_pp24", "UU 5/1960")])

    assert "UU 5/1960" not in graph.nodes
    assert graph.dangling["UU 5/1960"] == 1
    assert graph.report()["coverage"] == pytest.approx(3 / 4)


def test_an_ambiguous_identifier_produces_no_edge_rather_than_a_guess() -> None:
    """! Two documents claiming `UU 40/2007` is a corpus defect. Picking one arbitrarily
    puts a confident wrong edge in a graph whose entire value is that its edges are real."""
    duplicated = {**IDENTIFIERS, "doc_dupe": "UU 40/2007"}

    graph = build_graph(duplicated, CITATIONS)

    assert graph.ambiguous == {"UU 40/2007": 2}
    assert graph.metrics().in_degree["doc_uu40"] == 0
    assert graph.dangling["UU 40/2007"] == 2


def test_node_order_does_not_depend_on_shard_read_order() -> None:
    """! Iteration over a dict is insertion order. An unsorted node list makes the
    pagerank *column* differ between runs that produce identical values."""
    shuffled = dict(reversed(list(IDENTIFIERS.items())))

    a = build_graph(IDENTIFIERS, CITATIONS)
    b = build_graph(shuffled, CITATIONS)

    assert a.nodes == b.nodes
    assert np.array_equal(a.src, b.src) and np.array_equal(a.dst, b.dst)


def test_a_corpus_with_no_citations_still_produces_a_usable_column() -> None:
    """A graph with no edges is a flat distribution, not an error and not NULL."""
    ranks = build_graph(IDENTIFIERS, []).pagerank()

    assert list(ranks.values()) == pytest.approx([0.25] * 4)


def test_degree_centrality_means_the_same_thing_at_any_corpus_size(graph) -> None:
    metrics = graph.metrics()

    assert metrics.degree_centrality["doc_uu40"] == pytest.approx(2 / 3)
    assert all(0.0 <= v <= 1.0 for v in metrics.degree_centrality.values())


def test_chunks_inherit_their_documents_row(graph) -> None:
    """The denormalisation, made explicit. Centrality is a property of the instrument, not
    of the paragraph."""
    row = graph.metrics().of("doc_uu40")

    assert set(row) == {
        "kg_pagerank",
        "kg_in_degree",
        "kg_out_degree",
        "kg_degree_centrality",
    }
    assert row["kg_in_degree"] == 2 and row["kg_out_degree"] == 0


def test_a_document_absent_from_the_graph_reads_as_zero_not_as_an_error(graph) -> None:
    assert graph.metrics().of("doc_never_seen")["kg_pagerank"] == 0.0


def test_the_graph_builds_from_what_the_extractor_actually_produces() -> None:
    """! The end-to-end shape, and the reason this closes `ABSORPTION.md` §6: the
    references `entities.py` reads out of bodies are exactly this function's input, so
    nothing has to be re-derived in between."""
    profile = Registry.load().get("id_regulation")
    bodies = {
        "doc_pp24": "Peraturan ini dibentuk berdasarkan Undang-Undang Nomor 40 Tahun 2007.",
        "doc_perbup5": (
            "Mengingat Undang-Undang Nomor 40 Tahun 2007 dan Peraturan Pemerintah "
            "Nomor 24 Tahun 2018, serta Pasal 5 ayat (2)."
        ),
    }
    citations = [
        (doc_id, ref.identifier)
        for doc_id, body in bodies.items()
        for ref in extract_references(profile, body)
        if ref.kind is ReferenceKind.REGULATION and ref.identifier
    ]

    graph = build_graph(IDENTIFIERS, citations)

    assert graph.edge_count == 3
    assert graph.metrics().in_degree["doc_uu40"] == 2
    assert not graph.dangling
