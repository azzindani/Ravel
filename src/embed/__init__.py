"""Phase B: chunks to vectors.

The embedder is a plugin, so the corpus schema is generated from the manifest rather than
hardcoded (`EMBEDDING.md` §2). The manifest is not documentation — it is the thing the
loader checks before a bundle is allowed into a database, and the thing whose absence cost
the incumbent corpus its reproducibility (`ABSORPTION.md` §11, §18.2).
"""

from embed.factory import build_embedder, spec_of
from embed.hashing import (
    HashEmbedder,
    HashOmniEmbedder,
    RawHashEmbedder,
    ReferenceHashEmbedder,
    spec_for,
)
from embed.preflight import (
    PreflightError,
    PreflightReport,
    check_independent_backends,
    check_instruction_is_applied,
    check_spec_pair,
    cosine,
    preflight,
    round_trip,
)
from embed.progress import Progress, Tracker, format_progress, read_progress
from embed.run import Embedding, EmbedReport, check_vectors
from embed.spec import (
    Embedder,
    EmbedderSpec,
    InstructionStyle,
    ModalItem,
    Modality,
    OmniEmbedder,
    PaddingSide,
    Pooling,
    render_document,
    render_query,
)

__all__ = [
    "EmbedReport",
    "Embedding",
    "build_embedder",
    "check_vectors",
    "spec_of",
    "Embedder",
    "EmbedderSpec",
    "HashEmbedder",
    "HashOmniEmbedder",
    "InstructionStyle",
    "ModalItem",
    "Modality",
    "OmniEmbedder",
    "PaddingSide",
    "Pooling",
    "PreflightError",
    "PreflightReport",
    "Progress",
    "RawHashEmbedder",
    "ReferenceHashEmbedder",
    "Tracker",
    "check_independent_backends",
    "check_instruction_is_applied",
    "check_spec_pair",
    "cosine",
    "format_progress",
    "preflight",
    "read_progress",
    "render_document",
    "render_query",
    "round_trip",
    "spec_for",
]
