"""Every corpus is chunked to one standard (`docs/pipeline/STANDARD.md` §2).

! Until 2026-11-28 each corpus carried its own budget: Indonesia's profile said 4,096
while its corpus file said 512 and won, the XML law profiles said 1,024, and all of them
counted a word estimate. This test is what keeps that from coming back: every profile
states the standard, and every corpus file resolves to it, in the embedder's own tokens.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chunk import Chunking
from chunk.tokens import ESTIMATE
from spec import CorpusSpec
from spec.loader import Registry

MAX_TOKENS = 4096
MIN_TOKENS = 8
OVERLAP_TOKENS = 0
EMBED_MAX_LENGTH = 8192

ROOT = Path(__file__).resolve().parents[1]
CORPORA = sorted(p.stem for p in (ROOT / "corpora").glob("*.yaml"))
REGISTRY = Registry.load()


def test_there_is_one_version_of_every_profile() -> None:
    refs = [p.ref for p in REGISTRY]
    assert refs and all(r.endswith("@1.0") for r in refs), refs


@pytest.mark.parametrize("ref", sorted(p.ref for p in REGISTRY))
def test_every_profile_states_the_standard(ref: str) -> None:
    chunk = REGISTRY.get(ref).spec.pipeline.chunk
    assert (chunk.max_tokens, chunk.min_tokens) == (MAX_TOKENS, MIN_TOKENS), ref
    assert chunk.overlap_tokens in (None, OVERLAP_TOKENS), ref


@pytest.mark.parametrize("corpus_id", CORPORA)
def test_every_corpus_chunks_to_the_standard(corpus_id: str, tmp_path: Path) -> None:
    spec = CorpusSpec.find(corpus_id)
    run = Chunking(spec, workspace=tmp_path)
    configs = [run.config]
    if run._routed:
        configs += [run.corpus.chunk.to_config(p) for p in REGISTRY]
    for config in configs:
        assert (config.max_tokens, config.min_tokens, config.overlap_tokens) == (
            MAX_TOKENS,
            MIN_TOKENS,
            OVERLAP_TOKENS,
        ), corpus_id
    assert run.config.tokenizer != ESTIMATE, f"{corpus_id} counts by estimate"


@pytest.mark.parametrize("corpus_id", CORPORA)
def test_every_corpus_embeds_at_the_standard_length(corpus_id: str) -> None:
    embed = CorpusSpec.find(corpus_id).embed
    assert embed is not None and embed.max_length == EMBED_MAX_LENGTH, corpus_id
