"""The execution model: ledger, shards, resume.

Interruption is the normal path, not an error path. These tests assert that killing a
run at any point loses at most one unsealed shard — which is the whole claim of
`EXECUTION.md` §1.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from canon import CanonicalDoc, read_shard
from runtime import Ledger, ShardWriter, Status


def clone(doc: CanonicalDoc, n: int) -> CanonicalDoc:
    copy = doc.model_copy(deep=True)
    sha = f"{n:064x}"
    copy.source.sha256 = sha
    copy.doc_id = f"sha256:{sha}"
    return copy


# ===========================================================================
# Ledger
# ===========================================================================
def test_done_keys_are_what_a_rerun_skips(tmp_path: Path) -> None:
    with Ledger(tmp_path / "l.db") as ledger:
        ledger.record("k1", "extract", Status.DONE)
        ledger.record("k2", "extract", Status.FAILED, detail="boom")
        ledger.record("k3", "chunk", Status.DONE)

        assert ledger.done_keys("extract") == {"k1"}
        assert ledger.done_keys("chunk") == {"k3"}


def test_deferred_is_not_a_failure_but_is_still_recorded(tmp_path: Path) -> None:
    """! A scanned page with no OCR path is a missing stage, not a fault. It must
    not fail a healthy run, and it must not vanish either (`LOOPHOLES.md` §3)."""
    with Ledger(tmp_path / "l.db") as ledger:
        ledger.record("k1", "extract", Status.DEFERRED, detail="no text layer")

        assert ledger.done_keys("extract") == set()
        assert ledger.counts("extract") == {"deferred": 1}
        assert ledger.failures("extract")[0].detail == "no text layer"


def test_recording_is_idempotent_and_counts_attempts(tmp_path: Path) -> None:
    with Ledger(tmp_path / "l.db") as ledger:
        for _ in range(3):
            ledger.record("k1", "extract", Status.FAILED, detail="transient")
        assert ledger.counts("extract") == {"failed": 1}
        assert ledger.attempts("k1") == 3


def test_a_retry_can_supersede_a_failure(tmp_path: Path) -> None:
    with Ledger(tmp_path / "l.db") as ledger:
        ledger.record("k1", "extract", Status.FAILED, detail="OOM")
        ledger.record("k1", "extract", Status.DONE, shard="part-00000.parquet")
        assert ledger.done_keys("extract") == {"k1"}


def test_forget_makes_a_stage_recompute(tmp_path: Path) -> None:
    """A stage version bump invalidates its cache — cheap insurance, used freely."""
    with Ledger(tmp_path / "l.db") as ledger:
        ledger.record("k1", "extract", Status.DONE)
        ledger.record("k2", "chunk", Status.DONE)
        assert ledger.forget("extract") == 1
        assert ledger.done_keys("extract") == set()
        assert ledger.done_keys("chunk") == {"k2"}


def test_ledger_survives_reopening(tmp_path: Path) -> None:
    path = tmp_path / "l.db"
    with Ledger(path) as ledger:
        ledger.record_many([("k1", "extract", Status.DONE, None, "s0")])
    with Ledger(path) as reopened:
        assert reopened.done_keys("extract") == {"k1"}


# ===========================================================================
# Shards
# ===========================================================================
def test_documents_share_shards(doc: CanonicalDoc, tmp_path: Path) -> None:
    with ShardWriter(tmp_path, target_bytes=10**9) as writer:
        for i in range(20):
            writer.add(clone(doc, i), f"k{i}")

    shards = sorted(tmp_path.glob("part-*.parquet"))
    assert len(shards) == 1
    assert len(read_shard(str(shards[0]))) == 20


def test_shards_seal_at_the_size_target(doc: CanonicalDoc, tmp_path: Path) -> None:
    with ShardWriter(tmp_path, target_bytes=800) as writer:
        for i in range(12):
            writer.add(clone(doc, i), f"k{i}")

    shards = sorted(tmp_path.glob("part-*.parquet"))
    assert len(shards) > 1
    assert sum(len(read_shard(str(s))) for s in shards) == 12


def test_sealing_reports_its_keys(doc: CanonicalDoc, tmp_path: Path) -> None:
    """! The ledger is written from these, and only after the file exists. Marking
    work done before its artifact is durable is how a resumed run skips something
    that was never written."""
    sealed = []
    with ShardWriter(tmp_path, target_bytes=10**9, on_seal=sealed.append) as writer:
        for i in range(5):
            writer.add(clone(doc, i), f"k{i}")

    assert len(sealed) == 1
    assert sealed[0].keys == [f"k{i}" for i in range(5)]
    assert sealed[0].docs == 5
    assert sealed[0].path.exists()


def test_a_resumed_run_does_not_overwrite_existing_shards(
    doc: CanonicalDoc, tmp_path: Path
) -> None:
    with ShardWriter(tmp_path, target_bytes=10**9) as first:
        first.add(clone(doc, 1), "k1")
    with ShardWriter(tmp_path, target_bytes=10**9) as second:
        second.add(clone(doc, 2), "k2")

    shards = sorted(tmp_path.glob("part-*.parquet"))
    assert [s.name for s in shards] == ["part-00000.parquet", "part-00001.parquet"]


def test_an_interrupted_run_keeps_the_work_already_done(
    doc: CanonicalDoc, tmp_path: Path
) -> None:
    """Ctrl-C after 3 documents must leave those 3 on disk, not discard them."""
    with pytest.raises(KeyboardInterrupt), ShardWriter(tmp_path, target_bytes=10**9) as writer:
        for i in range(3):
            writer.add(clone(doc, i), f"k{i}")
        raise KeyboardInterrupt

    shards = sorted(tmp_path.glob("part-*.parquet"))
    assert len(shards) == 1
    assert len(read_shard(str(shards[0]))) == 3


def test_an_empty_writer_produces_no_shard(tmp_path: Path) -> None:
    with ShardWriter(tmp_path) as writer:
        assert writer.seal() is None
    assert list(tmp_path.glob("*.parquet")) == []


def test_a_shard_seals_on_the_block_cap_before_the_size_target(
    doc: CanonicalDoc, tmp_path: Path
) -> None:
    """! The bound that keeps a run alive on a small box.

    `target_bytes` estimates compressed parquet; the buffered objects producing it cost
    far more. The first ID_Legal run held 5 GB resident to seal a 41 MB shard. With a
    block cap, memory is bounded by configuration rather than by luck.
    """
    per_doc = len(doc.blocks)
    with ShardWriter(tmp_path, target_bytes=10**9, max_blocks=per_doc * 3) as writer:
        for i in range(9):
            writer.add(clone(doc, i), f"k{i}")

    shards = sorted(tmp_path.glob("part-*.parquet"))
    assert len(shards) == 3
    assert all(len(read_shard(str(s))) == 3 for s in shards)


def test_a_shard_seals_on_the_document_cap(doc: CanonicalDoc, tmp_path: Path) -> None:
    with ShardWriter(tmp_path, target_bytes=10**9, max_blocks=0, max_docs=4) as writer:
        for i in range(8):
            writer.add(clone(doc, i), f"k{i}")

    assert len(sorted(tmp_path.glob("part-*.parquet"))) == 2


def test_the_caps_can_be_disabled(doc: CanonicalDoc, tmp_path: Path) -> None:
    """0 means unbounded — the size target alone decides, as it did before."""
    with ShardWriter(tmp_path, target_bytes=10**9, max_blocks=0, max_docs=0) as writer:
        for i in range(20):
            writer.add(clone(doc, i), f"k{i}")

    assert len(sorted(tmp_path.glob("part-*.parquet"))) == 1


def test_one_huge_document_does_not_blow_the_block_cap(
    doc: CanonicalDoc, tmp_path: Path
) -> None:
    """! The bound is `max(cap, one document)`, not `cap + one document`.

    ID_Legal contains a 3,359-page scan that extracts to 479,355 blocks — 570x the
    median. Sealing after the fact would have let a 250k cap hold 729k blocks at its
    peak, so the memory bound degraded exactly when the corpus threw something unusual
    at it. Sealing first keeps ordinary documents behind the cap regardless.
    """
    per_doc = len(doc.blocks)
    giant = clone(doc, 99)
    giant.blocks = [b.model_copy(deep=True) for _ in range(4) for b in doc.blocks]
    for order, block in enumerate(giant.blocks):
        block.id, block.reading_order = f"b{order:05d}", order

    writer = ShardWriter(tmp_path, target_bytes=10**9, max_blocks=per_doc * 2)
    with writer:
        for i in range(3):
            writer.add(clone(doc, i), f"k{i}")
        writer.add(giant, "giant")
        for i in range(3, 6):
            writer.add(clone(doc, i), f"k{i}")

    # The giant is alone in its shard: nothing was buffered alongside it, so the
    # buffer never held the cap AND an outsized document at the same time.
    sealed_with_giant = next(s for s in writer.sealed if "giant" in s.keys)
    assert sealed_with_giant.keys == ["giant"]
    assert all(s.docs <= 2 for s in writer.sealed if "giant" not in s.keys)
    assert sum(s.docs for s in writer.sealed) == 7


def test_forcing_clears_a_generations_shards(doc: CanonicalDoc, tmp_path: Path) -> None:
    """! A generation directory stops a *config change* from mixing artifact sets. It
    cannot stop a rerun of the same config from duplicating itself, because the writer
    resumes into a fresh index rather than overwriting — so forcing must clear first."""
    from runtime.shards import clear_shards

    with ShardWriter(tmp_path, target_bytes=10**9) as writer:
        for i in range(3):
            writer.add(clone(doc, i), f"k{i}")

    assert clear_shards(tmp_path) == 1
    assert list(tmp_path.glob("part-*.parquet")) == []

    with ShardWriter(tmp_path, target_bytes=10**9) as again:
        again.add(clone(doc, 0), "k0")

    shards = sorted(tmp_path.glob("part-*.parquet"))
    assert [s.name for s in shards] == ["part-00000.parquet"]
