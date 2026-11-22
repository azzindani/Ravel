"""`ravel build` and `ravel update`: every stage, in order, from what git holds.

The stages are replaced by recorders: each is tested on its own, and what is worth failing
on here is the order, and the steps that run only when asked for."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import typer

import cli

STAGES = (
    "sources_fetch",
    "extract",
    "chunk_cmd",
    "signals_cmd",
    "sparse_cmd",
    "verify_backend_cmd",
    "embed_cmd",
    "bundle_build",
    "load_cmd",
)


@pytest.fixture
def ran(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []
    for name in STAGES:

        def record(*args: Any, _name: str = name, **kwargs: Any) -> None:
            calls.append((_name, kwargs))

        monkeypatch.setattr(cli, name, record)
    return calls


def test_a_build_runs_every_stage_from_the_publisher(
    ran: list[tuple[str, dict[str, Any]]], tmp_path: Path
) -> None:
    cli.build_cmd("ca_laws", tmp_path / "bundle", workspace=tmp_path / "ws")
    assert [name for name, _ in ran] == [
        "sources_fetch",
        "extract",
        "chunk_cmd",
        "signals_cmd",
        "sparse_cmd",
        "embed_cmd",
        "bundle_build",
    ]
    # ! A first build fits its own vocabulary and reuses nothing.
    assert dict(ran)["sparse_cmd"].get("vocab") is None
    assert not dict(ran)["embed_cmd"].get("reuse")


def test_an_endpoint_is_verified_before_anything_is_embedded_through_it(
    ran: list[tuple[str, dict[str, Any]]], tmp_path: Path
) -> None:
    cli.build_cmd(
        "ca_laws",
        tmp_path / "bundle",
        workspace=tmp_path / "ws",
        fetch=False,
        base_url="http://localhost:8000/v1",
        dsn="postgresql://x",
    )
    names = [name for name, _ in ran]
    assert "sources_fetch" not in names
    assert names.index("verify_backend_cmd") < names.index("embed_cmd")
    assert names[-1] == "load_cmd" and dict(ran)["load_cmd"]["replace"] is True


def test_a_sealed_bundle_is_never_built_over(
    ran: list[tuple[str, dict[str, Any]]], tmp_path: Path
) -> None:
    (tmp_path / "bundle").mkdir()
    with pytest.raises(typer.Exit):
        cli.build_cmd("ca_laws", tmp_path / "bundle", workspace=tmp_path / "ws")
    assert ran == []
