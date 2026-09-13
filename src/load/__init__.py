"""Getting a bundle into Postgres — preflight, `COPY`, verification.

Nothing in this package opens a connection. It reads a sealed bundle and produces two
things: a verdict on whether the bundle may load at all, and the procedure that would load
it. Both are inspectable before anything touches a database, which is the point — the
alternative is discovering a problem at 90% of a `COPY` that has been running for an hour.

`psycopg` is an optional extra (`pip install ravel[load]`) and is deliberately not
imported here.
"""

from load.plan import Step, copy_from, load_plan, quote_ident, quote_literal
from load.preflight import (
    Check,
    LoadReport,
    Status,
    check_canary,
    check_checksums,
    check_completeness,
    check_consistency,
    check_dimension,
    check_disk,
    check_provenance,
    preflight,
)

__all__ = [
    "Check",
    "LoadReport",
    "Status",
    "Step",
    "check_canary",
    "check_checksums",
    "check_completeness",
    "check_consistency",
    "check_dimension",
    "check_disk",
    "check_provenance",
    "copy_from",
    "load_plan",
    "preflight",
    "quote_ident",
    "quote_literal",
]
