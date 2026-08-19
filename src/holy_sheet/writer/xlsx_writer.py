"""XLSX writer -- emits a complete OOXML SpreadsheetML package as bytes.

Everything here is string concatenation, and that is the CONTRACT rather than a
style choice. The observable agreement between the three engines is at the level
of part bytes: attribute order (`<c r="A1" s="3" t="inlineStr">`, in that
order), self-closing style (`<c r="A1"/>` for a null cell, but `<xf …></xf>`
always as a pair), the absence of inter-element whitespace, and `&apos;` rather
than `&#39;`. A DOM serialiser owns every one of those decisions, which is why
`xml.etree` is used for READING and never for writing.

The package it emits is exactly these parts and no others -- no
`sharedStrings.xml`, no `calcChain.xml`, no `theme1.xml`. Those are the classic
sources of xlsx diff noise and all three engines deliberately skip them.
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone

from ..helpers.php import PHP_INT_MAX, PHP_INT_MIN, format_float, php_number_format, php_to_string
from ..helpers.xml import xml_escape
from ..workbook.cell import Cell
from ..workbook.cell_address import CellAddress
from ..workbook.sheet import Sheet
from ..workbook.workbook import Workbook
from .styles_registry import StylesRegistry

# A fixed DOS timestamp (the zip format's own minimum) instead of "now". Without
# it the container is a clock and the same input produces different bytes every
# run, which makes a golden fixture impossible and hides anything else
# timestamp-shaped that leaks in.
_ZIP_DATE = (1980, 1, 1, 0, 0, 0)


class XlsxWriter:
    def write(self, workbook: Workbook, path: str) -> None:
        data = self.to_bytes(workbook)
        with open(path, "wb") as handle:
            handle.write(data)

    def to_bytes(self, workbook: Workbook) -> bytes:
        styles = StylesRegistry()

        # Render the sheets FIRST so every format is registered before
        # styles.xml is serialised. Reordering these two lines produces a
        # styles.xml that is missing every style the sheets reference.
        sheet_xmls = [self.sheet_xml(sheet, styles) for sheet in workbook.sheets]

        sheets_with_comments = [
            i for i, sheet in enumerate(workbook.sheets) if sheet.has_comments()
        ]

        parts: list[tuple[str, str]] = [
            ("[Content_Types].xml", self.content_types_xml(workbook, sheets_with_comments)),
            ("_rels/.rels", self.root_rels_xml()),
            ("xl/workbook.xml", self.workbook_xml(workbook)),
            ("xl/_rels/workbook.xml.rels", self.workbook_rels_xml(workbook)),
            ("xl/styles.xml", styles.to_xml()),
            ("docProps/core.xml", self.core_xml(workbook)),
            ("docProps/app.xml", self.app_xml()),
        ]

        for i, sheet in enumerate(workbook.sheets):
            n = i + 1
            parts.append((f"xl/worksheets/sheet{n}.xml", sheet_xmls[i]))
            if sheet.has_comments():
                parts.append(
                    (f"xl/worksheets/_rels/sheet{n}.xml.rels", self.sheet_rels_xml(n))
                )
                parts.append((f"xl/comments{n}.xml", self.comments_xml(sheet)))
                parts.append((f"xl/drawings/vmlDrawing{n}.vml", self.vml_drawing_xml(sheet)))

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, xml in parts:
                info = zipfile.ZipInfo(name, date_time=_ZIP_DATE)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                archive.writestr(info, xml.encode("utf-8"))
        return buffer.getvalue()

    # ------------------------------------------------------------------ #
    # Package parts                                                       #
    # ------------------------------------------------------------------ #

    def content_types_xml(self, workbook: Workbook, sheets_with_comments: list[int]) -> str:
        overrides = (
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
            '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        )
        for i in range(len(workbook.sheets)):
            n = i + 1
            overrides += (
                f'<Override PartName="/xl/worksheets/sheet{n}.xml"'
                ' ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            )
        for i in sheets_with_comments:
            n = i + 1
            overrides += (
                f'<Override PartName="/xl/comments{n}.xml"'
                ' ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.comments+xml"/>'
            )

        defaults = (
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
        )
        if sheets_with_comments:
            defaults += (
                '<Default Extension="vml"'
                ' ContentType="application/vnd.openxmlformats-officedocument.vmlDrawing"/>'
            )

        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            + defaults
            + overrides
            + "</Types>"
        )

    def root_rels_xml(self) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
            "</Relationships>"
        )

    def workbook_xml(self, workbook: Workbook) -> str:
        sheets = ""
        for i, sheet in enumerate(workbook.sheets):
            name = xml_escape(sheet.name)
            sheets += f'<sheet name="{name}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
            ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<sheets>{sheets}</sheets>"
            "</workbook>"
        )

    def workbook_rels_xml(self, workbook: Workbook) -> str:
        rels = ""
        for i in range(len(workbook.sheets)):
            rels += (
                f'<Relationship Id="rId{i + 1}"'
                ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"'
                f' Target="worksheets/sheet{i + 1}.xml"/>'
            )
        rels += (
            f'<Relationship Id="rId{len(workbook.sheets) + 1}"'
            ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles"'
            ' Target="styles.xml"/>'
        )
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + rels
            + "</Relationships>"
        )

    def sheet_rels_xml(self, sheet_num: int) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/vmlDrawing"'
            f' Target="../drawings/vmlDrawing{sheet_num}.vml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"'
            f' Target="../comments{sheet_num}.xml"/>'
            "</Relationships>"
        )

    def sheet_xml(self, sheet: Sheet, styles: StylesRegistry) -> str:
        sheet_views_xml = ""
        if sheet.frozen_rows > 0 or sheet.frozen_cols > 0:
            top_left = CellAddress.letter(sheet.frozen_cols) + str(sheet.frozen_rows + 1)
            pane = "<pane"
            if sheet.frozen_cols > 0:
                pane += f' xSplit="{sheet.frozen_cols}"'
            if sheet.frozen_rows > 0:
                pane += f' ySplit="{sheet.frozen_rows}"'
            pane += f' topLeftCell="{top_left}" activePane="bottomRight" state="frozen"/>'
            sheet_views_xml = (
                f'<sheetViews><sheetView workbookViewId="0">{pane}</sheetView></sheetViews>'
            )

        cols_xml = ""
        if sheet.column_widths:
            cols_xml = "<cols>"
            for col_idx, px in sheet.column_widths.items():
                excel_width = max(1.0, (px - 5) / 7)
                col_num = col_idx + 1
                cols_xml += (
                    f'<col min="{col_num}" max="{col_num}"'
                    f' width="{php_number_format(excel_width, 4)}" customWidth="1"/>'
                )
            cols_xml += "</cols>"

        rows_xml = ""
        for row_index, row in sheet.rows().items():
            cells_xml = ""
            # LEXICOGRAPHIC on the column letter -- so a sheet wider than 26
            # columns emits A, AA, AB, …, B, C. That is neither Excel's
            # canonical order nor intuition, and it is what BOTH shipped engines
            # do (`ksort` on a string key in PHP, `[...keys].sort()` in Node).
            # It is replicated on purpose and pinned by the `wide` fixture:
            # "fixing" it here would break parts parity for every wide sheet in
            # every engine, so the fix belongs in a coordinated release train,
            # not in a port.
            for col in sorted(row.keys()):
                cells_xml += self.cell_xml(row[col], styles)
            rows_xml += f'<row r="{row_index}">{cells_xml}</row>'

        merges_xml = ""
        if sheet.merged_regions:
            merges_xml = f'<mergeCells count="{len(sheet.merged_regions)}">'
            for merge in sheet.merged_regions:
                merges_xml += f'<mergeCell ref="{merge.ref()}"/>'
            merges_xml += "</mergeCells>"

        legacy_drawing = '<legacyDrawing r:id="rId1"/>' if sheet.has_comments() else ""

        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
            ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            + sheet_views_xml
            + cols_xml
            + f"<sheetData>{rows_xml}</sheetData>"
            + merges_xml
            + legacy_drawing
            + "</worksheet>"
        )

    def cell_xml(self, cell: Cell, styles: StylesRegistry) -> str:
        ref = cell.address
        cell_type = cell.excel_type()
        style_idx = styles.register(cell.format)
        s_attr = f' s="{style_idx}"' if style_idx > 0 else ""

        if cell.formula is not None:
            formula = xml_escape(cell.formula.lstrip("="))
            cached = cell.value if cell.cached_value is None else cell.cached_value
            value_xml = "" if cached is None else f"<v>{xml_escape(php_to_string(cached))}</v>"
            return f'<c r="{ref}"{s_attr}><f>{formula}</f>{value_xml}</c>'

        if cell_type == "inlineStr":
            text = xml_escape(str(cell.value))
            return (
                f'<c r="{ref}"{s_attr} t="inlineStr">'
                f'<is><t xml:space="preserve">{text}</t></is></c>'
            )

        if cell_type == "b":
            return f'<c r="{ref}"{s_attr} t="b"><v>{"1" if cell.value is True else "0"}</v></c>'

        if cell.value is None:
            return f'<c r="{ref}"{s_attr}/>'

        value = cell.value
        if isinstance(value, bool):  # unreachable (handled above), guarded anyway
            text = "1" if value else "0"
        elif isinstance(value, int):
            # PHP's ints are 64-bit and json_decode hands anything wider back as
            # a float; Python's are unbounded, so an out-of-range int would put
            # digits in the cell that no other engine can produce. Match PHP.
            text = (
                str(value)
                if PHP_INT_MIN <= value <= PHP_INT_MAX
                else format_float(float(value))
            )
        elif isinstance(value, float):
            # NaN / Infinity have no `<v>` representation -- "NaN" in a cell is
            # a corrupt sheet, not a big number. format_float maps them to 0,
            # which is what PHP's number_format does.
            text = format_float(value)
        else:  # pragma: no cover - the normalizer stringifies anything else
            text = str(value)

        if text == "":
            text = "0"
        return f'<c r="{ref}"{s_attr}><v>{text}</v></c>'

    def comments_xml(self, sheet: Sheet) -> str:
        authors: list[str] = []
        comment_list = ""
        for address, comment in sheet.comments():
            author = comment.author if comment.author is not None else "Author"
            if author not in authors:
                authors.append(author)
            author_idx = authors.index(author)
            text = xml_escape(comment.text)
            comment_list += (
                f'<comment ref="{address}" authorId="{author_idx}">'
                f'<text><r><t xml:space="preserve">{text}</t></r></text>'
                "</comment>"
            )
        authors_xml = "<authors>"
        for author in authors:
            authors_xml += f"<author>{xml_escape(author)}</author>"
        authors_xml += "</authors>"
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<comments xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            + authors_xml
            + f"<commentList>{comment_list}</commentList>"
            + "</comments>"
        )

    def vml_drawing_xml(self, sheet: Sheet) -> str:
        shapes = ""
        shape_id = 1024
        for address, _comment in sheet.comments():
            parsed = CellAddress.parse(address)
            if parsed is None:  # pragma: no cover - normalizer keeps these well-formed
                continue
            col, row_number = parsed
            row = row_number - 1
            shape_id += 1
            shapes += (
                f'<v:shape id="_x0000_s{shape_id}" type="#_x0000_t202" '
                'style="position:absolute;margin-left:60pt;margin-top:5pt;width:108pt;height:60pt;z-index:1;visibility:hidden" '
                'fillcolor="#ffffe1" o:insetmode="auto">'
                '<v:fill color2="#ffffe1"/>'
                '<v:shadow on="t" color="black" obscured="t"/>'
                '<v:path o:connecttype="none"/>'
                '<v:textbox><div style="text-align:left"></div></v:textbox>'
                '<x:ClientData ObjectType="Note">'
                "<x:MoveWithCells/>"
                "<x:SizeWithCells/>"
                f"<x:Anchor>{col + 1}, 15, {row}, 10, {col + 3}, 31, {row + 4}, 18</x:Anchor>"
                "<x:AutoFill>False</x:AutoFill>"
                f"<x:Row>{row}</x:Row>"
                f"<x:Column>{col}</x:Column>"
                "</x:ClientData>"
                "</v:shape>"
            )
        return (
            '<xml xmlns:v="urn:schemas-microsoft-com:vml" '
            'xmlns:o="urn:schemas-microsoft-com:office:office" '
            'xmlns:x="urn:schemas-microsoft-com:office:excel">'
            '<o:shapelayout v:ext="edit"><o:idmap v:ext="edit" data="1"/></o:shapelayout>'
            '<v:shapetype id="_x0000_t202" coordsize="21600,21600" o:spt="202" path="m,l,21600r21600,l21600,xe">'
            '<v:stroke joinstyle="miter"/><v:path gradientshapeok="t" o:connecttype="rect"/></v:shapetype>'
            + shapes
            + "</xml>"
        )

    def core_xml(self, workbook: Workbook) -> str:
        created = workbook.meta.get("created")
        now = _gmdate_z() if created is None else str(created)
        creator = xml_escape(str(_coalesce(workbook.meta.get("creator"), "Holy Sheet")))
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"'
            ' xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/"'
            ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f"<dc:creator>{creator}</dc:creator>"
            f"<cp:lastModifiedBy>{creator}</cp:lastModifiedBy>"
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>'
            f'<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>'
            "</cp:coreProperties>"
        )

    def app_xml(self) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"'
            ' xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
            "<Application>Holy Sheet</Application>"
            "<DocSecurity>0</DocSecurity>"
            "<ScaleCrop>false</ScaleCrop>"
            "<SharedDoc>false</SharedDoc>"
            "<HyperlinksChanged>false</HyperlinksChanged>"
            "<AppVersion>1.0</AppVersion>"
            "</Properties>"
        )


def _coalesce(preferred, fallback):
    return fallback if preferred is None else preferred


def _gmdate_z() -> str:
    """PHP `gmdate('Y-m-d\\TH:i:s\\Z')` -- current UTC, seconds precision.

    Only reached when `meta.created` is absent, and any workbook that wants to
    be a reproducible artifact should set it.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
