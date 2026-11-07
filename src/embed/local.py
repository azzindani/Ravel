"""The local embedder: transformers on whatever GPU is here, CPU otherwise.

Used for bulk corpus embedding: no rate limit, full control of pooling, padding and
precision (`EMBEDDING.md` §1). `torch` and `transformers` are imported when an embedder is
*built*, never when this module is imported, so a machine without them (CI, the query-side
laptop, `ravel profiles list`) still imports `embed` and runs every stage that does not
embed (`STACK.md` §1: accelerators are never dependencies).

Every input is routed through `render_document` / `render_query`. An embedder that tokenised
the raw text would produce vectors that fail the preflight, which is the point of having one
renderer (`spec.py`).
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from embed.spec import (
    EmbedderSpec,
    InstructionStyle,
    PaddingSide,
    Pooling,
    render_document,
    render_query,
)

__all__ = ["TransformersEmbedder", "last_token_index"]

log = logging.getLogger(__name__)

_DTYPES = {"float16": "float16", "bfloat16": "bfloat16", "float32": "float32"}


def last_token_index(
    attention_mask: np.ndarray[Any, Any], padding_side: PaddingSide
) -> np.ndarray[Any, Any]:
    """Position of each row's last real token.

    ! The whole reason `padding_side` is in the manifest. With left padding the last real
    token is always at `-1`; with right padding it is at `mask.sum(1) - 1`. Reading `-1`
    on a right-padded batch pools a PAD position for every text shorter than the longest:
    a unit-norm vector, perfectly well formed, meaning nothing (`spec.PaddingSide`).
    Pure numpy so the rule can be tested without torch.
    """
    mask = np.asarray(attention_mask)
    if padding_side is PaddingSide.LEFT:
        return np.full(mask.shape[0], mask.shape[1] - 1, dtype=np.int64)
    return (mask.sum(axis=1) - 1).astype(np.int64)


class TransformersEmbedder:
    """An `Embedder` over a Hugging Face encoder."""

    def __init__(self, spec: EmbedderSpec, *, device: str | None = None) -> None:
        if spec.instruction_style is InstructionStyle.CHAT_SYSTEM:
            raise NotImplementedError(
                f"{spec.id}: chat-template (system turn) models are the omni path "
                "(EMBEDDING.md §4) and are not built; use a text model."
            )
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise RuntimeError(
                "the local embedder needs torch and transformers: `uv sync --extra embed` "
                "(and a CUDA build of torch for a GPU)"
            ) from exc

        self.spec = spec
        self._torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        dtype = getattr(torch, _DTYPES.get(spec.dtype, "float32"))
        if self.device == "cpu" and dtype != torch.float32:
            # ! Half precision on CPU is slow and in places unsupported. The vectors are
            # stored fp16 anyway; the compute precision is what changes, and it is recorded.
            log.warning("%s: %s on cpu, computing in float32", spec.id, spec.dtype)
            dtype = torch.float32

        # ! `revision` is the manifest's `model_version`: a floating alias can update
        # under a corpus that took GPU-days to build.
        self.tokenizer = AutoTokenizer.from_pretrained(
            spec.model, revision=spec.model_version, padding_side=spec.padding_side.value
        )
        self.model = (
            AutoModel.from_pretrained(spec.model, revision=spec.model_version, dtype=dtype)
            .to(self.device)
            .eval()
        )
        width = int(self.model.config.hidden_size)
        if width != spec.dim:
            raise ValueError(
                f"{spec.id}: the manifest declares {spec.dim} dimensions and "
                f"{spec.model} produces {width}. Declare the width the model has; a "
                "truncated (MRL) vector is a different spec."
            )

    def device_status(self) -> dict[str, Any]:
        """What the card is doing, for the progress file. Cheap, and never raises."""
        status: dict[str, Any] = {"device": self.device}
        if self.device.startswith("cuda"):
            torch = self._torch
            status["memory_gb"] = round(torch.cuda.memory_allocated() / 1e9, 2)
            status["reserved_gb"] = round(torch.cuda.memory_reserved() / 1e9, 2)
            status["peak_gb"] = round(torch.cuda.max_memory_allocated() / 1e9, 2)
            status["total_gb"] = round(
                torch.cuda.get_device_properties(self.device).total_memory / 1e9, 2
            )
        return status

    # -- pooling ---------------------------------------------------------------
    def _pool(self, hidden: Any, mask: Any) -> Any:
        torch = self._torch
        match self.spec.pooling:
            case Pooling.LAST_TOKEN:
                index = torch.as_tensor(
                    last_token_index(mask.cpu().numpy(), self.spec.padding_side),
                    device=hidden.device,
                )
                return hidden[torch.arange(hidden.shape[0], device=hidden.device), index]
            case Pooling.MEAN:
                weights = mask.unsqueeze(-1).to(hidden.dtype)
                return (hidden * weights).sum(1) / weights.sum(1).clamp(min=1)
            case Pooling.CLS:
                return hidden[:, 0]
        raise ValueError(f"unknown pooling {self.spec.pooling!r}")

    def _tokenize(self, texts: list[str]) -> Any:
        tok = self.tokenizer
        if self.spec.pooling is Pooling.LAST_TOKEN and tok.eos_token_id is not None:
            # ! Qwen3-Embedding pools the last token and expects it to be the end-of-text
            # token, which its tokenizer does not append. Truncating to `max_length - 1`
            # and appending it ourselves keeps the pooled position a real EOS even on a
            # text cut at the limit; padding afterwards respects `padding_side`.
            ids = tok(
                texts,
                padding=False,
                truncation=True,
                max_length=self.spec.max_length - 1,
                return_attention_mask=False,
            )
            ids["input_ids"] = [seq + [tok.eos_token_id] for seq in ids["input_ids"]]
            return tok.pad(ids, padding=True, return_tensors="pt")
        return tok(
            texts,
            padding=True,
            truncation=True,
            max_length=self.spec.max_length,
            return_tensors="pt",
        )

    def _encode(self, rendered: list[str]) -> np.ndarray[Any, Any]:
        torch = self._torch
        batch = self._tokenize(rendered).to(self.device)
        with torch.inference_mode():
            hidden = self.model(**batch).last_hidden_state
            vectors = self._pool(hidden, batch["attention_mask"]).float()
            if self.spec.normalize:
                vectors = torch.nn.functional.normalize(vectors, p=2, dim=1)
        return np.asarray(vectors.cpu().numpy(), dtype=np.float32)

    def _encode_safely(self, rendered: list[str]) -> np.ndarray[Any, Any]:
        """Halve the batch on out-of-memory rather than lose the run.

        A legal corpus has chunks of 3 characters and chunks of 32,000 in one shard, so a
        batch size that fits the median does not fit the tail; on an 8 GB card the tail is
        where it dies, hours in.
        """
        torch = self._torch
        try:
            return self._encode(rendered)
        except torch.cuda.OutOfMemoryError:
            if len(rendered) == 1:
                raise
            torch.cuda.empty_cache()
            half = len(rendered) // 2
            log.warning(
                "out of memory at %d texts, retrying as %d + %d",
                len(rendered),
                half,
                len(rendered) - half,
            )
            return np.concatenate(
                [self._encode_safely(rendered[:half]), self._encode_safely(rendered[half:])]
            )

    # -- the Embedder protocol ---------------------------------------------------
    def embed_documents(self, batch: list[str]) -> np.ndarray[Any, Any]:
        rendered = [str(render_document(self.spec, text)) for text in batch]
        return self._encode_safely(rendered)

    def embed_queries(self, batch: list[str]) -> np.ndarray[Any, Any]:
        rendered = [str(render_query(self.spec, text)) for text in batch]
        return self._encode_safely(rendered)
