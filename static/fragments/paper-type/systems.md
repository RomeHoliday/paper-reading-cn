# Paper type: systems

Primary contribution is a system, architecture, algorithm implementation, or
systems evaluation (OS, networking, databases, storage, ML systems, ANN
indexes / search engines, etc.).

Formal reading is single-agent by default. Keep a selective bilingual report:
load-bearing items need bounded English, Chinese explanation, and page /
`S###` / `F###` / `T###` anchors. Prefer `claim_ledger` IDs over free-floating
numbers in the overview.

## Required analytical spine

Even at `brief` depth, keep structure (shorten text, do not drop sections):

1. **Problem** — bottleneck, workload, why prior systems fail (EN + ZH).
2. **Design** — mechanisms and invariants that address the constraint; no
   slogan labels (“关键洞见是”, “Takeaway:”).
3. **Implementation** — stack, critical path, defaults/knobs, prototype vs
   production-hardened *as the paper states*.
4. **Evaluation** — testbed, workloads, baselines, metrics; prefer the paper’s
   evaluation questions; order: end-to-end → micro → ablation/sensitivity →
   overhead.
5. **Baseline fairness audit** — per named baseline (below).
6. **Deployment assumptions** — claimed target vs evaluated testbed (below).
7. **Claim → evaluation map** — every abstract/intro performance claim maps to
   proving figure/table IDs or is marked `unproven` / limitation.
8. **Limitations & likely reviewer objections** — insight, claim–eval match,
   baselines, attribution, overclaim, generality, reproducibility, ignored
   neighbor, number consistency; each hit is “covered by §/fig” or explicit
   gap—never silent.

## P0 — do not ship

| ID | Failure |
|----|---------|
| S-P0-1 | Overview restates abstract speedups without figure/table / ledger anchor, or silently merges disagreeing numbers |
| S-P0-2 | “Beats baseline” without fairness status, or while `cross_regime` / asymmetric tuning is unstated |
| S-P0-3 | Repo/code used to prove paper numeric results or upgrade `author_claim` |
| S-P0-4 | Invented ablation deltas or invented missing-neighbor citations |

**Rules:**

- Every performance number in the report is a ledger entry: `author_claim` +
  `paper_evidence` + anchors; derived ratios need numeric provenance
  (`quoted` / `derived` + expression). Overview may only reuse ledger IDs.
- Per baseline record (audit note, not a hard schema field): name,
  `config_source` (`authors_recommended` | `paper_table` | `default` |
  `unknown`), `tuning` (`equal` | `unequal` | `unstated`), metric anchor
  (same recall@k / SLO / workload), `fairness`
  (`fair_compare` | `asymmetric_tuning` | `cross_regime` | `unstated`).
- Chinese may call a result a “win” only when `fair_compare`, or when
  asymmetry is stated in the same bilingual block as `reader_judgment`.
- With `code_repo=present`: map mechanisms/defaults only
  (`exact` / `partial` / `mismatch` / `unverified`). Code never changes
  paper truth values; unpublished local measurements are `reader_judgment`.

## P1 — mandatory gaps to surface

| ID | Check |
|----|-------|
| S-P1-1 | **Deployment match:** claimed deployment (datacenter / RDMA / multi-GPU / SSD QD, …) vs testbed → `matched` \| `scaled-down` \| `lab-only` \| `unstated`. Overview must not imply production when ≠ `matched` |
| S-P1-2 | **Claim→eval bijection:** abstract+intro claims mapped to `F#`/`T#` or `unproven` |
| S-P1-3 | **Attribution / ablation:** multi-mechanism designs note which ablation supports which mechanism, or `no_ablation_in_paper` as `reader_judgment`—do not invent deltas |
| S-P1-4 | **ANN recall regime** (vector search / index / retrieval): dataset (scale, dim); QPS/latency at stated Recall@k; high-recall points (0.95/0.99) if discussed; build time / index size / update if claimed. Speedups without recall anchor → `cross_regime` |
| S-P1-5 | Tail-latency claims need tail evidence; mean-only → `partial` / `unproven` for that claim |

## Audit records (recommended; soft)

Record under evidence audit / QA notes (do not invent hard `report.json`
fields that contradict the current schema):

- `systems_audit.deployment` — claimed vs evaluated + match
- `systems_audit.baselines[]` — fairness rows above
- `systems_audit.claim_eval_map[]` — `{claim_id, proved_by[], status}`

## Reproduction checklist

Hardware/software versions if stated; datasets/traces; baseline names+versions;
config knobs and paper-recommended settings; build/run commands if present;
open questions. Repo mapping fills paths/symbols—never name-only matches.

## Likely reviewer objections

Unfair baselines, micro vs end-to-end gap, unsupported scalability
extrapolation, unreproducible configs, overclaimed novelty, ANN cross-recall
comparisons, deployment/testbed mismatch.
