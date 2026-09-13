"""Phase A: source files to canonical documents.

An extractor reports **what is on the page**; a structurer decides **what it means**
(`docs/EXTRACTION.md` §4b). Keeping those apart is what lets one corpus profile serve
a PDF, a scraped HTML page and a Markdown export without re-extraction.
"""

from extract.base import BlockBuilder, ExtractionFailed, Extractor, Probe, Registry, registry
from extract.native import NativeExtractor, probe
from extract.quality import TextLayer, TextQuality, Thresholds, score_text
from extract.run import Extraction, RunReport, iter_canon, probe_any
from extract.structure import Structurer, is_centered
from extract.text import TextExtractor, normalize, probe_text

__all__ = [
    "BlockBuilder",
    "Extraction",
    "ExtractionFailed",
    "Extractor",
    "NativeExtractor",
    "Probe",
    "RunReport",
    "TextLayer",
    "TextQuality",
    "Thresholds",
    "iter_canon",
    "probe_any",
    "Registry",
    "Structurer",
    "TextExtractor",
    "is_centered",
    "normalize",
    "probe",
    "probe_text",
    "registry",
    "score_text",
]
