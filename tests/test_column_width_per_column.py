"""A column's OWN ``width``, which this port documented and silently ignored.

``skills/holy-sheet.schema.json`` -- the tool definition handed to an LLM, shared
byte-identically with the PHP and Node ports under a checksum test -- describes
``columns[].width`` as *"Column width in pixels. Same as columnWidths but
per-column."* Only the sheet-level ``columnWidths`` map was ever read, so a width
written the way the schema documents it emitted **no ``<cols>`` element at all**:
no exception, no validation error, nothing from ``validate_and_repair``. Silent at
every layer.

Reported against PHP as holy-sheet#8 by the MOIC team, whose owner's complaint was
"can't style spreadsheets at all". Measured, that was mostly this: the theme,
header fill, banded rows and currency formats all landed, but a real account name
truncated and a correctly-formatted currency rendered as ``#####`` -- which is what
a reader actually sees. Fixed in PHP 2.4.0 and Node 2.5.0; this is the third port.

This port was the worst of the three: ``_normalize_column_widths`` was called from
**two** places (the ``cells`` path and the ``columns``/``rows`` path), so a fix
applied to one would have left the other behaving differently -- the same twin
divergence the shared schema exists to prevent.

PRECEDENCE, identical to PHP and Node: the sheet-level map is applied LAST and
wins. It is the mechanism that already worked, so a consumer who moved to it to
route around this bug must not then find a leftover ``width`` overriding them.
"""

from __future__ import annotations

import holy_sheet
from holy_sheet.schema.normalizer import Normalizer


def _widths(columns, **extra):
    doc = {"sheets": [{"name": "S", "columns": columns, "rows": [["a", "b", "c"]], **extra}]}
    return Normalizer().normalize(doc).sheets[0].column_widths


def test_a_columns_own_width_is_honoured():
    assert _widths(
        [{"header": "Account", "width": 260}, {"header": "Amount", "width": 120}]
    ) == {0: 260.0, 1: 120.0}


def test_a_width_applies_at_the_columns_position_and_a_column_without_one_is_skipped():
    assert _widths([{"header": "A"}, {"header": "B", "width": 90}, {"header": "C"}]) == {1: 90.0}


def test_the_sheet_level_map_wins_on_a_conflict():
    # Deliberate, and the reason is migration: columnWidths is what worked, so a
    # consumer who moved to it must not be overridden by a stale width.
    assert _widths([{"header": "Account", "width": 260}], columnWidths={"0": 400}) == {0: 400.0}


def test_the_two_sources_merge_rather_than_one_replacing_the_other():
    assert _widths(
        [{"header": "A", "width": 100}, {"header": "B", "width": 200}],
        columnWidths={"2": 300},
    ) == {0: 100.0, 1: 200.0, 2: 300.0}


def test_widths_come_out_in_ascending_column_order():
    # Merging two sources means insertion order is no longer column order, and
    # ``<col>`` children are expected ascending.
    merged = _widths(
        [{"header": "A"}, {"header": "B"}, {"header": "C", "width": 70}],
        columnWidths={"0": 50},
    )

    assert list(merged.keys()) == [0, 2]


def test_a_width_obeys_the_same_rule_as_the_sheet_level_map():
    # One rule across both sources, or columns[].width becomes a second place
    # where "abc" means column A.
    assert _widths([{"header": "A", "width": "120"}]) == {0: 120.0}
    assert _widths([{"header": "A", "width": -5}]) == {}
    assert _widths([{"header": "A", "width": "wide"}]) == {}


def test_an_invalid_width_is_reported_by_path_rather_than_dropped_in_silence():
    # The original defect's real cost was silence: an agent composes against the
    # schema doc, gets no error, and never learns the field was discarded.
    errors = holy_sheet.validate(
        {"sheets": [{"name": "S", "columns": [{"header": "A", "width": "wide"}], "rows": [["a"]]}]}
    )

    assert any(e["path"] == "sheets[0].columns[0].width" for e in errors), errors


def test_a_string_column_definition_carries_no_width_and_does_not_raise():
    # ``columns: ["Account", "Amount"]`` is a documented shorthand; the merge must
    # not look for ``.width`` on a string.
    assert _widths(["Account", "Amount"]) == {}


def test_the_cells_path_also_honours_the_sheet_level_map():
    # The normalizer has TWO sheet-construction paths and both must agree. A fix
    # applied to one would have left the other behaving differently -- which is the
    # divergence that made this port the worst of the three.
    doc = {
        "sheets": [
            {"name": "S", "cells": {"A1": {"value": "x"}}, "columnWidths": {"0": 150}}
        ]
    }

    assert Normalizer().normalize(doc).sheets[0].column_widths == {0: 150.0}
