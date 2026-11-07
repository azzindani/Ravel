"""The API embedder: any OpenAI-compatible `/embeddings` endpoint.

For small corpora, for the query side, and for the round-trip preflight against a local
build (`EMBEDDING.md` §1). Same interface as the local embedder, so a variant swaps one for
the other by config.

! The API key is read from an environment variable the config **names**, never from the
config itself. A key in a YAML file is a key in git.

! Hosts differ. OpenRouter routes one model across providers whose serving config differs,
and the vectors with it, so `provider_pin` is sent as the only allowed provider with
fallbacks off. A manifest that records the pin and a request that ignores it would be the
defect in `ABSORPTION.md` §11 again.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import numpy as np

from embed.spec import EmbedderSpec, InstructionStyle, render_document, render_query

__all__ = ["OpenAICompatEmbedder"]

log = logging.getLogger(__name__)


class OpenAICompatEmbedder:
    thread_safe = True
    """The client holds no per-call state, so the stage may keep several requests in flight."""

    def __init__(
        self,
        spec: EmbedderSpec,
        *,
        base_url: str,
        api_key_env: str,
        retries: int = 5,
        client: Any = None,
    ) -> None:
        if spec.instruction_style is InstructionStyle.CHAT_SYSTEM:
            raise NotImplementedError(
                f"{spec.id}: an embeddings endpoint takes strings, not chat turns"
            )
        self.spec = spec
        self.retries = retries
        if client is not None:  # the seam tests use; production builds its own
            self.client = client
            return
        key = os.environ.get(api_key_env)
        if not key:
            raise RuntimeError(f"{spec.id}: ${api_key_env} is not set")
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise RuntimeError("the API embedder needs `uv sync --extra embed`") from exc
        self.client = OpenAI(base_url=base_url, api_key=key)

    def _extra_body(self) -> dict[str, Any]:
        if not self.spec.provider_pin:
            return {}
        return {"provider": {"order": [self.spec.provider_pin], "allow_fallbacks": False}}

    def _call(self, rendered: list[str]) -> np.ndarray[Any, Any]:
        delay = 1.0
        for attempt in range(1, self.retries + 1):
            try:
                response = self.client.embeddings.create(
                    model=self.spec.model,
                    input=rendered,
                    encoding_format="float",
                    extra_body=self._extra_body() or None,
                )
                break
            except Exception as exc:  # noqa: BLE001 - classified below
                status = getattr(exc, "status_code", None)
                retryable = status is None or status == 429 or status >= 500
                if attempt == self.retries or not retryable:
                    raise
                log.warning(
                    "embeddings call failed (%s), retry %d in %.0fs", exc, attempt, delay
                )
                time.sleep(delay)
                delay = min(delay * 2, 60.0)
        # ! By `index`, not by position: a server is free to answer out of order, and a
        # vector attached to the wrong chunk is a silent mis-join.
        rows = sorted(response.data, key=lambda d: d.index)
        if len(rows) != len(rendered):
            raise RuntimeError(f"sent {len(rendered)} texts, received {len(rows)} vectors")
        matrix = np.asarray([r.embedding for r in rows], dtype=np.float32)
        if self.spec.normalize:
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            matrix = matrix / np.where(norms == 0, 1.0, norms)
        return matrix

    def embed_documents(self, batch: list[str]) -> np.ndarray[Any, Any]:
        return self._call([str(render_document(self.spec, text)) for text in batch])

    def embed_queries(self, batch: list[str]) -> np.ndarray[Any, Any]:
        return self._call([str(render_query(self.spec, text)) for text in batch])
