# Paper type: empirical

Primary contribution is an empirical study: experimental design, measurement,
statistics, human subjects, ML evaluation-as-science, or observational analysis
(clinical / lab / social / econ).

Formal reading is single-agent by default. Selective bilingual report: every
load-bearing design/stat claim is bounded English + Chinese + exact anchors.
Overview narrates by card/ledger IDs—it never replaces body-anchored cards.

## Classify subtype

Note `empirical_subtype` in audit notes:
`ml_eval` | `biomedical` | `social_obs` | `mixed`.
Shared mandatory slots; subtype only changes prompts and allowed
`n/a_with_reason` (do not force IV/DiD language onto ImageNet papers).

## Mandatory evidence cards (body-anchored)

Each card: bilingual unit(s) + ≥1 `sources` anchor **not** exclusively from
abstract / highlights / teaser / graphical abstract. If only abstract text
exists for a slot, set status `unavailable_in_body` with an explicit note—
do not silently fill from the abstract.

| ID | Slot | Must capture |
|----|------|--------------|
| EQ | Research question | Primary Q / hypothesis |
| POP | Population / data | Units, inclusion, dataset, N / splits |
| DES | Design | RCT / observational / quasi / lab / ML train-val-test or CV |
| EST | Estimand | Target parameter or evaluation functional (plain language + EN span) |
| CTL | Controls / identification | Covariates, FE, IV, matching, DiD, randomization; ML: leakage, negatives, tuning. If none: record with EN span—do not invent |
| STA | Statistics | Model / test / metric, primary specification |
| UNC | Uncertainty | CI / SE / p / q / credible / bootstrap; or explicit none near estimate |
| ROB | Robustness | Sensitivity / placebo / alt spec / ablation-as-stability; else `not_reported` after body search |
| LIM | Limitations | Author-stated threats (prefer Discussion; abstract OK only if also body-anchored) |
| AVL | Availability | Data + code statements, access constraints, URL/DOI if given → `stated` \| `not_stated` \| `restricted` \| `link_only` |

**Primary results** (`RES-1…k`, ≥1 at standard/deep): each headline finding
links STA+UNC (+ table/figure when the number lives there) and sets
`claim_strength` (below).

Depth: **brief** — EQ, POP, DES, EST, ≥1 RES+UNC, LIM, AVL; CTL/ROB may be
short `n/a_with_reason`. **standard** — all slots + ≥2 RES + ROB/CTL filled.
**deep** — + multiplicity note when ≥3 primary outcomes/metrics selected.

## P0 — do not ship

| ID | Failure |
|----|---------|
| E-P0-1 | Abstract-only methods/results: mandatory cards missing, empty, or solely abstract-anchored without `unavailable_in_body` |
| E-P0-2 | Causal-language upgrade: ZH/overview turns associate/predict into 导致 / 因果效应 / “proves X causes Y” beyond card strength |
| E-P0-3 | Point estimate / “significant” without nearby uncertainty when the paper reports it, or wrong table/figure anchor |

**Abstract use:** OK for overview connective tissue and restatement *after*
body cards exist. Forbidden as sole `paper_evidence` for
RES/STA/UNC/POP/DES/EST/CTL/ROB.

## Causal-language ceiling

Set `claim_strength` from anchored English (judgment may downgrade, never
upgrade):

| EN cues | Max strength | ZH framing |
|---------|--------------|------------|
| describe / prevalence / correlate | `associational` or lower | 相关 / 伴随 |
| predict / forecast / accuracy | `predictive` | 预测 / 判别（非机制因果） |
| effect / impact / causes | `causal_claim` | 作者主张的效应；注明是否识别 |
| RCT / valid IV / sharp RDD + assumptions | `causal_identified` | 在识别策略下的因果估计 + 关键假设 |

Overview Chinese must not exceed the strongest linked RES/EQ card. Do not
translate “associated with” as “导致”. Author causal verbs without
identification → keep `causal_claim` + `reader_judgment` gap note.

## Statistics & uncertainty (each primary RES)

1. Name estimator / model / metric as in English.
2. Quote point estimate with units and comparison target.
3. Attach uncertainty if in the same row/sentence/adjacent note; never invent
   p/CI (stars alone → `significance_code_only` if that is all the paper gives).
4. Record N / runs / seeds / clustering / robust SE / FDR when stated.
5. Separate pre-registered primary vs exploratory if marked.

## Robustness & availability

- ROB accepts alt specs, placebo, bandwidth, attrition, seed/HP sensitivity,
  held-out sites—not abstract “results are robust” alone.
- AVL: data and code statuses separately when both exist. A URL is *stated
  location*, not successful reproduction. Mounted repo mapping must not
  upgrade paper availability claims.

## Audit records (recommended; soft)

Keep an `empirical_audit` note object for QA (cards, `claim_strength`,
`abstract_only`, `causal_language_pass`) without requiring schema fields that
contradict current `report` / `claim_ledger` contracts. Prefer ledger roles
`author_claim` / `paper_evidence` / `reader_judgment` plus tags for card IDs.

## Likely reviewer objections

Confounds, multiple testing, leakage, non-representative samples, metric
mismatch, unreproducible preprocessing, overgeneralization, identification
gaps framed as proven causation.
