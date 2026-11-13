"""The parts of the packed-database path that need no Docker: what attach refuses, and why."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from db import image
from db.image import ImageError, attach_image


def _snapshot(tmp_path: Path, *, image_id: str = "sha256:aaa") -> Path:
    tar = tmp_path / "db.tar"
    tar.write_bytes(b"not really a tar")
    (tmp_path / "db.tar.json").write_text(
        json.dumps(
            {
                "format": 1,
                "postgres_image": "vera-db:test",
                "postgres_image_id": image_id,
                "user": "vera",
                "database": "vera",
                "bytes": 16,
                "sha256": "0" * 64,
                "manifest": {},
            }
        ),
        encoding="utf-8",
    )
    return tar


def test_a_snapshot_without_its_record_is_refused(tmp_path: Path) -> None:
    tar = tmp_path / "db.tar"
    tar.write_bytes(b"x")
    with pytest.raises(ImageError, match="record"):
        attach_image(tar, volume="v", password="p")


def test_a_snapshot_is_refused_by_a_postgres_it_was_not_built_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A data directory is only valid for the server that wrote it."""
    monkeypatch.setattr(image, "_image_id", lambda _image: "sha256:bbb")
    with pytest.raises(ImageError, match="not the Postgres"):
        attach_image(_snapshot(tmp_path), volume="v", password="p")


def test_attach_never_writes_into_a_volume_that_already_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(image, "_image_id", lambda _image: "sha256:aaa")
    monkeypatch.setattr(
        image,
        "_docker",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=b"", stderr=b""),
    )
    with pytest.raises(ImageError, match="already exists"):
        attach_image(_snapshot(tmp_path), volume="v", password="p")


def test_a_free_port_avoids_the_windows_excluded_range() -> None:
    for _ in range(20):
        assert 20000 <= image._free_port() <= 40000
