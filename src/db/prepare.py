"""The whole path from a sealed bundle to a database ready to serve.

    preflight -> place -> load (with the placement) -> accelerate -> layout -> verify

Ravel runs this to build a deployable database and Vera runs the same code to deploy one,
which is the point of it living here once. Each stage is the library already tested on its
own; this orders them and refuses to report ready until the database itself says so.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from db.accelerate import AcceleratorReport, accelerate
from db.layout import LayoutReport, build_layout
from db.place import place
from db.verify import VerifyError, verify_ready

__all__ = ["PrepareError", "PrepareReport", "prepare"]


class PrepareError(RuntimeError):
    """A stage failed or the database is not ready. Nothing after it ran."""


@dataclass
class PrepareReport:
    corpus_id: str
    seconds: dict[str, float] = field(default_factory=dict)
    rows: dict[str, int] = field(default_factory=dict)
    accelerators: AcceleratorReport = field(default_factory=AcceleratorReport)
    ready: dict[str, int] = field(default_factory=dict)
    vocabulary: Path | None = None
    layout: LayoutReport | None = None


def prepare(
    bundle: Path | str,
    dsn: str,
    *,
    replace: bool = False,
    rows_per_cluster: int = 2000,
    tolerance: float = 0.0,
    allow_skipped: bool = True,
    vocabulary_to: Path | str | None = None,
    work: Path | str | None = None,
    on_step: Callable[[str, str], None] | None = None,
    on_rows: Callable[[str, int], None] | None = None,
    on_note: Callable[[str], None] | None = None,
) -> PrepareReport:
    """Run every stage; return only when the database reports itself ready.

    `allow_skipped` defaults to true because the preflight checks that need a live embedder
    (dimension, consistency, canary) cannot run before there is a database. They are not
    lost: the canary is written by the load, and the engine refuses to start on it.
    """
    from bundle.writer import read_bundle
    from load import LoadError, execute, preflight

    root = Path(bundle)
    note = on_note or (lambda _msg: None)
    document: dict[str, Any] = read_bundle(root)
    report = PrepareReport(corpus_id=str(document["corpus_id"]))

    def timed(name: str, started: float) -> None:
        report.seconds[name] = time.time() - started

    started = time.time()
    checks = preflight(
        root, free_bytes=shutil.disk_usage(root).free, failure_tolerance=tolerance
    )
    if checks.failed:
        failed = "; ".join(
            f"{c.name}: {c.detail}" for c in checks.checks if c.status.value == "fail"
        )
        raise PrepareError(f"preflight failed, this bundle must not be loaded: {failed}")
    if checks.skipped and not allow_skipped:
        raise PrepareError(
            f"{len(checks.skipped)} preflight checks could not run: "
            + ", ".join(c.name for c in checks.skipped)
        )
    timed("preflight", started)

    owned = work is None
    workdir = Path(tempfile.mkdtemp(prefix="ravel-prepare-")) if owned else Path(str(work))
    try:
        started = time.time()
        placement = place(root, workdir, rows_per_cluster=rows_per_cluster, on_progress=note)
        timed("place", started)

        started = time.time()
        try:
            loaded = execute(
                root,
                dsn,
                replace=replace,
                include_indexes=True,
                placement=placement,
                on_step=on_step,
                on_rows=on_rows,
            )
        except LoadError as exc:
            raise PrepareError(str(exc)) from exc
        report.rows = dict(loaded.rows)
        timed("load", started)
    finally:
        if owned:
            shutil.rmtree(workdir, ignore_errors=True)

    started = time.time()
    report.accelerators = accelerate(dsn)
    timed("accelerate", started)

    # ! Required, ✗ an accelerator. Without it the dense arm still answers, from `chunks`,
    # but a cold probe is ~140x slower (`db.layout`), and that is the difference between
    # routing holding at scale and not.
    import psycopg

    started = time.time()
    with psycopg.connect(dsn, autocommit=True) as conn:
        report.layout = build_layout(conn)
    timed("layout", started)

    if vocabulary_to is not None and (root / "corpus.bm25.json").exists():
        target = Path(str(vocabulary_to))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / "corpus.bm25.json", target)
        report.vocabulary = target

    started = time.time()
    try:
        report.ready = verify_ready(
            dsn, json.loads((root / "manifest.json").read_text("utf-8"))
        )
    except VerifyError as exc:
        raise PrepareError(f"the database is not ready: {exc}") from exc
    timed("verify", started)
    return report
