from collections.abc import Callable

from plan.binding import require_mounted_catalog
from plan.transfer import SinglePlan
from source.device import FrameDevice
from source.reader import ForwardReader

from .candidate import CandidateVolume


class ExecutionError(RuntimeError):
    """The single-volume transfer failed after the mount check."""


def execute_single(
    plan: SinglePlan,
    device: FrameDevice,
    *,
    candidate_factory: Callable[[SinglePlan], CandidateVolume] = CandidateVolume,
) -> CandidateVolume:
    reader = ForwardReader(device)
    require_mounted_catalog(plan.binding, reader.directory)
    candidate = None
    try:
        candidate = candidate_factory(plan)
        for position in plan.selection.positions:
            candidate.append(reader.read_through(position))
        candidate.complete()
    except Exception as error:
        if candidate is not None:
            candidate.discard()
        raise ExecutionError(f'transfer failed: {error}') from error
    return candidate
