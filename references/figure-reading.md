# Figure and table reading

Rules for selecting and explaining figures/tables in the report. Not a full
visual reconstruction of the PDF. Production remains single-agent; **automatic
cropping is out of policy** for formal readings.

## Selection

- Include only load-bearing figures/tables that support or challenge claims.
- Prefer the first substantive discussion site for placement (`placed_after` /
  `placement.placed_near_claim_ids`).
- Skip decorative logos, repeated teaser plots, and appendix plots unless the
  claim depends on them.

## Required card fields (`report.selected_figures_tables[]`)

Every selected figure/table is an evidence card. Present entries use the full
contract regardless of depth (`brief` may include fewer cards, not weaker ones).

| Field | Rule |
|-------|------|
| `asset_id` | `F###` or `T###` matching `source_map` |
| `kind` | `figure` or `table` |
| `page` | 1-based source page |
| `caption_en` | Original caption text (from `C###`); required, nonempty; must be a bounded span of the cited caption block |
| `caption_zh` | Faithful Chinese caption; required, nonempty |
| `zh_mode` | Must be `caption_translation` |
| `axes_encoding_zh` | Axes, legends, color/shape encodings (figures); optional for pure tables |
| `reading_zh` | What the plot/table shows (not a slogan Takeaway) |
| `evidence_supported_zh` | Claims the figure+caption+cited body actually support |
| `evidence_not_supported_zh` | Overclaims / inferences the paper does **not** support |
| `sources` | Page + caption `C###` (+ `figure_id`/`table_id`); when `figure_id` is set, `block_id` must equal that figure’s `caption_id` |
| `asset_id` / `kind` | Must exist in `source_map` figures/tables; `kind=figure` iff `F###`, `kind=table` iff `T###` |
| `asset_path` | Relative path under bundle `assets/` (e.g. `assets/F001.png`) |
| `crop` | Explicit crop provenance (see below); required when an asset is present |
| `crop_approximate` | Legacy boolean; prefer `crop.status` (`approximate` when uncertain) |
| `alt_text` | Nonempty English description of visible encodings (accessibility); verified, not caption fallback |

Supported vs unsupported boundaries are mandatory: chart reading must not silently
become paper claims. Numbers read off ink without a printed value go to the claim
ledger as `chart_estimate`, never silent `quoted`.

## Crop provenance (`crop` / `*.crop.json`)

Production crops are **explicit bbox only** via `scripts/crop_asset.py`
(`--bundle-root`, `--page`, `--bbox`, `--out`). No vision/auto-detect bbox may be
admitted without the single agent rewriting and re-validating coordinates.

```json
{
  "method": "explicit_bbox",
  "status": "verified",
  "page": 4,
  "bbox_pdf_points": [72.0, 180.0, 540.0, 520.0],
  "coord_origin": "top_left",
  "zoom": 2.0,
  "effective_dpi": 144,
  "source_sha256": "<hex>",
  "metadata_path": "assets/F001.crop.json",
  "note": ""
}
```

| Rule | Gate |
|------|------|
| `method` | Only `explicit_bbox`. `auto` / `full_page` / `screenshot_guess` are reject unless `status=approximate` with explanatory `note`. |
| `status` | `verified` (tight, labels intact) or `approximate` (uncertain box). |
| `zoom` | ≥ 2.0; typical `effective_dpi` = 72 × zoom (≥ 144 at zoom 2). |
| Area | Width/height ≥ 8 pt; area ≥ 0.5% of page; area > 85% of page requires `--approximate` and nonempty `--note`. |
| Paths | PNG and sidecar must resolve under `<bundle-root>/assets`; absolute / `..` / symlink escapes rejected. |
| Identity | Report `crop` and sidecar both require `source_sha256`, which must equal `sha256(bundle/source.pdf)`. 1-based `page`, `bbox_pdf_points`, `zoom`, `effective_dpi`, page size, relative `out_png`, `validated: true`. |

### CLI

```powershell
python scripts/crop_asset.py `
  --bundle-root <bundle> `
  --pdf <source.pdf> `
  --page 3 `
  --bbox 72,400,540,720 `
  --out assets/F001.png `
  --zoom 2 `
  --figure-id F001

# Full-page / uncertain crops only when tighter bbox is impossible:
python scripts/crop_asset.py `
  --bundle-root <bundle> `
  --pdf <source.pdf> `
  --page 3 `
  --bbox 0,0,612,792 `
  --out assets/F002.png `
  --approximate `
  --note "scanned full-bleed figure; no tighter bbox"
```

Do **not**:

- Detect figure regions automatically and crop all.
- Use full page-render PNGs as figure assets without a validated sub-bbox.
- Crop from a different PDF revision than the bundle `source.pdf`.
- Invent bbox coordinates without inspecting a page render or `source_map` candidates.
- Crop `caption_bbox` as if it were the figure/table body. Caption-anchored
  `source_map` records omit body `bbox` on purpose; a caption-only box may be
  used only with `status=approximate` and a nonempty explanatory `note`.

## Source map figure/table fields

`source_map.figures[]` / `tables[]` keep `id`, `page`, optional `caption_id`,
optional body `bbox`, optional `caption_bbox`, `image_path`, `alt_text`,
`placed_after`.

Caption-anchored candidates (`source=caption_anchor` from `extract_pdf.py`):

- Emit `caption_bbox` for the caption text region.
- Omit body `bbox` until an explicit figure/table-body crop is chosen.
- Keep `crop_status=pending` and a note that the caption box is not ink.

Xref/heuristic candidates may carry a geometric body `bbox`. When a production
crop exists, also record (where representable):

- `crop_metadata_path` — e.g. `assets/F001.crop.json`
- `zoom`, `effective_dpi`, `source_sha256`
- `image_path` under `assets/`

Status namespaces (do not conflate):

- `source_map.*.crop_status`: extraction pipeline — `pending` | `approximate` |
  `ready` (owned with extract_pdf).
- `report.selected_figures_tables[].crop.status` and `*.crop.json` `status`:
  crop QA — `verified` | `approximate` (owned with crop_asset).

## Reading discipline

- Cite caption and body text when both matter.
- Multi-panel figures: identify panels in prose or structured panel ids; claims
  that depend on one panel must name it.
- Comparison conditions (systems, datasets, hardware, metrics) belong in the
  reading when ≥2 series appear.
- Tables: cite the row/cell block when available; otherwise caption + table id.
- Alt text is accessibility aid only, not independent evidence.

## Placement in HTML/PDF

Keep the card near the first substantive Chinese discussion. Later mentions
link back to the same `asset_id` (e.g. `#F001`). Do not duplicate asset bytes.
See `pdf-rendering.md`.
