from dataclasses import dataclass

from source.catalog import SourceDirectory


@dataclass(frozen=True, slots=True)
class Selection:
    positions: tuple[int, ...]

    def origins(self, directory: SourceDirectory) -> tuple[tuple[str, int], ...]:
        return tuple((directory.volume_id, position) for position in self.positions)
