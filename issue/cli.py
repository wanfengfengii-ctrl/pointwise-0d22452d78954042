import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from media import execute_single
from plan import plan_single
from source import RecordLayout, SourceCatalog, SourceDirectory, open_frame_device, register_source

from . import IssueCatalog, IssueStore, publish_single, read_issue
from .receipt import IssueReceipt


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
    args = parser.parse_args(argv)
    try:
        args.handler(args)
    except (OSError, ValueError, LookupError, RuntimeError) as error:
        print(f'{args.command} failed ({type(error).__name__}): {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
