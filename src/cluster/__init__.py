"""Phase C: the routing structure — domain anchors, centroids, assignment.

The stage that cannot stream. k-means needs every vector to exist, which is the
structural reason this pipeline has three phases rather than one (`CLAUDE.md` §3).

It is also the stage whose failures are quietest: routing decides *where not to look*, so
a bad clustering returns fewer results rather than wrong ones, and nothing in a result set
says a cluster was never probed. Every check in here exists because of that.
"""

from cluster.anchors import (
    Calibration,
    DomainAnchor,
    calibrate,
    corpus_centroid,
    similarities,
)
from cluster.kmeans import (
    ROWS_PER_CLUSTER,
    Assignment,
    Clustering,
    ClusteringError,
    ClusterStats,
    Metric,
    check_balance,
    check_complete,
    drift,
    fit,
    metric_for,
    sample_indices,
    stats_of,
    suggest_k,
)

__all__ = [
    "ROWS_PER_CLUSTER",
    "Assignment",
    "Calibration",
    "ClusterStats",
    "Clustering",
    "ClusteringError",
    "DomainAnchor",
    "Metric",
    "calibrate",
    "check_balance",
    "check_complete",
    "corpus_centroid",
    "drift",
    "fit",
    "metric_for",
    "sample_indices",
    "similarities",
    "stats_of",
    "suggest_k",
]
