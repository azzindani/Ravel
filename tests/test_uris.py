"""Every path is a URI (`CLAUDE.md` §9) — so the three deployment shapes are one path.

These use `memory://` rather than a network filesystem on purpose: it is genuinely
non-local, so it exercises the fetch branch, while staying hermetic and instant. A test
that needs S3 to prove the abstraction has not proved the abstraction.
"""

from __future__ import annotations

from pathlib import Path

import fsspec
import pytest

from uris import as_local_path, find, is_local, resolve, split_suffix_variants


@pytest.fixture
def remote() -> fsspec.AbstractFileSystem:
    """A non-local filesystem holding a small corpus, including a case variant."""
    fs = fsspec.filesystem("memory")
    for name in ("a/one.pdf", "a/TWO.PDF", "b/three.pdf", "b/notes.txt"):
        fs.pipe_file(f"/corpus/{name}", b"%PDF-1.7 " + name.encode())
    yield fs
    fs.store.clear()


# -- the three shapes are one code path ------------------------------------------


@pytest.mark.parametrize("spelling", ["bare", "relative", "file_uri"])
def test_local_spellings_all_resolve_local(tmp_path: Path, spelling: str) -> None:
    """A bare path, a relative path and a file:// URI are the same thing to a caller.

    ! The URI is built from `tmp_path`, never written literally. A test that hardcodes
    an absolute path passes on one machine and is the defect this module exists to
    remove from the rest of the repository."""
    uri = {
        "bare": str(tmp_path),
        "relative": ".",
        "file_uri": tmp_path.as_uri(),
    }[spelling]

    fs, _ = resolve(uri)
    assert is_local(fs)


def test_remote_is_not_mistaken_for_local(remote: fsspec.AbstractFileSystem) -> None:
    assert not is_local(remote)


def test_local_path_is_yielded_in_place_and_never_copied(tmp_path: Path) -> None:
    """! The corpus is 5 GB. A helper that copies to read would make `file://` the
    slowest option, which would push every local run back onto raw paths and undo
    the abstraction."""
    source = tmp_path / "doc.pdf"
    source.write_bytes(b"%PDF-1.7 local")
    fs, path = resolve(str(source))

    with as_local_path(fs, path) as local:
        assert local.resolve() == source.resolve()


def test_remote_file_is_fetched_then_released(remote: fsspec.AbstractFileSystem) -> None:
    with as_local_path(remote, "/corpus/a/one.pdf") as local:
        assert local.read_bytes() == b"%PDF-1.7 a/one.pdf"
        assert local.suffix == ".pdf"  # extractors and OCR sniff on extension
        fetched = local
    assert not fetched.exists(), "a temporary fetch must not outlive its block"


def test_cache_dir_keeps_the_fetch_for_a_resumed_run(
    remote: fsspec.AbstractFileSystem, tmp_path: Path
) -> None:
    """What makes a resumed cloud job cheap: the second pass does not re-download."""
    with as_local_path(remote, "/corpus/a/one.pdf", cache_dir=tmp_path) as first:
        assert first.exists()
    assert first.exists(), "a cached fetch must survive its block"

    remote.rm("/corpus/a/one.pdf")  # the source is gone; the cache must still serve
    with as_local_path(remote, "/corpus/a/one.pdf", cache_dir=tmp_path) as second:
        assert second.read_bytes() == b"%PDF-1.7 a/one.pdf"


# -- listing: the two properties measurements depend on --------------------------


def test_case_variants_are_found_on_a_case_sensitive_filesystem(
    remote: fsspec.AbstractFileSystem,
) -> None:
    """! Remote filesystems are case-sensitive, so `TWO.PDF` is only found if the
    uppercase spelling is asked for. Locally Windows returns it from `*.pdf` already —
    the same code must not therefore count it twice (the test below)."""
    _, hits = find("memory:///corpus", fs=remote)

    assert "/corpus/a/TWO.PDF" in hits
    assert "/corpus/b/notes.txt" not in hits


def test_nothing_is_listed_twice(remote: fsspec.AbstractFileSystem) -> None:
    """A double-counted document silently halves an evaluation's effective sample."""
    _, hits = find("memory:///corpus", ["**/*.pdf", "**/*.pdf"], fs=remote)

    assert len(hits) == len(set(hits)) == 3


def test_listing_is_sorted_so_a_seeded_sample_is_reproducible(
    remote: fsspec.AbstractFileSystem,
) -> None:
    """! Load-bearing. Callers sample with a seeded shuffle to repeat a measurement.
    Object stores do not promise listing order, so without the sort the same seed picks
    different documents on hf:// than on file:// and the two runs cannot be compared —
    which is the comparison the URI support exists to enable."""
    _, first = find("memory:///corpus", fs=remote)
    _, second = find("memory:///corpus", fs=remote)

    assert first == second == sorted(first)


def test_missing_root_lists_nothing_rather_than_raising(
    remote: fsspec.AbstractFileSystem,
) -> None:
    _, hits = find("memory:///absent", fs=remote)
    assert hits == []


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        ("**/*.pdf", ("**/*.pdf", "**/*.PDF")),
        ("**/*.PDF", ("**/*.pdf", "**/*.PDF")),
        ("**/*", ("**/*",)),  # no suffix to vary
        ("data.2024/*", ("data.2024/*",)),  # a dot that is not a suffix
    ],
)
def test_suffix_variants(pattern: str, expected: tuple[str, ...]) -> None:
    assert set(split_suffix_variants(pattern)) == set(expected)
