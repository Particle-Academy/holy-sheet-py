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

    A refusal over a limit (``archive_limit``, and ``read_limit`` from
    `ReadLimitExceeded`) carries `limit`, `max` and `actual`, so an app can tell
    which one to raise; they are None for every other reason. A bad limit
    SETTING (``archive_limits``, ``read_limits``) names it in the message.
    """

    def __init__(
        self,
        reason: str,
        limit: str | None = None,
        max: int | None = None,
        actual: int | None = None,
        detail: str | None = None,
    ) -> None:
        self.reason = reason
        self.limit = limit
        self.max = max
        self.actual = actual
        if detail is None and limit is not None and max is not None and actual is not None:
            detail = f"{limit} is {max:,}; this file needs at least {actual:,}"
        super().__init__(f"Source archive refused: {reason}" + (f" ({detail})" if detail is not None else ""))

    @classmethod
    def over_limit(cls, limit: str, max: int, actual: int) -> ArchiveRefused:
        """One archive limit exceeded: ``archive_limit``, naming it."""
        return cls("archive_limit", limit, max, actual)

    @classmethod
    def bad_setting(cls, reason: str, key: object, known: bool) -> ArchiveRefused:
        """A bad limit setting: `reason` is ``archive_limits`` or ``read_limits``."""
        return cls(reason, detail=f"{key} must be a positive integer" if known else f'unknown limit "{key}"')


_LIMIT_WORDS = {
    "maxRows": ("a sheet has", "row", "rows", ""),
    "maxColumns": ("a sheet has", "column", "columns", ""),
    "maxCells": ("the workbook has at least", "cell", "cells", "at least "),
    "maxSharedStrings": ("the workbook has", "shared string", "shared strings", ""),
}


class ReadLimitExceeded(ArchiveRefused):
    """A read stopped because the spreadsheet is bigger than a read limit allows.

    Mirrors PHP and Node `ReadLimitExceeded`: `reason` is ``read_limit``,
    `limit` names which one (``maxRows``, ``maxColumns``, ``maxCells``,
    ``maxSharedStrings``), `max` is its value and `actual` what the file holds.
    The message says the same in plain words, for showing to a person. It is an
    `ArchiveRefused`, so an existing ``except ArchiveRefused`` still catches it.
    """

    def __init__(self, limit: str, max: int, actual: int, subject: str | None = None) -> None:  # noqa: A002
        super().__init__("read_limit", limit, max, actual)
        what, one, many, by = _LIMIT_WORDS[limit]
        if subject is not None:
            what, by = subject, ""

        def unit(n: int) -> str:
            return one if n == 1 else many

        message = (
            f"Spreadsheet refused: {what} {actual:,} {unit(actual)}, "
            f"over the limit of {max:,} {unit(max)} ({limit}) by {by}{actual - max:,}."
        )
        self.args = (message,)

    def __str__(self) -> str:
        return str(self.args[0])


class SheetNotFoundException(ValueError):
    """`read(data, sheet=...)` named a sheet the workbook does not have.

    Names match exactly; the message lists every one. Mirrors PHP
    `SheetNotFoundException` (an `InvalidArgumentException` there).
    """

    def __init__(self, sheet: str, available: list[str]) -> None:
        self.sheet = sheet
        self.available = available
        names = ", ".join(f'"{name}"' for name in available)
        super().__init__(f'[holy-sheet] no sheet named "{sheet}"; this workbook has: {names}')


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
    def refused_declaration(cls, path: str | None = None) -> "UnsupportedFormatException":
        return cls(
            f"[holy-sheet] cannot read {path if path is not None else 'this file'}: "
            "its [Content_Types].xml carries a DOCTYPE or an unreadable encoding, "
            "which no spreadsheet package has",
            path,
        )

    @classmethod
    def refused_part(cls, path: str | None = None) -> "UnsupportedFormatException":
        """A part other than the declaration carries a DOCTYPE. It used to read as
        an absent part, so a worksheet with one came back as an empty sheet.
        Mirrors PHP `UnsupportedFormatException::refusedPart`."""
        return cls(
            f"[holy-sheet] cannot read {path if path is not None else 'this file'}: "
            "one of its XML parts carries a DOCTYPE, which no spreadsheet part has",
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
