"""A deterministic embedder that needs no GPU, no download and no network.

Not a toy. It exists so the three things that actually go wrong can be tested at all:

- the plugin boundary (does a stage hold an `Embedder` correctly?),
- the manifest (does the fingerprint change when a field that changes vectors changes?),
- the preflight (does a mismatch actually fail?).

None of those need a real model, and a test suite that needs an 8 GB checkpoint to check
them is a test suite nobody runs. It is deterministic across processes and platforms —
seeded from SHA-256 of the rendered input, not from `hash()` — so a fingerprint recorded
today still matches tomorrow.

! It is **not** a semantic model. Similar sentences get unrelated vectors. Never use it
to score retrieval; it will produce a number and the number will mean nothing.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any

from embed.spec import (
    EmbedderSpec,
    InstructionStyle,
    PaddingSide,
    Pooling,
    render_document,
    render_query,
)

__all__ = ["HashEmbedder", "spec_for"]


def spec_for(
    dim: int = 8,
    *,
    id: str = "hash-test",
    instruction_style: InstructionStyle = InstructionStyle.NONE,
    doc_instruction: str = "",
    query_instruction: str = "",
    **overrides: Any,
) -> EmbedderSpec:
    """A valid spec with sane defaults, for tests that care about one field."""
    defaults: dict[str, Any] = {
        "id": id,
        "model": "ravel/hash-embedder",
        "model_version": "1.0.0",
        "dim": dim,
        "pooling": Pooling.LAST_TOKEN,
        "padding_side": PaddingSide.LEFT,
        "normalize": True,
        "instruction_style": instruction_style,
        "doc_instruction": doc_instruction,
        "query_instruction": query_instruction,
    }
    # ! Overrides win over every default, including the ones named above as parameters.
    # A test that says `spec_for(padding_side=RIGHT)` must get RIGHT, not a TypeError.
    return EmbedderSpec(**(defaults | overrides))


@dataclass(slots=True)
class HashEmbedder:
    """Maps rendered input to a unit vector by hashing.

    ! It embeds the **rendered** input, never the raw text. That is the whole point: an
    implementation that skips `render_document` produces different vectors, so the
    preflight catches the §11 defect instead of the defect catching the corpus.
    """

    spec: EmbedderSpec

    def _vector(self, rendered: str | list[dict[str, Any]]) -> list[float]:
        material = repr(rendered).encode("utf-8")
        out: list[float] = []
        counter = 0
        while len(out) < self.spec.dim:
            digest = hashlib.sha256(material + counter.to_bytes(4, "big")).digest()
            out.extend((b - 127.5) / 127.5 for b in digest)
            counter += 1
        vec = out[: self.spec.dim]
        if self.spec.normalize:
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            vec = [x / norm for x in vec]
        return vec

    def embed_documents(self, batch: list[str]) -> list[list[float]]:
        return [self._vector(render_document(self.spec, t)) for t in batch]

    def embed_queries(self, batch: list[str]) -> list[list[float]]:
        return [self._vector(render_query(self.spec, t)) for t in batch]


@dataclass(slots=True)
class RawHashEmbedder(HashEmbedder):
    """The bug, as a test fixture: honours the manifest everywhere except applying it.

    This is `prepare_texts(self, contents): return contents` — a real embedder in every
    visible respect, declaring an instruction it never applies. It exists so the preflight
    has something to fail, and so a future refactor cannot quietly make the check
    unfalsifiable.
    """

    def embed_documents(self, batch: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in batch]  # the instruction is dropped here

    def embed_queries(self, batch: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in batch]
