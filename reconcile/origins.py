from media.record import WrittenRecord
from plan.selection import Selection
from source.catalog import SourceDirectory


class OriginMismatch(ValueError):
    """The actual write sequence disagrees with the planned physical origins."""


def verify_origins(
    directory: SourceDirectory, selection: Selection, records: tuple[WrittenRecord, ...]
) -> None:
    expected = selection.origins(directory)
    if len(records) != len(expected):
        raise OriginMismatch('wrong number of written records')
    for index, (record, origin) in enumerate(zip(records, expected)):
        if type(record.target_index) is not int or record.target_index != index:
            raise OriginMismatch(f'nonconsecutive target index at record {index}')
        actual = (record.source_volume_id, record.source_position)
        if type(record.source_position) is not int or actual != origin:
            raise OriginMismatch(f'record {index} has origin {actual!r}, expected {origin!r}')
