"""The command line — the primary face (INTERFACES.md §1).

It must work with nothing else running: no MCP server, no HTTP endpoint, no
orchestrator. Stages are added here as they land.

Parameters use `Annotated[...]` rather than call-in-default: it is Typer's current
idiom, it keeps the annotation honest, and it does not trip B008.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table as RichTable

from canon import CANON_VERSION, Severity, iter_shard, validate
from extract import Extraction
from runtime import Ledger
from sources import guess_mime, human_bytes, scan, write_manifest
from spec import CorpusSpec, default_registry, registry_root

app = typer.Typer(
    name="ravel",
    help="Compile documents into a verifiable corpus bundle.",
    no_args_is_help=True,
    add_completion=False,
)
canon_app = typer.Typer(help="Inspect and validate canonical documents.", no_args_is_help=True)
sources_app = typer.Typer(help="Discover and hash source documents.", no_args_is_help=True)
profiles_app = typer.Typer(help="Inspect the document profile registry.", no_args_is_help=True)
bundle_app = typer.Typer(help="Inspect and verify corpus bundles.", no_args_is_help=True)
eval_app = typer.Typer(help="Labeled sets and variant scoring.", no_args_is_help=True)
variants_app = typer.Typer(help="Variant configurations and cache keys.", no_args_is_help=True)
app.add_typer(canon_app, name="canon")
app.add_typer(sources_app, name="sources")
app.add_typer(profiles_app, name="profiles")
app.add_typer(bundle_app, name="bundle")
app.add_typer(eval_app, name="eval")
app.add_typer(variants_app, name="variants")

WORKSPACE = Annotated[Path, typer.Option("--workspace", "-w", help="Where artifacts go.")]
CorpusArg = Annotated[str, typer.Argument(help="Corpus id from corpora/.")]

# ! Force UTF-8 on the streams before anything writes to them.
#
# Windows' legacy console is cp1252, and rich's legacy-console renderer writes through it
# character by character. A single character outside that codepage — U+2192 in a step note,
# a typographic dash in a check's detail — raises UnicodeEncodeError *mid-render*, so the
# command dies with a traceback after printing half its output. The text was correct; the
# terminal could not spell it. `errors="replace"` degrades to a question mark on a console
# that cannot show the glyph, which is the right trade for a tool that has to run on a
# Windows workstation and a Linux GPU box with the same code (`STACK.md` §1).
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

err = Console(stderr=True)
out = Console()

ShardArg = Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)]
RootArg = Annotated[Path, typer.Argument(exists=True, file_okay=False, readable=True)]
ProfileArg = Annotated[str, typer.Argument(help="Profile id or id@version.")]


@app.command()
def version() -> None:
    """Print component versions."""
    out.print(f"ravel 0.0.1 · canon {CANON_VERSION} · python {sys.version.split()[0]}")


@profiles_app.command("list")
def profiles_list() -> None:
    """List the document profiles in the registry."""
    table = RichTable(show_header=True, header_style="bold")
    for column in ("profile", "hash", "units", "types", "mime", "title"):
        table.add_column(column, overflow="fold")
    for profile in sorted(default_registry(), key=lambda p: p.ref):
        table.add_row(
            profile.ref,
            profile.config_hash,
            str(len(profile.units)),
            str(len(profile.spec.identity.types)),
            str(len(profile.spec.match.mime)),
            profile.spec.title,
        )
    out.print(table)
    out.print(f"[dim]{registry_root()}[/dim]")


@profiles_app.command("show")
def profiles_show(ref: ProfileArg) -> None:
    """Show one profile's structural units and identity patterns."""
    profile = default_registry().get(ref)
    out.print(
        f"[bold]{profile.ref}[/bold]  {profile.spec.title}  [dim]{profile.config_hash}[/dim]"
    )
    if profile.spec.description:
        out.print(f"[dim]{profile.spec.description.strip()}[/dim]")

    table = RichTable(show_header=True, header_style="bold")
    for column in ("unit", "level", "marker only", "pattern"):
        table.add_column(column, overflow="fold")
    for unit in profile.units:
        table.add_row(
            unit.name,
            str(unit.level),
            "yes" if unit.marker_only else "",
            unit.pattern.pattern,
        )
    out.print(table)


@profiles_app.command("try")
def profiles_try(
    ref: ProfileArg,
    lines: Annotated[list[str], typer.Argument(help="Lines to classify.")],
) -> None:
    """Classify lines against a profile — the fast loop when writing patterns."""
    profile = default_registry().get(ref)
    table = RichTable(show_header=True, header_style="bold")
    table.add_column("line", overflow="fold")
    table.add_column("unit")
    table.add_column("level")
    for line in lines:
        unit = profile.structural(line)
        table.add_row(
            line,
            f"[green]{unit.name}[/green]" if unit else "[dim]—[/dim]",
            str(unit.level) if unit else "",
        )
    out.print(table)


@profiles_app.command("route")
def profiles_route(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
) -> None:
    """Show which profile a file would be assigned, and why."""
    mime = guess_mime(path)
    try:
        sample = path.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        sample = ""
    registry = default_registry()
    chosen = registry.route(mime=mime, sample=sample)

    table = RichTable(show_header=True, header_style="bold")
    for column in ("profile", "score", "chosen"):
        table.add_column(column)
    for profile in sorted(registry, key=lambda p: p.ref):
        score = profile.score(mime=mime, sample=sample)
        table.add_row(
            profile.ref,
            "—" if score is None else str(score),
            "[green]<--[/green]" if chosen and profile.ref == chosen.ref else "",
        )
    out.print(f"{path.name}  [dim]{mime}[/dim]")
    out.print(table)


@sources_app.command("scan")
def sources_scan(
    root: RootArg,
    manifest: Annotated[
        Path | None, typer.Option("--manifest", "-m", help="Where to write the JSONL.")
    ] = None,
    pattern: Annotated[
        list[str] | None, typer.Option("--pattern", "-p", help="Glob; repeatable.")
    ] = None,
    include_hidden: Annotated[bool, typer.Option("--include-hidden")] = False,
) -> None:
    """Discover source files, hash them, and write the source manifest.

    Sources are read-only: this never modifies, renames or deletes anything.
    """
    files, report = scan(root, pattern or ["**/*"], skip_hidden=not include_hidden)

    summary = RichTable(show_header=False, box=None)
    summary.add_row("files", f"{report.files:,}")
    summary.add_row("bytes", human_bytes(report.bytes))
    summary.add_row("unique", f"{report.unique:,}")
    summary.add_row("duplicates", f"{report.duplicates:,} ({report.duplicate_ratio:.1%})")
    summary.add_row("unreadable", f"{len(report.unreadable):,}")
    summary.add_row(
        "extensions",
        "  ".join(f"{ext} {n:,}" for ext, n in list(report.by_extension.items())[:6]),
    )
    out.print(summary)

    for problem in report.unreadable[:10]:
        err.print(f"[yellow]unreadable[/yellow] {problem}")
    if len(report.unreadable) > 10:
        err.print(f"[yellow]... and {len(report.unreadable) - 10} more[/yellow]")

    if manifest:
        written = write_manifest(files, manifest)
        out.print(f"[bold]{written:,}[/bold] entries -> {manifest}")


@app.command()
def extract(
    corpus_id: CorpusArg,
    workspace: WORKSPACE = Path("."),
    limit: Annotated[int, typer.Option("--limit", help="Stop after N documents.")] = 0,
    force: Annotated[bool, typer.Option("--force", help="Ignore the ledger.")] = False,
) -> None:
    """Phase A: source documents into canonical parquet shards.

    Resumable. Kill it at any point and run it again: it processes only what the
    ledger has not recorded, losing at most the unsealed shard.
    """
    spec = CorpusSpec.find(corpus_id)
    run = Extraction(spec, workspace=workspace)
    report = run.run(limit=limit, force=force)

    summary = RichTable(show_header=False, box=None)
    summary.add_row("corpus", f"{spec.id}  [dim]{spec.config_hash}[/dim]")
    summary.add_row("generation", f"[dim]{run.generation}[/dim]")
    summary.add_row("considered", f"{report.considered:,}")
    summary.add_row("already done", f"{report.skipped_done:,}")
    if report.duplicates:
        summary.add_row("duplicate bytes", f"{report.duplicates:,}")
    summary.add_row("extracted", f"[bold]{report.extracted:,}[/bold]")
    summary.add_row("failed", f"{report.failed:,}  ({report.failure_rate:.1%})")
    summary.add_row(
        "deferred", f"{report.deferred:,}  [dim]no text layer · awaiting OCR[/dim]"
    )
    summary.add_row("coverage", f"{report.coverage:.1%}")
    summary.add_row("shards", f"{report.shards:,}  ({report.bytes_written / 1e6:.1f} MB)")
    summary.add_row("elapsed", f"{report.seconds:.1f}s")
    if report.by_extractor:
        summary.add_row(
            "extractors", "  ".join(f"{k} {v:,}" for k, v in report.by_extractor.items())
        )
    if report.by_profile:
        summary.add_row(
            "profiles", "  ".join(f"{k} {v:,}" for k, v in report.by_profile.items())
        )
    out.print(summary)

    for path, reason in report.errors[:10]:
        err.print(f"[yellow]{path}[/yellow] {reason}")
    if len(report.errors) > 10:
        err.print(f"[yellow]... and {len(report.errors) - 10} more[/yellow]")

    # ! Completeness is asserted, never assumed. A corpus silently missing documents
    # is the failure this check exists to prevent (LOOPHOLES.md §3).
    tolerance = spec.extract.failure_tolerance
    if not report.within(tolerance):
        err.print(
            f"[red]failure rate {report.failure_rate:.1%} exceeds the corpus "
            f"tolerance of {tolerance:.1%}[/red]"
        )
        raise typer.Exit(1)


@app.command(name="chunk")
def chunk_cmd(
    corpus_id: CorpusArg,
    workspace: WORKSPACE = Path("."),
    chunker: Annotated[
        str, typer.Option("--chunker", help="Override the corpus chunker.")
    ] = "",
    variant: Annotated[
        str, typer.Option("--variant", help="Name this chunking. Variants live side by side.")
    ] = "default",
    limit: Annotated[int, typer.Option("--limit", help="Stop after N documents.")] = 0,
    force: Annotated[bool, typer.Option("--force", help="Ignore the ledger.")] = False,
) -> None:
    """Phase B: canonical documents into chunks with immutable provenance.

    Reads the canonical shards `extract` produced — never the original files. Cheap
    and re-runnable by design, which is what makes a chunking experiment a command
    rather than a project.
    """
    import chunk as chunking

    spec = CorpusSpec.find(corpus_id)
    run = chunking.Chunking(
        spec, workspace=workspace, chunker=chunker or None, variant=variant
    )
    report = run.run(limit=limit, force=force)

    summary = RichTable(show_header=False, box=None)
    summary.add_row("corpus", f"{spec.id}  [dim]{report.variant}[/dim]")
    summary.add_row(
        "chunker", f"{run.entry.ref}  [dim]{run.config.config_hash}[/dim]"
    )
    summary.add_row("documents", f"{report.documents:,}")
    summary.add_row("already done", f"{report.skipped_done:,}")
    if report.duplicates:
        summary.add_row("duplicates", f"{report.duplicates:,}")
    summary.add_row("chunks", f"[bold]{report.chunks:,}[/bold]")
    summary.add_row("per document", f"{report.chunks_per_doc:.1f}")
    summary.add_row("median tokens", f"{report.median_tokens:,}")
    summary.add_row("with locator", f"{report.section_rate:.1%}")
    summary.add_row("with identifier", f"{report.identifier_rate:.1%}")
    summary.add_row("empty documents", f"{report.empty:,}")
    summary.add_row("failed", f"{report.failed:,}")
    summary.add_row("shards", f"{report.shards:,}  ({report.bytes_written / 1e6:.1f} MB)")
    summary.add_row("elapsed", f"{report.seconds:.1f}s")
    out.print(summary)

    for path, reason in report.errors[:10]:
        err.print(f"[yellow]{path}[/yellow] {reason}")
    if report.empty:
        err.print(
            f"[yellow]{report.empty:,} documents produced no chunks · "
            f"they are recorded, not silently dropped[/yellow]"
        )


@app.command()
def status(corpus_id: CorpusArg, workspace: WORKSPACE = Path(".")) -> None:
    """What has been done, and what a rerun would pick up."""
    spec = CorpusSpec.find(corpus_id)
    run = Extraction(spec, workspace=workspace)

    table = RichTable(show_header=False, box=None)
    table.add_row("corpus", spec.id)
    table.add_row("canon", str(run.canon_dir))
    shards = sorted(run.canon_dir.glob("part-*.parquet")) if run.canon_dir.is_dir() else []
    table.add_row("shards", f"{len(shards):,}")
    if not run.ledger_path.exists():
        table.add_row("ledger", "[dim]nothing recorded yet[/dim]")
        out.print(table)
        return
    with Ledger(run.ledger_path) as ledger:
        counts = ledger.counts("extract")
        for name, value in counts.items():
            table.add_row(name, f"{value:,}")
        out.print(table)
        for entry in ledger.failures("extract", limit=10):
            err.print(f"[yellow]{entry.status.value}[/yellow] {entry.detail}")


BundleArg = Annotated[Path, typer.Argument(exists=True, file_okay=False, readable=True)]
QueriesArg = Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)]


@eval_app.command("check")
def eval_check(
    queries_path: QueriesArg,
    holdout: Annotated[
        float, typer.Option("--holdout", help="Share to reserve from sweeps.")
    ] = 0.2,
) -> None:
    """Validate a labeled set before it is used to decide anything.

    The set is the only ground truth this project has, and it is the cheapest thing to get
    wrong. Exits non-zero if it is too small or its composition has drifted — both of
    which change what the headline number means without changing how it looks.
    """
    from evaluate import QuerySet, check_composition, check_size

    queries = QuerySet.load(queries_path)
    sweep, held = queries.split(holdout=holdout)

    table = RichTable(show_header=False, box=None)
    table.add_row("queries", f"{len(queries):,}")
    table.add_row("version", queries.version)
    for kind, count in queries.composition().items():
        share = count / len(queries) if len(queries) else 0.0
        table.add_row(f"  {kind}", f"{count:,}  [dim]{share:.0%}[/dim]")
    table.add_row("sweep / holdout", f"{len(sweep):,} / {len(held):,}")
    out.print(table)

    failed = False
    for ok, detail in (check_size(queries), check_composition(queries)):
        if ok:
            out.print(f"[green]ok  [/green] {detail}")
        else:
            err.print(f"[red]FAIL[/red] {detail}")
            failed = True
    if failed:
        raise typer.Exit(1)


@variants_app.command("plan")
def variants_plan(
    corpus_id: CorpusArg,
    workspace: WORKSPACE = Path("."),
) -> None:
    """Show the corpus's default variant, its cache keys, and what each stage costs.

    The keys are the reuse policy: two variants sharing a key share that stage's artifacts,
    and nothing else may (`VARIANTS.md` §4).
    """
    from variants import STAGES, VariantSpec

    spec = CorpusSpec.find(corpus_id)
    variant = VariantSpec(
        corpus_id=spec.id,
        name="default",
        config={
            "extract": {
                "profile": spec.extract.profile,
                "extractors": list(spec.extract.extractors),
                "native_text_ratio": spec.extract.native_text_ratio,
            },
            "chunk": {
                "chunker": spec.chunk.chunker,
                "max_tokens": spec.chunk.max_tokens,
                "min_tokens": spec.chunk.min_tokens,
            },
            "enrich": {},
            "embed": {},
            "cluster": {},
        },
    )

    table = RichTable(show_header=True, header_style="bold")
    table.add_column("stage")
    table.add_column("key")
    table.add_column("parameters", overflow="fold")
    for stage in STAGES:
        params = variant.config[stage]
        table.add_row(
            stage,
            f"[dim]{variant.stage_key(stage)}[/dim]",
            "  ".join(f"{k}={v}" for k, v in params.items()) or "[dim]—[/dim]",
        )
    out.print(f"[bold]{spec.id}[/bold] / {variant.name}  [dim]{variant.config_hash}[/dim]")
    out.print(table)
    out.print(f"[dim]{workspace.resolve()}[/dim]")


@bundle_app.command("show")
def bundle_show(path: BundleArg) -> None:
    """The recipe and the inventory of a sealed bundle.

    Two hashes, two questions: the recipe answers "are these two bundles the same build?",
    the inventory answers "did this one arrive intact?".
    """
    from bundle import read_bundle

    document = read_bundle(path)
    dense = document["dense"]
    inventory = document.get("inventory", {})

    table = RichTable(show_header=False, box=None)
    table.add_row("corpus", f"{document['corpus_id']}  [dim]run {document['run_id']}[/dim]")
    table.add_row("recipe", f"[dim]{document['manifest_sha256']}[/dim]")
    table.add_row("chunks", f"{document['chunk_count']:,}")
    table.add_row(
        "dense",
        f"{dense['model']} @ {dense['model_version']} · {dense['dim']}d · "
        f"{dense['pooling']} · {dense['padding_side']} pad",
    )
    sparse = document.get("sparse")
    if sparse:
        table.add_row(
            "sparse",
            f"{sparse['scheme']} · {sparse['dim']}d · fitted over "
            f"{sparse['fit_docs']:,} docs",
        )
    clustering = document.get("clustering") or {}
    if clustering:
        table.add_row(
            "clustering",
            f"{clustering.get('algo', '?')} · k={clustering.get('k', '?')} · "
            f"generation {clustering.get('generation', '?')}",
        )
    table.add_row("files", f"{inventory.get('files', 0):,}")
    if not document.get("provenance_complete", True):
        table.add_row("provenance", "[yellow]incomplete — results are not citable[/yellow]")
    out.print(table)

    counts = RichTable(show_header=True, header_style="bold")
    counts.add_column("file", overflow="fold")
    counts.add_column("rows", justify="right")
    for name, rows in sorted(inventory.get("counts", {}).items()):
        counts.add_row(name, f"{rows:,}" if rows else "—")
    out.print(counts)


@bundle_app.command("verify")
def bundle_verify(
    path: BundleArg,
    target_dim: Annotated[
        int, typer.Option("--dim", help="Width of the target dense column.")
    ] = 0,
    tolerance: Annotated[
        float, typer.Option("--tolerance", help="Share of failed documents allowed.")
    ] = 0.0,
    allow_skipped: Annotated[
        bool,
        typer.Option("--allow-skipped", help="Pass when a check could not run at all."),
    ] = False,
    allow_incomplete_provenance: Annotated[
        bool, typer.Option("--allow-incomplete-provenance")
    ] = False,
) -> None:
    """Run the load preflight. Exits non-zero if the bundle must not load.

    The canary needs a live query-side embedder and so is reported as skipped here; a
    skipped check does not pass unless `--allow-skipped` says so.
    """
    import shutil

    from load import preflight

    report = preflight(
        path,
        target_dim=target_dim or None,
        free_bytes=shutil.disk_usage(path).free,
        failure_tolerance=tolerance,
        allow_incomplete_provenance=allow_incomplete_provenance,
    )
    for check in report.checks:
        style = {"ok": "green", "fail": "red", "skipped": "yellow"}[check.status.value]
        out.print(f"[{style}]{check.status.value:<8}[/{style}] {check.name}: {check.detail}")

    if report.failed:
        err.print("[red]preflight failed — this bundle must not be loaded[/red]")
        raise typer.Exit(1)
    if report.skipped and not allow_skipped:
        err.print(
            f"[yellow]{len(report.skipped)} checks could not run "
            f"({', '.join(c.name for c in report.skipped)}). A skipped check is not a "
            f"passed one — supply its input, or accept the gap with "
            f"--allow-skipped.[/yellow]"
        )
        raise typer.Exit(1)
    out.print("[green]preflight passed[/green]")


@bundle_app.command("plan")
def bundle_plan(
    path: BundleArg,
    show_sql: Annotated[bool, typer.Option("--sql", help="Print each statement.")] = False,
) -> None:
    """Print the load procedure without running it.

    A procedure you can read is a procedure someone can review before it runs against a
    database holding a corpus that took GPU-days to build.
    """
    from bundle import read_bundle
    from load import load_plan

    steps = load_plan(read_bundle(path), path)
    for i, step in enumerate(steps, 1):
        marker = "" if step.transactional else "  [dim](outside the transaction)[/dim]"
        out.print(f"[bold]{i:>2}. {step.name}[/bold]{marker}")
        if step.note:
            out.print(f"    [dim]{step.note}[/dim]")
        if show_sql:
            out.print(f"[dim]{step.sql}[/dim]")


@canon_app.command("validate")
def canon_validate(
    shard: ShardArg,
    min_text_ratio: Annotated[
        float,
        typer.Option(
            "--min-text-ratio",
            min=0.0,
            max=1.0,
            help="Floor below which an extraction is presumed to have failed silently.",
        ),
    ] = 0.30,
    show_warnings: Annotated[
        bool, typer.Option("--warnings", help="Report warnings too.")
    ] = False,
    limit: Annotated[int, typer.Option("--limit", help="Stop after N documents.")] = 0,
) -> None:
    """Validate every canonical document in a parquet shard.

    Exits non-zero if any document has an error, so this is usable as a gate.
    """
    checked = failed = 0
    rows: list[tuple[str, str, str]] = []

    for doc in iter_shard(str(shard)):
        if limit and checked >= limit:
            break
        checked += 1
        issues = validate(doc, min_text_ratio=min_text_ratio)
        if not show_warnings:
            issues = [i for i in issues if i.severity is Severity.ERROR]
        if any(i.severity is Severity.ERROR for i in issues):
            failed += 1
        rows.extend((doc.doc_id[7:19], issue.severity.value, str(issue)) for issue in issues)

    if rows:
        table = RichTable(show_header=True, header_style="bold")
        table.add_column("doc", style="dim", no_wrap=True)
        table.add_column("issue")
        for doc_id, severity, text in rows:
            style = "red" if severity == "error" else "yellow"
            table.add_row(doc_id, f"[{style}]{text}[/{style}]")
        err.print(table)

    summary = f"{checked} documents checked · {failed} with errors"
    (err.print if failed else out.print)(f"[bold]{summary}[/bold]")
    raise typer.Exit(1 if failed else 0)


@canon_app.command("show")
def canon_show(
    shard: ShardArg,
    doc_id: Annotated[
        str | None, typer.Option("--doc", help="Show one document by id or prefix.")
    ] = None,
) -> None:
    """Outline the documents in a shard: structure, not content."""
    table = RichTable(show_header=True, header_style="bold")
    for column in ("doc", "title", "extractor", "pages", "blocks", "tables"):
        table.add_column(column, overflow="fold")

    for doc in iter_shard(str(shard)):
        if doc_id and doc_id not in doc.doc_id:
            continue
        table.add_row(
            doc.doc_id[7:19],
            doc.source.title,
            f"{doc.extraction.extractor}@{doc.extraction.extractor_version}",
            str(doc.extraction.pages or "-"),
            str(len(doc.blocks)),
            str(len(doc.tables)),
        )
    out.print(table)


if __name__ == "__main__":
    app()
