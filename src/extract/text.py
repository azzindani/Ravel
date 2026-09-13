"""Text-shaped sources: HTML, Markdown, and plain text.

The data is not only PDFs. A scraped article, a Markdown export and a plain
transcript all become the same canonical document, and the same profile then finds
the same structure in them — because a profile describes a **kind of document**, not
a file format.

! No lxml, no BeautifulSoup. The HTML here is a tag-shaped *text* source, not a
document to render: extracting headings and paragraphs needs the tags, not a DOM.
Python's own `HTMLParser` handles that in a few dozen lines with no dependency, and a
real parser can be dropped in behind the same interface if a corpus ever demands it.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

from canon import BlockType, CanonicalDoc, Extraction, Source
from extract.base import BlockBuilder, ExtractionFailed, Probe, registry
from extract.quality import Thresholds, score_text
from sources import guess_mime, sha256_file

#: Tags whose content never belongs in a corpus.
DROP_TAGS = frozenset({"script", "style", "noscript", "template", "svg", "head"})
#: Tags that end a block of running text.
BLOCK_TAGS = frozenset(
    {"p", "div", "section", "article", "br", "tr", "td", "th", "blockquote", "pre"}
)
HEADINGS = {f"h{n}": n for n in range(1, 7)}
LIST_TAGS = frozenset({"li", "dd", "dt"})

MD_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
MD_LIST = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")
MD_SETEXT = re.compile(r"^(=+|-+)\s*$")


class _Harvester(HTMLParser):
    """Collects (kind, level, text) in document order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[tuple[str, int | None, str]] = []
        self._buffer: list[str] = []
        self._skip = 0
        self._heading: int | None = None
        self._list = False

    # -- buffer -------------------------------------------------------------
    def _flush(self) -> None:
        text = normalize(" ".join(self._buffer))
        self._buffer.clear()
        if not text:
            self._heading, self._list = None, False
            return
        if self._heading is not None:
            self.out.append(("heading", self._heading, text))
        elif self._list:
            self.out.append(("list_item", None, text))
        else:
            self.out.append(("paragraph", None, text))
        self._heading, self._list = None, False

    # -- parser hooks -------------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in DROP_TAGS:
            self._skip += 1
            return
        if self._skip:
            return
        if tag in HEADINGS:
            self._flush()
            self._heading = HEADINGS[tag]
        elif tag in LIST_TAGS:
            self._flush()
            self._list = True
        elif tag in BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in DROP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag in HEADINGS or tag in LIST_TAGS or tag in BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if not self._skip and data.strip():
            self._buffer.append(data)

    def close(self) -> None:  # noqa: D102
        super().close()
        self._flush()


def normalize(text: str) -> str:
    """Collapse whitespace and repair the usual transport damage.

    NFKC folds the ligatures and full-width characters that scraped pages carry;
    the zero-width and soft-hyphen strip removes characters that are invisible on
    screen but split words for a tokenizer — which is how `Undang-Undang` becomes
    two unrelated terms.
    """
    text = unescape(text)
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("­", "").replace("​", "").replace("﻿", "")
    return re.sub(r"[ \t ]+", " ", text).strip()


def _html_blocks(raw: str, builder: BlockBuilder) -> None:
    harvester = _Harvester()
    harvester.feed(raw)
    harvester.close()
    for kind, level, text in harvester.out:
        if kind == "heading":
            builder.add(BlockType.HEADING, text, level=level)
        elif kind == "list_item":
            builder.add(BlockType.LIST_ITEM, text)
        else:
            builder.add(BlockType.PARAGRAPH, text)


def _markdown_blocks(raw: str, builder: BlockBuilder) -> None:
    lines = raw.splitlines()
    fenced = False
    for index, line in enumerate(lines):
        if line.strip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            if stripped := line.rstrip():
                builder.add(BlockType.PARAGRAPH, stripped)
            continue

        text = normalize(line)
        if not text:
            continue
        # A row of = or - underlines the previous line: that line was a heading.
        if MD_SETEXT.match(line.strip()) and builder.blocks and index:
            previous = builder.blocks[-1]
            if previous.type is BlockType.PARAGRAPH:
                builder.blocks.pop()
                builder.add(BlockType.HEADING, previous.text, level=1 if "=" in line else 2)
            continue
        if m := MD_HEADING.match(text):
            builder.add(BlockType.HEADING, normalize(m.group(2)), level=len(m.group(1)))
        elif m := MD_LIST.match(text):
            builder.add(BlockType.LIST_ITEM, normalize(m.group(1)))
        else:
            builder.add(BlockType.PARAGRAPH, text)


def _plain_blocks(raw: str, builder: BlockBuilder) -> None:
    for line in raw.splitlines():
        if text := normalize(line):
            builder.add(BlockType.PARAGRAPH, text)


class TextExtractor:
    """HTML, Markdown and plain text to canonical documents. No GPU, no model."""

    id = "text"
    # 1.1 — heading_path was built by indexing the stack by depth, so two headings at
    # the same profile level nested instead of displacing each other. Every document
    # extracted at 1.0 carries a wrong path; the bump invalidates them deliberately.
    version = "1.1"

    SUPPORTED = {
        "text/html": _html_blocks,
        "application/xhtml+xml": _html_blocks,
        "text/markdown": _markdown_blocks,
        "text/x-markdown": _markdown_blocks,
        "text/plain": _plain_blocks,
    }

    def supports(self, path: Path, probe: Probe) -> bool:
        return probe.mime in self.SUPPORTED

    def extract(self, path: Path, *, title: str, url: str | None = None) -> CanonicalDoc:
        mime = guess_mime(path)
        parse = self.SUPPORTED.get(mime)
        if parse is None:
            raise ExtractionFailed(f"{path.name}: {mime} is not a text source")

        raw = path.read_text(encoding="utf-8", errors="replace")
        builder = BlockBuilder()
        parse(raw, builder)

        if not builder.blocks:
            # ! An empty success is worse than a crash: it enters the corpus as a
            # real document with no content (LOOPHOLES.md §2).
            raise ExtractionFailed(f"{path.name}: produced no text ({len(raw)} bytes read)")

        digest = sha256_file(path)
        return CanonicalDoc(
            doc_id=f"sha256:{digest}",
            source=Source(
                path=path.as_posix(),
                url=url,
                title=title,
                mime=mime,
                bytes=path.stat().st_size,
                sha256=digest,
            ),
            extraction=Extraction(
                extractor=self.id,
                extractor_version=self.version,
                params={"mime": mime, "normalize": "NFKC"},
                extracted_at=datetime.now(UTC),
            ),
            blocks=builder.blocks,
        )


def probe_text(path: Path, *, thresholds: Thresholds | None = None) -> Probe:
    mime = guess_mime(path)
    try:
        # ! `errors="replace"` makes the U+FFFD signal in `score_text` do double duty
        # here: every replacement character is a byte this file claimed was UTF-8 and
        # was not. That is encoding damage, and it is the same verdict either way —
        # the text is present and untrustworthy.
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return Probe(mime=mime, pages=None, text_ratio=0.0, chars=0)
    return Probe(
        mime=mime,
        pages=None,
        text_ratio=1.0 if raw.strip() else 0.0,
        chars=len(raw),
        quality=score_text(raw, thresholds=thresholds),
    )


registry.register(TextExtractor())
