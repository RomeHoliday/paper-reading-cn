# Depth: deep

Target report length: about **25–45 PDF pages**. Use when the user asks for
深读 / deep / 逐节精读-level analysis without switching to full translation.

## Selection intensity

- Still selective: do **not** become a full bilingual `paper.md`.
- Expand argument map with intermediate lemmas/claims and dependency edges.
- Method: include important variants, failure handling, and parameter spaces.
- Figures/tables: more coverage of secondary but load-bearing plots; still
  skip purely decorative assets.
- Evidence audit: per-metric and per-experiment breakdowns; fairness checks
  when the paper makes baseline or systems-evaluation comparisons;
  sensitivity and ablations.
- Theory papers: theorem map + proof sketch at lemma granularity when present.
- Surveys: richer taxonomy walkthrough and coverage gaps.
- Reproduction: detailed checklist with configs, seeds, hardware, and known
  brittle steps.
- If `code_repo=present`, aim for high mapping coverage of mechanism-level
  claims; leave honest `unverified` rather than forced `exact`.

## Guardrails

- Do not pad with rhetorical summaries to hit 25 pages.
- Every added item still needs English source + Chinese explanation + anchor.
- If page budget would force dropping anchors, stop adding items instead.
