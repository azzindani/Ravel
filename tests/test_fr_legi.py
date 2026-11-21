"""France's LEGI: the `legi` dialect's links (a text assembled from its folder of files),
`text.split_at`, and the `fr_legi` profile (`docs/SOURCE_FR_LEGI.md`)."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from canon import Severity, validate
from chunk import Builder, ChunkConfig, get
from extract.structure import Structurer
from extract.xml import XmlExtractor
from sources.fetch import Fetcher
from spec import CorpusSpec, default_registry

TEXT = "LEGITEXT000005634379"

STRUCT = f"""<?xml version="1.0" encoding="UTF-8"?>
<TEXTELR>
<META><META_COMMUN><ID>{TEXT}</ID><NATURE>CODE</NATURE></META_COMMUN>
<META_SPEC><META_TEXTE_CHRONICLE><NUM/><DATE_TEXTE>2000-09-18</DATE_TEXTE>
<DERNIERE_MODIFICATION>2026-10-01</DERNIERE_MODIFICATION></META_TEXTE_CHRONICLE></META_SPEC>
</META>
<VERSIONS><VERSION etat="VIGUEUR">
<LIEN_TXT debut="2000-09-21" fin="2999-01-01" id="{TEXT}" num=""/>
</VERSION></VERSIONS>
<STRUCT>
<LIEN_SECTION_TA cid="LEGISCTA000006083281" debut="2000-09-21" etat="VIGUEUR" fin="2999-01-01"
  id="LEGISCTA000006083281" niv="1"
  url="/LEGI/SCTA/00/00/06/08/32/LEGISCTA000006083281.xml">Partie législative</LIEN_SECTION_TA>
<LIEN_SECTION_TA debut="2000-09-21" etat="ABROGE" fin="2010-01-01" id="LEGISCTA000006083299"
  niv="1" url="/LEGI/SCTA/00/00/06/08/32/LEGISCTA000006083299.xml">Abrogée</LIEN_SECTION_TA>
<LIEN_ART debut="2000-09-21" etat="VIGUEUR" fin="2999-01-01" id="LEGIARTI000006219000"
  num="L1" origine="LEGI"/>
<LIEN_ART debut="2000-09-21" etat="VIGUEUR" fin="2999-01-01" id="LEGIARTI000006219001"
  num="L2" origine="LEGI"/>
</STRUCT>
</TEXTELR>
"""

VERSION = f"""<?xml version="1.0" encoding="UTF-8"?>
<TEXTE_VERSION><META><META_COMMUN><ID>{TEXT}</ID></META_COMMUN>
<META_SPEC><META_TEXTE_VERSION><TITRE>Code de commerce</TITRE>
<TITREFULL>Code de commerce (version longue)</TITREFULL></META_TEXTE_VERSION></META_SPEC></META>
<VISAS><CONTENU>Vu la Constitution</CONTENU></VISAS>
</TEXTE_VERSION>
"""

SECTION = """<?xml version="1.0" encoding="UTF-8"?>
<SECTION_TA><ID>LEGISCTA000006083281</ID><TITRE_TA>Partie législative</TITRE_TA>
<CONTEXTE><TEXTE cid="x"><TITRE_TXT>Code de commerce</TITRE_TXT></TEXTE></CONTEXTE>
<STRUCTURE_TA>
<LIEN_ART debut="2015-08-08" etat="MODIFIE" fin="2020-01-01" id="LEGIARTI000030927100"
  num="L233-3" origine="LEGI"/>
<LIEN_ART debut="2020-01-01" etat="VIGUEUR" fin="2999-01-01" id="LEGIARTI000030927196"
  num="L233-3" origine="LEGI"/>
</STRUCTURE_TA>
</SECTION_TA>
"""


def _article(id_: str, num: str, body: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ARTICLE><META><META_COMMUN><ID>{id_}</ID></META_COMMUN>
<META_SPEC><META_ARTICLE><NUM>{num}</NUM><ETAT>VIGUEUR</ETAT>
<DATE_DEBUT>2020-01-01</DATE_DEBUT></META_ARTICLE></META_SPEC></META>
<CONTEXTE><TEXTE><TITRE_TXT>Code de commerce</TITRE_TXT></TEXTE></CONTEXTE>
<VERSIONS><VERSION etat="MODIFIE"><LIEN_ART id="LEGIARTI000030927100" etat="MODIFIE"
  num="{num}"/></VERSION></VERSIONS>
<BLOC_TEXTUEL><CONTENU>{body}</CONTENU></BLOC_TEXTUEL>
</ARTICLE>
"""


def _article_path(root: Path, id_: str) -> Path:
    d = id_[8:]
    return (
        root / "article/LEGI/ARTI" / d[0:2] / d[2:4] / d[4:6] / d[6:8] / d[8:10] / f"{id_}.xml"
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def profile() -> Any:
    return default_registry().get("fr_legi@1.0")


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    root = tmp_path / "JORFTEXT000000000001"
    _write(root / f"texte/struct/{TEXT}.xml", STRUCT)
    _write(root / f"texte/version/{TEXT}.xml", VERSION)
    _write(root / "section_ta/LEGI/SCTA/00/00/06/08/32/LEGISCTA000006083281.xml", SECTION)
    _write(
        _article_path(root, "LEGIARTI000030927196"),
        _article(
            "LEGIARTI000030927196",
            "L233-3",
            "<p>I.- Toute personne est considérée :<br/><br/>1° Lorsqu'elle détient la "
            "<i>majorité</i> ;<br/>2° Lorsqu'elle dispose seule de la majorité.</p>",
        ),
    )
    _write(
        _article_path(root, "LEGIARTI000006219000"),
        _article("LEGIARTI000006219000", "L1", "<p>Premier article.</p>"),
    )
    # LEGIARTI000006219001 is linked but absent: a gap that shows.
    return root


def _doc(folder: Path, profile: Any) -> Any:
    path = folder / f"texte/struct/{TEXT}.xml"
    raw = XmlExtractor().extract(path, title=TEXT, url="u", profile=profile)
    return raw, Structurer(profile).apply(raw)


def test_links_assemble_the_text_in_force(folder: Path, profile: Any) -> None:
    raw, doc = _doc(folder, profile)
    # Followed: the version page, the section in force, and two articles in force.
    # Dropped: the repealed section and the superseded article version. Missing: one article.
    assert raw.extraction.params["links"] == {"followed": 4, "dropped": 2, "missing": 1}
    heads = [(b.attrs.get("unit"), b.text) for b in doc.blocks if b.type.value == "heading"]
    assert heads[:3] == [
        ("text", "Code de commerce"),
        ("section1", "Partie législative"),
        ("article", "L233-3"),
    ]
    text = " ".join(b.text for b in doc.blocks)
    assert "Abrogée" not in text and "Vu la Constitution" not in text


def test_the_assembled_text_is_a_valid_canonical_document(folder: Path, profile: Any) -> None:
    # ! The extraction run validates every document; an article heading at level 14 once
    # failed the render/parse round trip and quarantined all of LEGI.
    _, doc = _doc(folder, profile)
    assert [i for i in validate(doc) if i.severity is Severity.ERROR] == []


def test_split_at_cuts_an_article_into_paragraphs(folder: Path, profile: Any) -> None:
    _, doc = _doc(folder, profile)
    paras = [b.text for b in doc.blocks if b.type.value == "paragraph"]
    assert paras[:3] == [
        "I.- Toute personne est considérée :",
        "1° Lorsqu'elle détient la majorité ;",
        "2° Lorsqu'elle dispose seule de la majorité.",
    ]


def test_chunks_cite_text_and_article(folder: Path, profile: Any) -> None:
    _, doc = _doc(folder, profile)
    e = get("markup")
    b = Builder(
        doc=doc,
        chunker=e.id,
        version=e.version,
        config=ChunkConfig(max_tokens=1024, min_tokens=0),
        profile=profile,
    )
    chunks = {c.identifier: c for c in e.fn(doc, b)}
    c = chunks["Code de commerce, art. L233-3"]
    assert (c.regulation_type, c.regulation_number) == ("CODE", TEXT)
    assert c.effective_date == "2026-10-01"
    assert "Code de commerce, art. L1" in chunks


def test_a_link_outside_the_folder_is_not_followed(tmp_path: Path, profile: Any) -> None:
    root = tmp_path / "t"
    escaped = STRUCT.replace("/LEGI/SCTA/00/00/06/08/32/", "/../../../../")
    _write(root / f"texte/struct/{TEXT}.xml", escaped)
    _write(root / f"texte/version/{TEXT}.xml", VERSION)
    _write(tmp_path / "LEGISCTA000006083281.xml", SECTION)
    _write(
        _article_path(root, "LEGIARTI000006219000"),
        _article("LEGIARTI000006219000", "L1", "<p>Premier article.</p>"),
    )
    raw = XmlExtractor().extract(
        root / f"texte/struct/{TEXT}.xml", title=TEXT, url="u", profile=profile
    )
    # The version page and article L1; the section's file lies outside the text's folder.
    assert raw.extraction.params["links"]["followed"] == 2


def _tar(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _legi_corpus(root: Path, **fetch: Any) -> CorpusSpec:
    base = "https://echanges.example/LEGI/"
    return CorpusSpec.model_validate(
        {
            "id": "fr_legi",
            "title": "t",
            "sources": {"root": str(root), "snapshot": "current.txt"},
            "fetch": {
                "kind": "archive_index",
                "listing": base,
                "files": base,
                "archive": r"^Freemium_legi_global_(?P<edition>\d{8}-\d{6})\.tar\.gz$",
                "paths": ["*/legi/global/code_et_TNC_en_vigueur/*"],
                "documents": ["texte/struct/*.xml"],
                **fetch,
            },
            "provenance": {"base_url": base, "archive_suffix": ".tar.gz"},
        }
    )


def test_archive_index_takes_the_newest_dump_and_cites_members(tmp_path: Path) -> None:
    base = "https://echanges.example/LEGI/"
    text = "20261108-140000/legi/global/code_et_TNC_en_vigueur/code_en_vigueur/LEGI/T"
    dump = _tar(
        {
            f"{text}/texte/struct/LEGITEXT1.xml": b"<TEXTELR/>",
            f"{text}/article/LEGI/ARTI/a.xml": b"<ARTICLE/>",
            "20261108-140000/legi/global/code_et_TNC_non_vigueur/x.xml": b"<old/>",
        }
    )
    index = (
        b'<a href="Freemium_legi_global_20251001-120000.tar.gz">old</a>'
        b'<a href="Freemium_legi_global_20261108-140000.tar.gz">new</a>'
        b'<a href="LEGI_20261109-210000.tar.gz">increment</a>'
    )
    asked: list[str] = []

    def opener(url: str, headers: dict[str, str], timeout: float) -> io.BytesIO:
        asked.append(url)
        return io.BytesIO(dump if url.endswith(".tar.gz") else index)

    corpus = _legi_corpus(tmp_path)
    report = Fetcher(corpus, opener=opener, sleep=lambda _: None).run()
    assert report.editions == {"archive": "20261108-140000"}
    assert asked[-1] == f"{base}Freemium_legi_global_20261108-140000.tar.gz"
    # The members in force, unpacked; the repealed ones are not.
    assert report.unpacked == 2
    struct = f"Freemium_legi_global_20261108-140000/{text}/texte/struct/LEGITEXT1.xml"
    lines = (tmp_path / "current.txt").read_text(encoding="utf-8").splitlines()
    assert [x for x in lines if not x.startswith("#")] == [struct]
    assert (tmp_path / struct).read_bytes() == b"<TEXTELR/>"
    records = {
        json.loads(x)["path"]: json.loads(x)
        for x in (tmp_path / "fetch.jsonl").read_text(encoding="utf-8").splitlines()
    }
    cited = (
        f"{base}Freemium_legi_global_20261108-140000.tar.gz#{text}/texte/struct/LEGITEXT1.xml"
    )
    assert records[struct]["url"] == cited
    # The citation the extractor writes is the manifest's.
    assert corpus.provenance.url_for(struct) == cited
    # A second run unpacks nothing again.
    again = Fetcher(corpus, opener=opener, sleep=lambda _: None).run()
    assert again.present == 1 and again.unpacked == 2


def test_archive_index_refuses_a_member_escaping(tmp_path: Path) -> None:
    dump = _tar({"x/legi/global/code_et_TNC_en_vigueur/../../../../evil.xml": b"<x/>"})
    index = b'<a href="Freemium_legi_global_20261108-140000.tar.gz">new</a>'

    def opener(url: str, headers: dict[str, str], timeout: float) -> io.BytesIO:
        return io.BytesIO(dump if url.endswith(".tar.gz") else index)

    with pytest.raises(ValueError, match="escapes"):
        Fetcher(_legi_corpus(tmp_path), opener=opener, sleep=lambda _: None).run()
    assert not (tmp_path.parent / "evil.xml").exists()


def test_archive_index_needs_a_dated_archive(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="edition"):
        _legi_corpus(tmp_path, archive=r"^Freemium.*\.tar\.gz$")


def test_the_corpus_loads() -> None:
    corpus = CorpusSpec.find("fr_legi")
    assert corpus.extract.profile == "fr_legi@1.0"
    assert corpus.provenance.url_for("a.tar.gz") == f"{corpus.provenance.base_url}a.tar.gz"


def test_archive_index_refuses_a_glob_matching_nothing(tmp_path: Path) -> None:
    dump = _tar({"legi/global/code_et_TNC_en_vigueur/a.xml": b"<x/>"})
    index = b'<a href="Freemium_legi_global_20261108-140000.tar.gz">new</a>'

    def opener(url: str, headers: dict[str, str], timeout: float) -> io.BytesIO:
        return io.BytesIO(dump if url.endswith(".tar.gz") else index)

    # The corpus's glob expects an edition folder first; the full dump has none.
    with pytest.raises(ValueError, match="no member matches"):
        Fetcher(_legi_corpus(tmp_path), opener=opener, sleep=lambda _: None).run()
    assert not (tmp_path / "current.txt").exists()
