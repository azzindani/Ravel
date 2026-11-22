"""A registry file a published dataset was made with is never edited or removed
(`registry/published.yaml`, `PROFILES.md` §7): a change is a new version beside it."""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def test_every_published_registry_file_is_as_it_was_published() -> None:
    entries = yaml.safe_load((ROOT / "registry" / "published.yaml").read_text("utf-8"))
    changed = []
    for entry in entries["published"]:
        for name, digest in entry["files"].items():
            path = ROOT / name
            if not path.is_file():
                changed.append(f"{name} ({entry['dataset']}): removed")
            elif _digest(path) != digest:
                changed.append(f"{name} ({entry['dataset']}): edited")
    assert not changed, (
        "published registry files changed; write a new version file instead: "
        + "; ".join(changed)
    )


def test_a_published_profile_still_loads() -> None:
    from spec import default_registry

    entries = yaml.safe_load((ROOT / "registry" / "published.yaml").read_text("utf-8"))
    refs = [
        Path(name).stem
        for entry in entries["published"]
        for name in entry["files"]
        if name.startswith("registry/profiles/")
    ]
    registry = default_registry()
    for ref in refs:
        assert registry.get(ref).ref == ref
