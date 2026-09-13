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


class UnsupportedFormatException(RuntimeError):
    """Raised by `read()` / `describe()` for a file that is neither an xlsx
    workbook nor an OpenDocument spreadsheet. Mirrors PHP
    `UnsupportedFormatException`.

    It subclasses `RuntimeError`, which is what an unreadable file raised before
    it existed, so an existing `except RuntimeError` still catches it.
    `mimetype` carries what the package declared about itself when it declared
    anything (an OpenDocument TEXT file, say), so a caller can say "that is a
    document, not a spreadsheet" instead of "could not read".
    """

    def __init__(self, message: str, path: str | None = None, mimetype: str | None = None) -> None:
        super().__init__(message)
        self.path = path
        self.mimetype = mimetype

    @classmethod
    def not_a_zip(cls, path: str | None = None) -> "UnsupportedFormatException":
        return cls(
            f"[holy-sheet] cannot read {path if path is not None else 'this file'}: "
            "it is not a zip archive, and xlsx and ods both are",
            path,
        )

    @classmethod
    def unknown_package(cls, path: str | None, mimetype: str | None) -> "UnsupportedFormatException":
        what = (
            f"its mimetype is {mimetype}"
            if mimetype
            else "it has neither xl/workbook.xml nor an OpenDocument mimetype"
        )
        return cls(
            f"[holy-sheet] cannot read {path if path is not None else 'this file'}: {what}. Supported: xlsx, ods",
            path,
            mimetype or None,
        )
