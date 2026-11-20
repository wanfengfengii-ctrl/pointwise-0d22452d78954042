from collections.abc import Sequence
from io import BytesIO
from pathlib import Path

from issue import IssueCatalog, IssueStore
from media.candidate import CandidateVolume
from media.writer import SequentialWriter
from plan.transfer import SinglePlan
from source.catalog import SourceDirectory
from source.layout import RecordLayout
from source.reader import SourceFrame


SAMPLE_FRAMES = (b'000', b'-02', b'   ', b'007', b' 07')


def mounted_directory(
    volume_id: str = 'SC-17',
    *,
    width: int = 3,
    slot_count: int = 5,
    positions: Sequence[int] | None = None,
) -> SourceDirectory:
    return SourceDirectory(volume_id, RecordLayout(width), slot_count, range(slot_count) if positions is None else positions)


class RecordedSource:
    def __init__(
        self, directory: SourceDirectory, frames: Sequence[bytes], events: list[tuple], *, fail_at: int | None = None
    ) -> None:
        self._directory = directory
        self._frames = tuple(frames)
        self._events = events
        self._fail_at = fail_at
        self._next_slot = 0
        self._failed = False

    @property
    def directory(self) -> SourceDirectory:
        return self._directory

    def read_frame(self, position: int) -> bytes:
        if self._failed or position != self._next_slot or position >= self.directory.slot_count:
            raise OSError('fixture read head refused the requested slot')
        self._events.append(('read', self.directory.volume_id, position))
        if position == self._fail_at:
            self._failed = True
            raise OSError(f'injected read failure at slot {position}')
        self._next_slot += 1
        return self._frames[position] if position < len(self._frames) else b''


class RecordedTarget(BytesIO):
    def __init__(
        self, width: int, events: list[tuple], *, short_at: int | None = None, fail_seal: bool = False
    ) -> None:
        super().__init__()
        self._width = width
        self._events = events
        self._short_at = short_at
        self._fail_seal = fail_seal

    def write(self, frame: bytes) -> int:
        index = self.tell() // self._width
        if index == self._short_at:
            self._events.append(('short_write', index))
            return super().write(frame[:-1])
        length = super().write(frame)
        self._events.append(('write', index, frame))
        return length

    def flush(self) -> None:
        if self._fail_seal:
            raise OSError('injected seal failure')
        super().flush()


class RecordedStation:
    def __init__(
        self,
        directory: SourceDirectory,
        frames: Sequence[bytes] = SAMPLE_FRAMES,
        *,
        fail_at: int | None = None,
        short_write_at: int | None = None,
        fail_seal: bool = False,
    ) -> None:
        self.events: list[tuple] = []
        self.source = RecordedSource(directory, frames, self.events, fail_at=fail_at)
        self._short_write_at = short_write_at
        self._fail_seal = fail_seal
        self.candidates: list[CandidateVolume] = []
        self.outputs: list[RecordedTarget] = []

    def open_candidate(self, plan: SinglePlan) -> CandidateVolume:
        width = plan.binding.directory.layout.width
        output = RecordedTarget(width, self.events, short_at=self._short_write_at, fail_seal=self._fail_seal)
        candidate = CandidateVolume(plan, SequentialWriter(width, output))
        self.candidates.append(candidate)
        self.outputs.append(output)
        return candidate


class RetentionSlots:
    """A bounded physical rack used independently of the V1 direct transfer."""

    def __init__(self, capacity: int, events: list[tuple]) -> None:
        if type(capacity) is not int or capacity < 0:
            raise ValueError('rack capacity must be a nonnegative integer')
        self.capacity = capacity
        self.peak = 0
        self._held: dict[int, SourceFrame] = {}
        self._events = events

    def hold(self, frame: SourceFrame) -> int:
        for slot in range(self.capacity):
            if slot not in self._held:
                self._held[slot] = frame
                self.peak = max(self.peak, len(self._held))
                self._events.append(('hold', slot, frame.volume_id, frame.position))
                return slot
        raise OSError('retention rack is full')

    def get(self, slot: int) -> SourceFrame:
        frame = self._held[slot]
        self._events.append(('get', slot, frame.volume_id, frame.position))
        return frame

    def release(self, slot: int) -> None:
        frame = self._held.pop(slot)
        self._events.append(('release', slot, frame.volume_id, frame.position))


def open_issues(path: Path) -> IssueCatalog:
    store = IssueStore(path)
    store.initialize()
    return IssueCatalog(store)
