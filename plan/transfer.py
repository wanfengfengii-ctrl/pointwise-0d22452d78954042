from collections.abc import Sequence
from dataclasses import dataclass

from source.catalog import SourceCatalog

from .binding import CatalogBinding, bind_catalog
from .selection import Selection
from .validation import validate_selection


@dataclass(frozen=True, slots=True, init=False)
class SinglePlan:
    binding: CatalogBinding
    selection: Selection

    def __init__(self, binding: CatalogBinding, positions: Sequence[int]) -> None:
        selection = validate_selection(binding.directory, positions)
        object.__setattr__(self, 'binding', binding)
        object.__setattr__(self, 'selection', selection)


def plan_single(catalog: SourceCatalog, volume_id: str, positions: Sequence[int]) -> SinglePlan:
    directory = catalog.lookup(volume_id)
    return SinglePlan(bind_catalog(directory), positions)
