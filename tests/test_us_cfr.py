"""The US CFR pipeline: fetch, the `xml` extractor and its dialects, `markup`, signals.

`SOURCE_US_CFR.md`. Hermetic: the XML is written here and the publisher's listing is a
fake opener, so the suite never touches the network.
"""

from __future__ import annotations

import importlib
import io
import json
import urllib.error
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from canon import BlockType, Severity, validate
from chunk import Builder, ChunkConfig, embed_text, get
from enrich import signals
from extract.structure import Structurer
from extract.xml import XmlExtractor
from sources import scan
from sources.fetch import Fetcher
from spec import CorpusSpec, Registry, RegistryError, default_registry
from spec.loader import load_spec
from spec.models import MarkupIdentity
from spec.xml_dialect import XmlDialect

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
                <P>(c)(1) Fees: § 1.2 and 7 CFR 2.17; see 7 U.S.C. 2132, Pub. L. 89-544.</P>
                <P>(2) A second paragraph.</P>
                <P>(i) Under (c)(2).</P>
                <P>(ii) Also under (c)(2).</P>
                <WEIRD>An element nobody declared.</WEIRD>
              </SECTION>
            </SUBJGRP>
            <SECTION>
              <SECTNO>§ 1.2</SECTNO><SUBJECT>Applicability.</SUBJECT>
              <P>This part applies to dealers.</P>
              <GPOTABLE COLS="2"><TTITLE>Table 1 to § 1.2</TTITLE>
                <BOXHD><CHED H="1">Class</CHED><CHED H="1">Fee</CHED></BOXHD>
                <ROW><ENT>A</ENT><ENT>$10</ENT></ROW></GPOTABLE>
              <CITA>[54 FR 36119, Aug. 31, 1989, as amended at 60 FR 13893, Mar. 15, 1995]
              </CITA>
              <EFFDNOTP><HD SOURCE="HED">Effective Date Note:</HD>
                <P>At 90 FR 1000, Jan. 2, 2025, § 1.2 was revised, effective Mar. 3, 2025.</P>
                <REVTXT><SECTION><SECTNO>§ 1.2</SECTNO><SUBJECT>Applicability.</SUBJECT>
                  <P>This part applies to dealers and exhibitors.</P></SECTION></REVTXT>
              </EFFDNOTP>
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
    doc = XmlExtractor().extract(
        path, title=path.stem, url="https://example.test/x.xml", profile=profile
    )
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
    assert section.level == 8  # the profile's `structure.units`, not the code's


def test_a_closed_unit_leaves_the_path(cfr_file: Path, profile: Any) -> None:
    """§ 1.2 follows the subject group in the same subpart; it is not inside it."""
    doc = extracted(cfr_file, profile)
    by_text = {b.text: b for b in doc.blocks}
    assert "Scope" in by_text["(a) Act means the Animal Welfare Act."].heading_path
    assert "Scope" not in by_text["This part applies to dealers."].heading_path
    assert not [i for i in validate(doc) if i.severity is Severity.ERROR]


def test_closing_early_is_only_valid_for_markup(cfr_file: Path, profile: Any) -> None:
    raw = XmlExtractor().extract(cfr_file, title="x", url="x", profile=profile)
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


def test_malformed_xml_fails_the_document(tmp_path: Path, profile: Any) -> None:
    from extract.base import ExtractionFailed

    path = tmp_path / "bad.xml"
    path.write_text(CFR.replace("</SECTION>", "", 1), encoding="utf-8")
    with pytest.raises(ExtractionFailed, match="well-formed"):
        XmlExtractor().extract(path, title="x", profile=profile)


def test_a_root_no_dialect_reads_fails_the_document(tmp_path: Path, profile: Any) -> None:
    from extract.base import ExtractionFailed

    path = tmp_path / "fr.xml"
    path.write_text("<FEDREG><P>text</P></FEDREG>", encoding="utf-8")
    with pytest.raises(ExtractionFailed, match="no dialect"):
        XmlExtractor().extract(path, title="x", profile=profile)
    with pytest.raises(ExtractionFailed, match="dialect"):
        XmlExtractor().extract(path, title="x")


def test_notes_keep_their_kind_and_quoted_sections_stay_text(
    cfr_file: Path, profile: Any
) -> None:
    doc = extracted(cfr_file, profile)
    note = next(b for b in doc.blocks if b.text.startswith("Effective Date Note:"))
    assert note.attrs["kind"] == "effective_date_note"
    # The section an amendment will put in force is quoted text, never a second § 1.2.
    sections = [b.text for b in doc.blocks if b.attrs.get("unit") == "section"]
    assert sections.count("§ 1.2 Applicability.") == 1
    quoted = next(b for b in doc.blocks if b.text.endswith("dealers and exhibitors."))
    assert quoted.attrs["kind"] == "revised_text"
    assert doc.extraction.params["dialect"] == "gpo_cfr@1.0"


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


# ---------------------------------------------------------------------------
# Dialects: the element knowledge is the registry's
# ---------------------------------------------------------------------------
def test_the_profile_embeds_its_dialects(profile: Any) -> None:
    dialects = profile.extract_options("xml")["dialects"]
    assert [d["id"] for d in dialects] == ["gpo_cfr", "ecfr"]
    assert dialects[0]["units"]["tags"]["SECTION"] == "section"


def _registry_copy(tmp_path: Path) -> Path:
    import shutil

    root = Path(__file__).resolve().parents[1] / "registry"
    shutil.copytree(root / "dialects", tmp_path / "dialects")
    (tmp_path / "profiles").mkdir()
    shutil.copy(root / "profiles" / "us_cfr@1.0.yaml", tmp_path / "profiles")
    return tmp_path


def test_a_corrected_dialect_re_extracts(tmp_path: Path) -> None:
    """The hash covers the dialect's rules, not its name."""
    root = _registry_copy(tmp_path)
    before = Registry.load(root).get("us_cfr@1.0").structure_hash
    path = root / "dialects" / "gpo_cfr@1.0.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("CHAPTI, ", ""), encoding="utf-8")
    assert Registry.load(root).get("us_cfr@1.0").structure_hash != before


def test_a_dialect_unit_the_profile_does_not_declare_is_refused(tmp_path: Path) -> None:
    root = _registry_copy(tmp_path)
    path = root / "dialects" / "ecfr@1.0.yaml"
    text = path.read_text(encoding="utf-8").replace("SUBJGRP: subjgrp", "SUBJGRP: annex")
    path.write_text(text, encoding="utf-8")
    with pytest.raises(RegistryError, match="annex"):
        load_spec(root / "profiles" / "us_cfr@1.0.yaml")


def test_a_missing_dialect_is_named(tmp_path: Path) -> None:
    root = _registry_copy(tmp_path)
    (root / "dialects" / "ecfr@1.0.yaml").unlink()
    with pytest.raises(RegistryError, match="no dialect ecfr@1.0"):
        load_spec(root / "profiles" / "us_cfr@1.0.yaml")


def test_an_element_in_two_roles_is_refused() -> None:
    base: dict[str, Any] = {
        "id": "x",
        "version": "1.0",
        "title": "x",
        "document": {"detect": {"root": "^X$"}},
        "units": {"tags": {"S": "section"}},
        "headings": {"default": {"first": ["HD"]}},
    }
    XmlDialect.model_validate(base)
    with pytest.raises(ValidationError, match="both"):
        XmlDialect.model_validate({**base, "skip": ["P"], "leaves": ["P"]})
    with pytest.raises(ValidationError, match="default"):
        XmlDialect.model_validate({**base, "headings": {"section": {"first": ["HD"]}}})


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


def test_a_snapshot_is_matched_by_name_not_by_walking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "t/texte/struct").mkdir(parents=True)
    (tmp_path / "t/texte/struct/A.xml").write_text("<x/>")
    (tmp_path / "t/article").mkdir(parents=True)
    (tmp_path / "t/article/B.xml").write_text("<y/>")

    def refuse(*_: object, **__: object) -> None:
        raise AssertionError("a snapshot scan walked the root")

    # ! By module: `sources.scan` as a dotted name is the re-exported function.
    monkeypatch.setattr(importlib.import_module("sources.scan"), "walk", refuse)
    listed = ["t/texte/struct/A.xml", "t/article/B.xml"]
    files, _ = scan(tmp_path, ["**/texte/struct/*.xml"], only=listed)
    # The list cannot widen the corpus: B.xml is listed but no pattern takes it.
    assert [f.path for f in files] == ["t/texte/struct/A.xml"]


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
    assert report.held == {"title-2": "2026 lacks vol 2 (1 files, 2 before); using 2025"}
    assert report.downloaded == 3


def test_a_stray_volume_does_not_make_an_edition_complete(tmp_path: Path) -> None:
    """govinfo's 2026 title 14: `vol0` (a copy of vol3) and no `vol1`. Five files against
    five, and a volume of law missing; the numbers tell, the count does not."""
    web = FakeWeb()
    web.pages[f"{G}/json/CFR/2026/title-2"] = listing(
        [
            xml("CFR-2026-title2-vol0.xml", "2026", 2, len(BODY)),
            xml("CFR-2026-title2-vol2.xml", "2026", 2, len(BODY)),
        ]
    )
    web.pages[f"{G}/json/CFR/2025/title-2"] = listing(
        [
            xml("CFR-2025-title2-vol1.xml", "2025", 2, len(BODY)),
            xml("CFR-2025-title2-vol2.xml", "2025", 2, len(BODY)),
        ]
    )
    for name in ("CFR-2025-title2-vol1.xml", "CFR-2025-title2-vol2.xml"):
        web.pages[f"{G}/CFR/2025/title-2/{name}"] = BODY
    report = Fetcher(cfr_corpus(tmp_path), opener=web, sleep=lambda _: None).run()
    assert report.editions["title-2"] == "2025"
    assert report.held["title-2"].startswith("2026 lacks vol 1 ")


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------
def signal_rows(path: Path, profile: Any) -> dict[str, dict[str, Any]]:
    doc = extracted(path, profile)
    entry = get("markup")
    builder = Builder(
        doc=doc,
        chunker=entry.id,
        version=entry.version,
        config=ChunkConfig(max_tokens=1024, min_tokens=0),
        profile=profile,
    )
    chunks = list(entry.fn(doc, builder))
    rows = signals.compute(chunks, [doc], profile.spec.signals)
    return {
        c.identifier: row
        for c, row in zip(chunks, rows, strict=True)
        if not c.attrs.get("kind")
    }


def test_signals_references(cfr_file: Path, profile: Any) -> None:
    row = signal_rows(cfr_file, profile)["9 CFR 1.1"]
    # A bare `§ 1.2` is read in the chunk's own title.
    assert sorted(row["cfr_references"]) == ["7 CFR 2.17", "9 CFR 1.2"]
    assert row["usc_references"] == ["7 U.S.C. 2132"]
    assert row["public_laws"] == ["Pub. L. 89-544"]


def test_signals_a_parts_authority_holds_for_each_section(cfr_file: Path, profile: Any) -> None:
    rows = signal_rows(cfr_file, profile)
    for cited in ("9 CFR 1.1", "9 CFR 1.2", "9 CFR Appendix A to Part 1"):
        assert rows[cited]["authority_usc"] == ["7 U.S.C. 2131-2159"]
        assert rows[cited]["source"] == "Source: 54 FR 36119, Aug. 31, 1989."
    # Another part, another authority: none stated.
    assert rows["9 CFR 201.1"]["authority_usc"] == []


def test_signals_amendment_history(cfr_file: Path, profile: Any) -> None:
    row = signal_rows(cfr_file, profile)["9 CFR 1.2"]
    assert row["amendments"] == ["54 FR 36119, Aug. 31, 1989", "60 FR 13893, Mar. 15, 1995"]
    assert row["amendments_latest"] == "1995-03-15"
    assert row["effective_date_note"].startswith("Effective Date Note: At 90 FR 1000")


def test_signals_defined_terms_must_be_italic(cfr_file: Path, profile: Any) -> None:
    row = signal_rows(cfr_file, profile)["9 CFR 1.1"]
    assert row["defined_terms"] == ["Act", "Dealer"]


def test_signals_paragraph_range(cfr_file: Path, profile: Any) -> None:
    row = signal_rows(cfr_file, profile)["9 CFR 1.1"]
    assert (row["paragraph_first"], row["paragraph_last"]) == ("(a)", "(c)(2)(ii)")


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        # `(i)` after `(h)` continues the letters; after `(1)` it opens the numerals.
        (["(g) x", "(h) x", "(i) x"], "(i)"),
        (["(h) x", "(1) x", "(i) x"], "(h)(1)(i)"),
        (["(h) x", "(1) x", "(i) x", "(ii) x", "(2) x"], "(h)(2)"),
        # GPO sets the fifth level's numbers in italics: `( 1 )` once whitespace is read.
        (["(a)(1)(i) x", "(A) x", "( 1 ) italic"], "(a)(1)(i)(A)(1)"),
        (["(z) x", "(aa) x"], "(aa)"),
        (["(u) x", "(v) x"], "(v)"),
    ],
)
def test_outline_reads_a_marker_by_what_is_in_force(
    profile: Any, lines: list[str], expected: str
) -> None:
    outline = signals.Outline(profile.spec.signals.outline)
    found = [outline.read(line) for line in lines]
    assert found[-1] == expected


def test_outline_lets_the_next_marker_break_a_tie(profile: Any) -> None:
    """`(i)` under `(h)(1)`: a numeral when `(ii)` follows, the next letter when `(j)` does."""
    spec = profile.spec.signals.outline
    as_numeral, as_letter = signals.Outline(spec), signals.Outline(spec)
    for outline, after in ((as_numeral, "(ii) x"), (as_letter, "(j) x")):
        outline.read("(h) x")
        outline.read("(1) x")
        outline.read("(i) x", after)
    assert "".join(f"({m})" for _, m in as_numeral.stack) == "(h)(1)(i)"
    assert "".join(f"({m})" for _, m in as_letter.stack) == "(i)"


RUNAWAY = """<?xml version="1.0" encoding="UTF-8"?>
<CFRDOC>
  <FMTR><TITLEPG><TITLENUM>Title 9</TITLENUM></TITLEPG></FMTR>
  <TITLE><CFRTITLE><TITLEHD><HD SOURCE="HED">Title 9—Animals</HD></TITLEHD></CFRTITLE>
    <CHAPTER><TOC><TOCHD><HD SOURCE="HED">CHAPTER I—SERVICE</HD></TOCHD></TOC>
      <PART><HD SOURCE="HED">PART 5—FIRST</HD>
        <SUBPART><HD SOURCE="HED">Subpart B—Procedures</HD>
          <SECTION><SECTNO>§ 5.1</SECTNO><SUBJECT>Old.</SUBJECT><P>Old text.</P>
            <EFFDNOTP><HD SOURCE="HED">Effective Date Note:</HD><P>{note} was revised.</P>
              <REVTXT>
                {quoted}
                <SUBPART><HD SOURCE="HED">Subpart C—Next</HD>
                  <SECTION><SECTNO>§ 5.20</SECTNO><SUBJECT>Real.</SUBJECT>
                    <P>Real C.</P></SECTION>
                </SUBPART>
                <PART><HD SOURCE="HED">PART 6—SECOND</HD>
                  <SECTION><SECTNO>§ 6.1</SECTNO><SUBJECT>Real.</SUBJECT>
                    <P>Real six.</P></SECTION>
                </PART>
              </REVTXT></EFFDNOTP></SECTION></SUBPART></PART></CHAPTER></TITLE>
</CFRDOC>
"""


@pytest.mark.parametrize(
    ("quoted", "real"),
    [
        # The quote revises subpart B, its own: subpart C cannot be part of it.
        (
            '<SUBPART><HD SOURCE="HED">Subpart B—Procedures</HD><SECTION><SECTNO>§ 5.1'
            "</SECTNO><SUBJECT>New.</SUBJECT><P>New text.</P></SECTION></SUBPART>",
            ["§ 5.1 Old.", "§ 5.20 Real.", "§ 6.1 Real."],
        ),
        # The quote revises one section: the subpart after it is shallower, and ends it.
        (
            "<SECTION><SECTNO>§ 5.1</SECTNO><SUBJECT>New.</SUBJECT><P>New text.</P></SECTION>",
            ["§ 5.1 Old.", "§ 5.20 Real.", "§ 6.1 Real."],
        ),
    ],
)
def test_a_quote_left_open_ends_at_a_unit_it_cannot_hold(
    tmp_path: Path, profile: Any, quoted: str, real: list[str]
) -> None:
    """GPO leaves a REVTXT open to the end of a volume (40 CFR vol. 33, 34 CFR vol. 3)."""
    path = tmp_path / "CFR-2025-title9-vol9.xml"
    path.write_text(RUNAWAY.format(note="Subpart B", quoted=quoted), encoding="utf-8")
    doc = extracted(path, profile)
    sections = [b.text for b in doc.blocks if b.attrs.get("unit") == "section"]
    assert sections == real
    assert "§ 5.1 New." in [b.text for b in doc.blocks if b.attrs.get("kind") == "revised_text"]
    assert doc.extraction.params["quotes_ended"] == 1
    assert not [i for i in validate(doc) if i.severity is Severity.ERROR]


SOURCES = """<?xml version="1.0" encoding="UTF-8"?>
<CFRDOC>
  <FMTR><TITLEPG><TITLENUM>Title 9</TITLENUM></TITLEPG></FMTR>
  <TITLE><CFRTITLE><TITLEHD><HD SOURCE="HED">Title 9—Animals</HD></TITLEHD></CFRTITLE>
    <CHAPTER><TOC><TOCHD><HD SOURCE="HED">CHAPTER I—SERVICE</HD></TOCHD></TOC>
      <PART><HD SOURCE="HED">PART 5—FIRST</HD>
        <SOURCE><HD SOURCE="HED">Source:</HD><P>10 FR 100, Jan. 1, 1945.</P></SOURCE>
        <SUBPART><HD SOURCE="HED">Subpart A—Old</HD>
          <SECTION><SECTNO>§ 5.1</SECTNO><SUBJECT>One.</SUBJECT><P>Text.</P></SECTION>
        </SUBPART>
        <SUBPART><HD SOURCE="HED">Subpart B—New</HD>
          <SOURCE><HD SOURCE="HED">Source:</HD><P>80 FR 800, Jan. 1, 2015.</P></SOURCE>
          <SECTION><SECTNO>§ 5.20</SECTNO><SUBJECT>Two.</SUBJECT><P>Text.</P></SECTION>
        </SUBPART></PART></CHAPTER></TITLE>
</CFRDOC>
"""


def test_signals_a_subparts_own_source_is_the_nearest(tmp_path: Path, profile: Any) -> None:
    path = tmp_path / "CFR-2025-title9-vol8.xml"
    path.write_text(SOURCES, encoding="utf-8")
    rows = signal_rows(path, profile)
    assert rows["9 CFR 5.1"]["source"] == "Source: 10 FR 100, Jan. 1, 1945."
    assert rows["9 CFR 5.20"]["source"] == "Source: 80 FR 800, Jan. 1, 2015."


@pytest.mark.parametrize(
    ("heading", "marker", "number"),
    [
        ("§ 52.21 Prevention.", "§ 52.21", "52.21"),
        # 11,623 sections print a bare number: title 48 throughout, 40 CFR part 86.
        ("86.1860-17 How to comply.", "86.1860-17", "86.1860-17"),
        ("52.223-13—52.223-18 [Reserved]", "52.223-13—52.223-18", "52.223-13—52.223-18"),
        ("§§ 1.1-1.3 [Reserved]", "§§ 1.1-1.3", "1.1-1.3"),
        # 14 CFR part 241 numbers its sections in words; kept as printed.
        ("Sec. 1-1 Purpose.", "Sec. 1-1", "Sec. 1-1"),
    ],
)
def test_section_labels(profile: Any, heading: str, marker: str, number: str) -> None:
    from extract.xml import _label
    from spec.xml_dialect import XmlOptions

    dialect = XmlOptions.model_validate(profile.extract_options("xml")).dialects[0]
    found = _label(heading, dialect.label_rule("section"), None)
    assert (found[0], found[2]) == (marker, number)


def test_outline_reads_a_marker_printed_after_a_heading(profile: Any) -> None:
    outline = signals.Outline(profile.spec.signals.outline)
    lines = ["(f) Delays.", "(g) Invoice. (1) The Contractor shall.", "(i) Name;", "(iv) Date;"]
    assert [outline.read(line) for line in lines][-1] == "(g)(1)(iv)"
    outline = signals.Outline(profile.spec.signals.outline)
    lines = ["(a) Std.", "(5) Prohibited (Source: 1 CFR 2.3) —(i) Use of data.", "(A) Except"]
    assert [outline.read(line) for line in lines][-1] == "(a)(5)(i)(A)"


def test_outline_a_section_that_opens_below_the_first_level(profile: Any) -> None:
    """A section whose paragraphs start at `(1)`: `(i)` under it is a numeral, not a letter."""
    outline = signals.Outline(profile.spec.signals.outline)
    found = [outline.read(line) for line in ["(1) x", "(i) x", "(ii) x", "(iii) x", "(2) x"]]
    assert found == ["(1)", "(1)(i)", "(1)(ii)", "(1)(iii)", "(2)"]
