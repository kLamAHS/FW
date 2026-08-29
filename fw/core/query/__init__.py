"""Asking the world questions (§49).

The brief calls this one of the application's most important features and this file was
zero bytes. `language` is what a question *is* — a small tree of dataclasses a form can
fill in and JSON can carry; `engine` turns one into a single SQL statement over the fact
spine; `saved` keeps the ones a writer wants to ask again.
"""

from fw.core.query import language
from fw.core.query.engine import run
from fw.core.query.language import (
    DIRECTIONS,
    ORDERS,
    TESTS,
    Answer,
    Condition,
    Query,
    QueryError,
    Row,
    Saved,
    Within,
)
from fw.core.query.saved import forget, save, saved

__all__ = [
    "DIRECTIONS", "ORDERS", "TESTS", "language",
    "Answer", "Condition", "Query", "QueryError", "Row", "Saved", "Within",
    "forget", "run", "save", "saved",
]
