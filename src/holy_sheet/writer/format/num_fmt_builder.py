"""CellFormat -> the Excel number-format code that goes in `<numFmt formatCode>`.

Returns None when the format needs no numFmt (plain text). Built-in Excel
formats occupy ids below 164; this always emits a custom code (>= 164) so the
rendering does not shift between Excel versions and locales.
"""

from __future__ import annotations

from ...workbook.cell_format import CellFormat

_CURRENCY_SYMBOLS = {
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "JPY": "¥",
    "CNY": "¥",
    "INR": "₹",
    "AUD": "A$",
    "CAD": "C$",
    "CHF": "CHF",
    "KRW": "₩",
}


class NumFmtBuilder:
    @staticmethod
    def build(fmt: CellFormat) -> str | None:
        display = fmt.display_format
        decimals = fmt.decimals

        if display is None or display in ("auto", "text"):
            return None
        if display == "number":
            return _number_format(decimals)
        if display == "percentage":
            return _percent_format(decimals)
        if display == "currency":
            return _currency_format(fmt.currency, decimals)
        if display == "date":
            return "yyyy-mm-dd"
        if display == "datetime":
            return "yyyy-mm-dd hh:mm:ss"
        return None


def _number_format(decimals: int | None) -> str:
    if decimals is None or decimals <= 0:
        return "#,##0"
    return "#,##0." + "0" * decimals


def _percent_format(decimals: int | None) -> str:
    places = 1 if decimals is None else decimals
    if places <= 0:
        return "0%"
    return "0." + "0" * places + "%"


def _currency_format(currency: str | None, decimals: int | None) -> str:
    symbol = _currency_symbol(currency or "USD")
    places = 2 if decimals is None else decimals
    body = "#,##0" if places <= 0 else "#,##0." + "0" * places
    return f'"{symbol}"{body};-"{symbol}"{body}'


def _currency_symbol(iso: str) -> str:
    return _CURRENCY_SYMBOLS.get(iso.upper(), iso + " ")
