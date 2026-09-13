"""Phase C: k-means over the whole corpus, and the structure Vera routes with.

Why this stage is quietly critical
----------------------------------
Vera holds the domain anchors and the ~10K centroids hot in memory and uses them to
decide **where not to look** (`CLUSTERING.md` §1). Bad clustering therefore does not
raise: it produces silent zero-recall. The chunk is in the corpus, the query is correct,
the engine is working, and the right cluster was simply never probed. Nothing in the
result set says so — which is why almost everything in this module is a check rather than
an algorithm.

The metric is part of the recipe
--------------------------------
! The load-bearing decision here, and the one most likely to be got wrong quietly.

Vera routes by **cosine** against the centroids. If Ravel fits and assigns by Euclidean
distance the two disagree about which cluster a chunk belongs to, because for unit-norm
`x`

    ||x - c||^2 = 1 - 2(x.c) + ||c||^2

and the `||c||^2` term does not vanish: a centroid sitting nearer the origin — a broad,
internally diverse cluster, exactly the kind that swallows rows — wins rows it is not the
nearest *direction* to. Ravel writes `cluster_id`, Vera probes by cosine, and a fraction
of every cluster is filed where the engine will not look for it. No error, no count out of
place, just recall lower than it should be for reasons no log records.

So the metric is derived from the embedder spec (`normalize=True` means the space is
directional, so cosine) and written into the manifest. Spherical k-means re-normalises
centroids each iteration, which makes assignment *identical* to the operation Vera
performs at query time.

Memory
------
It cannot stream (`CLUSTERING.md` §2) — k-means needs the whole vector set — but it does
not need it *at once*. Centroids are fitted on a deterministic sample; assignment streams
the full corpus in batches. At 100M x 4096 fp16 the vectors are ~0.8TB and the sample is a
few million rows, which is the difference between a job that runs and one that does not.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import numpy as np

__all__ = [
    "Assignment",
    "ClusterStats",
    "Clustering",
    "ClusteringError",
    "Metric",
    "ROWS_PER_CLUSTER",
    "drift",
    "fit",
    "metric_for",
    "sample_indices",
    "stats_of",
    "suggest_k",
]

#: Rows per matmul block during assignment. The temporary is `batch x k` floats, so at
#: k=10,000 this is a 320 MB intermediate — large enough to keep BLAS busy, small enough
#: that a box able to hold the centroids can hold this too.
BATCH = 8192

#: Target rows per cluster (`CLUSTERING.md` §3): Vera's leaf scan assumes ~10K.
ROWS_PER_CLUSTER = 10_000


class ClusteringError(ValueError):
    """A clustering that would route badly, caught before it is published."""


class Metric(StrEnum):
    COSINE = "cosine"
    """Spherical k-means: centroids re-normalised each iteration, assignment by inner
    product. Correct whenever the embedder normalises — which is what makes it the same
    operation Vera performs."""

    EUCLIDEAN = "euclidean"
    """Lloyd's. Correct for un-normalised spaces, and wrong — silently — for normalised
    ones. See the module docstring."""


def metric_for(*, normalize: bool) -> Metric:
    """The metric implied by the embedder spec.

    Derived rather than configured, because the one way to get this wrong is to let it be
    a free choice made in a different file from the one that set `normalize`.
    """
    return Metric.COSINE if normalize else Metric.EUCLIDEAN


def suggest_k(rows: int, *, rows_per_cluster: int = ROWS_PER_CLUSTER) -> int:
    """`k ~= n / 10000`, floored at 1 (`CLUSTERING.md` §3).

    A suggestion, not a default anything reads automatically: `k` and Vera's
    `clusters_probed` are two halves of one dial and have to be tuned together against the
    eval set, never separately.
    """
    return max(1, round(rows / rows_per_cluster))


def sample_indices(rows: int, sample_size: int, *, seed: int) -> np.ndarray:
    """Which rows the centroids are fitted on — reproducibly.

    ! Seeded and sorted, never "the first N shards". The corpus arrives in source order,
    which for this corpus means grouped by issuing body and year, so the first two million
    rows are a handful of institutions. Centroids fitted on that describe a corpus nobody
    has, and every routing decision afterwards inherits the skew.
    """
    if sample_size <= 0 or sample_size >= rows:
        return np.arange(rows, dtype=np.int64)
    rng = np.random.default_rng(seed)
    return np.sort(rng.choice(rows, size=sample_size, replace=False))


def _as_matrix(vectors: np.ndarray) -> np.ndarray:
    matrix = np.asarray(vectors, dtype=np.float32)
    if matrix.ndim != 2:
        raise ClusteringError(f"expected a 2-D (rows, dim) array, got shape {matrix.shape}")
    if matrix.shape[0] == 0:
        raise ClusteringError("cannot cluster an empty vector set")
    return matrix


def _normalise(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    # A zero vector has no direction. Leave it at the origin rather than dividing by zero
    # and producing NaN, which would poison every centroid it ever touched.
    np.maximum(norms, 1e-12, out=norms)
    unit: np.ndarray = matrix / norms
    return unit


def _closest(
    vectors: np.ndarray, centroids: np.ndarray, metric: Metric
) -> tuple[np.ndarray, np.ndarray]:
    """Nearest centroid per row and the distance to it, blocked to bound the temporary."""
    n = vectors.shape[0]
    ids = np.empty(n, dtype=np.int32)
    dist = np.empty(n, dtype=np.float32)
    centroid_sq = (centroids.astype(np.float32) ** 2).sum(axis=1)

    for start in range(0, n, BATCH):
        block = vectors[start : start + BATCH]
        rows = np.arange(block.shape[0])
        inner = block @ centroids.T
        if metric is Metric.COSINE:
            best = inner.argmax(axis=1)
            ids[start : start + block.shape[0]] = best
            # ! Clamped at zero. An inner product of a unit vector with itself comes back
            # as 1.0000001 in float32, so `1 - inner` is a *negative distance* — which
            # then averages into a drift baseline and makes a later run look like an
            # improvement. Tiny, and it propagates into a number someone reads.
            dist[start : start + block.shape[0]] = np.maximum(1.0 - inner[rows, best], 0.0)
        else:
            partial = centroid_sq[None, :] - 2.0 * inner
            best = partial.argmin(axis=1)
            squared = partial[rows, best] + (block**2).sum(axis=1)
            ids[start : start + block.shape[0]] = best
            dist[start : start + block.shape[0]] = np.sqrt(np.maximum(squared, 0.0))
    return ids, dist


def _kmeans_plus_plus(vectors: np.ndarray, k: int, metric: Metric, rng: Any) -> np.ndarray:
    """D^2 seeding. Uniform init leaves dead centroids at this k; ++ costs one pass."""
    n = vectors.shape[0]
    centroids = np.empty((k, vectors.shape[1]), dtype=np.float32)
    centroids[0] = vectors[rng.integers(n)]

    closest = _closest(vectors, centroids[:1], metric)[1].astype(np.float64) ** 2
    for i in range(1, k):
        total = float(closest.sum())
        if total <= 0.0:
            # Every remaining point coincides with a chosen centre, so there is no D^2
            # mass to sample against. Uniform, rather than dividing by zero.
            centroids[i] = vectors[rng.integers(n)]
        else:
            centroids[i] = vectors[rng.choice(n, p=closest / total)]
        fresh = _closest(vectors, centroids[i : i + 1], metric)[1].astype(np.float64) ** 2
        np.minimum(closest, fresh, out=closest)
    return centroids


def _recentre(
    vectors: np.ndarray, ids: np.ndarray, dist: np.ndarray, k: int, metric: Metric
) -> tuple[np.ndarray, int]:
    """Mean of each cluster's members, with directionless clusters re-seeded.

    ! An empty cluster is not harmless. Vera probes a fixed number of clusters per query,
    so an empty one is a probe slot that can never return a row: the routing budget
    shrinks silently, and only for the queries unlucky enough to land near it. Re-seeding
    from the worst-fitted point is the standard repair and it also does useful work — the
    row furthest from its centroid is by construction the one the current centroids
    describe worst.

    ! Re-seeding repairs *transient* emptiness during iteration. It cannot manufacture
    distinct directions: ask for 12 clusters over 3 distinct points and nine of them come
    back empty however often they are re-seeded. That is why `check_balance` exists and
    why this function does not raise — the guarantee belongs to the check, not to the
    repair (`CLAUDE.md` §15).

    A cluster whose members sum to the origin is treated as empty for the same reason. It
    happens: a chunk that embeds to all zeros — an empty body, or a backend that returns
    zeros on failure — has no direction, and a zero centroid under cosine scores 0 against
    every query, so `argmax` hands its rows to whichever cluster sorts first.
    """
    centroids = np.zeros((k, vectors.shape[1]), dtype=np.float32)
    counts = np.bincount(ids, minlength=k)
    np.add.at(centroids, ids, vectors)

    directionless = np.linalg.norm(centroids, axis=1) < 1e-6
    needs_seed = np.flatnonzero((counts == 0) | directionless)

    reseeded = 0
    order = np.argsort(-dist)  # worst-fitted first
    # ! Never re-seed from a zero row. Under cosine a zero vector is maximally distant
    # from everything, so it sorts to the front of `order` — the repair would hand the
    # empty cluster exactly the row that cannot give it a direction, and the next
    # iteration would re-seed it again from the same place.
    usable = order[np.linalg.norm(vectors[order], axis=1) > 1e-6]
    if usable.shape[0]:
        order = usable
    for cluster in needs_seed:
        centroids[cluster] = vectors[order[reseeded % order.shape[0]]]
        counts[cluster] = 1
        reseeded += 1

    centroids /= counts[:, None].astype(np.float32)
    if metric is Metric.COSINE:
        centroids = _normalise(centroids)
    return centroids, reseeded


@dataclass(frozen=True, slots=True)
class Assignment:
    """Which cluster each row belongs to, and how well it fits."""

    cluster_ids: np.ndarray
    distances: np.ndarray

    def __len__(self) -> int:
        return int(self.cluster_ids.shape[0])


@dataclass(frozen=True, slots=True)
class ClusterStats:
    """The distribution — the half of clustering quality that is checkable.

    Count alone says nothing. k=400 with one cluster holding 40% of the corpus is a worse
    routing structure than k=100 balanced, and both report "clustered successfully".
    """

    k: int
    rows: int
    sizes: tuple[int, ...]
    mean_distance: float
    max_distance: float

    @property
    def empty(self) -> int:
        return sum(1 for size in self.sizes if size == 0)

    @property
    def largest(self) -> int:
        return max(self.sizes, default=0)

    @property
    def largest_share(self) -> float:
        return self.largest / self.rows if self.rows else 0.0

    @property
    def median(self) -> int:
        return int(np.median(self.sizes)) if self.sizes else 0

    def oversized(self, ceiling: int) -> tuple[int, ...]:
        return tuple(i for i, size in enumerate(self.sizes) if size > ceiling)

    def summary(self) -> dict[str, Any]:
        return {
            "k": self.k,
            "rows": self.rows,
            "empty": self.empty,
            "median": self.median,
            "largest": self.largest,
            "largest_share": round(self.largest_share, 6),
            "mean_distance": round(self.mean_distance, 6),
        }


def stats_of(assignment: Assignment, k: int) -> ClusterStats:
    sizes = np.bincount(assignment.cluster_ids, minlength=k)
    return ClusterStats(
        k=k,
        rows=len(assignment),
        sizes=tuple(int(s) for s in sizes),
        mean_distance=float(assignment.distances.mean()) if len(assignment) else 0.0,
        max_distance=float(assignment.distances.max()) if len(assignment) else 0.0,
    )


def check_balance(
    stats: ClusterStats, *, max_share: float = 0.10, ceiling: int = 0
) -> tuple[bool, str]:
    """The pre-publish validation of `CLUSTERING.md` §5: no empties, nothing oversized.

    ! Balance matters as much as count. A cluster holding 40% of the corpus destroys the
    memory guarantee Vera's sequential loading depends on — the leaf scan is sized for
    ~10K rows and a probe into that cluster reads four million. The engine does not
    degrade gracefully here; it runs out of the budget it was designed around.
    """
    if stats.rows == 0:
        return False, "no rows assigned"

    problems: list[str] = []
    if stats.empty:
        problems.append(
            f"{stats.empty} of {stats.k} clusters are empty — each is a probe slot that "
            f"can never return a row"
        )
    if stats.largest_share > max_share:
        problems.append(
            f"the largest cluster holds {stats.largest:,} rows ({stats.largest_share:.1%} "
            f"of the corpus, ceiling {max_share:.0%}); a probe into it reads far more than "
            f"the leaf scan is sized for"
        )
    limit = ceiling or ROWS_PER_CLUSTER * 4
    over = stats.oversized(limit)
    if over:
        problems.append(
            f"{len(over)} clusters exceed {limit:,} rows (largest {stats.largest:,}) — "
            f"split them before publishing"
        )
    if problems:
        return False, "; ".join(problems)
    return True, (
        f"{stats.k} clusters over {stats.rows:,} rows · median {stats.median:,} · "
        f"largest {stats.largest:,} ({stats.largest_share:.1%})"
    )


def check_complete(assigned: int, expected: int) -> tuple[bool, str]:
    """Every chunk assigned, counted rather than assumed.

    An unassigned chunk is not an error anywhere downstream: `cluster_id` is nullable, the
    row loads, and Vera's routed search simply never reaches it. It is in the corpus and
    it is unreachable, which is the worst of the three possible states.
    """
    if assigned == expected:
        return True, f"all {expected:,} chunks assigned"
    missing = expected - assigned
    return False, (
        f"{missing:,} of {expected:,} chunks carry no cluster_id — they would load fine "
        f"and never be returned by a routed query"
    )


@dataclass(frozen=True, slots=True)
class Clustering:
    """Fitted centroids, plus everything needed to reproduce them.

    The provenance fields are not decoration. `CLUSTERING.md` §2 requires the sample size
    and seed in the manifest for the same reason the embedder spec exists: a routing
    structure whose recipe is not written down cannot be rebuilt, extended, or compared
    against its successor.
    """

    centroids: np.ndarray
    metric: Metric
    seed: int
    sample_size: int
    iterations: int
    converged: bool
    reseeded: int
    generation: int = 1
    """Vera's atomic re-cluster swap pins a generation (`CLUSTERING.md` §5). Bumped by a
    re-cluster, never by an incremental assign — assigning new rows to existing centroids
    is the *same* generation by definition, and saying otherwise would invalidate a
    routing structure that did not change."""

    def __post_init__(self) -> None:
        if self.centroids.ndim != 2 or self.centroids.shape[0] == 0:
            raise ClusteringError(
                f"centroids must be a non-empty (k, dim) array, got shape "
                f"{self.centroids.shape}"
            )
        if self.metric is Metric.COSINE:
            norms = np.linalg.norm(self.centroids, axis=1)
            if not np.allclose(norms, 1.0, atol=1e-3):
                # ! Assignment under COSINE is `argmax(x @ C.T)`, which is cosine only
                # when the rows of C are unit-norm. Non-unit centroids make it argmax of
                # an inner product scaled by centroid length, so a long centroid wins
                # rows it is not the nearest direction to — the same disagreement this
                # module exists to prevent, one level down. Refused rather than silently
                # normalised: a caller who passes Euclidean means here has made a real
                # mistake about which space they are in, and normalising for them would
                # hide it.
                raise ClusteringError(
                    f"cosine centroids must be unit-norm; norms range "
                    f"[{norms.min():.4f}, {norms.max():.4f}]. Means of vectors are not "
                    f"unit-norm — normalise them, or declare Metric.EUCLIDEAN."
                )

    @property
    def k(self) -> int:
        return int(self.centroids.shape[0])

    @property
    def dim(self) -> int:
        return int(self.centroids.shape[1])

    def assign(self, vectors: np.ndarray) -> Assignment:
        """Assign rows to the nearest centroid, in the metric that fitted them."""
        matrix = _as_matrix(vectors)
        if matrix.shape[1] != self.dim:
            raise ClusteringError(
                f"vectors are {matrix.shape[1]}-dimensional but the centroids are "
                f"{self.dim}-dimensional — these are different spaces (CLAUDE.md §7.3)"
            )
        if self.metric is Metric.COSINE:
            matrix = _normalise(matrix)
        ids, dist = _closest(matrix, self.centroids, self.metric)
        return Assignment(cluster_ids=ids, distances=dist)

    def assign_stream(self, batches: Iterable[np.ndarray]) -> Iterator[Assignment]:
        """The full-corpus pass. Bounded by one batch, never by the corpus."""
        for batch in batches:
            yield self.assign(batch)

    def sha256(self) -> str:
        """Identity of the centroid set, for the manifest.

        Over the bytes of the array *and* the metric: the same centroids assigned by a
        different metric are a different routing structure.
        """
        digest = hashlib.sha256(np.ascontiguousarray(self.centroids, dtype=np.float32))
        digest.update(self.metric.value.encode())
        return digest.hexdigest()

    def recipe(self) -> dict[str, Any]:
        """The `clustering` block of the bundle manifest (`BUNDLE.md` §3)."""
        return {
            "algo": f"kmeans/{self.metric.value}",
            "k": self.k,
            "dim": self.dim,
            "seed": self.seed,
            "sample_size": self.sample_size,
            "iters": self.iterations,
            "converged": self.converged,
            "reseeded": self.reseeded,
            "generation": self.generation,
            "centroids_sha256": self.sha256(),
        }


def fit(
    vectors: np.ndarray,
    k: int,
    *,
    metric: Metric = Metric.COSINE,
    seed: int = 42,
    max_iter: int = 50,
    tol: float = 1e-4,
    sample_size: int = 0,
    generation: int = 1,
) -> Clustering:
    """Fit `k` centroids, on a deterministic sample when one is asked for.

    `tol` is the mean centroid shift below which the fit is called converged. Iterating
    past it rearranges rows on a boundary without changing which cluster a query would
    probe, and at 10K centroids an iteration is not cheap.
    """
    matrix = _as_matrix(vectors)
    if k < 1:
        raise ClusteringError(f"k must be at least 1, got {k}")
    if k > matrix.shape[0]:
        raise ClusteringError(
            f"asked for {k} clusters over {matrix.shape[0]} vectors — there is no "
            f"assignment in which every cluster has a member"
        )
    if metric is Metric.COSINE:
        matrix = _normalise(matrix)

    fitted_on = matrix
    drawn = 0
    if sample_size and sample_size < matrix.shape[0]:
        picked = sample_indices(matrix.shape[0], sample_size, seed=seed)
        fitted_on = matrix[picked]
        drawn = int(picked.shape[0])

    rng = np.random.default_rng(seed)
    centroids = _kmeans_plus_plus(fitted_on, k, metric, rng)

    reseeded = 0
    converged = False
    iterations = 0
    for step in range(1, max_iter + 1):
        iterations = step
        ids, dist = _closest(fitted_on, centroids, metric)
        moved, repaired = _recentre(fitted_on, ids, dist, k, metric)
        reseeded += repaired
        shift = float(np.linalg.norm(moved - centroids, axis=1).mean())
        centroids = moved
        if shift <= tol:
            converged = True
            break

    return Clustering(
        centroids=centroids,
        metric=metric,
        seed=seed,
        sample_size=drawn,
        iterations=iterations,
        converged=converged,
        reseeded=reseeded,
        generation=generation,
    )


def drift(
    clustering: Clustering, assignment: Assignment, baseline: ClusterStats
) -> dict[str, Any]:
    """How far the corpus has moved from the centroids that describe it.

    ! The metric that matters on an incremental run (`CLUSTERING.md` §6). Assigning new
    documents to stale centroids degrades routing *gradually and invisibly* — the same
    silent-failure shape as embedding drift, and with the same tell: nothing errors, the
    counts stay right, and recall slides. Reported on every incremental run so a
    re-cluster is a decision made against numbers rather than a hunch.
    """
    current = stats_of(assignment, clustering.k)
    grew = current.mean_distance - baseline.mean_distance
    return {
        "generation": clustering.generation,
        "rows": current.rows,
        "mean_distance": round(current.mean_distance, 6),
        "baseline_mean_distance": round(baseline.mean_distance, 6),
        "mean_distance_delta": round(grew, 6),
        "relative_drift": round(
            grew / baseline.mean_distance if baseline.mean_distance else 0.0, 6
        ),
        "largest_share": round(current.largest_share, 6),
        "empty": current.empty,
    }
