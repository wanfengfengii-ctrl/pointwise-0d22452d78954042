from .catalog import IssueCatalog
from .receipt import IssuedVolume


class IssueNotFound(LookupError):
    """There is no published issue for the exact number supplied."""


def read_issue(catalog: IssueCatalog, number: str) -> IssuedVolume:
    volume = catalog.lookup(number)
    if volume is None:
        raise IssueNotFound(f'no published issue {number!r}')
    return volume
