"""The seven checks of `BUNDLE.md` §5, and the rule that makes them worth running.

    Fail any check → load nothing. Partial loads are the worst outcome, because the
    resulting corpus looks fine.

That is the whole design. A corpus that failed to load is a problem someone fixes in an
afternoon; a corpus that loaded 94% of its rows is a search engine that returns slightly
wrong answers for a year. Every check here is cheap and runs before the first `COPY`.

Skipped is not passed
---------------------
! The rule inherited from `embed/preflight.py`, and the one most likely to be eroded in a
hurry. A check that could not run — no query-side embedder to hand, no free-space figure —
reports SKIPPED, and a report containing a skip does **not** pass unless the caller says
so explicitly. The alternative is a preflight that goes green on a laptop with none of its
inputs available, which is worse than no preflight at all: it is a preflight someone
trusts.

That failure has a precedent in this project. `embed/preflight.py` was written, and my own
first test of it passed the same embedder as both build and query side — cosine 1.000000,
three green checks, and a gate that could only pass. The fix was `check_independent_backends`
and the lesson generalises: a check must be capable of failing (`CLAUDE.md` §15).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np

from bundle.writer import BundleLayoutError, sha256_file
from embed.preflight import cosine
from embed.spec import EmbedderSpec

__all__ = [
    "Check",
    "LoadReport",
    "Status",
    "check_canary",
    "check_checksums",
    "check_completeness",
    "check_consistency",
    "check_dimension",
    "check_disk",
    "check_provenance",
    "preflight",
]

DEFAULT_MIN_COSINE = 0.999

#: Bytes on disk in Postgres per byte of zstd parquet, before indexes. A rough figure,
#: and deliberately generous: parquet stores a dictionary-encoded, compressed column and
#: the heap stores rows. Used only to fail *early* — a wrong estimate costs a spurious
#: warning, and the failure it prevents is running out of disk at 90% of a COPY.
HEAP_EXPANSION = 4.0

#: Extra headroom for building the GIN and btree indexes after the load. Roughly the size
#: of the table again: the build sorts the whole column set before writing.
INDEX_HEADROOM = 1.0


class Status(StrEnum):
    OK = "ok"
    FAIL = "fail"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    status: Status
    detail: str

    @property
    def ok(self) -> bool:
        return self.status is Status.OK


def _ok(name: str, detail: str) -> Check:
    return Check(name, Status.OK, detail)


def _fail(name: str, detail: str) -> Check:
    return Check(name, Status.FAIL, detail)


def _skip(name: str, detail: str) -> Check:
    return Check(name, Status.SKIPPED, detail)


@dataclass(frozen=True, slots=True)
class LoadReport:
    bundle: Path
    checks: tuple[Check, ...]

    @property
    def failed(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.status is Status.FAIL)

    @property
    def skipped(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.status is Status.SKIPPED)

    def passed(self, *, allow_skipped: bool = False) -> bool:
        """! `allow_skipped` defaults to False. See the module docstring."""
        if self.failed:
            return False
        return allow_skipped or not self.skipped

    def raise_for_status(self, *, allow_skipped: bool = False) -> None:
        if self.passed(allow_skipped=allow_skipped):
            return
        lines = [
            f"  {c.status.value.upper():<8} {c.name}: {c.detail}"
            for c in self.checks
            if c.status is not Status.OK
        ]
        raise BundleLayoutError(
            "load preflight failed — nothing was loaded:\n" + "\n".join(lines)
        )

    def describe(self) -> str:
        symbol = {Status.OK: "ok  ", Status.FAIL: "FAIL", Status.SKIPPED: "skip"}
        return "\n".join(f"{symbol[c.status]} {c.name}: {c.detail}" for c in self.checks)


# -- the checks ---------------------------------------------------------------------


def check_dimension(document: dict[str, Any], target_dim: int | None) -> Check:
    """1. The manifest's `dim` equals the target column's width.

    A bundle and a database are married by dimension (`EMBEDDING.md` §2). `COPY` would
    catch this too — but after the schema has been created and the operator has waited.
    """
    dim = int(document["dense"]["dim"])
    if target_dim is None:
        return _skip(
            "dimension",
            f"bundle is {dim}-dimensional; no target column width was supplied to "
            f"compare it against",
        )
    if dim != target_dim:
        return _fail(
            "dimension",
            f"bundle is {dim}-dimensional and the target column is {target_dim}. "
            f"These are different spaces, not a resize (CLAUDE.md §7.3).",
        )
    return _ok("dimension", f"{dim}-dimensional, matching the target column")


def check_consistency(document: dict[str, Any], expected: EmbedderSpec | None) -> Check:
    """2. Model, version, instruction, pooling, normalisation all match the deployment.

    ! Compared field by field rather than by fingerprint, so the failure says *which*
    field moved. "Manifest mismatch" sends someone to diff two JSON blobs; "padding_side:
    bundle=left, deployment=right" is the answer.
    """
    if expected is None:
        return _skip("consistency", "no target deployment spec supplied")

    dense = document["dense"]
    fields = (
        "model",
        "model_version",
        "dim",
        "pooling",
        "padding_side",
        "normalize",
        "instruction_style",
        "query_instruction",
    )
    drift = [
        f"{name}: bundle={dense.get(name)!r} deployment={getattr(expected, name)!r}"
        for name in fields
        if str(dense.get(name)) != str(getattr(expected, name))
    ]
    if drift:
        return _fail(
            "consistency",
            "the query-side embedder is not the one that built this corpus — "
            + "; ".join(drift),
        )
    return _ok("consistency", f"{dense['model']} @ {dense['model_version']}")


def check_canary(
    root: Path, query_vector: Sequence[float] | None, *, min_cosine: float = DEFAULT_MIN_COSINE
) -> Check:
    """3. The query-side embedder reproduces `reference.npy`.

    The one check that tests the *live* system rather than a recorded claim. Everything
    else compares strings in a manifest; this compares vectors, so it catches the cases a
    manifest cannot describe — a quantised checkpoint, a different pooling implementation,
    a tokenizer that loaded with a different padding side than the one recorded.
    """
    path = root / "reference.npy"
    if not path.exists():
        return _fail(
            "canary",
            "no reference.npy — this bundle cannot be verified against a live embedder "
            "at all, only trusted (BUNDLE.md §3)",
        )
    if query_vector is None:
        return _skip("canary", "no query-side vector supplied; the canary did not run")

    reference = np.load(path)
    produced = np.asarray(query_vector, dtype=np.float64)
    if produced.shape != reference.shape:
        return _fail(
            "canary",
            f"the query embedder returned {produced.shape} against a "
            f"{reference.shape} reference",
        )
    score = cosine(reference.tolist(), produced.tolist())
    if score < min_cosine:
        return _fail(
            "canary",
            f"cosine {score:.6f} against the recorded reference vector, below "
            f"{min_cosine}. The query-side embedder is not the one that built this "
            f"corpus; results would be quietly worse, never wrong-looking.",
        )
    return _ok("canary", f"cosine {score:.6f} against the recorded reference")


def check_completeness(document: dict[str, Any], *, tolerance: float = 0.0) -> Check:
    """4. Row counts match the manifest, and the failure count is within tolerance.

    ! "A bundle missing 400 documents must not load silently." The incumbent's extraction
    produced 994,602 records against a 748,558-row corpus and nothing recorded what
    happened to the missing quarter (`ABSORPTION.md`). A count that nobody compares is
    not a count.
    """
    inventory = document.get("inventory", {})
    counts: dict[str, int] = inventory.get("counts", {})
    claimed = int(document["chunk_count"])
    written = sum(n for name, n in counts.items() if name.startswith("chunks/"))
    vectors = sum(n for name, n in counts.items() if name.startswith("vectors/"))
    failed = int(counts.get("failed.jsonl", 0))

    if written != claimed:
        return _fail(
            "completeness",
            f"the manifest claims {claimed:,} chunks and the shards hold {written:,}",
        )
    if vectors and vectors != written:
        return _fail(
            "completeness",
            f"{vectors:,} vectors for {written:,} chunks — the load-time join would drop "
            f"the difference without reporting it",
        )
    total = written + failed
    share = failed / total if total else 0.0
    if share > tolerance:
        return _fail(
            "completeness",
            f"{failed:,} of {total:,} documents failed ({share:.2%}), above the corpus "
            f"tolerance of {tolerance:.2%}",
        )
    return _ok(
        "completeness",
        f"{written:,} chunks, {vectors:,} vectors, {failed:,} failed ({share:.2%})",
    )


def check_checksums(root: Path, document: dict[str, Any]) -> Check:
    """5. Every recorded file matches its hash — and no unrecorded file is present.

    ! The second half is the one worth having. A shard on disk that the inventory does not
    mention is the two-generations bug: a re-run that appended new shards beside stale
    ones, where the loader reads both and the count is simply wrong. Nothing raises, and
    the corpus is silently larger than it should be (`runtime/shards.py`).
    """
    recorded: dict[str, str] = document.get("inventory", {}).get("checksums", {})
    if not recorded:
        return _fail("checksums", "the manifest records no checksums; nothing to verify")

    mismatched = [
        name
        for name, digest in recorded.items()
        if not (root / name).exists() or sha256_file(root / name) != digest
    ]
    on_disk = {
        str(p.relative_to(root)).replace("\\", "/")
        for p in root.rglob("*")
        if p.is_file() and p.name != "manifest.json"
    }
    untracked = sorted(on_disk - set(recorded))

    problems = []
    if mismatched:
        problems.append(f"{len(mismatched)} files missing or altered: {mismatched[:5]}")
    if untracked:
        problems.append(
            f"{len(untracked)} files on disk are not in the inventory: {untracked[:5]} — "
            f"a stale generation left beside this one would be loaded too"
        )
    if problems:
        return _fail("checksums", "; ".join(problems))
    return _ok("checksums", f"{len(recorded)} files verified")


def check_disk(
    root: Path,
    free_bytes: int | None,
    *,
    expansion: float = HEAP_EXPANSION,
    headroom: float = INDEX_HEADROOM,
) -> Check:
    """6. Free space exceeds the projected load size plus index build headroom.

    At ~0.8TB for 100M rows, running out mid-`COPY` is a real and ugly failure: the
    transaction rolls back after hours, the disk is still full, and the operator is left
    cleaning up rather than loading.
    """
    on_disk = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
    projected = int(on_disk * expansion * (1 + headroom))
    if free_bytes is None:
        return _skip(
            "disk",
            f"bundle is {on_disk / 1e9:.1f} GB; projected load needs about "
            f"{projected / 1e9:.1f} GB. No free-space figure supplied.",
        )
    if free_bytes < projected:
        return _fail(
            "disk",
            f"{free_bytes / 1e9:.1f} GB free, projected load needs about "
            f"{projected / 1e9:.1f} GB ({on_disk / 1e9:.1f} GB of parquet at {expansion:.0f}x "
            f"heap expansion plus {headroom:.0%} index headroom)",
        )
    return _ok(
        "disk", f"{free_bytes / 1e9:.1f} GB free, projected need {projected / 1e9:.1f} GB"
    )


def check_provenance(document: dict[str, Any]) -> Check:
    """7. Zero rows with a null `source_url`.

    ! Fails, and the corpus is still loadable — with `provenance_complete=False` written
    into the manifest and a nullable column in the generated schema. The incumbent has no
    `source_url` on 100% of its rows and is worth loading as a scored baseline, but it
    must be loaded *flagged*, never dressed up as verifiable (`ABSORPTION.md` §3, §19).
    Passing this check silently is the failure; failing it loudly is the feature.
    """
    complete = bool(document.get("provenance_complete", True))
    if complete:
        return _ok("provenance", "every chunk carries a source_url")
    return _fail(
        "provenance",
        "this bundle is provenance-incomplete: it has chunks with no source_url. It may "
        "still be loaded deliberately (allow_incomplete_provenance) — but as a flagged "
        "baseline, not as a citable corpus.",
    )


# -- the gate -----------------------------------------------------------------------


def preflight(
    root: Path | str,
    *,
    target_dim: int | None = None,
    deployment: EmbedderSpec | None = None,
    query_vector: Sequence[float] | None = None,
    free_bytes: int | None = None,
    failure_tolerance: float = 0.0,
    min_cosine: float = DEFAULT_MIN_COSINE,
    allow_incomplete_provenance: bool = False,
) -> LoadReport:
    """Run every check. Nothing here touches a database.

    `allow_incomplete_provenance` downgrades check 7 from a failure to a skip rather than
    to a pass, so a bundle loaded under it still cannot report a clean preflight. The
    operator made a choice, and the report keeps saying so.
    """
    root = Path(root)
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        return LoadReport(
            bundle=root,
            checks=(
                _fail(
                    "manifest",
                    f"{root} has no manifest.json — an unsealed build directory, not a "
                    f"bundle. Without it the corpus is unverifiable (CLAUDE.md §6).",
                ),
            ),
        )

    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    provenance = check_provenance(document)
    if allow_incomplete_provenance and provenance.status is Status.FAIL:
        provenance = _skip(
            "provenance",
            "provenance-incomplete, loaded deliberately — results from this corpus must "
            "be reported as uncitable",
        )

    return LoadReport(
        bundle=root,
        checks=(
            check_dimension(document, target_dim),
            check_consistency(document, deployment),
            check_canary(root, query_vector, min_cosine=min_cosine),
            check_completeness(document, tolerance=failure_tolerance),
            check_checksums(root, document),
            check_disk(root, free_bytes),
            provenance,
        ),
    )
