from media.candidate import CandidateSnapshot
from plan.transfer import SinglePlan

from .amounts import count_amounts
from .manifest import ReconciliationManifest, build_manifest
from .origins import verify_origins


def reconcile_candidate(plan: SinglePlan, snapshot: CandidateSnapshot) -> ReconciliationManifest:
    if snapshot.plan != plan:
        raise ValueError('candidate was completed for a different bound plan')
    directory = plan.binding.directory
    if len(snapshot.body) != directory.layout.width * len(snapshot.records):
        raise ValueError('candidate body does not match its fixed-width write boundaries')
    verify_origins(directory, plan.selection, snapshot.records)
    amounts = count_amounts(directory.layout, snapshot.body, snapshot.records)
    return build_manifest(snapshot.records, amounts)
