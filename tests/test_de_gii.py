"""Germany's Gesetze im Internet: `fetch.kind: listing` over archives, each law cited as the
archive it was read from, and the `de_gii` dialect, whose file is a flat list of norms
(`docs/sources/SOURCE_DE_GII.md`)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

from chunk import Builder, ChunkConfig, get
from extract.structure import Structurer
from extract.xml import XmlExtractor
from sources.fetch import Fetcher
from spec import default_registry
from spec.corpus import CorpusSpec

BASE = "http://www.gesetze.example.de/"
TOC = f"{BASE}gii-toc.xml"

LAW = """<?xml version="1.0" encoding="UTF-8"?>
<dokumente builddate="20260506" doknr="BJNR001950896">
<norm doknr="BJNR001950896"><metadaten><jurabk>BGB</jurabk><amtabk>BGB</amtabk>
<ausfertigung-datum manuell="ja">1896-08-18</ausfertigung-datum>
<langue>Bürgerliches Gesetzbuch</langue></metadaten>
<textdaten><fussnoten><Content><P>(+++ Textnachweis Geltung ab: 1.1.1980 +++)</P></Content></fussnoten></textdaten></norm>
<norm><metadaten><jurabk>BGB</jurabk><gliederungseinheit><gliederungskennzahl>010</gliederungskennzahl>
<gliederungsbez>Buch 1</gliederungsbez><gliederungstitel>Allgemeiner Teil</gliederungstitel></gliederungseinheit></metadaten><textdaten/></norm>
<norm><metadaten><jurabk>BGB</jurabk><gliederungseinheit><gliederungskennzahl>010010</gliederungskennzahl>
<gliederungsbez>Abschnitt 1</gliederungsbez><gliederungstitel>Personen</gliederungstitel></gliederungseinheit></metadaten><textdaten/></norm>
<norm><metadaten><jurabk>BGB</jurabk><enbez>§ 1</enbez><titel format="XML">Beginn der Rechtsfähigkeit</titel></metadaten>
<textdaten><text format="XML"><Content><P>Die Rechtsfähigkeit des Menschen beginnt mit der Vollendung der Geburt.</P></Content></text></textdaten></norm>
<norm><metadaten><jurabk>BGB</jurabk><enbez>§ 7</enbez><titel format="XML">Wohnsitz; Begründung und Aufhebung</titel></metadaten>
<textdaten><text format="XML"><Content><P>(1) Wer sich an einem Orte ständig <B>niederlässt</B>, begründet an diesem Ort seinen Wohnsitz.</P>
<P>(2) Der Wohnsitz kann gleichzeitig an mehreren Orten bestehen:<DL Type="arabic"><DT>1.</DT><DD><LA>am Ort der Arbeit,</LA></DD><DT>2.</DT><DD><LA>am Ort der Familie.</LA></DD></DL></P></Content></text>
<fussnoten><Content><P>§ 7 Abs. 2: IdF d. Art. 1 G v. 1.1.2002 I 42</P></Content></fussnoten></textdaten></norm>
<norm><metadaten><jurabk>BGB</jurabk><enbez>(XXXX) §§ 8 bis 9</enbez><titel format="XML">(weggefallen)</titel></metadaten><textdaten/></norm>
<norm><metadaten><jurabk>BGB</jurabk><gliederungseinheit><gliederungskennzahl>020</gliederungskennzahl>
<gliederungsbez>Buch 2</gliederungsbez><gliederungstitel>Recht der Schuldverhältnisse</gliederungstitel></gliederungseinheit></metadaten><textdaten/></norm>
<norm><metadaten><jurabk>BGB</jurabk><enbez>§ 241</enbez><titel format="XML">Pflichten aus dem Schuldverhältnis</titel></metadaten>
<textdaten><text format="XML"><Content><P>Kraft des Schuldverhältnisses ist der Gläubiger berechtigt, von dem Schuldner eine Leistung zu fordern.</P></Content></text></textdaten></norm>
<norm><metadaten><jurabk>BGB</jurabk><enbez>Anhang 1 zu Nummer 3</enbez><titel format="XML">Sätze</titel></metadaten>
<textdaten><text format="XML"><Content><table><tgroup cols="2"><colspec colname="col1"/><colspec colname="col2"/>
<tbody><row><entry>bisheriger Satz</entry><entry>neuer Satz</entry></row><row><entry>105 Euro</entry><entry>115 Euro</entry></row></tbody></tgroup></table></Content></text></textdaten></norm>
</dokumente>
"""  # noqa: E501 -- the publisher's XML, one norm per line as it prints them


def _law(tmp_path: Path) -> tuple[Any, dict[str, Any]]:
    path = tmp_path / "BJNR001950896.xml"
    path.write_text(LAW, encoding="utf-8")
    profile = default_registry().get("de_gii@1.0")
    raw = XmlExtractor().extract(path, title="x", url="u", profile=profile)
    doc = Structurer(profile).apply(raw)
    e = get("markup")
    b = Builder(
        doc=doc,
        chunker=e.id,
        version=e.version,
        config=ChunkConfig(max_tokens=1024, min_tokens=0),
        profile=profile,
    )
    return raw, {c.identifier: c for c in e.fn(doc, b)}


def test_provisions_cite_as_german_practice_does(tmp_path: Path) -> None:
    raw, chunks = _law(tmp_path)
    assert raw.extraction.warnings == []
    assert {"§ 1 BGB", "§ 7 BGB", "§ 241 BGB", "Anhang 1 zu Nummer 3 BGB", "BGB"} <= set(chunks)
    seven = chunks["§ 7 BGB"]
    assert (seven.regulation_number, seven.about, seven.year) == (
        "BGB",
        "Bürgerliches Gesetzbuch",
        1896,
    )
    assert "niederlässt" in seven.body and "1. am Ort der Arbeit" in seven.body
    # The norm's footnote is read with it; the metadata is not read as text.
    assert "IdF d. Art. 1" in seven.body and "jurabk" not in seven.body


def test_outline_norms_nest_by_the_length_of_their_number(tmp_path: Path) -> None:
    _, chunks = _law(tmp_path)
    assert chunks["§ 7 BGB"].heading_path == (
        "Bürgerliches Gesetzbuch › Buch 1 Allgemeiner Teil › Abschnitt 1 Personen"
        " › § 7 Wohnsitz; Begründung und Aufhebung"
    )
    # ! `020` closes `010` and the `010010` inside it: § 241 is not in Abschnitt 1.
    assert chunks["§ 241 BGB"].heading_path == (
        "Bürgerliches Gesetzbuch › Buch 2 Recht der Schuldverhältnisse"
        " › § 241 Pflichten aus dem Schuldverhältnis"
    )


def test_a_repealed_provision_is_counted_not_emitted(tmp_path: Path) -> None:
    raw, chunks = _law(tmp_path)
    assert not any("§§ 8 bis 9" in i for i in chunks)
    assert raw.extraction.params["reserved"] == {"article": 1}


def test_the_stated_designation_is_the_marker_however_many_words(tmp_path: Path) -> None:
    # ! No pattern could tell where `Anhang 1 zu Nummer 3` ends and its caption begins.
    _, chunks = _law(tmp_path)
    annex = chunks["Anhang 1 zu Nummer 3 BGB"]
    assert annex.heading_path.endswith("› Anhang 1 zu Nummer 3 Sätze")
    assert "105 Euro" in annex.body


# -- the fetch ------------------------------------------------------------------------------


def _zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def test_each_law_is_fetched_unpacked_and_cited_as_its_archive(tmp_path: Path) -> None:
    toc = (
        f"<items><item><title>BGB</title><link>{BASE}bgb/xml.zip</link></item>"
        f"<item><title>GG</title><link>{BASE}gg/xml.zip</link></item></items>"
    ).encode()
    files = {
        TOC: toc,
        f"{BASE}bgb/xml.zip": _zip({"BJNR001950896.xml": LAW.encode(), "pic.jpg": b"x"}),
        f"{BASE}gg/xml.zip": _zip({"BJNR000010949.xml": LAW.encode()}),
    }

    def opener(url: str, headers: dict[str, str], timeout: float) -> io.BytesIO:
        return io.BytesIO(files[url])

    corpus = CorpusSpec.model_validate(
        {
            "id": "de_gii",
            "title": "t",
            "sources": {"root": str(tmp_path), "snapshot": "current.txt"},
            "fetch": {
                "kind": "listing",
                "listing": TOC,
                "files": BASE,
                "links": "<link>(?P<url>[^<]+)</link>",
                "edition": "20261122",
                "paths": ["*.xml"],
                "documents": ["*.xml"],
            },
            "provenance": {
                "base_url": BASE,
                "archive_suffix": ".zip",
                "url_map": "url_map.jsonl",
            },
        }
    )
    Fetcher(corpus, opener=opener, sleep=lambda _: None).run()

    lines = (tmp_path / "current.txt").read_text(encoding="utf-8").splitlines()
    member = "20261122/bgb/xml/BJNR001950896.xml"
    assert [x for x in lines if not x.startswith("#")] == [
        member,
        "20261122/gg/xml/BJNR000010949.xml",
    ]
    assert corpus.url_for(member) == f"{BASE}bgb/xml.zip#BJNR001950896.xml"
    # Only the wanted members are unpacked: a law's images are not sources.
    assert not (tmp_path / "20261122/bgb/xml/pic.jpg").exists()


def test_a_law_that_states_no_text_is_empty_not_failed(tmp_path: Path) -> None:
    import pytest

    from extract.base import ExtractionEmpty

    # ! A notice whose one norm has a bare `<textdaten/>`: nothing to read, nothing lost.
    path = tmp_path / "BJNR053400010.xml"
    path.write_text(
        '<dokumente doknr="BJNR053400010"><norm><metadaten><jurabk>WBGArt13InkrBek</jurabk>'
        "<langue>Bekanntmachung über das Inkrafttreten</langue></metadaten><textdaten/></norm>"
        "</dokumente>",
        encoding="utf-8",
    )
    profile = default_registry().get("de_gii@1.0")
    with pytest.raises(ExtractionEmpty, match="states no text"):
        XmlExtractor().extract(path, title="x", url="u", profile=profile)
    # The same, printed as an empty text element.
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "<textdaten/>", '<textdaten><text format="decorated"/></textdaten>'
        ),
        encoding="utf-8",
    )
    with pytest.raises(ExtractionEmpty, match="states no text"):
        XmlExtractor().extract(path, title="x", url="u", profile=profile)


INTRODUCTORY = """<?xml version="1.0" encoding="UTF-8"?>
<dokumente builddate="20260831" doknr="BJNR006049896">
<norm doknr="BJNR006049896"><metadaten><jurabk>BGBEG</jurabk>
<ausfertigung-datum manuell="ja">1896-08-18</ausfertigung-datum>
<langue>Einführungsgesetz zum Bürgerlichen Gesetzbuche</langue></metadaten><textdaten/></norm>
<norm><metadaten><jurabk>BGBEG</jurabk><gliederungseinheit><gliederungskennzahl>010</gliederungskennzahl>
<gliederungsbez>Erster Teil</gliederungsbez><gliederungstitel>Allgemeine Vorschriften</gliederungstitel></gliederungseinheit></metadaten><textdaten/></norm>
<norm><metadaten><jurabk>BGBEG</jurabk><gliederungseinheit><gliederungskennzahl>010010</gliederungskennzahl>
<gliederungsbez>Art 2</gliederungsbez><gliederungstitel/></gliederungseinheit></metadaten>
<textdaten><text format="XML"><Content><P>Gesetz im Sinne des Bürgerlichen Gesetzbuchs und dieses Gesetzes ist jede Rechtsnorm.</P></Content></text></textdaten></norm>
<norm><metadaten><jurabk>BGBEG</jurabk><gliederungseinheit><gliederungskennzahl>090</gliederungskennzahl>
<gliederungsbez>Art 229</gliederungsbez><gliederungstitel>Weitere Überleitungsvorschriften</gliederungstitel></gliederungseinheit></metadaten><textdaten/></norm>
<norm><metadaten><jurabk>BGBEG</jurabk><gliederungseinheit><gliederungskennzahl>090</gliederungskennzahl>
<gliederungsbez>Art 229</gliederungsbez><gliederungstitel/></gliederungseinheit><enbez>§ 1</enbez><titel format="XML">Überleitungsvorschrift zum Gesetz zur Beschleunigung fälliger Zahlungen</titel></metadaten>
<textdaten><text format="XML"><Content><P>(1) § 284 Abs. 3 des Bürgerlichen Gesetzbuchs gilt auch für Geldforderungen.</P></Content></text></textdaten></norm>
<norm><metadaten><jurabk>BGBEG</jurabk><gliederungseinheit><gliederungskennzahl>090</gliederungskennzahl>
<gliederungsbez>-</gliederungsbez><gliederungstitel/></gliederungseinheit><enbez>§ 2</enbez><titel format="XML">Vorschriften</titel></metadaten>
<textdaten><text format="XML"><Content><P>Ein Paragraph, dessen Gliederung keine Bezeichnung hat.</P></Content></text></textdaten></norm>
</dokumente>
"""  # noqa: E501 -- the shape of EGBGB, as the publisher prints it


def test_a_numbered_norm_inside_an_article_is_a_provision_cited_within_it(
    tmp_path: Path,
) -> None:
    # A norm stating `enbez` and the outline it is in is a provision (`units.flat.unless`),
    # cited within the article, whose §§ start from 1 again. An outline that prints its
    # article's text is cited as that article, ✗ the law; `-` is no label and cites nothing.
    path = tmp_path / "BJNR006049896.xml"
    path.write_text(INTRODUCTORY, encoding="utf-8")
    profile = default_registry().get("de_gii@1.0")
    doc = Structurer(profile).apply(
        XmlExtractor().extract(path, title="x", url="u", profile=profile)
    )
    e = get("markup")
    b = Builder(doc=doc, chunker=e.id, version=e.version,
                config=ChunkConfig(max_tokens=1024, min_tokens=0), profile=profile)  # fmt: skip
    chunks = {c.identifier: c for c in e.fn(doc, b)}
    assert set(chunks) == {"Art 2 BGBEG", "Art 229 § 1 BGBEG", "§ 2 BGBEG"}
    inner = chunks["Art 229 § 1 BGBEG"]
    assert inner.heading_path.endswith(
        "Art 229 Weitere Überleitungsvorschriften › § 1 Überleitungsvorschrift zum Gesetz zur "
        "Beschleunigung fälliger Zahlungen"
    )
    assert inner.body.startswith("(1) § 284 Abs. 3")
