"""Scoring a variant against the labeled set.

Named `evaluate`, not `eval`: a top-level module that shadows a builtin is a trap for
whoever imports it next, the same reason `mcp_server` is not `mcp`. The labeled data lives
in `eval/`, which is a directory rather than a package.

Without this package the rest of Ravel is a very organized way to produce corpora nobody
can compare (`EVAL.md`).
"""

from evaluate.labels import (
    COMPOSITION_TARGETS,
    MIN_USEFUL_QUERIES,
    LabeledQuery,
    Promotion,
    QuerySet,
    QueryType,
    Relevance,
    check_composition,
    check_size,
    merge,
    read_promotions,
)
from evaluate.metrics import (
    Candidate,
    Coverage,
    EvalReport,
    bootstrap_ci,
    covers,
    evaluate,
    is_hit,
    locator_path,
    ndcg_at,
    recall_at,
    reciprocal_rank,
    routing_loss,
)
from evaluate.oracle import OracleIndex, build_index, sampling_note, search, top_k

__all__ = [
    "COMPOSITION_TARGETS",
    "MIN_USEFUL_QUERIES",
    "Candidate",
    "Coverage",
    "EvalReport",
    "LabeledQuery",
    "OracleIndex",
    "Promotion",
    "QuerySet",
    "QueryType",
    "Relevance",
    "bootstrap_ci",
    "build_index",
    "check_composition",
    "check_size",
    "covers",
    "evaluate",
    "is_hit",
    "locator_path",
    "merge",
    "ndcg_at",
    "read_promotions",
    "recall_at",
    "sampling_note",
    "reciprocal_rank",
    "routing_loss",
    "search",
    "top_k",
]
