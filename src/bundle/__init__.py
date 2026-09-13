"""Phase C: the output contract — parquet shards plus a manifest.

The manifest is part of the corpus (`CLAUDE.md` §6), and the schema is generated from it
rather than hand-maintained (`BUNDLE.md` §2). Those two rules are the same rule: a
hand-written schema and a built corpus drift apart silently, and the schema is the first
thing a reader trusts.
"""

from bundle.manifest import (
    BundleManifest,
    ManifestError,
    SparseSpec,
    check_provenance,
    check_vocabulary_covers_corpus,
)
from bundle.schema import render_schema

__all__ = [
    "BundleManifest",
    "ManifestError",
    "SparseSpec",
    "check_provenance",
    "check_vocabulary_covers_corpus",
    "render_schema",
]
