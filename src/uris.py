"""Every path is a URI (`CLAUDE.md` §9).

One helper, three deployment shapes, and no branch anywhere that names them:

    full local        sources file://./corpus       compute here
    hybrid            sources hf://datasets/...       compute here
    cloud to cloud    sources hf://datasets/...       compute on Kaggle/Colab/Vast

`INTERFACES.md` §5 states the rule this module exists to make true — *hybrid is not a
mode, it is just different URIs per stage*. A stage that takes a URI gets all three for
free; a stage that takes a `Path` gets exactly one, and adding the others later means
touching every call site.

Why materialising is not a design failure
-----------------------------------------
PyMuPDF and Tesseract read files, not streams, so a remote source has to land on disk
before an extractor can see it. That is not a leak in the abstraction — it *is* the
abstraction: `as_local_path` is a no-op for a local file and a cached fetch for a remote
one, so the caller never learns which it got. The local path stays free, which matters
when the corpus is 5 GB and the alternative is copying it to read it.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from fsspec import AbstractFileSystem
from fsspec.core import url_to_fs

__all__ = ["as_local_path", "find", "is_local", "resolve", "split_suffix_variants"]


def resolve(uri: str | os.PathLike[str]) -> tuple[AbstractFileSystem, str]:
    """`uri` to (filesystem, path). A bare path is `file://`, so callers need no branch."""
    # fsspec ships no types, so this is where the Any stops: named once, at the
    # boundary, rather than spreading into every caller's inferred signature.
    fs, path = url_to_fs(str(uri))
    return fs, str(path)


def is_local(fs: AbstractFileSystem) -> bool:
    """True when `fs` addresses this machine's disk, so a copy can be skipped."""
    protocol = fs.protocol
    names = (protocol,) if isinstance(protocol, str) else tuple(protocol)
    return "file" in names or "local" in names


def split_suffix_variants(pattern: str) -> tuple[str, ...]:
    """`**/*.pdf` to both case spellings, deduplicated.

    ! Not cosmetic, and the reason is asymmetric. Windows globbing is case-insensitive,
    so locally `**/*.pdf` already returns `REPORT.PDF` and a naive `pdf + PDF`
    concatenation double-counts it — which silently halves an evaluation's sample size.
    Remote filesystems are case-SENSITIVE, so there the uppercase spelling must be asked
    for explicitly or those documents vanish from the sample instead. Emitting both and
    deduplicating downstream is the only behaviour that is correct on both.
    """
    stem, dot, suffix = pattern.rpartition(".")
    if not dot or not suffix.isalpha():
        return (pattern,)
    return tuple(dict.fromkeys([f"{stem}.{suffix.lower()}", f"{stem}.{suffix.upper()}"]))


def find(
    uri: str | os.PathLike[str],
    patterns: Iterable[str] = ("**/*.pdf",),
    *,
    fs: AbstractFileSystem | None = None,
) -> tuple[AbstractFileSystem, list[str]]:
    """Every file under `uri` matching `patterns`, deduplicated and **sorted**.

    ! Sorted is load-bearing, not tidiness. Callers sample with a seeded shuffle so a
    measurement can be repeated; a shuffle is only reproducible if what it shuffles
    arrives in a fixed order. Listing order is not guaranteed to agree between a local
    directory and an object store, so without the sort the same `--seed` selects
    different documents on `file://` than on `hf://` and the two runs cannot be compared
    — which is precisely the comparison this exists to enable.
    """
    if fs is None:
        fs, root = resolve(uri)
    else:
        _, root = resolve(uri)

    root = root.rstrip("/") or root
    hits: list[str] = []
    for pattern in patterns:
        for variant in split_suffix_variants(pattern):
            hits.extend(fs.glob(f"{root}/{variant}"))
    return fs, sorted(dict.fromkeys(hits))


@contextmanager
def as_local_path(
    fs: AbstractFileSystem, path: str, *, cache_dir: str | os.PathLike[str] | None = None
) -> Iterator[Path]:
    """A real local path for `path`, fetched only when it is not already one.

    The local case yields the file where it lies and copies nothing. The remote case
    fetches to `cache_dir` (or a temporary directory) and removes only what it created,
    so pointing `cache_dir` at persistent storage turns the fetch into a cache that
    survives the run — which is what makes a resumed cloud job cheap.
    """
    if is_local(fs):
        yield Path(fs._strip_protocol(path))
        return

    suffix = Path(path).suffix  # extractors and OCR both sniff on extension
    if cache_dir is not None:
        target = Path(cache_dir) / Path(path).name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            fs.get_file(path, str(target))
        yield target
        return

    tmp = Path(tempfile.mkdtemp(prefix="ravel-"))
    try:
        target = tmp / (Path(path).name or f"source{suffix}")
        fs.get_file(path, str(target))
        yield target
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
