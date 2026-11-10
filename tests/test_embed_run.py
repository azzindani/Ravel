"""The embedding stage, end to end on a synthetic corpus with the deterministic embedder.

No GPU, no download, no network. What these assert is the stage's behaviour: that vectors
line up with their chunks, that a rerun does nothing, that a different embedder cannot
reuse another's vectors, and that a window which cannot be right is refused before it is
written. Whether a real model is *good* is the preflight's and the eval's question.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq
import pytest

from bundle.writer import iter_vectors
from chunk import Chunking, iter_chunk_shards
from chunk.derive import embed_text
from embed import (
    Embedding,
    HashEmbedder,
    build_embedder,
    check_vectors,
    render_document,
    spec_for,
)
from embed.local import last_token_index
from embed.run import window_key
from embed.spec import InstructionStyle, PaddingSide
from extract import Extraction
from runtime import Ledger
from spec import CorpusSpec, default_registry
from spec.corpus import EmbedSpec

DOC = """PERATURAN BUPATI BOYOLALI NOMOR {n} TAHUN 2011 TENTANG PAJAK DAERAH

## Menimbang

bahwa untuk melaksanakan ketentuan tersebut perlu menetapkan peraturan.

## BAB II

Pasal 1

(1) Wajib Pajak wajib menyelenggarakan pembukuan sesuai ketentuan yang berlaku.

(2) Pembukuan tersebut disimpan selama sepuluh tahun di tempat kedudukannya.

Pasal 2

Ketentuan lebih lanjut mengenai tata cara diatur dengan Peraturan Bupati tersendiri.
"""

DIM = 16


def embed_config(**overrides: Any) -> dict[str, Any]:
    return {
        "kind": "hash",
        "id": "hash-test",
        "model": "ravel/hash-embedder",
        "revision": "1.0.0",
        "dim": DIM,
        "shard_rows": 5,
        "batch_items": 3,
        **overrides,
    }


@pytest.fixture
def corpus(tmp_path: Path) -> CorpusSpec:
    sources = tmp_path / "src"
    sources.mkdir()
    for n in range(1, 5):
        (sources / f"perbup_{n}.md").write_text(DOC.format(n=n), encoding="utf-8")
    return CorpusSpec.model_validate(
        {
            "id": "fixture",
            "title": "Fixture corpus",
            "sources": {"root": str(sources), "include": ["**/*.md"]},
            "provenance": {"base_url": "hf://datasets/example/fixture/"},
            "extract": {"profile": "id_regulation", "extractors": ["text"]},
            "chunk": {"chunker": "unit", "max_tokens": 256, "min_tokens": 8},
            "embed": embed_config(),
        }
    )


@pytest.fixture
def workspace(corpus: CorpusSpec, tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    Extraction(corpus, workspace=ws).run()
    Chunking(corpus, workspace=ws).run()
    return ws


def stage(corpus: CorpusSpec, workspace: Path, **config: Any) -> Embedding:
    cfg = EmbedSpec.model_validate(embed_config(**config))
    return Embedding(corpus, workspace=workspace, embedder=build_embedder(cfg), config=cfg)


def vectors_of(run: Embedding) -> dict[str, np.ndarray[Any, Any]]:
    out: dict[str, np.ndarray[Any, Any]] = {}
    for shard in sorted(run.vector_dir.glob("part-*.parquet")):
        for chunk_id, vector in iter_vectors(shard):
            assert chunk_id not in out, f"{chunk_id} appears in two shards"
            out[chunk_id] = np.asarray(vector, dtype=np.float32)
    return out


# ===========================================================================
def test_every_chunk_gets_exactly_one_vector_of_the_declared_width(
    corpus: CorpusSpec, workspace: Path
) -> None:
    run = stage(corpus, workspace)
    report = run.run()

    chunks = list(iter_chunk_shards(workspace, "fixture"))
    vectors = vectors_of(run)

    assert report.vectors == len(chunks) > 5
    assert set(vectors) == {c.id for c in chunks}
    assert len(vectors) == len(chunks), "a chunk embedded twice is a chunk in the corpus twice"
    assert all(v.shape == (DIM,) for v in vectors.values())


def test_a_vector_belongs_to_the_chunk_it_is_filed_under(
    corpus: CorpusSpec, workspace: Path
) -> None:
    """! The stage sorts by length and batches, then puts results back. A vector attached
    to the wrong chunk is a silent mis-join, so recompute each one independently."""
    run = stage(corpus, workspace, batch_items=2)
    run.run()
    spec = run.embedder.spec
    reference = HashEmbedder(spec)
    vectors = vectors_of(run)

    for chunk in iter_chunk_shards(workspace, "fixture"):
        expected = np.asarray(reference.embed_documents([embed_text(chunk, run.profile)])[0])
        assert np.allclose(vectors[chunk.id], expected, atol=1e-2), chunk.id


def test_what_is_embedded_is_the_template_and_the_body_is_not_touched(
    corpus: CorpusSpec, workspace: Path
) -> None:
    run = stage(corpus, workspace)
    chunk = next(iter_chunk_shards(workspace, "fixture"))
    shown = embed_text(chunk, run.profile)

    assert chunk.body in shown
    assert chunk.body == next(iter_chunk_shards(workspace, "fixture")).body


def test_a_second_run_does_nothing(corpus: CorpusSpec, workspace: Path) -> None:
    first = stage(corpus, workspace).run()
    second = stage(corpus, workspace).run()

    assert first.vectors > 0
    assert second.vectors == 0
    assert second.skipped_done == first.windows
    assert second.shards == 0


def test_a_killed_run_resumes_from_the_last_sealed_shard(
    corpus: CorpusSpec, workspace: Path
) -> None:
    run = stage(corpus, workspace)
    partial = run.run(limit=5)
    assert 0 < partial.vectors < len(list(iter_chunk_shards(workspace, "fixture")))

    rest = stage(corpus, workspace).run()
    assert rest.skipped_done == partial.shards
    total = len(list(iter_chunk_shards(workspace, "fixture")))
    assert partial.vectors + rest.vectors == total
    assert len(vectors_of(run)) == total


def test_force_recomputes_without_duplicating(corpus: CorpusSpec, workspace: Path) -> None:
    run = stage(corpus, workspace)
    run.run()
    again = stage(corpus, workspace).run(force=True)

    assert again.vectors > 0
    assert len(vectors_of(run)) == len(list(iter_chunk_shards(workspace, "fixture")))


def test_a_different_embedder_cannot_reuse_anothers_vectors(
    corpus: CorpusSpec, workspace: Path
) -> None:
    """! `CLAUDE.md` §7.3 and §7.7. Padding side alone changes a vector, so it changes the
    directory and every key."""
    a = stage(corpus, workspace)
    b = stage(corpus, workspace, padding_side="right")
    a.run()
    second = b.run()

    assert a.vector_dir != b.vector_dir
    assert second.skipped_done == 0 and second.vectors > 0


def test_the_template_is_part_of_the_key(corpus: CorpusSpec, workspace: Path) -> None:
    run = stage(corpus, workspace)
    key = window_key(
        shard="part-00000.parquet",
        start=0,
        end=5,
        fingerprint=run.embedder.spec.fingerprint(),
        template=run.template_hash,
        chunk_generation=run.chunk_dir.name,
    )
    other = window_key(
        shard="part-00000.parquet",
        start=0,
        end=5,
        fingerprint=run.embedder.spec.fingerprint(),
        template="another",
        chunk_generation=run.chunk_dir.name,
    )
    assert key != other


def test_the_ledger_is_written_after_the_shard_exists(
    corpus: CorpusSpec, workspace: Path
) -> None:
    run = stage(corpus, workspace)
    run.run()
    with Ledger(run.ledger_path) as ledger:
        done = ledger.done_keys(run._stage())
    shards = sorted(run.vector_dir.glob("part-*.parquet"))
    assert len(done) == len(shards) > 0
    assert all(pq.read_table(p).num_rows > 0 for p in shards)


def test_a_manifest_is_written_beside_the_vectors(corpus: CorpusSpec, workspace: Path) -> None:
    run = stage(corpus, workspace)
    run.run()
    manifest = (run.vector_dir / "embed.json").read_text(encoding="utf-8")
    assert run.embedder.spec.fingerprint() in manifest
    assert run.template_hash in manifest


def test_a_width_the_corpus_did_not_declare_is_refused_before_any_work(
    corpus: CorpusSpec, workspace: Path
) -> None:
    cfg = EmbedSpec.model_validate(embed_config(dim=DIM))
    wrong = HashEmbedder(spec_for(dim=DIM * 2, id="hash-test"))
    with pytest.raises(ValueError, match="dimensions"):
        Embedding(corpus, workspace=workspace, embedder=wrong, config=cfg)


def test_a_stage_without_chunks_says_to_chunk_first(corpus: CorpusSpec, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="ravel chunk"):
        stage(corpus, tmp_path / "empty")


def test_a_corpus_without_an_embed_section_is_an_error(
    corpus: CorpusSpec, workspace: Path
) -> None:
    bare = corpus.model_copy(update={"embed": None})
    with pytest.raises(ValueError, match="embed"):
        Embedding(bare, workspace=workspace, embedder=HashEmbedder(spec_for(dim=DIM)))


# -- the refusals ---------------------------------------------------------------
def test_check_vectors_refuses_nan_the_wrong_width_and_the_wrong_norm() -> None:
    good = np.eye(3, DIM, dtype=np.float32)
    check_vectors(good, dim=DIM, rows=3, normalized=True)

    with pytest.raises(ValueError, match="expected 3 vectors"):
        check_vectors(good, dim=DIM + 1, rows=3, normalized=True)
    bad = good.copy()
    bad[1, 0] = np.nan
    with pytest.raises(ValueError, match="NaN"):
        check_vectors(bad, dim=DIM, rows=3, normalized=True)
    with pytest.raises(ValueError, match="unit length"):
        check_vectors(good * 2, dim=DIM, rows=3, normalized=True)
    check_vectors(good * 2, dim=DIM, rows=3, normalized=False)  # the manifest said so


class Broken(HashEmbedder):
    """Returns NaN for one text: a model that overflows on one pathological chunk."""

    def embed_documents(self, batch: list[str]) -> Any:
        out = np.asarray(super().embed_documents(batch), dtype=np.float32)
        out[0, 0] = np.nan
        return out


def test_a_window_that_cannot_be_right_is_never_written(
    corpus: CorpusSpec, workspace: Path
) -> None:
    cfg = EmbedSpec.model_validate(embed_config())
    run = Embedding(
        corpus,
        workspace=workspace,
        embedder=Broken(spec_for(dim=DIM, id="hash-test")),
        config=cfg,
    )
    with pytest.raises(ValueError, match="NaN"):
        run.run()
    assert not list(run.vector_dir.glob("part-*.parquet"))
    with Ledger(run.ledger_path) as ledger:
        assert not ledger.done_keys(run._stage())


# -- instruction rendering, the bug this stage's model depends on ----------------------------
def test_a_document_is_embedded_bare_when_only_the_query_carries_an_instruction() -> None:
    """! Qwen3 text: the instruction is on the query, never on the document. A document
    rendered as an empty `Instruct:` template sits in a space no query is asked from."""
    spec = spec_for(
        instruction_style=InstructionStyle.PREFIX,
        doc_instruction="",
        query_instruction="Find law",
    )
    assert render_document(spec, "isi pasal") == "isi pasal"


# -- last-token pooling, which padding side decides -------------------------------------------
def test_the_last_real_token_depends_on_the_padding_side() -> None:
    mask = np.array([[0, 0, 1, 1, 1], [1, 1, 1, 1, 1], [0, 1, 1, 1, 1]])
    assert list(last_token_index(mask, PaddingSide.LEFT)) == [4, 4, 4]

    right = np.array([[1, 1, 1, 0, 0], [1, 1, 1, 1, 1], [1, 1, 0, 0, 0]])
    assert list(last_token_index(right, PaddingSide.RIGHT)) == [2, 4, 1]
    # ! Reading -1 on a right-padded batch pools a PAD position for every short text.
    assert list(last_token_index(right, PaddingSide.LEFT)) != [2, 4, 1]


def test_the_registry_profile_the_corpus_pins_is_the_one_the_template_comes_from(
    corpus: CorpusSpec, workspace: Path
) -> None:
    run = stage(corpus, workspace)
    assert run.profile is default_registry().get("id_regulation")


def test_progress_is_reported_from_the_first_batch_to_the_end(
    corpus: CorpusSpec, workspace: Path
) -> None:
    """A run you cannot watch is a run you cannot trust: the file says how far it is."""
    from embed import read_progress

    seen: list[Any] = []
    run = stage(corpus, workspace, batch_items=2, shard_rows=4)
    report = run.run(on_status=seen.append, status_every=0.0)

    data = read_progress(run.progress_path)
    assert data is not None
    assert data["phase"] == "done"
    assert data["total"] == data["done"] == data["sealed"] == report.vectors
    assert data["percent"] == 100.0
    assert data["windows"] == data["windows_total"] == report.windows
    assert len(seen) > report.windows, "progress must move between sealed windows too"


def test_a_resumed_run_counts_the_sealed_windows_as_done(
    corpus: CorpusSpec, workspace: Path
) -> None:
    from embed import read_progress

    first = stage(corpus, workspace, shard_rows=4)
    first.run()
    again = stage(corpus, workspace, shard_rows=4)
    again.run()
    data = read_progress(again.progress_path)
    assert data is not None
    assert (
        data["percent"] == 100.0 and data["this_run"] == 0 and data["sealed"] == data["total"]
    )


# ===========================================================================
# The bundle assembled from what the stages wrote.
def _sidecar_rows(doc_ids: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "doc_id": d,
            "verdict": "clean",
            "indexable": True,
            "chunks": 1,
            "damaged_chunks": 0,
            "words": 100,
            "unknown_share": 0.0,
            "damaged_share": 0.0,
            "replacement_per_1000": 0.0,
            "reasons": [],
        }
        for d in doc_ids
    ]


def test_a_finished_run_becomes_a_sealed_bundle_that_passes_preflight(
    corpus: CorpusSpec, workspace: Path, tmp_path: Path
) -> None:
    from bundle.build import build_bundle
    from bundle.writer import read_bundle, write_doc_quality
    from load import preflight

    run = stage(corpus, workspace)
    run.run()
    chunks = list(iter_chunk_shards(workspace, "fixture"))
    docs = sorted({c.doc_id for c in chunks})
    # a duplicated document, and one the corpus does not hold: both must be dropped
    quality = tmp_path / "q.parquet"
    write_doc_quality(quality, _sidecar_rows(docs + [docs[0], "sha256:not-in-corpus"]))

    text = "Pasal 1 wajib pajak"
    canary = np.asarray(run.embedder.embed_queries([text])[0], dtype=np.float32)
    report = build_bundle(
        corpus,
        workspace=workspace,
        root=tmp_path / "bundle",
        quality=quality,
        reference=(canary, text),
    )
    assert report.chunks == report.vectors == len(chunks)
    assert report.sidecars["doc_quality"] == len(docs)
    assert report.dropped["doc_quality"] == 2

    document = read_bundle(report.root)
    assert document["chunk_count"] == len(chunks)
    assert document["dense"]["dim"] == DIM
    result = preflight(report.root, target_dim=DIM, free_bytes=10**12)
    assert not result.failed, [c.detail for c in result.failed]


def test_a_half_embedded_corpus_is_not_bundled(
    corpus: CorpusSpec, workspace: Path, tmp_path: Path
) -> None:
    from bundle.build import BundleBuildError, build_bundle

    stage(corpus, workspace).run(limit=5)  # one window of several
    with pytest.raises(BundleBuildError, match="have no vector"):
        build_bundle(corpus, workspace=workspace, root=tmp_path / "bundle")
    assert not (tmp_path / "bundle" / "manifest.json").exists(), "nothing may be sealed"


def test_a_bundle_ships_the_vectors_the_stage_wrote_byte_for_byte(
    corpus: CorpusSpec, workspace: Path, tmp_path: Path
) -> None:
    from bundle.build import build_bundle

    run = stage(corpus, workspace)
    run.run()
    report = build_bundle(corpus, workspace=workspace, root=tmp_path / "b")
    written = {c: v for c, v in vectors_of(run).items()}
    shipped: dict[str, np.ndarray[Any, Any]] = {}
    for shard in sorted((report.root / "vectors").glob("part-*.parquet")):
        for chunk_id, vector in iter_vectors(shard):
            shipped[chunk_id] = np.asarray(vector, dtype=np.float32)
    assert set(shipped) == set(written)
    assert all(np.array_equal(shipped[c], written[c]) for c in written)


def test_a_quadratic_budget_keeps_long_texts_in_small_batches() -> None:
    from runtime.batching import batched

    short = ["a" * 300] * 40
    long_ = ["b" * 4000] * 6
    order = long_ + short  # longest first, as the stage sends them
    batches = list(batched(order, max_bytes=10**9, max_items=32, max_square=150_000_000))
    # a 4000-byte text allows 150e6 / 16e6 = 9 items in its batch, so the six long ones take
    # three short ones with them; after that 300-byte texts are bounded by the item cap
    assert [len(b) for b in batches] == [9, 32, 5]
    assert all(len(b) * len(b[0]) ** 2 <= 150_000_000 for b in batches)


def test_an_oversized_text_still_goes_out_alone_under_the_quadratic_budget() -> None:
    from runtime.batching import batched

    batches = list(batched(["x" * 20000, "y" * 5], max_square=150_000_000))
    assert batches[0] == ["x" * 20000] and batches[1] == ["y" * 5]


# ===========================================================================
# A serving engine over HTTP: the API backend, concurrency, and ordering.
class _FakeServer:
    """An OpenAI-compatible `/v1/embeddings` that answers out of order, slowly, and counts
    how many requests it saw at once: what vLLM or TEI looks like to the stage."""

    def __init__(self, embedder: Any, delay: float = 0.04) -> None:
        import json
        import threading
        import time
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        self.in_flight = 0
        self.max_in_flight = 0
        lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:  # silence
                pass

            def do_POST(self) -> None:  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                with lock:
                    outer.in_flight += 1
                    outer.max_in_flight = max(outer.max_in_flight, outer.in_flight)
                time.sleep(delay)
                vectors = np.asarray(embedder.embed_documents(body["input"]))
                rows = [
                    {"object": "embedding", "index": i, "embedding": vectors[i].tolist()}
                    for i in range(len(body["input"]))
                ]
                rows.reverse()  # a server may answer in any order; `index` is the truth
                payload = json.dumps(
                    {"object": "list", "data": rows, "model": body["model"], "usage": {}}
                ).encode()
                with lock:
                    outer.in_flight -= 1
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"

    def __enter__(self) -> _FakeServer:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def _served(corpus: CorpusSpec, workspace: Path, monkeypatch: pytest.MonkeyPatch, **cfg: Any):  # type: ignore[no-untyped-def]
    pytest.importorskip("openai")
    monkeypatch.setenv("EMBED_API_KEY", "test")
    reference = build_embedder(EmbedSpec.model_validate(embed_config()))
    return reference, _FakeServer(reference)


def test_vectors_from_a_server_equal_the_reference_whatever_order_it_answers_in(
    corpus: CorpusSpec, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reference, server = _served(corpus, workspace, monkeypatch)
    with server:
        api = EmbedSpec.model_validate(
            embed_config(kind="api", base_url=server.url, batch_items=3, concurrency=1)
        )
        run = Embedding(corpus, workspace=workspace, embedder=build_embedder(api), config=api)
        report = run.run()
    chunks = list(iter_chunk_shards(workspace, "fixture"))
    got = vectors_of(run)
    assert report.vectors == len(chunks) == len(got)
    for chunk in chunks:
        want = np.asarray(reference.embed_documents([embed_text(chunk, run.profile)]))[0]
        assert np.allclose(got[chunk.id], want, atol=1e-3), "a vector landed on the wrong chunk"


def test_concurrency_keeps_several_requests_in_flight_and_changes_no_vector(
    corpus: CorpusSpec, workspace: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    reference, server = _served(corpus, workspace, monkeypatch)
    results = {}
    with server:
        for width in (1, 4):
            ws = tmp_path / f"ws{width}"
            Extraction(corpus, workspace=ws).run()
            Chunking(corpus, workspace=ws).run()
            api = EmbedSpec.model_validate(
                embed_config(kind="api", base_url=server.url, batch_items=2, concurrency=width)
            )
            server.max_in_flight = 0
            run = Embedding(corpus, workspace=ws, embedder=build_embedder(api), config=api)
            run.run()
            results[width] = (server.max_in_flight, vectors_of(run))
    assert results[1][0] == 1, "concurrency 1 must send one request at a time"
    assert 1 < results[4][0] <= 4, f"saw {results[4][0]} in flight, expected 2..4"
    one, four = results[1][1], results[4][1]
    assert set(one) == set(four)
    assert all(np.array_equal(one[c], four[c]) for c in one), "concurrency changed a vector"


def test_a_local_model_ignores_concurrency() -> None:
    from embed.local import TransformersEmbedder

    assert getattr(TransformersEmbedder, "thread_safe", False) is False


def test_an_out_of_memory_retry_runs_after_the_failed_batch_is_released() -> None:
    """The retry must not run inside the `except` block.

    Inside it the exception's traceback pins the failed batch's tensors, so every nested
    retry starts with the memory that just ran out: under a memory cap a run died at 6.75 GB
    allocated. The invariant is that no exception is being handled while the halves run.
    """
    import sys
    import types

    from embed.local import TransformersEmbedder

    class OutOfMemory(Exception):
        pass

    seen: list[tuple[int, bool]] = []

    def encode(texts: list[str]) -> np.ndarray[Any, Any]:
        handling = sys.exc_info()[1] is not None
        seen.append((len(texts), handling))
        if len(texts) > 2:
            raise OutOfMemory
        return np.ones((len(texts), 2), dtype=np.float32)

    cuda = types.SimpleNamespace(OutOfMemoryError=OutOfMemory, empty_cache=lambda: None)
    embedder = object.__new__(TransformersEmbedder)
    embedder._torch = types.SimpleNamespace(cuda=cuda)  # type: ignore[assignment]
    embedder._encode = encode  # type: ignore[method-assign]
    out = embedder._encode_safely(["x"] * 8)
    assert out.shape == (8, 2)
    assert [n for n, _ in seen] == [8, 4, 2, 2, 4, 2, 2]
    assert not any(handling for _, handling in seen), "a retry ran inside the except block"


# ===========================================================================
# The workspace on a remote, and a corpus shared between workers.
def _remote(name: str):  # type: ignore[no-untyped-def]
    import fsspec

    fs = fsspec.filesystem("memory")
    base = f"/ravel-test/{name}"
    if fs.exists(base):
        fs.rm(base, recursive=True)
    return fs, base


def test_a_reclaimed_box_resumes_from_the_remote_without_recomputing(
    corpus: CorpusSpec, workspace: Path, tmp_path: Path
) -> None:
    """The cloud case: the instance and its disk are gone; a new one pulls and carries on."""
    from runtime.sync import AsyncPusher, RemoteMirror

    fs, base = _remote("reclaimed")
    first = stage(corpus, workspace, shard_rows=4)
    pusher = AsyncPusher(RemoteMirror(workspace, base, fs=fs), "fixture")
    report = first.run(on_progress=lambda r: pusher.submit())
    pushed = pusher.close()
    assert report.vectors > 0 and pushed.files > 0

    # a fresh machine: an empty workspace, only the remote survives
    fresh = tmp_path / "fresh"
    mirror = RemoteMirror(fresh, base, fs=fs)
    pulled = mirror.pull()
    assert mirror.pull_ledger("fixture"), "the ledger snapshot must come down too"
    assert pulled.files > 0

    again = stage(corpus, fresh, shard_rows=4)
    result = again.run()
    assert result.vectors == 0, "a resumed run recomputed windows the remote already held"
    assert result.skipped_done == report.windows
    assert set(vectors_of(again)) == set(vectors_of(first))


def test_a_push_adds_what_is_missing_and_repairs_a_short_upload(
    corpus: CorpusSpec, workspace: Path
) -> None:
    from runtime.sync import RemoteMirror

    fs, base = _remote("push")
    stage(corpus, workspace, shard_rows=4).run()
    mirror = RemoteMirror(workspace, base, fs=fs)
    first = mirror.push()
    second = mirror.push()
    assert first.files > 0 and second.files == 0 and second.skipped == first.files

    victim = next(p for p in fs.find(base) if p.endswith(".parquet") and "/vectors/" in p)
    fs.pipe_file(victim, b"cut short")
    assert mirror.push().files == 1, "a shard of the wrong size is a cut upload: send it again"


def test_no_database_file_and_no_partial_upload_reaches_the_remote(
    corpus: CorpusSpec, workspace: Path
) -> None:
    import sqlite3
    import tempfile

    from runtime.sync import RemoteMirror

    fs, base = _remote("clean")
    stage(corpus, workspace, shard_rows=4).run()
    mirror = RemoteMirror(workspace, base, fs=fs)
    mirror.push()
    assert mirror.push_ledger("fixture")
    names = [p for p in fs.find(base)]
    assert not any(n.endswith((".part", ".db-wal", ".db-shm", ".tmp")) for n in names)
    ledgers = [n for n in names if n.endswith("ledger.db")]
    assert len(ledgers) == 1, "the ledger goes up once, as a snapshot"
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "ledger.db"
        copy.write_bytes(fs.cat_file(ledgers[0]))
        db = sqlite3.connect(copy)
        try:
            count = db.execute("select count(*) from entries").fetchone()[0]
        finally:
            db.close()
    assert count > 0, "the snapshot is a whole, readable database"


def test_pull_never_overwrites_local_work(corpus: CorpusSpec, workspace: Path) -> None:
    from runtime.sync import RemoteMirror

    fs, base = _remote("pull")
    stage(corpus, workspace, shard_rows=4).run()
    mirror = RemoteMirror(workspace, base, fs=fs)
    mirror.push()
    mirror.push_ledger("fixture")

    ledger = workspace / "state" / "fixture" / "ledger.db"
    before = ledger.read_bytes()
    assert not mirror.pull_ledger("fixture"), "a local ledger is at least as new as a snapshot"
    assert ledger.read_bytes() == before
    assert mirror.pull().files == 0, "nothing is missing locally"


def test_workers_split_a_corpus_without_overlap_and_cover_all_of_it(
    corpus: CorpusSpec, workspace: Path
) -> None:
    shard_rows = 4
    reference = stage(corpus, workspace, shard_rows=shard_rows)
    chunks = list(iter_chunk_shards(workspace, "fixture"))

    for index in range(3):
        cfg = EmbedSpec.model_validate(embed_config(shard_rows=shard_rows))
        run = Embedding(
            corpus,
            workspace=workspace,
            embedder=build_embedder(cfg),
            config=cfg,
            worker=(index, 3),
        )
        run.run()
        assert run.ledger_path.name == f"ledger-embed-w{index}of3.db"
    # the three share one vector directory: every chunk exactly once across all of them
    final = vectors_of(reference)
    assert set(final) == {c.id for c in chunks}
    assert len(final) == len(chunks)


def test_a_worker_embeds_only_its_windows_and_a_rerun_does_nothing(
    corpus: CorpusSpec, workspace: Path
) -> None:
    cfg = EmbedSpec.model_validate(embed_config(shard_rows=3))

    def worker(i: int) -> Embedding:
        return Embedding(
            corpus, workspace=workspace, embedder=build_embedder(cfg), config=cfg, worker=(i, 2)
        )

    zero, one = worker(0), worker(1)
    first = zero.run()
    only_zero = set(vectors_of(zero))
    assert 0 < len(only_zero) < len(list(iter_chunk_shards(workspace, "fixture")))
    assert first.windows == zero.count_rows()[1]
    one.run()
    assert len(vectors_of(zero)) == len(list(iter_chunk_shards(workspace, "fixture")))
    assert worker(0).run().vectors == 0, "a worker's own ledger makes its rerun a no-op"


def test_force_is_refused_for_a_worker(corpus: CorpusSpec, workspace: Path) -> None:
    cfg = EmbedSpec.model_validate(embed_config(shard_rows=3))
    run = Embedding(
        corpus, workspace=workspace, embedder=build_embedder(cfg), config=cfg, worker=(0, 2)
    )
    with pytest.raises(ValueError, match="other workers"):
        run.run(force=True)


def test_a_bad_worker_spec_is_refused(corpus: CorpusSpec, workspace: Path) -> None:
    cfg = EmbedSpec.model_validate(embed_config())
    with pytest.raises(ValueError, match="worker"):
        Embedding(
            corpus, workspace=workspace, embedder=build_embedder(cfg), config=cfg, worker=(3, 3)
        )


def test_a_row_range_is_exactly_those_rows_whatever_the_batch_size(workspace: Path) -> None:
    from chunk.store import iter_chunk_range, iter_chunks

    shard = sorted((workspace / "chunks" / "fixture" / "default").rglob("part-*.parquet"))[0]
    everything = [c.id for c in iter_chunks(str(shard))]
    assert len(everything) > 6
    for begin, end in (
        (0, 3),
        (2, 6),
        (4, len(everything)),
        (len(everything), len(everything)),
    ):
        for batch in (1, 2, 3, 8192):
            got = [c.id for c in iter_chunk_range(str(shard), begin, end, batch_size=batch)]
            assert got == everything[begin:end], (begin, end, batch)


def test_a_resume_does_not_read_the_rows_of_windows_it_skips(
    corpus: CorpusSpec, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import embed.run as run_module

    stage(corpus, workspace, shard_rows=4).run()
    reads: list[tuple[int, int]] = []
    real = run_module.iter_chunk_range

    def spy(path: str, begin: int, end: int, *a: Any, **k: Any):  # type: ignore[no-untyped-def]
        reads.append((begin, end))
        return real(path, begin, end, *a, **k)

    monkeypatch.setattr(run_module, "iter_chunk_range", spy)
    again = stage(corpus, workspace, shard_rows=4).run()
    assert again.vectors == 0 and again.skipped_done > 0
    assert reads == [], "a sealed window was read just to be skipped"


def test_the_progress_file_says_where_the_time_went_outside_the_model(
    corpus: CorpusSpec, workspace: Path
) -> None:
    from embed import read_progress

    run = stage(corpus, workspace, shard_rows=4)
    run.run()
    data = read_progress(run.progress_path)
    assert data is not None
    assert set(data["phases"]) >= {"read", "prepare", "write"}
    assert all(seconds >= 0 for seconds in data["phases"].values())


def test_a_file_the_run_is_rewriting_is_left_for_the_next_push(
    corpus: CorpusSpec, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from runtime.sync import RemoteMirror

    fs, base = _remote("busy")
    stage(corpus, workspace, shard_rows=4).run()
    mirror = RemoteMirror(workspace, base, fs=fs)
    real = fs.put_file
    refused = {"left": 1}

    def busy(local: str, remote: str, *a: Any, **k: Any) -> None:
        if local.endswith(".json") and refused["left"]:
            refused["left"] -= 1
            raise PermissionError("the file is being replaced")
        return real(local, remote, *a, **k)

    monkeypatch.setattr(fs, "put_file", busy)
    first = mirror.push()
    assert len(first.errors) == 1 and first.files > 0, "one busy file must not stop the push"
    second = mirror.push()
    assert not second.errors and second.files == 1, "the busy file goes up next time"


def test_a_sequence_the_tokenizer_already_closed_is_not_closed_twice() -> None:
    """Qwen3's tokenizer ends every sequence with `<|endoftext|>`; its `eos_token` is the
    different `<|im_end|>`. Appending `eos_token_id` anyway pooled every vector at a token
    placed after the real one: a different space (cosine 0.64 on the same sentence)."""
    import types

    from embed.local import TransformersEmbedder
    from embed.spec import Pooling

    END_OF_TEXT, IM_END = 9, 8

    class Tok:
        eos_token_id = IM_END
        all_special_ids = (END_OF_TEXT, IM_END)

        def __init__(self, closes: bool) -> None:
            self.closes = closes

        def __call__(self, texts: list[str], **_: object) -> dict[str, list[list[int]]]:
            tail = [END_OF_TEXT] if self.closes else []
            return {"input_ids": [[1, 2, 3, *tail] for _ in texts]}

        def pad(
            self, ids: dict[str, list[list[int]]], **_: object
        ) -> dict[str, list[list[int]]]:
            return ids

    embedder = object.__new__(TransformersEmbedder)
    embedder.spec = types.SimpleNamespace(pooling=Pooling.LAST_TOKEN, max_length=16)  # type: ignore[assignment]
    embedder.append_eos = False

    embedder.tokenizer = Tok(closes=True)
    assert embedder._tokenize(["a"])["input_ids"] == [[1, 2, 3, END_OF_TEXT]]

    # A tokenizer that leaves the sequence open still gets a closing token.
    embedder.tokenizer = Tok(closes=False)
    assert embedder._tokenize(["a"])["input_ids"] == [[1, 2, 3, IM_END]]


def test_the_first_recipe_survives_as_a_declared_option() -> None:
    """A corpus of 1.7M vectors was embedded with `<|im_end|>` after `<|endoftext|>`. That
    stays reproducible, but only when the corpus config says so, and it keeps the old
    fingerprint so the vectors on disk stay valid; the standard recipe is a new one."""
    import types

    from embed.factory import spec_of
    from embed.local import TransformersEmbedder
    from embed.spec import Pooling
    from spec.corpus import EmbedSpec

    END_OF_TEXT, IM_END = 9, 8

    class Tok:
        eos_token_id = IM_END
        all_special_ids = (END_OF_TEXT, IM_END)

        def __call__(self, texts: list[str], **_: object) -> dict[str, list[list[int]]]:
            return {"input_ids": [[1, 2, END_OF_TEXT] for _ in texts]}

        def pad(
            self, ids: dict[str, list[list[int]]], **_: object
        ) -> dict[str, list[list[int]]]:
            return ids

    embedder = object.__new__(TransformersEmbedder)
    embedder.spec = types.SimpleNamespace(pooling=Pooling.LAST_TOKEN, max_length=16)  # type: ignore[assignment]
    embedder.tokenizer = Tok()
    embedder.append_eos = True
    assert embedder._tokenize(["a"])["input_ids"] == [[1, 2, END_OF_TEXT, IM_END]]

    base = {
        "id": "q",
        "kind": "local",
        "model": "Qwen/Qwen3-Embedding-0.6B",
        "revision": "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
        "dim": 1024,
    }
    legacy = spec_of(EmbedSpec(**base, append_eos=True))
    standard = spec_of(EmbedSpec(**base))
    assert legacy.fingerprint() != standard.fingerprint()
    assert standard.extra == {"tokenization": "tokenizer-closed"} and legacy.extra == {}


def test_token_batches_pad_only_the_gap_between_neighbours() -> None:
    from runtime.batching import batched_by_tokens

    lengths = [900, 5, 300, 301, 299, 12, 11, 700, 300, 2]
    order = sorted(range(len(lengths)), key=lambda i: lengths[i], reverse=True)
    batches = list(batched_by_tokens(order, lengths, budget=1000, max_items=4))

    assert sorted(i for b in batches for i in b) == list(range(len(lengths)))  # nothing lost
    for batch in batches:
        assert len(batch) <= 4
        longest = max(lengths[i] for i in batch)
        assert len(batch) == 1 or len(batch) * longest <= 1000  # a lone giant may exceed it
    # a text longer than the budget goes out alone instead of being dropped
    assert [900] in [[lengths[i] for i in b] for b in batches]


def test_fused_rms_norm_is_the_same_function_as_the_reference() -> None:
    torch = pytest.importorskip("torch")
    from embed.local import _fuse_rms_norms

    class Qwen3RMSNorm(torch.nn.Module):  # the reference implementation, copied
        def __init__(self) -> None:
            super().__init__()
            self.weight = torch.nn.Parameter(torch.rand(64) + 0.5)
            self.variance_epsilon = 1e-6

        def forward(self, h: Any) -> Any:
            dtype = h.dtype
            h = h.to(torch.float32)
            h = h * torch.rsqrt(h.pow(2).mean(-1, keepdim=True) + self.variance_epsilon)
            return self.weight * h.to(dtype)

    holder = torch.nn.Sequential(Qwen3RMSNorm())
    x = torch.randn(3, 7, 64)
    reference = holder(x)
    assert _fuse_rms_norms(holder) == 1
    fused = holder(x)
    cosine = torch.nn.functional.cosine_similarity(fused, reference, dim=-1)
    assert cosine.min() > 0.99999
    assert torch.allclose(fused, reference, atol=1e-5)


def test_compile_is_only_attempted_where_triton_and_a_compiler_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib.util
    import shutil

    from embed.local import can_compile

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    ok, why = can_compile()
    assert not ok and "Triton" in why

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(shutil, "which", lambda name: None)
    ok, why = can_compile()
    assert not ok and "compiler" in why

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/gcc")
    assert can_compile() == (True, "")


def test_the_standard_recipe_has_one_fingerprint_marker_for_every_backend() -> None:
    """An engine-built corpus must not share a fingerprint with the legacy vectors."""
    from embed.factory import spec_of
    from spec.corpus import EmbedSpec

    base = {
        "id": "q",
        "model": "Qwen/Qwen3-Embedding-0.6B",
        "revision": "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
        "dim": 1024,
    }
    local = spec_of(EmbedSpec(**base, kind="local"))
    engine = spec_of(EmbedSpec(**base, kind="api", base_url="http://x/v1"))
    legacy = spec_of(EmbedSpec(**base, kind="local", append_eos=True))

    assert local.extra == engine.extra == {"tokenization": "tokenizer-closed"}
    assert legacy.extra == {}
    assert engine.fingerprint() != legacy.fingerprint()
