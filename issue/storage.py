import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

from .receipt import IssuedOrigin, ReceiptDetails


_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS issues (
        id INTEGER PRIMARY KEY,
        width_hex TEXT NOT NULL,
        body BLOB NOT NULL
    )''',
    '''CREATE TABLE IF NOT EXISTS origins (
        issue_id INTEGER NOT NULL REFERENCES issues(id),
        target_index INTEGER NOT NULL CHECK(target_index >= 0),
        source_volume_id TEXT NOT NULL,
        source_position_hex TEXT NOT NULL,
        PRIMARY KEY (issue_id, target_index)
    )''',
    '''CREATE TABLE IF NOT EXISTS ledgers (
        issue_id INTEGER PRIMARY KEY REFERENCES issues(id),
        total_records INTEGER NOT NULL,
        valid_records INTEGER NOT NULL,
        missing_records INTEGER NOT NULL,
        zero_records INTEGER NOT NULL,
        total_hex TEXT NOT NULL,
        CHECK (valid_records >= zero_records AND zero_records >= 0 AND missing_records >= 0),
        CHECK (total_records = valid_records + missing_records)
    )''',
)


class IssueStoreError(RuntimeError):
    """The stored issue could not be initialized, committed or read."""


@dataclass(frozen=True, slots=True)
class StoredIssue:
    width: int
    body: bytes
    origins: tuple[IssuedOrigin, ...]
    details: ReceiptDetails


def _insert_issue_row(
    connection: sqlite3.Connection, width: int, body: bytes,
    origins: tuple[IssuedOrigin, ...], details: ReceiptDetails,
) -> int:
    cursor = connection.execute(
        'INSERT INTO issues (width_hex, body) VALUES (?, ?)', (format(width, 'x'), body)
    )
    issue_id = cursor.lastrowid
    if issue_id is None:
        raise IssueStoreError('issue store did not assign an issue number')
    connection.executemany(
        '''INSERT INTO origins (issue_id, target_index, source_volume_id, source_position_hex)
           VALUES (?, ?, ?, ?)''',
        (
            (issue_id, origin.target_index, origin.source_volume_id, format(origin.source_position, 'x'))
            for origin in origins
        ),
    )
    connection.execute(
        '''INSERT INTO ledgers (issue_id, total_records, valid_records, missing_records,
                                zero_records, total_hex) VALUES (?, ?, ?, ?, ?, ?)''',
        (
            issue_id, details.total_records, details.valid_records, details.missing_records,
            details.zero_records, format(details.total, 'x'),
        ),
    )
    return issue_id


class IssueStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        if str(self._path) == ':memory:':
            raise ValueError('an on-disk issue store path is required')

    @contextmanager
    def _transaction(self):
        with closing(sqlite3.connect(self._path)) as connection, connection:
            connection.execute('PRAGMA foreign_keys = ON')
            connection.execute('BEGIN')
            yield connection

    def initialize(self) -> None:
        try:
            with self._transaction() as connection:
                for statement in _SCHEMA:
                    connection.execute(statement)
        except sqlite3.Error as error:
            raise IssueStoreError(f'cannot initialize the issue store: {error}') from error

    def insert_issue(
        self, width: int, body: bytes, origins: tuple[IssuedOrigin, ...], details: ReceiptDetails
    ) -> int:
        try:
            with self._transaction() as connection:
                issue_id = _insert_issue_row(connection, width, body, origins, details)
        except sqlite3.Error as error:
            raise IssueStoreError(f'cannot commit the issue: {error}') from error
        return issue_id

    def insert_issues(self, issues: tuple[tuple[int, bytes, tuple[IssuedOrigin, ...], ReceiptDetails], ...]) -> tuple[int, ...]:
        """Commit every issue in one transaction; a failure leaves no new issue behind."""

        try:
            with self._transaction() as connection:
                issue_ids = tuple(
                    _insert_issue_row(connection, width, body, origins, details)
                    for width, body, origins, details in issues
                )
        except sqlite3.Error as error:
            raise IssueStoreError(f'cannot commit the issue set: {error}') from error
        return issue_ids

    def load_issue(self, issue_id: int) -> StoredIssue | None:
        try:
            with self._transaction() as connection:
                issue = connection.execute(
                    'SELECT width_hex, body FROM issues WHERE id = ?', (issue_id,)
                ).fetchone()
                if issue is None:
                    return None
                ledger = connection.execute(
                    '''SELECT total_records, valid_records, missing_records, zero_records, total_hex
                       FROM ledgers WHERE issue_id = ?''', (issue_id,)
                ).fetchone()
                if ledger is None:
                    raise IssueStoreError(f'issue {issue_id} has no fixed ledger')
                rows = connection.execute(
                    '''SELECT target_index, source_volume_id, source_position_hex FROM origins
                       WHERE issue_id = ? ORDER BY target_index''', (issue_id,)
                ).fetchall()
                width, body = int(issue[0], 16), issue[1]
                count, valid, missing, zeros, total_hex = ledger
                details = ReceiptDetails(count, valid, missing, zeros, int(total_hex, 16))
                origins = tuple(IssuedOrigin(index, volume, int(position, 16)) for index, volume, position in rows)
                if width <= 0 or not isinstance(body, bytes) or len(body) != width * count or len(origins) != count:
                    raise IssueStoreError(f'issue {issue_id} has inconsistent record boundaries')
                if any(origin.target_index != index for index, origin in enumerate(origins)):
                    raise IssueStoreError(f'issue {issue_id} has nonconsecutive origins')
                return StoredIssue(width, body, origins, details)
        except (sqlite3.Error, ValueError, TypeError) as error:
            raise IssueStoreError(f'cannot load issue {issue_id}: {error}') from error

    def list_issue_ids(self) -> tuple[int, ...]:
        try:
            with self._transaction() as connection:
                rows = connection.execute('SELECT id FROM issues ORDER BY id')
                return tuple(issue_id for (issue_id,) in rows)
        except sqlite3.Error as error:
            raise IssueStoreError(f'cannot list issues: {error}') from error
