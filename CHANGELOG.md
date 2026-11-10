# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**Pre-1.0: breaking changes land in MINOR releases.** Until `1.0.0`, a bump from
`0.1.x` to `0.2.0` may change or remove API. Pin accordingly, and read the entry
before upgrading — every breaking change here says what you have to DO, not just
what moved.

## [Unreleased]

### Changed

- **The agent guide (`guide()`) catches up with the PHP package's 2.10 to 2.14**
  (this package's 0.9 to 0.13). New: building a large or formula-heavy workbook
  in passes, checking after each; fixing only the cells a lint issue names
  (`lint` -> ops checked against `op_schema()` -> `reduce`), with a worked
  example; `diff`, `equivalent` and `validate_and_repair`; data validation;
  border styles; reading with `sheet` and `limits`, `rows`,
  `convert_template`, and refusals that name the limit. Byte-identical to the
  PHP package's copy (new SHA-256 in all three repos). **What to do:** nothing;
  guide text only, a patch.
- Dev only: `tests/test_agent_guide_covers_the_api.py` fails when an exported
  `holy_sheet.agent` function is not named in the guide (snake_case or its
  camelCase form) and not on its allowlist, where each entry carries its reason
  (`guide`, `version`). It failed on the old guide, which named none of 12.

## [0.13.0] - 2026-11-07

The port of PHP holy-sheet 2.14.0. Owner decision, 2026-11-07: "all
limitations need to be a config option so apps can build to their use case."
Every default stays where it was; an app can now set every limit a read
enforces. Same names, defaults, refusals and messages as PHP and Node, held to
PHP's cases (`read-options/cases.json`).

### Added

- **Every limit is configurable.** `read(data, limits=...)` and
  `describe(path, limits=...)` now take the archive guard's five limits beside
  the four read limits: `maxArchiveBytes`, `maxEntries`, `maxPartBytes`,
  `maxExpandedBytes`, `maxCompressionRatio`, `maxRows`, `maxColumns`,
  `maxCells`, `maxSharedStrings`. Until now `read()` and `describe()` opened the
  archive with no limits passed at all (`open_for_reading`), so a sheet part
  over 64 MiB (about 120,000 rows of 10 columns) could not be read whatever the
  app wanted. `DEFAULT_ARCHIVE_LIMITS` is now exported.
  **What to do:** nothing; every default is unchanged. To read a large export,
  raise the limits it needs; the README's "Large workbooks" section has the
  measured memory and time per row.
- **A refusal over a limit says which one.** `ArchiveRefused` now carries
  `limit`, `max` and `actual` (None when the refusal is not about a limit), and
  an `archive_limit` message names them: `Source archive refused: archive_limit
  (maxPartBytes is 67,108,864; this file needs at least 275,497,997)`.

### Changed

- **The read limits are now counted BEFORE the part is parsed, not after.**
  The worksheet was parsed into an element tree and counted afterwards, so with
  `maxPartBytes` raised, a 500,000-row upload grew to 4.8 GB before `maxCells`
  could refuse it. Rows, columns and cells are now counted by streaming the
  part through the same expat parser with a target that builds nothing
  (`stream_xml`), and the cell count refuses the moment the workbook passes
  `maxCells`; shared strings are counted the same way. Measured peak at
  refusal, 500,000 rows x 10 columns (a 275,497,997-byte sheet part):
  - at the **defaults**: refused by `maxPartBytes`, from the zip's central
    directory before anything is inflated, in 0.01 s at a **40 MiB** process
    peak;
  - with the part limits raised and `maxCells` at 1,000,000: refused by
    `maxCells` in 4.1 s at **591 MiB**, not 4.8 GB.
  A worksheet or shared-strings part cut off mid-document is counted up to the
  cut, so a truncated part over a limit is now refused; before, it parsed as
  malformed and read as empty, unseen by any limit.
  **What to do:** nothing; reads that succeeded still succeed with the same
  result. The stream replaces the old walk of the parsed tree, and measured
  4 to 8% more time on a full read (5,000,050 cells: 94.5 s against 87.6 s).
- **A bad limit setting names itself**: `read_limits (maxRows must be a
  positive integer)`, `archive_limits (maxPartBytes must be a positive
  integer)`, `read_limits (unknown limit "maxBogus")`. The reasons are
  unchanged. **What to do:** nothing, unless you compare `str(e)` exactly;
  compare `reason`.
- **`read()` and `describe()` open the archive once.** The format sniffer
  opened and inflated every part, then the reader opened them all again.

### Fixed

- **`describe(path)` read the whole file before checking its size.** It
  called `handle.read()` with no bound, so the archive guard's
  `maxArchiveBytes` only applied after the file was in memory. It now refuses
  before reading, as PHP and Node do, and reads no more than the limit.
  **What to do:** nothing.

## [0.12.0] - 2026-11-07

The port of PHP holy-sheet 2.13.0, approved in the change council as
CC-fancy-0012 and CC-fancy-0013 (owner, plus MOIC and Tynn, 2 of 2 each). The
same options, defaults, refusals and messages as PHP and Node, held to PHP's own
cases (`holy-sheet/tests/fixtures/read-options/cases.json`, run by
`tests/test_read_options.py`).

### Added

- **Read one sheet by name: `read(data, sheet="Contacts")`** (and
  `describe(path, sheet=...)`). Only that sheet is parsed. Names match exactly.
  An unknown name raises the new `SheetNotFoundException` (a `ValueError`) with
  `sheet`, `available` and a message listing every sheet. Works for `.ods` too.
  **What to do:** nothing; without `sheet` a read returns every sheet as before.
- **`rows(sheet, *, formulas="computed", max_cells=None)`: a read sheet as rows
  of values in column order.** A blank row in the middle is `[]`, so indexes are
  row numbers minus one; trailing blank rows are not padded. A formula gives its
  cached `computedValue` by default (None when none was cached);
  `formulas="formula"` gives the formula with its `=`. A merged region's value
  is at its top-left only. The view refuses past `max_cells` positions
  (`ReadLimitExceeded`) before it pads anything.
  **What to do:** nothing; it is new.
- **Border styles beyond thin.** A border side is a bare colour (thin, byte for
  byte as before) or `{"style", "color"}`, `style` one of `thin`, `medium`,
  `thick`, `dashed`, `dotted`, `double` (thin when omitted), `color` `#RRGGBB`
  (black when omitted). `validate()` reports a bad side at its path, with PHP's
  texts; `read()` gives a thin side back as the bare colour and another style as
  the object. Byte-identical to PHP (the `borderStyles` parity fixture). The
  shared JSON Schema gains a `Border` definition (new SHA-256 in all three
  repos).
  **What to do:** nothing. To use a style, give the side a dict.
- **`to_csv(table, options=None)`, a CSV writer matching PHP's
  `Exchange\Csv::toCsv()` byte for byte**, text and report: RFC 4180, CRLF line
  ends, formula-injection escaping on by default (a leading `=`, `+`, `-`, `@`,
  TAB or CR gets a `'`, reported `csv-escaped`; only host code should pass
  `{"escapeFormulas": False}`). Options and report keys are PHP's camelCase, so
  one JSON payload drives all three engines. Bad options raise the new
  `OptionsInvalid` (a `ValueError`). Held to PHP call for call by
  `tests/test_csv_parity_php.py` over `holy-sheet/tests/fixtures/csv/tables.json`.
  One deliberate difference: a table whose rows do not form a grid past the
  first bad row raises `TypeError` here, where PHP reads past its layout with a
  warning and writes a garbled row.
  **What to do:** nothing; it is new. Use it rather than joining fields
  yourself, which is how formula injection gets in.
- **Read limits on rows, columns, cells and shared strings**, through
  `read(data, limits={...})` / `describe(path, limits={...})`, with
  `DEFAULT_READ_LIMITS` exported: `maxRows` 1,048,576 and `maxColumns` 16,384
  (Excel's own sheet limits, per sheet), `maxCells` 20,000,000 (the workbook)
  and `maxSharedStrings` 20,000,000. The keys are PHP's camelCase, like the
  archive limits'. Each is checked before what it counts is built: a worksheet
  is counted from its parsed part before one cell exists, the shared-strings
  table before one string is rendered, an `.ods` repeat before it is expanded. A
  bad value (a `bool` included) or unknown key is
  `ArchiveRefused("read_limits")`. A refusal is the new `ReadLimitExceeded` (an
  `ArchiveRefused`, reason `read_limit`) with `limit`, `max`, `actual` and a
  message for people: `Spreadsheet refused: a sheet has 1,200,000 rows, over
  the limit of 1,048,576 rows (maxRows) by 151,424.` Why each default is what
  it is, and the measurements behind them (1,000,000 x 10 = 10,000,010 cells
  and 300,000 x 50 = 15,000,050 cells, both admitted), are in PHP holy-sheet's
  2.13.0 entry; this port measured 905 bytes per cell at its tracemalloc peak
  reading 1,100,010 cells.
  **Measured limits you will meet first.** Neither measured workbook can be read
  today, with or without this release: the archive guard refuses their worksheet
  parts (553 MB and 865 MB) against `maxPartBytes` of 64 MiB, so 10 mixed
  columns stop at about 120,000 rows. That is recorded here, not changed.
  **What to do:** nothing for an ordinary workbook. If you show refusals to
  people, catch `ReadLimitExceeded` (or keep catching `ArchiveRefused`) and show
  `str(e)`.

### Security

- **One `<col>` element could exhaust a read.** The column-width reader expanded
  `<col min="1" max="...">` one column at a time, so a 1.5 KB file with
  `max="2000000000"` ran a loop two billion iterations long. The default
  `maxColumns` now refuses it before the loop, and an `.ods` cell repeated
  across rows and columns is bounded by `maxCells`.
  **What to do:** upgrade if you read uploaded files.

## [0.11.0] - 2026-11-07

### Added

- **Data validation: dropdowns and bounds**, mirroring PHP holy-sheet 2.12.0. A
  sheet's `dataValidations` list (`{range, type, ...}`) and a column's
  `validation` (covering that column's data rows) write Excel data validation:
  `type: "list"` is a dropdown whose choices come inline from `values` or from
  cells through `source` (on this sheet or another); `whole`, `decimal`, `date`
  and `textLength` bound the entry with `operator` (`between`, `notBetween`,
  `equal`, `notEqual`, `greaterThan`, `lessThan`, `greaterThanOrEqual`,
  `lessThanOrEqual`) and `min` / `max` or `value`. `allowBlank`,
  `showDropdown`, an input `prompt` and an `error` alert (`stop`, `warning` or
  `information`) are optional. `validate()` reports a bad rule at its path in
  PHP's wording, including an inline list over Excel's 255-character limit;
  `validate_and_repair()` fixes only the case of a type, operator or error style,
  and never mutates the schema it is given. The worksheet part is byte-identical
  to PHP's (new `dataValidations` parity fixture). The reader does not return
  validations yet.
  **What to do:** nothing unless you want dropdowns. Workbooks without
  validations are written byte for byte as before. To add one, see the README's
  "Data validation" section; a template's dropdown usually belongs in
  `dataValidations` with a range past the sample rows, like `"B2:B500"`.

## [0.10.5] - 2026-11-07

Mirrors `particle-academy/holy-sheet` 2.11.5.

### Security

- **A `DOCTYPE` in any XML part now refuses the file, instead of reading as an
  empty part.** 0.10.4 stopped such a part from being expanded but read it as
  absent, so a worksheet carrying one came back as an EMPTY sheet, which a
  caller cannot tell from a sheet that holds nothing. `read()` and `describe()`
  of a file with a `DOCTYPE` in any part now raise `UnsupportedFormatException`
  (a `RuntimeError`), as one in `[Content_Types].xml` already did. A missing
  part is still just absent, and a part that is merely malformed, or in an
  encoding the parser cannot read, is handled exactly as before.
  **What to do:** nothing for real spreadsheets, which carry no `DOCTYPE`. If you
  read files you did not write and catch `RuntimeError` around a read, you
  already handle this; if you relied on such a file reading with empty sheets,
  it is now refused.

### Fixed

- **Workbooks in the 1904 date system read every date four years early.** The
  reader ignored `<workbookPr date1904="1"/>` and counted every serial from
  1899-12-30, so a date in such a file (older Mac Excel saves) came back 1462
  days too early. It now uses the 1904-01-01 epoch for that workbook.
  **What to do:** nothing. Dates in 1904-system files now read correctly; files
  in the default 1900 system read exactly as before. Writing one back produces
  the same dates in the 1900 system.
- **A rich inline string read as `None`.** A cell holding
  `<is><r><t>..</t></r></is>` lost its text. Its runs are now joined, exactly as
  a rich shared string's already were.
  **What to do:** nothing. Those cells now have their text.
- **BEHAVIOUR CHANGE: the cached value of a date-formatted formula is now an ISO
  string.** `computedValue` came back as the raw serial (`46334`) while the
  cell's own `value` was converted, so a date formula read as a number nobody
  wrote. It is now converted like `value`: `"2026-11-08"` for a date format,
  `"2026-11-08T06:00:00Z"` for a datetime one, in both xlsx and ods. Writing
  that output back turns it into the serial again, so `read()` output
  round-trips through `to_bytes()`.
  **What to do:** if your code reads `computedValue` of a date-formatted formula
  as a number, read it as an ISO date string instead (parse it, or check
  `isinstance(v, str)` first if you must handle both). Formulas that are not
  date-formatted are unchanged.
- **A column's `format` code was ignored.** The schema has documented
  `columns[].format`, a custom Excel number-format string that overrides the
  type-derived format, but the normalizer never read it. It is now written as a
  custom number format and applied to every cell in the column, including the
  totals row; the column `type` still decides the value (a `date` column's
  strings still become dates). A cell that sets its own `displayFormat` still
  wins. `validate()` now reports a `format` that cannot be written (not a
  string, empty, or longer than 255 bytes) at `sheets[i].columns[j].format`,
  with PHP's wording. Byte parity with PHP is pinned by a new `columnFormats`
  parity fixture.
  **What to do:** nothing, unless you set `format` on a column and relied on it
  having no effect. Remove it to keep the type-derived format.

### Changed

- **The shared conformance fixtures are pinned at 0.46.0** (were 0.34.0), in the
  test and in CI's checkout together. Re-run first: shared/decimal 18 passed, 0
  failed, 0 skipped, the same count as at 0.34.0. Test-only.
  **What to do:** nothing.

## [0.10.4] - 2026-11-04

### Security

- **The reader's `DOCTYPE` refusal now holds for every part of an xlsx or ods.**
  The earlier check did not cover every part as written, so a crafted file could
  define its own entities and have them expanded into the values and text the
  reader returned. No file contents were reachable this way and no external file
  was loaded. All parts now go through one gate, which behaves as PHP's does.
  **What to do:** upgrade if you read files you did not write. No code changes.
  Real spreadsheets carry no `DOCTYPE`, so they read as before. A refused
  `xl/workbook.xml` or ods `content.xml` still raises `RuntimeError`, and any
  other part is read as absent, as a malformed part always was.
- **A package whose `[Content_Types].xml` carries a `DOCTYPE` is now refused.**
  It was treated as declaring nothing, so the checks that depend on what a
  package declares did not run on it. Reading or converting one now raises
  `UnsupportedFormatException`, a `RuntimeError`.
  **What to do:** nothing, unless you catch `OfficeTemplateRefused` alone around
  a read.

### Fixed

- **A part declaring an encoding this parser cannot read no longer escapes the
  reader as a bare `ValueError`.** It counts as a refusal: an absent part, the
  `RuntimeError` above for the workbook part, or `UnsupportedFormatException` for
  `[Content_Types].xml`. PHP can decode some of these, so such a file reads there
  and not here.
- **Text that only looks like a `DOCTYPE` is no longer refused.** A shared string
  or chart title whose CDATA holds an HTML page near the start of its part was
  dropped along with the rest of that part. It now reads.

## [0.10.3] - 2026-10-28

### Fixed

- **Data loss in Google Sheets for sheets wider than 26 columns.** Cells within
  a row were written in string order of the column letter (`A, AA, AB, ..., B,
  C`) instead of the ascending column order OOXML requires. Google Sheets drops
  every cell in such a row after the first out-of-order one, silently: a row
  written `A1, AA1..AE1, B1..Z1` came back from a Sheets import/export holding
  only `A1`. Cells are now written `A..Z, AA, AB, ...`, byte-identical to PHP
  holy-sheet. LibreOffice 26.2 read both orders without loss; Excel was not
  measured. Sheets of 26 columns or fewer were never affected, and their bytes
  do not change.
  **What to do:** regenerate any file wider than 26 columns that was written by
  an earlier version and may be opened in Google Sheets. Nothing else changes:
  no API, schema or reader behaviour moved.

## [0.10.2] - 2026-10-28

### Fixed

`lint()` now follows Excel's formula semantics in the places below, ported from
PHP holy-sheet 2.11.1 (plus lazy `IF`, which all three linters gain together).
What you will notice: **fewer false `#NAME?` and `#DIV/0!` reports** on
formulas Excel accepts, and **some new true positives** on formulas Excel
rejects or errors on that the linter used to pass. `lint()`'s signature and the
issue shape `{sheet, address, formula, error, hint}` are unchanged. Nothing to
do unless you assert on the exact set of issues a formula produces. Error labels
and hint texts are byte-identical to PHP and Node.

- **`IF` evaluates only the branch it takes.** `=IF(A2=0,0,1/A2)` with A2 = 0
  used to lint as `#DIV/0!`; it now lints clean, as does any error in the
  untaken branch. That branch is still parsed, so a syntax error or an unknown
  function there is still `#NAME?`. An error in the condition still propagates,
  after the whole `IF` has been parsed. `IFERROR` is not in the linter's
  vocabulary and is still `#NAME?`.
- **`%` is postfix percent.** `=50%` is 0.5 and `=A1%` is A1/100; these used to
  lint as `#NAME?`. A binary `%` such as `=10%2`, which the linter used to
  evaluate as `10/100*2`, is now reported, because Excel rejects it.
- **`^` binds tighter than `*` and `/`.** `=2*3^2` is 18, not 36. It stays
  left-associative (`=2^3^2` is 64) and below negation (`=-2^2` is 4), as in
  Excel.
- **A doubled quote inside text is one quote.** `="a""b"` is the three
  characters `a"b`; it used to lint as `#NAME?`. A text literal that never
  closes (`="abc`) is now reported instead of passing.
- **IF reads its condition as Excel does.** Text is TRUE or FALSE only when it
  spells one of them, in any case: `"false"` used to count as TRUE. Any other
  text, `""` included, is now `#VALUE!` (a new true positive; it used to pass).
  An error in the condition now propagates instead of picking a branch.
- **LEN counts UTF-16 code units, as Excel does.** `=LEN("😀")` is 2, not 1:
  `len()` counts code points, and Excel (and PHP and Node) count a character
  outside the Basic Multilingual Plane as two.
- **Comparisons follow Excel's type order.** Numeric text is text, and Excel
  sorts text above every number: `="5">9` is TRUE and `="10"=10` is FALSE (both
  used to compare as numbers). Text compares without regard to case
  (`="a"="A"` is TRUE), logicals sort above text, and a blank cell equals 0,
  `""` and FALSE. An error on either side now propagates instead of being
  compared as text.
- **A `#NAME?` hint says which kind it is.** The label is still `#NAME?`, but
  every cause used to get one hint, "references an unknown function or has a
  syntax error". A malformed formula now gets `Syntax error: <what and
  where>...`; an unknown function gets `Unknown function VLOOKUP. ...`; a
  `#NAME?` inherited from another cell names that cell; and an exception inside
  the linter itself says it is a linter defect. Match on the hint's prefix if
  you need to tell them apart.
- **An unknown function is `#NAME?` wherever it sits.** `=IF(FALSE, FOO(1), 1)`
  used to pass, because the error was a value that IF discarded. Excel resolves
  names first, so it is `#NAME?` there and now here (a new true positive).
- **The tokenizer uses PHP's ASCII character classes.** A no-break space, or a
  letter such as `é` outside a text literal, is now a syntax error, as it is in
  PHP; Python's Unicode-aware `isspace` / `isalpha` accepted them. A digit
  such as `²` used to reach `float()` and fail inside the linter.

## [0.10.1] - 2026-10-27

### Fixed

- **An explicit `{"value": <string>}` cell is now written as a string,
  exactly as given**, matching PHP holy-sheet and holy-sheet-js.
  Numeric-string coercion used to run on the object form too, so
  `{"value": "+1"}` was written as the number 1, `{"value": "007"}` as 7 and
  `{"value": "1e5"}` as 100000: a phone prefix, zip code or part number lost
  its characters with no error. This applies in `rows` and in the sparse
  `cells` map, under any column type or number format, and to a `set_cell`
  op's `value`. Unchanged: a BARE string in a row or a `cells` entry is still
  coerced (`"+1"` is still 1) and a bare `"=..."` is still a formula; an
  object value that is already a number is still a number; and a string under
  a `date` / `datetime` display format is still converted to a date serial.
  `read()` returns such a cell as the same string. A new `explicitStrings`
  parity fixture compares the parts with the PHP engine.
  **What a consumer must do:** if you relied on `{"value": "100"}` becoming a
  number, pass the number (`{"value": 100}`) or the bare string (`"100"`).
  Otherwise nothing.

## [0.10.0] - 2026-10-27

### Added

- **Excel `.xltx` templates read, and `.ots` templates marked.** `read()` /
  `describe()` read an `.xltx` exactly as its `.xlsx` twin and add
  `"sourceKind": "template"`; an `.ots` (already read, unmarked) now says so
  too. `Workbook` gains `source_kind` (default `"document"`). Document reads are
  unchanged and carry no `sourceKind` key, so nothing to do unless you want to
  tell the two apart. Mirrors PHP and Node holy-sheet.
- **`holy_sheet.convert_template(data_or_path, to=None)`**, the `holy-sheet`
  console script and `python -m holy_sheet`: `template:convert <in> <out>
  [--to=document|template] [--force]` turns a template into its workbook twin
  and back. Only the declaration is rewritten: the workbook part's content-type
  value (OOXML), or the `mimetype` entry plus the manifest root's `media-type`
  (ODF 1.3 Part 2 section 3.3 requires them to agree). Every other zip member is
  copied as its original record, byte for byte; `mimetype` stays first and
  stored.
- `ArchiveRefused` and `OfficeTemplateRefused`, each with a constant `reason`.

### Changed

- **A macro-enabled template (`.xltm`) is now refused** with
  `OfficeTemplateRefused("macro_enabled")`; it used to be read as a workbook
  with its VBA silently ignored. So is an `.xltx` carrying a `vbaProject.bin`.
  An `.xlsm` still reads as before but is never converted.
- A package that declares a template of another family is refused with
  `family_mismatch` instead of being read as whatever its parts looked like.

### Security

- **Reads now pass the bounded archive guard PHP and Node already ran.** Every
  reader here opened uploads with a bare `zipfile.ZipFile` and inflated whatever
  the central directory declared: no ceiling on a part, the total or the
  compression ratio, and no refusal of unsafe or duplicate member names. They
  now go through `SourceArchive`, a stdlib-only port of PHP's validation: 64 MiB
  archive / part, 256 MiB expanded, 16,384 entries, 1000:1 ratio, coherent local
  and central records, CRC and exact sizes, bounded inflation. Refusals raise
  `ArchiveRefused` (a `RuntimeError`) with the same `reason` codes as PHP and
  Node. **What to do:** nothing for a well-formed workbook. Bytes that are not a
  zip still raise `UnsupportedFormatException`; a damaged zip that used to raise
  that (via `BadZipFile`) now raises `ArchiveRefused("archive_corrupt")`, as it
  does in PHP and Node, so catch `RuntimeError` if you caught both.

## [0.9.1] — 2026-10-19

### Fixed

- **A bar or column chart's value axis starts at zero** when every plotted value
  is zero or more. Left to the renderer, an axis over values like 90-97 started
  near 88, so a three-point gap read as a collapse; reviewers gated an
  agent-built deck for exactly that. A chart with a negative value, and a line
  chart, keep the renderer's own scaling. **What to do: nothing**; bar charts
  you already write now carry `<c:min val="0"/>` on the value axis.

## [0.9.0] — 2026-10-19

### Added

- **An agent guide ships with the package: `agent-guide/professional-workbooks.md`, and
  `holy_sheet.guide()` returns it.** What makes a workbook professional and the schema
  idioms that produce it, distilled from rubric-judged agent builds in the
  Fancy document lab: every rule in it moved a measured score. Give it to a
  model together with the JSON schema; it works with any agent loop. The same
  bytes ship in the PHP, Node and Python packages (a test in each pins the
  hash). **Nothing to do**; nothing else changed.

## [0.8.0] — 2026-10-19

### Added

- **`print.fitToPage`: one page wide AND tall.** `fitToWidth` scales a sheet
  to one page wide and lets it run as many pages tall as it needs, so the page
  break fell through the chart below a board summary in three of four
  agent-built workbooks reviewed. `fitToPage: true` scales the whole sheet,
  table, notes and chart, onto one sheet of paper.
- **`charts[].series[].points`: colour single points.** `[{"index": 2,
  "color": "#C2410C"}]` colours the one bar, slice or line marker the chart is
  about (index 0-based within `values`). Reviewers marked down every comparison
  chart whose bars were all one colour, because it did not say which bar
  mattered. Written as DrawingML data points, read back by `describe()` /
  `read()`, validated with a hint per point, and published in the schema.
- **Nothing to do**: a workbook that uses neither is byte-identical to before.

## [0.7.0] — 2026-10-19

### Added

- **`charts`: native Excel charts over cell ranges.** A sheet takes
  `charts: [{type, title?, categories?, series: [{name?, values, color?}],
  position?: {from, to}, dataLabels?, legend?, format?}]`. `type` is `column`,
  `bar` (horizontal, for long labels), `line` or `pie`. Ranges are A1 ranges on
  the chart's own sheet (`"B5:B7"`) or another (`"'Q3 Revenue'!B5:B7"`).
  `position` anchors the chart between two cells and defaults to beside the
  data. `format` is an Excel number format for the axis and labels (`"$#,##0"`).
  Agent-built workbooks reviewed for a board were marked down for having no
  chart where the point was comparison, and there was no way to make one.
- These are real charts, not pictures: edit a figure and the chart follows. The
  values are cached too, so a viewer that does not recalculate still draws them.
  Styling is quiet on purpose (grey 9pt axis text, light gridlines, no tick
  marks, a bold 12pt title) so a chart reads as part of the sheet.
- Read back by `describe()` / `read()` in the same shape, validated with a hint
  per field, and published in the schema. Byte-identical across the PHP, Node
  and Python engines.
- **Nothing to do**: a workbook without `charts` is byte-identical to before. A
  sheet with comments keeps their relationship ids; the drawing takes the next.

## [0.6.0] — 2026-10-19

### Added

- **`print`** (`orientation`, `fitToWidth`, `footer` with `{page}` / `{pages}`)
  and **`rowHeights`** (pixels by 1-based row number). Read back, validated and
  published in the schema. **Nothing to do**: a workbook without them is
  byte-identical. Same as `particle-academy/holy-sheet` 2.6.0 and
  `@particle-academy/holy-sheet` 2.7.0; parity holds (`printSetup` fixture).

### Fixed

- **`tool_definition()` served a stale schema.** This port's copy of
  `holy_sheet.schema.json` had not changed since the port was created and still
  described "v0.2.0" (7.8 KB against the shared file's 11 KB), so an agent using
  the Python tool was told about a fraction of what the writer accepts. It is
  now byte-identical to the PHP and Node copies, as the sync test always
  claimed it would be.

## [0.5.0] — 2026-10-11

### Fixed

- **A cell value that cannot be represented is REFUSED and names the cell,
  instead of being written as its `repr` — `['x']` in the cell.** Mirrors the PHP
  reference fix in `particle-academy/holy-sheet` 2.5.0 ([holy-sheet#9], reported
  from MOIC production); tracked here as [#1]. PHP is normative for semantics in
  this family, so the quirk was fixed there first and this is the mirror.

  `_coerce_value()` ended with "not a scalar, so it must be safe to stringify":

  ```python
  if not isinstance(value, (str, int, float, bool)):
      return str(value)   # a LIST reaches this line
  ```

  A list gets there whenever a plain `rows` form is nested one level too deep —
  `rows: [[["x"]]]` instead of `rows: [["x"]]`. No object-cell syntax is needed,
  which is exactly the shape an AI-generated edit produces. **In Python a TUPLE
  does it too**, a shape neither peer runtime can express.

  Measured through each normalizer rather than through `str()`, because the
  three corruptions differ and the difference decides which is dangerous:

  | cell | PHP (before) | Node (before) | this port (before) |
  |---|---|---|---|
  | `["x"]` | `"Array"` | `"x"` | `"['x']"` |
  | `["x","y"]` | `"Array"` | `"x,y"` | `"['x', 'y']"` |

  This port's was the **visible** kind — someone opening the sheet sees `['x']`
  and files a bug, where Node silently wrote exactly what the author meant. Both
  were corrupt, and neither named the cell, which was the actual production
  complaint: not that it failed, but that it said nothing about *where*.

  The guard now accepts a type that defines its own `__str__` (PHP's
  `Stringable`; `__repr__` alone does not count, because a repr is a debug
  representation and never a cell value) and refuses everything else — with
  **one exclusion that is the trap in this guard**: `bytes` *does* define its
  own `__str__`, and it yields `b'x'`. Measured, not assumed. The Node port has
  the identical trap with a different type, so each port names it in a test.

  The refusal is a `SchemaException` whose structured error names the cell as
  `Sheet!A1`, with a hint explaining the nesting mistake. That differs from the
  `sheets[0].rows[1]` schema path used elsewhere, deliberately: this fault is
  always one cell, and the A1 location is what an operator can act on.

- **`validate()` no longer passes a schema that `normalize()` will throw on.**
  The same input returned **zero** validation errors, so `validate()` told a
  caller the workbook was fine and the write then failed — moving the failure to
  where it is hardest to attribute, in the exact validate-then-write order an
  agent uses. It now reports `sheets[0].rows[0][0]` for a list cell.

  The check is `is_list` (PHP's `array_is_list`, so a tuple counts), **not**
  `is_array`: a dict cell is the `{"value": …, "format": {…}}` form and has
  always been legal. Rejecting any array would have traded this bug for a far
  worse one — every formatted cell in every workbook. There is a test holding
  that line.

  **What you must do:** nothing, if your cells were already scalars, dates, or
  cell dicts. If any schema you build can nest a row one level too deep, you now
  get a `SchemaException` naming the cell where you previously got a workbook
  with `['x']` in it — read `get_errors()[0]["path"]` for the address. Released
  as a MINOR because that raise is observable to a caller whose input the
  validator previously accepted.

### Changed

- **The `fancy-conformance` fixture pin moves `0.22.1` → `0.34.0`,** twelve
  releases in one step. The suites were re-run first and not to reach green:
  `shared/decimal` reports 18 passed, 0 failed, 0 skipped — the identical count
  recorded at 0.22.0 and 0.22.1 — so the newer fixtures change no answer this
  port gives. The CI checkout `ref` moved with it; a test fails if the two
  disagree.

  Worth recording *how* this was found, because the mechanism will repeat:
  `dark-slide-py` moved to 0.34.0 on 2026-10-07 and its stale-pin issue was
  closed as fixed — for that repo. Nobody asked which **sibling ports** carried
  the same pin, so this one sat twelve versions behind with a red suite, and the
  red surfaced only because an unrelated release ran the full suite here. The
  pin only moves when a human re-runs the tables, and nothing prompts that when
  the fixture package ships.

[#1]: https://github.com/Particle-Academy/holy-sheet-py/issues/1
[holy-sheet#9]: https://github.com/Particle-Academy/holy-sheet/issues/9

## [0.4.0] - 2026-10-02

### Fixed

- **A column's own `width` is honoured. It was documented and silently ignored.**

  The shared `skills/holy-sheet.schema.json` -- the tool definition handed to an
  LLM, byte-identical across all three ports under a checksum test -- describes
  `columns[].width` as *"Column width in pixels. Same as columnWidths but
  per-column."* Only the sheet-level `columnWidths` map was ever read, so a width
  written the way the schema documents it emitted **no `<cols>` element at all**:
  no exception, no validation error, nothing from `validate_and_repair`. Silent at
  every layer.

  Reported against PHP as **holy-sheet#8** by the MOIC team, whose owner's
  complaint was "can't style spreadsheets at all". Measured, that was mostly this:
  the theme, header fill, banded rows and currency formats all landed, but a real
  account name truncated and a correctly-formatted currency rendered as `#####` --
  which is what a reader actually sees. Fixed in PHP 2.4.0, then Node 2.5.0; PHP is
  normative and this follows it exactly.

  **This port was the worst of the three.** `_normalize_column_widths` was called
  from **two** places -- the `cells` path and the `columns`/`rows` path -- so a fix
  applied to one would have left the other behaving differently. Both now call one
  `_normalize_widths(sheet)`, which is the shape that cannot drift, and a test
  covers the `cells` path specifically for that reason.

  **Precedence, identical to PHP and Node: the sheet-level `columnWidths` map is
  applied LAST and wins.** It is the mechanism that already worked, so a consumer
  who moved to it to route around this bug must not then find a leftover `width`
  overriding them. The two sources merge rather than replace, and the result is
  sorted ascending -- `<col>` children are expected in column order, and merging
  two sources means insertion order (which IS observable here) is no longer column
  order.

  A `width` now goes through the same `column_widths` rule the map already obeyed,
  so `"abc"` and `-5` are rejected rather than written, and **`validate()` reports
  it at `sheets[0].columns[0].width`**. That half matters as much as the fix: the
  original defect's cost was that an agent composes against the schema doc, gets
  no error, and never learns the field was dropped.

  **What you must do: nothing, and do not migrate off `columnWidths`.** Output
  bytes change for anyone who already set `width`, which is why this is a minor.


## [0.3.2] - 2026-09-14

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

## [0.3.1] - 2026-09-14

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

## [0.3.0] - 2026-09-14

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
