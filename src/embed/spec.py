"""The embedder manifest, and the rendering it forces through one place.

Why this module is shaped the way it is
---------------------------------------
The incumbent corpus was embedded by `Vast/Embed_20251011/embed.py`, which contains:

    def get_detailed_instruct(task_description, query):
        return f'Instruct: {task_description}\\nQuery: {query}'

    def prepare_texts(self, contents):
        return contents

A correct instruction helper, defined once and never called, and a named seam for
applying it implemented as a pass-through. 748,558 documents went into an
instruction-aware model with no instruction, and nothing reported it, because retrieval
still returns its k nearest neighbours — just worse ones (`ABSORPTION.md` §11, §18.2).

Two structural answers, both here rather than in a convention:

1. **Instructions are rendered by a pure function, not by a method an implementation may
   forget to call.** `render_document` and `render_query` take a spec and a string. An
   embedder that does not route through them produces vectors that fail the preflight,
   so the failure is loud at build time rather than silent at query time.
2. **`instruction_style` is part of the manifest**, because "the instruction string" is
   not always a string: Qwen3 text takes a prefix, Qwen3-VL takes a system turn in a chat
   template and appends `.` if the instruction lacks trailing punctuation. A manifest
   recording only `query_instruction: str` cannot tell Vera which to reproduce, and the
   two do not yield the same vector.

The run that built the incumbent *did* write metadata — `embedding_model`,
`embedding_dimension`, `chunk_size_config` — and omitted pooling, normalisation,
instruction and padding side. It recorded the fields that were easy and none of the
fields that decide whether a vector can be reproduced. Everything in `EmbedderSpec` is
required for that reason.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "EmbedderSpec",
    "Embedder",
    "InstructionStyle",
    "Modality",
    "PaddingSide",
    "Pooling",
    "render_document",
    "render_query",
]


class Pooling(StrEnum):
    LAST_TOKEN = "last_token"
    MEAN = "mean"
    CLS = "cls"


class PaddingSide(StrEnum):
    """! Not cosmetic, and not recoverable from a checkpoint after the fact.

    Under `last_token` pooling the last token is `hidden[:, -1]` with left padding and
    `hidden[arange(B), mask.sum(1) - 1]` with right. Read the wrong end and every text
    shorter than the longest in its batch pools a PAD position: a unit-norm vector,
    perfectly well formed, meaning nothing. The two local Qwen3 encoders disagree — the
    text one loads `padding_side='left'`, the VL one `'right'` — so this is a property of
    the tokenizer as loaded, which is why it has to be recorded at build time.
    """

    LEFT = "left"
    RIGHT = "right"


class InstructionStyle(StrEnum):
    NONE = "none"
    """No instruction. Legitimate — but it must be *declared*, not defaulted into."""

    PREFIX = "prefix"
    """Qwen3 text: `Instruct: {task}\\nQuery: {text}`, prepended to the string."""

    CHAT_SYSTEM = "chat_system"
    """Qwen3-VL: a system turn in a chat template, with punctuation normalised."""


class Modality(StrEnum):
    TEXT = "text"
    OMNI = "omni"


@dataclass(frozen=True, slots=True)
class EmbedderSpec:
    """Everything needed to reproduce a vector, and nothing that is merely interesting.

    Goes verbatim into the bundle manifest (`BUNDLE.md` §3). Vera's query-side embedder
    is checked against it before a bundle loads, because a vector-space mismatch produces
    no error — only quietly worse results.
    """

    id: str
    model: str
    model_version: str
    """! An exact revision or commit, never `latest` or a bare model name. A floating
    alias can update under a corpus that took GPU-days to build."""
    dim: int
    pooling: Pooling
    padding_side: PaddingSide
    normalize: bool
    instruction_style: InstructionStyle = InstructionStyle.NONE
    doc_instruction: str = ""
    query_instruction: str = ""
    modality: Modality = Modality.TEXT
    max_length: int = 8192
    dtype: str = "float32"
    provider: str | None = None
    provider_pin: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.dim <= 0:
            raise ValueError(f"dim must be positive, got {self.dim}")
        if not self.model_version or self.model_version == "latest":
            raise ValueError(
                f"{self.id}: model_version must pin an exact revision, got "
                f"{self.model_version!r}. A floating alias can change under a built corpus."
            )
        declared = self.instruction_style is not InstructionStyle.NONE
        supplied = bool(self.doc_instruction or self.query_instruction)
        if supplied and not declared:
            raise ValueError(
                f"{self.id}: instructions supplied but instruction_style is 'none'. "
                "This is the shape of the defect in ABSORPTION.md §11 — an instruction "
                "that exists and is never applied."
            )
        if declared and not supplied:
            raise ValueError(
                f"{self.id}: instruction_style is {self.instruction_style!r} but both "
                "instruction strings are empty. Declare 'none' if that is intended."
            )

    # -- identity ------------------------------------------------------------

    def fingerprint(self) -> str:
        """Stable hash of every field that changes a vector.

        `provider`/`provider_pin` are included: routing the same model across hosts can
        change serving config, and the vectors with it.
        """
        payload = {k: v for k, v in asdict(self).items() if k != "id"}
        canonical = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    def same_space(self, other: EmbedderSpec) -> bool:
        """Whether two specs produce comparable vectors.

        `CLAUDE.md` §7.3: never mix vectors from different models, versions,
        instructions or pooling in one column.
        """
        return self.fingerprint() == other.fingerprint()

    def manifest(self) -> dict[str, Any]:
        return {"fingerprint": self.fingerprint(), **asdict(self)}


# -- rendering: the one place an instruction is applied ------------------------


def _normalise_instruction(instruction: str) -> str:
    """! Qwen3-VL appends `.` when the instruction does not end in punctuation, so
    "Represent the document" and "Represent the document." are the same instruction to
    it and different strings to everyone else. Reproduced here so a manifest round-trips
    to the same vector rather than to a nearly identical one."""
    text = instruction.strip()
    if text and not unicodedata.category(text[-1]).startswith("P"):
        text += "."
    return text


def _render(spec: EmbedderSpec, text: str, instruction: str) -> str | list[dict[str, Any]]:
    match spec.instruction_style:
        case InstructionStyle.NONE:
            return text
        case InstructionStyle.PREFIX:
            # ! The space after `Query:` is Qwen3's own template. The lab notebook in
            # §11 omitted it; a different string is a different vector.
            return f"Instruct: {instruction}\nQuery: {text}"
        case InstructionStyle.CHAT_SYSTEM:
            return [
                {"role": "system", "content": _normalise_instruction(instruction)},
                {"role": "user", "content": text},
            ]
    raise ValueError(f"unknown instruction_style {spec.instruction_style!r}")


def render_document(spec: EmbedderSpec, text: str) -> str | list[dict[str, Any]]:
    """Model input for a corpus document. Every embedder must route through this."""
    return _render(spec, text, spec.doc_instruction)


def render_query(spec: EmbedderSpec, text: str) -> str | list[dict[str, Any]]:
    """Model input for a query. Vera calls the equivalent; the two must agree."""
    return _render(spec, text, spec.query_instruction)


@runtime_checkable
class Embedder(Protocol):
    """A plugin. `spec` is the manifest; `embed_documents` must honour it."""

    spec: EmbedderSpec

    def embed_documents(self, batch: list[str]) -> Any: ...

    def embed_queries(self, batch: list[str]) -> Any: ...
