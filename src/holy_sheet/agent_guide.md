# Agent guide: professional workbooks with holy-sheet

<!--
  The agent guide for holy-sheet: what makes a workbook professional, and the
  schema idioms that produce it. Canonical copy: the PHP package's
  agent-guide/professional-workbooks.md; the Node and Python packages carry the same
  bytes (a test in each pins the hash). Distilled from rubric-judged builds in
  the Fancy document lab -- every rule here moved a measured score.
-->

## How to use this guide

It is written for an agent and works with any agent loop. Give it to the model
as part of its instructions (a system prompt, a skill, a rules file), together
with the package's JSON schema (`Agent::toolDefinition()` / `Agent.toolDefinition()` / `tool_definition()`). Then the procedure is:

1. Read the schema once and plan a workbook in full: structure, every figure,
   every derived number, computed and checked before anything is written.
2. Build the whole JSON in one go, following the recipes below.
3. Check it: `Agent::validate` (PHP), `Agent.validate` (Node) or `validate` (Python) returns errors with a hint each.
4. Write it (`Agent::write` / `Agent.toBytes` / `to_bytes`). If you can render the file and look at it, do,
   but repair only what you can see is wrong: one-shot "improvements" made
   blind regressed more documents than they fixed.

You are building a workbook a CFO would forward to the board UNCHANGED. Meeting the
brief is the floor. A table of the given numbers is a data dump; a professional
sheet tells the reader what it is, what the numbers mean, and how they were derived.

## The bar (every item is required)

1. **Title block.** Row 1: what the sheet is. Row 2: period, units/currency, source
   and as-of date. A reader who sees only this sheet knows exactly what it shows.
2. **Analysis, not just data.** Add the columns a reader needs to interpret the
   figures: share of total, variance vs average or target, rank. All as FORMULAS.
3. **Formulas for everything derived.** Totals, averages, shares, variances. Never a
   typed number where a formula belongs.
4. **Headers sit over their data.** Text columns left-aligned, numeric columns
   right-aligned, header cells aligned the same way as the data below them.
5. **Totals are distinct.** Bold, a top border, a light fill, and a clear label
   ("Total", "Average growth (simple)").
6. **Formats.** Currency with the right decimals (whole dollars for millions-scale
   board figures unless cents matter), percentages to 1 decimal, ISO or written dates.
   Negative values in a warning colour.
7. **Usable.** Frozen panes below the header row, widths that fit every value (never ####),
   a notes block stating method and definitions.

## Never ship

- A placeholder ("TBD", "[date]"). Name the period instead.
- Rounded figures that disagree with the source. Board figures given to the cent show
  cents (decimals 2) unless you round consistently AND say "rounded" in the title block.
- A sheet that prints across two pages. Keep the used width within ~7 inches
  (about 650 px of columnWidths in total) and keep every text cell short: one note per
  row, at most ~95 characters, so nothing spills past the table's right edge.

## Layout: use the `cells` model for a professional sheet

`columns` forces the header into row 1, which leaves no room for a title block. Build
the sheet from `cells` (keys are A1 addresses) instead:

- A1 title: `{value:"Q3 2026 Revenue by Region", format:{bold:true, fontSize:16, color:"#111827"}}`,
  merged across the table: `mergedRegions:[{start:"A1", end:"E1"}]`.
- A2 subtitle: `{value:"Quarter ended 30 Sep 2026 | USD | Source: management accounts", format:{italic:true, color:"#6B7280"}}`, merged A2:E2.
- Row 4 header: each cell `{value:"Revenue", format:{bold:true, color:"#FFFFFF", backgroundColor:"#1F2937", textAlign:"right"}}`
  (textAlign "left" for text columns).
- Data rows from row 5: `{value:1250000.5, format:{displayFormat:"currency", decimals:0}}`,
  `{value:0.184, format:{displayFormat:"percentage", decimals:1}}`.
- Derived cells: `{formula:"B5/B$8", format:{displayFormat:"percentage", decimals:1}}`
  (formula WITHOUT a leading "=").
- Total row: `{formula:"SUM(B5:B7)", format:{bold:true, displayFormat:"currency", decimals:0, borderTop:"#111827", backgroundColor:"#F3F4F6"}}`,
  and style the label and blank cells in that row the same way so the band is continuous.
- Notes last (below the chart, when there is one): `{value:"Notes", format:{bold:true}}`
  then one definition per row in `#4B5563` (dark enough to read; not pale grey italic).
- `frozenRows: 4` (title block + header stay visible), `columnWidths` in pixels:
  labels ~180, currency ~150, percentages ~110, dates ~120.
- Breathing room: `rowHeights` in pixels by row NUMBER -- title row 36, header row 30,
  data rows 24, total row 28: `{"1": 36, "4": 30, "5": 24, "6": 24, "7": 24, "8": 28}`.
  Packed default rows read as a data dump.
- Banding: give alternate data rows a very light fill (`backgroundColor:"#F9FAFB"`) and
  every data row a hairline bottom border (`borderBottom:"#E5E7EB"`), so the eye can
  follow a row across.
- Print: a one-page summary (table, key point, chart, notes) is
  `print:{orientation:"portrait", fitToPage:true, footer:{left:"Q3 2026 Revenue Review",
  right:"Page {page} of {pages}"}}` -- `fitToPage` scales the whole sheet onto ONE page,
  so the page break can never cut the chart in half. Only a long data table (more than
  ~40 rows) uses `fitToWidth:true` instead, and then its chart goes on its own sheet.
  Use landscape when the table is wider than ~650px. Every workbook carries a print setup.

## A chart, whenever the point is a comparison

A board reader sees a comparison faster in a chart than in a column of figures, and
reviewers mark a comparison sheet down when it has none. Add ONE native chart per
comparison (a real Excel chart over the cells, not a picture: it updates when the
figures change):

```
charts: [{
  type: "bar",
  title: "EMEA delivered 48% of Q3 revenue",
  categories: "A5:A7",
  series: [{name: "Revenue", values: "B5:B7", color: "#94A3B8",
            points: [{index: 2, color: "#1F3A5F"}]}],
  dataLabels: true,
  format: "$#,##0.00",
  position: {from: "A11", to: "F27"}
}]
```

- **Highlight the point the title is about.** Every bar one colour does not say
  which bar matters. Give the series a muted colour (`#94A3B8`) and the subject of
  the title -- the leader, the outlier, the line over budget -- the strong colour
  with `points: [{index, color}]` (index is 0-based within `values`). Flag
  over-budget or missed-target points in red (`#DC2626`).
- **Labels show what the table shows.** The chart's `format` uses the SAME decimals
  as the cells it plots: a table in cents and a label rounded to dollars read as two
  different numbers. For millions, label in millions and say so in the title
  ("$M") -- never a rounded label beside an exact cell.

- **Type.** `bar` (horizontal) for categories with names longer than a word, and for
  ranking; `column` for up to ~6 short categories; `line` for a trend over time
  periods; `pie` only for 2-5 parts of one whole, and never when a value is negative.
- **Ranges are the data rows only**, never the header or the total row: a total
  plotted beside its parts dwarfs them.
- **Title states the finding**, like the key point ("EMEA led Q3 revenue"), not
  the axis ("Revenue by region").
- **One series, one colour** (the sheet's header colour). Add a second series only
  when the comparison is between the two series; the legend appears by itself.
- **dataLabels: true** with `format` matching the cells (`"$#,##0"`, `"0.0%"`), so
  the reader does not have to read values off an axis.
- **Place it directly below the key point**, starting in column A, about 6 columns
  wide and 16 rows tall (`from: "A{row}"`, `to: "F{row+16}"`), and put the notes
  BELOW the chart. The chart sits next to the table it summarises; the notes
  that define terms come last. Beside the table it widens the printed page; on top
  of cells it hides them. With `fitToPage` the whole stack prints on one page.

## Recipes

- **Board summary sheet:** title block; a table with value, share of total, change and
  variance vs average; total row; a one-line "Key point" cell above the notes stating
  the finding in words (e.g. "EMEA delivered 48.5% of revenue and grew fastest").
- **Model with inputs:** an "Inputs" block (light yellow fill #FEF9C3 on input cells,
  labelled), calculations referencing the inputs by cell, outputs in a separate
  clearly labelled block. Use separate sheets when the model is larger than one screen.
- **Detail + summary:** a "Summary" sheet first (the one that opens), a "Data" sheet with
  the full table; the summary's figures are formulas into Data.

## Failures we have seen (do not repeat)

- No title, period, units or source: a CFO cannot forward it. -> Title block, always.
- Header labels left-aligned over right-aligned numbers. -> Align headers with data.
- Only the given numbers, nothing derived. -> Add share, variance, rank.
- An "average growth" that is an unweighted mean presented as if it were the total
  growth. -> Label it precisely, or compute the weighted figure.
- Footnote in tiny grey italics as the only context. -> Title block plus notes.
- A column repeating the same value on every row (a date already in the subtitle). -> Drop it.
- One label doing two jobs ("Total revenue / average growth"). -> One row per measure,
  each with its own label and styling.
- A long "key point" sentence in one cell running across the page. -> Merge it across
  the table width (mergedRegions) and keep it to one line of the table's width.

- A chart split across two printed pages. -> `fitToPage: true`.
- All bars one colour, so the flagged or leading item does not stand out. -> `points`.
- A threshold "input" cell whose flag colours did not follow it (fills are static).
  -> Add an input only when everything it drives is a FORMULA (a Status column
  `IF(D6>$B$3,"Over","Within")`); state the threshold in the notes otherwise.
- A total row with blank cells under columns that have a year-end or overall value.
  -> Fill every column of the total row with its overall figure, or a dash, never blank.
- A "vs average" column of percentages with no unit. -> Name percentage points in the
  header ("vs avg (pp)").
- Two versions of one measure (simple and weighted average) with no headline. -> Label
  one as the headline and say why in the notes.

## Before you write the workbook, check

Title and subtitle present and merged; every derived value a formula with a correct
range; header alignment matches data; totals band continuous; widths fit; frozen
panes set; negative figures coloured; a chart for the comparison over the data rows
only, with its subject highlighted and labels matching the cells; `fitToPage` on a
one-page summary; numbers re-added by hand once. Then write once.
