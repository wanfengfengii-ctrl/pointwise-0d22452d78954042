from media.candidate import CandidateVolume
from plan.transfer import SinglePlan
from reconcile import reconcile_candidate

from .catalog import IssueCatalog
from .receipt import IssueReceipt


class PublishError(RuntimeError):
    """A candidate did not pass reconciliation or could not be committed."""


def publish_single(catalog: IssueCatalog, plan: SinglePlan, candidate: CandidateVolume) -> IssueReceipt:
    try:
        snapshot = candidate.snapshot()
        manifest = reconcile_candidate(plan, snapshot)
        return catalog.commit(snapshot, manifest)
    except Exception as error:
        candidate.discard()
        raise PublishError(f'publication failed: {error}') from error
