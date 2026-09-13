"""Phase B: chunks to vectors.

The embedder is a plugin, so the corpus schema is generated from the manifest rather than
hardcoded (`EMBEDDING.md` §2). The manifest is not documentation — it is the thing the
loader checks before a bundle is allowed into a database, and the thing whose absence cost
the incumbent corpus its reproducibility (`ABSORPTION.md` §11, §18.2).
"""

from embed.hashing import HashEmbedder, RawHashEmbedder, spec_for
from embed.preflight import (
    PreflightError,
    PreflightReport,
    check_instruction_is_applied,
    check_spec_pair,
    cosine,
    preflight,
    round_trip,
)
from embed.spec import (
    Embedder,
    EmbedderSpec,
    InstructionStyle,
    Modality,
    PaddingSide,
    Pooling,
    render_document,
    render_query,
)

__all__ = [
    "Embedder",
    "EmbedderSpec",
    "HashEmbedder",
    "InstructionStyle",
    "Modality",
    "PaddingSide",
    "Pooling",
    "PreflightError",
    "PreflightReport",
    "RawHashEmbedder",
    "check_instruction_is_applied",
    "check_spec_pair",
    "cosine",
    "preflight",
    "render_document",
    "render_query",
    "round_trip",
    "spec_for",
]
