import re

from media.candidate import CandidateSnapshot
from reconcile.manifest import ReconciliationManifest

from .receipt import IssueReceipt, IssuedOrigin, IssuedVolume, ReceiptDetails
from .storage import IssueStore


_MAX_ISSUE_ID = (1 << 63) - 1
_ISSUE_NUMBER = re.compile(r'ISS-[0-9]{6,19}')


def format_issue_number(issue_id: int) -> str:
    if type(issue_id) is not int or not 0 < issue_id <= _MAX_ISSUE_ID:
        raise ValueError('invalid issue sequence number')
    return f'ISS-{issue_id:06d}'


def parse_issue_number(number: str) -> int | None:
    if not isinstance(number, str) or _ISSUE_NUMBER.fullmatch(number) is None:
        return None
    issue_id = int(number[4:])
    if 0 < issue_id <= _MAX_ISSUE_ID and number == format_issue_number(issue_id):
        return issue_id
    return None


class IssueCatalog:
    def __init__(self, store: IssueStore) -> None:
        self._store = store

    def commit(self, snapshot: CandidateSnapshot, manifest: ReconciliationManifest) -> IssueReceipt:
        amounts = manifest.amounts
        details = ReceiptDetails(
            amounts.total_records, amounts.valid_records, amounts.missing_records, amounts.zero_records, amounts.total
        )
        origins = tuple(
            IssuedOrigin(record.target_index, record.source_volume_id, record.source_position)
            for record in manifest.records
        )
        width = snapshot.plan.binding.directory.layout.width
        issue_id = self._store.insert_issue(width, snapshot.body, origins, details)
        return IssueReceipt(format_issue_number(issue_id), origins, details)

    def lookup(self, number: str) -> IssuedVolume | None:
        issue_id = parse_issue_number(number)
        if issue_id is None:
            return None
        stored = self._store.load_issue(issue_id)
        if stored is None:
            return None
        receipt = IssueReceipt(number, stored.origins, stored.details)
        return IssuedVolume(stored.width, stored.body, receipt)

    def list_numbers(self) -> tuple[str, ...]:
        return tuple(format_issue_number(issue_id) for issue_id in self._store.list_issue_ids())
