# Paper type: survey

Primary contribution is a survey, taxonomy, systematic review, or perspective
that organizes a literature rather than proposing one new system/theory.

Formal reading is single-agent by default. Produce a Chinese **literature map
of this one survey PDF**—not a meta-analysis re-run, not independent
verification of cited experiments, not a digest of every cited abstract.

## Required map sections

1. **Scope / search** — databases, years, venues, keywords, inclusion/
   exclusion; or explicit `coverage_status: unstated` / `partial` (never invent
   PRISMA-like criteria).
2. **Taxonomy** — axes, category labels (EN+ZH), membership examples anchored
   to survey text/table; prefer one cropped taxonomy figure/table. Re-bucketing
   is `reader_judgment` (读者重分类) only.
3. **Coverage window** — explicit years / “as of …” / last-included venue year;
   trend and “SOTA” claims must keep 截至… when the survey dates itself.
4. **Stance split** — consensus vs author opinion vs cited-work report vs
   reader judgment (table below).
5. **Comparative claims** — table cells with survey anchors; every cited-system
   metric marked second-hand.
6. **Omissions & biases** — author-admitted gaps + clearly labeled reader gaps
   (venue / language / recency / selection). Do not invent missing papers.
7. **Use paths** — ordered citation keys / section pointers from the survey
   (≤1 sentence survey gloss each)—pointers, not verified summaries.

## Honesty banner (required)

Near the coverage map / comparison tables, include non-empty Chinese banner:

> 本报告只精读这一篇综述。文中涉及的被引工作的数据与结论均为综述转述，
> 未在本任务中独立核对原文。

Record as audit note `honesty.survey_second_hand_banner_zh` (soft field for
QA; do not omit the visible banner).

## Stance tags

| Stance | Meaning | Surface cue |
|--------|---------|-------------|
| `survey_consensus_claim` | Survey asserts field-wide agreement | EN span with community/widely/standard…; ZH: 综述所称的共识 |
| `author_position` | Authors’ recommendation / critique | ZH: 作者主张/倾向 |
| `cited_work_report` | Survey reports what paper X claims | ZH: 综述对〔引用〕的转述（未独立核验）; ledger `secondary_citation: true` |
| `survey_own_result` | Rare: survey’s own experiment | `secondary_citation: false` |
| `reader_judgment` | Our synthesis, staleness, unfairness | ZH: 阅读判断 |

Forbidden: translating author advocacy into “学界普遍认为” / “领域共识”
without consensus cues in the English span.

## P0 — do not ship

| ID | Failure |
|----|---------|
| U-P0-1 | Second-hand upgrade: cited-system numbers treated as primary / re-measured; missing `secondary_citation: true` on cited-result claims |
| U-P0-2 | Consensus laundering: “学界普遍认为” without survey consensus EN (or unlabeled `reader_judgment`) |
| U-P0-3 | Cited-paper expansion: recalling / fetching / summarizing cited works as if independently read (unless user supplied those PDFs in *this* job) |
| U-P0-4 | Missing honesty banner on survey bundle |

**Secondary-citation rules (align claim ledger):**

- Facts from cited works: `secondary_citation: true`, anchors on **survey**
  page/block/table cell only; optional `cited_work` / `cited_marker` as printed.
- Cap confidence at `medium` for second-hand result claims unless
  `survey_own_result`.
- Do not invent primary-paper page numbers or “经核验，原文确实报告…”.
- Do not `derived`-combine two cited papers unless the **survey** prints the
  comparison (still second-hand).

## P1 — scope, taxonomy, window, omissions

| ID | Check |
|----|-------|
| U-P1-1 | `survey_scope` stated or explicitly `unstated`/`partial` with focusing sentence |
| U-P1-2 | Taxonomy axes + categories follow survey tables/figures; silent recluster → fail |
| U-P1-3 | `coverage_window` captured; trend claims inherit it; reading-time staleness is dated `reader_judgment` only |
| U-P1-4 | Coverage boundaries: in/out populations, exclusions, survey’s own unfair-comparison warnings |
| U-P1-5 | `omissions_and_biases` non-empty at standard/deep (≥1 item); brief may skip section but still needs honesty banner |
| U-P1-6 | Selected comparison-table metric cells: table_id + page (+ row/col note) and `secondary_citation: true` when attributing a cited system |

## Depth (map quality, not cited digests)

| Depth | Extras |
|-------|--------|
| brief | Scope + taxonomy crop + ~3 cluster cards + honesty banner |
| standard | + comparison-table audit + omissions + use paths |
| deep | + axis stability notes + chronology nodes + stance-tagged open problems + fuller bias audit |

## Audit records (recommended; soft)

Notes for QA (avoid inventing hard schema keys that contradict current
`report` / `claim_ledger`): `survey_scope`, `taxonomy`, `coverage_window`,
`omissions_and_biases`, honesty banner text. Ledger already supports
`secondary_citation` (+ `cited_work` / `cited_marker`); carry `stance` via
`tags` until any schema bump.

Reproduction:

- Narrative survey: set `reproduction.status: not_applicable`, keep
  `checklist: []`, explain why in `reason_zh`, and anchor that classification
  with `reproduction.sources` to the survey's own scope/abstract. Do not invent
  an English reproduction instruction.
- Systematic review: use `status: applicable` and restate the survey's search
  protocol steps in the bilingual anchored `checklist`—not “reproduce all
  cited systems”.

## Likely reviewer objections

Incomplete coverage, unstable taxonomy, unfair cross-setting tables, outdated
snapshot, missing negative results, opinion presented as community consensus.
