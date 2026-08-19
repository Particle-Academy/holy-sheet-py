"""Ported from `holy-sheet/tests/Unit/HelpersTest.php` -- inference + builders."""

from __future__ import annotations

import holy_sheet
from holy_sheet import Inference


class TestInference:
    def test_infers_an_integer_column_from_header_and_values(self) -> None:
        assert Inference.detect([1, 2, 3], "count")["type"] == "integer"

    def test_infers_currency_from_a_header_pattern(self) -> None:
        column = Inference.detect([1.5, 2.0], "revenue")
        assert column["type"] == "currency"
        assert column["currency"] == "USD"

    def test_honours_a_currency_option(self) -> None:
        column = Inference.detect([1.5], "revenue", {"currency": "EUR"})
        assert column["currency"] == "EUR"

    def test_infers_percent_only_when_values_sit_in_zero_to_one(self) -> None:
        assert Inference.detect([0.1, 0.5], "growth_rate")["type"] == "percent"
        assert Inference.detect([10, 50], "growth_rate")["type"] == "integer"

    def test_infers_date_when_values_match_iso_date(self) -> None:
        assert Inference.detect(["2024-01-01", "2024-02-01"], "created")["type"] == "date"

    def test_infers_datetime_when_values_carry_a_time(self) -> None:
        column = Inference.detect(["2024-01-01T09:00:00Z"], "created")
        assert column["type"] == "datetime"

    def test_infers_boolean_for_an_all_bool_column(self) -> None:
        """Bools must not be swept up by the numeric branch.

        `isinstance(True, int)` is True in Python, so `_all_numeric` checking
        int before bool would type this column as `integer`.
        """
        assert Inference.detect([True, False, True], "active")["type"] == "boolean"

    def test_falls_back_to_auto_for_mixed_types(self) -> None:
        assert Inference.detect([1, "two", True], "mixed")["type"] == "auto"

    def test_falls_back_to_auto_for_an_empty_column(self) -> None:
        assert Inference.detect([None, None], "empty")["type"] == "auto"

    def test_records_decimal_places_for_a_float_column(self) -> None:
        column = Inference.detect([1.5, 2.25, 3.125], "size")
        assert column["type"] == "number"
        assert column["decimals"] == 3


class TestFromArray:
    def test_builds_a_schema_from_rows_and_headers(self) -> None:
        schema = holy_sheet.from_array(
            [["NA", 100], ["EU", 200]], headers=["Region", "Revenue"]
        )

        sheet = schema["sheets"][0]
        assert sheet["name"] == "Sheet 1"
        assert sheet["columns"][1]["type"] == "currency"
        assert len(sheet["rows"]) == 2

    def test_treats_the_first_row_as_headers_when_omitted(self) -> None:
        schema = holy_sheet.from_array([["Name", "Age"], ["Alice", 30], ["Bob", 42]])

        sheet = schema["sheets"][0]
        assert sheet["columns"][0]["header"] == "Name"
        assert len(sheet["rows"]) == 2

    def test_handles_an_empty_input(self) -> None:
        schema = holy_sheet.from_array([])
        assert schema["sheets"][0]["rows"] == []

    def test_passes_options_through_to_the_sheet(self) -> None:
        schema = holy_sheet.from_array(
            [["a", 1]],
            headers=["K", "V"],
            options={"theme": "minimal", "frozenRows": 1, "totals": {"V": "sum"}},
        )

        sheet = schema["sheets"][0]
        assert sheet["theme"] == "minimal"
        assert sheet["frozenRows"] == 1
        assert sheet["totals"] == {"V": "sum"}

    def test_the_output_is_writable_without_translation(self) -> None:
        schema = holy_sheet.from_array([["Region", "Revenue"], ["NA", 100]])
        assert holy_sheet.validate(schema) == []
        assert holy_sheet.to_bytes(schema)[:2] == b"PK"


class TestFromCsv:
    def test_builds_a_schema_from_a_csv_string(self) -> None:
        schema = holy_sheet.from_csv("Name,Age\nAlice,30\nBob,42")

        sheet = schema["sheets"][0]
        assert sheet["columns"][0]["header"] == "Name"
        assert sheet["columns"][1]["type"] == "integer"
        assert sheet["rows"] == [["Alice", 30], ["Bob", 42]]

    def test_handles_quoted_fields_with_embedded_newlines(self) -> None:
        schema = holy_sheet.from_csv('Name,Note\n"Alice","line 1\nline 2"\n')
        assert schema["sheets"][0]["rows"][0][1] == "line 1\nline 2"

    def test_builds_from_a_file_path(self, tmp_path) -> None:
        """Accepting a path is PHP's behaviour and the superset.

        Node takes content only (it targets browsers, which have no filesystem),
        so the two peers genuinely differ on what `fromCsv` means. This follows
        PHP.
        """
        path = tmp_path / "cities.csv"
        path.write_text("City,Pop\nNYC,8000000\nLA,4000000\n", encoding="utf-8")

        schema = holy_sheet.from_csv(str(path))

        assert schema["sheets"][0]["rows"] == [["NYC", 8000000], ["LA", 4000000]]

    def test_treats_a_one_line_csv_as_content_not_a_path(self) -> None:
        schema = holy_sheet.from_csv("a,b")
        assert schema["sheets"][0]["columns"][0]["header"] == "a"

    def test_coerces_numeric_strings_the_way_php_does(self) -> None:
        """"1e5" is 100000, not 1 -- and "007" is the integer 7."""
        schema = holy_sheet.from_csv("V\n1e5\n007")
        assert schema["sheets"][0]["rows"] == [[100000.0], [7]]

    def test_handles_an_empty_csv(self) -> None:
        schema = holy_sheet.from_csv("")
        assert schema["sheets"][0]["rows"] == []
