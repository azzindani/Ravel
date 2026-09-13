"""Source scanning: discovery, hashing, and honest reporting."""

from __future__ import annotations

from pathlib import Path

import pytest

from sources import read_manifest, scan, sha256_file, write_manifest
from sources.scan import human_bytes


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "a").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "a" / "one.pdf").write_bytes(b"%PDF-1.7 one")
    (tmp_path / "a" / "two.pdf").write_bytes(b"%PDF-1.7 two")
    (tmp_path / "copy.pdf").write_bytes(b"%PDF-1.7 one")  # same bytes as one.pdf
    (tmp_path / "notes.md").write_text("hello", encoding="utf-8")
    (tmp_path / ".hidden" / "skip.pdf").write_bytes(b"%PDF-1.7 hidden")
    return tmp_path


def test_scan_finds_and_hashes(tree: Path) -> None:
    files, report = scan(tree)

    assert report.files == 4  # hidden directory skipped
    assert {f.path for f in files} == {"a/one.pdf", "a/two.pdf", "copy.pdf", "notes.md"}
    assert all(len(f.sha256) == 64 for f in files)
    assert report.by_extension[".pdf"] == 3


def test_duplicates_are_reported_not_dropped(tree: Path) -> None:
    """! Two paths, one hash is a fact about the corpus. Deciding what it means is
    the operator's call, so the scanner reports and keeps both."""
    files, report = scan(tree)

    assert report.files == 4
    assert report.unique == 3
    assert report.duplicates == 1
    assert len([f for f in files if f.path in {"a/one.pdf", "copy.pdf"}]) == 2

    by_path = {f.path: f.sha256 for f in files}
    assert by_path["a/one.pdf"] == by_path["copy.pdf"]


def test_patterns_filter(tree: Path) -> None:
    files, report = scan(tree, ["**/*.pdf"])
    assert report.files == 3
    assert all(f.path.endswith(".pdf") for f in files)


def test_doc_id_is_the_content_hash(tree: Path) -> None:
    files, _ = scan(tree, ["**/one.pdf"])
    (one,) = files
    assert one.doc_id == f"sha256:{sha256_file(tree / 'a' / 'one.pdf')}"


def test_scan_is_reproducible(tree: Path) -> None:
    """Filesystem order is not stable; a scan must be."""
    first, _ = scan(tree)
    second, _ = scan(tree)
    assert [f.path for f in first] == [f.path for f in second]
    assert [f.sha256 for f in first] == [f.sha256 for f in second]


def test_manifest_round_trip(tree: Path, tmp_path: Path) -> None:
    files, _ = scan(tree)
    manifest = tmp_path / "out" / "manifest.jsonl"

    assert write_manifest(files, manifest) == len(files)
    assert read_manifest(manifest) == files


def test_hashing_is_streamed(tmp_path: Path) -> None:
    """A large file must not be read into memory whole."""
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * (3 << 20))
    assert sha256_file(big, block=4096) == sha256_file(big)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, "0 B"), (512, "512 B"), (2048, "2.0 KB"), (5 << 20, "5.0 MB"), (3 << 30, "3.0 GB")],
)
def test_human_bytes(value: int, expected: str) -> None:
    assert human_bytes(value) == expected
