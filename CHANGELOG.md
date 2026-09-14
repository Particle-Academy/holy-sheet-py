# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**Pre-1.0: breaking changes land in MINOR releases.** Until `1.0.0`, a bump from
`0.1.x` to `0.2.0` may change or remove API. Pin accordingly, and read the entry
before upgrading — every breaking change here says what you have to DO, not just
what moved.

## [Unreleased]

## [0.2.1] - 2026-09-14

### Fixed

- **`lint()` accepts quoted sheet names** (holy-sheet [#6](https://github.com/Particle-Academy/holy-sheet/issues/6)). `=SUM('My Earnings Projection'!A2:A3)` linted as `#NAME?` because the tokenizer had no case for `'`, so any cross-sheet formula pointing at a sheet whose name contains a space failed. Excel's doubled-quote escape works too: `'Q3 ''Final'''!B2` is the sheet `Q3 'Final'`.
- **A reference to a sheet that does not exist is `#REF!`**, quoted or not, and the hint names the sheet and lists the ones that exist. It used to lint clean, because the missing sheet's cells read as blanks.
- **Sheet names match case-insensitively**, as in Excel.

The same fix as `particle-academy/holy-sheet` 2.2.1, with the hint text identical in all three runtimes and pinned by a test in each.

**What you must do:** nothing, unless you relied on a formula that names a missing sheet linting clean. It now reports `#REF!`.

## [0.2.0] - 2026-09-13

### Added

- **`read()` and `describe()` read OpenDocument spreadsheets (`.ods`) into the same schema as `.xlsx`.** Ported from `particle-academy/holy-sheet`'s new reader, which has the full list of what maps (values, formulas translated to A1, repeats, merges, comments, styles, data styles, metadata) and what does not (frozen panes, column widths, fonts, function-name translation). The format is sniffed from the bytes, so the caller's branch on the file type can go. `OdsReader`, `FormatSniffer` and `UnsupportedFormatException` are exported.

  PHP semantics are reproduced where Python's builtins differ: half-away-from-zero rounding through `php_round`, ASCII-only `trim` / `strtolower` / `\d`, and explicit `is None` checks, because an XML element with no children is falsy. `tests/test_ods_reader_parity_php.py` diffs this reader against PHP on the PHP repo's fixtures, key order and int/float included, through a new `scripts/php_describe.php`; `tests/test_ods_reader.py` ports the PHP assertions. Both load the fixtures from the PHP checkout, as the parity suite already located its sources.

### Changed

- **Unreadable bytes now raise `UnsupportedFormatException` instead of a bare `RuntimeError`.** It subclasses `RuntimeError`, so **an existing `except RuntimeError` keeps working: do nothing.** The message for bytes that are not a zip still says "zip archive", which `test_reader.py` pins; only code matching the rest of the old text (`cannot open the input`, `missing xl/workbook.xml`) sees different wording.

### Fixed

- **`__version__` / `version()` read the INSTALLED distribution metadata instead of a literal.** The literal happened to agree with `pyproject.toml` today and had nothing keeping it that way — a second copy of a number that already exists. Reading the metadata deletes the copy rather than re-syncing it. `test_version_is_single_sourced.py` gains a check that fails if the literal is typed back in, which is the only part that runs without an install.

- **An empty `cells: []` validates.** PHP's `describe()` reports a sheet with no cells as `cells: []`, and the validator rejected an empty list as "not a map", breaking describe-then-write for a workbook with an empty sheet. PHP (2.2.0) and Node (2.3.0) are fixed the same way. A non-empty list is still an error.

## [0.1.0] - 2026-08-18

First release. The Python mirror of PHP `particle-academy/holy-sheet` 1.3.0 and
Node `@particle-academy/holy-sheet`.

### Added

- **The Agent surface**, as module-level snake_case functions: `validate`,
  `validate_and_repair`, `to_bytes`, `write`, `read`, `describe`,
  `tool_definition`, `from_array`, `from_csv`, `lint`, `version`. No class to
  instantiate and no DI container — `import holy_sheet` is the whole setup.
- **The declarative workbook schema**, taken as a plain `dict`. Row-oriented
  (`columns` + `rows`) and sparse (`cells`) sheets, four themes, symbolic totals,
  merged regions, column widths, frozen panes, comments, formulas with cached
  values, and per-cell or per-column formatting.
- **`XlsxWriter`** — a complete OOXML package with deduplicated
  fonts/fills/borders/numFmts. Output is deterministic: fixed part order, fixed
  1980-01-01 zip timestamps, the same bytes for the same input every time.
- **`XlsxReader`** — `read(bytes)` and `describe(path)` round-trip an xlsx back
  to a schema that can be written again unchanged. Handles shared strings, which
  this writer never emits but Excel always does.
- **`FormulaLinter`** — evaluates every formula and reports `#VALUE!`, `#REF!`,
  `#NAME?`, `#DIV/0!` and `#CIRC!` with an actionable hint, including the
  header-row off-by-one ("Did you mean B2? (it holds 12000)").
- **`Validator` + `Repairer`** — structured errors, and conservative repairs for
  the unambiguous mistakes agents make (`sheet` for `sheets`, `row` for `rows`,
  integer-keyed rows objects, stringified numerics, unknown themes, whitespace in
  A1 addresses, and an omitted date column type).
- **`from_array` / `from_csv`** with header-plus-sample type inference.
  `from_csv` accepts a path as well as content, following PHP.
- **`tool_definition()`**, returning the JSON Schema that is byte-identical
  across all three engines. It **raises** when the file is missing rather than
  returning an empty dict — an empty tool definition hands a model no hints and
  produces no error anywhere.
- **Cross-runtime parity as a test result.** `tests/test_parity_php.py` drives
  the PHP writer as a subprocess and asserts byte-identical OOXML parts for every
  fixture; `tests/test_reader_parity_php.py` asserts a PHP-written file describes
  identically to a Python-written one. Both FAIL under `CI` when PHP is absent
  rather than skipping.
- **`helpers/php.py`** — PHP's numeric and string semantics written down once:
  `is_numeric_string`, `numeric_string_to_number`, `php_round`,
  `php_number_format`, `format_float`, `php_to_string`, `type_of`. Every
  numeric decision in the package routes through it, and the Python builtins it
  replaces (`round`, `float`, `f"{v:.14f}"`, `str`) are never called on a value
  that reaches a cell.

### Notes for anyone porting or reviewing this

- **`bool` is a subclass of `int` in Python**, so every type branch checks `bool`
  first. A branch that does not writes `<v>1</v>` with no `t="b"` — a sheet
  showing 1 where the author wrote TRUE, with no error anywhere.
- **`round()` is banker's rounding and `f"{v:.14f}"` rounds half to even.** PHP
  does both half away from zero. The `numericHazards` fixture carries two exact
  ties at the 14th decimal so a half-even formatter fails the build.
- **Cells within a row are ordered lexicographically on the column letter**, so a
  sheet wider than 26 columns emits `A, AA, AB, …, B, C`. This is a shared wart
  that both existing engines have; it is replicated on purpose and pinned by the
  `wide` fixture, because changing it changes the bytes of every wide sheet in
  every engine at once.
- **The XML is built by string concatenation, and must stay that way.** Attribute
  order, self-closing style and `&apos;`-vs-`&#39;` are the cross-runtime
  contract. `xml.etree` reads; it never writes.
- **`number_format` implements the rule, not PHP's floating-point artefact.** PHP
  reaches its answer via a pre-rounding step whose result changed between 8.3 and
  8.4; reproducing it would tie this package's output to the maintainer's PHP
  build. The two agree everywhere except the 15th significant digit of values
  below 10, and the parity oracle proves it part-for-part.
- **Not ported, deliberately** (the Node port omits them too): PHP's `Laravel/`
  bridge, `Toolkit/`, and `Schema/Dumper` + `DumpOptions` (`dumpJson`).

[Unreleased]: https://github.com/Particle-Academy/holy-sheet-py/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Particle-Academy/holy-sheet-py/releases/tag/v0.1.0
