"""A database that is already built: pack it once, attach it in a minute.

`ravel db prepare` turns a bundle into a ready database in about 16 minutes at 1.7M chunks.
That is the price of building one; it should be paid once, by Ravel, not at every deploy.
`build_image` pays it in a throw-away Postgres container and packs the container's data
directory; `attach_image` unpacks that into a volume and checks the result. A deploy is then
a file copy and a start-up, not a rebuild.

! A physical copy of a data directory is only valid for the Postgres it was made by: the same
major version, the same extension builds. So the snapshot records the image it was built with
and `attach_image` refuses a different one, rather than starting a server on pages it may
misread. The credentials are part of the data directory too (a role's password lives in the
catalog), so they are the ones given at build time.
"""

from __future__ import annotations

import hashlib
import json
import random
import socket
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from db.prepare import PrepareReport, prepare
from db.verify import verify_ready

__all__ = ["ImageError", "ImageReport", "attach_image", "build_image"]

DATA_DIR = "/var/lib/postgresql/data"
SNAPSHOT_FORMAT = 1


class ImageError(RuntimeError):
    """The snapshot could not be built or attached. What it says is what to fix."""


@dataclass
class ImageReport:
    path: Path
    bytes: int
    sha256: str
    seconds: dict[str, float]
    prepared: PrepareReport | None = None


def _docker(*args: str, check: bool = True, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    try:
        result = subprocess.run(["docker", *args], check=False, **kwargs)
    except FileNotFoundError as exc:
        raise ImageError("docker is not installed or not on PATH") from exc
    if check and result.returncode != 0:
        detail = ""
        if isinstance(result.stderr, bytes):
            detail = result.stderr.decode("utf-8", "replace").strip()[:300]
        raise ImageError(f"`docker {' '.join(args[:3])}` failed ({result.returncode}) {detail}")
    return result


def _image_id(image: str) -> str:
    out = _docker("image", "inspect", image, "--format", "{{.Id}}", capture_output=True)
    return str(out.stdout.decode().strip())


def _free_port() -> int:
    for _ in range(50):
        port = random.randint(20000, 40000)
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise ImageError("no free local port found")


def _wait_ready(container: str, user: str, timeout: float = 120.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        probe = _docker(
            "exec", container, "pg_isready", "-U", user, check=False, capture_output=True
        )
        if probe.returncode == 0:
            time.sleep(1.0)  # the entrypoint restarts the server once after initdb
            again = _docker(
                "exec", container, "pg_isready", "-U", user, check=False, capture_output=True
            )
            if again.returncode == 0:
                return
        time.sleep(1.0)
    raise ImageError(f"{container}: Postgres did not become ready in {timeout:.0f}s")


def _dsn(port: int, user: str, password: str, database: str) -> str:
    return f"host=127.0.0.1 port={port} dbname={database} user={user} password={password}"


def _start(
    image: str, volume: str, name: str, port: int, user: str, password: str, db: str
) -> None:
    command = [
        "run", "-d", "--name", name,
        "-v", f"{volume}:{DATA_DIR}",
        "-p", f"127.0.0.1:{port}:5432",
        "-e", f"POSTGRES_USER={user}",
        "-e", f"POSTGRES_PASSWORD={password}",
        "-e", f"POSTGRES_DB={db}",
        image,
    ]  # fmt: skip
    _docker(*command, capture_output=True)
    _wait_ready(name, user)


def build_image(
    bundle: Path | str,
    out: Path | str,
    *,
    postgres_image: str = "vera-db:pg16-rum-bm25",
    user: str = "vera",
    password: str,
    database: str = "vera",
    rows_per_cluster: int = 2000,
    tolerance: float = 0.0,
    on_note: Callable[[str], None] | None = None,
) -> ImageReport:
    """Prepare the bundle in a throw-away Postgres and pack its data directory to `out`."""
    note = on_note or (lambda _m: None)
    root, target = Path(bundle), Path(out)
    document = json.loads((root / "manifest.json").read_text("utf-8"))
    tag = f"{random.randint(0, 0xFFFFFF):06x}"
    volume, name, port = f"ravel-image-{tag}", f"ravel-image-{tag}", _free_port()
    seconds: dict[str, float] = {}
    image_id = _image_id(postgres_image)

    _docker("volume", "create", volume, capture_output=True)
    try:
        started = time.time()
        _start(postgres_image, volume, name, port, user, password, database)
        seconds["start"] = time.time() - started

        note(f"preparing {document['corpus_id']} in {name} (port {port})")
        started = time.time()
        prepared = prepare(
            root,
            _dsn(port, user, password, database),
            rows_per_cluster=rows_per_cluster,
            tolerance=tolerance,
            on_note=note,
        )
        seconds["prepare"] = time.time() - started

        started = time.time()
        _docker(
            "exec", name, "psql", "-U", user, "-d", database, "-c", "CHECKPOINT",
            capture_output=True,
        )  # fmt: skip
        _docker("stop", "-t", "300", name, capture_output=True)
        seconds["stop"] = time.time() - started

        started = time.time()
        digest = hashlib.sha256()
        total = 0
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as sink:
            proc = subprocess.Popen(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-v",
                    f"{volume}:/data:ro",
                    "--entrypoint",
                    "tar",
                    postgres_image,
                    "-C",
                    "/data",
                    "-cf",
                    "-",
                    ".",
                ],  # fmt: skip
                stdout=subprocess.PIPE,
            )
            assert proc.stdout is not None
            for block in iter(lambda: proc.stdout.read(8 << 20), b""):  # type: ignore[union-attr]
                sink.write(block)
                digest.update(block)
                total += len(block)
            if proc.wait() != 0:
                raise ImageError("tar of the data directory failed")
        seconds["pack"] = time.time() - started

        sidecar = {
            "format": SNAPSHOT_FORMAT,
            "corpus_id": document["corpus_id"],
            "bundle_manifest_sha256": document["manifest_sha256"],
            "postgres_image": postgres_image,
            "postgres_image_id": image_id,
            "user": user,
            "database": database,
            "bytes": total,
            "sha256": digest.hexdigest(),
            "manifest": document,
        }
        Path(str(target) + ".json").write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
        return ImageReport(
            path=target,
            bytes=total,
            sha256=digest.hexdigest(),
            seconds=seconds,
            prepared=prepared,
        )
    finally:
        _docker("rm", "-f", name, check=False, capture_output=True)
        _docker("volume", "rm", "-f", volume, check=False, capture_output=True)


def attach_image(
    snapshot: Path | str,
    *,
    volume: str,
    postgres_image: str | None = None,
    password: str,
    verify: bool = True,
    on_note: Callable[[str], None] | None = None,
) -> ImageReport:
    """Unpack a packed database into a new volume, then start it and check it is ready."""
    note = on_note or (lambda _m: None)
    path = Path(snapshot)
    sidecar_path = Path(str(path) + ".json")
    if not sidecar_path.exists():
        raise ImageError(f"{sidecar_path} is missing: a snapshot is the tar and its record")
    sidecar = json.loads(sidecar_path.read_text("utf-8"))
    image = postgres_image or str(sidecar["postgres_image"])
    if _image_id(image) != sidecar["postgres_image_id"]:
        raise ImageError(
            f"{image} is not the Postgres this snapshot was built with "
            f"({sidecar['postgres_image_id'][:19]}): a data directory is only valid for the "
            "server that wrote it"
        )
    if _docker("volume", "inspect", volume, check=False, capture_output=True).returncode == 0:
        raise ImageError(f"volume {volume} already exists: attach into a new one")

    seconds: dict[str, float] = {}
    _docker("volume", "create", volume, capture_output=True)
    started = time.time()
    with path.open("rb") as source:
        result = _docker(
            "run", "--rm", "-i", "-v", f"{volume}:/data", "--entrypoint", "tar", image,
            "-C", "/data", "-xf", "-",
            stdin=source, capture_output=True,
        )  # fmt: skip
    del result
    seconds["unpack"] = time.time() - started
    note(f"unpacked {sidecar['bytes'] / 1e9:.1f} GB in {seconds['unpack']:.1f}s")

    if verify:
        name, port = f"ravel-attach-{random.randint(0, 0xFFFFFF):06x}", _free_port()
        try:
            started = time.time()
            _start(
                image,
                volume,
                name,
                port,
                str(sidecar["user"]),
                password,
                str(sidecar["database"]),
            )
            seconds["start"] = time.time() - started
            started = time.time()
            verify_ready(
                _dsn(port, str(sidecar["user"]), password, str(sidecar["database"])),
                sidecar["manifest"],
            )
            seconds["verify"] = time.time() - started
            _docker("exec", name, "psql", "-U", str(sidecar["user"]), "-c", "CHECKPOINT",
                    capture_output=True)  # fmt: skip
        finally:
            _docker("stop", "-t", "120", name, check=False, capture_output=True)
            _docker("rm", "-f", name, check=False, capture_output=True)
    return ImageReport(
        path=path, bytes=int(sidecar["bytes"]), sha256=str(sidecar["sha256"]), seconds=seconds
    )
