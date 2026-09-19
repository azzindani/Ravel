"""Byte-aware batching for an embedding endpoint.

Ported from `Vera/dev_tools/pre_embed/batching.py` — `ABSORPTION.md` §7 records the
verdict as **port as-is**, and the logic here is the same logic. What changed is the
typing and one default, both noted below.

! A fixed batch size is not safe on a legal corpus. Chunk length spans 3 characters to
32,767, so 32 documents can be 2 KB or 1 MB depending on where in the table you are.
TEI rejects a payload over 2 MB with HTTP 413, and a fixed size means the run dies
partway through — after the GPU hours are already spent. Budget by serialized bytes
*and* count, whichever binds first.

! The two constants are **defaults, not policy**. `CLAUDE.md` §7.8 forbids hardcoding a
batch size; a caller that has a configured one passes it. They are the TEI-derived
starting point for a caller that has not, not a limit compiled into the pipeline.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence

__all__ = ["MAX_BATCH_BYTES", "MAX_BATCH_ITEMS", "batched"]

#: TEI's default `payload_limit` is 2,000,000 bytes. Stay well under it: JSON escaping
#: inflates non-ASCII, and Indonesian legal text is full of it.
MAX_BATCH_BYTES = 1_200_000
MAX_BATCH_ITEMS = 32


def _identity(item: object) -> str:
    """The default measurer: the item is already the text.

    ! Deliberately not `str`. `str` accepts anything, so a caller that forgets `text_of`
    on a list of rows would batch by the length of `"('UU 30/2007', 'Pasal 8…')"` — a
    number related to the payload only by coincidence, and wrong quietly. Raising here
    costs one line and turns a silent mis-batching into a stack trace.
    """
    if not isinstance(item, str):
        raise TypeError(
            f"batched() got a {type(item).__name__} and no `text_of`, so there is no way "
            f"to measure its payload. Pass text_of=lambda row: row[…] naming the field "
            f"that is actually sent to the embedder."
        )
    return item


def batched[T](
    items: Sequence[T],
    *,
    text_of: Callable[[T], str] | None = None,
    max_bytes: int = MAX_BATCH_BYTES,
    max_items: int = MAX_BATCH_ITEMS,
) -> Iterator[list[T]]:
    """Yield batches bounded by both payload size and item count.

    `text_of` selects the string that will actually be sent; omit it when the items are
    already strings.
    """
    if max_bytes <= 0:
        raise ValueError(f"max_bytes must be positive, got {max_bytes}")
    if max_items <= 0:
        raise ValueError(f"max_items must be positive, got {max_items}")

    measure: Callable[[T], str] = text_of if text_of is not None else _identity
    batch: list[T] = []
    size = 0
    for item in items:
        n = len(measure(item).encode("utf-8"))
        # ! `if batch and …` — a single oversized document still goes out on its own
        # rather than being dropped or truncated here. TEI rejects it loudly, which is
        # the right failure: a chunk too large to embed is an upstream bug in chunking,
        # and silently shortening it would put a vector of the wrong text in the corpus.
        if batch and (size + n > max_bytes or len(batch) >= max_items):
            yield batch
            batch, size = [], 0
        batch.append(item)
        size += n
    if batch:
        yield batch
