"""Physical two-source transfer with a bounded retention rack and paired issue."""

from collections import deque
from contextlib import suppress

from plan.binding import MountMismatch
from plan.dual import DualPlan, normalize_targets
from reconcile import reconcile_written
from source.reader import ForwardReader, ReadError

from .record import WrittenRecord
from .transfer import ExecutionError
from .writer import SequentialWriter


class _TargetBuffer:
    """One target's ordered work list and its real sequential write head."""

    def __init__(self, index: int, name: str, origins, writer: SequentialWriter) -> None:
        self.index = index
        self.name = name
        self.origins = tuple(origins)
        self.queue = deque(self.origins)
        self.writer = writer
        self.records: list[WrittenRecord] = []

    def at_front(self, key) -> bool:
        return bool(self.queue) and self.queue[0] == key


def _require_writer(writer, width: int) -> None:
    if not isinstance(writer, SequentialWriter):
        raise ValueError('target writers must be SequentialWriter instances')
    if writer.width != width:
        raise ValueError('target writer width differs from the common layout')
    if not writer.is_open:
        raise ValueError('target writers must be newly opened')


class _Transfer:
    def __init__(self, catalog, plan: DualPlan, devices, rack, writers) -> None:
        self._catalog = catalog
        self._plan = plan
        self._devices = tuple(devices)
        self._rack = rack
        self._capacity = rack.capacity
        self._writers = tuple(writers)
        self._targets = tuple(
            _TargetBuffer(index, target.name, target.origins, self._writers[index])
            for index, target in enumerate(plan.targets)
        )
        self._needs: dict[tuple[str, int], set[int]] = {}
        for index, target in enumerate(plan.targets):
            for key in target.origins:
                self._needs.setdefault(key, set()).add(index)
        self._delivered: dict[tuple[str, int], set[int]] = {key: set() for key in self._needs}
        # Exactly one physical slot per shared origin, kept until its last target is fed.
        self._retained: dict[tuple[str, int], int] = {}
        self._retained_order: list[tuple[str, int]] = []

    # -- delivery --------------------------------------------------------
    def _write_to(self, target: _TargetBuffer, key, frame) -> None:
        target_index = target.writer.append(frame.body)
        target.records.append(WrittenRecord(target_index, key[0], key[1]))
        target.queue.popleft()
        self._delivered[key].add(target.index)

    def _fully_delivered(self, key) -> bool:
        return self._delivered[key] >= self._needs[key]

    def _deliver_fresh(self, key, frame) -> None:
        for target in self._targets:
            if target.index not in self._delivered[key] and target.at_front(key):
                self._write_to(target, key, frame)

    def _flush(self) -> None:
        """Honour racked origins whose turn has arrived, freeing consumed slots."""
        while True:
            changed = False
            for key in list(self._retained_order):
                if key not in self._retained:
                    continue
                slot = self._retained[key]
                frame = None
                for target in self._targets:
                    if target.index in self._delivered[key] or not target.at_front(key):
                        continue
                    if frame is None:
                        frame = self._rack.get(slot)
                        if (frame.volume_id, frame.position) != key:
                            raise ExecutionError('retention rack returned a different frame')
                    self._write_to(target, key, frame)
                    changed = True
                if self._fully_delivered(key):
                    del self._retained[key]
                    self._retained_order.remove(key)
                    self._rack.release(slot)
                    changed = True
            if not changed:
                break

    # -- transfer --------------------------------------------------------
    def _transfer(self) -> None:
        readers = [ForwardReader(device) for device in self._devices]
        for volume, position in self._plan.reads:
            # Before advancing any head, everything still needed must already sit in a slot.
            self._flush()
            try:
                frame = readers[volume].read_through(position)
            except ReadError as error:
                raise ExecutionError(str(error)) from error
            key = (frame.volume_id, frame.position)
            if key not in self._needs:
                raise ExecutionError(f'source frame {key!r} is not required by any target')
            # The fresh frame may go straight to the target whose turn it is; leftovers are racked.
            self._deliver_fresh(key, frame)
            self._flush()
            if not self._fully_delivered(key):
                try:
                    slot = self._rack.hold(frame)
                except OSError as error:
                    raise ExecutionError(
                        f'cannot retain source frame {key!r} within capacity {self._capacity}: {error}'
                    ) from error
                self._retained[key] = slot
                self._retained_order.append(key)
            self._flush()
        self._flush()
        for target in self._targets:
            if target.queue:
                raise ExecutionError(f'target {target.name!r} is missing planned frames')

    # -- teardown --------------------------------------------------------
    def _release_retained(self) -> None:
        for key in list(self._retained_order):
            if key in self._retained:
                with suppress(Exception):
                    self._rack.release(self._retained.pop(key))
        self._retained_order.clear()

    def _abort_execution(self) -> None:
        for writer in self._writers:
            writer.abort()
        self._release_retained()

    def _publish(self, bodies):
        from issue import IssueCatalog
        from issue.publish import PublishError

        layout = self._plan.directories[0].layout
        try:
            manifests = tuple(
                reconcile_written(layout, target.origins, body, tuple(target.records))
                for target, body in zip(self._targets, bodies)
            )
            if not isinstance(self._catalog, IssueCatalog):
                raise TypeError('a bound IssueCatalog is required')
            return self._catalog.commit_pair(
                layout.width,
                (bodies[0], manifests[0]),
                (bodies[1], manifests[1]),
            )
        except Exception as error:
            self._abort_execution()
            if isinstance(error, PublishError):
                raise
            raise PublishError(f'publication failed: {error}') from error

    def run(self):
        try:
            self._transfer()
            # Sealing closes both heads; a seal failure still counts as an execution failure.
            bodies = tuple(target.writer.seal() for target in self._targets)
        except Exception as error:
            self._abort_execution()
            if isinstance(error, ExecutionError):
                raise
            raise ExecutionError(f'double transfer failed: {error}') from error
        return self._publish(bodies)


def execute_transfer(catalog, plan, devices, targets, rack, *, writers=None):
    """Execute and jointly issue the two-target transfer described by ``plan``.

    Every check runs before any source frame, target byte or retention slot is touched.
    A failed execution raises :class:`ExecutionError`; a failed reconciliation or commit
    raises :class:`issue.publish.PublishError`; either way no new issue is created.
    """

    if not isinstance(plan, DualPlan):
        raise ValueError('an executable DualPlan is required')
    expected_targets = normalize_targets(plan.directories, targets)
    if expected_targets != plan.targets:
        raise ValueError('work order targets differ from the plan')

    if isinstance(devices, (str, bytes, bytearray)):
        raise ValueError('two frame devices are required')
    fixed_devices = tuple(devices)
    if len(fixed_devices) != 2:
        raise ValueError('exactly two frame devices are required')
    for index, device in enumerate(fixed_devices):
        if getattr(device, 'directory', None) != plan.directories[index]:
            raise MountMismatch(f'mounted directory for source {index} differs from the plan')

    if not hasattr(rack, 'capacity') or rack.capacity != plan.capacity:
        raise ValueError('retention rack capacity differs from the plan')
    for method in ('hold', 'get', 'release'):
        if not callable(getattr(rack, method, None)):
            raise ValueError('retention rack does not provide the required port')

    if not hasattr(catalog, 'commit_pair'):
        raise ValueError('a bound IssueCatalog is required')

    width = plan.directories[0].layout.width
    if writers is None:
        fixed_writers = (SequentialWriter(width), SequentialWriter(width))
    else:
        if isinstance(writers, (str, bytes, bytearray)):
            raise ValueError('two target writers are required')
        fixed_writers = tuple(writers)
        if len(fixed_writers) != 2:
            raise ValueError('exactly two target writers are required')
        if fixed_writers[0] is fixed_writers[1]:
            raise ValueError('the two target writers must be distinct')
        for writer in fixed_writers:
            _require_writer(writer, width)

    return _Transfer(catalog, plan, fixed_devices, rack, fixed_writers).run()
