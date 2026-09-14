# AGENTS.md — holy-sheet (Python)

This file describes **this repo's code**: its API, its invariants, and the traps
that will cost you a day. Process rules — publishing, versioning, backports,
support lifecycle — live in the envelope's `AGENTS.md` and are deliberately not
repeated here.

## What this is

A zero-dependency `.xlsx` writer, reader and formula linter. The **third**
implementation of one contract:

| | |
|---|---|
| `particle-academy/holy-sheet` (PHP) | the reference — shipped first, most complete |
| `@particle-academy/holy-sheet` (Node) | the container/API model — bytes-in/bytes-out, no host |
| `holy-sheet` (this) | Python |

**PHP is normative for SEMANTICS. Node is normative for STRUCTURE.** That split
is why the module layout mirrors the Node port's directories while every
behavioural decision follows PHP's.

### Python never casts a deciding vote

Where PHP and Node already disagree, **this port follows PHP**. Not because PHP
is better, but because the disagreements are documented in
`.ai/plans/polyglot/parity/documents.md` with a ruling attached, and a third
engine picking its own answer turns a two-way drift into a permanent three-way
split. Following PHP keeps the tally 2–1 in the direction already decided.

Where PHP has a feature Node lacks, this implements PHP's. Two of those:

- **`=`-promotion** — a bare string cell beginning with `=` is a formula.
- **Bare scalar `cells` entries** — `{"A1": 42}` as well as `{"A1": {"value": 42}}`.

Node is a minor behind on both.

## The API shape

Module-level snake_case functions, mirroring PHP's static `Agent` and TS's
`Agent` object: `validate`, `validate_and_repair`, `to_bytes`, `write`, `read`,
`describe`, `lint`, `from_array`, `from_csv`, `tool_definition`, `version`,
`diff`, `reduce`, `op_schema`, `equivalent`.
Lower-level classes keep their peer names (`Validator`, `Normalizer`,
`XlsxWriter`, `XlsxReader`, `CellAddress`, …) so a reader moving between the
three repos recognises them.

**The input schema is a plain `dict` and must stay one.** The declarative
one-shot schema is the product: an agent emits the whole workbook in a single
JSON object, and the *Validator* is the gate. A dataclass would move the gate
into a constructor and reject exactly the loose input `validate_and_repair`
exists to fix. `schema/types.py` holds `TypedDict`s (`total=False`) for editor
support only — never construct from them.

The internal `Workbook` / `Sheet` / `Cell` / `CellFormat` objects the Normalizer
produces ARE real classes. They are internal, like their peers.

Three shape differences from a peer, each deliberate and each pinned by a test:

- `write()` is **synchronous** (PHP's is; Node's is async only for browsers).
- `from_csv()` accepts a **path or content** (PHP's superset; Node takes content).
- `read()` takes **bytes**, `describe()` takes a **path** (Node's split).

## Byte parity with PHP is the definition of done

`tests/test_parity_php.py` runs the PHP writer as a subprocess and asserts this
port emits **byte-identical OOXML parts** for every fixture. `KNOWN_DIVERGENT_PARTS`
is empty and the ledger ratchets both ways — a new divergence fails, and a stale
entry fails too.

**Never byte-compare the container.** PHP writes through `ZipArchive` (DEFLATE,
real mtimes); this writes a fixed 1980-01-01 DOS date. Those files can never
match, and a reader sees parts, never the compression.

### Therefore: the XML is a string builder, and must stay one

Attribute order (`<c r="A1" s="3" t="inlineStr">`, in that order), self-closing
style (`<c r="A1"/>` for a null cell, but `<xf …></xf>` always as a pair), the
absence of inter-element whitespace, and `&apos;` rather than `&#39;` are all
part of the contract. A DOM serialiser owns every one of those decisions and
would fail the first fixture.

`xml.etree` is used for **reading** and never for writing. That is the correct
division: nothing is serialised on the read side.

`helpers/xml.py` has two escapers and the difference is intentional:
`xml_escape` strips XML-illegal control characters first; `xml_escape_raw` does
not. The reference engine's `StylesRegistry` calls bare `htmlspecialchars` where
its writer calls its own `escape()`, so number-format codes and font names skip
the strip. Mirrored, not tidied — tidying it would change bytes both shipped
engines currently agree on.

## Numbers: the section to read before touching the writer

This is where every cross-runtime bug in this family has lived. Five real
PHP↔JS divergences shipped in the Node port, all of them here.

Everything numeric goes through `helpers/php.py`. **Do not reach for a builtin.**

### `bool` is a subclass of `int`

`isinstance(True, int)` is `True` and `True == 1`. Any branch that tests `int`
before `bool` writes a boolean into a numeric cell — `<v>1</v>` with no `t="b"`,
a sheet showing 1 where the author wrote TRUE, and no error anywhere. **Check
`bool` first, every time you branch on type.** The places that matter:
`Cell.excel_type`, `XlsxWriter.cell_xml`, `php.type_of`, `php.php_to_string`,
`Inference._all_numeric`, `Inference._all_integer`, the linter's coercions.

### `round()` is banker's rounding

`round(0.5) == 0` and `round(2.5) == 2`. PHP's is half away from zero. Use
`php_round`; the builtin is never called anywhere in this package. It matters in
the reader's column-width inverse and in the linter's `ROUND()`.

At a non-zero precision `php_round` quantises the **shortest repr**, so
`php_round(1.2345, 3)` is 1.235 — PHP's answer, and the one a caller writing
"1.2345" means. (The exact double is 1.23449999999999993.)

### `f"{v:.14f}"` rounds half to EVEN

That is the `<v>` serialisation of every float cell. PHP's `number_format` rounds
half **away from zero**, and the two disagree on any double whose decimal
expansion terminates in a 5 at the 15th place — `j / 2**15` with odd `j`. Use
`php_number_format`. Two such values live in the `numericHazards` parity fixture
so a half-even formatter fails the build rather than shipping.

**Known, deliberate deviation:** PHP reaches its answer through a floating-point
*pre-rounding* step whose result changed between PHP 8.3 and 8.4 (8.4 dropped the
`>= 1e15` bail-out, so `number_format(32.666666666666664, 14)` now disagrees with
`sprintf('%.14F', …)` of the same double). Reproducing that would make this
package's output a function of whichever PHP the maintainer built against, which
is parity with nothing. `php_number_format` implements the **rule** — round the
exact binary expansion, half away from zero, suppress the sign on a zero result
— and the two agree everywhere except the 15th significant digit of values below
10. The oracle proves it part-for-part on every fixture. If you change this,
re-run the parity suite; it is the only thing that can tell you.

### Only ever trim a fraction, never an exponent

`format_float` = `number_format(v, 14)` then strip trailing zeros then a trailing
dot. The Node port trimmed unconditionally, and because `toFixed` goes
exponential at 1e21 the strip chewed the **exponent**: 1e300 was written to the
sheet as `1e3`. Python's `format` never goes exponential for `f`, so the hazard
is absent — the rule is written down because the next port will be offered a
formatter that does.

### Numeric-string coercion is PHP's, not Python's

`float()` accepts `"inf"`, `"nan"`, `"1_000"` and `"0x1A"`-adjacent forms that
PHP's `is_numeric` rejects. Always guard with `is_numeric_string` before
`numeric_string_to_number`. And the coercion returns `int` for an
integer-shaped string inside zend_long's range and `float` otherwise — it does
**not** clamp, which is what `(int)` did and how `"1e21"` became
9223372036854775807.

### Python ints are unbounded; PHP's are 64-bit

An `int` outside `[-2**63, 2**63)` is written through the float formatter,
because that is what PHP's `json_decode` would have produced for the same
literal. Losing the extra precision is the point: it is what makes the document
the same on both backends.

### Non-finite values

NaN and ±Infinity have no `<v>` representation — "NaN" in a cell is a corrupt
sheet, not a big number. Both engines write `0`.

## Other invariants

**Cells within a row are ordered LEXICOGRAPHICALLY on the column letter**, so a
sheet wider than 26 columns emits `A, AA, AB, AC, AD, B, C, …`. That matches
neither Excel's canonical order nor intuition, and **both shipped engines do it**
(`ksort` on a string key in PHP, `[...keys].sort()` in Node). It is replicated on
purpose and pinned by the `wide` fixture. Fixing it changes the bytes of every
wide sheet in every engine simultaneously, so it belongs in a coordinated release
train — not in a port. Do not "fix" it here.

**`cells` is document-ordered.** Python dicts are insertion-ordered, so the
contract the Node port needed a deliberate structure for is free here — but it is
still a contract. `Sheet.comments()` and the comment/VML part numbering read it.

**The zip is deterministic.** Fixed part order, fixed `date_time=(1980, 1, 1, 0,
0, 0)`. Without it the container is a clock, the same input produces different
bytes every run, and a golden fixture is impossible.

**Sheet XML is rendered before `styles.xml` is serialised.** Every format has to
register first. Swapping those two lines in `XlsxWriter.to_bytes` produces a
styles part missing every style the sheets reference.

**Reading rejects a DOCTYPE before parsing.** An xlsx never legitimately carries
one, and an internal DTD subset is the entry point for entity expansion on
untrusted input. Refusing the construct is cheaper than depending on what a
parser does with it today. See `reader/xml.py`.

**Dates parse in UTC, from an explicit grammar.** Not `datetime.fromisoformat`,
whose accepted set changed in Python 3.11 — that would make a cell's serial
depend on the interpreter. Not PHP's `DateTimeImmutable` grammar either, which
accepts `"next monday"` and is unportable. Unparseable input yields serial 0.0,
matching the reference's failure path. The epoch anchor is 1899-12-30, which
cancels Excel's 1900-leap-year bug for every date from 1900-03-01.

**`tool_definition()` raises when the schema file is missing.** PHP returns an
empty array; that is the worse failure, because an empty tool definition produces
no error anywhere — just a model told nothing about the tool it is holding. The
file is byte-identical across all three repos and each pins its SHA-256 over
CRLF-normalised content (`tests/test_schema_sync.py`). Edit one copy, edit all
three, update all three constants.

**`read()` sniffs the bytes and also reads `.ods`**, into the same schema, through
`reader/ods_reader.py` (a port of PHP's `OdsReader`, which documents what maps).
Three traps specific to it, each pinned by `tests/test_ods_reader.py`:

- **An `Element` with no children is falsy.** `a.get(k) or b.get(k)` skips an
  empty `<style:style/>` and resolves the wrong style. Compare with `is None`.
- **Match OpenDocument attributes by URI** (`ods/ns.py`), never with the
  local-name helpers in `reader/xml.py`: LibreOffice puts `office:value-type`
  and `calcext:value-type` on the same cell.
- **PHP string semantics, not Python's**: `php_trim` (ASCII only), ASCII-only regex
  classes (`[0-9]`, and `re.ASCII` wherever `\b` appears), `php_round` for
  fractional seconds.

The ODS fixtures live in the PHP repo and are found beside its sources
(`_oracle.ods_fixtures_dir`); missing fixtures are an error, not a skip.

**The ops (`ops/`) run on PHP arrays, not dicts.** `diff` must return PHP's op
list, in PHP's order, for the same input (`tests/test_sheet_ops_parity_php.py`
compares them through `scripts/php_ops.php`), and PHP's answer depends on array
semantics a dict does not have. `ops/_php_array.py` holds them, and nothing in
`ops/` should compare values any other way:

- **`[]` and `{}` are one value, and so are `{"0": x}` and `[x]`.** `php_pairs`
  normalises keys as PHP stores them; `canon` encodes a 0..n-1 map as a list.
  That is why `columnWidths` from JSON (`{"0": 120}`) and from `describe()`
  (`{0: 120.0}`) compare equal, and why a column shift returns int keys.
- **Equality is `canon()` text or `identical()`, never `==`.** `True == 1 == 1.0`
  in Python; PHP's `===` and its canonical JSON tell all three apart.
- **`(int)` is `php_int_cast`, not `int()`**: `"12abc"` is 12, `"1e3"` is 1000,
  an out-of-range float wraps modulo 2**64 and an out-of-range string saturates.
- **Addresses parse with `parse_address`, not `CellAddress.parse`.** The latter
  trims and upper-cases with Unicode rules and matches Unicode digits, so it
  accepts `"\ufb001"` and `"A\u0661"`, which PHP rejects. The writer still uses
  it; that divergence is known and not yet fixed.
- **PHP's quirks are mirrored, not fixed**: a `type` of `true` is `remove_sheet`
  (`switch` compares loosely), a padded address is stored untrimmed, and any two
  values `json_encode` rejects (NaN, INF, invalid UTF-8) compare as the same.
  Changing one changes the ops a history stores, in one runtime only.
- **Internally the reducer shares structure** (`apply_shared`) and copies each
  level it changes; the public entry points deep-copy once. Never mutate a value
  inside `ops/` in place.

**Linter hint strings are byte-compared against PHP.** They contain em dashes
(`Division by zero — the divisor evaluated to 0.`). A hyphen there is a parity
failure.

## Deliberately out of scope

Three things the PHP package has that neither port implements, and that a
contributor will otherwise assume were forgotten:

- `Laravel/` — the service provider, facade, artisan command and query adapter.
- `Toolkit/` — the prompt/tool/schema-store layer.
- `Schema/Dumper` + `DumpOptions` — `dumpJson()`, the cell-level content dump.

The Node port omits all three too. `Dumper` is the one with a real case for
being ported; it is the read-tool counterpart to `describe()` and it is what
lets an agent make targeted cell edits.

## Testing

`python -m pytest`. **Write the test first**; a bug fix lands with a test that
fails against the old code, and you verify that it does.

The parity oracle needs `php` on `PATH` (or `PHP_BIN` pointing at a real
interpreter — on Windows `php` is usually a `.bat` shim, which a bare `exec`
cannot spawn) and the PHP sources beside this checkout (or `HOLY_SHEET_PHP_SRC`).
Locally a missing toolchain skips those tests and says so; **under `CI` it
raises**, because a suite that quietly stops comparing anything reads exactly
like one that compares everything.

Do not weaken `tests/_oracle.py`, `tests/test_parity_php.py` or
`tests/test_determinism.py`. They are the reason this is a port rather than a
rewrite.
