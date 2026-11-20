from dataclasses import dataclass

from source.catalog import SourceDirectory


class MountMismatch(ValueError):
    """The independently described mount does not match the planned source."""


@dataclass(frozen=True, slots=True)
class CatalogBinding:
    directory: SourceDirectory


def bind_catalog(directory: SourceDirectory) -> CatalogBinding:
    return CatalogBinding(directory)


def require_mounted_catalog(binding: CatalogBinding, mounted: SourceDirectory) -> None:
    if mounted != binding.directory:
        raise MountMismatch(
            f'mounted directory {mounted.volume_id!r} differs from the binding '
            f'for {binding.directory.volume_id!r}'
        )
