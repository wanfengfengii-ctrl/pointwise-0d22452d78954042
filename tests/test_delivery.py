import json
import sqlite3
import unittest
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from issue import IssueNotFound, publish_single, read_issue
from issue.publish import PublishError
from issue.receipt import IssuedOrigin, ReceiptDetails
from media.candidate import CandidateStateError, CandidateVolume
from media.record import WrittenRecord
from media.transfer import ExecutionError, execute_single
from plan import plan_single
from plan.binding import MountMismatch
from reconcile.origins import OriginMismatch
from source import SourceCatalog, register_source
from source.reader import SourceFrame
from tests.fixtures import SAMPLE_FRAMES, RecordedStation, RetentionSlots, mounted_directory, open_issues


class TestDelivery(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store_path = Path(temporary.name) / 'issues.db'
        self.issues = open_issues(self.store_path)
        self.sources = SourceCatalog()
        register_source(self.sources, 'SC-17', 3, 5, [0, 1, 2, 3, 4])
        self.directory = mounted_directory()

    def _execute(self, plan, station):
        return execute_single(plan, station.source, candidate_factory=station.open_candidate)

    def _publish_old_issue(self):
        plan = plan_single(self.sources, 'SC-17', [3])
        receipt = publish_single(self.issues, plan, self._execute(plan, RecordedStation(self.directory)))
        return read_issue(self.issues, receipt.number)

    def _assert_old_issue_only(self, old) -> None:
        reopened = open_issues(self.store_path)
        self.assertEqual(reopened.list_numbers(), (old.receipt.number,))
        self.assertEqual(read_issue(reopened, old.receipt.number), old)
        with self.assertRaises(IssueNotFound):
            read_issue(reopened, 'ISS-000002')

    def test_zero_missing_and_origins(self) -> None:
        plan = plan_single(self.sources, 'SC-17', [0, 2, 4])
        candidate = self._execute(plan, RecordedStation(self.directory))
        receipt = publish_single(self.issues, plan, candidate)
        self.assertEqual(receipt.details, ReceiptDetails(3, 2, 1, 1, 7))
        self.assertEqual(receipt.origins, (IssuedOrigin(0, 'SC-17', 0), IssuedOrigin(1, 'SC-17', 2), IssuedOrigin(2, 'SC-17', 4)))
        self.assertEqual(receipt.origin_at(1), IssuedOrigin(1, 'SC-17', 2))
        self.assertEqual(read_issue(self.issues, receipt.number).receipt, receipt)

    def test_gapped_selection(self) -> None:
        plan = plan_single(self.sources, 'SC-17', [0, 2, 4])
        station = RecordedStation(self.directory)
        candidate = self._execute(plan, station)
        snapshot = candidate.snapshot()
        self.assertEqual(snapshot.records, (WrittenRecord(0, 'SC-17', 0), WrittenRecord(1, 'SC-17', 2), WrittenRecord(2, 'SC-17', 4)))
        expected_events = [
            ('read', 'SC-17', 0), ('write', 0, b'000'), ('read', 'SC-17', 1),
            ('read', 'SC-17', 2), ('write', 1, b'   '), ('read', 'SC-17', 3),
            ('read', 'SC-17', 4), ('write', 2, b' 07'),
        ]
        self.assertEqual(station.events, expected_events)
        expected_body = b'000' + b'   ' + b' 07'
        self.assertEqual(snapshot.body, expected_body)
        receipt = publish_single(self.issues, plan, candidate)
        picked_up = read_issue(open_issues(self.store_path), receipt.number)
        expected_details = dict(total_records=3, valid_records=2, missing_records=1, zero_records=1, total=7)
        expected_origins = [
            dict(target_index=0, source_volume_id='SC-17', source_position=0),
            dict(target_index=1, source_volume_id='SC-17', source_position=2),
            dict(target_index=2, source_volume_id='SC-17', source_position=4),
        ]
        actual = dict(
            read_slots=[event[2] for event in station.events if event[0] == 'read'],
            written_frames=[event[2].decode('ascii') for event in station.events if event[0] == 'write'],
        )
        expected = dict(read_slots=[0, 1, 2, 3, 4], written_frames=['000', '   ', ' 07'])
        downstream_actual = dict(
            body_hex=picked_up.body.hex(), details=asdict(picked_up.receipt.details),
            origins=[asdict(origin) for origin in picked_up.receipt.origins],
        )
        downstream_expected = dict(body_hex=expected_body.hex(), details=expected_details, origins=expected_origins)
        self.assertEqual(actual, expected)
        self.assertEqual(downstream_actual, downstream_expected)
        print('POINTWISE_SCENARIO ' + json.dumps(dict(
            case_id='v4.gapped_selection',
            input=dict(volume_id='SC-17', width=3, frames=[frame.decode('ascii') for frame in SAMPLE_FRAMES], selection=[0, 2, 4]),
            expected=expected, actual=actual, downstream_expected=downstream_expected, downstream_actual=downstream_actual,
            code_paths=['source/reader.py', 'media/transfer.py', 'media/candidate.py', 'reconcile/checks.py', 'issue/storage.py', 'issue/read.py'],
            oracle='Arrays and totals are specified by hand from slots 0, 2 and 4. Zero and seven are the two measured values.',
        )), flush=True)

    def test_empty_issue(self) -> None:
        plan = plan_single(self.sources, 'SC-17', [])
        station = RecordedStation(self.directory)
        receipt = publish_single(self.issues, plan, self._execute(plan, station))
        volume = read_issue(self.issues, receipt.number)
        self.assertEqual(station.events, [])
        self.assertEqual(volume.body, b'')
        self.assertEqual(volume.width, 3)
        self.assertEqual(receipt.origins, ())
        self.assertEqual(receipt.details, ReceiptDetails(0, 0, 0, 0, 0))
        with self.assertRaises(IndexError):
            volume.record_at(0)

    def test_signed_exact_total(self) -> None:
        frames = (b'9223372036854775810', b'-2'.rjust(19, b' '))
        directory = mounted_directory('WIDE', width=19, slot_count=2)
        register_source(self.sources, 'WIDE', 19, 2, [0, 1])
        plan = plan_single(self.sources, 'WIDE', [0, 1])
        receipt = publish_single(self.issues, plan, self._execute(plan, RecordedStation(directory, frames)))
        self.assertEqual(receipt.details, ReceiptDetails(2, 2, 0, 0, 9223372036854775808))
        reopened = read_issue(open_issues(self.store_path), receipt.number)
        self.assertEqual(reopened.receipt, receipt)
        self.assertEqual(reopened.body, b''.join(frames))

    def test_wide_decimal_frame_retains_exact_value(self) -> None:
        frame = b'1' + b'0' * 4300
        directory = mounted_directory('LONG', width=4301, slot_count=1)
        register_source(self.sources, 'LONG', 4301, 1, [0])
        plan = plan_single(self.sources, 'LONG', [0])
        receipt = publish_single(self.issues, plan, self._execute(plan, RecordedStation(directory, (frame,))))
        volume = read_issue(open_issues(self.store_path), receipt.number)
        self.assertTrue(volume.receipt.details.total == 10 ** 4300)
        self.assertEqual(volume.record_at(0), frame)

    def test_binding_rejects_other_media(self) -> None:
        plan = plan_single(self.sources, 'SC-17', [0])
        station = RecordedStation(mounted_directory('OTHER'))
        with self.assertRaises(MountMismatch):
            self._execute(plan, station)
        self.assertEqual(station.events, [])
        self.assertEqual(station.candidates, [])
        self.assertEqual(self.issues.list_numbers(), ())

    def test_same_id_other_directory(self) -> None:
        plan = plan_single(self.sources, 'SC-17', [0, 2])
        station = RecordedStation(mounted_directory(positions=[0, 2, 4]))
        with self.assertRaises(MountMismatch):
            self._execute(plan, station)
        self.assertEqual(station.events, [])
        self.assertEqual(station.candidates, [])
        self.assertEqual(self.issues.list_numbers(), ())

    def test_read_failure_discards_candidate(self) -> None:
        old = self._publish_old_issue()
        plan = plan_single(self.sources, 'SC-17', [0, 2])
        station = RecordedStation(self.directory, fail_at=1)
        with self.assertRaisesRegex(ExecutionError, 'injected read failure at slot 1'):
            self._execute(plan, station)
        self.assertEqual(station.events, [('read', 'SC-17', 0), ('write', 0, b'000'), ('read', 'SC-17', 1)])
        self.assertEqual(station.candidates[0].state, 'discarded')
        self.assertTrue(station.outputs[0].closed)
        self._assert_old_issue_only(old)

    def test_discard_is_terminal(self) -> None:
        old = self._publish_old_issue()
        plan = plan_single(self.sources, 'SC-17', [0, 1])
        station = RecordedStation(self.directory, fail_at=1)
        with self.assertRaises(ExecutionError):
            self._execute(plan, station)
        candidate = station.candidates[0]
        with self.assertRaises(CandidateStateError):
            candidate.append(SourceFrame('SC-17', 1, b'-02'))
        with self.assertRaises(CandidateStateError):
            candidate.complete()
        with self.assertRaises(CandidateStateError):
            candidate.snapshot()
        with self.assertRaises(PublishError):
            publish_single(self.issues, plan, candidate)
        self.assertEqual(candidate.state, 'discarded')
        self.assertTrue(station.outputs[0].closed)
        self._assert_old_issue_only(old)

    def test_short_frame(self) -> None:
        old = self._publish_old_issue()
        plan = plan_single(self.sources, 'SC-17', [0, 2])
        station = RecordedStation(self.directory, (b'000', b'1', b'   '))
        with self.assertRaisesRegex(ExecutionError, 'slot 1: expected a frame of 3 bytes'):
            self._execute(plan, station)
        self.assertEqual(station.events, [('read', 'SC-17', 0), ('write', 0, b'000'), ('read', 'SC-17', 1)])
        self.assertEqual(station.candidates[0].state, 'discarded')
        self.assertTrue(station.outputs[0].closed)
        self._assert_old_issue_only(old)

    def test_bad_record(self) -> None:
        old = self._publish_old_issue()
        plan = plan_single(self.sources, 'SC-17', [0, 2])
        station = RecordedStation(self.directory, (b'000', b'1 2', b'   '))
        with self.assertRaisesRegex(ExecutionError, 'slot 1: expected ASCII decimal digits'):
            self._execute(plan, station)
        self.assertEqual(station.events, [('read', 'SC-17', 0), ('write', 0, b'000'), ('read', 'SC-17', 1)])
        self.assertEqual(station.candidates[0].state, 'discarded')
        self._assert_old_issue_only(old)

    def test_write_failure(self) -> None:
        old = self._publish_old_issue()
        plan = plan_single(self.sources, 'SC-17', [0, 1, 2])
        station = RecordedStation(self.directory, short_write_at=1)
        with self.assertRaisesRegex(ExecutionError, 'short target write at record 1'):
            self._execute(plan, station)
        self.assertEqual(station.events, [('read', 'SC-17', 0), ('write', 0, b'000'), ('read', 'SC-17', 1), ('short_write', 1)])
        self.assertEqual(station.candidates[0].state, 'discarded')
        self.assertTrue(station.outputs[0].closed)
        self._assert_old_issue_only(old)

    def test_seal_failure(self) -> None:
        old = self._publish_old_issue()
        plan = plan_single(self.sources, 'SC-17', [0])
        station = RecordedStation(self.directory, fail_seal=True)
        with self.assertRaisesRegex(ExecutionError, 'injected seal failure'):
            self._execute(plan, station)
        self.assertEqual(station.events, [('read', 'SC-17', 0), ('write', 0, b'000')])
        self.assertEqual(station.candidates[0].state, 'discarded')
        self.assertTrue(station.outputs[0].closed)
        self._assert_old_issue_only(old)

    def test_incomplete_candidate_cannot_seal(self) -> None:
        plan = plan_single(self.sources, 'SC-17', [0, 1])
        candidate = CandidateVolume(plan)
        candidate.append(SourceFrame('SC-17', 0, b'000'))
        with self.assertRaises(CandidateStateError):
            candidate.complete()
        with self.assertRaises(PublishError):
            publish_single(self.issues, plan, candidate)
        self.assertEqual(candidate.state, 'discarded')
        self.assertEqual(self.issues.list_numbers(), ())

    def test_origin_mismatch_rejects_publish(self) -> None:
        plan = plan_single(self.sources, 'SC-17', [0, 2])
        candidate = CandidateVolume(plan)
        candidate.append(SourceFrame('OTHER', 0, b'000'))
        candidate.append(SourceFrame('SC-17', 2, b'   '))
        candidate.complete()
        with self.assertRaises(PublishError) as caught:
            publish_single(self.issues, plan, candidate)
        self.assertIsInstance(caught.exception.__cause__, OriginMismatch)
        self.assertEqual(candidate.state, 'discarded')
        self.assertEqual(self.issues.list_numbers(), ())

    def test_same_body_swap(self) -> None:
        plan = plan_single(self.sources, 'SC-17', [1, 3])
        candidate = CandidateVolume(plan)
        candidate.append(SourceFrame('SC-17', 3, b'007'))
        candidate.append(SourceFrame('SC-17', 1, b'007'))
        self.assertEqual(candidate.complete().body, b'007007')
        with self.assertRaisesRegex(PublishError, 'record 0 has origin'):
            publish_single(self.issues, plan, candidate)
        self.assertEqual(candidate.state, 'discarded')
        self.assertEqual(self.issues.list_numbers(), ())

    def test_publish_requires_same_bound_plan(self) -> None:
        original = plan_single(self.sources, 'SC-17', [0])
        different = plan_single(self.sources, 'SC-17', [1])
        candidate = self._execute(original, RecordedStation(self.directory))
        with self.assertRaisesRegex(PublishError, 'different bound plan'):
            publish_single(self.issues, different, candidate)
        self.assertEqual(candidate.state, 'discarded')
        self.assertEqual(self.issues.list_numbers(), ())

    def test_commit_failure(self) -> None:
        old = self._publish_old_issue()
        with closing(sqlite3.connect(self.store_path)) as connection, connection:
            connection.execute('''CREATE TRIGGER interrupt_ledger BEFORE INSERT ON ledgers
                WHEN EXISTS (SELECT 1 FROM issues WHERE id = NEW.issue_id)
                BEGIN SELECT RAISE(ABORT, 'injected ledger write failure'); END''')
        plan = plan_single(self.sources, 'SC-17', [0, 2])
        candidate = self._execute(plan, RecordedStation(self.directory))
        with self.assertRaisesRegex(PublishError, 'injected ledger write failure'):
            publish_single(self.issues, plan, candidate)
        self.assertEqual(candidate.state, 'discarded')
        with self.assertRaises(PublishError):
            publish_single(self.issues, plan, candidate)
        self._assert_old_issue_only(old)
        with closing(sqlite3.connect(self.store_path)) as connection:
            self.assertEqual(connection.execute('SELECT issue_id, target_index FROM origins').fetchall(), [(1, 0)])
            self.assertEqual(connection.execute('SELECT issue_id FROM ledgers').fetchall(), [(1,)])

    def test_station_retention_capacity(self) -> None:
        events = []
        frame = SourceFrame('SC-17', 0, b'000')
        with self.assertRaises(OSError):
            RetentionSlots(0, events).hold(frame)
        rack = RetentionSlots(1, events)
        slot = rack.hold(frame)
        with self.assertRaises(OSError):
            rack.hold(SourceFrame('SC-17', 1, b'-02'))
        self.assertEqual(rack.get(slot), frame)
        rack.release(slot)
        with self.assertRaises(KeyError):
            rack.get(slot)
        self.assertEqual(rack.peak, 1)
        self.assertEqual(events, [('hold', 0, 'SC-17', 0), ('get', 0, 'SC-17', 0), ('release', 0, 'SC-17', 0)])
