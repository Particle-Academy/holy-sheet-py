"""Schema failure, carrying the structured error list rather than a message."""

from __future__ import annotations

from typing import Any


class SchemaException(Exception):
    """Raised when a Holy Sheet schema fails validation.

    Wraps the structured error list (`path`, `expected`, `got`, `value`,
    `hint`) that `Validator.validate` produces. Agents catching this should read
    `errors` rather than parse the message -- every entry is independently
    actionable, and the message only summarises the first one.
    """

    def __init__(self, errors: list[dict[str, Any]], message: str | None = None) -> None:
        self.errors = errors
        super().__init__(message if message is not None else _summarize(errors))

    @classmethod
    def from_errors(cls, errors: list[dict[str, Any]]) -> "SchemaException":
        return cls(errors)

    def get_errors(self) -> list[dict[str, Any]]:
        """Peer-named accessor (`getErrors()` in PHP and TS)."""
        return self.errors


def _summarize(errors: list[dict[str, Any]]) -> str:
    if len(errors) == 1:
        first = errors[0]
        return (
            f"[holy-sheet] schema invalid at {first['path']}: "
            f"expected {first['expected']}, got {first['got']}"
        )
    first = errors[0]
    rest = len(errors) - 1
    return (
        f"[holy-sheet] schema invalid at {first['path']}: "
        f"expected {first['expected']}, got {first['got']} (+{rest} more)"
    )
