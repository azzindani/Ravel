"""The docling extractor: routing by text layer, the hash it joins, and the mapping.

The mapping tests build a `DoclingDocument` in code, so they need `docling_core` (the
`extract` extra) but never a model, a GPU or a PDF.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from canon import BlockType
from extract.base import Probe
from extract.docling_pdf import DoclingExtractor, pipeline_for
from extract.quality import TextLayer
from spec.corpus import CorpusSpec, DoclingSpec


def test_a_clean_layer_keeps_its_text_and_a_damaged_one_is_reread() -> None:
    """! A damaged layer is somebody else's OCR, baked in (`ABSORPTION.md` §16): the
    standard pipeline would keep its typos, so it goes to the VLM like an absent one."""
    spec = DoclingSpec()
    assert pipeline_for(TextLayer.CLEAN, spec) == "standard"
    assert pipeline_for(TextLayer.DAMAGED, spec) == "vlm"
    assert pipeline_for(TextLayer.ABSENT, spec) == "vlm"
    assert pipeline_for(TextLayer.CLEAN, DoclingSpec(clean="vlm")) == "vlm"


def test_supports_any_pdf_with_pages_and_nothing_else() -> None:
    ex = DoclingExtractor(DoclingSpec())
    pdf, md = Path("a.pdf"), Path("a.md")

    def probe(mime: str, pages: int | None) -> Probe:
        return Probe(mime=mime, pages=pages, text_ratio=0.0, chars=0)

    assert ex.supports(pdf, probe("application/pdf", 3))
    assert not ex.supports(pdf, probe("application/pdf", 0))
    assert not ex.supports(md, probe("text/markdown", None))


def test_docling_settings_change_the_hash_only_where_docling_runs() -> None:
    """! Every docling knob reaches a document, so it is in the extraction hash
    (`LOOPHOLES.md` §1) — but only for a corpus that names docling, or every existing
    generation would be invalidated by a field it never reads."""
    pdf = CorpusSpec.find("id_pdf")
    changed = pdf.model_copy(deep=True)
    changed.extract.docling.vlm_preset = "smoldocling"
    assert changed.extraction_hash != pdf.extraction_hash

    native = CorpusSpec.find("id_legal")
    moved = native.model_copy(deep=True)
    moved.extract.docling.vlm_preset = "smoldocling"
    assert moved.extraction_hash == native.extraction_hash


def _document():  # type: ignore[no-untyped-def]
    dc = pytest.importorskip("docling_core.types.doc")
    doc = dc.DoclingDocument(name="t")
    doc.add_page(page_no=1, size=dc.Size(width=600, height=800))

    def prov(t: float, b: float):  # type: ignore[no-untyped-def]
        box = dc.BoundingBox(l=50, t=t, r=550, b=b, coord_origin=dc.CoordOrigin.BOTTOMLEFT)
        return dc.ProvenanceItem(page_no=1, bbox=box, charspan=(0, 0))

    doc.add_text(
        dc.DocItemLabel.PAGE_HEADER,
        "BERITA DAERAH",
        prov=prov(790, 780),
        content_layer=dc.ContentLayer.FURNITURE,
    )
    doc.add_title("PERATURAN BUPATI SLEMAN", prov=prov(760, 740))
    doc.add_heading("BAB I", level=1, prov=prov(700, 690))
    doc.add_heading("Pasal 1", level=2, prov=prov(680, 670))
    doc.add_text(dc.DocItemLabel.TEXT, "Ayat satu.\n\nAyat dua.", prov=prov(660, 640))
    cells = [
        dc.TableCell(
            text="No",
            start_row_offset_idx=0,
            end_row_offset_idx=1,
            start_col_offset_idx=0,
            end_col_offset_idx=1,
            column_header=True,
        ),
        dc.TableCell(
            text="Tarif",
            start_row_offset_idx=0,
            end_row_offset_idx=1,
            start_col_offset_idx=1,
            end_col_offset_idx=2,
            column_header=True,
        ),
        dc.TableCell(
            text="1",
            start_row_offset_idx=1,
            end_row_offset_idx=2,
            start_col_offset_idx=0,
            end_col_offset_idx=1,
        ),
        dc.TableCell(
            text="Rp 5.000",
            start_row_offset_idx=1,
            end_row_offset_idx=2,
            start_col_offset_idx=1,
            end_col_offset_idx=2,
        ),
    ]
    doc.add_table(dc.TableData(table_cells=cells, num_rows=2, num_cols=2), prov=prov(600, 500))
    return doc


def test_mapping_keeps_furniture_headings_and_tables_in_reading_order() -> None:
    from extract.docling_pdf import to_canon

    builder, tables, _ = to_canon(_document())
    kinds = [(b.type, b.text) for b in builder.blocks]
    assert kinds[0] == (BlockType.PAGE_HEADER, "BERITA DAERAH")
    assert kinds[1] == (BlockType.HEADING, "PERATURAN BUPATI SLEMAN")
    # ! A blank line is the canonical block separator: one docling item, two blocks.
    assert (BlockType.PARAGRAPH, "Ayat satu.") in kinds
    assert (BlockType.PARAGRAPH, "Ayat dua.") in kinds
    assert kinds[-1][0] is BlockType.TABLE_REF

    title, bab, pasal = builder.blocks[1], builder.blocks[2], builder.blocks[3]
    assert (title.level, bab.level, pasal.level) == (1, 2, 3)
    assert builder.blocks[4].heading_path == ["PERATURAN BUPATI SLEMAN", "BAB I", "Pasal 1"]

    (table,) = tables
    assert (table.n_rows, table.n_cols, table.header_rows) == (2, 2, 1)
    assert builder.blocks[-1].ref == table.id
    assert "Rp 5.000" in table.markdown


def test_bbox_is_top_left_like_the_native_extractor() -> None:
    from extract.docling_pdf import to_canon

    builder, _, _ = to_canon(_document())
    title = builder.blocks[1]
    assert title.page == 1
    assert title.bbox == (50.0, 40.0, 550.0, 60.0)


def test_a_captioned_table_survives_the_round_trip() -> None:
    """! A reference block renders as its ref alone, so a caption put in its text failed
    the render/parse round trip and the whole document with it (5 of the first 50 large
    PDFs). The caption lives on the table, and docling yields it again as its own item."""
    from datetime import UTC, datetime

    from canon import CanonicalDoc, Extraction, Severity, Source, validate
    from extract.docling_pdf import to_canon

    dc = pytest.importorskip("docling_core.types.doc")
    doc = dc.DoclingDocument(name="t")
    doc.add_page(page_no=1, size=dc.Size(width=600, height=800))
    caption = doc.add_text(dc.DocItemLabel.CAPTION, "Tabel 1 Tarif Retribusi")
    cell = dc.TableCell(
        text="Rp 5.000",
        start_row_offset_idx=0,
        end_row_offset_idx=1,
        start_col_offset_idx=0,
        end_col_offset_idx=1,
    )
    doc.add_table(dc.TableData(table_cells=[cell], num_rows=1, num_cols=1), caption=caption)
    doc.add_text(dc.DocItemLabel.TEXT, "Pasal 2")

    builder, tables, assets = to_canon(doc)
    canon = CanonicalDoc(
        doc_id="sha256:" + "0" * 64,
        source=Source(
            path="a.pdf", title="a", mime="application/pdf", bytes=1, sha256="0" * 64
        ),
        extraction=Extraction(
            extractor="docling", extractor_version="1.0", extracted_at=datetime.now(UTC)
        ),
        blocks=builder.blocks,
        tables=tables,
        assets=assets,
    )
    assert [i for i in validate(canon) if i.severity is Severity.ERROR] == []
    assert tables[0].caption == "Tabel 1 Tarif Retribusi"
    assert next(b for b in builder.blocks if b.type is BlockType.TABLE_REF).text == ""


@pytest.mark.parametrize(("standard", "vlm"), [(1, 1), (2, 3)])
def test_one_document_per_lane_and_the_pipelines_overlap(
    monkeypatch: pytest.MonkeyPatch, standard: int, vlm: int
) -> None:
    """! `extract.workers` overlaps a clean document (this machine's GPU) with a scanned
    one (the VLM server), never two of a kind: two standard reads load the models twice
    on an 8 GB card, two VLM documents double the queue that times a page out."""
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    base = pytest.importorskip("docling.datamodel.base_models")
    ex = DoclingExtractor(DoclingSpec(vlm_workers=1, standard_lanes=standard, vlm_lanes=vlm))
    running: dict[str, int] = {"standard": 0, "vlm": 0}
    peak: dict[str, int] = {"standard": 0, "vlm": 0, "both": 0}
    guard = threading.Lock()

    class Fake:
        def __init__(self, pipeline: str) -> None:
            self.pipeline = pipeline

        def convert(self, *_: object, **__: object) -> object:
            with guard:
                running[self.pipeline] += 1
                peak[self.pipeline] = max(peak[self.pipeline], running[self.pipeline])
                peak["both"] = max(peak["both"], sum(running.values()))
            time.sleep(0.05)
            with guard:
                running[self.pipeline] -= 1
            return type("R", (), {"status": base.ConversionStatus.SUCCESS, "errors": []})()

    monkeypatch.setattr(ex, "converter", lambda pipeline, slot=0: Fake(pipeline))
    jobs = ["standard", "vlm"] * 6
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        for _, failure in pool.map(lambda p: ex._convert(Path("a.pdf"), p, 8), jobs):
            assert failure is None

    assert (peak["standard"], peak["vlm"], peak["both"]) == (standard, vlm, standard + vlm)


def test_lanes_are_hardware_and_leave_the_generation_alone() -> None:
    """! How many documents read at once never changes what one says, so a bigger box
    can raise the lanes without re-extracting a corpus it already has."""
    pdf = CorpusSpec.find("id_pdf")
    wider = pdf.model_copy(deep=True)
    wider.extract.docling.standard_lanes = 4
    wider.extract.docling.vlm_lanes = 4
    assert wider.extraction_hash == pdf.extraction_hash
