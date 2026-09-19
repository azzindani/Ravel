"""Byte-aware batching — the invariants, not a replay of the loop.

`CLAUDE.md` §5.15: a validator must assert the invariant rather than re-implement the
construction. So these tests assert properties of every emitted batch (no batch exceeds
either bound; nothing is lost, duplicated or reordered) instead of recomputing the
expected split with the same arithmetic the code uses.
"""

from __future__ import annotations

import pytest

from runtime.batching import MAX_BATCH_BYTES, MAX_BATCH_ITEMS, batched


def _flatten[T](batches: list[list[T]]) -> list[T]:
    return [item for batch in batches for item in batch]


def test_every_item_survives_exactly_once_and_in_order() -> None:
    items = [f"chunk-{i}" for i in range(250)]
    batches = list(batched(items, max_items=7))
    assert _flatten(batches) == items


def test_no_batch_exceeds_either_bound() -> None:
    # Lengths chosen to make both bounds bind at different points in the run.
    items = ["x" * n for n in (1, 5, 400, 3, 900, 2, 7, 1200, 4)]
    batches = list(batched(items, max_bytes=1000, max_items=4))
    for batch in batches:
        payload = sum(len(item.encode("utf-8")) for item in batch)
        assert len(batch) <= 4
        # A lone oversized item is the documented exception: it goes out on its own.
        assert payload <= 1000 or len(batch) == 1


def test_a_single_oversized_document_goes_out_alone_rather_than_being_dropped() -> None:
    items = ["small", "M" * 5_000, "also small"]
    batches = list(batched(items, max_bytes=100))
    assert ["M" * 5_000] in batches
    assert _flatten(batches) == items


def test_bytes_are_measured_utf8_encoded_not_by_character_count() -> None:
    """! The whole reason this module exists. Indonesian legal text is not ASCII, and
    `len(s)` under-counts exactly where the 413 comes from."""
    # 'â' is 2 bytes in UTF-8, so 60 characters are 120 bytes.
    items = ["â" * 60, "â" * 60]
    assert len(list(batched(items, max_bytes=200))) == 2
    assert len(list(batched(items, max_bytes=240))) == 1


def test_text_of_selects_the_field_that_is_actually_sent() -> None:
    rows = [(1, "short"), (2, "b" * 500), (3, "c" * 500)]
    batches = list(batched(rows, text_of=lambda row: row[1], max_bytes=600))
    assert _flatten(batches) == rows
    assert all(len(b) >= 1 for b in batches)


def test_a_non_string_without_text_of_raises_rather_than_mis_measuring() -> None:
    with pytest.raises(TypeError, match="no way to measure"):
        list(batched([(1, "text")]))


def test_empty_input_yields_nothing() -> None:
    assert list(batched([])) == []


@pytest.mark.parametrize(("max_bytes", "max_items"), [(0, 8), (-1, 8), (800, 0), (800, -3)])
def test_a_bound_that_cannot_work_fails_loudly_at_the_call(
    max_bytes: int, max_items: int
) -> None:
    """`CLAUDE.md` §7.12's sibling rule: a limit that cannot work fails at once, not
    after the GPU hours are spent."""
    with pytest.raises(ValueError):
        list(batched(["a"], max_bytes=max_bytes, max_items=max_items))


def test_defaults_stay_under_teis_two_megabyte_payload_limit() -> None:
    assert MAX_BATCH_BYTES < 2_000_000
    assert MAX_BATCH_ITEMS > 0
