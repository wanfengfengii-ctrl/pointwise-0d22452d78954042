from collections.abc import Sequence

from source.catalog import SourceDirectory

from .selection import Selection


class InvalidSelection(ValueError):
    """A selection is not a strictly ordered subset of the source directory."""


def validate_selection(directory: SourceDirectory, positions: Sequence[int]) -> Selection:
    if not isinstance(positions, Sequence) or isinstance(positions, (str, bytes, bytearray)):
        raise InvalidSelection('selection must be a sequence of physical positions')
    requested = tuple(positions)
    available = frozenset(directory.positions)
    previous = -1
    for position in requested:
        if type(position) is not int or not 0 <= position < directory.slot_count:
            raise InvalidSelection(f'position out of range: {position!r}')
        if position not in available:
            raise InvalidSelection(f'position {position} is absent from the source directory')
        if position <= previous:
            raise InvalidSelection('selected positions must be strictly increasing and unique')
        previous = position
    return Selection(requested)
