"""Directory-only scheduling for the two-source, two-target transfer."""

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass

from source.catalog import SourceDirectory


class DualPlanError(ValueError):
    """The requested double transfer cannot be described by this plan."""


@dataclass(frozen=True, slots=True)
class TransferTarget:
    """One import machine's ordered list of named origins."""

    name: str
    origins: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class DualPlan:
    """A frozen, executable two-source two-target transfer plan.

    ``reads`` is the physical read order chosen by the scheduler.  Every
    step is ``(source_index, position)`` and the positions within one source
    are strictly increasing.
    """

    directories: tuple[SourceDirectory, SourceDirectory]
    targets: tuple[TransferTarget, TransferTarget]
    capacity: int
    reads: tuple[tuple[int, int], ...]


def validate_capacity(capacity: int) -> int:
    if type(capacity) is not int or capacity < 0:
        raise ValueError('retention capacity must be a nonnegative integer')
    return capacity


def _fixed_directories(directories) -> tuple[SourceDirectory, SourceDirectory]:
    if isinstance(directories, (str, bytes, bytearray)) or not isinstance(directories, Sequence):
        raise ValueError('two source directories are required')
    fixed = tuple(directories)
    if len(fixed) != 2:
        raise ValueError('exactly two source directories are required')
    if not all(isinstance(directory, SourceDirectory) for directory in fixed):
        raise ValueError('each source directory must be a SourceDirectory')
    if fixed[0].volume_id == fixed[1].volume_id:
        raise ValueError('source volume numbers must be distinct')
    if fixed[0].layout != fixed[1].layout:
        raise ValueError('both sources must use the same V1 record layout')
    return fixed


def _directory_positions(directories: tuple[SourceDirectory, SourceDirectory]) -> dict[str, frozenset[int]]:
    return {directory.volume_id: frozenset(directory.positions) for directory in directories}


def _fixed_origins(
    directory_index: dict[str, SourceDirectory],
    available: dict[str, frozenset[int]],
    origins,
) -> tuple[tuple[str, int], ...]:
    if isinstance(origins, (str, bytes, bytearray)) or not isinstance(origins, Sequence):
        raise ValueError('target origins must be a sequence of (volume_id, position) pairs')
    fixed: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for pair in origins:
        if isinstance(pair, (str, bytes, bytearray)) or not isinstance(pair, Sequence) or len(pair) != 2:
            raise ValueError('each origin must be a (volume_id, position) pair')
        volume_id, position = pair
        directory = directory_index.get(volume_id) if isinstance(volume_id, str) else None
        if directory is None:
            raise ValueError(f'origin volume {volume_id!r} is not one of the two sources')
        if type(position) is not int or not 0 <= position < directory.slot_count:
            raise ValueError(f'origin position out of range: {position!r}')
        if position not in available[volume_id]:
            raise ValueError(f'origin position {position} is absent from source {volume_id!r}')
        key = (volume_id, position)
        if key in seen:
            raise ValueError(f'origin {key!r} occurs twice in one target')
        seen.add(key)
        fixed.append(key)
    return tuple(fixed)


def normalize_targets(
    directories: tuple[SourceDirectory, SourceDirectory], targets
) -> tuple[TransferTarget, TransferTarget]:
    """Validate and freeze the work order supplied in target order."""

    if isinstance(targets, (str, bytes, bytearray)) or not isinstance(targets, Sequence):
        raise ValueError('two (name, origins) targets are required')
    fixed = tuple(targets)
    if len(fixed) != 2:
        raise ValueError('exactly two targets are required')
    directory_index = {directory.volume_id: directory for directory in directories}
    available = _directory_positions(directories)
    result: list[TransferTarget] = []
    names: set[str] = set()
    for item in fixed:
        if isinstance(item, (str, bytes, bytearray)) or not isinstance(item, Sequence) or len(item) != 2:
            raise ValueError('each target must be a (name, origins) pair')
        name, origins = item
        if not isinstance(name, str) or not name.strip():
            raise ValueError('target name must be a nonblank string')
        if name in names:
            raise ValueError('target names must be distinct')
        names.add(name)
        result.append(TransferTarget(name, _fixed_origins(directory_index, available, origins)))
    return (result[0], result[1])


def _indexed_targets(
    directories: tuple[SourceDirectory, SourceDirectory], targets: tuple[TransferTarget, TransferTarget]
) -> tuple[tuple[tuple[int, int], ...], tuple[tuple[int, int], ...]]:
    volume_index = {directory.volume_id: index for index, directory in enumerate(directories)}
    return tuple(
        tuple((volume_index[volume_id], position) for volume_id, position in target.origins)
        for target in targets
    )


def _schedule(
    directories: tuple[SourceDirectory, SourceDirectory],
    targets: tuple[TransferTarget, TransferTarget],
    capacity: int,
) -> tuple[tuple[int, int], ...] | None:
    """Return a legal physical read order, or ``None`` when none fits."""

    indexed = _indexed_targets(directories, targets)
    references = [
        sorted({position for sequence in indexed for volume, position in sequence if volume == index})
        for index in (0, 1)
    ]
    ranks = [
        {position: rank for rank, position in enumerate(positions)} for positions in references
    ]

    def state_progress(state: tuple[int, int]):
        def read(volume: int, position: int) -> bool:
            return ranks[volume][position] < state[volume]

        pointers = []
        for sequence in indexed:
            pointer = 0
            while pointer < len(sequence) and read(*sequence[pointer]):
                pointer += 1
            pointers.append(pointer)
        needed: set[tuple[int, int]] = set()
        for pointer, sequence in zip(pointers, indexed):
            needed.update(sequence[pointer:])
        held = sum(1 for volume, position in needed if read(volume, position))
        return held

    start = (0, 0)
    goal = (len(references[0]), len(references[1]))
    if state_progress(start) > capacity or state_progress(goal) > capacity:
        return None
    parents: dict[tuple[int, int], tuple[tuple[int, int], int] | None] = {start: None}
    queue = deque([start])
    while queue:
        state = queue.popleft()
        if state == goal:
            break
        for volume in (0, 1):
            if state[volume] >= len(references[volume]):
                continue
            successor = list(state)
            successor[volume] += 1
            successor = (successor[0], successor[1])
            if successor in parents:
                continue
            if state_progress(successor) > capacity:
                continue
            parents[successor] = (state, volume)
            queue.append(successor)
    if goal not in parents:
        return None
    path: list[tuple[int, int]] = []
    state = goal
    while parents[state] is not None:
        previous, volume = parents[state]
        path.append((volume, references[volume][state[volume] - 1]))
        state = previous
    path.reverse()
    return tuple(path)


def plan_transfer(directories, targets, capacity):
    """Plan a two-source transfer or return ``"NO_PLAN"``.

    Only directories are inspected; no frame body or device is touched.
    """

    fixed_directories = _fixed_directories(directories)
    fixed_targets = normalize_targets(fixed_directories, targets)
    validate_capacity(capacity)
    reads = _schedule(fixed_directories, fixed_targets, capacity)
    if reads is None:
        return 'NO_PLAN'
    return DualPlan(fixed_directories, fixed_targets, capacity, reads)
