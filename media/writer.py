from contextlib import suppress
from io import BytesIO
from typing import BinaryIO


class WriteError(OSError):
    """A target frame or sealed target could not be written completely."""


class SequentialWriter:
    def __init__(self, width: int, stream: BinaryIO | None = None) -> None:
        if type(width) is not int or width <= 0:
            raise ValueError('target width must be a positive integer')
        self._width = width
        self._stream = BytesIO() if stream is None else stream
        self._count = 0
        self._open = True

    @property
    def width(self) -> int:
        return self._width

    def _require_open(self) -> None:
        if not self._open:
            raise WriteError('target writer is closed')

    def append(self, body: bytes) -> int:
        self._require_open()
        try:
            if not isinstance(body, bytes) or len(body) != self._width:
                raise WriteError('target append requires exactly one frame')
            if self._stream.write(body) != self._width:
                raise WriteError(f'short target write at record {self._count}')
        except Exception:
            self.abort()
            raise
        target_index = self._count
        self._count += 1
        return target_index

    def seal(self) -> bytes:
        self._require_open()
        try:
            self._stream.flush()
            self._stream.seek(0)
            body = self._stream.read()
            if not isinstance(body, bytes) or len(body) != self._width * self._count:
                raise WriteError('sealed target does not contain all appended frames')
            self._stream.close()
        except Exception:
            self.abort()
            raise
        self._open = False
        return body

    def abort(self) -> None:
        if self._open:
            self._open = False
            with suppress(Exception):
                self._stream.close()
