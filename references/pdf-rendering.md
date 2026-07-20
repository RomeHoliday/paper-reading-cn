# PDF rendering

Pipeline: `report.json` (+ optional `code_map.json`) → `render_report.py` →
`report.html` (+ `assets/report.css`) → headless Edge/Chrome print →
`report.pdf` → post-print QA (**PyMuPDF / fitz required** for
`delivery=complete`).

## CLI

```powershell
python scripts/render_report.py --bundle <output>/<paper-slug>
python scripts/render_report.py --bundle <output>/<paper-slug> --html-only
python scripts/render_report.py --bundle <output>/<paper-slug> --pdf
python scripts/render_report.py --bundle <output>/<paper-slug> --browser <path> --timeout-sec 120
python scripts/render_report.py --selftest
```

- `--html-only`: write HTML only; `qa_report.render.status` / `delivery` =
  `html_only` (not generic `ok`). Stale `report.pdf` / `.partial` are removed.
- `--pdf`: request PDF print (default when `--html-only` is absent). Mutually
  exclusive with `--html-only`.
- `--browser` / `PAPER_READING_BROWSER`: if set, that path must exist and be
  usable — **no fallback** to system Edge/Chrome (exit 4 on miss).
- `--skip-pdf-qa`: never sets `delivery=complete`; PDF is removed (exit 6).
- Formal runs use local Chromium CLI only — never Cursor browser MCP.

## HTML / CSS safety

- Self-contained relative links into `assets/`; no remote CSS/JS/fonts/`data:` /
  `file:` / `javascript:` references in emitted HTML.
- Stylesheet is fail-closed **before** HTML write: reject `</style`, any HTML
  brackets (`<>`), `@import`, `url(`, `data:`, `expression()`, remote/`file:`/
  `javascript:` references. Unsafe bundle `assets/report.css` is not applied
  (no override of skill policy); exit 3, no new `report.html`.
- `report.html` is written atomically (`report.html.partial` → replace).
- All dynamic paper/repo text passes through HTML escaping (`html.escape`).
- Display `meta.execution_mode` when present; retain legacy `single_agent` for
  compatibility.
- When `code_map.json` is present, render an implementation-correspondence
  section: version alignment / commit / dirty warning, each mapping’s
  `status` + `match_basis`, `paper_statement_en` + `paper_explanation_zh` +
  paper anchors, and each `path:line_start–line_end` with `symbol`,
  `role_en` / `role_zh`, and a short escaped `code_excerpt`.
- Repository evidence must never be portrayed as proving paper performance.
- Figure cards use **`alt_text` only** for `img alt` (never caption fallback).
  If `alt_text` is absent, renderer uses the safe generic `figure asset` and
  records a warning; nonempty `alt_text` remains a verifier concern.
- Chinese academic stylesheet (`assets/report.css`): CJK stack, A4 `@page`,
  claim/figure/code cards, print rules — no `url(` / `@import`.
- Embed selectable CJK probe `中文精读探针` for post-print text QA.

## Print settings

- Prefer headless Microsoft Edge, then Chrome/Chromium when neither
  `--browser` nor `PAPER_READING_BROWSER` is set.
- Fresh temporary `--user-data-dir` per print; cleaned after the run.
- Print to `report.pdf.partial` then atomic replace to `report.pdf`.
- At render start, quarantine/delete stale `report.pdf` and `.partial`.
- Every non-`complete` terminal path (html-only, browser missing, print fail,
  QA fail, skip QA, fitz missing) leaves **no** `report.pdf`.
- Paper size A4 via CSS `@page`; no remote refs.
- If no supported browser is installed (and no explicit override): keep HTML,
  exit 4, `delivery=html_only`, `status=skip`, `code=browser_not_found`.
- Selftest may `SKIP` print when no browser; HTML/escape/asset/CSS safety
  tests still must PASS.

## Post-print PDF QA (hard dependency on PyMuPDF)

`delivery=complete` requires importable PyMuPDF (`fitz`) and all of:

1. `page_count >= 1`
2. MediaBox ≈ A4 (595×842 pt ± 2; portrait or landscape)
3. Extractable selectable text contains `meta.title_zh` and the CJK probe
   (or title CJK as fallback probe)

Store `page_count`, `text_chars`, and `media_box` under
`qa_report.render.validation`.

**Missing `fitz`:** hard-fail / `delivery=failed` (status `unverified`), PDF
removed — magic+size alone is **not** enough for complete. Install:
`python -m pip install pymupdf`.

**`--skip-pdf-qa`:** same — never `delivery=complete`; PDF removed.

## Layout discipline

- One job per section; match `report.schema.json` section order.
- Keep figure cards near first discussion; avoid dumping all assets at the end.
- Depth budgets (approx.): brief 6–10 pages, standard 12–25, deep 25–45 —
  evidence completeness beats padding.
- Do not dispatch subagents for rendering; the single reading agent runs the
  scripts locally.
