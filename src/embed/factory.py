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
from spec.hardware import HardwareSpec

__all__ = ["build_embedder", "spec_of", "with_hardware"]


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
        # ! The local embedder tokenises itself, so HOW it closes a sequence is part of the
        # space it produces. Vectors made before the closing-token fix (an extra `<|im_end|>`
        # after the tokenizer's own `<|endoftext|>`) are a different space, and the
        # fingerprint is what keeps a resumed run from adding to them: a new value here is a
        # new vector generation, never a mix.
        extra=({} if cfg.append_eos else {"tokenization": "tokenizer-closed"})
        if cfg.kind == "local"
        else {},
    )


def build_embedder(cfg: EmbedSpec) -> Embedder:
    spec = spec_of(cfg)
    match cfg.kind:
        case "hash":
            return HashEmbedder(spec)
        case "local":
            from embed.local import TransformersEmbedder

            return TransformersEmbedder(
                spec,
                device=cfg.device,
                memory_fraction=cfg.memory_fraction,
                append_eos=cfg.append_eos,
                fused_norm=cfg.fused_norm,
            )
        case "api":
            from embed.api import OpenAICompatEmbedder

            assert cfg.base_url is not None  # EmbedSpec guarantees it
            return OpenAICompatEmbedder(
                spec, base_url=cfg.base_url, api_key_env=cfg.api_key_env
            )
    raise ValueError(f"unknown embedder kind {cfg.kind!r}")


def with_hardware(cfg: EmbedSpec, hardware: HardwareSpec) -> EmbedSpec:
    """The configuration with a machine's batching and device applied.

    Only a local model is sized to an accelerator; an API endpoint batches by its own limits
    and the hash embedder has nothing to size. None of the fields set here reaches `spec_of`,
    which is what keeps a vector made on one machine valid on another.
    """
    if cfg.kind != "local":
        return cfg
    update: dict[str, object] = {
        "batch_items": hardware.batch_items,
        "batch_bytes": hardware.batch_bytes,
        "batch_square": hardware.batch_square,
        "batch_tokens": hardware.batch_tokens,
        "memory_fraction": hardware.memory_fraction,
    }
    if hardware.device:
        update["device"] = hardware.device
    return cfg.model_copy(update=update)
