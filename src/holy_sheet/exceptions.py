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


class ArchiveRefused(RuntimeError):
    """Bounded archive/security refusal with a constant `reason`; carries no
    member names or document content. Mirrors PHP and Node `ArchiveRefused`.

    Reasons: `archive_corrupt`, `archive_unsupported`, `archive_path`,
    `archive_duplicate`, `archive_limit`, `archive_limits`.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Source archive refused: {reason}")


_TEMPLATE_MESSAGES = {
    "macro_enabled": "Macro-enabled templates (.xltm) are refused: re-save it as an .xltx in Excel.",
    "family_mismatch": "This package declares a template of a different kind than its contents.",
    "already_template": "This package is already a template.",
    "already_document": "This package is already a document.",
    "not_convertible": "This package has no single content-type declaration that can be rewritten.",
    "odf_mimetype": "An OpenDocument package must store its mimetype entry first and uncompressed.",
    "bad_target": 'The conversion target must be "document" or "template".',
}


class OfficeTemplateRefused(RuntimeError):
    """An Office or LibreOffice template package refused, with a constant
    `reason`. Mirrors PHP and Node `OfficeTemplateRefused`:

    - `macro_enabled`   an .xltm (or a template carrying a VBA project). Never
                        read and never converted.
    - `family_mismatch` it declares a template of another family, or its parts
                        are not the family it declares.
    - `already_template` / `already_document`  conversion asked for what it is.
    - `not_convertible` there is no single declaration that can be rewritten.
    - `odf_mimetype`    an ODF package whose `mimetype` is not first and stored.
    - `bad_target`      the conversion target is not `document` or `template`.

    Branch on `reason`; the message is for people.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Template refused ({reason}): {_TEMPLATE_MESSAGES.get(reason, reason)}")


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
