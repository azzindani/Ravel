"""The labeled set — the scarce resource, and the one thing here that is ground truth.

Named `evaluate` rather than `eval` for the same reason `mcp_server` is not `mcp`: a
top-level module that shadows a builtin is a trap for whoever imports it next. The labeled
data itself lives in `eval/`, which is a directory, not a package.

The rule that makes an eval set survive a re-chunk
--------------------------------------------------
`EVAL.md` §1: *"Relevance is recorded as a document + locator, never as a chunk id. Chunk
ids change with every chunker — that is the whole point of variants. A chunk counts as a
hit when its provenance covers the labeled locator. This is what makes two differently
chunked corpora comparable at all, and getting it wrong makes the eval set worthless."*

Everything in this module follows from that sentence. A `Relevance` cannot hold a chunk id;
there is nowhere to put one.

Negatives
---------
`EVAL.md` §1 wants ~10% negative queries — questions with no answer in the corpus — and
notes that they are the ones people skip and the ones that catch a system confidently
returning something irrelevant. A negative with a `relevant` entry is a contradiction, so
it is refused here rather than quietly scored as a miss on every arm.
"""

from __future__ import annotations

import json
import random
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

__all__ = [
    "COMPOSITION_TARGETS",
    "LabeledQuery",
    "QuerySet",
    "QueryType",
    "Relevance",
    "check_composition",
    "check_size",
]


class QueryType(StrEnum):
    FACTUAL = "factual"
    CONCEPTUAL = "conceptual"
    EXACT_CITATION = "exact_citation"
    CROSS_REFERENCE = "cross_reference"
    NEGATIVE = "negative"


#: `EVAL.md` §1's composition targets, as shares. Not enforced — a set is allowed to be
#: unbalanced — but reported, because an unbalanced set silently changes what the headline
#: number means. A set that is 80% exact-citation measures the identifier path and is
#: nearly blind to the embedder.
COMPOSITION_TARGETS: dict[QueryType, float] = {
    QueryType.FACTUAL: 0.40,
    QueryType.EXACT_CITATION: 0.20,
    QueryType.CONCEPTUAL: 0.20,
    QueryType.CROSS_REFERENCE: 0.10,
    QueryType.NEGATIVE: 0.10,
}

#: Below this, `EVAL.md` §1: "the noise exceeds the effect sizes you care about, and you
#: will promote a variant on a coin flip."
MIN_USEFUL_QUERIES = 100


@dataclass(frozen=True, slots=True)
class Relevance:
    """Where the answer lives — as a citation, never as a chunk id."""

    doc: str
    """The instrument's identifier, in the short form the profile produces: `UU 36/2008`."""

    locator: str = ""
    """`Pasal 17 ayat (1)`. Empty means "anywhere in this document", which is a weaker but
    legitimate label — some questions are answered by an instrument, not by a clause."""

    grade: int = 1
    """Graded relevance for nDCG. 3 = the clause that answers it, 1 = related context."""

    def __post_init__(self) -> None:
        if not self.doc.strip():
            raise ValueError(
                "a relevance judgement needs a document identifier; there is deliberately "
                "nowhere to record a chunk id (EVAL.md §1)"
            )
        if self.grade < 0:
            raise ValueError(f"grade cannot be negative, got {self.grade}")


@dataclass(frozen=True, slots=True)
class LabeledQuery:
    qid: str
    query: str
    type: QueryType = QueryType.FACTUAL
    relevant: tuple[Relevance, ...] = ()
    notes: str = ""

    def __post_init__(self) -> None:
        if self.type is QueryType.NEGATIVE and self.relevant:
            raise ValueError(
                f"{self.qid}: a negative query has no answer in the corpus by definition, "
                f"but {len(self.relevant)} relevant documents are recorded. One of the two "
                f"is wrong, and scoring it either way measures the label rather than the "
                f"corpus."
            )
        if self.type is not QueryType.NEGATIVE and not self.relevant:
            raise ValueError(
                f"{self.qid}: no relevant documents. If that is deliberate, mark it "
                f"`negative` — an unlabelled positive scores zero on every arm and drags "
                f"every comparison toward it equally."
            )

    @property
    def documents(self) -> frozenset[str]:
        return frozenset(r.doc for r in self.relevant)

    def to_dict(self) -> dict[str, Any]:
        return {
            "qid": self.qid,
            "query": self.query,
            "type": self.type.value,
            "relevant": [
                {"doc": r.doc, "locator": r.locator, "grade": r.grade} for r in self.relevant
            ],
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> LabeledQuery:
        return cls(
            qid=str(payload["qid"]),
            query=str(payload["query"]),
            type=QueryType(payload.get("type", "factual")),
            relevant=tuple(
                Relevance(
                    doc=str(item["doc"]),
                    locator=str(item.get("locator", "")),
                    grade=int(item.get("grade", 1)),
                )
                for item in payload.get("relevant", [])
            ),
            notes=str(payload.get("notes", "")),
        )


@dataclass(frozen=True, slots=True)
class QuerySet:
    """A frozen, versioned set of labeled queries."""

    items: tuple[LabeledQuery, ...]
    version: str = "v1"
    """! `EVAL.md` §6: "Freeze v1 of the set before the first variant sweep. A set that
    changes with the corpus cannot compare anything." Carried in every report so two
    numbers from different set versions cannot be put in the same table by accident."""

    notes: str = ""

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for item in self.items:
            if item.qid in seen:
                raise ValueError(
                    f"duplicate qid {item.qid!r} — a query counted twice weights itself"
                )
            seen.add(item.qid)

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self) -> Iterator[LabeledQuery]:
        return iter(self.items)

    def of_type(self, kind: QueryType) -> tuple[LabeledQuery, ...]:
        return tuple(item for item in self.items if item.type is kind)

    def composition(self) -> dict[str, int]:
        counts = {kind.value: 0 for kind in QueryType}
        for item in self.items:
            counts[item.type.value] += 1
        return counts

    # -- persistence --------------------------------------------------------------

    @classmethod
    def load(cls, path: Path | str, *, version: str = "v1") -> QuerySet:
        """Read JSONL, or a JSON array, or `{"queries": [...]}`.

        Three shapes because the seed set (`Vera/dev_tools/eval/queries.json`) is one of
        them and reformatting somebody's hand-labelled ground truth to suit a parser is a
        chance to corrupt it for no gain.
        """
        text = Path(path).read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError(f"{path} is empty")
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError:
            # JSONL. Detected by the whole-file parse failing rather than by sniffing the
            # first character: a JSONL file's first line is itself a valid JSON object, so
            # every cheap heuristic gets this backwards.
            payloads = [json.loads(line) for line in text.splitlines() if line.strip()]
        else:
            payloads = (
                loaded
                if isinstance(loaded, list)
                else loaded.get("queries", loaded.get("items", []))
            )
        return cls(items=tuple(LabeledQuery.from_dict(p) for p in payloads), version=version)

    def save(self, path: Path | str) -> int:
        with Path(path).open("w", encoding="utf-8", newline="\n") as handle:
            for item in self.items:
                handle.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")
        return len(self.items)

    # -- discipline ----------------------------------------------------------------

    def split(self, *, holdout: float = 0.2, seed: int = 42) -> tuple[QuerySet, QuerySet]:
        """Sweep set and holdout set.

        ! `EVAL.md` §4: *"Keep ~20% of the labeled set unused during sweeps and check the
        winner against it once. Sweeping against the whole set overfits it — and the set is
        the only ground truth you have."*

        Stratified by query type and seeded, so the holdout is not accidentally all
        negatives — which would make it measure one arm and certify the rest.
        """
        if not 0.0 < holdout < 1.0:
            raise ValueError(f"holdout must be in (0, 1), got {holdout}")
        rng = random.Random(seed)
        sweep: list[LabeledQuery] = []
        held: list[LabeledQuery] = []
        for kind in QueryType:
            stratum = sorted(self.of_type(kind), key=lambda q: q.qid)
            rng.shuffle(stratum)
            cut = round(len(stratum) * holdout)
            held.extend(stratum[:cut])
            sweep.extend(stratum[cut:])
        key = lambda q: q.qid  # noqa: E731
        return (
            QuerySet(items=tuple(sorted(sweep, key=key)), version=f"{self.version}/sweep"),
            QuerySet(items=tuple(sorted(held, key=key)), version=f"{self.version}/holdout"),
        )


def check_size(queries: QuerySet, *, minimum: int = MIN_USEFUL_QUERIES) -> tuple[bool, str]:
    """Is this set big enough for its differences to mean anything?"""
    if len(queries) >= minimum:
        return True, f"{len(queries)} queries"
    return False, (
        f"{len(queries)} queries, below {minimum}. Below roughly this size the noise "
        f"exceeds the effect sizes worth caring about, and a variant gets promoted on a "
        f"coin flip (EVAL.md §1). Report a confidence interval and treat small margins as "
        f"nothing."
    )


def check_composition(
    queries: QuerySet,
    *,
    targets: dict[QueryType, float] | None = None,
    tolerance: float = 0.15,
) -> tuple[bool, str]:
    """Does the set still measure what it was built to measure?

    ! Reported, not enforced. The point is that the headline number means something
    different when the mix shifts: a set that drifts to 80% exact-citation is measuring
    the identifier path and is nearly blind to the embedder, and the score will look
    stable while the thing it describes changes.
    """
    if not len(queries):
        return False, "empty set"
    wanted = targets or COMPOSITION_TARGETS
    counts = queries.composition()
    drifted = []
    for kind, share in wanted.items():
        actual = counts[kind.value] / len(queries)
        if abs(actual - share) > tolerance:
            drifted.append(f"{kind.value} {actual:.0%} (target {share:.0%})")
    if drifted:
        return False, "composition has drifted: " + ", ".join(drifted)
    return True, ", ".join(f"{k} {v}" for k, v in counts.items() if v)


def merge(sets: Iterable[QuerySet], *, version: str) -> QuerySet:
    """Combine sets, refusing duplicate qids rather than silently keeping one."""
    items: list[LabeledQuery] = []
    for queries in sets:
        items.extend(queries.items)
    return QuerySet(items=tuple(items), version=version)


@dataclass(frozen=True, slots=True)
class Promotion:
    """The record `EVAL.md` §5 requires, written rather than remembered.

    *"In six months the question 'why is the corpus chunked this way?' must have a data
    answer, not an archaeology project. That single property is the difference between
    Ravel and ID_Legal."*
    """

    variant: str
    config_hash: str
    bundle_version: str
    eval_run: str
    query_set_version: str
    metrics: dict[str, float] = field(default_factory=dict)
    margin_over_incumbent: float = 0.0
    confidence_interval: tuple[float, float] = (0.0, 0.0)
    build_cost_hours: float = 0.0
    decision: str = "promoted"
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant": self.variant,
            "config_hash": self.config_hash,
            "bundle_version": self.bundle_version,
            "eval_run": self.eval_run,
            "query_set_version": self.query_set_version,
            "metrics": self.metrics,
            "margin_over_incumbent": self.margin_over_incumbent,
            "confidence_interval": list(self.confidence_interval),
            "build_cost_hours": self.build_cost_hours,
            "decision": self.decision,
            "rationale": self.rationale,
        }

    def append_to(self, path: Path | str) -> None:
        """! Append, never rewrite. The history is the artifact; a promotions file that
        only holds the current winner answers "what is it now" and not "why"."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(self.to_dict(), ensure_ascii=False) + "\n")


def read_promotions(path: Path | str) -> tuple[dict[str, Any], ...]:
    target = Path(path)
    if not target.exists():
        return ()
    return tuple(
        json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line
    )
