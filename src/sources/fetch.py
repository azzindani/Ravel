"""Fetch a declared publisher bulk listing into `sources.root` (`SOURCE_US_CFR.md` §2).

    listing -> wanted files -> missing? -> download to .part -> check -> rename
            -> fetch.jsonl (append)  -> snapshot list (only when nothing is missing)

! Not a crawler, and that is the line `CLAUDE.md` §7.9 draws. Every file fetched is one a
machine-readable listing names, saved at its path below `fetch.files`, which the corpus
loader holds equal to `provenance.base_url`: a fetched file's citation is the URL it was
read from, by construction.

! Never overwrites. A file already present at the listed size is kept; one at another size
is a conflict, reported and left alone. Which issue of a re-published file a corpus cites
is the operator's decision, not a side effect of an update run.

Recurrent by design: rerun it and it fetches what was published since, then rewrites the
snapshot. Old editions stay on disk, because sources are never deleted.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from sources.scan import sha256_file
from spec.corpus import CorpusSpec, FetchSpec

MANIFEST = "fetch.jsonl"
READ_BLOCK = 1 << 20

#: `(url, headers) -> binary stream`. The seam the tests replace; nothing else does I/O.
Opener = Callable[[str, dict[str, str], float], BinaryIO]


@dataclass(frozen=True, slots=True)
class Wanted:
    """One file the listing names: where it goes, where it comes from, what to expect."""

    path: str
    url: str
    size: int | None = None
    group: str = ""
    """What the file belongs to (`title-2`), for the report."""


@dataclass
class FetchReport:
    corpus: str
    wanted: int = 0
    present: int = 0
    downloaded: int = 0
    bytes: int = 0
    conflicts: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    editions: dict[str, str] = field(default_factory=dict)
    """Group -> the edition taken (`title-2 -> 2026`)."""
    held: dict[str, str] = field(default_factory=dict)
    """Group -> why a newer edition was not taken (published only in part)."""
    snapshot: str | None = None
    seconds: float = 0.0

    @property
    def complete(self) -> bool:
        return not self.failed


class _Gunzip(gzip.GzipFile):
    """A gzip stream that also closes the response under it."""

    def __init__(self, response: Any) -> None:
        super().__init__(fileobj=response)
        self._response = response

    def close(self) -> None:
        try:
            super().close()
        finally:
            self._response.close()


def _open(url: str, headers: dict[str, str], timeout: float) -> BinaryIO:
    request = urllib.request.Request(url, headers=headers)
    response = urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 — declared listing
    if response.headers.get("Content-Encoding", "").lower() == "gzip":
        return _Gunzip(response)  # type: ignore[return-value]
    return response  # type: ignore[no-any-return]


class Fetcher:
    """One corpus's `fetch:` block, run against its `sources.root`."""

    def __init__(
        self,
        corpus: CorpusSpec,
        *,
        opener: Opener = _open,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if corpus.fetch is None:
            raise ValueError(f"corpus {corpus.id!r} declares no fetch: block")
        self.corpus = corpus
        self.spec: FetchSpec = corpus.fetch
        self.root = Path(corpus.sources.root)
        self._opener = opener
        self._sleep = sleep
        self.held: dict[str, str] = {}
        """Group -> why its newest edition was not taken."""

    # -- http ----------------------------------------------------------------
    def _headers(self, *, compressed: bool, accept: str = "*/*") -> dict[str, str]:
        # ! govinfo answers a bulk listing with HTML unless JSON is asked for by name.
        headers = {"User-Agent": self.spec.user_agent, "Accept": accept}
        if compressed:
            # ! The eCFR versioner refuses an uncompressed request outright (support code 11).
            headers["Accept-Encoding"] = "gzip"
        return headers

    def _retrying(
        self,
        url: str,
        action: Callable[[BinaryIO], Any],
        *,
        compressed: bool,
        accept: str = "*/*",
    ) -> Any:
        attempt = 0
        while True:
            try:
                headers = self._headers(compressed=compressed, accept=accept)
                with self._opener(url, headers, self.spec.timeout_s) as stream:
                    return action(stream)
            except urllib.error.HTTPError as exc:
                # A 404 is an answer, not a transient: retrying it only delays the report.
                if (exc.code < 500 and exc.code != 429) or attempt >= self.spec.retries:
                    raise
            except urllib.error.URLError, TimeoutError, ConnectionError, OSError:
                if attempt >= self.spec.retries:
                    raise
            attempt += 1
            self._sleep(min(60.0, 2.0**attempt * max(self.spec.delay_s, 0.5)))

    def get_json(self, url: str) -> Any:
        def parse(stream: BinaryIO) -> Any:
            return json.loads(stream.read().decode("utf-8"))

        return self._retrying(
            url, parse, compressed=self.spec.kind == "ecfr", accept="application/json"
        )

    # -- planning ------------------------------------------------------------
    def plan(self) -> tuple[list[Wanted], dict[str, str]]:
        """Every file the snapshot needs, and the edition each group came from."""
        if self.spec.kind == "govinfo_cfr":
            return self._plan_govinfo()
        return self._plan_ecfr()

    def _relative(self, url: str) -> str:
        prefix = self.spec.files.rstrip("/") + "/"
        if not url.startswith(prefix):
            # ! A listed file outside the provenance root would be cited at a URL it was
            # not read from. Refused rather than rewritten.
            raise ValueError(f"listed file {url} is not under fetch.files {prefix}")
        relative = url[len(prefix) :]
        if ".." in Path(relative).parts or relative.startswith("/"):
            raise ValueError(f"listed file {url} escapes the sources root")
        return relative

    def _plan_govinfo(self) -> tuple[list[Wanted], dict[str, str]]:
        root = self.get_json(self.spec.listing)
        years = sorted(
            (f for f in root.get("files", []) if f.get("folder") and str(f["name"]).isdigit()),
            key=lambda f: int(f["name"]),
            reverse=True,
        )
        if self.spec.edition != "latest":
            years = [f for f in years if f["name"] == self.spec.edition]
            if not years:
                raise ValueError(f"no CFR edition {self.spec.edition!r} in {self.spec.listing}")
        # Every edition of every title within the lookback, newest first.
        editions: dict[int, list[tuple[str, list[dict[str, Any]]]]] = {}
        for edition in years[: self.spec.lookback]:
            listing = self.get_json(edition["link"])
            for folder in listing.get("files", []):
                match = re.fullmatch(r"title-(\d+)", str(folder.get("name", "")))
                if not (folder.get("folder") and match):
                    continue
                title = int(match.group(1))
                if self.spec.titles and title not in self.spec.titles:
                    continue
                volumes = [
                    f
                    for f in self.get_json(folder["link"]).get("files", [])
                    if not f.get("folder") and str(f.get("name", "")).lower().endswith(".xml")
                ]
                if volumes:
                    editions.setdefault(title, []).append((str(edition["name"]), volumes))

        wanted: list[Wanted] = []
        taken: dict[str, str] = {}
        for title, found in sorted(editions.items()):
            group = f"title-{title}"
            chosen = found[-1]
            for index, (year, volumes) in enumerate(found):
                older = found[index + 1] if index + 1 < len(found) else None
                # ! An edition is published volume by volume over months: in Nov 2026 the
                # 2026 title 7 had 12 of its 14 volumes. Taking it would drop two volumes of
                # law from a corpus that looks complete, so a newest edition missing a volume
                # the one before it had is held back, and the hold is reported.
                missing = _missing_volumes(volumes, older[1]) if older else []
                if not missing:
                    chosen = (year, volumes)
                    break
                assert older is not None  # `missing` is empty without one
                self.held[group] = (
                    f"{year} lacks vol {', '.join(missing)} "
                    f"({len(volumes)} files, {len(older[1])} before); using {older[0]}"
                )
            year, volumes = chosen
            taken[group] = year
            for volume in sorted(volumes, key=lambda f: str(f["name"])):
                url = str(volume["link"])
                size = volume.get("size")
                wanted.append(
                    Wanted(
                        path=self._relative(url),
                        url=url,
                        size=int(size) if size is not None else None,
                        group=group,
                    )
                )
        return wanted, taken

    def _plan_ecfr(self) -> tuple[list[Wanted], dict[str, str]]:
        listing = self.get_json(self.spec.listing)
        wanted: list[Wanted] = []
        taken: dict[str, str] = {}
        for title in listing.get("titles", []):
            number = int(title["number"])
            if title.get("reserved") or (self.spec.titles and number not in self.spec.titles):
                continue
            # The title's own currency: a date past it is refused by the versioner.
            date = (
                str(title["up_to_date_as_of"])
                if self.spec.edition == "latest"
                else self.spec.edition
            )
            group = f"title-{number}"
            # ! The versioner's documented path, `full/{date}/title-{n}.xml`. Constructed,
            # unlike govinfo's, because the versioner is an API and not a file listing; the
            # date and the title both come from `titles.json`.
            url = f"{self.spec.files.rstrip('/')}/{date}/title-{number}.xml"
            wanted.append(Wanted(path=self._relative(url), url=url, group=group))
            taken[group] = date
        return wanted, taken

    # -- the run --------------------------------------------------------------
    def run(
        self,
        *,
        dry_run: bool = False,
        on_progress: Callable[[FetchReport, Wanted], None] | None = None,
    ) -> FetchReport:
        started = time.time()
        report = FetchReport(corpus=self.corpus.id)
        wanted, report.editions = self.plan()
        report.held = dict(self.held)
        report.wanted = len(wanted)
        known = self._recorded()

        for item in wanted:
            target = self.root / item.path
            if target.is_file():
                if item.size is not None and target.stat().st_size != item.size:
                    report.conflicts.append(
                        f"{item.path}: {target.stat().st_size:,} bytes on disk, "
                        f"{item.size:,} listed"
                    )
                elif item.path not in known and not dry_run:
                    self._record(item, target, sha256_file(target), note="present")
                report.present += 1
                continue
            if dry_run:
                continue
            try:
                digest, size = self._download(item, target)
            except Exception as exc:  # noqa: BLE001 — one file must not end the run
                report.failed.append((item.path, f"{type(exc).__name__}: {exc}"))
                continue
            self._record(item, target, digest)
            report.downloaded += 1
            report.bytes += size
            if on_progress:
                on_progress(report, item)
            self._sleep(self.spec.delay_s)

        # ! Written only when the snapshot is whole. A list naming files that were never
        # fetched would make the next extraction a partial corpus that looks complete.
        if report.complete and not dry_run and self.corpus.sources.snapshot:
            snapshot = self.root / self.corpus.sources.snapshot
            lines = [
                f"# {self.corpus.id} · {datetime.now(UTC).isoformat(timespec='seconds')}",
                *(f"# {group} {edition}" for group, edition in sorted(report.editions.items())),
                *sorted(w.path for w in wanted),
            ]
            temporary = snapshot.with_suffix(snapshot.suffix + ".part")
            temporary.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
            os.replace(temporary, snapshot)
            report.snapshot = str(snapshot)
        report.seconds = time.time() - started
        return report

    def _download(self, item: Wanted, target: Path) -> tuple[str, int]:
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".part")

        def write(stream: BinaryIO) -> tuple[str, int]:
            digest = hashlib.sha256()
            size = 0
            with open(partial, "wb") as handle:
                while block := stream.read(READ_BLOCK):
                    handle.write(block)
                    digest.update(block)
                    size += len(block)
            return digest.hexdigest(), size

        try:
            digest, size = self._retrying(item.url, write, compressed=self.spec.kind == "ecfr")
            if item.size is not None and size != item.size:
                raise OSError(f"got {size:,} bytes, listing says {item.size:,}")
            if size == 0:
                raise OSError("empty response")
            os.replace(partial, target)
        finally:
            partial.unlink(missing_ok=True)
        return digest, size

    # -- manifest ---------------------------------------------------------------
    @property
    def manifest_path(self) -> Path:
        return self.root / MANIFEST

    def _recorded(self) -> set[str]:
        if not self.manifest_path.is_file():
            return set()
        with open(self.manifest_path, encoding="utf-8") as handle:
            return {json.loads(line)["path"] for line in handle if line.strip()}

    def _record(self, item: Wanted, target: Path, digest: str, *, note: str = "") -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        entry = {
            "path": item.path,
            "url": item.url,
            "bytes": target.stat().st_size,
            "sha256": digest,
            "group": item.group,
            "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        if note:
            entry["note"] = note
        with open(self.manifest_path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


_VOLUME = re.compile(r"vol(\d+)\.xml$", re.IGNORECASE)


def _missing_volumes(newer: list[dict[str, Any]], older: list[dict[str, Any]]) -> list[str]:
    """Volume numbers the older edition has and the newer one does not.

    ! By number, not by count. govinfo's 2026 title 14 lists `vol0`, a byte-for-byte copy of
    `vol3`, and no `vol1`: five files against five, so a count passed an edition missing a
    volume. A name without a number falls back to the count.
    """

    def numbers(files: list[dict[str, Any]]) -> set[int] | None:
        found = [_VOLUME.search(str(f.get("name", ""))) for f in files]
        return None if not all(found) else {int(m.group(1)) for m in found if m}

    new, old = numbers(newer), numbers(older)
    if new is None or old is None:
        return [] if len(newer) >= len(older) else [f"{len(older) - len(newer)} unnamed"]
    return [str(n) for n in sorted(old - new)]


__all__ = ["FetchReport", "Fetcher", "Wanted"]
