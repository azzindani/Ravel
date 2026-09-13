"""Non-PDF sources: the same engine, the same profile, different file formats."""

from __future__ import annotations

from pathlib import Path

import pytest

from canon import BlockType, is_valid, validate
from extract import ExtractionFailed, Structurer, TextExtractor, normalize
from spec import default_registry

HTML = """<html><head><title>t</title><style>p{color:red}</style></head><body>
<script>var x = 1;</script>
<h1>PERATURAN BUPATI BOYOLALI NOMOR 41 TAHUN 2011</h1>
<p>Menimbang bahwa ketentuan berikut berlaku.</p>
<h2>BAB II</h2>
<p>Pasal 9</p>
<p>(1) Wajib Pajak wajib menyelenggarakan pembukuan.</p>
<ul><li>a. orang pribadi;</li><li>b. badan usaha tetap;</li></ul>
</body></html>"""

MARKDOWN = """# PERATURAN BUPATI BOYOLALI NOMOR 41 TAHUN 2011

Menimbang bahwa ketentuan berikut berlaku.

## BAB II

Pasal 9

- a. orang pribadi;
- b. badan usaha tetap;
"""


def write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


@pytest.mark.parametrize(("name", "body"), [("doc.html", HTML), ("doc.md", MARKDOWN)])
def test_text_sources_produce_valid_canonical_documents(
    tmp_path: Path, name: str, body: str
) -> None:
    doc = TextExtractor().extract(write(tmp_path, name, body), title="Perbup 41/2011")
    assert is_valid(doc), [str(i) for i in validate(doc)]
    assert doc.blocks


def test_html_script_and_style_never_reach_the_corpus(tmp_path: Path) -> None:
    doc = TextExtractor().extract(write(tmp_path, "doc.html", HTML), title="t")
    assert "var x" not in doc.text
    assert "color:red" not in doc.text


def test_html_headings_and_lists_are_typed(tmp_path: Path) -> None:
    doc = TextExtractor().extract(write(tmp_path, "doc.html", HTML), title="t")
    kinds = {b.type for b in doc.blocks}
    assert BlockType.HEADING in kinds
    assert BlockType.LIST_ITEM in kinds


def test_the_same_profile_finds_structure_in_html_and_markdown(tmp_path: Path) -> None:
    """! The point of the whole registry: `Pasal 9` is a heading because the profile
    says so, not because the file was a PDF."""
    structurer = Structurer(default_registry().get("id_regulation"))
    found = {}
    for name, body in (("doc.html", HTML), ("doc.md", MARKDOWN)):
        doc = structurer.apply(TextExtractor().extract(write(tmp_path, name, body), title="t"))
        found[name] = {
            b.attrs.get("unit") for b in doc.blocks if b.type is BlockType.HEADING
        }
    assert "pasal" in found["doc.html"]
    assert "bab" in found["doc.html"]
    assert found["doc.html"] == found["doc.md"]


def test_heading_paths_carry_through_a_text_source(tmp_path: Path) -> None:
    structurer = Structurer(default_registry().get("id_regulation"))
    source = write(tmp_path, "d.md", MARKDOWN)
    doc = structurer.apply(TextExtractor().extract(source, title="t"))
    ayat = next(b for b in doc.ordered() if "orang pribadi" in b.text)
    assert "BAB II" in ayat.heading_path
    assert "Pasal 9" in ayat.heading_path


def test_empty_source_fails_loudly(tmp_path: Path) -> None:
    """An empty success would enter the corpus as a content-free document."""
    with pytest.raises(ExtractionFailed, match="produced no text"):
        TextExtractor().extract(write(tmp_path, "empty.md", "   \n\n  "), title="t")


def test_unsupported_mime_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ExtractionFailed, match="not a text source"):
        TextExtractor().extract(write(tmp_path, "thing.csv", "a,b\n1,2"), title="t")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Undang­Undang", "UndangUndang"),      # soft hyphen, invisible on screen
        ("a​b", "ab"),                            # zero-width space
        ("café    x", "café x"),            # NFKC + nbsp collapse
        ("&amp;lt;", "&lt;"),                          # unescaped ONCE, never twice
    ],
)
def test_normalize_repairs_transport_damage(raw: str, expected: str) -> None:
    """! A soft hyphen is invisible on screen but splits a word for a tokenizer —
    which is how `Undang-Undang` becomes two unrelated index terms.

    Entities are unescaped exactly once. Unescaping until stable would corrupt a
    document that legitimately contains the text `&lt;`, which legal and technical
    corpora routinely do."""
    assert normalize(raw) == expected
