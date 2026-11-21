import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from issue import read_issue
from tests.fixtures import open_issues


class TestDualCli(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.store = self.work / 'issues.db'

    def _write(self, name: str, payload) -> Path:
        path = self.work / name
        path.write_text(json.dumps(payload), encoding='utf-8')
        return path

    def _layout(self):
        directories = [
            self._write('reg0.json', dict(volume_id='LEFT', version=1, width=3, slot_count=1, positions=[0])),
            self._write('reg1.json', dict(volume_id='RIGHT', version=1, width=3, slot_count=2, positions=[0, 1])),
        ]
        mounted = [
            self._write('mnt0.json', dict(volume_id='LEFT', version=1, width=3, slot_count=1, positions=[0])),
            self._write('mnt1.json', dict(volume_id='RIGHT', version=1, width=3, slot_count=2, positions=[0, 1])),
        ]
        (self.work / 's0.bin').write_bytes(b'000')
        (self.work / 's1.bin').write_bytes(b'-02   ')
        targets = [['meter', [['RIGHT', 0], ['RIGHT', 1], ['LEFT', 0]]],
                   ['quality', [['RIGHT', 1], ['RIGHT', 0], ['LEFT', 0]]]]
        return directories, mounted, targets

    def _run(self, directories, mounted, targets, capacity: str):
        argv = [sys.executable, '-B', '-m', 'issue.cli', 'deliver-dual', '--store', str(self.store)]
        for path in directories:
            argv += ['--directory', str(path)]
        for path in mounted:
            argv += ['--mounted-directory', str(path)]
        for name in ('s0.bin', 's1.bin'):
            argv += ['--source', str(self.work / name)]
        argv += ['--targets', json.dumps(targets), '--capacity', capacity]
        return subprocess.run(argv, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)

    def test_zero_capacity_reports_no_plan_without_issues(self) -> None:
        directories, mounted, targets = self._layout()
        result = self._run(directories, mounted, targets, '0')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'plan': 'NO_PLAN', 'capacity': 0})
        self.assertEqual(open_issues(self.store).list_numbers(), ())

    def test_one_slot_is_executed_and_pickable(self) -> None:
        directories, mounted, targets = self._layout()
        result = self._run(directories, mounted, targets, '1')
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload['plan'], 'EXECUTED')
        self.assertEqual(payload['peak_slots'], 1)
        numbers = [receipt['number'] for receipt in payload['receipts']]
        self.assertEqual(len(set(numbers)), 2)
        reopened = open_issues(self.store)
        self.assertEqual(reopened.list_numbers(), tuple(numbers))
        self.assertEqual(
            [read_issue(reopened, number).body.hex() for number in numbers],
            ['2d3032202020303030', '2020202d3032303030'],
        )

    def test_mismatched_mount_is_refused_before_io(self) -> None:
        directories, mounted, targets = self._layout()
        mounted[0] = self._write(
            'mnt0.json', dict(volume_id='LEFT', version=1, width=3, slot_count=1, positions=[0], extra=1)
        )
        bad = json.loads(mounted[0].read_text(encoding='utf-8'))
        bad['volume_id'] = 'OTHER'
        mounted[0] = self._write('mnt0.json', bad)
        result = self._run(directories, mounted, targets, '1')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertIn('MountMismatch', result.stderr)
        self.assertEqual(open_issues(self.store).list_numbers(), ())


if __name__ == '__main__':
    unittest.main()
