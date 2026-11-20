from pathlib import Path
from typing import Protocol

from .catalog import SourceDirectory


class FrameDevice(Protocol):
    @property
    def directory(self) -> SourceDirectory:
        ...

    def read_frame(self, position: int) -> bytes:
        """Read the next physical slot or raise an OSError."""
        ...


class FileFrameDevice:
    def __init__(self, path: str | Path, directory: SourceDirectory) -> None:
        self._directory = directory
        self._stream = Path(path).open('rb', buffering=0)
        self._next_position = 0
        self._failed = False

    @property
    def directory(self) -> SourceDirectory:
        return self._directory

    def read_frame(self, position: int) -> bytes:
        if self._stream.closed or self._failed:
            raise OSError('source read head is unavailable')
        if position != self._next_position or position >= self._directory.slot_count:
            raise OSError(f'source read head cannot read slot {position}')
        try:
            frame = self._stream.read(self._directory.layout.width)
        except (OSError, OverflowError) as error:
            self._failed = True
            raise OSError(f'physical read failed at slot {position}') from error
        self._next_position += 1
        if len(frame) != self._directory.layout.width:
            self._failed = True
        return frame

    def close(self) -> None:
        self._stream.close()


def open_frame_device(path: str | Path, directory: SourceDirectory) -> FileFrameDevice:
    return FileFrameDevice(path, directory)
