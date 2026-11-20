from dataclasses import dataclass

from media.record import WrittenRecord

from .amounts import Amounts


@dataclass(frozen=True, slots=True)
class ReconciliationManifest:
    records: tuple[WrittenRecord, ...]
    amounts: Amounts


def build_manifest(records: tuple[WrittenRecord, ...], amounts: Amounts) -> ReconciliationManifest:
    if amounts.total_records != len(records):
        raise ValueError('ledger record count differs from the verified write count')
    if amounts.total_records != amounts.valid_records + amounts.missing_records:
        raise ValueError('valid and missing counts do not add up to the record count')
    if amounts.missing_records < 0 or not 0 <= amounts.zero_records <= amounts.valid_records:
        raise ValueError('invalid missing or measured zero count')
    return ReconciliationManifest(records, amounts)
