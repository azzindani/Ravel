"""Fixtures: a small canonical document shaped like an Indonesian regulation."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from canon import Block, BlockType, CanonicalDoc, Cell, Extraction, Source, Table


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def block(
    order: int,
    btype: BlockType,
    text: str = "",
    *,
    level: int | None = None,
    path: list[str] | None = None,
    ref: str | None = None,
    page: int = 1,
) -> Block:
    return Block(
        id=f"b{order:04d}",
        type=btype,
        text=text,
        level=level,
        heading_path=path or [],
        page=page,
        bbox=(72.0, 100.0 + order, 523.5, 130.0 + order),
        reading_order=order,
        confidence=0.98,
        ref=ref,
    )


@pytest.fixture
def doc() -> CanonicalDoc:
    """Covers every block type, a spanned table, and a real heading hierarchy."""
    sha = _sha("UU 28/2007")
    root = "UU 28/2007"
    bab = "Bab II"
    pasal = "Pasal 9"

    blocks = [
        block(0, BlockType.PAGE_HEADER, "Lembaran Negara Republik Indonesia"),
        block(1, BlockType.HEADING, root, level=1, path=[root]),
        block(
            2, BlockType.PARAGRAPH, "Menimbang bahwa ketentuan berikut berlaku.", path=[root]
        ),
        block(3, BlockType.HEADING, bab, level=2, path=[root, bab]),
        block(4, BlockType.HEADING, pasal, level=3, path=[root, bab, pasal]),
        block(
            5,
            BlockType.PARAGRAPH,
            "(1) Wajib Pajak wajib menyelenggarakan pembukuan.",
            path=[root, bab, pasal],
        ),
        block(
            6,
            BlockType.LIST_ITEM,
            "a. orang pribadi yang melakukan kegiatan usaha;",
            path=[root, bab, pasal],
        ),
        block(7, BlockType.LIST_ITEM, "b. badan usaha tetap;", path=[root, bab, pasal]),
        block(8, BlockType.CAPTION, "Tarif Pajak Penghasilan", path=[root, bab, pasal]),
        block(9, BlockType.TABLE_REF, ref="t001", path=[root, bab, pasal]),
        block(10, BlockType.FORMULA, "PPh = 0.05 \\times P", path=[root, bab, pasal]),
        block(11, BlockType.FOOTNOTE, "Lihat Pasal 4 ayat (2).", path=[root, bab, pasal]),
        block(12, BlockType.PAGE_FOOTER, "Halaman 17", path=[root, bab, pasal]),
    ]

    table = Table(
        id="t001",
        page=1,
        caption="Tarif Pajak Penghasilan",
        n_rows=3,
        n_cols=2,
        header_rows=1,
        cells=[
            Cell(r=0, c=0, colspan=2, text="Lapisan Penghasilan"),
            Cell(r=1, c=0, text="s.d. Rp 60 juta"),
            Cell(r=1, c=1, text="5%"),
            Cell(r=2, c=0, text="di atas Rp 60 juta"),
            Cell(r=2, c=1, text="15%"),
        ],
        confidence=0.91,
    )

    return CanonicalDoc(
        doc_id=f"sha256:{sha}",
        source=Source(
            path="sources/id_legal/UU_28_2007.pdf",
            url="https://peraturan.go.id/id/uu-no-28-tahun-2007",
            title="Undang-Undang Nomor 28 Tahun 2007",
            mime="application/pdf",
            bytes=2481203,
            sha256=sha,
        ),
        extraction=Extraction(
            extractor="smoldocling",
            extractor_version="0.1.0",
            model="ds4sd/SmolDocling-256M-preview",
            params={"dpi": 144, "ocr": False},
            extracted_at=datetime(2026, 9, 12, 10, 0, tzinfo=UTC),
            pages=84,
            warnings=["page 41: low OCR confidence"],
        ),
        blocks=blocks,
        tables=[table],
    )
