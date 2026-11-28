"""What a chunk's size is counted in: the embedder's own tokenizer (`STANDARD.md` §2).

! The budget a chunk is split at is a promise about the model: `max_tokens` 4,096 of the
embedder's tokens leaves the rest of its 8,192 for the context header, so no chunk is ever
cut short where it is embedded. A word estimate cannot keep that promise. Measured against
Qwen3's tokenizer on 1,000-1,500 chunks per corpus (2026-11-28), the estimate read
0.55x the real count on Japanese, 0.82x on English, 1.39x on German and 1.44x on Indonesian
(p99 2.36x): one budget in estimated tokens was a different real size in every language,
and the largest Indonesian chunks ran past what the model reads.

`estimate` remains for an embedder that has no tokenizer (the deterministic `hash` one
the tests use). It is named in the config, so it is in the chunk ids, and is never chosen
silently in place of a tokenizer that failed to load.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

from chunk.base import estimate_tokens

#: The tokenizer name of a corpus whose embedder has none.
ESTIMATE = "estimate"

TokenCounter = Callable[[str], int]


def tokenizer_ref(embed: Any) -> str:
    """`model@revision` of the corpus's embedder, or `estimate` when it has no tokenizer.

    ! The revision is part of the name: a model's next revision may tokenize differently,
    and its chunks are then other chunks.
    """
    if embed is None or getattr(embed, "kind", "hash") == "hash":
        return ESTIMATE
    return f"{embed.model}@{embed.revision}"


@functools.lru_cache(maxsize=4)
def counter_for(ref: str) -> TokenCounter:
    """Tokens of a text as the named tokenizer counts them, special tokens left out (the
    template adds them where the text is embedded).

    ! A tokenizer that cannot be loaded fails the run. Falling back to the estimate would
    chunk at another size under the same name.
    """
    if ref == ESTIMATE:
        return estimate_tokens
    model, _, revision = ref.partition("@")
    if not model or not revision:
        raise ValueError(f"tokenizer {ref!r}: expected `model@revision` or `{ESTIMATE}`")
    from tokenizers import Tokenizer

    try:
        tokenizer = Tokenizer.from_pretrained(model, revision=revision)
    except Exception as exc:
        raise RuntimeError(
            f"tokenizer {ref}: cannot be loaded ({exc}) · chunk sizes are counted in the "
            "embedder's tokens, so chunking stops rather than counting another way"
        ) from exc
    return lambda text: len(tokenizer.encode(text, add_special_tokens=False).ids)
