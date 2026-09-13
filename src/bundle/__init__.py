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
from bundle.schema import render_indexes, render_schema
from bundle.writer import (
    BUNDLE_VERSION,
    BundleLayoutError,
    BundleWriter,
    iter_vectors,
    read_bundle,
    sha256_file,
    vector_schema,
    write_clusters,
    write_domains,
    write_edges,
    write_signals,
    write_vectors,
)

__all__ = [
    "BUNDLE_VERSION",
    "BundleLayoutError",
    "BundleManifest",
    "BundleWriter",
    "ManifestError",
    "SparseSpec",
    "check_provenance",
    "check_vocabulary_covers_corpus",
    "iter_vectors",
    "read_bundle",
    "render_indexes",
    "render_schema",
    "sha256_file",
    "vector_schema",
    "write_clusters",
    "write_domains",
    "write_edges",
    "write_signals",
    "write_vectors",
]
