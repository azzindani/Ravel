"""Japan's e-Gov law XML: `fetch.kind: archive_url` with the publisher's index, the url map
that cites each version at its page, and the `jp_law` dialect (`SOURCE_JP_EGOV.md`)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

import pytest

from chunk import Builder, ChunkConfig, get
from extract.structure import Structurer
from extract.xml import XmlExtractor
from sources.fetch import Fetcher
from spec import default_registry
from spec.corpus import CorpusSpec

LISTING = "https://laws.example.jp/bulkdownload?file_section=1"
BASE = "https://laws.example.jp/"
HEADER = "法令種別,法令番号,法令名,法令ID,本文URL,未施行"

LAW = """<?xml version="1.0" encoding="UTF-8"?>
<Law Era="Showa" Lang="ja" LawType="Act" Num="285" Year="26">
<LawNum>昭和二十六年法律第二百八十五号</LawNum>
<LawBody><LawTitle Kana="はくぶつかんほう">博物館法</LawTitle>
<TOC><TOCLabel>目次</TOCLabel><TOCChapter Num="1"><ChapterTitle>第一章　総則</ChapterTitle>
<ArticleRange>（第一条―第二条）</ArticleRange></TOCChapter></TOC>
<MainProvision><Chapter Num="1"><ChapterTitle>第一章　総則</ChapterTitle>
<Article Num="1"><ArticleCaption>（目的）</ArticleCaption><ArticleTitle>第一条</ArticleTitle>
<Paragraph Num="1"><ParagraphNum/><ParagraphSentence><Sentence Num="1">この法律は、<Ruby>博<Rt>はく</Rt></Ruby>物館の発達を図る。</Sentence></ParagraphSentence></Paragraph>
</Article>
<Article Num="2"><ArticleCaption>（定義）</ArticleCaption><ArticleTitle>第二条</ArticleTitle>
<Paragraph Num="1"><ParagraphNum/><ParagraphSentence><Sentence Num="1">次に掲げる。</Sentence></ParagraphSentence>
<Item Num="1"><ItemTitle>一</ItemTitle><ItemSentence><Sentence Num="1">資料の収集</Sentence></ItemSentence></Item>
</Paragraph>
<Paragraph Num="2"><ParagraphNum>２</ParagraphNum><ParagraphSentence><Sentence Num="1">公立博物館とする。</Sentence></ParagraphSentence></Paragraph>
</Article></Chapter></MainProvision>
<SupplProvision><SupplProvisionLabel>附　則</SupplProvisionLabel>
<Paragraph Num="1"><ParagraphNum/><ParagraphSentence><Sentence Num="1">この法律は、公布の日から施行する。</Sentence></ParagraphSentence></Paragraph>
</SupplProvision>
<SupplProvision AmendLawNum="令和四年四月一五日法律第二四号"><SupplProvisionLabel>附　則</SupplProvisionLabel>
<Article Num="1"><ArticleCaption>（施行期日）</ArticleCaption><ArticleTitle>第一条</ArticleTitle>
<Paragraph Num="1"><ParagraphNum/><ParagraphSentence><Sentence Num="1">令和五年四月一日から施行する。</Sentence></ParagraphSentence></Paragraph>
</Article></SupplProvision>
</LawBody></Law>
"""  # noqa: E501 -- the publisher's XML, one element per line as it prints it


def _zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _archive(rows: list[str], extra: dict[str, bytes] | None = None) -> bytes:
    index = ("﻿" + "\n".join([HEADER, *rows]) + "\n").encode("utf-8")
    return _zip(
        {
            "all_law_list.csv": index,
            "326AC_20230401_504AC/326AC_20230401_504AC.xml": LAW.encode("utf-8"),
            "326AC_20270101_507AC/326AC_20270101_507AC.xml": LAW.encode("utf-8"),
            **(extra or {}),
        }
    )


ROWS = [
    f"法律,昭和二十六年法律第二百八十五号,博物館法,326AC,{BASE}law/326AC/20230401_504AC,",
    f"法律,昭和二十六年法律第二百八十五号,博物館法,326AC,{BASE}law/326AC/20270101_507AC,○",
]


def _corpus(root: Path, **index: Any) -> CorpusSpec:
    return CorpusSpec.model_validate(
        {
            "id": "jp_egov",
            "title": "t",
            "sources": {"root": str(root), "snapshot": "current.txt"},
            "fetch": {
                "kind": "archive_url",
                "listing": LISTING,
                "files": BASE,
                "archive": "all_xml",
                "edition": "20261122",
                "paths": ["*"],
                "index": {
                    "file": "all_law_list.csv",
                    "where": {"未施行": ""},
                    "url": "本文URL",
                    "member": r"/law/(?P<id>[^/]+)/(?P<version>[^/]+)$",
                    "document": "{id}_{version}/{id}_{version}.xml",
                    **index,
                },
            },
            "provenance": {
                "base_url": BASE,
                "archive_suffix": ".zip",
                "url_map": "url_map.jsonl",
            },
        }
    )


def _fetch(root: Path, archive: bytes, **index: Any) -> tuple[CorpusSpec, Any]:
    asked: list[str] = []

    def opener(url: str, headers: dict[str, str], timeout: float) -> io.BytesIO:
        asked.append(url)
        return io.BytesIO(archive)

    corpus = _corpus(root, **index)
    report = Fetcher(corpus, opener=opener, sleep=lambda _: None).run()
    assert asked == [LISTING]
    return corpus, report


def test_the_index_picks_the_version_in_force_and_cites_its_page(tmp_path: Path) -> None:
    corpus, report = _fetch(tmp_path, _archive(ROWS))
    assert report.editions == {"archive": "20261122"}
    assert (tmp_path / "all_xml_20261122.zip").is_file()
    member = "all_xml_20261122/326AC_20230401_504AC/326AC_20230401_504AC.xml"
    lines = (tmp_path / "current.txt").read_text(encoding="utf-8").splitlines()
    # The version not yet in force (`未施行`) is unpacked but not in the snapshot.
    assert [x for x in lines if not x.startswith("#")] == [member]
    assert corpus.url_for(member) == f"{BASE}law/326AC/20230401_504AC"


def test_a_path_the_index_does_not_name_has_no_citation(tmp_path: Path) -> None:
    corpus, _ = _fetch(tmp_path, _archive(ROWS))
    future = "all_xml_20261122/326AC_20270101_507AC/326AC_20270101_507AC.xml"
    with pytest.raises(ValueError, match="never made up"):
        corpus.url_for(future)


def test_a_row_naming_no_member_stops_the_fetch(tmp_path: Path) -> None:
    rows = [*ROWS, f"法律,x,y,999AC,{BASE}law/999AC/20200101_000,"]
    with pytest.raises(ValueError, match="not in the archive"):
        _fetch(tmp_path, _archive(rows))
    assert not (tmp_path / "current.txt").exists()


def test_index_needs_a_url_map(tmp_path: Path) -> None:
    spec = _corpus(tmp_path).model_dump()
    spec["provenance"]["url_map"] = None
    with pytest.raises(ValueError, match="url_map"):
        CorpusSpec.model_validate(spec)


def _law(tmp_path: Path) -> tuple[Any, Any]:
    path = tmp_path / "law.xml"
    path.write_text(LAW, encoding="utf-8")
    profile = default_registry().get("jp_law@1.0")
    raw = XmlExtractor().extract(path, title="x", url="u", profile=profile)
    return raw, Structurer(profile).apply(raw)


def test_articles_cite_as_japanese_practice_does(tmp_path: Path) -> None:
    raw, doc = _law(tmp_path)
    assert raw.extraction.warnings == []
    profile = default_registry().get("jp_law@1.0")
    e = get("markup")
    b = Builder(
        doc=doc,
        chunker=e.id,
        version=e.version,
        config=ChunkConfig(max_tokens=1024, min_tokens=0),
        profile=profile,
    )
    chunks = {c.identifier: c for c in e.fn(doc, b)}
    assert "博物館法第一条" in chunks and "博物館法第二条" in chunks
    first = chunks["博物館法第一条"]
    assert (first.regulation_type, first.regulation_number, first.about) == (
        "Act",
        "昭和二十六年法律第二百八十五号",
        "博物館法",
    )
    # ! A supplementary provision's 第一条 is not the law's: it cites the amending law, and
    # the one made with the law cites `制定時`.
    assert "博物館法附則（令和四年四月一五日法律第二四号）第一条" in chunks
    assert "博物館法附則（制定時）" in chunks
    # The ruby reading is dropped; the table of contents is not read.
    assert "物館の発達" in first.body and "はく" not in first.body
    assert "ArticleRange" not in str(raw.extraction.params) and "目次" not in first.body


def test_japanese_is_counted_by_character_and_other_text_as_before() -> None:
    from chunk.base import estimate_tokens

    # ! `\w+` read a run of kanji and kana as one word: articles never split.
    assert estimate_tokens("この法律は、社会教育法の精神に基づき") >= 18
    assert estimate_tokens("Wajib Pajak wajib menyelenggarakan pembukuan.") == 8
