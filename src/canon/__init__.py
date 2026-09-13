"""The canonical document: the intermediate representation every stage speaks."""

from canon.models import (
    CANON_VERSION,
    Asset,
    Block,
    BlockType,
    CanonicalDoc,
    Cell,
    Extraction,
    Source,
    Table,
)
from canon.render import parse_blocks, render_block, render_blocks, render_table
from canon.store import iter_shard, read_shard, write_shard
from canon.validate import Issue, Severity, is_valid, validate

__all__ = [
    "CANON_VERSION", "Asset", "Block", "BlockType", "CanonicalDoc", "Cell",
    "Extraction", "Source", "Table", "Issue", "Severity", "is_valid", "validate",
    "parse_blocks", "render_block", "render_blocks", "render_table",
    "iter_shard", "read_shard", "write_shard",
]
