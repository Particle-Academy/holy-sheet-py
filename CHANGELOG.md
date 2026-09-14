# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**Pre-1.0: breaking changes land in MINOR releases.** Until `1.0.0`, a bump from
`0.1.x` to `0.2.0` may change or remove API. Pin accordingly, and read the entry
before upgrading — every breaking change here says what you have to DO, not just
what moved.

## [Unreleased]

## [0.3.2] - 2026-09-15

### Fixed

- **A `columnWidths` key that is not a column index made `to_bytes()` raise.** The
  normalizer did `int(key)`, so `{"abc": 999}` raised `ValueError` from
  `to_bytes()`, `write()` and `diff()`. PHP overwrote column A with it and Node
  wrote a NaN column; all three follow one rule now, mirroring PHP holy-sheet
  2.3.4 (`holy_sheet/schema/column_widths.py`):
  - a **key** is a 0-based column index from 0 to 16383, as an `int` or a string
    of ASCII digits;
  - a **width** is a non-negative finite number (not a `bool`), or a string of
    digits (`"80.5"`).
  - `validate()` reports each other entry by path (`sheets[0].columnWidths.abc`),
    so `write()` and `to_bytes()` refuse it with `SchemaException` instead of
    `ValueError`.
  - `validate_and_repair()` turns a one- or two-letter key into its index (`"B"`
    is 1) and drops an entry it cannot repair, and lists both.
  - The normalizer skips such an entry.

  **What you must do:** nothing, unless you caught `ValueError` from a bad width;
  it is `SchemaException` now, from `validate()` first.

## [0.3.1] - 2026-09-15

### Fixed

Six defects in the op code, mirroring `particle-academy/holy-sheet` 2.3.3, which is the reference. 0.3.0 reproduced PHP 2.3.1's behaviour exactly, and five of these were PHP defects this port found and reported (fixed in PHP 2.3.2); the sixth was fixed in PHP 2.3.3. Each has a test ported from PHP's `SheetOpsTest.php`, and each fails against 0.3.0.

- **`op_schema()` rejected most `set_column_widths` ops `diff()` emits.** PHP encodes widths keyed 0..n-1 as a JSON list (`[120, 80, 140]`), and 0.3.0 allowed only an empty list. `columnWidths` is now an object or an array of non-negative numbers, a list indexed by position; the schema matches PHP 2.3.3's key for key, description included.
- **An op with a non-string `type` could remove a sheet.** 0.3.0 mirrored PHP's loose `switch`, so `type: True` matched `remove_sheet`. A `type` that is not a string naming an op type now skips the op.
- **A padded address wrote a key of its own.** `set_cell` stored `" a1 "` under `" A1 "`, and `clear_cell` could not reach A1 with it. Both now trim with PHP's `trim()` set (space, tab, LF, CR, NUL, vertical tab; not NBSP) before upper-casing.
- **A column-width key that is not a column index was read as column A** by `insert_columns` and `delete_columns` (`(int) "abc"` is 0), and could overwrite column A's width. It is dropped. An int key and a string of ASCII digits (`"007"`) are still indexes.
- **Two values JSON cannot hold compared as the same.** `SheetDiff.same()` encoded both to `""`, so a cell going from NaN to infinity, or between two strings holding different lone surrogates (invalid UTF-8 in PHP), recorded no change. It now raises `ValueError`, as PHP throws `JsonException`, for NaN, an infinity, an int too large for a float, a lone surrogate in a string or a key, and more than 4096 nested arrays (PHP's depth, where every array counts, an empty one included). `diff()` raises with it. The comparison walks an explicit stack instead of recursing, so the depth limit is PHP's rather than Python's recursion limit. `reduce()` is not changed and still deep-copies its result with `copy.deepcopy`, which raises `RecursionError` for a value nested past about 490 levels, where PHP has no limit.
- **An op with a position or count that is not a number moved or unfroze things.** `add_sheet.index`, `move_sheet.toIndex`, `set_frozen.rows`/`cols` and the row and column ops' `at`/`count` were read with PHP's `(int)` cast, so `toIndex: "last"` moved a sheet to the front, `index: "end"` inserted one there, and `rows: "one"` unfroze the panes. A present value that is not an int or a string of ASCII digits (`None`, `True`, `2.0` and `"1e3"` included) now skips the op; absent keys keep their defaults (`add_sheet` appends, `move_sheet` stays, `set_frozen` uses 0). As in PHP, the check covers every op that reaches a sheet, so a junk `count` on a `set_cell` skips it too. `SheetReducer.integer()` is PHP's helper for the rule.

`tests/test_sheet_ops_parity_php.py` gains PHP-checked reducer cases for each fix, diff cases for a list of widths and a padded cell key, and `SheetDiff.same` at 4095 to 4097 levels, which `scripts/php_ops.php` builds in PHP (a new `nested` call) so the nesting never crosses JSON.

**What you must do:** nothing, unless you relied on one of the above. `diff()` output changes only for a schema holding a padded cell key, where it matches PHP 2.3.3's, and `diff()` now raises on a value JSON cannot hold, which parsed JSON never contains.

## [0.3.0] - 2026-09-15

### Added

- **`diff()`, `reduce()`, `op_schema()` and `equivalent()`: a workbook's versions stored as ops** (holy-sheet [#7](https://github.com/Particle-Academy/holy-sheet/issues/7)). Hashing xlsx bytes cannot keep a one-cell edit small, because a zip changes nearly every byte, so a version history had to store a whole file per edit. `diff(new, old)` is the op list that restores `old` from `new`. Ported from `particle-academy/holy-sheet` 2.3.0, which is the reference, with the op schema as corrected in 2.3.1.
  - `reduce(a, diff(a, b))` equals `b`, key order aside. The ops are verified by replaying them: a sheet the granular ops cannot reproduce is replaced whole, and so, as a last resort, is the workbook.
  - One changed cell is one `set_cell`. Rows and columns are aligned by content first, so an inserted row is one `insert_rows` plus its cells rather than every cell below it rewritten.
  - Schemas that write the same workbook diff to `[]`, so a save without a change records nothing. A columns/rows sheet and the cells it becomes are the same, and so is the creation time the writer stamps on a schema that names none.
  - `set_cell`, `set_range` and `set_workbook` are fancy-sheets' `SheetOp` shapes and behave as its reducer does (a `set_cell` without a formula clears it and keeps the format). The rest are holy-sheet's: `clear_cell`, `insert_rows`/`delete_rows`, `insert_columns`/`delete_columns`, `add_sheet`/`remove_sheet`/`rename_sheet`/`move_sheet`/`replace_sheet`, `set_merged_regions`, `set_column_widths`, `set_frozen` and `set_meta`.
  - Row and column ops move cells, merged regions and column widths. They do not rewrite formula text; a formula that changes with an insert is its own `set_cell`.
  - `op_schema()` accepts `set_column_widths` with an empty `columnWidths` array, which is what `diff()` emits when every width is removed (PHP 2.3.1's fix, taken here before the first release).

  **The same ops as PHP, not merely ops that work.** A history written by one runtime is replayed by the other, so `tests/test_sheet_ops_parity_php.py` sends 214 calls to the PHP reference through a new `scripts/php_ops.php` and compares each result as the JSON PHP prints: op order, op key order, int versus float. The cases are every edit in both directions, the branches of the rename pairing, a seeded run of random op lists (diffed and reduced), the reducer's edge cases, `equivalent` pairs, the op schema and `hunks`. `tests/test_sheet_ops.py` ports PHP's `SheetOpsTest.php` case for case, including its seeded random-edit run (`random.Random(20260915)`).

  Python differs from PHP where its builtins do, and the port follows PHP each time: `True == 1` and `1 == 1.0` in Python but not in PHP's comparisons; `(int)` is not `int()`; a PHP array is both a list and a map, so `{"0": 120}` is `[120]`. `columnWidths` keys arrive from JSON as strings, and a column insert or delete returns them as integers (`(int) $key`, as PHP does, and the keys `describe()` returns). `SheetDiff`, `SheetReducer` and `SheetOpSchema` are exported under their PHP names.

  **What you must do:** nothing; this only adds functions. One side effect: `from holy_sheet import *` now binds `diff` and `reduce`, so a star import placed after `from functools import reduce` replaces it.

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
