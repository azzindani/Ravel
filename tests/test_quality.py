"""The text-layer quality gate (`ABSORPTION.md` §16).

The cases below are not invented. Each damaged string is a real one, taken from the
extraction outputs or source PDFs the measurement was run over, so a future edit that
loosens a threshold has to argue with a document rather than with a number.
"""

from __future__ import annotations

import pytest

from extract.quality import (
    TextLayer,
    Thresholds,
    longest_unbroken_run,
    normalise_damage,
    score_text,
)

CLEAN = (
    "PERATURAN BUPATI NOMOR 54 TAHUN 2022 TENTANG PENJABARAN ANGGARAN PENDAPATAN "
    "DAN BELANJA DAERAH TAHUN ANGGARAN 2023 DENGAN RAHMAT TUHAN YANG MAHA ESA. "
    "Pasal 1 Dalam Peraturan Bupati ini yang dimaksud dengan Daerah adalah Kabupaten."
)


def test_clean_indonesian_regulation_text_is_clean() -> None:
    q = score_text(CLEAN, pages=1)

    assert q.layer is TextLayer.CLEAN
    assert q.usable
    assert q.reasons == ()


# -- the damaged class: present, extracts without error, wrong ----------------------


def test_space_loss_is_damage() -> None:
    """! The real one. Native text read `PENJABARAN ANGGARAN PENDAPATAN DAN BELANJA
    DAERAH TAHUN ANGGARAN 2023`; what shipped is below. Every space gone, `r`->`t` in
    *Anggaran*, letter O for digit 0 (`ABSORPTION.md` §15)."""
    shipped = "PenjabaranAnggaranPendapatandanBelanjaDaerahTahunAnggatan2O23 " * 6
    q = score_text(shipped, pages=1)

    assert q.layer is TextLayer.DAMAGED
    assert not q.usable
    assert any("space loss" in r for r in q.reasons)


def test_control_characters_are_damage() -> None:
    """! Also real, and from a *native* text layer rather than an OCR output:
    `22._PERBUP_NO_22__TAHUN_2022` reads `NOMOR \\x01J.. TAHUN 2022` straight out of the
    PDF. The regulation number — the one field the exact-citation path trusts."""
    q = score_text(CLEAN + " PERATURAN BUPATI KEPULAUAN SULA NOMOR \x01J.. TAHUN 2022", pages=1)

    assert q.layer is TextLayer.DAMAGED
    assert q.control_chars == 1


def test_replacement_characters_are_damage() -> None:
    q = score_text(CLEAN + " BUPATI KEPULAUAN SULA, FIFIAN �GSIMUS", pages=1)

    assert q.layer is TextLayer.DAMAGED
    assert q.replacement_chars == 1


def test_damage_is_reported_with_its_evidence() -> None:
    """The verdict travels with the reason, so a routing decision can be audited later
    rather than re-derived (`ABSORPTION.md` §18.3)."""
    q = score_text(CLEAN + "\x01 " + "ANGGARANPENDAPATANDANBELANJADAERAHTAHUN", pages=1)

    assert len(q.reasons) == 2
    assert "damaged" in q.describe()


# -- the absent class ---------------------------------------------------------------


def test_no_text_layer_is_absent_not_damaged() -> None:
    """A scan is not a damaged document, and the distinction is not pedantic: both go to
    OCR, but only one of them means the PDF lied about having text."""
    q = score_text("  \n  ", pages=4)

    assert q.layer is TextLayer.ABSENT
    assert not q.usable


def test_emptiness_is_judged_per_page() -> None:
    """! 800 characters is a healthy one-pager and an empty 40-page scan with stray
    marks. Judging the total rather than the rate is how the second one gets extracted
    as if it were the first."""
    text = "Pasal 1 Dalam Peraturan ini yang dimaksud dengan Daerah adalah Kabupaten. " * 11

    assert score_text(text, pages=1).layer is TextLayer.CLEAN
    assert score_text(text, pages=40).layer is TextLayer.ABSENT


# -- the signal itself --------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Nomor 21/PMK.03/2022 tentang Pajak", 7),  # identifiers are not words
        ("Rp1.500.000,00 dibayarkan", 10),  # "dibayarkan"; the amount is not a word
        ("", 0),
        ("penyelenggaraan", 15),  # a long, real Indonesian word
    ],
)
def test_run_length_counts_letters_only(text: str, expected: int) -> None:
    """! Regulations are full of `21/PMK.03/2022` and `Rp1.500.000,00`. Counting
    non-space characters instead of letters flags those as space loss, which would
    route most of the corpus to OCR."""
    assert longest_unbroken_run(text) == expected


def test_real_indonesian_words_stay_under_the_threshold() -> None:
    """The threshold has to clear the language's genuinely long words, or the gate
    rejects correct extractions. `mempertanggungjawabkan` is 22 letters and legal
    Indonesian uses it constantly."""
    text = CLEAN + " mempertanggungjawabkan pertanggungjawaban penyelenggaraannya"

    assert longest_unbroken_run(text) < Thresholds().max_unbroken_run
    assert score_text(text, pages=1).layer is TextLayer.CLEAN


def test_thresholds_are_configurable_not_baked_in() -> None:
    """`CLAUDE.md` §8 — corpus constants live in config."""
    text = score_text("A" * 30 + " " + CLEAN, pages=1)
    assert text.layer is TextLayer.DAMAGED

    relaxed = score_text(
        "A" * 30 + " " + CLEAN, pages=1, thresholds=Thresholds(max_unbroken_run=40)
    )
    assert relaxed.layer is TextLayer.CLEAN


# -- normalisation is not repair ----------------------------------------------------


def test_normalise_strips_control_characters_only() -> None:
    assert normalise_damage("NOMOR \x01J.. TAHUN 2022") == "NOMOR J.. TAHUN 2022"


def test_normalise_does_not_repair_damage() -> None:
    """! `CLAUDE.md` §7.1. A downstream stage that papers over an extraction defect
    produces a plausible document that no longer matches its source. Re-extraction is
    the fix; this only stops damage breaking a parser on the way there."""
    broken = "PenjabaranAnggaranPendapatan"

    assert normalise_damage(broken) == broken
