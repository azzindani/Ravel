"""Native text extraction via PyMuPDF — the fast path for born-digital PDFs.

Milliseconds per page, no GPU. Correct whenever the text layer is trustworthy, which
for Indonesian regulations published as digital PDFs is most of the corpus.

Headings are inferred from typography: a line set larger than the body, or bold and
short, is a heading candidate. That is a heuristic and it is *supposed* to be —
`EXTRACTION.md` §2 routes structure-heavy documents to SmolDocling precisely because
typography alone is not reliable. This extractor also serves as the measuring stick
that says how unreliable it is on a given corpus.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from canon import BlockType, CanonicalDoc, Extraction, Source
from extract.base import BlockBuilder, ExtractionFailed, Probe, registry
from extract.quality import Thresholds, score_text
from sources import guess_mime, sha256_file

# A line must exceed the body size by this factor to be a heading on size alone.
HEADING_SIZE_RATIO = 1.12
# Headings are short. Beyond this many characters, large type is a pull quote or a
# title page, not a structural heading.
HEADING_MAX_CHARS = 120
# Repeated on this share of pages at the same vertical band = running head/foot.
RUNNING_PAGE_SHARE = 0.5


@dataclass(frozen=True, slots=True)
class Line:
    text: str
    size: float
    bold: bool
    page: int
    bbox: tuple[float, float, float, float]


def _lines(doc: Any) -> Iterator[Line]:
    """Flatten PyMuPDF's dict output into typed lines, in reading order."""
    for page_no, page in enumerate(doc, start=1):
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:  # 0 = text; images handled as assets later
                continue
            for line in block["lines"]:
                spans = [s for s in line["spans"] if s["text"].strip()]
                if not spans:
                    continue
                text = "".join(s["text"] for s in spans).strip()
                if not text:
                    continue
                sizes = [s["size"] for s in spans]
                # Bit 4 of PyMuPDF's span flags marks a bold face.
                bold = all(bool(s["flags"] & 2**4) for s in spans)
                yield Line(
                    text=text,
                    size=max(sizes),
                    bold=bold,
                    page=page_no,
                    bbox=tuple(round(v, 2) for v in line["bbox"]),  # type: ignore[arg-type]
                )


def _body_size(lines: list[Line]) -> float:
    """The modal font size — what the document's running text is set in."""
    if not lines:
        return 0.0
    rounded = [round(line.size * 2) / 2 for line in lines]
    try:
        return statistics.mode(rounded)
    except statistics.StatisticsError:
        return statistics.median(rounded)


def _running_bands(lines: list[Line], pages: int) -> set[str]:
    """Text repeated at the same vertical position across many pages.

    Detected by cross-page repetition rather than by regex: a running head is a
    *layout* fact, and a regex written for one publisher misses the next one.
    """
    if pages < 3:
        return set()
    seen: dict[str, set[int]] = {}
    for line in lines:
        key = f"{round(line.bbox[1] / 10)}|{line.text}"
        seen.setdefault(key, set()).add(line.page)
    floor = pages * RUNNING_PAGE_SHARE
    return {key for key, page_set in seen.items() if len(page_set) >= floor}


def _heading_level(size: float, body: float, levels: list[float]) -> int:
    """Map a font size onto a heading level, largest size = level 1.

    ! A heading detected by weight rather than size (bold, but set at body size)
    belongs one level below the smallest size-derived heading — not at level 9.
    Level 9 is the deepest the format allows, so putting body-size headings there
    inverts the hierarchy: every later heading would appear to nest under them.
    """
    for index, candidate in enumerate(levels, start=1):
        if abs(size - candidate) < 0.25:
            return min(index, 9)
    return min(len(levels) + 1, 9)


class NativeExtractor:
    id = "native"
    # 1.1 — heading_path was built by indexing the stack by depth, so two headings at
    # the same profile level nested instead of displacing each other. Every document
    # extracted at 1.0 carries a wrong path; the bump invalidates them deliberately.
    version = "1.1"

    def supports(self, path: Path, probe: Probe) -> bool:
        return probe.mime == "application/pdf" and probe.text_ratio >= 0.9

    def extract(self, path: Path, *, title: str, url: str | None = None) -> CanonicalDoc:
        import pymupdf

        try:
            pdf = pymupdf.open(path)
        except Exception as exc:  # noqa: BLE001 — any open failure is deterministic
            raise ExtractionFailed(f"{path.name}: cannot open · {exc}") from exc

        with pdf:
            pages = pdf.page_count
            lines = list(_lines(pdf))

        if not lines:
            # ! Do not return an empty document. A scanned PDF has no text layer and
            # belongs on the OCR path; silently emitting nothing is how a corpus
            # acquires content-free documents nobody notices (LOOPHOLES.md §2).
            raise ExtractionFailed(
                f"{path.name}: no text layer ({pages} pages) · route to OCR"
            )

        body = _body_size(lines)
        running = _running_bands(lines, pages)
        heading_floor = body * HEADING_SIZE_RATIO
        heading_sizes = sorted(
            {round(line.size * 2) / 2 for line in lines if line.size > heading_floor},
            reverse=True,
        )

        builder = BlockBuilder()
        for line in lines:
            key = f"{round(line.bbox[1] / 10)}|{line.text}"
            is_running = key in running
            top_of_page = line.bbox[1] < 100

            if is_running:
                btype = BlockType.PAGE_HEADER if top_of_page else BlockType.PAGE_FOOTER
                builder.add(btype, line.text, page=line.page, bbox=line.bbox)
                continue

            big = line.size > heading_floor
            emphatic = line.bold and len(line.text) <= HEADING_MAX_CHARS
            if (big or emphatic) and len(line.text) <= HEADING_MAX_CHARS:
                builder.add(
                    BlockType.HEADING,
                    line.text,
                    level=_heading_level(line.size, body, heading_sizes),
                    page=line.page,
                    bbox=line.bbox,
                    attrs={"size": line.size, "bold": line.bold},
                )
            else:
                builder.add(
                    BlockType.PARAGRAPH,
                    line.text,
                    page=line.page,
                    bbox=line.bbox,
                    attrs={"size": line.size, "bold": line.bold},
                )

        digest = sha256_file(path)
        return CanonicalDoc(
            doc_id=f"sha256:{digest}",
            source=Source(
                path=path.as_posix(),
                url=url,
                title=title,
                mime=guess_mime(path),
                bytes=path.stat().st_size,
                sha256=digest,
            ),
            extraction=Extraction(
                extractor=self.id,
                extractor_version=self.version,
                params={
                    "heading_size_ratio": HEADING_SIZE_RATIO,
                    "body_size": body,
                    "heading_sizes": heading_sizes[:6],
                },
                extracted_at=datetime.now(UTC),
                pages=pages,
            ),
            blocks=builder.blocks,
        )


def probe(path: Path, *, thresholds: Thresholds | None = None) -> Probe:
    """Cheap look: does this PDF have a usable text layer, and is it worth using?

    (`EXTRACTION.md` §2.) Returns coverage *and* a quality verdict — see
    `extract.quality` for why the second one is not optional.
    """
    import pymupdf

    mime = guess_mime(path)
    if mime != "application/pdf":
        return Probe(mime=mime, pages=None, text_ratio=0.0, chars=0)

    try:
        pdf = pymupdf.open(path)
    except Exception:  # noqa: BLE001
        return Probe(mime=mime, pages=None, text_ratio=0.0, chars=0)

    with pdf:
        pages = pdf.page_count
        if not pages:
            return Probe(mime=mime, pages=0, text_ratio=0.0, chars=0)
        parts: list[str] = []
        with_text = 0
        for page in pdf:
            text = page.get_text("text").strip()
            parts.append(text)
            # A handful of stray characters is OCR bleed, not a text layer.
            with_text += len(text) > 32

    # ! Scored on the joined text, not per page. Space loss and control characters are
    # properties of a document's extraction, and a single clean page does not redeem
    # the rest — `ABSORPTION.md` §16 measured the classes this way.
    joined = "\n".join(parts)
    return Probe(
        mime=mime,
        pages=pages,
        text_ratio=with_text / pages,
        chars=len(joined.strip()),
        quality=score_text(joined, pages=pages, thresholds=thresholds),
    )


registry.register(NativeExtractor())
