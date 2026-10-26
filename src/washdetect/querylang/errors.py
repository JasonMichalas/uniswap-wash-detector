"""Errors a person can read. These are raised at parse time and at run time."""


class QueryError(Exception):
    """The query text or a lowered program is not valid, or it cannot be run."""
