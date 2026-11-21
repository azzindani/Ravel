"""France's LEGI: the `legi` dialect's links (a text assembled from its folder of files),
`text.split_at`, and the `fr_legi` profile (`docs/SOURCE_FR_LEGI.md`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chunk import Builder, ChunkConfig, get
from extract.structure import Structurer
from extract.xml import XmlExtractor
from spec import default_registry

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
