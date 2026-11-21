import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from issue import read_issue
from issue.publish import PublishError
from issue.receipt import ReceiptDetails
from media import execute_transfer
from media.transfer import ExecutionError
from media.writer import SequentialWriter
from plan import plan_transfer
from plan.binding import MountMismatch
from source import SourceDirectory, RecordLayout
from tests.fixtures import RecordedSource, RecordedTarget, RetentionSlots, open_issues


def _directory(volume_id, frames, *, positions=None, width=3):
    count = len(frames)
    return SourceDirectory(
        volume_id, RecordLayout(width), count, range(count) if positions is None else positions
    )


class _Shift:
    """Two mounted sources, a rack and two observed write heads."""

    def __init__(self, left_frames, right_frames, targets, capacity, *, left_positions=None, right_positions=None):
        self.left = _directory('LEFT', left_frames, positions=left_positions)
        self.right = _directory('RIGHT', right_frames, positions=right_positions)
        self.directories = [self.left, self.right]
        self.targets = targets
        self.plan = plan_transfer(self.directories, targets, capacity)
        self.events: list[tuple] = []
        self.devices = [
            RecordedSource(self.left, left_frames, self.events),
            RecordedSource(self.right, right_frames, self.events),
        ]
        self.rack = RetentionSlots(capacity, self.events)
        self.outputs = [RecordedTarget(3, self.events), RecordedTarget(3, self.events)]
        self.writers = [SequentialWriter(3, output) for output in self.outputs]


class TestDualExecution(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store_path = Path(temporary.name) / 'issues.db'
        self.issues = open_issues(self.store_path)

    def _example(self, capacity=1):
        left = [b'000']
        right = [b'-02', b'   ']
        targets = [
            ('meter', [('RIGHT', 0), ('RIGHT', 1), ('LEFT', 0)]),
            ('quality', [('RIGHT', 1), ('RIGHT', 0), ('LEFT', 0)]),
        ]
        return _Shift(left, right, targets, capacity)

    def _run(self, shift, *, writers='default'):
        kwargs = {} if writers == 'default' else {'writers': writers}
        return execute_transfer(
            self.issues, shift.plan, shift.devices, shift.targets, shift.rack, **kwargs
        )

    # -- success ---------------------------------------------------------
    def test_example_bodies_origins_and_peak(self) -> None:
        shift = self._example()
        receipts = self._run(shift)
        self.assertEqual(len(receipts), 2)
        self.assertNotEqual(receipts[0].number, receipts[1].number)
        first = read_issue(self.issues, receipts[0].number)
        second = read_issue(self.issues, receipts[1].number)
        self.assertEqual(first.body.hex(), '2d3032202020303030')
        self.assertEqual(second.body.hex(), '2020202d3032303030')
        expected_origins = (('RIGHT', 0), ('RIGHT', 1), ('LEFT', 0))
        self.assertEqual(
            tuple((origin.source_volume_id, origin.source_position) for origin in receipts[0].origins),
            expected_origins,
        )
        self.assertEqual(
            tuple((origin.source_volume_id, origin.source_position) for origin in receipts[1].origins),
            (('RIGHT', 1), ('RIGHT', 0), ('LEFT', 0)),
        )
        for receipt in receipts:
            self.assertEqual(receipt.details, ReceiptDetails(3, 2, 1, 1, -2))
        self.assertEqual(shift.rack.peak, 1)
        self.assertEqual(shift.rack._held, {})
        self.assertEqual(self.issues.list_numbers(), (receipts[0].number, receipts[1].number))

    def test_reads_are_forward_only_and_validated(self) -> None:
        shift = self._example()
        self._run(shift)
        for volume_id in ('LEFT', 'RIGHT'):
            slots = [event[2] for event in shift.events if event[0] == 'read' and event[1] == volume_id]
            self.assertEqual(slots, sorted(set(slots)))
        self.assertIn(('read', 'RIGHT', 0), shift.events)
        self.assertIn(('read', 'RIGHT', 1), shift.events)
        self.assertIn(('read', 'LEFT', 0), shift.events)

    def test_empty_targets_sign_zero_ledgers(self) -> None:
        shift = _Shift([b'000'], [b'-02'], [('a', []), ('b', [])], 0)
        receipts = self._run(shift)
        self.assertEqual(shift.events, [])
        self.assertEqual(receipts[0].details, ReceiptDetails(0, 0, 0, 0, 0))
        self.assertEqual(receipts[1].origins, ())
        self.assertEqual(read_issue(self.issues, receipts[0].number).body, b'')

    def test_direct_handoff_without_rack(self) -> None:
        # Both targets want the same order; no frame ever needs to wait.
        shift = _Shift([b'007'], [b'   '], [('a', [('RIGHT', 0)]), ('b', [('LEFT', 0)])], 0)
        receipts = self._run(shift)
        self.assertEqual(self.issues.lookup(receipts[0].number).body, b'   ')
        self.assertEqual(self.issues.lookup(receipts[1].number).body, b'007')
        self.assertEqual(shift.rack.peak, 0)

    def test_unreferenced_source_volume_is_never_read(self) -> None:
        shift = _Shift([b'000'], [b'-02'], [('a', [('LEFT', 0)]), ('b', [('LEFT', 0)])], 0)
        self._run(shift)
        self.assertFalse([event for event in shift.events if event[0] == 'read' and event[1] == 'RIGHT'])

    def test_read_stops_at_last_referenced_slot(self) -> None:
        shift = _Shift([b'000'], [b'-02', b'   ', b'007', b' 07'], [('a', [('RIGHT', 1)]), ('b', [])], 0)
        self._run(shift)
        slots = [event[2] for event in shift.events if event[0] == 'read' and event[1] == 'RIGHT']
        self.assertEqual(slots, [0, 1])

    def test_default_writers_are_used_when_omitted(self) -> None:
        shift = self._example()
        receipts = self._run(shift)
        self.assertEqual(read_issue(self.issues, receipts[0].number).body.hex(), '2d3032202020303030')

    # -- preflight rejections: nothing moves -----------------------------
    def _assert_nothing_touched(self, shift, *, allow_reads=False) -> None:
        if not allow_reads:
            self.assertEqual(shift.events, [])
        self.assertEqual(shift.outputs[0].tell(), 0)
        self.assertEqual(shift.outputs[1].tell(), 0)
        self.assertEqual(shift.rack._held, {})
        self.assertEqual(self.issues.list_numbers(), ())

    def test_no_plan_plan_value_is_rejected_before_io(self) -> None:
        shift = _Shift(
            [b'000'], [b'-02', b'   '],
            [('m', [('RIGHT', 0), ('RIGHT', 1)]), ('q', [('RIGHT', 1), ('RIGHT', 0), ('LEFT', 0)])], 0,
        )
        self.assertEqual(shift.plan, 'NO_PLAN')
        with self.assertRaises(ValueError):
            execute_transfer(self.issues, shift.plan, shift.devices, shift.targets, shift.rack)
        self._assert_nothing_touched(shift)

    def test_changed_work_order_is_rejected(self) -> None:
        shift = self._example()
        tampered = [('meter', [('RIGHT', 0), ('LEFT', 0)]), ('quality', shift.targets[1][1])]
        with self.assertRaises(ValueError):
            execute_transfer(self.issues, shift.plan, shift.devices, tampered, shift.rack)
        self._assert_nothing_touched(shift)

    def test_changed_mount_is_rejected(self) -> None:
        shift = self._example()
        other = RecordedSource(_directory('RIGHT', [b'-02', b'   '], positions=[0]), [b'-02', b'   '], [])
        with self.assertRaises(MountMismatch):
            execute_transfer(self.issues, shift.plan, [shift.devices[0], other], shift.targets, shift.rack)
        self._assert_nothing_touched(shift)

    def test_changed_capacity_is_rejected(self) -> None:
        shift = self._example(capacity=1)
        other_rack = RetentionSlots(2, [])
        with self.assertRaises(ValueError):
            execute_transfer(self.issues, shift.plan, shift.devices, shift.targets, other_rack)
        self._assert_nothing_touched(shift)

    def test_wrong_writer_width_is_rejected(self) -> None:
        shift = self._example()
        bad = [SequentialWriter(4), SequentialWriter(3)]
        with self.assertRaises(ValueError):
            execute_transfer(self.issues, shift.plan, shift.devices, shift.targets, shift.rack, writers=bad)
        self._assert_nothing_touched(shift)

    # -- execution failures: candidates and rack torn down ---------------
    def test_read_failure_aborts_everything(self) -> None:
        old = self._publish_old_issue()
        shift = self._example()
        shift.devices[1] = RecordedSource(shift.right, [b'-02', b'   '], shift.events, fail_at=0)
        with self.assertRaisesRegex(ExecutionError, 'injected read failure at slot 0'):
            execute_transfer(self.issues, shift.plan, shift.devices, shift.targets, shift.rack,
                             writers=shift.writers)
        self.assertEqual(shift.rack._held, {})
        self.assertTrue(all(not writer.is_open for writer in shift.writers))
        self.assertOnlyOld(old)

    def test_retention_slot_not_on_plan_fails_closed(self) -> None:
        shift = _Shift(
            [b'000'], [b'-02', b'   '],
            [('m', [('RIGHT', 1), ('RIGHT', 0), ('LEFT', 0)]), ('q', [('RIGHT', 1), ('LEFT', 0)])], 1,
        )
        # Force the hold to fail although the planner thought one slot sufficed.
        class StubbornRack(RetentionSlots):
            def hold(self, frame):
                raise OSError('rack refused the hold')

        shift.rack = StubbornRack(1, shift.events)
        with self.assertRaisesRegex(ExecutionError, 'cannot retain'):
            execute_transfer(self.issues, shift.plan, shift.devices, shift.targets, shift.rack,
                             writers=shift.writers)
        self.assertEqual(self.issues.list_numbers(), ())

    def test_write_failure_aborts_both_targets(self) -> None:
        old = self._publish_old_issue()
        shift = self._example()
        outputs = [RecordedTarget(3, shift.events, short_at=1), RecordedTarget(3, shift.events)]
        writers = [SequentialWriter(3, outputs[0]), SequentialWriter(3, outputs[1])]
        with self.assertRaisesRegex(ExecutionError, 'short target write at record 1'):
            execute_transfer(self.issues, shift.plan, shift.devices, shift.targets, shift.rack, writers=writers)
        self.assertEqual(shift.rack._held, {})
        self.assertTrue(all(not writer.is_open for writer in writers))
        self.assertOnlyOld(old)

    def test_spec_read_error_scenario_delivers_nothing(self) -> None:
        # Left holds A, right runs B,C,D in physical order; targets are B,C,D,A and D,B,A.
        old = self._publish_old_issue()
        shift = _Shift(
            [b'000'], [b'-02', b'   ', b'007', b' 07'],
            [('a', [('RIGHT', 0), ('RIGHT', 1), ('RIGHT', 2), ('LEFT', 0)]),
             ('b', [('RIGHT', 3), ('RIGHT', 0), ('LEFT', 0)])],
            1,
        )
        shift.devices[1] = RecordedSource(shift.right, [b'-02', b'   ', b'007', b' 07'], shift.events, fail_at=2)
        with self.assertRaisesRegex(ExecutionError, "slot 2"):
            execute_transfer(self.issues, shift.plan, shift.devices, shift.targets, shift.rack,
                             writers=shift.writers)
        self.assertEqual(shift.rack._held, {})
        self.assertOnlyOld(old)

    def test_seal_failure_is_execution_error(self) -> None:
        old = self._publish_old_issue()
        shift = self._example()
        outputs = [RecordedTarget(3, shift.events), RecordedTarget(3, shift.events, fail_seal=True)]
        writers = [SequentialWriter(3, outputs[0]), SequentialWriter(3, outputs[1])]
        with self.assertRaisesRegex(ExecutionError, 'injected seal failure'):
            execute_transfer(self.issues, shift.plan, shift.devices, shift.targets, shift.rack, writers=writers)
        self.assertEqual(shift.rack._held, {})
        self.assertOnlyOld(old)

    def test_second_target_commit_failure_rolls_back_both(self) -> None:
        old = self._publish_old_issue()
        shift = self._example()
        with closing(sqlite3.connect(self.store_path)) as connection, connection:
            connection.execute('''CREATE TRIGGER abort_second BEFORE INSERT ON issues
                WHEN (SELECT COUNT(*) FROM issues) >= 2
                BEGIN SELECT RAISE(ABORT, 'injected paired commit failure'); END''')
        with self.assertRaisesRegex(PublishError, 'injected paired commit failure'):
            execute_transfer(self.issues, shift.plan, shift.devices, shift.targets, shift.rack,
                             writers=shift.writers)
        self.assertTrue(all(not writer.is_open for writer in shift.writers))
        self.assertEqual(shift.rack._held, {})
        self.assertOnlyOld(old)

    def _publish_old_issue(self):
        from issue import publish_single
        from media import execute_single
        from plan import plan_single
        from source import SourceCatalog, register_source

        sources = SourceCatalog()
        register_source(sources, 'OLD', 3, 1, [0])
        directory = SourceDirectory('OLD', RecordLayout(3), 1, [0])
        plan = plan_single(sources, 'OLD', [0])
        events: list[tuple] = []
        device = RecordedSource(directory, [b'123'], events)
        candidate = execute_single(plan, device)
        return publish_single(self.issues, plan, candidate)

    def assertOnlyOld(self, old) -> None:
        reopened = open_issues(self.store_path)
        self.assertEqual(reopened.list_numbers(), (old.number,))
        self.assertEqual(read_issue(reopened, old.number), read_issue(reopened, old.number))


if __name__ == '__main__':
    unittest.main()
