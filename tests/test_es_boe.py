"""Spain's BOE consolidated legislation: `fetch.kind: json_api`, a paged JSON listing of every
law at its current edition, and the `boe` dialect, whose text is a flat run of typed
`<bloque>`s each holding every wording it has had (`docs/sources/SOURCE_ES_BOE.md`).

Hermetic: the API is a fake opener.
"""

from __future__ import annotations

import io
import json
import urllib.parse
from pathlib import Path
from typing import Any

from chunk import Builder, ChunkConfig, get
from extract.structure import Structurer
from extract.xml import XmlExtractor
from sources.fetch import Fetcher
from spec import default_registry
from spec.corpus import CorpusSpec

API = "https://www.boe.example.es/datosabiertos/api/legislacion-consolidada"

LAW = """<?xml version="1.0" encoding="utf-8"?>
<response><status><code>200</code><text>ok</text></status><data>
<metadatos><identificador>BOE-A-2000-1</identificador><ambito codigo="1">Estatal</ambito>
<departamento codigo="7723">Jefatura del Estado</departamento>
<rango codigo="1300">Ley</rango><fecha_disposicion>20000110</fecha_disposicion>
<numero_oficial>1/2000</numero_oficial>
<titulo>Ley 1/2000, de 10 de enero, de pruebas.</titulo>
<url_eli>https://www.boe.es/eli/es/l/2000/01/10/1</url_eli></metadatos>
<analisis><materias><materia codigo="1">Pruebas</materia></materias></analisis>
<texto>
<bloque id="pr" tipo="preambulo" titulo="PREÁMBULO"><version id_norma="BOE-A-2000-1" fecha_publicacion="20000111" fecha_vigencia="20000111">
<p class="parrafo">La presente ley regula las pruebas.</p></version></bloque>
<bloque id="ci" tipo="encabezado" titulo="CAPÍTULO I"><version id_norma="BOE-A-2000-1" fecha_publicacion="20000111" fecha_vigencia="20000111">
<p class="capitulo_num">CAPÍTULO I</p><p class="capitulo_tit">Disposiciones generales</p></version></bloque>
<bloque id="a1" tipo="precepto" titulo="Artículo 1"><version id_norma="BOE-A-2000-1" fecha_publicacion="20000111" fecha_vigencia="20000111">
<p class="articulo">Artículo 1. Objeto.</p><p class="parrafo">Texto derogado de la primera redacción.</p></version>
<version id_norma="BOE-A-2010-9" fecha_publicacion="20100301" fecha_vigencia="20100302">
<p class="articulo">Artículo 1. Objeto.</p><p class="parrafo">Esta ley tiene por objeto las pruebas, en su redacción vigente.</p></version></bloque>
<bloque id="a2" tipo="precepto" titulo="Artículo 2"><version id_norma="BOE-A-2000-1" fecha_publicacion="20000111" fecha_vigencia="20000111">
<p class="articulo">Artículo 2 bis. Ámbito.</p><p class="parrafo">Se aplica en todo el territorio.</p></version></bloque>
<bloque id="da1" tipo="precepto" titulo="Disposición adicional primera"><version id_norma="BOE-A-2000-1" fecha_publicacion="20000111" fecha_vigencia="20000111">
<p class="articulo">Disposición adicional primera. Plazos.</p><p class="parrafo">Los plazos se cuentan por días hábiles.</p></version></bloque>
<bloque id="df" tipo="precepto" titulo="Disposición final"><version id_norma="BOE-A-2000-1" fecha_publicacion="20000111" fecha_vigencia="20000111">
<p class="capitulo">DISPOSICIÓN FINAL</p><p class="parrafo">Entra en vigor al día siguiente.</p></version></bloque>
</texto></data></response>
"""  # noqa: E501 -- the publisher's XML, one bloque per line as it prints them


def _law(tmp_path: Path) -> tuple[Any, dict[str, Any]]:
    path = tmp_path / "BOE-A-2000-1.xml"
    path.write_text(LAW, encoding="utf-8")
    profile = default_registry().get("es_boe@1.0")
    raw = XmlExtractor().extract(path, title="x", url="u", profile=profile)
    doc = Structurer(profile).apply(raw)
    e = get("markup")
    b = Builder(
        doc=doc,
        chunker=e.id,
        version=e.version,
        config=ChunkConfig(max_tokens=4096, min_tokens=0),
        profile=profile,
    )
    return raw, {c.identifier: c for c in e.fn(doc, b)}


def test_articles_cite_as_spanish_practice_does(tmp_path: Path) -> None:
    _, chunks = _law(tmp_path)
    one = chunks["Ley 1/2000, Artículo 1"]
    assert (one.regulation_type, one.regulation_number, one.year) == ("Ley", "1/2000", 2000)
    assert "Ley 2/2000" not in chunks and "Ley 1/2000, Artículo 2 bis" in chunks
    assert one.heading_path is not None and "CAPÍTULO I Disposiciones generales" in (
        one.heading_path
    )


def test_only_the_wording_in_force_is_read(tmp_path: Path) -> None:
    """! Every wording an article has had is in the file, oldest first: only the last is
    the law (`document.latest`)."""
    raw, chunks = _law(tmp_path)
    one = chunks["Ley 1/2000, Artículo 1"]
    assert "redacción vigente" in one.body and "derogado" not in one.body
    assert raw.extraction.params["skipped"]["superseded: data/texto/bloque/version"] == 1


def test_a_heading_is_not_read_again_as_text(tmp_path: Path) -> None:
    """! The heading is a paragraph of the article's own text (`headings.*.take`)."""
    _, chunks = _law(tmp_path)
    bodies = [c.body for c in chunks.values()]
    assert all("Artículo 1." not in b and "CAPÍTULO I" not in b for b in bodies)


def test_a_disposition_is_under_no_chapter(tmp_path: Path) -> None:
    _, chunks = _law(tmp_path)
    extra = chunks["Ley 1/2000, Disposición adicional primera"]
    final = chunks["Ley 1/2000, DISPOSICIÓN FINAL"]
    for chunk in (extra, final):
        assert chunk.heading_path is not None and "CAPÍTULO" not in chunk.heading_path
    assert "días hábiles" in extra.body and "Entra en vigor" in final.body


def boe_corpus(tmp_path: Path) -> CorpusSpec:
    return CorpusSpec.model_validate(
        {
            "id": "es_boe_test",
            "title": "BOE fixture",
            "sources": {
                "root": str(tmp_path / "src"),
                "include": ["**/*.xml"],
                "snapshot": "current.txt",
            },
            "fetch": {
                "kind": "json_api",
                "listing": API,
                "files": f"{API}/id/",
                "items": "data",
                "id_field": "identificador",
                "edition_field": "fecha_actualizacion",
                "item_url": f"{API}/id/{{id}}",
                "item_path": "{id}/{edition}.xml",
                "page_size": 2,
                "accept": "application/xml",
                "delay_s": 0,
            },
            "provenance": {"base_url": f"{API}/id/", "url_map": "fetch.urls.jsonl"},
            "extract": {"profile": "es_boe@1.0", "extractors": ["xml"]},
        }
    )


def test_every_listed_law_is_fetched_at_its_newest_edition(tmp_path: Path) -> None:
    pages = {
        0: [
            {"identificador": "BOE-A-2000-1", "fecha_actualizacion": "20250101T000000Z"},
            {"identificador": "BOE-A-2000-2", "fecha_actualizacion": "20240101T000000Z"},
        ],
        # A law listed twice keeps its newest edition; a short page ends the listing.
        2: [{"identificador": "BOE-A-2000-1", "fecha_actualizacion": "20260101T000000Z"}],
    }
    accepted: list[str] = []

    def opener(url: str, headers: dict[str, str], timeout: float) -> io.BytesIO:
        if url.startswith(f"{API}?"):
            offset = int(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["offset"][0])
            return io.BytesIO(json.dumps({"data": pages.get(offset, [])}).encode())
        accepted.append(headers["Accept"])
        return io.BytesIO(LAW.encode())

    corpus = boe_corpus(tmp_path)
    report = Fetcher(corpus, opener=opener, sleep=lambda _: None).run()
    assert not report.failed and report.downloaded == 2
    root = tmp_path / "src"
    assert (root / "BOE-A-2000-1" / "20260101T000000Z.xml").is_file()
    assert not (root / "BOE-A-2000-1" / "20250101T000000Z.xml").exists()
    assert accepted == ["application/xml", "application/xml"]
    cited = {
        r["path"]: r["url"]
        for r in map(json.loads, (root / "fetch.urls.jsonl").read_text().splitlines())
    }
    assert cited["BOE-A-2000-1/20260101T000000Z.xml"] == f"{API}/id/BOE-A-2000-1"
