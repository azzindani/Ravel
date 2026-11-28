"""Docling extraction — layout-aware reading of PDFs, on a GPU when there is one.

`EXTRACTION.md` §2b. One extractor, two docling pipelines, picked per document by the
probe's text-layer verdict (`Probe.layer`), configured by the corpus (`DoclingSpec`):

- **CLEAN** text layer → docling's *standard* pipeline. The born-digital text is kept
  and docling's layout model and TableFormer add what PyMuPDF cannot see: reading order
  across columns, tables as cells with spans, page furniture as furniture.
- **DAMAGED / ABSENT** → the *VLM* pipeline. A vision-language model (granite-docling,
  SmolDocling's successor) re-reads the rendered page. A damaged layer is somebody
  else's old OCR (`ABSORPTION.md` §16); keeping it keeps their typos.

What this extractor reports is **what is on the page**; what it means stays with the
structurer and the profile (§4b). Docling's section headers are therefore emitted as
headings with docling's level, and a profile with real patterns demotes the ones that
are not structure, exactly as it does for the native extractor's typographic guesses.

! Docling is imported inside `extract()`, never at module load: the hermetic suite and
every corpus that does not name `docling` must not pay for torch.
"""

from __future__ import annotations

import queue
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import AnyUrl

from canon import Asset, BlockType, CanonicalDoc, Cell, Extraction, Source, Table
from extract.base import BlockBuilder, ExtractionFailed, Probe
from extract.quality import TextLayer
from sources import guess_mime, sha256_file
from spec.corpus import LANES

if TYPE_CHECKING:
    from spec.corpus import DoclingSpec
    from spec.models import Profile

#: Docling label → canonical block type. Anything unlisted is body text.
LABELS: dict[str, BlockType] = {
    "title": BlockType.HEADING,
    "section_header": BlockType.HEADING,
    "list_item": BlockType.LIST_ITEM,
    "caption": BlockType.CAPTION,
    "footnote": BlockType.FOOTNOTE,
    "page_header": BlockType.PAGE_HEADER,
    "page_footer": BlockType.PAGE_FOOTER,
    "formula": BlockType.FORMULA,
}


def pipeline_for(layer: TextLayer, spec: DoclingSpec) -> str:
    """Which docling pipeline reads a document whose text layer scored `layer`."""
    return spec.clean if layer is TextLayer.CLEAN else spec.damaged


class DoclingExtractor:
    id = "docling"
    # 1.1: table and figure ref blocks carry no caption text (1.0's failed the round trip),
    # and the VLM path reads page ranges in parallel. Every 1.0 document is superseded.
    version = "1.1"

    def __init__(self, spec: DoclingSpec) -> None:
        self.spec = spec
        self._converters: dict[str, Any] = {}
        self._building = threading.Lock()
        # ! Per thread: with `extract.workers` > 1 the router's probe for one document
        # must never be read back by the thread extracting another.
        self._local = threading.local()
        # ! A lane is one document in flight on one pipeline, with converters of its own:
        # `standard_lanes` and `vlm_lanes`, whatever `extract.workers` is. On the 8 GB
        # laptop both are 1: two standard reads load the layout and table models twice
        # beside the VLM server, two VLM documents double the requests queued behind one
        # KV cache, which is what times a page out. One of each overlaps the two pieces
        # of hardware. A free lane's number is taken from the queue and given back.
        self._lanes: dict[str, queue.Queue[int]] = {}
        for pipeline, count in (("standard", spec.standard_lanes), ("vlm", spec.vlm_lanes)):
            self._lanes[pipeline] = queue.Queue()
            for lane in range(count):
                self._lanes[pipeline].put(lane)

    # -- routing ------------------------------------------------------------
    def supports(self, path: Path, probe: Probe) -> bool:
        # The router asks `supports` right before `extract`, with the probe it already
        # paid for; keeping it saves re-reading a 400-page PDF to choose a pipeline.
        self._local.probed = (path, probe)
        return probe.mime == "application/pdf" and bool(probe.pages)

    # -- docling ------------------------------------------------------------
    def _device(self) -> Any:
        from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions

        device = {
            "auto": AcceleratorDevice.AUTO,
            "cuda": AcceleratorDevice.CUDA,
            "cpu": AcceleratorDevice.CPU,
        }[self.spec.device]
        return AcceleratorOptions(device=device)

    def _vlm_options(self) -> Any:
        from docling.datamodel.pipeline_options import VlmConvertOptions

        if self.spec.vlm_engine == "auto":
            options = VlmConvertOptions.from_preset(self.spec.vlm_preset)
        else:
            from docling.datamodel import vlm_engine_options as eng
            from docling.models.inference_engines.vlm.base import VlmEngineType

            if self.spec.vlm_engine == "api":
                if not self.spec.vlm_url:
                    raise ValueError("docling.vlm_engine 'api' needs docling.vlm_url")
                engine: Any = eng.ApiVlmEngineOptions(
                    engine_type=VlmEngineType.API,
                    url=AnyUrl(self.spec.vlm_url),
                    concurrency=self.spec.vlm_concurrency,
                    timeout=self.spec.vlm_timeout_s,
                    # ! DocTags ARE special tokens. vLLM strips them by default, and
                    # docling then parses every page as empty while every request
                    # returns 200: measured 0 characters for 3 pages, against 7,219
                    # from the in-process engine. Kept, the two agree at 0.9992.
                    params={
                        "skip_special_tokens": False,
                        "max_tokens": self.spec.vlm_max_tokens,
                    },
                )
            elif self.spec.vlm_engine == "vllm":
                engine = eng.VllmVlmEngineOptions()
            else:
                engine = eng.TransformersVlmEngineOptions()
            options = VlmConvertOptions.from_preset(self.spec.vlm_preset, engine_options=engine)
        options.batch_size = self.spec.vlm_batch
        options.scale = self.spec.images_scale
        return options

    def converter(self, pipeline: str, *, slot: int | str = 0) -> Any:
        """One converter per pipeline and worker slot, built once: model weights load on
        first use. ! A slot each, because one converter is not shared between threads."""
        key = f"{pipeline}:{slot}"
        with self._building:
            if key not in self._converters:
                self._converters[key] = self._build(pipeline)
            return self._converters[key]

    def _build(self, pipeline: str) -> Any:

        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, VlmPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        if pipeline == "vlm":
            from docling.pipeline.vlm_pipeline import VlmPipeline

            fmt = PdfFormatOption(
                pipeline_cls=VlmPipeline,
                pipeline_options=VlmPipelineOptions(
                    vlm_options=self._vlm_options(),
                    accelerator_options=self._device(),
                    # Docling refuses to send pages anywhere unless told it may.
                    enable_remote_services=self.spec.vlm_engine == "api",
                ),
            )
        else:
            # ! OCR off: the standard pipeline is chosen only for a CLEAN layer, and a
            # clean layer is the text. OCR on top would replace good bytes with guesses.
            fmt = PdfFormatOption(
                pipeline_options=PdfPipelineOptions(
                    do_ocr=False,
                    do_table_structure=self.spec.tables,
                    accelerator_options=self._device(),
                    images_scale=self.spec.images_scale,
                )
            )
        return DocumentConverter(format_options={InputFormat.PDF: fmt})

    def _ranges(self, pipeline: str, pages: int) -> list[tuple[int, int] | None]:
        """The page ranges one document is read in: the whole of it, or for the VLM path
        `vlm_pages_per_call` pages at a time, so `vlm_workers` ranges are in flight at once.

        ! Docling keeps at most 3-4 pages in flight on its own and waits for each batch to
        finish, so the server idled between pages: 1.9 s a page at any `vlm_batch`
        (EXTRACTION.md §2b). Ranges in parallel brought it to 1.0 s on the same pages.
        """
        span = self.spec.vlm_pages_per_call
        if pipeline != "vlm" or self.spec.vlm_workers <= 1 or pages <= span:
            return [None]
        return [(start, min(start + span - 1, pages)) for start in range(1, pages + 1, span)]

    def _convert(self, path: Path, pipeline: str, pages: int) -> tuple[list[Any], str | None]:
        """Docling's results, in page order, and, when any range failed, why."""
        from concurrent.futures import ThreadPoolExecutor

        from docling.datamodel.base_models import ConversionStatus

        ranges = self._ranges(pipeline, pages)
        workers = min(self.spec.vlm_workers, len(ranges))

        def one(index_range: tuple[int, tuple[int, int] | None]) -> Any:
            index, page_range = index_range
            converter = self.converter(pipeline, slot=f"{lane}.{index % workers}")
            if page_range is None:
                return converter.convert(path, raises_on_error=False)
            return converter.convert(path, raises_on_error=False, page_range=page_range)

        lane = self._lanes[pipeline].get()
        try:
            if workers <= 1:
                results = [one((0, r)) for r in ranges]
            else:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    results = list(pool.map(one, enumerate(ranges)))
        except Exception as exc:  # noqa: BLE001 — docling raises anything; recorded
            return [], f"{type(exc).__name__}: {exc}"
        finally:
            self._lanes[pipeline].put(lane)
        failed = [r for r in results if r.status is ConversionStatus.FAILURE]
        if failed:
            errors = "; ".join(str(e.error_message) for r in failed for e in r.errors[:2])
            return results, f"{len(failed)} of {len(results)} ranges: {errors or 'no reason'}"
        return results, None

    # -- the extractor ------------------------------------------------------
    def extract(
        self,
        path: Path,
        *,
        title: str,
        url: str | None = None,
        profile: Profile | None = None,
        probe: Probe | None = None,
    ) -> CanonicalDoc:
        from docling.datamodel.base_models import ConversionStatus

        probed: tuple[Path, Probe] | None = getattr(self._local, "probed", None)
        if probe is None and probed and probed[0] == path:
            probe = probed[1]
        if probe is None:
            from extract.native import probe as probe_pdf

            probe = probe_pdf(path)
        pipeline = pipeline_for(probe.layer, self.spec)
        pages = probe.pages or 0

        started = time.monotonic()
        results, failure = self._convert(path, pipeline, pages)
        warnings: list[str] = []
        other = "vlm" if pipeline == "standard" else "standard"
        if failure and self.spec.fallback and other != pipeline:
            # ! A docling fault on one pipeline is a fault of that pipeline, ✗ of the PDF:
            # the standard pipeline's layout stage raised `list index out of range` on a
            # 2026-11-28 regulation the VLM reads. Tried once, recorded, never silent.
            warnings.append(f"docling {pipeline} failed ({failure}); read with {other}")
            pipeline = other
            results, failure = self._convert(path, pipeline, pages)
        if failure or not results:
            raise ExtractionFailed(f"{path.name}: docling failed · {failure or 'no result'}")
        elapsed = time.monotonic() - started

        warnings += [f"docling: {e.error_message}" for r in results for e in r.errors][:20]
        statuses = {r.status for r in results}
        if statuses != {ConversionStatus.SUCCESS}:
            warnings.insert(0, "docling status " + ", ".join(sorted(x.value for x in statuses)))
        if pages and elapsed > self.spec.slow_page_s * pages:
            warnings.append(f"slow: {elapsed:.0f}s for {pages} pages")

        builder, tables, assets = to_canon(*(r.document for r in results))
        if not any(b.text.strip() for b in builder.blocks) and not tables:
            # ! Never an empty success (LOOPHOLES.md §2).
            raise ExtractionFailed(f"{path.name}: docling read {pages} pages and found no text")

        digest = sha256_file(path)
        model = None
        if pipeline == "vlm":
            model = self.spec.vlm_preset
        return CanonicalDoc(
            doc_id=f"sha256:{digest}",
            source=Source(
                path=path.as_posix(),
                url=url,
                title=title,
                mime=guess_mime(path),
                bytes=path.stat().st_size,
                sha256=digest,
            ),
            extraction=Extraction(
                extractor=self.id,
                extractor_version=self.version,
                model=model,
                params={
                    "pipeline": pipeline,
                    "text_layer": probe.layer.value,
                    "docling": _version(),
                    "seconds": round(elapsed, 1),
                    "seconds_per_page": round(elapsed / pages, 3) if pages else None,
                    **self.spec.model_dump(mode="json", exclude=set(LANES)),
                },
                extracted_at=datetime.now(UTC),
                pages=pages or None,
                warnings=warnings,
            ),
            blocks=builder.blocks,
            tables=tables,
            assets=assets,
        )


def _version() -> str:
    from importlib.metadata import version

    return version("docling")


def _bbox(item: Any, doc: Any) -> tuple[int | None, tuple[float, float, float, float] | None]:
    """Page and top-left-origin bbox of an item's first provenance, as native records it."""
    if not getattr(item, "prov", None):
        return None, None
    prov = item.prov[0]
    page = doc.pages.get(prov.page_no)
    box = prov.bbox
    if page is not None and page.size is not None:
        box = box.to_top_left_origin(page_height=page.size.height)
    return prov.page_no, (round(box.l, 2), round(box.t, 2), round(box.r, 2), round(box.b, 2))


def _paragraphs(text: str) -> list[str]:
    """Split on blank lines: a blank line is the canonical block separator."""
    return [part.strip() for part in text.split("\n\n") if part.strip()]


def _table(item: Any, table_id: str, page: int | None, bbox: Any, doc: Any) -> Table:
    data = item.data
    n_rows, n_cols = data.num_rows, data.num_cols
    cells: list[Cell] = []
    seen: set[tuple[int, int]] = set()
    header_rows = 0
    for cell in data.table_cells:
        r, c = cell.start_row_offset_idx, cell.start_col_offset_idx
        if (r, c) in seen or r >= n_rows or c >= n_cols:
            continue
        seen.add((r, c))
        rowspan = max(1, min(cell.end_row_offset_idx, n_rows) - r)
        colspan = max(1, min(cell.end_col_offset_idx, n_cols) - c)
        cells.append(
            Cell(r=r, c=c, rowspan=rowspan, colspan=colspan, text=" ".join(cell.text.split()))
        )
        if cell.column_header:
            header_rows = max(header_rows, r + rowspan)
    caption = item.caption_text(doc) if hasattr(item, "caption_text") else None
    return Table(
        id=table_id,
        page=page,
        bbox=bbox,
        caption=caption or None,
        n_rows=n_rows,
        n_cols=n_cols,
        header_rows=header_rows,
        cells=cells,
    )


def to_canon(*docs: Any) -> tuple[BlockBuilder, list[Table], list[Asset]]:
    """`DoclingDocument`s as one document's canonical blocks, tables and assets, in reading
    order: the page ranges of one PDF, read apart, joined in page order through one builder,
    so the heading stack runs on across them."""
    from docling_core.types.doc.common.content_layer import ContentLayer
    from docling_core.types.doc.items.picture.picture import PictureItem
    from docling_core.types.doc.items.table.table import TableItem

    builder = BlockBuilder()
    tables: list[Table] = []
    assets: list[Asset] = []
    layers = {ContentLayer.BODY, ContentLayer.FURNITURE}

    walked = (
        (part, item)
        for part in docs
        for item, _depth in part.iterate_items(included_content_layers=layers)
    )
    for doc, item in walked:
        label = str(getattr(item.label, "value", item.label))
        page, bbox = _bbox(item, doc)

        if isinstance(item, TableItem):
            table = _table(item, f"t{len(tables):04d}", page, bbox, doc)
            tables.append(table)
            builder.add(
                BlockType.TABLE_REF,
                # ! Empty: a reference block renders as its ref alone, so text here fails the
                # render/parse round trip and the whole document with it (5 of the first 50
                # large PDFs). The caption is `table.caption`, and docling yields it again as
                # its own caption item.
                "",
                page=page,
                bbox=bbox,
                ref=table.id,
                attrs={"label": label},
            )
            continue
        if isinstance(item, PictureItem):
            asset = Asset(
                id=f"a{len(assets):04d}",
                kind="figure",
                page=page,
                bbox=bbox,
                recipe={"render": "crop", "page": page, "bbox": bbox, "origin": "top_left"},
            )
            assets.append(asset)
            builder.add(
                BlockType.FIGURE_REF,
                "",
                page=page,
                bbox=bbox,
                ref=asset.id,
                attrs={"label": label},
            )
            continue

        text = getattr(item, "text", "") or ""
        btype = LABELS.get(label, BlockType.PARAGRAPH)
        level = None
        if btype is BlockType.HEADING:
            # title = 1; docling numbers section headers from 1, so they sit below it.
            level = 1 if label == "title" else min(int(getattr(item, "level", 1) or 1) + 1, 16)
        parts = _paragraphs(text)
        if btype is BlockType.HEADING and len(parts) > 1:
            parts = [" ".join(parts)]
        for part in parts:
            builder.add(btype, part, level=level, page=page, bbox=bbox, attrs={"label": label})

    return builder, tables, assets
