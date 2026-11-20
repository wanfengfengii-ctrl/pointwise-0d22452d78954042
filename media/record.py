from dataclasses import dataclass

from source.reader import SourceFrame


@dataclass(frozen=True, slots=True)
class WrittenRecord:
    target_index: int
    source_volume_id: str
    source_position: int

    def byte_range(self, width: int) -> slice:
        first_byte = self.target_index * width
        return slice(first_byte, first_byte + width)


def record_written_frame(target_index: int, frame: SourceFrame) -> WrittenRecord:
    return WrittenRecord(target_index, frame.volume_id, frame.position)
