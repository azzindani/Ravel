"""Canada's consolidated Acts and regulations: the `justice_ca` dialect, the `ca_laws`
profile and the `github` fetch (`docs/SOURCE_CA_LAWS.md`)."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from canon import BlockType
from chunk import Builder, ChunkConfig, get
from enrich import signals
from extract.structure import Structurer
from extract.xml import XmlExtractor
from sources.fetch import Fetcher
from spec import CorpusSpec, default_registry

LIMS = 'xmlns:lims="http://justice.gc.ca/lims"'

REGULATION = f"""<?xml version="1.0" encoding="utf-8"?>
<Regulation {LIMS} lims:current-date="2026-10-19" lims:lastAmendedDate="2025-09-19"
    regulation-type="SOR" in-force="yes" xml:lang="en">
  <Identification>
    <InstrumentNumber>SOR/2018-108</InstrumentNumber>
    <EnablingAuthority>
      <XRefExternal link="F-27">FOOD AND DRUGS ACT</XRefExternal><XRefExternal
        link="S-1.1">SAFE FOOD FOR CANADIANS ACT</XRefExternal>
    </EnablingAuthority>
    <LongTitle>Safe Food for Canadians Regulations</LongTitle>
  </Identification>
  <Body>
    <Heading level="1"><Label>PART 1</Label><TitleText>Interpretation</TitleText></Heading>
    <Section lims:lastAmendedDate="2019-01-15">
      <MarginalNote>Definitions</MarginalNote><Label>1</Label>
      <Subsection><Label>(1)</Label><Text>The following definitions apply.</Text>
        <Definition>
          <Text><DefinedTermEn>food</DefinedTermEn> means any article; (aliment)</Text>
        </Definition>
      </Subsection>
      <Subsection><Label>(2)</Label><Text>In these Regulations,</Text>
        <Paragraph><Label>(a)</Label><Text>a reference to SOR/2016-12 includes</Text>
          <Subparagraph><Label>(i)</Label><Text>its schedule.</Text></Subparagraph>
        </Paragraph>
      </Subsection>
      <HistoricalNote>
        <HistoricalNoteSubItem>SOR/2017-4, s. 1; SOR/2018-9, s. 2</HistoricalNoteSubItem>
        <HistoricalNoteSubItem>SOR/2019-12, s. 3</HistoricalNoteSubItem>
      </HistoricalNote>
    </Section>
    <Heading level="1"><Label>PART 2</Label><TitleText>Trade</TitleText></Heading>
    <Heading level="2"><Label>DIVISION 1</Label><TitleText>Licences</TitleText></Heading>
    <Section><MarginalNote>Licence</MarginalNote><Label>5</Label>
      <Text>A person must hold a licence.</Text></Section>
    <Heading level="2"><TitleText>Exports</TitleText></Heading>
    <Section><Label>6</Label><Text>Exports are permitted.</Text></Section>
  </Body>
  <Schedule>
    <ScheduleFormHeading><Label>SCHEDULE 1</Label><OriginatingRef>(Section 5)</OriginatingRef>
      <TitleText>Fees</TitleText></ScheduleFormHeading>
    <TableGroup><table><tgroup cols="3">
      <colspec colname="c1"/><colspec colname="c2"/><colspec colname="c3"/>
      <thead><row><entry>Item</entry><entry namest="c2" nameend="c3">Fee</entry></row></thead>
      <tbody><row><entry>1</entry><entry>Licence</entry><entry>$250</entry></row></tbody>
    </tgroup></table></TableGroup>
  </Schedule>
  <Schedule>
    <BillPiece><RelatedOrNotInForce>
      <Heading level="5"><TitleText>— SOR/2020-1, s. 5</TitleText></Heading>
      <Section><Label>5</Label><Text>A transitional rule.</Text></Section>
    </RelatedOrNotInForce></BillPiece>
  </Schedule>
  <RecentAmendments>
    <Amendment><AmendmentCitation>SOR/2025-242</AmendmentCitation><AmendmentDate>2026-10-01</AmendmentDate></Amendment>
    <Amendment><AmendmentCitation>SOR/2024-7</AmendmentCitation><AmendmentDate>2024-02-01</AmendmentDate></Amendment>
  </RecentAmendments>
</Regulation>
"""

STATUTE = f"""<?xml version="1.0" encoding="utf-8"?>
<Statute {LIMS} lims:current-date="2026-10-19" in-force="yes">
  <Identification>
    <LongTitle>An Act to extend the present laws</LongTitle>
    <ShortTitle status="official">Access to Information Act</ShortTitle>
    <Chapter><ConsolidatedNumber official="yes">A-1</ConsolidatedNumber></Chapter>
  </Identification>
  <Body>
    <Section><MarginalNote>Short title</MarginalNote><Label>1</Label>
      <Text>This Act may be cited as the Access to Information Act.</Text></Section>
  </Body>
</Statute>
"""


@pytest.fixture
def profile():  # type: ignore[no-untyped-def]
    return default_registry().get("ca_laws@1.0")


def extracted(tmp_path: Path, xml: str, profile: Any):  # type: ignore[no-untyped-def]
    path = tmp_path / "doc.xml"
    path.write_text(xml, encoding="utf-8")
    doc = XmlExtractor().extract(
        path, title="doc", url="https://example.test/x", profile=profile
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


def test_flat_headings_nest_and_close(tmp_path: Path, profile: Any) -> None:
    doc = extracted(tmp_path, REGULATION, profile)
    paths = {b.text: b.heading_path for b in doc.blocks if b.type is BlockType.HEADING}
    assert paths["1 Definitions"] == [
        "Safe Food for Canadians Regulations",
        "PART 1 Interpretation",
        "1 Definitions",
    ]
    # PART 2 closes PART 1; a second level-2 heading closes the first.
    assert paths["6"] == [
        "Safe Food for Canadians Regulations",
        "PART 2 Trade",
        "Exports",
        "6",
    ]
    # The schedule is not inside the last Part: the Body's headings close with the Body.
    schedule = next(h for h in paths if h.startswith("SCHEDULE 1"))
    assert paths[schedule] == ["Safe Food for Canadians Regulations", schedule]
    assert schedule == "SCHEDULE 1 (Section 5) Fees"
    assert doc.extraction.warnings == []


def test_units_state_numbers_read_from_their_own_paths(tmp_path: Path, profile: Any) -> None:
    doc = extracted(tmp_path, REGULATION, profile)
    heads = {b.text: b.attrs for b in doc.blocks if b.type is BlockType.HEADING}
    top = heads["Safe Food for Canadians Regulations"]
    assert (top["number"], top["type"], top["date"]) == ("SOR/2018-108", "SOR", "2026-10-19")
    assert heads["1 Definitions"]["number"] == "1"
    assert heads["1 Definitions"]["last_amended"] == "2019-01-15"
    assert heads["PART 2 Trade"]["number"] == "2"
    statute = extracted(tmp_path, STATUTE, profile)
    act = statute.blocks[0]
    # An Act states no regulation-type: the dialect's literal fallback names it.
    assert (act.text, act.attrs["number"], act.attrs["type"]) == (
        "Access to Information Act",
        "A-1",
        "Act",
    )


def test_labels_open_their_provision_text(tmp_path: Path, profile: Any) -> None:
    doc = extracted(tmp_path, REGULATION, profile)
    texts = [b.text for b in doc.blocks if b.type is BlockType.PARAGRAPH]
    assert "(1) The following definitions apply." in texts
    assert "(a) a reference to SOR/2016-12 includes" in texts
    assert "(i) its schedule." in texts


def test_related_provisions_are_text_not_sections(tmp_path: Path, profile: Any) -> None:
    doc = extracted(tmp_path, REGULATION, profile)
    sections = [b.attrs["number"] for b in doc.blocks if b.attrs.get("unit") == "section"]
    # The related provision's `5` is the amending regulation's, not a second section 5.
    assert sections == ["1", "5", "6"]
    related = [b.text for b in doc.blocks if b.attrs.get("kind") == "related_provisions"]
    assert related[:2] == ["— SOR/2020-1, s. 5", "5"]


def test_cals_table_spans(tmp_path: Path, profile: Any) -> None:
    doc = extracted(tmp_path, REGULATION, profile)
    table = doc.tables[0]
    grid = {(c.r, c.c): c.text for c in table.cells}
    assert (table.n_rows, table.n_cols, table.header_rows) == (2, 3, 1)
    assert grid == {
        (0, 0): "Item",
        (0, 1): "Fee",
        (1, 0): "1",
        (1, 1): "Licence",
        (1, 2): "$250",
    }


def test_chunks_cite_instrument_and_section(tmp_path: Path, profile: Any) -> None:
    doc = extracted(tmp_path, REGULATION, profile)
    chunks = chunked(doc, profile)
    cited = [c.identifier for c in chunks]
    assert "SOR/2018-108, s. 1" in cited and "SOR/2018-108, s. 6" in cited
    assert "SOR/2018-108, Schedule 1" in cited
    first = next(c for c in chunks if c.identifier == "SOR/2018-108, s. 1")
    assert (first.regulation_type, first.regulation_number, first.effective_date) == (
        "SOR",
        "SOR/2018-108",
        "2026-10-19",
    )
    assert first.about == "Safe Food for Canadians Regulations"


def test_signals(tmp_path: Path, profile: Any) -> None:
    doc = extracted(tmp_path, REGULATION, profile)
    chunks = chunked(doc, profile)
    rows = {
        c.identifier: r
        for c, r in zip(
            chunks, signals.compute(chunks, [doc], profile.spec.signals), strict=True
        )
        if not c.attrs.get("kind")
    }
    row = rows["SOR/2018-108, s. 1"]
    # Two Acts printed with nothing between them: two values, not one glued name.
    assert row["authority"] == ["FOOD AND DRUGS ACT", "SAFE FOOD FOR CANADIANS ACT"]
    assert row["amendments"] == ["SOR/2017-4, s. 1", "SOR/2018-9, s. 2", "SOR/2019-12, s. 3"]
    assert row["defined_terms"] == ["food"]
    assert row["regulation_references"] == [
        "SOR/2016-12",
        "SOR/2017-4",
        "SOR/2018-9",
        "SOR/2019-12",
    ]
    assert (row["paragraph_first"], row["paragraph_last"]) == ("(1)", "(2)(a)(i)")
    assert row["recent_amendments"] == ["SOR/2025-242, 2026-10-01", "SOR/2024-7, 2024-02-01"]
    assert row["recent_amendments_latest"] == "2026-10-01"


def test_github_fetch_pins_a_commit(tmp_path: Path) -> None:
    api = "https://api.github.com/repos/o/r"
    raw = "https://raw.githubusercontent.com/o/r/"
    pages = {
        api: {"default_branch": "main"},
        f"{api}/commits/main": {"sha": "abc123"},
        f"{api}/git/trees/abc123?recursive=1": {
            "truncated": False,
            "tree": [
                {"path": "eng/acts/A-1.xml", "type": "blob", "size": 4},
                {"path": "eng/regulations/C.R.C.,_c._10.xml", "type": "blob", "size": 4},
                {"path": "fra/lois/A-1.xml", "type": "blob", "size": 4},
                {"path": "eng/acts", "type": "tree"},
            ],
        },
    }

    def opener(url: str, headers: dict[str, str], timeout: float) -> io.BytesIO:
        if url.startswith(raw):
            return io.BytesIO(b"<x/>")
        return io.BytesIO(json.dumps(pages[url]).encode())

    corpus = CorpusSpec.model_validate(
        {
            "id": "ca_laws",
            "title": "t",
            "sources": {"root": str(tmp_path), "snapshot": "current.txt"},
            "fetch": {
                "kind": "github",
                "listing": api,
                "files": raw,
                "paths": ["eng/acts/*.xml", "eng/regulations/*.xml"],
            },
            "provenance": {"base_url": raw},
        }
    )
    report = Fetcher(corpus, opener=opener, sleep=lambda _: None).run()
    assert report.downloaded == 2 and report.complete
    assert report.editions == {"eng/acts": "abc123", "eng/regulations": "abc123"}
    assert (tmp_path / "abc123/eng/regulations/C.R.C.,_c._10.xml").read_bytes() == b"<x/>"
    records = [json.loads(x) for x in (tmp_path / "fetch.jsonl").read_text().splitlines()]
    assert f"{raw}abc123/eng/regulations/C.R.C.,_c._10.xml" in {r["url"] for r in records}


def test_github_fetch_needs_paths(tmp_path: Path) -> None:
    raw = "https://raw.githubusercontent.com/o/r/"
    with pytest.raises(ValidationError, match="needs fetch.paths"):
        CorpusSpec.model_validate(
            {
                "id": "ca_laws",
                "title": "t",
                "sources": {"root": str(tmp_path), "snapshot": "current.txt"},
                "fetch": {
                    "kind": "github",
                    "listing": "https://api.github.com/repos/o/r",
                    "files": raw,
                },
                "provenance": {"base_url": raw},
            }
        )


def test_the_corpus_loads() -> None:
    corpus = CorpusSpec.find("ca_laws")
    assert corpus.extract.profile == "ca_laws@1.0"
    assert corpus.fetch is not None and corpus.fetch.kind == "github"


GROUPED = f"""<?xml version="1.0" encoding="utf-8"?>
<Statute {LIMS} lims:current-date="2026-10-19">
  <Identification>
    <ShortTitle>Elevators Act</ShortTitle>
    <Chapter><ConsolidatedNumber>H-5.6</ConsolidatedNumber></Chapter>
  </Identification>
  <Body>
    <Group1-Part><Heading><Label>PART I</Label><TitleText>Acts</TitleText></Heading>
      <Section><Label>1</Label><Text>The Elevator Act.</Text></Section>
    </Group1-Part>
    <Section><Label>2</Label><Text>After the Part.</Text></Section>
  </Body>
  <Schedule>
    <BillPiece><RelatedOrNotInForce>
      <Section><Label>2</Label><Text>The schedule is replaced by the following:</Text></Section>
      <Schedule><ScheduleFormHeading><Label>SCHEDULE</Label></ScheduleFormHeading>
        <Section><Label>1</Label><Text>Quoted.</Text></Section></Schedule>
      <Section><Label>3</Label><Text>Still the amending Act.</Text></Section>
    </RelatedOrNotInForce></BillPiece>
  </Schedule>
</Statute>
"""


def test_a_part_wrapped_in_an_element_is_the_same_unit(tmp_path: Path, profile: Any) -> None:
    doc = extracted(tmp_path, GROUPED, profile)
    paths = {b.text: b.heading_path for b in doc.blocks if b.type is BlockType.HEADING}
    assert paths["1"] == ["Elevators Act", "PART I Acts", "1"]
    assert paths["2"] == ["Elevators Act", "2"]


def test_a_quote_holding_a_schedule_stays_quoted(tmp_path: Path, profile: Any) -> None:
    doc = extracted(tmp_path, GROUPED, profile)
    sections = [b.attrs["number"] for b in doc.blocks if b.attrs.get("unit") == "section"]
    # Justice Canada closes its quotes: the quoted schedule ends nothing.
    assert sections == ["1", "2"]
    assert doc.extraction.params["quotes_ended"] == 0
    related = [b.text for b in doc.blocks if b.attrs.get("kind") == "related_provisions"]
    assert "Still the amending Act." in related


def test_a_long_table_splits_by_rows_with_its_header(tmp_path: Path, profile: Any) -> None:
    rows = "".join(
        f"<row><entry>{i}</entry><entry>Point {i} at latitude 49, longitude 123</entry></row>"
        for i in range(400)
    )
    xml = REGULATION.replace(
        "<tbody><row><entry>1</entry><entry>Licence</entry><entry>$250</entry></row></tbody>",
        f"<tbody>{rows}</tbody>",
    )
    doc = extracted(tmp_path, xml, profile)
    tables = [c for c in chunked(doc, profile) if c.attrs.get("kind") == "table"]
    assert len(tables) > 1
    assert all(c.token_count <= 1024 for c in tables)
    assert all(c.body.startswith("| Item | Fee |") for c in tables)
    assert [c.part for c in tables] == [(i + 1, len(tables)) for i in range(len(tables))]
    assert len({c.id for c in tables}) == len(tables)
