# Paper type: theory

Primary contribution is a formal model, theorem, proof, complexity bound, or
theoretical characterization (with or without light experiments).

Formal reading is single-agent by default. Selective bilingual report: quote
formal statements tightly; every selected definition / assumption / theorem /
lemma needs bounded English, Chinese explanation, and exact anchors.

## Extract in this order

| Artifact | Must capture | Chinese fidelity |
|----------|--------------|------------------|
| Definitions | domain, free variables, “iff” vs “if” | Do not widen domain or add uniqueness |
| Assumptions | numbered A_i / model constraints | Do not add “合理/通常” assumptions |
| Theorem / lemma / corollary | hypotheses → conclusion; quantifiers; mode | Preserve asymptotic / w.h.p. / in expectation / approximate hedges |
| Proof strategy | technique tags + completeness | Never call a sketch a completed proof |
| Dependency | lemma/assumption → theorem edges | Do not present dependent results as independent |
| Boundary / counterexample | when assumptions fail; sourced only | No invented counterexamples as paper content |
| Intuition vs formal | separate records | Intuition cannot solely support a contribution |
| Notation | symbol → EN/ZH gloss + first use | ZH gloss ≤ EN strength |
| Code-repo (if present) | algorithm / check only | **Code never proves a theorem** |

## P0 — do not ship

| ID | Failure |
|----|---------|
| T-P0-1 | ZH upgrades the theorem: drops assumptions, widens quantifiers, turns asymptotic/expectation/w.h.p. into absolute guarantees, or rewrites sketch/outline as “证明了” |
| T-P0-2 | Intuition / proof sketch / motivational example filed as the theorem or as sole contribution support |
| T-P0-3 | Repo/code treated as proving theorem T (`exact` + 证明/成立/验证了定理) |

**Rules:**

- Tag each formal item with a `statement_role` audit label:
  `definition` | `assumption` | `theorem` | `lemma` | `corollary` |
  `proposition` | `conjecture` | `proof_strategy` | `intuition` |
  `counterexample` | `boundary`. Contributions must cite a formal role with
  anchors—not bare intuition.
- Keep `hedge_span_en` / matching ZH hedge (充分大, 依概率, 期望意义下, 草图,
  在假设…下, …). Strength lint: EN hedges must survive in ZH.
- With `code_repo=present`, allowed link kinds (audit notes):
  `implements_algorithm` | `checks_assumption_instance` |
  `reproduces_bound_experiment` | `other_unverified`. Never “proves theorem”.

## P1 — mandatory structure

| ID | Check |
|----|-------|
| T-P1-1 | **Assumption ledger** — id, EN, ZH, anchors; theorems attach `assumptions_refs` when the source states “Assume A1–A3” |
| T-P1-2 | **Quantifiers & guarantee mode** — record scope (e.g. ∀n≥n0) and mode: `deterministic` \| `in_expectation` \| `with_high_probability` \| `asymptotic` \| `approximate` \| `information_theoretic` \| `none_stated` |
| T-P1-3 | **Proof completeness** — `full_proof` \| `sketch` \| `deferred_appendix` \| `omitted` copied from paper wording; ZH must not upgrade |
| T-P1-4 | **Dependency edges** — `uses_assumption` / `uses_lemma` / `extends`; standard/deep: chain to hero result; brief: main theorem + direct deps |
| T-P1-5 | **Boundary** — for each hero theorem: sourced boundary/counterexample **or** `boundary_status=not_discussed_in_paper` (never invent) |
| T-P1-6 | **Notation table** — symbol, definition_en/zh, first_anchor, scope; ZH must not add optimality/uniqueness/equivalence absent in EN |
| T-P1-7 | Conjecture/open stays 猜想/未证 voice—not theorem-voice ZH |

## Common ZH upgrade patterns (block)

- “for sufficiently large n” → “对任意 n”
- “in expectation” / “w.h.p.” → bare “成立”
- “sketch” / “outline” → “证明了”
- “near-optimal” / “O(log n)-approx” → “最优”
- Omitting “under i.i.d. / Gaussian / realizability”
- “upper-bounds” / “consistent with” → “等价于” / “充分必要”
- Hiding conditions on c, ε

## Secondary experiments

If a small experiment section exists, label it `secondary_evidence` under
theory rules. Do not require the full empirical checklist unless the paper was
misclassified.

## Audit records (recommended; soft)

Record as QA / evidence-audit notes (not hard schema claims contradicting
current `report` / `claim_ledger`):

- per-claim: `statement_role`, `quantifier_scope`, `guarantee_mode`,
  `completeness`, `assumptions_refs`, `hedge_span_en`/`zh`, `zh_strength_ok`
- report-level notes: `assumption_ledger[]`, `notation_table[]`,
  `dependency_edges[]`, `theory_hero_results[]` (1–3 main theorems)

Use existing ledger roles (`author_claim` / `paper_evidence` /
`reader_judgment`) and optional `tags` until any schema bump lands.

## Likely reviewer objections

Hidden assumptions, loose constants, non-constructive results presented as
algorithms, asymptotic vs practical regime gaps, missing related theoretical
baselines, sketch overstated as complete proof.
