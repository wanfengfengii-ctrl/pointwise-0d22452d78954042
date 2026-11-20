from dataclasses import dataclass

from plan.transfer import SinglePlan
from source.layout import decode_record
from source.reader import SourceFrame

from .record import WrittenRecord, record_written_frame
from .writer import SequentialWriter


class CandidateStateError(ValueError):
    """The requested operation is invalid for the candidate's current state."""


@dataclass(frozen=True, slots=True)
class CandidateSnapshot:
    plan: SinglePlan
    body: bytes
    records: tuple[WrittenRecord, ...]


class CandidateVolume:
    def __init__(self, plan: SinglePlan, writer: SequentialWriter | None = None) -> None:
        self._plan = plan
        self._layout = plan.binding.directory.layout
        self._writer = SequentialWriter(self._layout.width) if writer is None else writer
        if self._writer.width != self._layout.width:
            self._writer.abort()
            raise ValueError('target writer width differs from the plan')
        self._state = 'writing'
        self._records: list[WrittenRecord] = []
        self._sealed: CandidateSnapshot | None = None

    @property
    def state(self) -> str:
        return self._state

    def _require_writing(self) -> None:
        if self._state != 'writing':
            raise CandidateStateError(f'candidate is {self._state}')

    def append(self, frame: SourceFrame) -> None:
        self._require_writing()
        try:
            if len(self._records) == len(self._plan.selection.positions):
                raise CandidateStateError('candidate already has the planned number of records')
            decode_record(self._layout, frame.body)
            target_index = self._writer.append(frame.body)
            self._records.append(record_written_frame(target_index, frame))
        except Exception:
            self.discard()
            raise

    def complete(self) -> CandidateSnapshot:
        self._require_writing()
        try:
            if len(self._records) != len(self._plan.selection.positions):
                raise CandidateStateError('candidate is missing selected records')
            body = self._writer.seal()
            self._sealed = CandidateSnapshot(self._plan, body, tuple(self._records))
        except Exception:
            self.discard()
            raise
        self._state = 'complete'
        return self._sealed

    def discard(self) -> None:
        self._state = 'discarded'
        self._sealed = None
        self._records.clear()
        self._writer.abort()

    def snapshot(self) -> CandidateSnapshot:
        if self._sealed is None:
            raise CandidateStateError(f'candidate is {self._state}')
        return self._sealed
