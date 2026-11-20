from collections.abc import Sequence

from .catalog import SourceCatalog, SourceDirectory
from .layout import RecordLayout


def register_source(
    catalog: SourceCatalog,
    volume_id: str,
    width: int,
    slot_count: int,
    positions: Sequence[int],
    *,
    version: int = 1,
) -> str:
    directory = SourceDirectory(volume_id, RecordLayout(width, version), slot_count, positions)
    return catalog.register(directory)
