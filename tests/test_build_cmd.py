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


def test_publish_takes_its_card_from_git_and_stages_it_beside_the_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    exported: list[Path] = []

    def export(corpus_id: str, out_dir: Path, **kwargs: Any) -> None:
        (out_dir / "data").mkdir(parents=True)
        exported.append(out_dir)

    monkeypatch.setattr(cli, "export_cmd", export)
    card = tmp_path / "X_2611.md"
    card.write_text("# X", encoding="utf-8")
    cli.publish_cmd("ca_laws", "Owner/X_2611", workspace=tmp_path, card=card, dry_run=True)
    assert exported == [tmp_path / "publish" / "X_2611"]
    assert (exported[0] / "README.md").read_text(encoding="utf-8") == "# X"


def test_publish_without_a_card_in_git_is_refused(tmp_path: Path) -> None:
    with pytest.raises(typer.Exit):
        cli.publish_cmd("ca_laws", "Owner/NO_CARD_2611", workspace=tmp_path, dry_run=True)
