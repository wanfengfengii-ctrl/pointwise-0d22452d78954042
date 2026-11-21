from media.candidate import CandidateSnapshot
from plan.transfer import SinglePlan
from source.layout import RecordLayout

from .amounts import count_amounts
from .manifest import ReconciliationManifest, build_manifest
from .origins import verify_expected_origins, verify_origins


def reconcile_candidate(plan: SinglePlan, snapshot: CandidateSnapshot) -> ReconciliationManifest:
    if snapshot.plan != plan:
        raise ValueError('candidate was completed for a different bound plan')
    directory = plan.binding.directory
    if len(snapshot.body) != directory.layout.width * len(snapshot.records):
        raise ValueError('candidate body does not match its fixed-width write boundaries')
    verify_origins(directory, plan.selection, snapshot.records)
    amounts = count_amounts(directory.layout, snapshot.body, snapshot.records)
    return build_manifest(snapshot.records, amounts)


def reconcile_written(
    layout: RecordLayout,
    expected_origins: tuple[tuple[str, int], ...],
    body: bytes,
    records: tuple,
) -> ReconciliationManifest:
    """Reconcile one completed target of the double transfer against its work order."""

    if not isinstance(body, bytes) or len(body) != layout.width * len(records):
        raise ValueError('target body does not match its fixed-width write boundaries')
    verify_expected_origins(tuple(expected_origins), tuple(records))
    amounts = count_amounts(layout, body, tuple(records))
    return build_manifest(tuple(records), amounts)
