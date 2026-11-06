"""The local embedder against a real model. Skipped unless one is named.

    RAVEL_TEST_MODEL=/path/to/Qwen3-Embedding-0.6B uv run pytest tests/test_embed_local_real.py

Needs torch and transformers (`uv sync --extra embed`) and a checkpoint; CI has neither, so
these never run there. They exist because the hash embedder cannot catch the failures that
matter on a GPU: pooling the wrong end of a padded batch, and a model that overflows in half
precision.
"""

from __future__ import annotations

import dataclasses
import os

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("transformers")

MODEL = os.environ.get("RAVEL_TEST_MODEL")
pytestmark = pytest.mark.skipif(not MODEL, reason="RAVEL_TEST_MODEL names no checkpoint")

from embed import EmbedderSpec, spec_for  # noqa: E402
from embed.local import TransformersEmbedder  # noqa: E402
from embed.spec import InstructionStyle, PaddingSide, Pooling  # noqa: E402

TEXTS = [
    "Pasal 1. Wajib Pajak wajib menyelenggarakan pembukuan.",
    "Setiap orang dilarang membuang sampah sembarangan di sungai, selokan, dan jalan umum "
    "serta tempat lain yang tidak ditentukan oleh pemerintah daerah.",
    "Ya.",
]


def spec(**over: object) -> EmbedderSpec:
    assert MODEL is not None
    base = spec_for(
        dim=1024,
        id="real",
        model=MODEL,
        model_version="local",
        pooling=Pooling.LAST_TOKEN,
        padding_side=PaddingSide.LEFT,
        instruction_style=InstructionStyle.PREFIX,
        query_instruction="Given a legal or regulatory question, retrieve passages",
        dtype="float16",
        max_length=512,
    )
    return dataclasses.replace(base, **over)


@pytest.fixture(scope="module")
def embedder() -> TransformersEmbedder:
    return TransformersEmbedder(spec())


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def test_vectors_have_the_declared_width_and_unit_norm(embedder: TransformersEmbedder) -> None:
    vectors = np.asarray(embedder.embed_documents(TEXTS))
    assert vectors.shape == (3, 1024)
    assert np.isfinite(vectors).all()
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-2)


def test_a_text_embeds_the_same_alone_and_inside_a_padded_batch(
    embedder: TransformersEmbedder,
) -> None:
    """! The padding-side bug in one assertion. A short text next to a long one is padded;
    if the wrong end is pooled it gets a PAD embedding and stops resembling itself."""
    together = np.asarray(embedder.embed_documents(TEXTS))
    for i, text in enumerate(TEXTS):
        alone = np.asarray(embedder.embed_documents([text]))[0]
        assert cosine(alone, together[i]) > 0.99, f"text {i} changed when batched"


def test_pooling_the_wrong_end_visibly_breaks_a_batch(embedder: TransformersEmbedder) -> None:
    """The negative control: pad on the right but pool as if padded on the left, and the
    short text gets a PAD position. If this did not change the vector the test above would
    prove nothing."""
    good = np.asarray(embedder.embed_documents(TEXTS))
    embedder.tokenizer.padding_side = "right"
    try:
        bad = np.asarray(embedder.embed_documents(TEXTS))
    finally:
        embedder.tokenizer.padding_side = "left"
    assert cosine(good[2], bad[2]) < 0.9, "the short text did not notice its padding"


def test_a_question_is_closer_to_its_answer_than_to_an_unrelated_passage(
    embedder: TransformersEmbedder,
) -> None:
    question = np.asarray(embedder.embed_queries(["Siapa yang wajib membuat pembukuan?"]))[0]
    docs = np.asarray(embedder.embed_documents(TEXTS))
    assert cosine(question, docs[0]) > cosine(question, docs[1])


def test_a_width_the_model_does_not_have_is_refused() -> None:
    with pytest.raises(ValueError, match="dimensions"):
        TransformersEmbedder(spec(dim=2048))
