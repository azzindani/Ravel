"""Declarative document profiles: the knowledge that must not live in code."""

from spec.corpus import CorpusSpec, ExtractSpec, ProvenanceSpec, SourcesSpec
from spec.loader import ENV_VAR, Registry, RegistryError, default_registry, registry_root
from spec.models import (
    CleanupSpec,
    IdentitySpec,
    MatchSpec,
    Profile,
    ProfileSpec,
    StructureSpec,
    Unit,
    UnitSpec,
)

__all__ = [
    "ENV_VAR",
    "CleanupSpec",
    "CorpusSpec",
    "ExtractSpec",
    "ProvenanceSpec",
    "SourcesSpec",
    "IdentitySpec",
    "MatchSpec",
    "Profile",
    "ProfileSpec",
    "Registry",
    "RegistryError",
    "StructureSpec",
    "Unit",
    "UnitSpec",
    "default_registry",
    "registry_root",
]
