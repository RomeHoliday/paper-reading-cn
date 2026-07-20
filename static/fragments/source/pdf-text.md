# Source: selectable-text PDF

The PDF has an extractable text layer. Prefer `scripts/extract_pdf.py` for
block-level extraction with page IDs and bounding boxes.

## Extraction rules

- Extract the text layer directly; do not OCR text that is already selectable.
  Never invent OCR text in the extractor.
- Process the whole document for the source map, even though the report is
  selective. Selection happens in analysis, not during ingestion.
- Recover natural reading order in multi-column layouts; do not trust raw
  top-to-bottom stream order.
- `layout=double` or `mixed` caps page/block `confidence` at `medium` (modest
  mid-gutter heuristic; no strong gutter/mass oracle). Treat column order as
  best-effort and prefer span checks over layout confidence alone.
- Running chrome: short top/bottom band text (~6% of page height) that repeats
  on ≥ ceil(2/3) of extracted pages (and on at least 2 pages) is emitted as
  `type=header|footer` with `section_role=running_header|running_footer`.
  Single-page titles are not chrome. Do not use running chrome as default claim
  corpus unless explicitly cited.
- Rejoin hyphenated line breaks; preserve ligatures, superscripts, subscripts,
  math tokens, and citation markers.
- Caption-anchored figures/tables record `caption_bbox` and omit body `bbox`
  until an explicit crop; do not crop the caption box as figure/table ink.
- Xref image candidates are ordered by `(page, y0, x0, xref)` before `F###`
  assignment.
- Crop figures/tables with `scripts/crop_asset.py` using explicit `--page` and
  `--bbox`; do not paste a page-wide screenshot as a figure asset.
- If some pages lack a usable text layer, treat those pages under
  `scanned-pdf` rules and mark confidence in `source_map.json` and
  `translation_notes.md`.

## Report implications

- Prefer verbatim `original_en` from the text layer for load-bearing quotes.
- When layout splits a sentence across blocks, merge carefully for the quote
  but keep the primary `block_id` (and list secondary IDs if needed).
- Tables that exist only as images still need crops and caption bilingual
  fields; do not reconstruct numeric cells from OCR unless necessary—and mark
  confidence if you do.
