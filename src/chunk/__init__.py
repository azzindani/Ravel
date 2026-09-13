"""Phase B, first stage: canonical documents → chunks with immutable provenance.

`CHUNKING.md`. Retrieval can only return what chunking created, and this is the
cheapest stage to re-run — which is exactly why the Phase A boundary exists.
"""

# ! Imported for their side effect: a chunker that is never imported is never
# registered, and `ravel chunk --chunker unit` would fail with "no chunker 'unit'"
# on a machine where nothing else happened to import the module. Order is irrelevant
# — these pull in `chunk.base`, which does not import this package back.
from chunk import heading as _heading  # noqa: E402, F401  (registration)
from chunk import unit as _unit  # noqa: E402, F401  (registration)
from chunk.base import (
    BODY_TYPES,
    Builder,
    ChunkConfig,
    Entry,
    available,
    chunker,
    estimate_tokens,
    get,
)
from chunk.models import PATH_SEP, Chunk, chunk_id
from chunk.run import Chunking, RunReport, iter_chunk_shards
from chunk.store import SCHEMA, iter_chunks, read_chunks, write_chunks

__all__ = [
    "BODY_TYPES",
    "PATH_SEP",
    "SCHEMA",
    "Builder",
    "Chunk",
    "ChunkConfig",
    "Chunking",
    "Entry",
    "RunReport",
    "available",
    "chunk_id",
    "chunker",
    "estimate_tokens",
    "get",
    "iter_chunk_shards",
    "iter_chunks",
    "read_chunks",
    "write_chunks",
]
