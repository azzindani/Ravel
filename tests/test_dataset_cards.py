"""Every dataset card's front matter is valid YAML naming its data files.

! The hub refuses a card whose front matter does not parse, after the export has run:
ID_REG_RAG_2611's first upload of the shared layout failed on one mis-indented line.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

CARDS = sorted(
    p for p in (Path(__file__).parents[1] / "datasets").glob("*.md") if p.name != "README.md"
)


@pytest.mark.parametrize("card", CARDS, ids=[p.stem for p in CARDS])
def test_a_card_front_matter_parses_and_names_the_data(card: Path) -> None:
    text = card.read_text("utf-8").replace("\r\n", "\n")
    assert text.startswith("---\n"), "front matter first"
    meta = yaml.safe_load(text.split("---\n", 2)[1])
    files = [f for c in meta["configs"] for f in c["data_files"]]
    assert files and all(f["path"].startswith("data/") for f in files), files
