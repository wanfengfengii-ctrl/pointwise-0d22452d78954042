from collections.abc import Sequence
from dataclasses import dataclass

from .layout import RecordLayout, validate_layout


class SourceConflict(ValueError):
    """A source number already names a different directory."""


class UnknownSource(LookupError):
    """A source number has not been registered in this session."""


@dataclass(frozen=True, slots=True, init=False)
class SourceDirectory:
    volume_id: str
    layout: RecordLayout
    slot_count: int
    positions: tuple[int, ...]

    def __init__(
        self, volume_id: str, layout: RecordLayout, slot_count: int, positions: Sequence[int]
    ) -> None:
        if not isinstance(volume_id, str) or not volume_id.strip():
            raise ValueError('source volume number must be a nonblank string')
        validate_layout(layout, slot_count)
        if not isinstance(positions, Sequence) or isinstance(positions, (str, bytes, bytearray)):
            raise ValueError('source directory positions must be a sequence of integers')
        fixed_positions = tuple(positions)
        previous = -1
        for position in fixed_positions:
            if type(position) is not int or not 0 <= position < slot_count:
                raise ValueError(f'directory position out of range: {position!r}')
            if position <= previous:
                raise ValueError('source directory positions must be strictly increasing')
            previous = position
        object.__setattr__(self, 'volume_id', volume_id)
        object.__setattr__(self, 'layout', layout)
        object.__setattr__(self, 'slot_count', slot_count)
        object.__setattr__(self, 'positions', fixed_positions)


class SourceCatalog:
    def __init__(self) -> None:
        self._directories: dict[str, SourceDirectory] = {}

    def register(self, directory: SourceDirectory) -> str:
        fixed = self._directories.setdefault(directory.volume_id, directory)
        if directory != fixed:
            raise SourceConflict(f'conflicting directory for source {directory.volume_id!r}')
        return fixed.volume_id

    def lookup(self, volume_id: str) -> SourceDirectory:
        if not isinstance(volume_id, str):
            raise ValueError('source volume number must be a string')
        try:
            return self._directories[volume_id]
        except KeyError as error:
            raise UnknownSource(volume_id) from error
