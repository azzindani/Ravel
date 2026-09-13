"""Phase C: centroids, assignment, anchors, and the checks that make routing reviewable.

The theme of this file is that clustering fails *quietly*. Every test here asserts
something that would otherwise be discovered as "recall is a bit lower than we expected"
six months after the corpus was published.
"""

from __future__ import annotations

import numpy as np
import pytest

from cluster import (
    Clustering,
    ClusteringError,
    DomainAnchor,
    Metric,
    calibrate,
    check_balance,
    check_complete,
    corpus_centroid,
    drift,
    fit,
    metric_for,
    sample_indices,
    stats_of,
    suggest_k,
)

DIM = 16


def unit(matrix: np.ndarray) -> np.ndarray:
    matrix = np.asarray(matrix, dtype=np.float32)
    return matrix / np.linalg.norm(matrix, axis=1, keepdims=True)


def blobs(spreads: tuple[float, ...], *, per: int = 200, seed: int = 3) -> list[np.ndarray]:
    """Unit-norm clusters with deliberately unequal spreads.

    Equal spreads hide the Euclidean/cosine disagreement, because equally tight clusters
    produce equally long centroids. Real corpora are not equally tight.
    """
    rng = np.random.default_rng(seed)
    out = []
    for spread in spreads:
        centre = rng.normal(size=DIM)
        centre /= np.linalg.norm(centre)
        out.append(unit(centre + spread * rng.normal(size=(per, DIM))))
    return out


@pytest.fixture
def corpus() -> np.ndarray:
    return np.vstack(blobs((0.02, 0.6, 0.25))).astype(np.float32)


# -- the metric is part of the recipe -------------------------------------------------


def test_euclidean_and_cosine_disagree_about_where_rows_belong(corpus: np.ndarray) -> None:
    """! The reason the metric travels in the manifest.

    Fit by Euclidean, route by cosine — which is what happens when Ravel uses the library
    default and Vera scores by inner product — and the two disagree about a measurable
    share of the corpus. Those rows are in the database, correct, and filed in a cluster
    the engine will not probe for them. Nothing errors and no count is wrong.
    """
    fitted = fit(corpus, 3, metric=Metric.EUCLIDEAN, seed=11)
    by_euclid = fitted.assign(corpus).cluster_ids

    # The same centroids as a cosine router sees them: direction only, length discarded.
    as_cosine = Clustering(
        centroids=unit(fitted.centroids),
        metric=Metric.COSINE,
        seed=11,
        sample_size=0,
        iterations=fitted.iterations,
        converged=True,
        reseeded=0,
    )
    by_cosine = as_cosine.assign(corpus).cluster_ids

    assert float((by_euclid != by_cosine).mean()) > 0.01


def test_cosine_centroids_must_be_unit_norm(corpus: np.ndarray) -> None:
    """! Handing Euclidean means to a cosine clustering is the same defect one level down:
    `argmax(x @ C.T)` is cosine only when the rows of C have length 1."""
    euclid = fit(corpus, 3, metric=Metric.EUCLIDEAN, seed=11)

    with pytest.raises(ClusteringError, match="unit-norm"):
        Clustering(
            centroids=euclid.centroids,
            metric=Metric.COSINE,
            seed=1,
            sample_size=0,
            iterations=1,
            converged=True,
            reseeded=0,
        )


def test_metric_follows_the_embedder_rather_than_a_flag() -> None:
    assert metric_for(normalize=True) is Metric.COSINE
    assert metric_for(normalize=False) is Metric.EUCLIDEAN


def test_the_fingerprint_separates_two_identical_centroid_sets(corpus: np.ndarray) -> None:
    """Same centroids, different metric, different routing structure."""
    cosine = fit(corpus, 3, seed=5)
    euclid = Clustering(
        centroids=cosine.centroids,
        metric=Metric.EUCLIDEAN,
        seed=5,
        sample_size=0,
        iterations=1,
        converged=True,
        reseeded=0,
    )

    assert cosine.sha256() != euclid.sha256()


# -- reproducibility -------------------------------------------------------------------


def test_the_same_seed_produces_the_same_centroids(corpus: np.ndarray) -> None:
    """! A routing structure that cannot be rebuilt cannot be compared against its
    successor, which makes "did the re-cluster help?" unanswerable."""
    assert fit(corpus, 4, seed=7).sha256() == fit(corpus, 4, seed=7).sha256()


def test_the_sample_is_seeded_sorted_and_in_range() -> None:
    """! Sorted because the corpus arrives in source order — grouped by issuing body and
    year — so "the first N rows" is a handful of institutions, not a sample."""
    first = sample_indices(10_000, 500, seed=1)
    again = sample_indices(10_000, 500, seed=1)

    assert np.array_equal(first, again)
    assert np.array_equal(first, np.sort(first))
    assert len(set(first.tolist())) == 500
    assert first.min() >= 0 and first.max() < 10_000


def test_a_sample_larger_than_the_corpus_takes_everything() -> None:
    assert np.array_equal(sample_indices(50, 500, seed=1), np.arange(50))


def test_the_recipe_records_what_the_fit_actually_used(corpus: np.ndarray) -> None:
    """`CLUSTERING.md` §2 requires sample size and seed in the manifest. Recorded from the
    fit rather than echoed from the request: asking for 5,000 of 600 rows used 600."""
    recipe = fit(corpus, 3, seed=9, sample_size=5_000).recipe()

    assert recipe["seed"] == 9
    assert recipe["sample_size"] == 0, "no sample was drawn; the whole set was used"
    assert recipe["algo"] == "kmeans/cosine"
    assert len(recipe["centroids_sha256"]) == 64


def test_a_drawn_sample_is_recorded_by_its_real_size(corpus: np.ndarray) -> None:
    assert fit(corpus, 3, seed=9, sample_size=120).recipe()["sample_size"] == 120


# -- the structure itself ---------------------------------------------------------------


def test_separable_blobs_come_back_as_separate_clusters() -> None:
    """The floor: on data with an obvious answer, produce the obvious answer."""
    parts = blobs((0.05, 0.05, 0.05), per=150, seed=17)
    corpus = np.vstack(parts).astype(np.float32)
    assignment = fit(corpus, 3, seed=2).assign(corpus)

    for i in range(3):
        block = assignment.cluster_ids[i * 150 : (i + 1) * 150]
        majority = np.bincount(block).max() / len(block)
        assert majority > 0.95, f"blob {i} was split across clusters"


def test_more_clusters_than_directions_is_reported_not_repaired_away() -> None:
    """! Re-seeding repairs transient emptiness; it cannot manufacture distinct
    directions. Ask for 12 clusters over 3 distinct points and nine come back empty
    however often they are re-seeded — so the guarantee has to live in the check, not in
    the repair (`CLAUDE.md` §15). A fit that quietly returned twelve would be the worse
    outcome, because the manifest would then claim a routing structure that does not
    exist.
    """
    repeated = np.repeat(unit(np.random.default_rng(4).normal(size=(3, DIM))), 40, axis=0)
    corpus = repeated.astype(np.float32)

    stats = stats_of(fit(corpus, 12, seed=4).assign(corpus), 12)
    ok, detail = check_balance(stats, max_share=1.0)

    assert stats.empty == 9
    assert not ok and "empty" in detail


def test_a_zero_vector_does_not_poison_the_centroids() -> None:
    """! A chunk that embeds to all zeros is a real thing — an empty body, or a backend
    that returned zeros on failure. It has no direction, and under cosine a zero centroid
    scores 0 against every query, so `argmax` hands its rows to whichever cluster happens
    to sort first. Re-seeding must also refuse to draw *from* those rows: they are
    maximally distant from everything, so they sort to the front of the repair queue.
    """
    corpus = np.vstack([np.zeros((5, DIM), dtype=np.float32), *blobs((0.1,), per=50)])

    clustering = fit(corpus.astype(np.float32), 3, seed=1)

    assert np.isfinite(clustering.centroids).all()
    assert np.allclose(np.linalg.norm(clustering.centroids, axis=1), 1.0, atol=1e-3)


def test_distances_are_never_negative(corpus: np.ndarray) -> None:
    """! `1 - inner` goes slightly negative in float32 when a row sits on its own
    centroid. Tiny, and it averages into the drift baseline, where it makes the next run
    look like an improvement."""
    clustering = fit(corpus, 3, seed=1)

    assert clustering.assign(corpus).distances.min() >= 0.0


def test_assigning_a_different_width_is_refused(corpus: np.ndarray) -> None:
    """Two vector spaces are not one space with a shape mismatch (`CLAUDE.md` §7.3)."""
    clustering = fit(corpus, 3, seed=1)

    with pytest.raises(ClusteringError, match="different spaces"):
        clustering.assign(np.zeros((10, DIM * 2), dtype=np.float32))


def test_more_clusters_than_rows_is_refused(corpus: np.ndarray) -> None:
    with pytest.raises(ClusteringError, match="no assignment"):
        fit(corpus[:5], 10)


def test_streamed_assignment_matches_whole_corpus_assignment(corpus: np.ndarray) -> None:
    """! The full-corpus pass is streamed because 100M x 4096 does not fit in RAM. If
    batching changed an assignment, the bundle would depend on batch size."""
    clustering = fit(corpus, 4, seed=6)
    whole = clustering.assign(corpus).cluster_ids
    streamed = np.concatenate(
        [a.cluster_ids for a in clustering.assign_stream(np.array_split(corpus, 7))]
    )

    assert np.array_equal(whole, streamed)


def test_suggested_k_targets_ten_thousand_rows_a_cluster() -> None:
    assert suggest_k(4_000_000) == 400
    assert suggest_k(3) == 1, "never zero clusters"


# -- the pre-publish checks --------------------------------------------------------------


def test_a_dominant_cluster_fails_the_balance_check() -> None:
    """! `CLUSTERING.md` §3: a cluster holding 40% of the corpus destroys the memory
    guarantee Vera's sequential loading depends on. Count alone would report this as a
    healthy 4-cluster structure."""
    from cluster import Assignment

    lopsided = stats_of(
        Assignment(
            cluster_ids=np.array([0] * 400 + [1, 2, 3], dtype=np.int32),
            distances=np.zeros(403, dtype=np.float32),
        ),
        4,
    )

    ok, detail = check_balance(lopsided, max_share=0.10)

    assert not ok
    assert "largest cluster" in detail


def test_an_empty_cluster_fails_the_balance_check() -> None:
    from cluster import Assignment

    stats = stats_of(
        Assignment(cluster_ids=np.array([0, 0, 1, 1], dtype=np.int32), distances=np.zeros(4)),
        4,
    )

    ok, detail = check_balance(stats)

    assert not ok
    assert "empty" in detail


def test_a_balanced_clustering_passes(corpus: np.ndarray) -> None:
    clustering = fit(corpus, 3, seed=2)
    stats = stats_of(clustering.assign(corpus), clustering.k)

    ok, detail = check_balance(stats, max_share=0.60)

    assert ok, detail


def test_unassigned_chunks_are_counted_not_assumed_away() -> None:
    """! `cluster_id` is nullable, so an unassigned chunk loads without complaint and is
    never returned by a routed query. In the corpus and unreachable."""
    ok, detail = check_complete(748_000, 748_558)

    assert not ok
    assert "558" in detail
    assert check_complete(100, 100)[0]


def test_drift_reports_movement_against_a_baseline(corpus: np.ndarray) -> None:
    """`CLUSTERING.md` §6: assigning new documents to stale centroids degrades routing
    gradually and invisibly. The number has to be produced on every incremental run or
    the decision to re-cluster is a hunch."""
    clustering = fit(corpus, 3, seed=8)
    baseline = stats_of(clustering.assign(corpus), clustering.k)

    rng = np.random.default_rng(99)
    newcomers = unit(rng.normal(size=(200, DIM))).astype(np.float32)
    report = drift(clustering, clustering.assign(newcomers), baseline)

    assert report["mean_distance_delta"] > 0
    assert report["generation"] == clustering.generation


def test_an_incremental_assign_does_not_bump_the_generation(corpus: np.ndarray) -> None:
    """! Vera's atomic swap pins a generation. Bumping it for an assign would invalidate a
    routing structure that did not change."""
    clustering = fit(corpus, 3, seed=8, generation=4)
    clustering.assign(corpus)

    assert clustering.generation == 4


# -- domain anchors ------------------------------------------------------------------------


def test_an_anchor_pointing_away_from_the_corpus_is_reported(corpus: np.ndarray) -> None:
    """! The check that catches a plausible description written for a corpus nobody built.
    Its failure mode — queries the corpus could answer falling under the threshold — is
    indistinguishable from poor coverage."""
    rng = np.random.default_rng(23)
    elsewhere = unit(rng.normal(size=(1, DIM))).astype(np.float32)
    anchor = DomainAnchor(
        domain_id="id_legal",
        vectors=-corpus_centroid(corpus)[None, :],
        description="Indonesian regulations",
        centroid=corpus_centroid(corpus),
    )

    ok, detail = anchor.describes_corpus(min_cosine=0.3)

    assert not ok
    assert "different directions" in detail
    assert elsewhere.shape == (1, DIM)


def test_an_anchor_without_a_centroid_has_no_opinion(corpus: np.ndarray) -> None:
    """Fails open. An absent centroid is not evidence of a bad anchor."""
    anchor = DomainAnchor(
        domain_id="d", vectors=corpus[:1], description="something", centroid=None
    )

    assert anchor.describes_corpus()[0]


def test_an_anchor_must_carry_the_text_it_was_built_from(corpus: np.ndarray) -> None:
    """! A 4096-float vector cannot be argued with; a paragraph can. Without the
    description the routing decision is unreviewable."""
    with pytest.raises(ValueError, match="description"):
        DomainAnchor(domain_id="d", vectors=corpus[:1], description="   ")


def test_extra_anchors_can_only_widen_a_domain(corpus: np.ndarray) -> None:
    """Matched by max, which is what makes adding an anchor safe and removing one not."""
    one = DomainAnchor(domain_id="d", vectors=corpus[:1], description="a")
    two = DomainAnchor(domain_id="d", vectors=corpus[:2], description="a")

    assert (two.similarity(corpus) >= one.similarity(corpus) - 1e-6).all()


# -- threshold calibration -------------------------------------------------------------------


def test_calibration_holds_in_domain_recall_and_reports_what_it_costs() -> None:
    """! The asymmetry. A false reject is zero recall on an answerable query and the user
    sees an authoritative-looking empty result; a false accept is one wasted probe."""
    parts = blobs((0.05, 0.05), per=300, seed=31)
    anchor = DomainAnchor(domain_id="d", vectors=parts[0][:1], description="in-domain")

    cal = calibrate(anchor.similarity(parts[0]), anchor.similarity(parts[1]))

    assert cal.separable
    assert cal.false_reject <= 0.08, "the target was 95% recall"
    assert cal.false_accept < 0.05
    assert cal.margin > 0


def test_overlapping_distributions_are_marked_unseparable() -> None:
    """! The outcome most worth knowing, and the one a bare number would hide. An anchor
    that admits most out-of-domain queries is not routing — and a threshold returned
    without that verdict is a guess that ends up in a config file."""
    rng = np.random.default_rng(41)
    same = rng.normal(loc=0.5, scale=0.1, size=400)

    cal = calibrate(same, rng.normal(loc=0.5, scale=0.1, size=400))

    assert not cal.separable
    assert cal.false_accept > 0.5
    assert cal.margin < 0


def test_no_in_domain_queries_yields_no_threshold_at_all() -> None:
    """! Not 0.5. An anchor calibrated against nothing has not been calibrated, and a
    plausible default is a number somebody later mistakes for a measurement."""
    cal = calibrate(np.array([]), np.array([0.1, 0.2]))

    assert cal.threshold is None
    assert not cal.separable


def test_the_summary_carries_both_distributions_not_just_the_verdict() -> None:
    """Vera's threshold is a human decision. The percentiles are the evidence for it."""
    rng = np.random.default_rng(7)
    cal = calibrate(rng.uniform(0.6, 0.9, 200), rng.uniform(0.0, 0.3, 200))
    summary = cal.summary()

    assert summary["in_domain"]["p05"] > summary["out_domain"]["p95"]
    assert summary["n_in"] == 200 and summary["n_out"] == 200


def test_an_impossible_target_recall_is_refused() -> None:
    with pytest.raises(ValueError, match="target_recall"):
        calibrate(np.array([0.5]), np.array([0.1]), target_recall=1.5)
