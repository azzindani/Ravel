"""Source discovery: find the inputs, hash them, record what was found."""

from sources.scan import (
    ScanReport,
    SourceFile,
    guess_mime,
    human_bytes,
    read_manifest,
    scan,
    sha256_file,
    write_manifest,
)

__all__ = [
    "ScanReport", "SourceFile", "guess_mime", "human_bytes", "read_manifest", "scan",
    "sha256_file", "write_manifest",
]
