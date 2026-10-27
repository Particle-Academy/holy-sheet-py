"""Which reader a file needs, decided from its contents, never its extension.

Mirrors PHP `Reader\\FormatSniffer`. An OpenDocument package names itself in its
`mimetype` entry; an xlsx has no such entry and is recognised by
`xl/workbook.xml`. Anything else is refused by name.
"""

from __future__ import annotations

from ..archive.source_archive import open_for_reading
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
    def sniff(data: bytes, path: str | None = None) -> str:
        archive = open_for_reading(data, path)

        with archive:
            names = set(archive.names())
            raw = archive.read("mimetype")
            mimetype = php_trim(raw.decode("utf-8", "replace")) if raw is not None else None
            if mimetype is not None and mimetype in _ODS_MIMETYPES:
                return ODS
            if "xl/workbook.xml" in names:
                return XLSX

        raise UnsupportedFormatException.unknown_package(path, mimetype)
