from dataclasses import dataclass


def _require_target_index(index: int, length: int) -> None:
    if type(index) is not int or not 0 <= index < length:
        raise IndexError(f'target index out of range: {index!r}')


@dataclass(frozen=True, slots=True)
class IssuedOrigin:
    target_index: int
    source_volume_id: str
    source_position: int


@dataclass(frozen=True, slots=True)
class ReceiptDetails:
    total_records: int
    valid_records: int
    missing_records: int
    zero_records: int
    total: int


@dataclass(frozen=True, slots=True)
class IssueReceipt:
    number: str
    origins: tuple[IssuedOrigin, ...]
    details: ReceiptDetails

    def origin_at(self, index: int) -> IssuedOrigin:
        _require_target_index(index, len(self.origins))
        return self.origins[index]


@dataclass(frozen=True, slots=True)
class IssuedVolume:
    width: int
    body: bytes
    receipt: IssueReceipt

    def record_at(self, index: int) -> bytes:
        _require_target_index(index, self.receipt.details.total_records)
        start = index * self.width
        return self.body[start:start + self.width]
