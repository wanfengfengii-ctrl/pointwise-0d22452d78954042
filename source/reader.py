from dataclasses import dataclass

from .catalog import SourceDirectory
from .device import FrameDevice
from .layout import decode_record


class ReadError(OSError):
    """The current forward-only read session cannot continue."""


@dataclass(frozen=True, slots=True)
class SourceFrame:
    volume_id: str
    position: int
    body: bytes


class ForwardReader:
    def __init__(self, device: FrameDevice) -> None:
        self._device = device
        self._directory = device.directory
        self._next_position = 0
        self._failed = False

    @property
    def directory(self) -> SourceDirectory:
        return self._directory

    def read_through(self, position: int) -> SourceFrame:
        if self._failed:
            raise ReadError('source read session has already failed')
        if type(position) is not int or not self._next_position <= position < self._directory.slot_count:
            raise ReadError(f'cannot advance the read head to slot {position!r}')
        for physical_position in range(self._next_position, position + 1):
            try:
                body = self._device.read_frame(physical_position)
                decode_record(self._directory.layout, body)
            except (OSError, ValueError) as error:
                self._failed = True
                raise ReadError(
                    f'source {self._directory.volume_id!r} slot {physical_position}: {error}'
                ) from error
            self._next_position = physical_position + 1
        return SourceFrame(self._directory.volume_id, position, body)
