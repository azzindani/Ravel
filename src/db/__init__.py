"""Make a database ready to serve, from a sealed bundle.

This is the one process that turns a bundle into a database the retrieval engine can start
on. Ravel runs it to build a deployable database; Vera runs the same code to deploy one. A
second implementation of "load, index, cluster, verify" in another repository is how the two
drift apart, so there is not one.

`prepare` is the whole path. `place` (fit the routing structure from the bundle's vectors,
before anything is loaded) and `accelerate` (the provenance guard and the optional indexes)
are its parts, public so they can be run and tested alone.
"""

from db.accelerate import AcceleratorReport, accelerate
from db.place import place
from db.prepare import PrepareError, PrepareReport, prepare
from db.verify import VerifyError, verify_ready

__all__ = [
    "AcceleratorReport",
    "PrepareError",
    "PrepareReport",
    "VerifyError",
    "accelerate",
    "place",
    "prepare",
    "verify_ready",
]
