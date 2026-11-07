"""Which reader a file needs, decided from its contents, never its extension.

Mirrors PHP `Reader\\FormatSniffer`. An OpenDocument package names itself in its
`mimetype` entry; an xlsx has no such entry and is recognised by
`xl/workbook.xml`. Anything else is refused by name.
"""

from __future__ import annotations

from ..archive.source_archive import SourceArchive, open_for_reading
from ..exceptions import UnsupportedFormatException
from .ods.ns import php_trim

XLSX = "xlsx"
ODS = "ods"

#: The spreadsheet media types, and the template variant (.ots) which is read the same way.
_ODS_MIMETYPES = frozenset(
    {
        "application/vnd.oasis.opendocument.spreadsheet",
        "application/vnd.oasis.opendocument.spreadsheet-template",
    }
)


class FormatSniffer:
    XLSX = XLSX
    ODS = ODS

    @staticmethod
    def sniff(data: bytes, path: str | None = None, archive_limits: dict[str, int] | None = None) -> str:
        return FormatSniffer.sniff_archive(data, path, archive_limits)[0]

    @staticmethod
    def sniff_archive(
        data: bytes, path: str | None = None, archive_limits: dict[str, int] | None = None
    ) -> tuple[str, SourceArchive]:
        """Which reader the bytes need, and the opened archive, so the reader
        reads it rather than opening (and inflating) the bytes a second time.
        Mirrors PHP `FormatSniffer::sniffBytes`."""
        archive = open_for_reading(data, path, archive_limits)
        return FormatSniffer._kind(archive, path), archive

    @staticmethod
    def _kind(archive: SourceArchive, path: str | None) -> str:
        with archive:
            names = set(archive.names())
            raw = archive.read("mimetype")
            mimetype = php_trim(raw.decode("utf-8", "replace")) if raw is not None else None
            if mimetype is not None and mimetype in _ODS_MIMETYPES:
                return ODS
            if "xl/workbook.xml" in names:
                return XLSX

        raise UnsupportedFormatException.unknown_package(path, mimetype)
