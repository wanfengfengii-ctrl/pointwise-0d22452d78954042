import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from issue import IssueNotFound, publish_single, read_issue
from issue.receipt import IssuedOrigin, ReceiptDetails
from media import execute_single
from plan import plan_single
from source import SourceCatalog, register_source
from tests.fixtures import RecordedStation, mounted_directory, open_issues


class TestRetrieval(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.store_path = self.work / 'issues.db'
        self.issues = open_issues(self.store_path)

    def _cli(self, *arguments: str):
        return subprocess.run(
            [sys.executable, '-B', '-m', 'issue.cli', *arguments],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=False,
        )

    def _deliver_args(self):
        registration = self.work / 'registered.json'
        mounted = self.work / 'mounted.json'
        source = self.work / 'source.bin'
        description = dict(volume_id='SC-17', version=1, width=3, slot_count=3, positions=[0, 1, 2])
        for path in (registration, mounted):
            path.write_text(json.dumps(description), encoding='utf-8')
        source.write_bytes(b' 07' + b'   ' + b'007')
        argv = (
            'deliver', '--store', str(self.store_path), '--directory', str(registration),
            '--mounted-directory', str(mounted), '--source', str(source), '--positions', '[0,1,2]',
        )
        return argv, (registration, mounted, source)

    def _publish(self, volume_id: str, frames: tuple[bytes, ...], selection: list[int]):
        directory = mounted_directory(volume_id, slot_count=len(frames))
        sources = SourceCatalog()
        register_source(sources, volume_id, 3, len(frames), list(range(len(frames))))
        plan = plan_single(sources, volume_id, selection)
        station = RecordedStation(directory, frames)
        candidate = execute_single(plan, station.source, candidate_factory=station.open_candidate)
        return publish_single(self.issues, plan, candidate)

    def test_reopen_after_unmount(self) -> None:
        argv, paths = self._deliver_args()
        delivery = self._cli(*argv)
        self.assertEqual(delivery.returncode, 0, delivery.stderr)
        receipt = json.loads(delivery.stdout)
        self.assertEqual(receipt['number'], 'ISS-000001')
        for path in paths:
            path.unlink()
        output = self.work / 'import.bin'
        pickup = self._cli('retrieve', '--store', str(self.store_path), receipt['number'], '--output', str(output))
        self.assertEqual(pickup.returncode, 0, pickup.stderr)
        picked = json.loads(pickup.stdout)
        expected = b' 07' + b'   ' + b'007'
        self.assertEqual(bytes.fromhex(picked['body_hex']), expected)
        self.assertEqual(picked['width'], 3)
        self.assertEqual(output.read_bytes(), expected)
        self.assertEqual(receipt['origins'], [
            dict(target_index=0, source_volume_id='SC-17', source_position=0),
            dict(target_index=1, source_volume_id='SC-17', source_position=1),
            dict(target_index=2, source_volume_id='SC-17', source_position=2),
        ])
        self.assertEqual(receipt['details'], dict(total_records=3, valid_records=2, missing_records=1, zero_records=0, total='14'))
        self.assertEqual(picked['origins'], receipt['origins'])
        self.assertEqual(picked['details'], receipt['details'])
        self.assertEqual(read_issue(open_issues(self.store_path), receipt['number']).body, expected)

    def test_sealed_bytes(self) -> None:
        receipt = self._publish('PADDED', (b' 07', b'007', b'  7'), [0, 1, 2])
        volume = read_issue(open_issues(self.store_path), receipt.number)
        self.assertEqual([volume.record_at(index) for index in range(3)], [b' 07', b'007', b'  7'])
        self.assertEqual(volume.receipt.origins, (
            IssuedOrigin(0, 'PADDED', 0), IssuedOrigin(1, 'PADDED', 1), IssuedOrigin(2, 'PADDED', 2)
        ))
        self.assertEqual(volume.receipt.details, ReceiptDetails(3, 3, 0, 0, 21))
        with self.assertRaises(IndexError):
            volume.record_at(-1)
        with self.assertRaises(IndexError):
            volume.receipt.origin_at(3)

    def test_unknown_issue(self) -> None:
        first = self._publish('DAY', (b'000',), [0])
        second = self._publish('NIGHT', (b'-02',), [0])
        catalog = open_issues(self.store_path)
        self.assertEqual(catalog.list_numbers(), ('ISS-000001', 'ISS-000002'))
        self.assertEqual(read_issue(catalog, second.number).body, b'-02')
        self.assertEqual(read_issue(catalog, first.number).body, b'000')
        for number in ('ISS-000003', 'CANDIDATE-1', 'ISS-000000', 'ISS-0000001', 'ISS-9999999999999999999'):
            with self.subTest(number=number), self.assertRaises(IssueNotFound):
                read_issue(catalog, number)
        result = self._cli('retrieve', '--store', str(self.store_path), 'ISS-000003')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertIn('IssueNotFound', result.stderr)
        self.assertEqual(catalog.list_numbers(), (first.number, second.number))

    def test_reinitialize(self) -> None:
        first = self._publish('DAY', (b' 07', b'   '), [0, 1])
        before_append = read_issue(open_issues(self.store_path), first.number)
        second = self._publish('ADDED', (b'-02', b'000'), [0, 1])
        appended = read_issue(self.issues, second.number)
        self.assertEqual(before_append.body, b' 07   ')
        self.assertEqual(before_append.receipt.details, ReceiptDetails(2, 1, 1, 0, 7))
        self.assertEqual(appended.body, b'-02000')
        self.assertEqual(appended.receipt.details, ReceiptDetails(2, 2, 0, 1, -2))
        for reopen in range(2):
            with self.subTest(reopen=reopen):
                opened = open_issues(self.store_path)
                self.assertEqual(opened.list_numbers(), (first.number, second.number))
                self.assertEqual(read_issue(opened, first.number), before_append)
                self.assertEqual(read_issue(opened, second.number), appended)

    def test_cli_rejects_mismatched_mount(self) -> None:
        argv, (_, mounted, _) = self._deliver_args()
        description = json.loads(mounted.read_text(encoding='utf-8'))
        description['volume_id'] = 'OTHER'
        mounted.write_text(json.dumps(description), encoding='utf-8')
        result = self._cli(*argv)
        self.assertEqual(result.returncode, 1)
        self.assertIn('MountMismatch', result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertEqual(open_issues(self.store_path).list_numbers(), ())
