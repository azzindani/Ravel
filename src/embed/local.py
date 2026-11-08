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

    def __init__(
        self,
        spec: EmbedderSpec,
        *,
        device: str | None = None,
        memory_fraction: float = 0.0,
        append_eos: bool = False,
    ) -> None:
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
        self.append_eos = append_eos
        self._torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        if memory_fraction and self.device.startswith("cuda"):
            # ! Over-subscribed Windows GPUs page to system memory instead of failing: slow,
            # silent, and worse the longer the run (the allocator's cache grows past the
            # card). A cap makes torch free cached blocks and retry, and an out-of-memory
            # at the cap is what `_encode_safely` already knows how to recover from.
            index = torch.device(self.device).index
            torch.cuda.set_per_process_memory_fraction(
                memory_fraction, torch.cuda.current_device() if index is None else index
            )
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
            # ! Last-token pooling reads the final position, so the final token must be the
            # one the model was trained to pool at. Qwen3-Embedding's tokenizer ALREADY ends
            # every sequence with `<|endoftext|>` (its post-processor adds it, and keeps it
            # when truncating). This used to append `eos_token_id` unconditionally on the
            # belief that it did not: the tokenizer's eos is `<|im_end|>`, so every vector
            # was pooled at an `<|im_end|>` placed after the `<|endoftext|>`: a different
            # space (cosine 0.64 against the same text embedded the standard way, and so
            # against any query embedded by a plain tokenizer call). Add a closing token
            # only when the tokenizer left the sequence open.
            if self.append_eos:
                # The first recipe, kept because a corpus was embedded with it (28 GPU
                # hours): truncate one short and append `eos_token_id` after whatever the
                # tokenizer already added. Declared in the corpus config as `append_eos`.
                legacy = tok(
                    texts,
                    padding=False,
                    truncation=True,
                    max_length=self.spec.max_length - 1,
                    return_attention_mask=False,
                )
                legacy["input_ids"] = [[*seq, tok.eos_token_id] for seq in legacy["input_ids"]]
                return tok.pad(legacy, padding=True, return_tensors="pt")
            ids = tok(
                texts,
                padding=False,
                truncation=True,
                max_length=self.spec.max_length,
                return_attention_mask=False,
            )
            closing = set(tok.all_special_ids)
            ids["input_ids"] = [
                seq
                if seq and seq[-1] in closing
                else [*seq[: self.spec.max_length - 1], tok.eos_token_id]
                for seq in ids["input_ids"]
            ]
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
        # ! The retry is OUTSIDE the `except` block on purpose. Inside it, the exception's
        # traceback keeps the failed batch's tensors alive, so every nested retry starts
        # with the memory it just ran out of: under a memory cap that ended a run at
        # 6.75 GB allocated, with the batch that failed still holding most of it.
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
