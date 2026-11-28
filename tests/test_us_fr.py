"""The US Federal Register pipeline: the `govinfo_tree` fetch, the `gpo_fr` dialect, markup.

`SOURCE_US_FR.md`. Hermetic: the issue XML is written here and govinfo's listing is a fake
opener, so the suite never touches the network.
"""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from typing import Any

from canon import BlockType, Severity, validate
from extract.structure import Structurer
from extract.xml import XmlExtractor
from sources.fetch import Fetcher
from spec import CorpusSpec, default_registry

G = "https://www.govinfo.gov/bulkdata"

ISSUE = """<?xml version="1.0" encoding="UTF-8"?>
<FEDREG>
  <VOL>89</VOL><NO>44</NO><DATE>Tuesday, March 5, 2024</DATE><UNITNAME>Contents</UNITNAME>
  <CNTNTS><AGCY><EAR>Agriculture</EAR></AGCY></CNTNTS>
  <VOL>89</VOL><NO>44</NO><DATE>Tuesday, March 5, 2024</DATE><UNITNAME>Rules</UNITNAME>
  <RULES>
    <RULE>
      <PREAMB>
        <PRTPAGE P="15725"/>
        <AGENCY TYPE="F">DEPARTMENT OF TRANSPORTATION</AGENCY>
        <SUBAGY>Federal Aviation Administration</SUBAGY>
        <CFR>14 CFR Part 39</CFR>
        <DEPDOC>[Docket No. FAA-2024-0456]</DEPDOC>
        <RIN>RIN 2120-AA64</RIN>
        <SUBJECT>Airworthiness Directives; Austro Engine GmbH Engines</SUBJECT>
        <ACT><HD SOURCE="HED">ACTION:</HD><P>Final rule.</P></ACT>
        <SUM><HD SOURCE="HED">SUMMARY:</HD><P>The FAA is adopting a new airworthiness
          directive.</P></SUM>
      </PREAMB>
      <SUPLINF><HD SOURCE="HED">SUPPLEMENTARY INFORMATION:</HD>
        <P>Background text that runs <PRTPAGE P="15726"/> onto the next page.</P>
        <REGTEXT TITLE="14" PART="39"><AMDPAR>1. The authority citation continues.</AMDPAR>
          <SECTION><SECTNO>§ 39.13</SECTNO><SUBJECT>[Amended]</SUBJECT></SECTION></REGTEXT>
      </SUPLINF>
      <FRDOC>[FR Doc. 2024-04579 Filed 2-29-24; 11:15 am]</FRDOC>
      <BILCOD>BILLING CODE 4910-13-P</BILCOD>
    </RULE>
  </RULES>
  <NOTICES>
    <NOTICE>
      <PREAMB>
        <AGENCY TYPE="N">DEPARTMENT OF AGRICULTURE</AGENCY>
        <SUBJECT>Notice of Public Meeting</SUBJECT>
      </PREAMB>
      <SUPLINF><P>The meeting is open to the public.</P></SUPLINF>
      <FRDOC>[FR Doc. 2024-04600 Filed 3-4-24; 8:45 am]</FRDOC>
    </NOTICE>
  </NOTICES>
</FEDREG>
"""


def _documents(tmp_path: Path) -> tuple[Any, list[Any]]:
    path = tmp_path / "FR-2024-03-05.xml"
    path.write_text(ISSUE, encoding="utf-8")
    profile = default_registry().get("us_fr@1.0")
    doc = Structurer(profile).apply(
        XmlExtractor().extract(path, title=path.stem, url="u", profile=profile)
    )
    headings = [b for b in doc.blocks if b.type is BlockType.HEADING]
    return doc, headings


def test_an_issue_is_a_unit_and_each_document_one_under_it(tmp_path: Path) -> None:
    doc, headings = _documents(tmp_path)
    assert [e for e in validate(doc) if e.severity is Severity.ERROR] == []
    issue, rule, notice = headings
    assert (issue.attrs["unit"], issue.text, issue.attrs["number"]) == (
        "issue",
        "Tuesday, March 5, 2024",
        "89",
    )
    assert issue.attrs["date"] == "2024-03-05"
    assert rule.attrs["unit"] == notice.attrs["unit"] == "document"
    assert rule.heading_path == ["Tuesday, March 5, 2024", rule.text]


def test_the_markup_states_number_agency_docket_and_action(tmp_path: Path) -> None:
    _, (_, rule, _) = _documents(tmp_path)
    # `~pattern` with a `value` group takes the part printed inside other text.
    assert rule.attrs["number"] == "2024-04579"
    assert rule.attrs["rin"] == "2120-AA64"
    assert rule.attrs["agency"] == "DEPARTMENT OF TRANSPORTATION"
    assert rule.attrs["docket"] == "[Docket No. FAA-2024-0456]"
    assert rule.attrs["action"] == "Final rule."


def test_a_document_is_cited_at_the_page_it_opens_on(tmp_path: Path) -> None:
    """! The notice prints no page break of its own: it opens on 15726, the page the
    break *inside the rule's paragraph* began. Its first break, had it one, would be the
    next page; the walker's running page never sees a break inside a paragraph."""
    _, (_, rule, notice) = _documents(tmp_path)
    assert rule.attrs["page"] == "15725"
    assert notice.attrs["page"] == "15726"


def test_running_matter_and_contents_are_not_text(tmp_path: Path) -> None:
    doc, _ = _documents(tmp_path)
    body = "\n".join(b.text for b in doc.blocks if b.type is not BlockType.HEADING)
    assert "Rules" not in body.split()  # UNITNAME
    assert "Agriculture" not in body  # the table of contents
    assert "BILLING CODE" not in body


class FakeWeb:
    def __init__(self) -> None:
        def listing(files: list[dict[str, Any]]) -> bytes:
            return json.dumps({"files": files}).encode()

        def folder(name: str, link: str) -> dict[str, Any]:
            return {"name": name, "folder": True, "link": link}

        def xml(name: str, link: str) -> dict[str, Any]:
            return {"name": name, "folder": False, "link": link}

        self.pages: dict[str, bytes] = {
            f"{G}/json/FR": listing(
                [folder("2025", f"{G}/json/FR/2025"), folder("2024", f"{G}/json/FR/2024")]
            ),
            f"{G}/json/FR/2025": listing([folder("01", f"{G}/json/FR/2025/01")]),
            f"{G}/json/FR/2025/01": listing(
                [
                    xml("FR-2025-01-02.xml", f"{G}/FR/2025/01/FR-2025-01-02.xml"),
                    xml("FR-2025-01.zip", f"{G}/FR/2025/01/FR-2025-01.zip"),
                ]
            ),
            f"{G}/FR/2025/01/FR-2025-01-02.xml": ISSUE.encode(),
        }
        self.requested: list[str] = []

    def __call__(self, url: str, headers: dict[str, str], timeout: float) -> io.BytesIO:
        self.requested.append(url)
        if url not in self.pages:
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)  # type: ignore[arg-type]
        return io.BytesIO(self.pages[url])


def fr_corpus(root: Path) -> CorpusSpec:
    return CorpusSpec.model_validate(
        {
            "id": "us_fr",
            "title": "t",
            "sources": {
                "root": str(root),
                "include": ["**/FR-*.xml"],
                "snapshot": "current.txt",
            },
            "fetch": {
                "kind": "govinfo_tree",
                "listing": f"{G}/json/FR",
                "files": f"{G}/FR/",
                "paths": ["2025/*/FR-2025-??-??.xml"],
            },
            "provenance": {"base_url": f"{G}/FR/"},
        }
    )


def test_the_tree_is_walked_only_where_the_globs_can_match(tmp_path: Path) -> None:
    web = FakeWeb()
    report = Fetcher(fr_corpus(tmp_path), opener=web, sleep=lambda _: None).run()
    assert report.downloaded == 1 and report.complete
    snapshot = (tmp_path / "current.txt").read_text(encoding="utf-8").splitlines()
    assert [s for s in snapshot if not s.startswith("#")] == ["2025/01/FR-2025-01-02.xml"]
    # ! 2024 is never listed, and the monthly zip beside the issues is never fetched.
    assert f"{G}/json/FR/2024" not in web.requested
    assert not any(u.endswith(".zip") for u in web.requested)


def test_the_corpus_definition_loads() -> None:
    spec = CorpusSpec.find("us_fr")
    assert spec.fetch is not None and spec.fetch.kind == "govinfo_tree"
    assert spec.fetch.files == spec.provenance.base_url
