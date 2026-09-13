"""Loading profiles from the registry.

The registry is a directory of YAML, in git, reviewed like code (INTERFACES.md §6).
Its location is configuration — `RAVEL_REGISTRY`, an explicit argument, or the
`registry/` directory beside the project — so a cloud session can point at a checkout
and a Docker image can bake one in.

! A registry entry is immutable once used in a published bundle: a change is a new
version. That is what keeps `config_hash` meaningful.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

import yaml

from spec.models import Profile, ProfileSpec

ENV_VAR = "RAVEL_REGISTRY"
PROFILES_DIR = "profiles"


class RegistryError(RuntimeError):
    pass


def registry_root(explicit: Path | str | None = None) -> Path:
    """Where profiles live: argument, then environment, then a checkout, then the default.

    ! The working-directory branch is not a convenience. `parents[2]` resolves to the
    repository root when `src/` is on the path and to `<venv>/Lib/` when Ravel is installed
    from a wheel — so an installed `ravel profiles list` looked for
    `<venv>/Lib/registry/profiles` and refused. The registry is data in git, reviewed
    alongside the code (`CLAUDE.md` §12), which means the copy an operator wants is almost
    always the one in the checkout they are standing in.

    Ordered so an explicit choice always wins: the argument, then `RAVEL_REGISTRY`, then
    `./registry` if it looks like one, then the packaged path. Nothing here searches
    upward — a registry found two directories above the one you are in is a surprise, and
    profiles decide how documents are parsed.
    """
    if explicit:
        return Path(explicit)
    if env := os.environ.get(ENV_VAR):
        return Path(env)
    local = Path.cwd() / "registry"
    if (local / PROFILES_DIR).is_dir():
        return local
    return Path(__file__).resolve().parents[2] / "registry"


def load_spec(path: Path) -> ProfileSpec:
    """One YAML file to a validated spec, with the filename cross-checked.

    ! The filename must agree with the contents. A file named `id_regulation@1.0.yaml`
    whose body says version 1.1 is ambiguous in exactly the way a cache key cannot
    tolerate, so it is an error rather than a preference.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RegistryError(f"{path.name}: invalid YAML · {exc}") from exc
    if not isinstance(raw, dict):
        raise RegistryError(f"{path.name}: expected a mapping at the top level")

    try:
        spec = ProfileSpec.model_validate(raw)
    except Exception as exc:
        raise RegistryError(f"{path.name}: {exc}") from exc

    expected = f"{spec.ref}.yaml"
    if path.name != expected:
        raise RegistryError(
            f"{path.name}: contents declare {spec.ref}, so the file must be {expected}"
        )
    return spec


def iter_specs(root: Path | str | None = None) -> Iterator[ProfileSpec]:
    directory = registry_root(root) / PROFILES_DIR
    if not directory.is_dir():
        raise RegistryError(f"no profile registry at {directory} · set {ENV_VAR}")
    for path in sorted(directory.glob("*.yaml")):
        yield load_spec(path)


class Registry:
    """Profiles indexed by `id` and `id@version`."""

    def __init__(self, profiles: list[Profile]) -> None:
        self._by_ref: dict[str, Profile] = {}
        for profile in profiles:
            if profile.ref in self._by_ref:
                raise RegistryError(f"duplicate profile {profile.ref}")
            self._by_ref[profile.ref] = profile
        # A bare id resolves to the highest version present.
        for profile in sorted(profiles, key=lambda p: _version_key(p.version)):
            self._by_ref[profile.id] = profile

    @classmethod
    def load(cls, root: Path | str | None = None) -> Registry:
        return cls([Profile.compile(spec) for spec in iter_specs(root)])

    def get(self, ref: str) -> Profile:
        if ref not in self._by_ref:
            raise KeyError(f"no profile {ref!r} · known: {self.refs()}")
        return self._by_ref[ref]

    def refs(self) -> list[str]:
        return sorted({p.ref for p in self._by_ref.values()})

    def __iter__(self) -> Iterator[Profile]:
        # ! Deduplicate by ref, not by object identity: a Profile holds a pydantic
        # spec and is therefore unhashable, and each one is indexed twice (bare id
        # and id@version).
        seen: dict[str, Profile] = {}
        for profile in self._by_ref.values():
            seen.setdefault(profile.ref, profile)
        return iter(seen.values())

    def __len__(self) -> int:
        return len({p.ref for p in self._by_ref.values()})

    def route(self, *, mime: str, sample: str) -> Profile | None:
        """The best-matching profile for a document, or None if nothing fits.

        Ties break on priority then id, so routing is deterministic — two runs over
        the same corpus must produce the same assignment or the cache is a lie.
        """
        viable = [
            (score, profile)
            for profile in self
            if (score := profile.score(mime=mime, sample=sample)) is not None
        ]
        if not viable:
            return None
        return max(viable, key=lambda sp: (sp[0], sp[1].id))[1]


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


@lru_cache(maxsize=4)
def default_registry(root: str | None = None) -> Registry:
    """Process-wide cache. Profiles are immutable, so sharing them is safe."""
    return Registry.load(root)
