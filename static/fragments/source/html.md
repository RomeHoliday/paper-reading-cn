# Source: publisher or preprint HTML

The source is an HTML page (publisher site, preprint server, or similar).

## Extraction rules

- Extract the article body; strip navigation, cookie banners, related-article
  rails, reference-manager widgets, and advertisements.
- Keep section headings and paragraph order from the article markup; use
  heading text as stable orientation aids in the source map.
- Capture each figure image and its caption; reconstruct HTML tables from
  markup when clean rather than screenshotting. Still assign `F###` / `T###` /
  `C###` IDs.
- Preserve inline math (MathML/LaTeX/images), superscript citation markers, and
  links into the reference list when they help anchoring.
- If the page is JavaScript-rendered and content is missing, note what could
  not be retrieved; do not invent it.
- Respect copyright: keep chat short; full local reproduction only for clearly
  lawful open-access content or user-provided captures.

## Report implications

- Prefer element-stable anchors (heading id, figure id) in addition to page
  numbers when HTML has no PDF page. If a PDF of the same paper is later
  obtained, rebuild or dual-map anchors rather than guessing page numbers.
- When both HTML and PDF exist, prefer PDF page+block IDs for the final bundle
  and note the HTML origin in `translation_notes.md`.
