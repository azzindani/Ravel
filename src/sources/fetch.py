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

import csv
import fnmatch
import gzip
import hashlib
import json
import os
import re
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from sources.scan import sha256_file
from spec.corpus import CorpusSpec, FetchSpec

MANIFEST = "fetch.jsonl"
READ_BLOCK = 1 << 20
NL = "\n"

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
    unpacked: int = 0
    """`archive_index`: members unpacked from the archive."""
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
        if self.spec.kind == "github":
            return self._plan_github()
        if self.spec.kind == "archive_index":
            return self._plan_archive_index()
        if self.spec.kind == "archive_url":
            return self._plan_archive_url()
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

    def _plan_github(self) -> tuple[list[Wanted], dict[str, str]]:
        """Every file of `paths` in the repository's tree at one commit.

        ! Pinned to a commit, never to a branch: a branch moves, and a file cited at
        `main/eng/acts/A-1.xml` would later resolve to text it was not read from. The commit
        is the edition, its sha is the first part of every path, and an older commit's
        files stay on disk beside the newer one's.
        """
        api = self.spec.listing.rstrip("/")
        sha = self.spec.edition
        if sha == "latest":
            branch = self.get_json(api)["default_branch"]
            sha = str(self.get_json(f"{api}/commits/{branch}")["sha"])
        tree = self.get_json(f"{api}/git/trees/{sha}?recursive=1")
        if tree.get("truncated"):
            # A truncated listing is a partial corpus that looks complete.
            raise ValueError(f"{api}: the tree at {sha} is truncated; list it by directory")
        prefix = self.spec.files.rstrip("/") + "/"
        wanted: list[Wanted] = []
        for entry in tree.get("tree", []):
            path = str(entry.get("path", ""))
            if entry.get("type") != "blob" or not any(
                fnmatch.fnmatchcase(path, glob) for glob in self.spec.paths
            ):
                continue
            if ".." in Path(path).parts or path.startswith("/"):
                raise ValueError(f"listed file {path} escapes the sources root")
            relative = f"{sha}/{path}"
            size = entry.get("size")
            wanted.append(
                Wanted(
                    path=relative,
                    url=prefix + urllib.parse.quote(relative, safe="/,()"),
                    size=int(size) if size is not None else None,
                    group="/".join(path.split("/")[:-1]),
                )
            )
        groups = {w.group for w in wanted}
        return sorted(wanted, key=lambda w: w.path), {g: sha for g in sorted(groups)}

    def _plan_archive_index(self) -> tuple[list[Wanted], dict[str, str]]:
        """The one archive of the edition: the newest the index lists, or the one named.

        ! One archive, never a mix: a full dump and the increments published after it are
        separate editions, and a corpus is built from one dump as it stands.
        """
        assert self.spec.archive is not None  # the corpus loader holds it present
        listing = self.spec.listing.rstrip("/") + "/"
        html = self._retrying(
            listing, lambda s: s.read().decode("utf-8", "replace"), compressed=False
        )
        pattern = re.compile(self.spec.archive)
        found: dict[str, str] = {}
        for href in re.findall(r'href="([^"?#]+)"', html):
            name = urllib.parse.unquote(href.rsplit("/", 1)[-1])
            match = pattern.fullmatch(name)
            if match:
                found[match.group("edition")] = name
        if not found:
            raise ValueError(f"{listing}: no archive matches {self.spec.archive!r}")
        edition = max(found) if self.spec.edition == "latest" else self.spec.edition
        if edition not in found:
            raise ValueError(f"{listing}: no archive of edition {edition!r}")
        name = found[edition]
        url = self.spec.files.rstrip("/") + "/" + urllib.parse.quote(name)
        return [Wanted(path=self._relative(url), url=url, group="archive")], {
            "archive": edition
        }

    def _plan_archive_url(self) -> tuple[list[Wanted], dict[str, str]]:
        """The archive one URL serves, dated by the day it was fetched.

        ! The URL always serves the publisher's current consolidation and the archive carries
        no date of its own, so the edition is the fetch day (UTC), and a pinned
        `edition: <yyyymmdd>` names one already on disk. A second fetch on the same day is
        the same edition and downloads nothing.
        """
        assert self.spec.archive is not None  # the corpus loader holds it present
        suffix = self.corpus.provenance.archive_suffix or ""
        edition = (
            datetime.now(UTC).strftime("%Y%m%d")
            if self.spec.edition == "latest"
            else self.spec.edition
        )
        name = f"{self.spec.archive}_{edition}{suffix}"
        return [Wanted(path=name, url=self.spec.listing, group="archive")], {"archive": edition}

    def _members(self, path: Path) -> Iterator[tuple[str, int, Callable[[], BinaryIO]]]:
        """`(name, size, open)` of every regular file in a tar or zip archive."""
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as zipped:
                for info in zipped.infolist():
                    if not info.is_dir():
                        yield info.filename, info.file_size, lambda i=info: zipped.open(i)
            return
        with tarfile.open(path, "r:*") as tarred:
            for member in tarred:
                if member.isfile():
                    source = tarred.extractfile(member)
                    assert source is not None  # a regular file
                    yield member.name, member.size, lambda s=source: s

    def _indexed(self, folder: str, members: list[str]) -> list[str]:
        """The documents the publisher's index keeps; their URLs go to the url map.

        ! Every kept row must name a member that was unpacked. A row pointing at nothing
        means the index and the archive disagree, and the snapshot would quietly lose a
        document, so the fetch stops.
        """
        spec = self.spec.index
        assert spec is not None
        index = self.root / folder / spec.file
        if not index.is_file():
            raise ValueError(f"{folder}: the archive holds no index {spec.file}")
        present = set(members)
        pattern = re.compile(spec.member)
        kept: dict[str, str] = {}
        with open(index, encoding=spec.encoding, newline="") as handle:
            for row in csv.DictReader(handle):
                if any((row.get(k) or "").strip() != v for k, v in spec.where.items()):
                    continue
                url = (row.get(spec.url) or "").strip()
                match = pattern.search(url)
                if match is None:
                    raise ValueError(f"{spec.file}: {url!r} does not match {spec.member}")
                relative = f"{folder}/{spec.document.format(**match.groupdict())}"
                if relative not in present:
                    raise ValueError(f"{spec.file}: {url} names {relative}, not in the archive")
                kept[relative] = url
        if not kept:
            raise ValueError(f"{spec.file}: no row passes {spec.where}")
        assert self.corpus.provenance.url_map is not None  # the corpus loader holds it
        target = self.root / self.corpus.provenance.url_map
        known: dict[str, str] = {}
        if target.is_file():
            with open(target, encoding="utf-8") as handle:
                known = {r["path"]: r["url"] for r in map(json.loads, handle) if r}
        known.update(kept)
        temporary = target.with_suffix(target.suffix + ".part")
        with open(temporary, "w", encoding="utf-8", newline=NL) as handle:
            for path, url in sorted(known.items()):
                handle.write(json.dumps({"path": path, "url": url}, ensure_ascii=False) + NL)
        os.replace(temporary, target)
        return sorted(kept)

    def _unpack(self, item: Wanted) -> list[str]:
        """The archive's wanted members, unpacked beside it into a folder of its name.

        Each member goes in `fetch.jsonl`, cited as `<archive url>#<member>`. Unpacked once:
        the folder's `.members` list, written last, is what says it is whole.
        """
        suffix = self.corpus.provenance.archive_suffix or ""
        if not item.path.endswith(suffix):
            raise ValueError(f"{item.path}: not a {suffix} archive")
        folder = item.path[: len(item.path) - len(suffix)]
        done = self.root / folder / ".members"
        if done.is_file():
            return done.read_text(encoding="utf-8").split("\n")[:-1]
        members: list[str] = []
        with open(self.manifest_path, "a", encoding="utf-8", newline=NL) as manifest:
            for raw, size, open_member in self._members(self.root / item.path):
                name = raw.lstrip("./")
                if not any(fnmatch.fnmatchcase(name, glob) for glob in self.spec.paths):
                    continue
                if ".." in Path(name).parts or Path(name).is_absolute():
                    raise ValueError(f"{item.path}: member {name} escapes the archive")
                relative = f"{folder}/{name}"
                target = self.root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                source = open_member()
                digest = hashlib.sha256()
                with open(target, "wb") as handle:
                    while block := source.read(READ_BLOCK):
                        handle.write(block)
                        digest.update(block)
                entry = {
                    "path": relative,
                    "url": f"{item.url}#{name}",
                    "bytes": size,
                    "sha256": digest.hexdigest(),
                    "group": item.path,
                    "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
                }
                manifest.write(json.dumps(entry, ensure_ascii=False) + "\n")
                members.append(relative)
        if not members:
            # ! An archive whose members the globs never match is a corpus of nothing, and
            # a wrong glob, not an empty edition.
            raise ValueError(f"{item.path}: no member matches fetch.paths {self.spec.paths}")
        members.sort()
        done.write_text("".join(m + "\n" for m in members), encoding="utf-8", newline="\n")
        return members

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

        missing: list[Wanted] = []
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
            if not dry_run:
                missing.append(item)

        def fetch_one(item: Wanted) -> tuple[str, int]:
            try:
                return self._download(item, self.root / item.path)
            finally:
                self._sleep(self.spec.delay_s)

        # ! Downloads run `concurrency` at a time; the manifest and the report are written
        # here, in the order results arrive, by this thread alone.
        with ThreadPoolExecutor(max_workers=self.spec.concurrency) as pool:
            futures = {pool.submit(fetch_one, item): item for item in missing}
            for future in as_completed(futures):
                item = futures[future]
                try:
                    digest, size = future.result()
                except Exception as exc:  # noqa: BLE001 — one file must not end the run
                    report.failed.append((item.path, f"{type(exc).__name__}: {exc}"))
                    continue
                self._record(item, self.root / item.path, digest)
                report.downloaded += 1
                report.bytes += size
                if on_progress:
                    on_progress(report, item)

        listed = sorted(w.path for w in wanted)
        archived = self.spec.kind in ("archive_index", "archive_url")
        if archived and report.complete and not dry_run and self.spec.index:
            folders = [(w, self._unpack(w)) for w in wanted]
            report.unpacked = sum(len(m) for _, m in folders)
            suffix = self.corpus.provenance.archive_suffix or ""
            listed = [
                d
                for w, members in folders
                for d in self._indexed(w.path[: len(w.path) - len(suffix)], members)
            ]
        elif archived and report.complete and not dry_run:
            members = [m for w in wanted for m in self._unpack(w)]
            report.unpacked = len(members)
            listed = sorted(
                m
                for m in members
                if not self.spec.documents
                or any(fnmatch.fnmatchcase(m, f"*/{glob}") for glob in self.spec.documents)
            )

        # ! Written only when the snapshot is whole. A list naming files that were never
        # fetched would make the next extraction a partial corpus that looks complete.
        if report.complete and not dry_run and self.corpus.sources.snapshot:
            snapshot = self.root / self.corpus.sources.snapshot
            lines = [
                f"# {self.corpus.id} · {datetime.now(UTC).isoformat(timespec='seconds')}",
                *(f"# {group} {edition}" for group, edition in sorted(report.editions.items())),
                *listed,
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
