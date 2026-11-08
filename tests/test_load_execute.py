"""The parts of the load executor that need no database. The rest runs in CI against pgvector
(`tools/ci_bundle_check.py` §9)."""

from __future__ import annotations

import numpy as np
import pytest

from load.execute import _columns, _statements, halfvec_text
from load.plan import CHUNK_COLUMNS, Step, copy_from


def test_a_half_precision_vector_survives_its_text_form_exactly() -> None:
    """Five significant digits identify every fp16 value, so the text loses nothing."""
    rng = np.random.default_rng(1)
    original = rng.normal(size=4096).astype(np.float16)
    parsed = np.array(halfvec_text(original).strip("[]").split(","), dtype=np.float32)
    assert np.array_equal(parsed.astype(np.float16), original)


def test_the_text_form_is_what_pgvector_parses() -> None:
    assert halfvec_text(np.array([0.5, -1.0, 0.0], dtype=np.float16)) == "[0.5,-1,0]"


def test_copy_statements_are_paired_with_their_files_in_order() -> None:
    sql = "\n".join(copy_from("chunks_stage", CHUNK_COLUMNS, f"/b/{n}") for n in ("a", "b"))
    step = Step(name="copy chunks", sql=sql, inputs=("chunks/a.parquet", "chunks/b.parquet"))
    pairs = _statements(step)
    assert [name for name, _ in pairs] == ["chunks/a.parquet", "chunks/b.parquet"]
    assert _columns(pairs[0][1]) == CHUNK_COLUMNS


def test_a_plan_whose_files_and_statements_disagree_is_refused() -> None:
    sql = copy_from("chunks_stage", ("id",), "/b/a")
    step = Step(name="copy chunks", sql=sql, inputs=("a.parquet", "b.parquet"))
    with pytest.raises(ValueError, match="disagree"):
        _statements(step)


def test_a_bundle_of_another_width_is_refused_before_anything_is_copied() -> None:
    from load.execute import width_mismatch

    assert width_mismatch(None, 1024) is None
    assert width_mismatch(1024, 1024) is None
    message = width_mismatch(16, 1024)
    assert message is not None
    assert "16" in message and "1024" in message


def test_the_column_list_is_read_back_without_its_quotes() -> None:
    assert _columns(copy_from("t", ("chunk_id", "embedding"), "/x")) == (
        "chunk_id",
        "embedding",
    )
