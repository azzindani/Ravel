"""The US CFR pipeline: fetch, the `cfr_xml` extractor, and the `markup` chunker.

`docs/SOURCE_US_CFR.md`. Hermetic: the XML is written here and the publisher's listing is a
fake opener, so the suite never touches the network.
"""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from canon import BlockType, Severity, validate
from chunk import Builder, ChunkConfig, embed_text, get
from extract.cfr import LEVELS, CfrXmlExtractor
from extract.structure import Structurer
from sources import scan
from sources.fetch import Fetcher
from spec import CorpusSpec, default_registry
from spec.models import MarkupIdentity

CFR = """<?xml version="1.0" encoding="UTF-8"?>
<CFRDOC>
  <AMDDATE>Oct. 1, 2025</AMDDATE>
  <FMTR><TITLEPG><TITLENUM>Title 9</TITLENUM><SUBJECT>Animals</SUBJECT>
    <REVISED>Revised as of January 1, 2026</REVISED></TITLEPG></FMTR>
  <TITLE>
    <LRH>9 CFR (1-1-26 Edition)</LRH>
    <CFRTITLE><TITLEHD>
      <HD SOURCE="HED">Title 9—Animals and Animal Products</HD></TITLEHD></CFRTITLE>
    <CHAPTER>
      <TOC><TOCHD>
        <HD SOURCE="HED">CHAPTER I—ANIMAL AND PLANT HEALTH INSPECTION SERVICE</HD></TOCHD>
        <CHAPTI><PT>1</PT><SUBJECT>Definition of terms</SUBJECT></CHAPTI></TOC>
      <SUBCHAP>
        <HD SOURCE="HED">SUBCHAPTER A—ANIMAL WELFARE</HD>
        <PART>
          <EAR>Pt. 1</EAR>
          <PRTPAGE P="7"/>
          <HD SOURCE="HED">PART 1—DEFINITION OF TERMS</HD>
          <CONTENTS><SECTNO>1.1</SECTNO><SUBJECT>Definitions.</SUBJECT></CONTENTS>
          <AUTH><HD SOURCE="HED">Authority:</HD><P>7 U.S.C. 2131-2159.</P></AUTH>
          <SOURCE><HD SOURCE="HED">Source:</HD><P>54 FR 36119, Aug. 31, 1989.</P></SOURCE>
          <SUBPART>
            <HD SOURCE="HED">Subpart A—General</HD>
            <SUBJGRP>
              <HD SOURCE="HED">Scope</HD>
              <SECTION>
                <SECTNO>§ 1.1</SECTNO><SUBJECT>Definitions.</SUBJECT>
                <P>(a) <E T="03">Act</E> means the Animal Welfare Act.</P>
                <PRTPAGE P="8"/>
                <P>(b) <E T="03">Dealer</E> means any person who sells animals.</P>
                <WEIRD>An element nobody declared.</WEIRD>
              </SECTION>
            </SUBJGRP>
            <SECTION>
              <SECTNO>§ 1.2</SECTNO><SUBJECT>Applicability.</SUBJECT>
              <P>This part applies to dealers.</P>
              <GPOTABLE COLS="2"><TTITLE>Table 1 to § 1.2</TTITLE>
                <BOXHD><CHED H="1">Class</CHED><CHED H="1">Fee</CHED></BOXHD>
                <ROW><ENT>A</ENT><ENT>$10</ENT></ROW></GPOTABLE>
              <CITA>[54 FR 36119, Aug. 31, 1989]</CITA>
            </SECTION>
            <SECTION><SECTNO>§ 1.3</SECTNO><RESERVED>[Reserved]</RESERVED></SECTION>
            <APPENDIX><EAR>Pt. 1, App. A</EAR>
              <HD SOURCE="HED">Appendix A to Part 1—Forms</HD><P>Form 7001.</P></APPENDIX>
          </SUBPART>
        </PART>
        <PART><RESERVED>PARTS 4-10 [RESERVED]</RESERVED></PART>
      </SUBCHAP>
    </CHAPTER>
    <CHAPTER>
      <TOC><TOCHD><HD SOURCE="HED">CHAPTER II—AGRICULTURAL MARKETING SERVICE</HD></TOCHD></TOC>
      <PART><HD SOURCE="HED">PART 201—STOCKYARDS</HD>
        <SECTION><SECTNO>§ 201.1</SECTNO><SUBJECT>Meaning of words.</SUBJECT>
          <P>Words in the singular include the plural.</P></SECTION></PART>
    </CHAPTER>
  </TITLE>
  <BMTR><P>Finding aids, not the law.</P></BMTR>
</CFRDOC>
"""

ECFR = """<?xml version="1.0" encoding="UTF-8"?>
<ECFR><AMDDATE>Aug. 17, 2026</AMDDATE><VOLUME N="1" AMDDATE="2026-08-17"/>
<DIV1 N="9" TYPE="TITLE"><HEAD>Title 9—Animals and Animal Products</HEAD>
<DIV3 N="I" TYPE="CHAPTER"><HEAD>CHAPTER I—ANIMAL AND PLANT HEALTH INSPECTION SERVICE</HEAD>
<DIV5 N="1" TYPE="PART"><HEAD>PART 1—DEFINITION OF TERMS</HEAD>
<AUTH><HED>Authority:</HED><PSPACE>7 U.S.C. 2131-2159.</PSPACE></AUTH>
<DIV8 N="1.1" TYPE="SECTION"><HEAD>§ 1.1   Definitions.</HEAD>
<P>(a) <I>Act</I> means the Animal Welfare Act.</P></DIV8>
<DIV8 N="1.3" TYPE="SECTION"><HEAD>§ 1.3 [Reserved]</HEAD></DIV8>
<DIV8 N="1.4" TYPE="SECTION"><HEAD>§ 1.4   Fees.</HEAD>
<TABLE><TR><TH>Class</TH><TH>Fee</TH></TR><TR><TD colspan="2">None</TD></TR></TABLE>
</DIV8></DIV5></DIV3></DIV1></ECFR>
"""


@pytest.fixture
def cfr_file(tmp_path: Path) -> Path:
    path = tmp_path / "CFR-2026-title9-vol1.xml"
    path.write_text(CFR, encoding="utf-8")
    return path


@pytest.fixture
def profile():  # type: ignore[no-untyped-def]
    return default_registry().get("us_cfr@1.0")


def extracted(path: Path, profile: Any):  # type: ignore[no-untyped-def]
    doc = CfrXmlExtractor().extract(path, title=path.stem, url="https://example.test/x.xml")
    return Structurer(profile).apply(doc)


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------
def test_headings_carry_what_the_markup_states(cfr_file: Path, profile: Any) -> None:
    doc = extracted(cfr_file, profile)
    heads = {b.text: b for b in doc.blocks if b.type is BlockType.HEADING}
    title = heads["Title 9—Animals and Animal Products"]
    assert {"unit": "title", "number": "9", "date": "2026-01-01"}.items() <= title.attrs.items()
    chapter = heads["CHAPTER I—ANIMAL AND PLANT HEALTH INSPECTION SERVICE"]
    assert chapter.attrs["name"] == "ANIMAL AND PLANT HEALTH INSPECTION SERVICE"
    section = heads["§ 1.1 Definitions."]
    assert {
        "unit": "section",
        "number": "1.1",
        "marker": "§ 1.1",
    }.items() <= section.attrs.items()
    appendix = heads["Appendix A to Part 1—Forms"]
    assert appendix.attrs["marker"] == "Appendix A to Part 1"
    assert section.level == LEVELS["section"]


def test_a_closed_unit_leaves_the_path(cfr_file: Path, profile: Any) -> None:
    """§ 1.2 follows the subject group in the same subpart; it is not inside it."""
    doc = extracted(cfr_file, profile)
    by_text = {b.text: b for b in doc.blocks}
    assert "Scope" in by_text["(a) Act means the Animal Welfare Act."].heading_path
    assert "Scope" not in by_text["This part applies to dealers."].heading_path
    assert not [i for i in validate(doc) if i.severity is Severity.ERROR]


def test_closing_early_is_only_valid_for_markup(cfr_file: Path, profile: Any) -> None:
    raw = CfrXmlExtractor().extract(cfr_file, title="x", url="https://example.test/x.xml")
    # Without the structurer's `structure: markup`, the same paths are a defect.
    assert any(i.rule == "heading_path" for i in validate(raw))


def test_nothing_is_lost_silently(cfr_file: Path, profile: Any) -> None:
    doc = extracted(cfr_file, profile)
    texts = [b.text for b in doc.blocks]
    assert "An element nobody declared." in texts
    assert any("WEIRD" in w for w in doc.extraction.warnings)
    assert "Authority: 7 U.S.C. 2131-2159." in texts
    assert "Finding aids, not the law." not in texts
    assert doc.extraction.params["reserved"] == {"section": 1, "part": 1}
    assert doc.extraction.params["headings_undeclared"] == 0
    assert doc.extraction.params["headings_level_mismatch"] == 0


def test_pages_and_tables(cfr_file: Path, profile: Any) -> None:
    doc = extracted(cfr_file, profile)
    by_text = {b.text: b for b in doc.blocks}
    assert by_text["PART 1—DEFINITION OF TERMS"].page == 7
    assert by_text["(b) Dealer means any person who sells animals."].page == 8
    (table,) = doc.tables
    assert table.caption == "Table 1 to § 1.2"
    assert [c.text for c in table.cells] == ["Class", "Fee", "A", "$10"]


def test_malformed_xml_fails_the_document(tmp_path: Path) -> None:
    from extract.base import ExtractionFailed

    path = tmp_path / "bad.xml"
    path.write_text(CFR.replace("</SECTION>", "", 1), encoding="utf-8")
    with pytest.raises(ExtractionFailed, match="well-formed"):
        CfrXmlExtractor().extract(path, title="x")


def test_ecfr_reads_to_the_same_identity(tmp_path: Path, profile: Any) -> None:
    path = tmp_path / "title-9.xml"
    path.write_text(ECFR, encoding="utf-8")
    doc = extracted(path, profile)
    heads = {b.text: b.attrs for b in doc.blocks if b.type is BlockType.HEADING}
    assert heads["§ 1.1 Definitions."]["number"] == "1.1"
    assert heads["Title 9—Animals and Animal Products"]["date"] == "2026-08-17"
    assert doc.extraction.params["reserved"] == {"section": 1}
    (table,) = doc.tables
    assert table.n_cols == 2 and table.header_rows == 1
    assert not [i for i in validate(doc) if i.severity is Severity.ERROR]


def test_profile_levels_match_the_extractor(profile: Any) -> None:
    assert {u.name: u.level for u in profile.units} == LEVELS


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------
def chunks_of(path: Path, profile: Any) -> list[Any]:
    doc = extracted(path, profile)
    entry = get("markup")
    builder = Builder(
        doc=doc,
        chunker=entry.id,
        version=entry.version,
        config=ChunkConfig(max_tokens=1024, min_tokens=0),
        profile=profile,
    )
    return list(entry.fn(doc, builder))


def test_identity_is_read_per_part(cfr_file: Path, profile: Any) -> None:
    chunks = {c.identifier: c for c in chunks_of(cfr_file, profile) if not c.attrs.get("kind")}
    first, other = chunks["9 CFR 1.1"], chunks["9 CFR 201.1"]
    assert first.regulation_number == "9 CFR Part 1"
    assert first.enacting_body == "ANIMAL AND PLANT HEALTH INSPECTION SERVICE"
    assert first.about == "DEFINITION OF TERMS"
    assert (first.year, first.effective_date) == (2026, "2026-01-01")
    # The second part of the same volume is another agency's, and says so.
    assert other.regulation_number == "9 CFR Part 201"
    assert other.enacting_body == "AGRICULTURAL MARKETING SERVICE"
    assert chunks["9 CFR Appendix A to Part 1"].article == "Appendix A to Part 1"
    assert chunks["9 CFR Part 1"].body.startswith("Authority:")


def test_sections_are_never_merged(cfr_file: Path, profile: Any) -> None:
    chunks = [c for c in chunks_of(cfr_file, profile) if not c.attrs.get("kind")]
    cited = [c.identifier for c in chunks]
    assert len(cited) == len(set(cited)), "one citation per chunk"
    assert next(c for c in chunks if c.identifier == "9 CFR 1.1").article == "§ 1.1"


def test_a_table_carries_its_sections_identity(cfr_file: Path, profile: Any) -> None:
    (table,) = [c for c in chunks_of(cfr_file, profile) if c.attrs.get("kind") == "table"]
    assert table.identifier == "9 CFR 1.2"
    assert table.regulation_number == "9 CFR Part 1"


def test_embed_text_header(cfr_file: Path, profile: Any) -> None:
    chunk = next(c for c in chunks_of(cfr_file, profile) if c.identifier == "9 CFR 1.1")
    header = embed_text(chunk, profile).split("\n\n")[0].splitlines()
    assert header[0] == "Citation: 9 CFR 1.1"
    assert header[1] == "Agency: ANIMAL AND PLANT HEALTH INSPECTION SERVICE"
    assert header[2].startswith("Location: Title 9—Animals")


def test_a_template_with_a_gap_yields_nothing() -> None:
    in_force = {"title": {"number": "9"}, "part": {"number": ""}}
    assert MarkupIdentity.render("{title.number} CFR Part {part.number}", in_force) is None
    assert MarkupIdentity.render("CFR", in_force) == "CFR"


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------
G = "https://www.govinfo.gov/bulkdata"


def listing(files: list[dict[str, Any]]) -> bytes:
    return json.dumps({"files": files}).encode()


def folder(name: str, link: str) -> dict[str, Any]:
    return {"name": name, "folder": True, "link": link}


def xml(name: str, year: str, title: int, size: int) -> dict[str, Any]:
    link = f"{G}/CFR/{year}/title-{title}/{name}"
    return {"name": name, "folder": False, "link": link, "size": size}


BODY = b"<CFRDOC/>" * 3


class FakeWeb:
    """The publisher: two editions, title 1 only in the older one."""

    def __init__(self) -> None:
        self.pages: dict[str, bytes] = {
            f"{G}/json/CFR": listing(
                [folder("2026", f"{G}/json/CFR/2026"), folder("2025", f"{G}/json/CFR/2025")]
            ),
            f"{G}/json/CFR/2026": listing([folder("title-2", f"{G}/json/CFR/2026/title-2")]),
            f"{G}/json/CFR/2025": listing(
                [
                    folder("title-1", f"{G}/json/CFR/2025/title-1"),
                    folder("title-2", f"{G}/json/CFR/2025/title-2"),
                ]
            ),
            f"{G}/json/CFR/2026/title-2": listing(
                [xml("CFR-2026-title2-vol01.xml", "2026", 2, len(BODY))]
            ),
            f"{G}/json/CFR/2025/title-1": listing(
                [xml("CFR-2025-title1-vol1.xml", "2025", 1, len(BODY))]
            ),
            f"{G}/json/CFR/2025/title-2": listing(
                [xml("CFR-2025-title2-vol1.xml", "2025", 2, len(BODY))]
            ),
            f"{G}/CFR/2026/title-2/CFR-2026-title2-vol01.xml": BODY,
            f"{G}/CFR/2025/title-1/CFR-2025-title1-vol1.xml": BODY,
        }
        self.requested: list[str] = []

    def __call__(self, url: str, headers: dict[str, str], timeout: float) -> io.BytesIO:
        self.requested.append(url)
        if url not in self.pages:
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)  # type: ignore[arg-type]
        return io.BytesIO(self.pages[url])


def cfr_corpus(root: Path) -> CorpusSpec:
    return CorpusSpec.model_validate(
        {
            "id": "us_cfr",
            "title": "t",
            "sources": {"root": str(root), "include": ["**/*.xml"], "snapshot": "current.txt"},
            "fetch": {"kind": "govinfo_cfr", "listing": f"{G}/json/CFR", "files": f"{G}/CFR/"},
            "provenance": {"base_url": f"{G}/CFR/"},
        }
    )


def test_fetch_takes_the_newest_edition_of_each_title(tmp_path: Path) -> None:
    web = FakeWeb()
    report = Fetcher(cfr_corpus(tmp_path), opener=web, sleep=lambda _: None).run()
    assert report.editions == {"title-2": "2026", "title-1": "2025"}
    assert report.downloaded == 2 and report.complete
    snapshot = (tmp_path / "current.txt").read_text(encoding="utf-8").splitlines()
    assert [s for s in snapshot if not s.startswith("#")] == [
        "2025/title-1/CFR-2025-title1-vol1.xml",
        "2026/title-2/CFR-2026-title2-vol01.xml",
    ]
    records = [json.loads(line) for line in (tmp_path / "fetch.jsonl").read_text().splitlines()]
    assert {r["url"] for r in records} == {
        f"{G}/CFR/2025/title-1/CFR-2025-title1-vol1.xml",
        f"{G}/CFR/2026/title-2/CFR-2026-title2-vol01.xml",
    }
    # The superseded 2025 title 2 was never fetched.
    assert not any("2025/title-2/CFR" in u for u in web.requested if u.endswith(".xml"))


def test_fetch_is_recurrent_and_never_overwrites(tmp_path: Path) -> None:
    corpus = cfr_corpus(tmp_path)
    Fetcher(corpus, opener=FakeWeb(), sleep=lambda _: None).run()
    again = Fetcher(corpus, opener=FakeWeb(), sleep=lambda _: None).run()
    assert (again.downloaded, again.present) == (0, 2)

    target = tmp_path / "2026/title-2/CFR-2026-title2-vol01.xml"
    target.write_bytes(b"republished, a different size")
    third = Fetcher(corpus, opener=FakeWeb(), sleep=lambda _: None).run()
    assert len(third.conflicts) == 1
    assert target.read_bytes() == b"republished, a different size"


def test_a_failed_file_leaves_no_snapshot(tmp_path: Path) -> None:
    web = FakeWeb()
    del web.pages[f"{G}/CFR/2025/title-1/CFR-2025-title1-vol1.xml"]
    report = Fetcher(cfr_corpus(tmp_path), opener=web, sleep=lambda _: None).run()
    assert not report.complete and report.snapshot is None
    assert not (tmp_path / "current.txt").exists()
    assert not list(tmp_path.rglob("*.part"))


def test_fetch_files_must_be_the_provenance_root(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="must equal provenance.base_url"):
        CorpusSpec.model_validate(
            {
                "id": "us_cfr",
                "title": "t",
                "sources": {"root": str(tmp_path), "snapshot": "current.txt"},
                "fetch": {
                    "kind": "govinfo_cfr",
                    "listing": f"{G}/json/CFR",
                    "files": f"{G}/CFR/",
                },
                "provenance": {"base_url": "hf://datasets/someone/mirror/"},
            }
        )


def test_ecfr_plan_pins_each_title_to_its_own_date(tmp_path: Path) -> None:
    api = "https://www.ecfr.gov/api/versioner/v1"
    titles = {
        "titles": [
            {"number": 1, "up_to_date_as_of": "2026-11-19", "reserved": False},
            {"number": 35, "up_to_date_as_of": "2026-11-19", "reserved": True},
        ]
    }
    corpus = CorpusSpec.model_validate(
        {
            "id": "us_ecfr",
            "title": "t",
            "sources": {"root": str(tmp_path), "snapshot": "current.txt"},
            "fetch": {"kind": "ecfr", "listing": f"{api}/titles.json", "files": f"{api}/full/"},
            "provenance": {"base_url": f"{api}/full/"},
        }
    )
    fetcher = Fetcher(
        corpus, opener=lambda *_: io.BytesIO(json.dumps(titles).encode()), sleep=lambda _: None
    )
    wanted, editions = fetcher.plan()
    assert [w.path for w in wanted] == ["2026-11-19/title-1.xml"]
    assert wanted[0].url == f"{api}/full/2026-11-19/title-1.xml"


def test_scan_reads_only_the_snapshot_and_reports_a_missing_file(tmp_path: Path) -> None:
    (tmp_path / "a.xml").write_text("<x/>")
    (tmp_path / "old.xml").write_text("<y/>")
    files, report = scan(tmp_path, ["**/*.xml"], only=["a.xml", "gone.xml"])
    assert [f.path for f in files] == ["a.xml"]
    assert report.unreadable and "gone.xml" in report.unreadable[0]


def test_a_partly_published_edition_is_held_back(tmp_path: Path) -> None:
    """An edition appears volume by volume: fewer volumes than the last edition is not done."""
    web = FakeWeb()
    web.pages[f"{G}/json/CFR/2025/title-2"] = listing(
        [
            xml("CFR-2025-title2-vol1.xml", "2025", 2, len(BODY)),
            xml("CFR-2025-title2-vol2.xml", "2025", 2, len(BODY)),
        ]
    )
    web.pages[f"{G}/CFR/2025/title-2/CFR-2025-title2-vol1.xml"] = BODY
    web.pages[f"{G}/CFR/2025/title-2/CFR-2025-title2-vol2.xml"] = BODY
    report = Fetcher(cfr_corpus(tmp_path), opener=web, sleep=lambda _: None).run()
    assert report.editions["title-2"] == "2025"
    assert report.held == {"title-2": "2026 has 1 of 2 volumes; using 2025"}
    assert report.downloaded == 3
