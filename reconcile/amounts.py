from dataclasses import dataclass

from media.record import WrittenRecord
from source.layout import RecordLayout, decode_record


@dataclass(frozen=True, slots=True)
class Amounts:
    total_records: int
    valid_records: int
    missing_records: int
    zero_records: int
    total: int


def count_amounts(layout: RecordLayout, body: bytes, records: tuple[WrittenRecord, ...]) -> Amounts:
    valid = missing = zeros = total = 0
    for record in records:
        value = decode_record(layout, body[record.byte_range(layout.width)])
        if value is None:
            missing += 1
        else:
            valid += 1
            zeros += value == 0
            total += value
    return Amounts(len(records), valid, missing, zeros, total)
