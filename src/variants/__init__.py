"""The experiment harness: named, hashed configurations that produce their own bundles.

A variant exists so that "is chunking on pasal better than 512-token windows?" has an
answer rather than an advocate (`VARIANTS.md` §1).
"""

from variants.spec import (
    STAGES,
    VariantError,
    VariantSpec,
    check_baseline_present,
    matrix,
    reuse_plan,
)

__all__ = [
    "STAGES",
    "VariantError",
    "VariantSpec",
    "check_baseline_present",
    "matrix",
    "reuse_plan",
]
