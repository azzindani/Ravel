"""EUR-Lex through the Cellar: the `sparql` fetch kind (`SOURCE_EU_LEX.md`).

Hermetic: the SPARQL endpoint and the Formex packages are a fake opener, so the suite never
touches the network.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse
import zipfile
from pathlib import Path

import pytest

from sources.fetch import Fetcher
from spec import CorpusSpec

C = "http://cellar.example/resource/cellar"
ENDPOINT = "https://cellar.example/sparql"
ACTS = "SELECT ?url ?group ?edition WHERE { ?a ?b ?c } ORDER BY ?url"
CONSOLIDATIONS = "SELECT ?url ?group ?edition WHERE { ?d ?e ?f } ORDER BY ?url"


def package(*members: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zipped:
        for name in members:
            zipped.writestr(name, f"<ACT><TITLE>{name}</TITLE></ACT>")
    return buffer.getvalue()


class FakeCellar:
    """Two queries' rows, paged two at a time, and one package per manifestation."""

    def __init__(self) -> None:
        def row(url: str, group: str, edition: str) -> dict[str, dict[str, str]]:
            out = {"group": {"value": group}, "edition": {"value": edition}}
            if url:
                out["url"] = {"value": url}
            return out

        self.rows = {
            ACTS: [
                row(f"{C}/a1.0006.01/zip", "2016R0679", "00000000"),  # amended since
                row(f"{C}/a2.0006.01/zip", "2019R0001", "00000000"),  # never amended
                row("", "1958R0001", "00000000"),  # in force, no English as adopted
            ],
            CONSOLIDATIONS: [
                row(f"{C}/c1.0022.01/zip", "2016R0679", "20160504"),
                row(f"{C}/c2.0022.01/zip", "2016R0679", "20180523"),  # the newest
                row(f"{C}/c3.0006.01/zip", "1958R0001", "20130701"),
                row(f"{C}/c4.0006.01/zip", "1999R9999", "20200101"),  # repealed: no act
            ],
        }
        self.packages = {
            f"{C}/a1.0006.01/zip": package("L_2016119EN.01000101.xml", "L.doc.xml"),
            f"{C}/a2.0006.01/zip": package("L_2019001EN.01000101.xml", "L.doc.xml"),
            f"{C}/c2.0022.01/zip": package("CL2016R0679EN0000020.0001.xml", "CL.doc.xml"),
            f"{C}/c3.0006.01/zip": package("CL1958R0001EN0030010.0001.xml", "CL.doc.xml"),
        }
        self.requested: list[str] = []

    def __call__(self, url: str, headers: dict[str, str], timeout: float) -> io.BytesIO:
        self.requested.append(url)
        if url.startswith(ENDPOINT):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["query"][0]
            # ! Paged from outside: the wrapper is a subquery, so the original query's own
            # ORDER BY is inside it and the LIMIT/OFFSET are the fetcher's.
            assert query.startswith("SELECT * WHERE {")
            inner = next(q for q in self.rows if q in query)
            limit, offset = (int(x) for x in query.rsplit("LIMIT ", 1)[1].split(" OFFSET "))
            page = self.rows[inner][offset : offset + limit]
            return io.BytesIO(json.dumps({"results": {"bindings": page}}).encode())
        if url not in self.packages:
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)  # type: ignore[arg-type]
        return io.BytesIO(self.packages[url])


def eu_corpus(root: Path) -> CorpusSpec:
    return CorpusSpec.model_validate(
        {
            "id": "eu_lex",
            "title": "t",
            "sources": {"root": str(root), "include": ["**/*.xml"], "snapshot": "current.txt"},
            "fetch": {
                "kind": "sparql",
                "listing": ENDPOINT,
                "files": f"{C}/",
                "queries": [ACTS, CONSOLIDATIONS],
                "page_size": 2,
                "paths": ["*.xml"],
                "documents": ["*[0-9].xml"],
            },
            "provenance": {"base_url": f"{C}/", "archive_suffix": "/zip"},
        }
    )


def test_the_newest_text_of_every_act_in_force_and_nothing_else(tmp_path: Path) -> None:
    """! The first query names the acts, the second only gives them newer texts: an act is
    read as its newest consolidation, else as adopted; a repealed act's consolidation and an
    older consolidation are never fetched, and an act whose only English is a consolidation
    is still in."""
    web = FakeCellar()
    report = Fetcher(eu_corpus(tmp_path), opener=web, sleep=lambda _: None).run()

    assert report.complete and report.downloaded == 3
    downloaded = {u for u in web.requested if not u.startswith(ENDPOINT)}
    assert downloaded == {f"{C}/a2.0006.01/zip", f"{C}/c2.0022.01/zip", f"{C}/c3.0006.01/zip"}
    snapshot = (tmp_path / "current.txt").read_text(encoding="utf-8").splitlines()
    assert [s for s in snapshot if not s.startswith("#")] == [
        "a2.0006.01/L_2019001EN.01000101.xml",
        "c2.0022.01/CL2016R0679EN0000020.0001.xml",
        "c3.0006.01/CL1958R0001EN0030010.0001.xml",
    ]


def test_a_member_is_cited_as_the_package_it_was_read_from(tmp_path: Path) -> None:
    spec = eu_corpus(tmp_path)
    assert (
        spec.url_for("c2.0022.01/CL2016R0679EN0000020.0001.xml")
        == f"{C}/c2.0022.01/zip#CL2016R0679EN0000020.0001.xml"
    )


def test_a_query_must_be_ordered_and_leave_the_paging_to_the_fetcher(tmp_path: Path) -> None:
    raw = eu_corpus(tmp_path).model_dump()
    raw["fetch"]["queries"] = ["SELECT ?url ?group ?edition WHERE { ?a ?b ?c }"]
    with pytest.raises(ValueError, match="ORDER BY"):
        CorpusSpec.model_validate(raw)
    raw["fetch"]["queries"] = [ACTS + " LIMIT 5"]
    with pytest.raises(ValueError, match="LIMIT"):
        CorpusSpec.model_validate(raw)


def test_the_corpus_definition_loads() -> None:
    spec = CorpusSpec.find("eu_lex")
    assert spec.fetch is not None and spec.fetch.kind == "sparql"
    assert len(spec.fetch.queries) == 2
