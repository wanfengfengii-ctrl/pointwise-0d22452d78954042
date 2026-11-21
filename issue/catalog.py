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
        width = snapshot.plan.binding.directory.layout.width
        receipt = self._receipt(manifest)
        issue_id = self._store.insert_issue(width, snapshot.body, receipt.origins, receipt.details)
        return IssueReceipt(format_issue_number(issue_id), receipt.origins, receipt.details)

    def commit_pair(
        self,
        width: int,
        first: tuple[bytes, ReconciliationManifest],
        second: tuple[bytes, ReconciliationManifest],
    ) -> tuple[IssueReceipt, IssueReceipt]:
        """Commit two reconciled targets together; both receipts or neither exist."""

        def details_for(manifest: ReconciliationManifest):
            amounts = manifest.amounts
            return ReceiptDetails(
                amounts.total_records, amounts.valid_records, amounts.missing_records,
                amounts.zero_records, amounts.total,
            )

        def origins_for(manifest: ReconciliationManifest):
            return tuple(
                IssuedOrigin(record.target_index, record.source_volume_id, record.source_position)
                for record in manifest.records
            )

        first_body, first_manifest = first
        second_body, second_manifest = second
        first_details, second_details = details_for(first_manifest), details_for(second_manifest)
        first_origins, second_origins = origins_for(first_manifest), origins_for(second_manifest)
        issue_ids = self._store.insert_issues((
            (width, first_body, first_origins, first_details),
            (width, second_body, second_origins, second_details),
        ))
        return (
            IssueReceipt(format_issue_number(issue_ids[0]), first_origins, first_details),
            IssueReceipt(format_issue_number(issue_ids[1]), second_origins, second_details),
        )

    @staticmethod
    def _receipt(manifest: ReconciliationManifest) -> IssueReceipt:
        amounts = manifest.amounts
        details = ReceiptDetails(
            amounts.total_records, amounts.valid_records, amounts.missing_records, amounts.zero_records, amounts.total
        )
        origins = tuple(
            IssuedOrigin(record.target_index, record.source_volume_id, record.source_position)
            for record in manifest.records
        )
        return IssueReceipt('', origins, details)

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
