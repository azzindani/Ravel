"""One vector space, one file (`registry/embedders/`): a corpus names it with `embed.use`,
and its hashes are those of the resolved fields."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from embed.factory import spec_of
from spec.corpus import EMBEDDERS, CorpusSpec, resolve_embedder

ROOT = Path(__file__).resolve().parents[1]
REF = "qwen3-embedding-0.6b@1.0"


def test_a_reference_resolves_to_the_registry_fields() -> None:
    resolved = resolve_embedder({"use": REF}, where="t")
    assert resolved == yaml.safe_load((EMBEDDERS / f"{REF}.yaml").read_text("utf-8"))


def test_how_the_space_is_run_may_be_overridden_and_what_it_is_may_not() -> None:
    assert resolve_embedder({"use": REF, "shard_rows": 5}, where="t")["shard_rows"] == 5
    with pytest.raises(ValueError, match="new registry version"):
        resolve_embedder({"use": REF, "pooling": "mean"}, where="t")
    with pytest.raises(ValueError, match="not in"):
        resolve_embedder({"use": "qwen3-embedding-0.6b@9.9"}, where="t")


def test_the_regulation_corpora_share_one_space() -> None:
    names = ["ca_laws", "us_cfr", "jp_egov", "fr_legi", "de_gii", "id_reg_markdown"]
    prints = {n: spec_of(CorpusSpec.find(n).embed).fingerprint() for n in names}  # type: ignore[arg-type]
    assert set(prints.values()) == {"2231c38c768a08c7"}, prints


def test_no_corpus_copies_a_registry_space() -> None:
    # ! The overlap this registry removed: a copied block drifts, and its corpus lands in
    # another space without a sign. Another space (BEIR's per-task instructions) is not a
    # copy: only a block with a registry space's fingerprint is.
    from spec.corpus import EmbedSpec

    def fingerprint(fields: dict[str, object]) -> str:
        return spec_of(EmbedSpec.model_validate(fields)).fingerprint()

    spaces = {
        fingerprint(yaml.safe_load(p.read_text("utf-8"))) for p in EMBEDDERS.glob("*.yaml")
    }
    copied = []
    for path in sorted((ROOT / "corpora").glob("*.yaml")):
        block = (yaml.safe_load(path.read_text("utf-8")) or {}).get("embed")
        if block and "use" not in block and fingerprint(block) in spaces:
            copied.append(path.name)
    assert not copied, f"name the registry embedder with `embed.use` instead: {copied}"
