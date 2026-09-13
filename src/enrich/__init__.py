"""Enrichment — disposable by design.

Signals, entities and graph edges live in tables that can be dropped and rebuilt without
touching `chunks` (`CLAUDE.md` §5.4). That is what makes a weight refit cheap: Vera's
`fit_factors.py` searches a 3,125-configuration grid, and none of it should require
re-ingesting a corpus.
"""

from enrich.entities import Reference, ReferenceKind, extract_references, reference_counts
from enrich.factors import Factors, compute_factors
from enrich.sparse import K1, TOKEN_RE, B, Bm25Vectorizer, TokeniserDrift, tokenize

__all__ = [
    "B",
    "Reference",
    "ReferenceKind",
    "extract_references",
    "reference_counts",
    "Bm25Vectorizer",
    "Factors",
    "K1",
    "TOKEN_RE",
    "TokeniserDrift",
    "compute_factors",
    "tokenize",
]
