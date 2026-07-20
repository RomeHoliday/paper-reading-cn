---
name: paper-reading-cn
description: >
  Produces a source-grounded Chinese PDF reading report for one academic paper
  from PDF, DOI, arXiv, or publisher HTML, with optional code-repository
  correspondence. Use when the user asks for 中文精读报告, 论文重点解读PDF, selective
  paper reading with English–Chinese anchors, paper↔code mapping, or a
  Chinese analytical PDF report rather than a full-paper translation.
  Triggers include 读论文出报告, 生成论文阅读PDF, 对照代码读论文, PDF/DOI/arXiv
  reading report, and similar requests. Do not use for full bilingual
  paragraph-by-paragraph translation (use nature-reader) unless the user
  explicitly wants a selective analytical report instead.
version: 1.0.0
---

# Paper Reading CN — Router

This skill builds a **selective, source-grounded Chinese PDF reading report**
for one paper. It is not a full-paper translation skill. Load-bearing claims,
captions, methods, limitations, and optional code correspondences each keep
English source text, Chinese explanation, and exact paper anchors.

Formal reading is single-agent by default. Do not dispatch subagents unless the
user explicitly requests them for this reading job.

Do not apply reading logic from memory. Always load fragments from disk as
described below.

## Routing protocol

### 1. Load the manifest and the core layer

Read [manifest.yaml](manifest.yaml). It declares four axes (`source_format`,
`paper_type`, `depth`, `code_repo`), allowed values, and fragment paths.

Also read every file listed under `always_load`:

- [static/core/execution-mode.md](static/core/execution-mode.md)
- [static/core/principles.md](static/core/principles.md)
- [static/core/workflow.md](static/core/workflow.md)
- [static/core/output-contract.md](static/core/output-contract.md)
- [static/core/chinese-style.md](static/core/chinese-style.md)

### 2. Detect the four axes

Decide each axis using the manifest `detect:` hints and the user input:

| Axis | Values | Default |
|------|--------|---------|
| `source_format` | `pdf-text`, `scanned-pdf`, `doi-arxiv`, `html` | `pdf-text` |
| `paper_type` | `systems`, `empirical`, `theory`, `survey`, `other` | `other` |
| `depth` | `brief`, `standard`, `deep` | `standard` |
| `code_repo` | `absent`, `present` | `absent` |

State `mode=single-agent` and the four detected values in one short line before
processing so the user can correct them cheaply. A DOI/arXiv input may require loading `doi-arxiv`
first, then the resolved artifact fragment (`pdf-text`, `scanned-pdf`, or
`html`).

### 3. Load only the matching fragments

Read the files mapped for the detected values. Do **not** read every fragment
under `static/fragments/`. Load only what step 2 selected (plus resolution
chaining for `doi-arxiv`).

### 4. Run the single-agent reading workflow

Apply loaded material in this order:

1. Core principles — selective report, bilingual load-bearing items, evidence rules.
2. Source-format fragment — extraction and OCR/layout notes.
3. Paper-type fragment — genre checklist (architecture, stats, proofs, taxonomy).
4. Depth fragment — page budget and selection intensity.
5. Repository fragment — skip code mapping (`absent`) or require statused mappings (`present`).
6. Workflow + output contract — assemble, validate, render, QA.

Use scripts under `scripts/` when available (`extract_pdf.py`, `crop_asset.py`,
`repo_snapshot.py`, `verify_bundle.py`, `render_report.py`). Prefer deterministic
extraction over hand-copied text. If deterministic extraction is missing or
fails, create only a clearly blocked draft with page renders and
`translation_notes.md`; do not invent source text, numbers, or anchors and do
not deliver `report.pdf` as verified.

### 5. Open references only when needed

Files under `references/` are deep references, not defaults. Open on demand per
`references.on_demand` in the manifest (one level deep from this router):

- output field shapes → `references/output-spec.md`
- evidence / numeric provenance → `references/grounding-rules.md`
- figure reading → `references/figure-reading.md`
- code alignment → `references/code-paper-alignment.md`
- PDF rendering → `references/pdf-rendering.md`
- script CLI → `references/script-usage.md`

Schemas live under `schemas/` and are consulted when assembling JSON artifacts.

## Product boundaries

**Do produce**

- a selective analytical Chinese report PDF with bilingual load-bearing items
- stable `source_map.json` / `claim_ledger.json` anchors
- optional `code_map.json` with `exact` / `partial` / `mismatch` / `unverified`
- blocked delivery when required bilingual fields, source IDs, assets, or code
  paths are missing

**Do not produce**

- a full-paper paragraph-by-paragraph bilingual translation (that is
  nature-reader's contract)
- formulaic announcement labels such as “本文的关键洞见是” or “Takeaway:”
- invented numbers, unanchored quotes, or name-only code correspondence
- default multi-agent / Grok dispatch for the reading job

## Copyright caution

For copyrighted publisher PDFs, keep chat responses short and point to the
local output bundle. Reproduce substantial source text in local artifacts only
for user-provided files or clearly lawful open-access content.
