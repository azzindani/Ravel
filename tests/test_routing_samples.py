"""Routing against real documents of the wrong kind.

`test_profiles.py` proves the router picks `id_regulation` for text that looks like an
Indonesian regulation. That is the easy half. This is the other half: **158 real PDFs,
144 of which are not regulations at all** — contracts, resumes, invoices, US federal
documents, Indonesian exchange filings — and none of them may be claimed by
`id_regulation`.

Why this matters more than it looks
-----------------------------------
`CLAUDE.md` §12 puts corpus knowledge in profiles so it can be changed without touching
code. The cost of that freedom is that a profile can be widened by anyone, and a widened
`match.content` pattern has no local symptom — it produces a *plausible* parse of a
document it should never have seen, and `identity.title` then trusts what it extracted.
A résumé routed to `id_regulation` does not crash; it produces a chunk claiming to be
`Pasal 3` of something. §12 of `ABSORPTION.md` records the near-miss: a 90-entry type
vocabulary was almost imported wholesale, and every entry would have widened a substring
match.

These are marked `samples` and skipped unless the corpus and PyMuPDF are both present,
so the default suite stays hermetic and fast (`pytest -m samples` to run them).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from spec import Registry

pytestmark = pytest.mark.samples

SAMPLES = Path(__file__).resolve().parents[1] / "samples"

# Families by filename prefix. Only the last is Indonesian regulation.
FOREIGN = ("contract_", "resume_", "invoice_", "us_gov_", "IDX_")
INDONESIAN = ("UU ", "PP ", "Peraturan ")

pytest.importorskip("pymupdf", reason="samples routing needs the `extract` extra")
if not SAMPLES.is_dir() or not any(SAMPLES.glob("*.pdf")):
    pytest.skip("samples/ corpus not present", allow_module_level=True)


def _first_page_text(path: Path, limit: int = 4000) -> str:
    """What the router actually sees: a sample off the front, not a full extraction."""
    import pymupdf

    with pymupdf.open(path) as doc:
        if doc.page_count == 0:
            return ""
        return doc[0].get_text()[:limit]


def _by_family(prefixes: tuple[str, ...]) -> list[Path]:
    return sorted(p for p in SAMPLES.glob("*.pdf") if p.name.startswith(prefixes))


@pytest.fixture(scope="module")
def registry() -> Registry:
    return Registry.load()


# -- the guard ---------------------------------------------------------------------


@pytest.mark.parametrize("path", _by_family(FOREIGN), ids=lambda p: p.stem[:40])
def test_foreign_documents_never_route_to_id_regulation(
    registry: Registry, path: Path
) -> None:
    """A contract, resume, invoice or US federal document is not an Indonesian
    regulation. `generic` may claim it, or nothing may — but never `id_regulation`,
    because a false positive there is a chunk that cites a pasal that does not exist."""
    sample = _first_page_text(path)
    if not sample.strip():
        pytest.skip("no text layer; routing on an empty sample proves nothing")

    chosen = registry.route(mime="application/pdf", sample=sample)

    assert chosen is None or chosen.id != "id_regulation", (
        f"{path.name} was claimed by id_regulation on this text:\n"
        f"{sample[:300]!r}"
    )


@pytest.mark.parametrize("path", _by_family(INDONESIAN), ids=lambda p: p.stem[:40])
def test_indonesian_regulations_do_route_to_id_regulation(
    registry: Registry, path: Path
) -> None:
    """The other half: tightening the profile until nothing false-positives is trivial
    if it also stops matching the corpus. This is the test that makes that visible."""
    sample = _first_page_text(path)
    if not sample.strip():
        pytest.skip("no text layer; routing on an empty sample proves nothing")

    chosen = registry.route(mime="application/pdf", sample=sample)

    assert chosen is not None and chosen.id == "id_regulation", (
        f"{path.name} routed to {chosen.ref if chosen else None}, not id_regulation"
    )


def test_the_corpus_actually_covers_both_sides() -> None:
    """! Guards the guard. Both tests above parametrize over a glob, and a glob that
    matches nothing makes a test suite that passes while testing nothing."""
    assert len(_by_family(FOREIGN)) >= 100
    assert len(_by_family(INDONESIAN)) >= 10
