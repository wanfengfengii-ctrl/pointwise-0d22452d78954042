import re
from dataclasses import dataclass


class RecordFormatError(ValueError):
    """A physical frame does not conform to the registered V1 layout."""


@dataclass(frozen=True, slots=True)
class RecordLayout:
    width: int
    version: int = 1

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != 1:
            raise ValueError('only V1 record layouts are supported')
        if type(self.width) is not int or self.width <= 0:
            raise ValueError('record width must be a positive integer')


def validate_layout(layout: RecordLayout, slot_count: int) -> None:
    if not isinstance(layout, RecordLayout):
        raise ValueError('a V1 record layout is required')
    if type(slot_count) is not int or slot_count < 0:
        raise ValueError('slot count must be a nonnegative integer')


_INTEGER_FRAME = re.compile(rb' *(-?[0-9]+)')


def decode_record(layout: RecordLayout, frame: bytes) -> int | None:
    if not isinstance(frame, bytes) or len(frame) != layout.width:
        raise RecordFormatError(f'expected a frame of {layout.width} bytes')
    if not frame.strip(b' '):
        return None
    match = _INTEGER_FRAME.fullmatch(frame)
    if match is None:
        raise RecordFormatError('expected ASCII decimal digits or an all-space frame')
    token = match[1]
    negative = token.startswith(b'-')
    digits = token[1:] if negative else token
    value = 0
    # Small conversions preserve precision for widths beyond Python's decimal conversion limit.
    for offset in range(0, len(digits), 9):
        block = digits[offset:offset + 9]
        value = value * 10 ** len(block) + int(block)
    return -value if negative else value
