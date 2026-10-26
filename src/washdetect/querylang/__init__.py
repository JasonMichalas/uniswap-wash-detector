"""A small read-only query language, and the SQL it lowers to."""

from washdetect.querylang.ast import Program
from washdetect.querylang.errors import QueryError
from washdetect.querylang.parser import parse

__all__ = ["Program", "QueryError", "parse"]
