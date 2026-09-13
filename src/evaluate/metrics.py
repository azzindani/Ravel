"""Scoring a variant: coverage matching, the metrics, and the noise around them.

Recall@50 is the corpus-side number
-----------------------------------
`EVAL.md` §2: *"Ranking within the candidate set is Vera's job; if a chunk is not in the
candidate set, no ranking change can save it. Ravel is judged on whether the right text is
retrievable at all."* MRR and nDCG are reported because they are cheap and diagnostic, but
a variant is promoted on Recall@50 and on exact-citation recall, which is a bug below 100%
rather than a tuning result.

Coverage, not equality
----------------------
The hit test is the load-bearing piece, and it has three answers rather than two. A chunk
at `Pasal 17` **contains** a label at `Pasal 17 ayat (1)`; a chunk at `Pasal 17 ayat (1)
huruf a` sits **within** a label at `Pasal 17`. Both retrieve text that answers the
question, and both count — which is exactly what makes a pasal-chunked corpus comparable
with an ayat-chunked one. A chunk at `Pasal 17 ayat (2)` covers neither and does not count,
though a matcher written as "does the locator string contain the label" would say it does.

Locator vocabulary comes from the profile
-----------------------------------------
`pasal`, `ayat`, `huruf`, `bab` are Indonesian legal structure — corpus knowledge, so they
come from `registry/profiles/*.yaml` (`CLAUDE.md` §12). What is in code here is the
*shape*: a unit name followed by a number or letter, possibly parenthesised. A second
corpus in another jurisdiction gets its own vocabulary without touching this file.
"""

from __future__ import annotations

import math
import random
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from evaluate.labels import LabeledQuery, QuerySet, QueryType, Relevance
from spec.models import Profile

__all__ = [
    "Candidate",
    "Coverage",
    "EvalReport",
    "bootstrap_ci",
    "covers",
    "evaluate",
    "locator_path",
    "ndcg_at",
    "recall_at",
    "reciprocal_rank",
]

DEFAULT_KS = (5, 20, 50)

#: A unit marker, optionally in brackets: a number (`17`, `17a`), a roman numeral (`VI`)
#: or a single letter (`a`).
#:
#: ! Roman numerals are not decoration here. Indonesian regulations number `BAB` in roman
#: and `Pasal` in arabic, so a pattern without them reads `BAB VI` as `bab v` — which
#: collides with `BAB V` and silently makes two different chapters one locator.
_VALUE = r"\(?\s*([0-9]+[a-z]?|[ivxlcdm]+|[a-z])\s*\)?"


@dataclass(frozen=True, slots=True)
class Candidate:
    """One retrieved chunk, as the scorer needs to see it."""

    chunk_id: str
    doc: str
    """The instrument's identifier — `UU 36/2008`. Not `doc_id`: the label names a
    citation, and a sha256 cannot be compared with one."""

    locator: str = ""
    """`heading_path` or `locator_section`, as the chunk recorded it."""


class Coverage(StrEnum):
    EXACT = "exact"
    CONTAINS = "contains"
    """The chunk is broader than the label — a pasal chunk against an ayat label."""

    WITHIN = "within"
    """The chunk is narrower — an ayat chunk against a pasal label."""

    NONE = "none"


def _names(profile: Profile) -> tuple[str, ...]:
    """Structural vocabulary, longest first so `bab` cannot shadow a longer name."""
    names = {unit.name.lower() for unit in profile.units} | {
        kind.lower() for kind in profile.subunits
    }
    return tuple(sorted(names, key=len, reverse=True))


def locator_path(profile: Profile, text: str) -> tuple[tuple[str, str], ...]:
    """`"BAB VI > Pasal 17 ayat (1)"` becomes `(("bab","vi"), ("pasal","17"), ("ayat","1"))`.

    Ordered by position in the string rather than by the profile's level, because a locator
    is written in reading order and a chunk's `heading_path` already is one.
    """
    if not text:
        return ()
    names = _names(profile)
    if not names:
        return ()
    pattern = re.compile(
        rf"\b({'|'.join(re.escape(n) for n in names)})\b\s*{_VALUE}", re.IGNORECASE
    )
    return tuple(
        (match.group(1).lower(), match.group(2).lower()) for match in pattern.finditer(text)
    )


def covers(profile: Profile, chunk_locator: str, label_locator: str) -> Coverage:
    """How the chunk's provenance relates to the labeled locator."""
    label = locator_path(profile, label_locator)
    if not label:
        # A label with no parseable locator is a document-level judgement: any chunk of
        # that document covers it. Deliberately generous — the alternative is scoring a
        # legitimate weaker label as a miss on every arm equally, which adds noise and no
        # information.
        return Coverage.CONTAINS
    chunk = locator_path(profile, chunk_locator)
    if not chunk:
        return Coverage.NONE
    if chunk == label:
        return Coverage.EXACT
    if chunk == label[: len(chunk)]:
        return Coverage.CONTAINS
    if label == chunk[: len(label)]:
        return Coverage.WITHIN
    return Coverage.NONE


def is_hit(
    profile: Profile, candidate: Candidate, relevance: Relevance, *, strict: bool = False
) -> bool:
    """Does this chunk answer this judgement?

    `strict` drops `WITHIN`, requiring the chunk to be at least as broad as the label. Off
    by default: a narrower chunk still retrieves text inside the labeled clause, and
    requiring exactness would make every recall number a function of chunk size rather than
    of retrieval quality — the comparison the eval set exists to make.
    """
    if _normalise_doc(candidate.doc) != _normalise_doc(relevance.doc):
        return False
    coverage = covers(profile, candidate.locator, relevance.locator)
    if coverage is Coverage.NONE:
        return False
    return coverage is not Coverage.WITHIN if strict else True


#: `Nomor`, `No.` and nothing at all are the same word in a citation.
_NOMOR = re.compile(r"\bno(?:mor)?\b\.?", re.IGNORECASE)


def _normalise_doc(identifier: str) -> str:
    """`UU No. 36/2008`, `UU Nomor 36/2008` and `uu 36 / 2008` are one instrument.

    ! A label is written by a human and the candidate's identifier is written by a parser.
    Requiring them to agree on punctuation would score a correct retrieval as a miss, and
    the miss would look like a corpus defect.
    """
    if not identifier:
        return ""
    return re.sub(r"[\s.]+", "", _NOMOR.sub(" ", identifier)).lower()


# -- metrics ---------------------------------------------------------------------------


def _hits(
    profile: Profile, ranked: Sequence[Candidate], query: LabeledQuery, *, strict: bool
) -> list[int]:
    """Grade of the best judgement each ranked candidate satisfies; 0 for a miss."""
    grades = []
    for candidate in ranked:
        best = 0
        for relevance in query.relevant:
            if is_hit(profile, candidate, relevance, strict=strict):
                best = max(best, relevance.grade)
        grades.append(best)
    return grades


def recall_at(grades: Sequence[int], relevant_count: int, k: int) -> float:
    """Share of judgements covered somewhere in the top k.

    ! Counted against distinct judgements, not against hits. Four chunks of the same pasal
    in the top five is one judgement covered, and a chunker that splits finely would
    otherwise score higher for producing more rows.
    """
    if relevant_count <= 0:
        return 0.0
    found = sum(1 for grade in grades[:k] if grade > 0)
    return min(found, relevant_count) / relevant_count


def reciprocal_rank(grades: Sequence[int]) -> float:
    for i, grade in enumerate(grades, 1):
        if grade > 0:
            return 1.0 / i
    return 0.0


def ndcg_at(grades: Sequence[int], k: int = 10) -> float:
    gain = sum(g / math.log2(i + 1) for i, g in enumerate(grades[:k], 1) if g > 0)
    ideal_grades = sorted((g for g in grades if g > 0), reverse=True)[:k]
    ideal = sum(g / math.log2(i + 1) for i, g in enumerate(ideal_grades, 1))
    return gain / ideal if ideal else 0.0


def bootstrap_ci(
    per_query: Sequence[float], *, samples: int = 1000, seed: int = 42, alpha: float = 0.05
) -> tuple[float, float]:
    """A confidence interval over the query set, by resampling queries.

    ! `EVAL.md` §4: *"Report noise. A 1.5-point Recall@50 difference on 200 queries is
    usually noise, and treating it as signal is how you end up with a corpus tuned to
    randomness."* A mean with no interval beside it invites exactly that reading, so
    nothing in this module returns one without the other.
    """
    if not per_query:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(per_query)
    means = sorted(
        sum(per_query[rng.randrange(n)] for _ in range(n)) / n for _ in range(samples)
    )
    low = means[max(0, int(samples * alpha / 2) - 1)]
    high = means[min(samples - 1, int(samples * (1 - alpha / 2)))]
    return (round(low, 6), round(high, 6))


@dataclass(frozen=True, slots=True)
class EvalReport:
    """One variant, one query set, one mode."""

    variant: str
    query_set_version: str
    mode: str
    """`oracle` (brute force over the variant's vectors) or `routed` (through Vera).

    ! `EVAL.md` §3: the *gap* between them is the routing loss, and it is diagnostic.
    Reporting one number without the other has repeatedly sent people to re-chunk when
    their real problem was routing — so the mode is a required field rather than a note.
    """

    queries: int
    recall: dict[int, float] = field(default_factory=dict)
    recall_ci: dict[int, tuple[float, float]] = field(default_factory=dict)
    mrr: float = 0.0
    ndcg10: float = 0.0
    exact_citation_recall: float = 0.0
    """Below 100% this is a bug, not a tuning result (`EVAL.md` §2)."""

    negative_empty_rate: float = 0.0
    """Share of no-answer queries that correctly returned nothing."""

    per_query: dict[str, float] = field(default_factory=dict)
    chunk_count: int = 0
    notes: str = ""

    @property
    def headline(self) -> float:
        return self.recall.get(50, max(self.recall.values(), default=0.0))

    def beats(self, other: EvalReport, *, margin: float = 0.0) -> tuple[bool, str]:
        """Is this variant better than that one by more than the noise?

        ! Compares the interval, not the mean. `EVAL.md` §6 promotes on *"a margin bigger
        than the set's noise"*, and two means differing by less than their overlapping
        intervals is a coin flip that will not reproduce.
        """
        if self.query_set_version != other.query_set_version:
            return False, (
                f"different query sets ({self.query_set_version} vs "
                f"{other.query_set_version}) — these numbers are not comparable"
            )
        if self.mode != other.mode:
            return False, f"different modes ({self.mode} vs {other.mode})"

        gain = self.headline - other.headline
        low = self.recall_ci.get(50, (self.headline, self.headline))[0]
        rival_high = other.recall_ci.get(50, (other.headline, other.headline))[1]
        if gain <= margin:
            return False, f"Recall@50 {self.headline:.3f} vs {other.headline:.3f} (+{gain:.3f})"
        if low <= rival_high:
            return False, (
                f"Recall@50 {self.headline:.3f} vs {other.headline:.3f} (+{gain:.3f}), but "
                f"the confidence intervals overlap ({low:.3f} <= {rival_high:.3f}). On "
                f"{self.queries} queries that is noise, not a result."
            )
        return True, (
            f"Recall@50 {self.headline:.3f} vs {other.headline:.3f} (+{gain:.3f}), "
            f"intervals disjoint"
        )

    def summary(self) -> dict[str, Any]:
        return {
            "variant": self.variant,
            "query_set": self.query_set_version,
            "mode": self.mode,
            "queries": self.queries,
            "recall": {f"@{k}": round(v, 6) for k, v in sorted(self.recall.items())},
            "recall_ci": {f"@{k}": list(v) for k, v in sorted(self.recall_ci.items())},
            "mrr": round(self.mrr, 6),
            "ndcg@10": round(self.ndcg10, 6),
            "exact_citation_recall": round(self.exact_citation_recall, 6),
            "negative_empty_rate": round(self.negative_empty_rate, 6),
            "chunks": self.chunk_count,
        }


def evaluate(
    queries: QuerySet,
    results: dict[str, Sequence[Candidate]],
    *,
    profile: Profile,
    variant: str,
    mode: str = "oracle",
    ks: Iterable[int] = DEFAULT_KS,
    strict: bool = False,
    chunk_count: int = 0,
    seed: int = 42,
) -> EvalReport:
    """Score one variant's retrieval against the labeled set.

    `results` maps qid to that query's ranked candidates. A qid missing from `results` is
    scored as returning nothing — which is correct for a negative and a zero for anything
    else, and is never silently skipped: a variant that crashed on 30 queries would
    otherwise report a higher mean than one that answered them badly.
    """
    ks = tuple(sorted(set(ks)))
    recalls: dict[int, list[float]] = {k: [] for k in ks}
    rrs: list[float] = []
    ndcgs: list[float] = []
    exact_hits: list[float] = []
    negatives: list[float] = []
    per_query: dict[str, float] = {}
    top_k = max(ks)

    for query in queries:
        ranked = list(results.get(query.qid, ()))
        if query.type is QueryType.NEGATIVE:
            negatives.append(1.0 if not ranked else 0.0)
            continue

        grades = _hits(profile, ranked, query, strict=strict)
        for k in ks:
            recalls[k].append(recall_at(grades, len(query.relevant), k))
        rrs.append(reciprocal_rank(grades))
        ndcgs.append(ndcg_at(grades, 10))
        per_query[query.qid] = recall_at(grades, len(query.relevant), top_k)
        if query.type is QueryType.EXACT_CITATION:
            exact_hits.append(1.0 if any(g > 0 for g in grades) else 0.0)

    def mean(values: Sequence[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    return EvalReport(
        variant=variant,
        query_set_version=queries.version,
        mode=mode,
        queries=len(queries),
        recall={k: mean(v) for k, v in recalls.items()},
        recall_ci={k: bootstrap_ci(v, seed=seed) for k, v in recalls.items()},
        mrr=mean(rrs),
        ndcg10=mean(ndcgs),
        exact_citation_recall=mean(exact_hits),
        negative_empty_rate=mean(negatives),
        per_query=per_query,
        chunk_count=chunk_count,
    )


def routing_loss(oracle: EvalReport, routed: EvalReport) -> dict[str, Any]:
    """The gap between what the corpus can do and what a user gets.

    ! Diagnostic, and the reason `mode` is mandatory. A large gap means clustering or
    `clusters_probed` needs work, **not** chunking — and re-chunking in response to a
    routing problem is an expensive way to change nothing (`EVAL.md` §3).
    """
    if oracle.query_set_version != routed.query_set_version:
        raise ValueError("routing loss compares two runs over the same query set")
    gap = oracle.headline - routed.headline
    return {
        "oracle_recall": round(oracle.headline, 6),
        "routed_recall": round(routed.headline, 6),
        "routing_loss": round(gap, 6),
        "relative_loss": round(gap / oracle.headline, 6) if oracle.headline else 0.0,
        "verdict": (
            "routing is losing more than chunking could recover; tune clusters_probed "
            "and k before touching the chunker"
            if gap > 0.05
            else "routing loss is small; differences here are corpus-side"
        ),
    }
