"""Oracle mode: the ceiling, measured with no routing in the way.

The end-to-end test at the bottom is the point of the whole `evaluate` package: a labeled
set, two differently chunked corpora, one embedder, and two numbers that can be put in the
same table. It runs on `HashEmbedder`, so it measures the plumbing rather than a model —
which is exactly what a test should measure, and it needs no GPU.
"""

from __future__ import annotations

import numpy as np
import pytest

from embed import HashEmbedder, spec_for
from evaluate import (
    Candidate,
    LabeledQuery,
    OracleIndex,
    QuerySet,
    QueryType,
    Relevance,
    build_index,
    evaluate,
    sampling_note,
    search,
    top_k,
)
from spec import Registry

DIM = 32


@pytest.fixture(scope="module")
def profile():
    return Registry.load().get("id_regulation")


def index_of(rows: list[tuple[str, str, str]], *, seed: int = 0) -> OracleIndex:
    rng = np.random.default_rng(seed)
    return build_index(
        [(cid, doc, loc, rng.normal(size=DIM).astype(np.float32)) for cid, doc, loc in rows]
    )


def test_top_k_returns_the_nearest_rows_in_order() -> None:
    index = OracleIndex()
    vectors = np.eye(4, DIM, dtype=np.float32)
    index.add(["a", "b", "c", "d"], ["D"] * 4, [""] * 4, vectors)

    probe = (vectors[2] * 3 + vectors[0]).reshape(1, -1)
    ranked = top_k(probe, index, k=2)

    assert [i for i, _ in ranked[0]] == [2, 0]
    assert ranked[0][0][1] > ranked[0][1][1]


def test_blocked_scoring_matches_unblocked() -> None:
    """! The index streams because the score matrix is what does not fit at corpus scale.
    If blocking changed the answer, every number would depend on the block size."""
    import evaluate.oracle as oracle

    rng = np.random.default_rng(5)
    index = index_of([(f"c{i}", "D", "") for i in range(500)], seed=5)
    probe = rng.normal(size=(3, DIM)).astype(np.float32)

    whole = top_k(probe, index, k=10)
    original, oracle.BLOCK = oracle.BLOCK, 37
    try:
        blocked = top_k(probe, index, k=10)
    finally:
        oracle.BLOCK = original

    assert [[i for i, _ in row] for row in whole] == [[i for i, _ in row] for row in blocked]


def test_a_misaligned_block_is_refused() -> None:
    """! Ids and vectors that do not line up score one chunk's vector against another
    chunk's citation, and the result is merely a disappointing number."""
    index = OracleIndex()

    with pytest.raises(ValueError, match="misaligned block"):
        index.add(["a", "b"], ["D"], [""], np.zeros((2, DIM), dtype=np.float32))


def test_misaligned_query_vectors_are_refused(profile) -> None:
    queries = QuerySet(
        items=(
            LabeledQuery(
                qid="q1", query="a", relevant=(Relevance(doc="UU 36/2008", locator="Pasal 1"),)
            ),
        )
    )

    with pytest.raises(ValueError, match="misalignment"):
        search(queries, np.zeros((3, DIM), dtype=np.float32), index_of([("c", "D", "")]))


def test_an_empty_index_returns_nothing_rather_than_erroring() -> None:
    queries = QuerySet(
        items=(LabeledQuery(qid="n1", query="a", type=QueryType.NEGATIVE),),
    )

    assert search(queries, np.zeros((1, DIM), dtype=np.float32), OracleIndex()) == {"n1": []}


def test_a_sampled_run_says_so(profile) -> None:
    """! A ceiling measured on 2% of the corpus is still a ceiling — but it may only be
    compared with another run at the same sample size."""
    note = sampling_note(4_000_000, 80_000)

    assert note["sampled"] and note["share"] == pytest.approx(0.02)
    assert "never against a full-corpus number" in note["caveat"]
    assert sampling_note(1000, 1000) == {"sampled": False, "chunks": 1000}


# -- end to end ---------------------------------------------------------------------------


def test_two_chunkings_are_scored_against_one_labeled_set(profile) -> None:
    """! Everything the `evaluate` package exists for, in one path.

    The corpus is chunked twice — once at pasal, once at ayat — the labels name a document
    and a locator and never a chunk id, and both arms retrieve the answer. That the two
    numbers are comparable at all *is* the result; without the coverage rule the ayat arm
    would score zero against pasal labels and look catastrophically worse than it is.
    """
    embedder = HashEmbedder(spec_for(dim=DIM, id="oracle-test"))
    bodies = {
        "Pasal 17": "Tarif pajak penghasilan bagi wajib pajak orang pribadi ditetapkan",
        "Pasal 17 ayat (1)": "Tarif pajak penghasilan bagi wajib pajak orang pribadi",
        "Pasal 4": "Yang menjadi objek pajak adalah penghasilan",
        "Pasal 9": "Wajib pajak wajib menyelenggarakan pembukuan",
    }

    def arm(locators: list[str]) -> OracleIndex:
        rows = [
            (f"c{i}", "UU 36/2008", loc, np.asarray(embedder.embed_documents([bodies[loc]])[0]))
            for i, loc in enumerate(locators)
        ]
        return build_index(rows)

    queries = QuerySet(
        items=(
            LabeledQuery(
                qid="q1",
                query="Tarif pajak penghasilan bagi wajib pajak orang pribadi ditetapkan",
                type=QueryType.FACTUAL,
                relevant=(Relevance(doc="UU 36/2008", locator="Pasal 17 ayat (1)", grade=3),),
            ),
        ),
        version="v1",
    )
    probe = np.asarray(embedder.embed_queries([queries.items[0].query]))

    by_pasal = evaluate(
        queries,
        search(queries, probe, arm(["Pasal 17", "Pasal 4", "Pasal 9"]), k=3),
        profile=profile,
        variant="pasal",
    )
    by_ayat = evaluate(
        queries,
        search(queries, probe, arm(["Pasal 17 ayat (1)", "Pasal 4", "Pasal 9"]), k=3),
        profile=profile,
        variant="ayat",
    )

    assert by_pasal.recall[50] == 1.0, "a pasal chunk covers an ayat label"
    assert by_ayat.recall[50] == 1.0, "an exact-locator chunk covers it too"
    assert by_pasal.query_set_version == by_ayat.query_set_version


def test_a_wrong_document_is_not_rescued_by_a_matching_locator(profile) -> None:
    """The other half of the coverage rule: the locator is only checked once the
    instrument matches."""
    queries = QuerySet(
        items=(
            LabeledQuery(
                qid="q1",
                query="x",
                relevant=(Relevance(doc="UU 36/2008", locator="Pasal 17"),),
            ),
        )
    )
    results = {"q1": [Candidate("c1", "UU 28/2007", "Pasal 17")]}

    assert evaluate(queries, results, profile=profile, variant="v").recall[50] == 0.0
