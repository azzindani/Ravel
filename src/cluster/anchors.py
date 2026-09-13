"""Domain anchors, and the calibration data Vera's threshold is chosen from.

Layer 1 of the routing structure (`CLUSTERING.md` §4). One vector per knowledge base;
Vera matches a query against the anchors and returns **nothing** when none clears the
threshold, because an honest "no match" beats a confident wrong domain.

Where the anchor comes from
---------------------------
Three constructions are defensible and the doc picks the third:

1. *Centroid of the corpus* — simple, and vague for a broad corpus. The mean of a corpus
   that covers tax, zoning and civil service points somewhere between all three, which is
   nothing in particular.
2. *Embedding of a written description* — sharp, human-controlled, and the description is
   documentation that a reviewer can read and disagree with.
3. *Several anchors per domain, matched by max* — the honest answer when a corpus really
   does cover separate areas, because it stops pretending one point represents them.

This module supports 2 and 3 and keeps 1 **alongside**, which turns out to be the useful
part: the cosine between a written description and the corpus centroid is a check nobody
usually writes. A description whose embedding sits far from the mass of the corpus does
not describe that corpus, and the failure it causes — queries the corpus can answer
falling under the threshold — looks exactly like a corpus with poor coverage.

Why calibration is data and not a number
----------------------------------------
`CLUSTERING.md` §4 asks for the threshold calibration data, "because Vera's threshold is a
number someone has to choose, and choosing it without data is guessing." So `calibrate`
never returns a bare number. It returns the threshold *with the cost it carries*, and it
says so out loud when the two distributions overlap too far for any threshold to separate
them — which is a real outcome and the one most worth knowing, since a threshold picked
anyway would look like a decision.

The asymmetry is deliberate. A false reject is zero recall on a query the corpus could
have answered, and the user sees an empty result set that looks authoritative. A false
accept is one extra domain probed, and the ranking sorts it out. So the threshold is
picked to hold in-domain recall at a target and the out-of-domain cost is *reported*,
never minimised at recall's expense.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

__all__ = [
    "Calibration",
    "DomainAnchor",
    "calibrate",
    "corpus_centroid",
    "similarities",
]


def _unit(vector: np.ndarray) -> np.ndarray:
    matrix = np.atleast_2d(np.asarray(vector, dtype=np.float32))
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    np.maximum(norms, 1e-12, out=norms)
    return matrix / norms


def corpus_centroid(vectors: np.ndarray) -> np.ndarray:
    """The mean direction of a corpus, normalised.

    Kept beside a written anchor rather than used as one — see the module docstring.
    """
    matrix = _unit(vectors)
    return _unit(matrix.mean(axis=0))[0]


def similarities(anchors: np.ndarray, queries: np.ndarray) -> np.ndarray:
    """Best anchor similarity per query. `(n_queries,)`, cosine, in [-1, 1]."""
    return (_unit(queries) @ _unit(anchors).T).max(axis=1)


@dataclass(frozen=True, slots=True)
class DomainAnchor:
    """One knowledge base's entry in layer 1."""

    domain_id: str
    vectors: np.ndarray
    """`(n_anchors, dim)`. Matched by max, so more anchors can only widen a domain, never
    narrow it — which is why adding one is safe and removing one is not."""

    description: str
    """The text that was embedded. Carried because the anchor is unreviewable without it:
    a 4096-float vector cannot be argued with, and a paragraph can."""

    centroid: np.ndarray | None = None
    """The corpus centroid, for comparison. Not used for matching."""

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise ValueError(
                f"domain {self.domain_id!r} has an anchor with no description — the "
                "description is the only reviewable part of a routing decision"
            )

    @property
    def n_anchors(self) -> int:
        return int(np.atleast_2d(self.vectors).shape[0])

    def similarity(self, queries: np.ndarray) -> np.ndarray:
        return similarities(self.vectors, queries)

    def describes_corpus(self, *, min_cosine: float = 0.3) -> tuple[bool, str]:
        """Does the written description point where the corpus actually is?

        ! The check that catches a plausible-sounding anchor written for a corpus that was
        not built. It fails open — `None` centroid means no opinion — because the centroid
        is optional and an absent one is not evidence of anything.
        """
        if self.centroid is None:
            return True, "no corpus centroid supplied; nothing to compare against"
        best = float(similarities(self.vectors, self.centroid[None, :])[0])
        if best < min_cosine:
            return False, (
                f"domain {self.domain_id!r}: the description embeds {best:.3f} from the "
                f"corpus centroid (floor {min_cosine:.2f}). The anchor and the corpus are "
                f"pointing in different directions, and every query this corpus could "
                f"answer will be judged against the wrong one."
            )
        return True, f"description sits {best:.3f} from the corpus centroid"


@dataclass(frozen=True, slots=True)
class Calibration:
    """A threshold, and what it costs. Never one without the other."""

    threshold: float | None
    target_recall: float
    false_reject: float
    """Share of in-domain queries the threshold would turn away. Zero recall each, so this
    is the expensive column."""

    false_accept: float
    """Share of out-of-domain queries admitted. One wasted probe each; the ranking sorts
    them out."""

    separable: bool
    in_domain: tuple[float, ...]
    out_domain: tuple[float, ...]

    @property
    def margin(self) -> float:
        """Gap between the weakest in-domain query and the strongest out-of-domain one.

        Negative means the distributions overlap — the usual case, and the number that
        says how badly.
        """
        if not self.in_domain or not self.out_domain:
            return 0.0
        return min(self.in_domain) - max(self.out_domain)

    def summary(self) -> dict[str, Any]:
        return {
            "threshold": self.threshold,
            "target_recall": self.target_recall,
            "false_reject": round(self.false_reject, 6),
            "false_accept": round(self.false_accept, 6),
            "separable": self.separable,
            "margin": round(self.margin, 6),
            "in_domain": _percentiles(self.in_domain),
            "out_domain": _percentiles(self.out_domain),
            "n_in": len(self.in_domain),
            "n_out": len(self.out_domain),
        }


def _percentiles(values: tuple[float, ...]) -> dict[str, float]:
    if not values:
        return {}
    array = np.asarray(values, dtype=np.float64)
    return {
        "p01": round(float(np.percentile(array, 1)), 6),
        "p05": round(float(np.percentile(array, 5)), 6),
        "p50": round(float(np.percentile(array, 50)), 6),
        "p95": round(float(np.percentile(array, 95)), 6),
        "p99": round(float(np.percentile(array, 99)), 6),
    }


def calibrate(
    in_domain: np.ndarray,
    out_domain: np.ndarray,
    *,
    target_recall: float = 0.95,
    max_false_accept: float = 0.5,
) -> Calibration:
    """Pick the threshold that holds in-domain recall at `target_recall`, and cost it.

    The threshold is the `(1 - target_recall)` quantile of the in-domain similarities: set
    it there and that share of genuine queries is admitted by construction. What it costs
    out-of-domain is then measured, not traded against.

    ! `separable=False` when the resulting false-accept rate exceeds `max_false_accept`.
    The threshold is still returned — it is the best available and a caller may want it —
    but it is returned *marked*, because an anchor that admits most out-of-domain queries
    is not routing, and the alternative (returning a number that looks like a decision)
    is how a guess gets into a config file and stays there.
    """
    if not 0.0 < target_recall <= 1.0:
        raise ValueError(f"target_recall must be in (0, 1], got {target_recall}")

    inside = tuple(float(v) for v in np.asarray(in_domain, dtype=np.float64).ravel())
    outside = tuple(float(v) for v in np.asarray(out_domain, dtype=np.float64).ravel())

    if not inside:
        # ! No threshold at all rather than a plausible default. An anchor calibrated
        # against no in-domain queries has not been calibrated, and 0.5 is a number
        # somebody will later mistake for a measurement.
        return Calibration(
            threshold=None,
            target_recall=target_recall,
            false_reject=0.0,
            false_accept=0.0,
            separable=False,
            in_domain=inside,
            out_domain=outside,
        )

    array = np.asarray(inside, dtype=np.float64)
    threshold = float(np.quantile(array, 1.0 - target_recall))
    false_reject = float((array < threshold).mean())
    false_accept = (
        float((np.asarray(outside, dtype=np.float64) >= threshold).mean()) if outside else 0.0
    )
    return Calibration(
        threshold=round(threshold, 6),
        target_recall=target_recall,
        false_reject=false_reject,
        false_accept=false_accept,
        separable=bool(outside) and false_accept <= max_false_accept,
        in_domain=inside,
        out_domain=outside,
    )
