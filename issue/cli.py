import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from media import execute_single, execute_transfer
from plan import plan_single, plan_transfer
from plan.binding import MountMismatch
from source import RecordLayout, SourceCatalog, SourceDirectory, open_frame_device, register_source

from . import IssueCatalog, IssueStore, publish_single, read_issue
from .receipt import IssueReceipt


class CapacityRack:
    """A plain on-process retention rack for the console entry point."""

    def __init__(self, capacity: int) -> None:
        if type(capacity) is not int or capacity < 0:
            raise ValueError('rack capacity must be a nonnegative integer')
        self.capacity = capacity
        self.peak = 0
        self._held: dict[int, object] = {}

    def hold(self, frame):
        for slot in range(self.capacity):
            if slot not in self._held:
                self._held[slot] = frame
                self.peak = max(self.peak, len(self._held))
                return slot
        raise OSError('retention rack is full')

    def get(self, slot: int):
        return self._held[slot]

    def release(self, slot: int) -> None:
        del self._held[slot]


def _load_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def _directory(data) -> SourceDirectory:
    if not isinstance(data, dict):
        raise ValueError('directory must be a JSON object')
    return SourceDirectory(
        data['volume_id'],
        RecordLayout(data['width'], data.get('version', 1)),
        data['slot_count'],
        data['positions'],
    )


def _open_catalog(path: Path) -> IssueCatalog:
    store = IssueStore(path)
    store.initialize()
    return IssueCatalog(store)


def _decimal_text(value: int) -> str:
    magnitude = abs(value)
    chunks = []
    while magnitude >= 10 ** 9:
        magnitude, remainder = divmod(magnitude, 10 ** 9)
        chunks.append(f'{remainder:09d}')
    sign = '-' if value < 0 else ''
    return sign + str(magnitude) + ''.join(reversed(chunks))


def _receipt_data(receipt: IssueReceipt) -> dict:
    details = asdict(receipt.details)
    details['total'] = _decimal_text(receipt.details.total)
    return {
        'number': receipt.number,
        'origins': [asdict(origin) for origin in receipt.origins],
        'details': details,
    }


def _deliver(args: argparse.Namespace) -> None:
    registration = _directory(_load_json(args.directory))
    mounted = _directory(_load_json(args.mounted_directory))
    source_catalog = SourceCatalog()
    volume_id = register_source(
        source_catalog, registration.volume_id, registration.layout.width,
        registration.slot_count, registration.positions, version=registration.layout.version,
    )
    plan = plan_single(source_catalog, volume_id, json.loads(args.positions))
    catalog = _open_catalog(args.store)
    with closing(open_frame_device(args.source, mounted)) as device:
        candidate = execute_single(plan, device)
    receipt = publish_single(catalog, plan, candidate)
    print(json.dumps(_receipt_data(receipt), ensure_ascii=False))


def _retrieve(args: argparse.Namespace) -> None:
    volume = read_issue(_open_catalog(args.store), args.number)
    if args.output is not None:
        if args.output.resolve() == args.store.resolve():
            raise ValueError('target output path must differ from the issue store')
        # Create a new import copy while preserving any file at the requested path.
        with args.output.open('xb') as output:
            output.write(volume.body)
    result = _receipt_data(volume.receipt)
    result.update(width=volume.width, body_hex=volume.body.hex())
    print(json.dumps(result, ensure_ascii=False))


def _run_shift_command(*argv: str) -> dict:
    result = subprocess.run(
        [sys.executable, '-B', '-m', 'issue.cli', *argv], capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f'shift command exited with {result.returncode}')
    return json.loads(result.stdout)


def _demo(_args: argparse.Namespace) -> None:
    sample = _load_json(Path(__file__).resolve().parents[1] / 'examples' / 'shift.json')
    with TemporaryDirectory(prefix='meter-shift-') as temporary:
        work = Path(temporary)
        registration = work / 'registration.json'
        mounted = work / 'mounted.json'
        source = work / 'source.bin'
        store = work / 'issues.db'
        output = work / 'import.bin'
        registration.write_text(json.dumps(sample['registration']), encoding='utf-8')
        mounted.write_text(json.dumps(sample['mounted']), encoding='utf-8')
        source.write_bytes(''.join(sample['frames']).encode('ascii'))
        receipt = _run_shift_command(
            'deliver', '--store', str(store), '--directory', str(registration),
            '--mounted-directory', str(mounted), '--source', str(source), '--positions',
            json.dumps(sample['selection']),
        )
        for path in (source, mounted, registration):
            path.unlink()
        pickup = _run_shift_command('retrieve', '--store', str(store), receipt['number'], '--output', str(output))
        expected_body = b'000' + b'   ' + b' 07'
        expected_details = dict(total_records=3, valid_records=2, missing_records=1, zero_records=1, total='7')
        expected_origins = [
            dict(target_index=index, source_volume_id='SC-17', source_position=position)
            for index, position in enumerate((0, 2, 4))
        ]
        if receipt['details'] != expected_details or receipt['origins'] != expected_origins:
            raise RuntimeError('demo receipt differs from the independent shift ledger')
        if pickup != dict(receipt, width=3, body_hex=expected_body.hex()) or output.read_bytes() != expected_body:
            raise RuntimeError('demo pickup differs from the signed shift volume')
        print(json.dumps({'delivery': receipt, 'pickup': pickup}, ensure_ascii=False))


def _dual_sample() -> dict:
    return _load_json(Path(__file__).resolve().parents[1] / 'examples' / 'dual_shift.json')


def _dual_directories(sample) -> list[SourceDirectory]:
    directories = []
    for source in sample['sources']:
        data = source['directory']
        directories.append(SourceDirectory(
            data['volume_id'], RecordLayout(data['width'], data.get('version', 1)),
            data['slot_count'], data['positions'],
        ))
    return directories


def _dual_targets(sample) -> list:
    return [(name, tuple(tuple(origin) for origin in origins)) for name, origins in sample['targets']]


def _dual_devices(directories, sample, work: Path):
    devices = []
    for index, (directory, source) in enumerate(zip(directories, sample['sources'])):
        path = work / f'source-{index}.bin'
        path.write_bytes(b''.join(frame.encode('ascii') for frame in source['frames']))
        devices.append(open_frame_device(path, directory))
    return devices


def _deliver_dual(args: argparse.Namespace) -> None:
    if not (len(args.directory) == len(args.mounted_directory) == len(args.source)):
        raise ValueError('two registered directories, mounted directories and sources are required')
    directories = []
    mounted_directories = []
    for path in args.directory:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        directories.append(SourceDirectory(
            data['volume_id'], RecordLayout(data['width'], data.get('version', 1)),
            data['slot_count'], data['positions'],
        ))
    for path in args.mounted_directory:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        mounted_directories.append(SourceDirectory(
            data['volume_id'], RecordLayout(data['width'], data.get('version', 1)),
            data['slot_count'], data['positions'],
        ))
    for registered, mounted in zip(directories, mounted_directories):
        if mounted != registered:
            raise MountMismatch(f'mounted directory {mounted.volume_id!r} differs from its registration')
    targets = [(name, tuple(tuple(origin) for origin in origins))
               for name, origins in json.loads(args.targets)]
    capacity = json.loads(args.capacity)
    plan = plan_transfer(directories, targets, capacity)
    if plan == 'NO_PLAN':
        print(json.dumps({'plan': 'NO_PLAN', 'capacity': capacity}, ensure_ascii=False))
        return
    catalog = _open_catalog(args.store)
    rack = CapacityRack(plan.capacity)
    devices = []
    try:
        for path, directory in zip(args.source, mounted_directories):
            devices.append(open_frame_device(path, directory))
        receipts = execute_transfer(catalog, plan, devices, targets, rack)
    finally:
        for device in devices:
            device.close()
    print(json.dumps(
        {'plan': 'EXECUTED', 'capacity': plan.capacity, 'peak_slots': rack.peak,
         'receipts': [_receipt_data(receipt) for receipt in receipts]},
        ensure_ascii=False,
    ))


def _demo_dual(_args: argparse.Namespace) -> None:
    sample = _dual_sample()
    expected = sample['expected']
    with TemporaryDirectory(prefix='meter-dual-') as temporary:
        work = Path(temporary)
        store = work / 'issues.db'
        directories = _dual_directories(sample)
        targets = _dual_targets(sample)

        if plan_transfer(directories, targets, 0) != expected['capacity_zero']:
            raise RuntimeError('zero-capacity plan is not the expected NO_PLAN')

        plan = plan_transfer(directories, targets, sample['capacity'])
        if plan == 'NO_PLAN':
            raise RuntimeError('sample plan unexpectedly has no solution')
        catalog = _open_catalog(store)
        rack = CapacityRack(plan.capacity)
        devices = _dual_devices(directories, sample, work)
        try:
            receipts = execute_transfer(catalog, plan, devices, targets, rack)
        finally:
            for device in devices:
                device.close()
        if rack.peak != expected['peak_slots']:
            raise RuntimeError('retention peak differs from the sample')

        # Unmount both sources, reopen the store, and pick each formal number up on its own.
        for path in work.glob('source-*.bin'):
            path.unlink()
        reopened = _open_catalog(store)
        pickups = []
        for index, receipt in enumerate(receipts):
            volume = read_issue(reopened, receipt.number)
            output = work / f'import-{index}.bin'
            output.write_bytes(volume.body)
            data = _receipt_data(volume.receipt)
            data.update(width=volume.width, body_hex=volume.body.hex())
            pickups.append(data)
            if data['body_hex'] != expected['body_hex'][index]:
                raise RuntimeError(f'target {index} body differs from the sample')
            details = data['details']
            wanted = dict(expected['details_each'])
            wanted['total'] = _decimal_text(int(wanted['total']))
            if details != wanted:
                raise RuntimeError(f'target {index} ledger differs from the sample')

        print(json.dumps({
            'zero_capacity': expected['capacity_zero'],
            'capacity': plan.capacity,
            'peak_slots': rack.peak,
            'reads': [[index, position] for index, position in plan.reads],
            'pickups': pickups,
            'numbers': list(reopened.list_numbers()),
        }, ensure_ascii=False))


def main(argv: Sequence[str] | None = None) -> int:

    parser = argparse.ArgumentParser(description='V1 fixed-width meter record exchange')
    commands = parser.add_subparsers(dest='command', required=True)
    deliver = commands.add_parser('deliver', help='extract and publish one exchange volume')
    deliver.add_argument('--store', type=Path, required=True)
    deliver.add_argument('--directory', type=Path, required=True)
    deliver.add_argument('--mounted-directory', type=Path, required=True)
    deliver.add_argument('--source', type=Path, required=True)
    deliver.add_argument('--positions', required=True, help='JSON array of source positions')
    deliver.set_defaults(handler=_deliver)
    retrieve = commands.add_parser('retrieve', help='pick up a volume by its exact issue number')
    retrieve.add_argument('--store', type=Path, required=True)
    retrieve.add_argument('number')
    retrieve.add_argument('--output', type=Path, help='create a new raw frame output file')
    retrieve.set_defaults(handler=_retrieve)
    commands.add_parser('demo', help='run the day and night shift sample').set_defaults(handler=_demo)
    dual = commands.add_parser('deliver-dual', help='plan and publish a two-source two-target shift')
    dual.add_argument('--store', type=Path, required=True)
    dual.add_argument('--directory', type=Path, action='append', required=True,
                      help='one registered directory per source, in plan order (twice)')
    dual.add_argument('--mounted-directory', type=Path, action='append', required=True,
                      help='one independent mounted directory per source, in plan order (twice)')
    dual.add_argument('--source', type=Path, action='append', required=True,
                      help='one raw source file per source, in plan order (twice)')
    dual.add_argument('--targets', required=True,
                      help='JSON array of (name, [[volume_id, position], ...]) in delivery order')
    dual.add_argument('--capacity', required=True, help='JSON integer retention capacity')
    dual.set_defaults(handler=_deliver_dual)
    commands.add_parser(
        'demo-dual', help='run the two-source, two-target sample'
    ).set_defaults(handler=_demo_dual)
    args = parser.parse_args(argv)
    try:
        args.handler(args)
    except (OSError, ValueError, LookupError, RuntimeError) as error:
        print(f'{args.command} failed ({type(error).__name__}): {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
