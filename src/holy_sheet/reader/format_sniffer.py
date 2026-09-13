"""Which reader a file needs, decided from its contents, never its extension.

Mirrors PHP `Reader\\FormatSniffer`. An OpenDocument package names itself in its
`mimetype` entry; an xlsx has no such entry and is recognised by
`xl/workbook.xml`. Anything else is refused by name.
"""

from __future__ import annotations

import io
import zipfile

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
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as error:
            raise UnsupportedFormatException.not_a_zip(path) from error

        with archive:
            names = set(archive.namelist())
            mimetype = (
                php_trim(archive.read("mimetype").decode("utf-8", "replace")) if "mimetype" in names else None
            )
            if mimetype is not None and mimetype in _ODS_MIMETYPES:
                return ODS
            if "xl/workbook.xml" in names:
                return XLSX

        raise UnsupportedFormatException.unknown_package(path, mimetype)
