"""Source discovery and hashing — the first stage, and the root of every cache key.

! Sources are read-only inputs. Nothing here modifies, renames or deletes a source
file (CLAUDE.md §5 rule 8).

The content hash produced here is `doc_id`. Every downstream key derives from it,
so a change to how it is computed invalidates an entire corpus — treat this file's
constants as part of the format.
"""

from __future__ import annotations

import hashlib
import mimetypes
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

READ_BLOCK = 1 << 20  # 1 MiB — large enough that syscall overhead disappears.


class SourceFile(BaseModel):
    """One discovered input. `sha256` is the identity of everything downstream."""

    model_config = ConfigDict(extra="forbid")

    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(ge=0)
    mime: str
    mtime: datetime
    discovered_at: datetime

    @property
    def doc_id(self) -> str:
        return f"sha256:{self.sha256}"


class ScanReport(BaseModel):
    """What a scan found — counts first, so a corpus is never silently partial."""

    model_config = ConfigDict(extra="forbid")

    root: str
    files: int
    bytes: int
    unique: int
    duplicates: int
    unreadable: list[str] = Field(default_factory=list)
    by_extension: dict[str, int] = Field(default_factory=dict)

    @property
    def duplicate_ratio(self) -> float:
        return self.duplicates / self.files if self.files else 0.0


def sha256_file(path: Path, *, block: int = READ_BLOCK) -> str:
    """Streamed, so a 500MB PDF costs 1 MiB of memory, not 500."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(block):
            digest.update(chunk)
    return digest.hexdigest()


def guess_mime(path: Path) -> str:
    mime, _ = mimetypes.guess_type(path.name)
    return mime or "application/octet-stream"


def walk(root: Path, patterns: Iterable[str] = ("**/*",)) -> Iterator[Path]:
    """Files matching any pattern, deduplicated, in a stable order.

    Sorted deliberately: a scan must be reproducible, and filesystem order is not.
    """
    seen: set[Path] = set()
    for pattern in patterns:
        for path in sorted(root.glob(pattern)):
            if path.is_file() and path not in seen:
                seen.add(path)
                yield path


def scan(
    root: Path,
    patterns: Iterable[str] = ("**/*",),
    *,
    skip_hidden: bool = True,
) -> tuple[list[SourceFile], ScanReport]:
    """Discover and hash every source file under `root`.

    Duplicate content is reported, not dropped: two paths with one hash is a fact
    about the corpus the operator should see, and deciding what it means is not
    this function's job.
    """
    now = datetime.now(UTC)
    found: list[SourceFile] = []
    unreadable: list[str] = []
    by_extension: dict[str, int] = {}
    hashes: set[str] = set()
    duplicates = 0
    total_bytes = 0

    for path in walk(root, patterns):
        if skip_hidden and any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        try:
            stat = path.stat()
            digest = sha256_file(path)
        except OSError as exc:
            unreadable.append(f"{path}: {exc.strerror or exc}")
            continue

        if digest in hashes:
            duplicates += 1
        hashes.add(digest)
        total_bytes += stat.st_size
        ext = path.suffix.lower() or "(none)"
        by_extension[ext] = by_extension.get(ext, 0) + 1

        found.append(
            SourceFile(
                path=path.relative_to(root).as_posix(),
                sha256=digest,
                bytes=stat.st_size,
                mime=guess_mime(path),
                mtime=datetime.fromtimestamp(stat.st_mtime, UTC),
                discovered_at=now,
            )
        )

    report = ScanReport(
        root=str(root),
        files=len(found),
        bytes=total_bytes,
        unique=len(hashes),
        duplicates=duplicates,
        unreadable=unreadable,
        by_extension=dict(sorted(by_extension.items(), key=lambda kv: -kv[1])),
    )
    return found, report


def write_manifest(files: Iterable[SourceFile], path: Path) -> int:
    """JSONL, one file per line — appendable, streamable, diffable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for source in files:
            handle.write(source.model_dump_json() + "\n")
            count += 1
    return count


def read_manifest(path: Path) -> list[SourceFile]:
    with open(path, encoding="utf-8") as handle:
        return [SourceFile.model_validate_json(line) for line in handle if line.strip()]


def human_bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"


__all__ = [
    "ScanReport",
    "SourceFile",
    "guess_mime",
    "human_bytes",
    "read_manifest",
    "scan",
    "sha256_file",
    "walk",
    "write_manifest",
]
