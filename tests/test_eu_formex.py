"""EUR-Lex Formex through its descriptor: the `eu_formex` dialect and the `eu_lex` profile
(`SOURCE_EU_LEX.md`). Hand-written packages shaped like the Cellar's, read end to end."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from canon import BlockType
from chunk import Builder, ChunkConfig, get
from extract.structure import Structurer
from extract.xml import XmlExtractor
from spec import default_registry

DESCRIPTOR = """<?xml version="1.0" encoding="UTF-8"?>
<DOC><BIB.DOC><NO.DOC TYPE="OJ" FORMAT="YN"><NO.CURRENT>626</NO.CURRENT><YEAR>1993</YEAR>
<COM>EEC</COM></NO.DOC></BIB.DOC>
<PUBLICATION.REF FILE="L.toc.xml"><COLL>L</COLL><DATE ISO="19931110">19931110</DATE>
</PUBLICATION.REF>
<FMX><DOC.MAIN.PUB NO.SEQ="0001"><LG.DOC>EN</LG.DOC><LEGAL.VALUE>{value}</LEGAL.VALUE>
{celex}<DATE ISO="{date}">{date}</DATE><PAGE.FIRST>1</PAGE.FIRST>
<REF.PHYS FILE="{main}" TYPE="DOC.XML"/></DOC.MAIN.PUB>
<DOC.SUB.PUB NO.SEQ="0001.0001" TYPE="ANNEX"><PAGE.FIRST>9</PAGE.FIRST>
<REF.PHYS FILE="L_1993001EN.01000901.xml" TYPE="DOC.XML"/>
<REF.PHYS FILE="L_1993001EN.01000901.pdf" TYPE="PDF"/></DOC.SUB.PUB></FMX>
<PAPER><VOLUME.PAPER><ITEM.VOLUME REF.NO.SEQ="0001"><TITLE><TI><P>{title}</P></TI></TITLE>
</ITEM.VOLUME></VOLUME.PAPER></PAPER></DOC>
"""

ACT = """<?xml version="1.0" encoding="UTF-8"?>
<ACT><BIB.INSTANCE><DOCUMENT.REF FILE="L.doc.xml"/><DATE ISO="19931025">19931025</DATE>
<NO.DOC FORMAT="YN" TYPE="OJ"><NO.CURRENT>626</NO.CURRENT><YEAR>1993</YEAR><COM>EEC</COM>
</NO.DOC></BIB.INSTANCE>
<TITLE><TI><P>Council Decision</P><P>of <DATE ISO="19931025">25 October 1993</DATE></P></TI>
</TITLE>
<PREAMBLE><PREAMBLE.INIT>THE COUNCIL OF THE EUROPEAN UNION,</PREAMBLE.INIT>
<GR.VISA><VISA>Having regard to Regulation (EEC) No 2658/87<NOTE NOTE.ID="E1"><P>OJ L 256,
7.9.1987, p. 1.</P></NOTE>,</VISA></GR.VISA>
<GR.CONSID><CONSID><NP><NO.P>(1)</NO.P><TXT>Whereas a convention was signed;</TXT></NP>
</CONSID></GR.CONSID><PREAMBLE.FINAL>HAS DECIDED AS FOLLOWS:</PREAMBLE.FINAL></PREAMBLE>
<ENACTING.TERMS>
<DIVISION><TITLE><TI><P>TITLE I</P></TI><STI><P>GENERAL</P></STI></TITLE>
<DIVISION><TITLE><TI><P>CHAPTER 1</P></TI><STI><P>Scope</P></STI></TITLE>
<ARTICLE IDENTIFIER="001"><TI.ART>Article 1</TI.ART><STI.ART><P>Subject matter</P></STI.ART>
<PARAG IDENTIFIER="001.001"><NO.PARAG>1.</NO.PARAG><ALINEA>The Convention is approved on
<DATE ISO="19931025">25 October 1993</DATE>.</ALINEA></PARAG>
<PARAG IDENTIFIER="001.002"><NO.PARAG>2.</NO.PARAG><ALINEA><P>It covers:</P>
<LIST TYPE="alpha"><ITEM><NP><NO.P>(a)</NO.P><TXT>fish;</TXT></NP></ITEM></LIST></ALINEA>
</PARAG></ARTICLE>
</DIVISION></DIVISION>
<ARTICLE IDENTIFIER="002"><TI.ART>Article 2</TI.ART>
<ALINEA>The rates are:</ALINEA>
<TBL NO.SEQ="0001" COLS="3"><TITLE><TI><P>Rates</P></TI></TITLE><CORPUS>
<ROW TYPE="HEADER"><CELL COL="1" TYPE="HEADER">Code</CELL><CELL COL="2" COLSPAN="2"
TYPE="HEADER">Rate</CELL></ROW>
<ROW><CELL COL="1" ROWSPAN="2">0301</CELL><CELL COL="2">5</CELL><CELL COL="3">%</CELL></ROW>
<ROW><CELL COL="2">7</CELL><CELL COL="3">%</CELL></ROW></CORPUS></TBL>
</ARTICLE>
</ENACTING.TERMS>
<FINAL><SIGNATURE><PL.DATE><P>Done at Luxembourg, 25 October 1993.</P></PL.DATE>
</SIGNATURE></FINAL></ACT>
"""

ANNEX = """<?xml version="1.0" encoding="UTF-8"?>
<ANNEX><BIB.INSTANCE><DOCUMENT.REF FILE="L.doc.xml"/></BIB.INSTANCE>
<TITLE><TI><P>ANNEX II</P></TI><STI><P>RULES OF PROCEDURE</P></STI></TITLE>
<CONTENTS><GR.SEQ LEVEL="1"><TITLE><TI><P>Part A</P></TI></TITLE>
<NP><NO.P>1.</NO.P><TXT>Meetings are public.</TXT></NP></GR.SEQ>
<ARTICLE IDENTIFIER="001"><TI.ART>Article 1</TI.ART><ALINEA>The chair opens the meeting.
</ALINEA></ARTICLE></CONTENTS></ANNEX>
"""

CONSOLIDATION = """<?xml version="1.0" encoding="utf-8"?>
<CONS.ACT><INFO.CONSLEG CONSLEG.DATE="20130814" CONSLEG.REF="1958R0001" LEG.VAL="REG"
START.DATE="20130701"/><INFO.PROD PROD.DATE="20130814"/>
<CONS.DOC><BIB.INSTANCE><DATE ISO="20130701">20130701</DATE></BIB.INSTANCE>
<FAM.COMP><BIB.DATA><NO.CELEX>31958R0001</NO.CELEX><DATE ISO="19580415">19580415</DATE>
<TITLE><TI><P><HT TYPE="UC">Regulation</HT> No 1</P><P>determining the languages</P></TI>
</TITLE></BIB.DATA></FAM.COMP>
<TITLE><TI><P>Regulation No 1</P></TI></TITLE>
<ENACTING.TERMS><ARTICLE IDENTIFIER="001"><TI.ART>Article 1</TI.ART>
<ALINEA>The official languages shall be Bulgarian, Croatian, Czech.</ALINEA></ARTICLE>
</ENACTING.TERMS>
<CONS.ANNEX><TITLE><TI><P>ANNEX</P></TI></TITLE><CONTENTS><P>Languages.</P></CONTENTS>
</CONS.ANNEX></CONS.DOC></CONS.ACT>
"""


@pytest.fixture
def profile():  # type: ignore[no-untyped-def]
    return default_registry().get("eu_lex@1.0")


def package(tmp_path: Path, **descriptor: str) -> Path:
    fields = {
        "value": "DEC",
        "celex": "",
        "date": "19931025",
        "main": "L_1993001EN.01000101.xml",
        "title": "Council Decision of 25 October 1993 concerning the conclusion of the "
        "Convention on fishing",
        **descriptor,
    }
    (tmp_path / "L_1993001EN.01000101.xml").write_text(ACT, encoding="utf-8")
    (tmp_path / "L_1993001EN.01000901.xml").write_text(ANNEX, encoding="utf-8")
    (tmp_path / "C.xml").write_text(CONSOLIDATION, encoding="utf-8")
    path = tmp_path / "L_1993001EN.01000101.doc.xml"
    path.write_text(DESCRIPTOR.format(**fields), encoding="utf-8")
    return path


def read(path: Path, profile: Any) -> Any:
    doc = XmlExtractor().extract(
        path, title=path.name, url="https://x.test/zip", profile=profile
    )
    return Structurer(profile).apply(doc)


def chunked(doc: Any, profile: Any) -> list[Any]:
    entry = get("markup")
    builder = Builder(
        doc=doc,
        chunker=entry.id,
        version=entry.version,
        config=ChunkConfig(max_tokens=1024, min_tokens=0),
        profile=profile,
    )
    return list(entry.fn(doc, builder))


def headings(doc: Any) -> dict[str, list[str]]:
    return {b.text: b.heading_path for b in doc.blocks if b.type is BlockType.HEADING}


def test_the_descriptor_reads_its_act_and_annex_as_one_document(
    tmp_path: Path, profile: Any
) -> None:
    doc = read(package(tmp_path), profile)
    paths = headings(doc)
    title = (
        "Council Decision of 25 October 1993 concerning the conclusion of the "
        "Convention on fishing"
    )
    assert paths["Article 1 Subject matter"] == [
        title,
        "TITLE I GENERAL",
        "CHAPTER 1 Scope",
        "Article 1 Subject matter",
    ]
    # ! The annex is read where the descriptor names it, after the act and inside it; its
    # own articles are not the act's.
    assert paths["ANNEX II RULES OF PROCEDURE"] == [title, "ANNEX II RULES OF PROCEDURE"]
    units = {b.text: b.attrs["unit"] for b in doc.blocks if b.type is BlockType.HEADING}
    assert units["Article 1"] == "annex_article"
    assert units["CHAPTER 1 Scope"] == "division2"
    assert doc.extraction.params["links"]["followed"] == 2  # the PDF is not text
    assert doc.extraction.warnings == []


def test_front_matter_is_not_text_and_inline_dates_are(tmp_path: Path, profile: Any) -> None:
    doc = read(package(tmp_path), profile)
    texts = [b.text for b in doc.blocks if b.type is BlockType.PARAGRAPH]
    # The descriptor's DATE and the act's own TITLE are dropped by path ...
    assert "19931025" not in texts and "Council Decision of 25 October 1993" not in texts
    # ... and the same DATE inline in a sentence stays.
    assert "1. The Convention is approved on 25 October 1993." in texts
    # A footnote is read where it is called, marked.
    assert "Having regard to Regulation (EEC) No 2658/87^OJ L 256, 7.9.1987, p. 1.," in texts
    assert "(a) fish;" in texts
    kinds = {b.text: b.attrs.get("kind") for b in doc.blocks if b.type is BlockType.PARAGRAPH}
    assert kinds["(1) Whereas a convention was signed;"] == "recital"
    assert kinds["Done at Luxembourg, 25 October 1993."] == "signature"


def test_a_formex_table_places_cells_by_column_and_span(tmp_path: Path, profile: Any) -> None:
    doc = read(package(tmp_path), profile)
    table = doc.tables[0]
    grid = {(c.r, c.c): c.text for c in table.cells}
    assert (table.n_rows, table.n_cols, table.header_rows, table.caption) == (3, 3, 1, "Rates")
    assert grid == {
        (0, 0): "Code",
        (0, 1): "Rate",
        (1, 0): "0301",
        (1, 1): "5",
        (1, 2): "%",
        (2, 1): "7",
        (2, 2): "%",
    }


def test_the_number_is_the_one_the_act_is_cited_by(tmp_path: Path, profile: Any) -> None:
    # The title prints no number: the act's `NO.DOC` states it, year first.
    doc = read(package(tmp_path), profile)
    act = doc.blocks[0].attrs
    assert (act["type"], act["number"], act["date"]) == ("Decision", "93/626/EEC", "1993-10-25")
    cited = {c.identifier for c in chunked(doc, profile)}
    assert "Decision 93/626/EEC, Article 1" in cited
    assert "Decision 93/626/EEC, Annex II" in cited
    assert "Decision 93/626/EEC, Annex II, Article 1" in cited
    # A title that prints the number wins over every other source.
    titled = read(
        package(
            tmp_path,
            title="Regulation (EU) 2016/679 of the European Parliament and of the Council",
            celex="<NO.CELEX>32016R0679</NO.CELEX>",
        ),
        profile,
    )
    attrs = titled.blocks[0].attrs
    assert (attrs["type"], attrs["number"], attrs["celex"]) == (
        "Regulation",
        "(EU) 2016/679",
        "32016R0679",
    )


def test_a_consolidation_is_the_act_and_states_its_version(
    tmp_path: Path, profile: Any
) -> None:
    linked = read(
        package(tmp_path, main="C.xml", title="Regulation No 1 determining the languages"),
        profile,
    )
    act = linked.blocks[0].attrs
    assert (act["number"], act["version_date"], act["consolidated"]) == (
        "No 1",
        "20130701",
        "20130814",
    )
    assert linked.extraction.params["units"]["consolidation"] == 1
    cited = {c.identifier for c in chunked(linked, profile)}
    assert {"Regulation No 1, Article 1", "Regulation No 1, Annex"} <= cited
    # ! A package that swaps the names: its `.doc.xml` is the consolidation, read as the act.
    swapped = tmp_path / "swapped.doc.xml"
    swapped.write_text(CONSOLIDATION, encoding="utf-8")
    alone = read(swapped, profile)
    first = alone.blocks[0]
    assert first.text == "Regulation No 1 determining the languages"
    assert (first.attrs["celex"], first.attrs["number"], first.attrs["date"]) == (
        "31958R0001",
        "No 1",
        "1958-04-15",
    )
    assert "Regulation No 1" not in [
        b.text for b in alone.blocks if b.type is BlockType.PARAGRAPH
    ]


def test_an_act_with_no_number_is_cited_by_its_journal_reference(
    tmp_path: Path, profile: Any
) -> None:
    """A 1961 Euratom decision states no CELEX, no `NO.DOC` of its own and no number in
    its title: its descriptor's Journal reference is all that names it."""
    path = package(tmp_path, title="Decision on the establishment of a Joint Undertaking")
    act = tmp_path / "L_1993001EN.01000101.xml"
    act.write_text(
        ACT.replace('<NO.DOC FORMAT="YN" TYPE="OJ"><NO.CURRENT>626</NO.CURRENT>', "<X>")
        .replace("<YEAR>1993</YEAR><COM>EEC</COM>\n</NO.DOC>", "</X>")
        .replace("<X></X>", ""),
        encoding="utf-8",
    )
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            '<NO.DOC TYPE="OJ" FORMAT="YN"><NO.CURRENT>626</NO.CURRENT><YEAR>1993</YEAR>\n'
            "<COM>EEC</COM></NO.DOC>",
            '<NO.DOC TYPE="OJ"><TXT>1961/P 65/01</TXT></NO.DOC>',
        ),
        encoding="utf-8",
    )
    doc = read(path, profile)
    assert doc.blocks[0].attrs["number"] == "1961/P 65/01"
    assert "Decision 1961/P 65/01, Article 2" in {c.identifier for c in chunked(doc, profile)}
