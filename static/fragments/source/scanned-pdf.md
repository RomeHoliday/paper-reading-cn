# Source: scanned PDF (OCR required)

The PDF is image-only or has an unreliable text layer. OCR is required before
analytical reading.

## Extraction rules

- First render and classify every page. Use a local OCR engine only when it is
  installed and its output can be recorded; do not assume a usable text layer.
- If local OCR is unavailable or fails, produce a render-only blocked draft:
  retain 1-based page anchors and images, set `ocr_status` to
  `missing_tool` or `failed`, leave source text unavailable, and do not build
  load-bearing claims from model vision or memory.
- Record per-block `confidence` in `source_map.json`. Mark low-confidence
  blocks in `translation_notes.md` and on the claim/caption that uses them.
- Preserve OCR wording when confident; flag garbled spans—do not silently
  “correct” into plausible science.
- Treat numerals, units, symbols, dataset names, and formulas as high-risk OCR
  sites. Cross-check against nearby context and figure crops; leave uncertainty
  visible.
- Crop figures/tables with explicit page + bbox. A tight correct crop beats a
  wide noisy one.
- Note skewed, rotated, or truncated pages; analyze only what is legible.

## Report implications

- A load-bearing claim requires high-confidence OCR. Medium-confidence OCR may
  appear only in an explicitly marked draft; low/unavailable OCR cannot support
  a report claim or numeric value.
- Do not invent missing digits to complete a table.
- If OCR coverage is too poor for a required section, keep the section with an
  explicit gap note rather than fabricating content. The PDF gate should
  reflect unresolved OCR blockers when they affect load-bearing items.
