"""Relabelling a built bundle: the chunker's own rule, applied after the fact.

No database and no model: a two-shard bundle written here, and an embedder that is a pure
function of the text, so "reproduces the stored vectors" is checkable exactly.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from bundle.relabel import RelabelError, plan_relabel, relabel_bundle
from bundle.writer import sha256_file
from chunk.derive import embed_text
from chunk.labels import ExplanationRule
from load.preflight import check_checksums
from spec import default_registry

PROFILE = default_registry().get("id_regulation_md@1.3")
DIM = 8


def fake_embed(texts: list[str]) -> np.ndarray:
    out = np.stack(
        [
            np.frombuffer(hashlib.sha256(t.encode()).digest()[:DIM], dtype=np.uint8).astype(
                np.float32
            )
            + 1.0
            for t in texts
        ]
    )
    return out / np.linalg.norm(out, axis=1, keepdims=True)


ROWS = [
    # (id, chapter, article, body)
    ("a1", "BAB I - KETENTUAN UMUM", "Pasal 1", "(1) Setiap orang wajib mendaftar."),
    ("a2", "PENUTUP", "Pasal 6", "Ayat (1) Cukup jelas Ayat (2) Cukup jelas"),
    ("a3", "PENJELASAN - II", "Pasal 7", "Cukup jelas"),
    ("b1", "LAMPIRAN", "Pasal 9", "Pasal 9 Huruf a Yang dimaksud dengan izin adalah ..."),
    ("b2", "BAB II - PERIZINAN", "Pasal 9", "a. izin usaha; b. izin lokasi."),
    ("b3", "N/A", "Pembukaan", "Yang dimaksud dengan peraturan ini ..."),
]


def make_bundle(root: Path) -> None:
    from types import SimpleNamespace

    checksums: dict[str, str] = {}
    counts: dict[str, int] = {}
    for n, part in enumerate((ROWS[:3], ROWS[3:])):
        rows = [
            {
                "id": i,
                "chapter": c,
                "article": a,
                "body": b,
                "regulation_type": "PERDA",
                "regulation_number": "1",
                "year": 2020,
                "about": "IZIN",
                "profile": "id_regulation_md@1.2",
            }
            for i, c, a, b in part
        ]
        rel = f"chunks/part-{n:05d}.parquet"
        (root / "chunks").mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(rows), root / rel)
        vec = fake_embed([embed_text(SimpleNamespace(**r), PROFILE) for r in rows])
        vrel = f"vectors/part-{n:05d}.parquet"
        (root / "vectors").mkdir(parents=True, exist_ok=True)
        emb = pa.FixedSizeListArray.from_arrays(
            pa.array(vec.astype(np.float16).reshape(-1)), DIM
        )
        pq.write_table(
            pa.table({"chunk_id": [r["id"] for r in rows], "embedding": emb}), root / vrel
        )
        for r_, k in ((rel, len(rows)), (vrel, len(rows))):
            checksums[r_] = sha256_file(root / r_)
            counts[r_] = k
    doc = {
        "bundle_version": "1",
        "manifest_sha256": "p" * 64,
        "corpus_id": "t",
        "profile_ref": "id_regulation_md",
        "chunk_count": len(ROWS),
        "inventory": {"counts": counts, "checksums": checksums, "files": len(checksums)},
    }
    (root / "manifest.json").write_text(json.dumps(doc), encoding="utf-8")


def test_the_rule_reads_the_text_and_leaves_the_rest() -> None:
    rule = ExplanationRule.compile(PROFILE.spec.structure.explanation)  # type: ignore[arg-type]
    labels = {i: rule.chapter_for(c, a, b) for i, c, a, b in ROWS}
    assert labels == {
        "a1": None,
        "a2": "PENJELASAN",
        "a3": None,
        "b1": "PENJELASAN",
        "b2": None,
        "b3": None,
    }


def test_relabel_rewrites_labels_and_vectors_and_nothing_else(tmp_path: Path) -> None:
    src, out = tmp_path / "v1", tmp_path / "v2"
    make_bundle(src)
    assert set(plan_relabel(src, PROFILE)) == {"a2", "b1"}
    report = relabel_bundle(src, out, PROFILE, fake_embed, check=4)
    assert report.changed == 2 and report.reproduced > 0.999
    chunks = pa.concat_tables(
        [pq.read_table(p) for p in sorted((out / "chunks").glob("*.parquet"))]
    ).to_pylist()
    by = {r["id"]: r for r in chunks}
    assert [r["id"] for r in chunks] == [r[0] for r in ROWS]  # ids and order unchanged
    assert by["a2"]["chapter"] == "PENJELASAN" and by["a2"]["profile"] == "id_regulation_md@1.3"
    assert by["a1"]["chapter"] == "BAB I - KETENTUAN UMUM"
    assert by["a1"]["profile"] == "id_regulation_md@1.2"
    doc = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert doc["relabel"]["parent_manifest_sha256"] == "p" * 64
    assert doc["manifest_sha256"] != "p" * 64 and doc["profile_ref"] == "id_regulation_md@1.3"
    assert check_checksums(out, doc).status.value == "ok"
    plan = pq.read_table(out / "relabel.parquet").to_pydict()
    assert plan == {
        "id": ["a2", "b1"],
        "old_chapter": ["PENUTUP", "LAMPIRAN"],
        "new_chapter": ["PENJELASAN", "PENJELASAN"],
    }


def test_an_embedder_that_does_not_reproduce_the_bundle_is_refused(tmp_path: Path) -> None:
    src = tmp_path / "v1"
    make_bundle(src)

    def other(texts: list[str]) -> np.ndarray:
        return fake_embed([t + " " for t in texts])

    with pytest.raises(RelabelError, match="does not reproduce"):
        relabel_bundle(src, tmp_path / "v2", PROFILE, other, check=4)
    assert not (tmp_path / "v2" / "manifest.json").exists()
