"""Enrichment — disposable by design.

Signals, entities and graph edges live in tables that can be dropped and rebuilt without
touching `chunks` (`CLAUDE.md` §5.4). That is what makes a weight refit cheap: Vera's
`fit_factors.py` searches a 3,125-configuration grid, and none of it should require
re-ingesting a corpus.
"""

from enrich.entities import Reference, ReferenceKind, extract_references, reference_counts
from enrich.factors import Factors, compute_factors
from enrich.graph import CitationGraph, GraphMetrics, build_graph
from enrich.quality import DocQuality, Lexicon, QualityGate
from enrich.relations import (
    DocIdentity,
    DocStatus,
    Index,
    Relation,
    RelationExtractor,
    derive_status,
    identity_of,
    resolve,
)
from enrich.sparse import K1, TOKEN_RE, B, Bm25Vectorizer, TokeniserDrift, tokenize

__all__ = [
    "DocIdentity",
    "DocQuality",
    "DocStatus",
    "Index",
    "Lexicon",
    "QualityGate",
    "Relation",
    "RelationExtractor",
    "derive_status",
    "identity_of",
    "resolve",
    "B",
    "Reference",
    "ReferenceKind",
    "extract_references",
    "reference_counts",
    "Bm25Vectorizer",
    "CitationGraph",
    "GraphMetrics",
    "build_graph",
    "Factors",
    "K1",
    "TOKEN_RE",
    "TokeniserDrift",
    "compute_factors",
    "tokenize",
]
