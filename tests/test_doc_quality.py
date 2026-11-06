"""The document quality gate (`docs/QUALITY.md`).

Hermetic: the word list is written to a temp registry, not read from the real one, so a
rebuilt lexicon cannot change what these tests mean.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from enrich.quality import Lexicon, QualityGate
from spec import Profile, ProfileSpec

CLEAN = (
    "Pemerintah daerah menetapkan peraturan tentang pengelolaan keuangan daerah dan "
    "pelaksanaan anggaran pendapatan dan belanja daerah untuk setiap tahun anggaran"
)
GARBLED = (
    "Pcmerintah dnerah mcnetapkan pcraturan tcntang pcngelolaan kcuangan dacrah dnn "
    "pclaksanaan anggnran pcndapatan dnn bclanja dacrah untnk setinp tahnn anggnran"
)
WORDS = sorted(set((CLEAN + " " + "pemerintah daerah").lower().split()))


def gate(tmp_path: Path, **quality: object) -> QualityGate:
    (tmp_path / "lexicons").mkdir(exist_ok=True)
    (tmp_path / "lexicons" / "t.txt").write_text(
        "# test list\n" + "\n".join(WORDS) + "\n", encoding="utf-8"
    )
    spec = ProfileSpec.model_validate(
        {
            "id": "t",
            "version": "1.0",
            "title": "t",
            "quality": {"lexicon": "lexicons/t.txt", "min_chunk_words": 5, **quality},
        }
    )
    return QualityGate(Profile.compile(spec), tmp_path)


def test_clean_text_is_clean(tmp_path: Path) -> None:
    q = gate(tmp_path).score("d", [CLEAN] * 4)
    assert q.verdict == "clean" and q.indexable
    assert q.unknown_share == 0 and q.damaged_chunks == 0


def test_a_garbled_document_is_unusable_and_not_indexable(tmp_path: Path) -> None:
    q = gate(tmp_path).score("d", [GARBLED] * 4)
    assert q.verdict == "unusable" and not q.indexable
    assert q.damaged_chunks == 4 and q.damaged_share == 1.0
    assert "damaged chunks" in q.reasons[0]


def test_damage_is_judged_by_how_much_of_the_document_it_covers(tmp_path: Path) -> None:
    """! One bad page must not discard a long regulation, and half noise must not pass."""
    g = gate(tmp_path)
    one_page = g.score("d", [CLEAN] * 9 + [GARBLED])
    assert one_page.verdict == "degraded" and one_page.indexable
    assert one_page.damaged_chunks == 1

    half = g.score("d", [CLEAN] * 5 + [GARBLED] * 5)
    assert half.verdict == "unusable"


def test_a_document_with_no_text_is_absent(tmp_path: Path) -> None:
    g = gate(tmp_path)
    assert g.score("d", []).verdict == "absent"
    assert g.score("d", ["1 2 3 | 4"]).verdict == "absent"
    assert not g.score("d", []).indexable


def test_a_short_real_decree_is_not_absent(tmp_path: Path) -> None:
    """Perpres 12/1965 is twenty words and a real document."""
    q = gate(tmp_path).score("d", [CLEAN])
    assert q.words >= 12 and q.verdict == "clean"


def test_replacement_characters_damage_a_chunk(tmp_path: Path) -> None:
    g = gate(tmp_path)
    assert g.chunk_damaged(CLEAN + " " + "�" * 40)
    assert not g.chunk_damaged(CLEAN + " �")


def test_table_rows_and_acronyms_are_not_judged(tmp_path: Path) -> None:
    """! A table of place names is made of words no list holds; it is not noise."""
    g = gate(tmp_path)
    table = "\n".join(
        f"| Desa Sukamaju{i} | Kecamatan Tanjungsari{i} | 120 |" for i in range(30)
    )
    assert not g.chunk_damaged(CLEAN + "\n" + table)
    assert not g.chunk_damaged(CLEAN + " DPN KSPN NIP BUMD")


def test_without_a_lexicon_only_what_needs_none_is_judged() -> None:
    spec = ProfileSpec.model_validate({"id": "t", "version": "1.0", "title": "t"})
    g = QualityGate(Profile.compile(spec))
    assert g.score("d", [GARBLED] * 4).verdict == "clean"
    assert g.score("d", []).verdict == "absent"


def test_not_indexable_is_the_profiles_decision(tmp_path: Path) -> None:
    q = gate(tmp_path, not_indexable=["degraded", "unusable", "absent"]).score(
        "d", [CLEAN] * 9 + [GARBLED]
    )
    assert q.verdict == "degraded" and not q.indexable


def test_the_hash_follows_the_thresholds_and_the_word_list(tmp_path: Path) -> None:
    a = gate(tmp_path).hash
    assert gate(tmp_path).hash == a
    assert gate(tmp_path, chunk_unknown=0.5).hash != a
    (tmp_path / "lexicons" / "t.txt").write_text("extra\n", encoding="utf-8")
    other = QualityGate(
        Profile.compile(
            ProfileSpec.model_validate(
                {
                    "id": "t",
                    "version": "1.0",
                    "title": "t",
                    "quality": {"lexicon": "lexicons/t.txt", "min_chunk_words": 5},
                }
            )
        ),
        tmp_path,
    )
    assert other.hash != a


def test_a_missing_lexicon_is_an_error_not_a_silent_pass(tmp_path: Path) -> None:
    spec = ProfileSpec.model_validate(
        {"id": "t", "version": "1.0", "title": "t", "quality": {"lexicon": "lexicons/none.txt"}}
    )
    with pytest.raises(FileNotFoundError):
        QualityGate(Profile.compile(spec), tmp_path)


def test_a_lexicon_cannot_point_outside_the_registry() -> None:
    with pytest.raises(ValueError, match="inside the registry"):
        ProfileSpec.model_validate(
            {"id": "t", "version": "1.0", "title": "t", "quality": {"lexicon": "../x.txt"}}
        )


def test_quality_does_not_move_the_chunk_hash() -> None:
    """Judging finished chunks must not invalidate a million of them."""
    base = {"id": "t", "version": "1.0", "title": "t"}
    a = ProfileSpec.model_validate(base)
    b = ProfileSpec.model_validate({**base, "quality": {"chunk_unknown": 0.9}})
    assert a.config_hash == b.config_hash
    assert a.structure_hash == b.structure_hash


def test_the_shipped_lexicon_loads_and_holds_the_words_of_the_domain() -> None:
    root = Path(__file__).resolve().parents[1] / "registry" / "lexicons"
    lex = Lexicon.load(root / "id_regulation_md@1.txt")
    assert len(lex) > 5000 and lex.sha256
    assert {"pemerintah", "peraturan", "pasal", "daerah"} <= lex.words
    assert not {"scbagairnana", "doeroh", "kobupaten"} & lex.words
