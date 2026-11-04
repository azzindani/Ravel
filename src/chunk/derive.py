"""Text derived from a chunk for the index layers: what is embedded, what is matched.

The knowledge base is the chunks, and `body` in them is verbatim. These two functions are
how the index layers (embedding, keyword search) get the text *they* want without
touching it: the embedder sees the body with the law and chapter it belongs to, and the
keyword index sees a copy with OCR damage repaired in the tokens a query will contain.
Both are computed on demand from a chunk and its profile, so changing a template is a
re-index and never a re-chunk.
"""

from __future__ import annotations

import re
import string

from chunk.models import Chunk
from spec import Profile

__all__ = ["embed_text", "search_text"]


def _value(chunk: Chunk, name: str, skip: frozenset[str]) -> str:
    raw = getattr(chunk, name, None)
    text = "" if raw is None else str(raw).strip()
    return "" if text in skip else text


def embed_text(chunk: Chunk, profile: Profile | None) -> str:
    """The body, preceded by the context the profile says places it.

    A part of the context is dropped when every field in it is empty, so a document whose
    subject could not be read gets a shorter header rather than a header with a hole in it.
    With no template the body is returned unchanged.
    """
    spec = profile.spec.pipeline.derive.embed_text if profile else None
    if spec is None or not spec.context:
        return chunk.body
    skip = frozenset(spec.skip_values)
    parts: list[str] = []
    for template in spec.context:
        names = [n for _, n, _, _ in string.Formatter().parse(template) if n]
        values = {n: _value(chunk, n, skip) for n in names}
        if names and not any(values.values()):
            continue
        rendered = re.sub(r"\s+", " ", template.format(**values)).strip(" /-—")
        if rendered:
            parts.append(rendered)
    if not parts:
        return chunk.body
    return f"{spec.separator.join(parts)}\n\n{chunk.body}"


def search_text(chunk: Chunk, profile: Profile | None) -> str:
    """The body with the profile's rewrites applied, for the keyword index only."""
    text = chunk.body
    if profile is None:
        return text
    for pattern, replacement in profile.rewrites:
        text = pattern.sub(replacement, text)
    return text
