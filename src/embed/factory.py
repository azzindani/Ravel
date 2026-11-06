"""From a corpus's `embed:` section to a working embedder.

The only place that knows which backend a `kind` names. Heavy imports stay inside the
backend modules, so asking for a `hash` embedder never imports torch.
"""

from __future__ import annotations

from embed.hashing import HashEmbedder
from embed.spec import (
    Embedder,
    EmbedderSpec,
    InstructionStyle,
    Modality,
    PaddingSide,
    Pooling,
)
from spec.corpus import EmbedSpec

__all__ = ["build_embedder", "spec_of"]


def spec_of(cfg: EmbedSpec) -> EmbedderSpec:
    """The manifest this configuration promises. Validates before any model is loaded."""
    return EmbedderSpec(
        id=cfg.id,
        model=cfg.model,
        model_version=cfg.revision,
        dim=cfg.dim,
        pooling=Pooling(cfg.pooling),
        padding_side=PaddingSide(cfg.padding_side),
        normalize=cfg.normalize,
        instruction_style=InstructionStyle(cfg.instruction_style),
        doc_instruction=cfg.doc_instruction,
        query_instruction=cfg.query_instruction,
        modality=Modality.TEXT,
        max_length=cfg.max_length,
        dtype=cfg.dtype,
        provider=cfg.provider,
        provider_pin=cfg.provider_pin,
    )


def build_embedder(cfg: EmbedSpec) -> Embedder:
    spec = spec_of(cfg)
    match cfg.kind:
        case "hash":
            return HashEmbedder(spec)
        case "local":
            from embed.local import TransformersEmbedder

            return TransformersEmbedder(spec, device=cfg.device)
        case "api":
            from embed.api import OpenAICompatEmbedder

            assert cfg.base_url is not None  # EmbedSpec guarantees it
            return OpenAICompatEmbedder(
                spec, base_url=cfg.base_url, api_key_env=cfg.api_key_env
            )
    raise ValueError(f"unknown embedder kind {cfg.kind!r}")
