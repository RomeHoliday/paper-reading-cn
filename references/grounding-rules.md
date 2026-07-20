# Grounding rules

Evidence-first rules for selective Chinese reading reports. Formal reading uses
`report.meta.execution_mode=single` by default (`user_requested_multi` only with
an explicit `user_opt_in_note`). Legacy `single_agent: true` is accepted as
`single`.

## Roles (never conflate)

| Role | Meaning |
|------|---------|
| `author_claim` | What the paper asserts. |
| `paper_evidence` | Measurements, figures, tables, proofs that support or bound a claim. |
| `reader_judgment` | Analyst interpretation. Must not upgrade a claim without paper support. |

Every load-bearing report unit and ledger claim carries `role` plus `zh_mode`
(`translation` | `explanation` | `caption_translation` | `term_gloss`). Report
units link into the claim ledger via required `claim_id`.

`reader_judgment` must use `zh_mode=explanation`, keep `original_en` empty
(no quote-shaped EN), and reach `author_claim` / `paper_evidence` through
`depends_on_claim_ids` (no closed RJ-only cycles).

Mark secondary citations (`secondary_citation: true`) when the paper reports
another work’s result; then `evidence_independence` must be `secondary` or
`non_independent`.

## Anchor rules

1. Every load-bearing sentence cites `page` + `block_id` from `source_map.json`.
2. Figure/table evidence also cites `figure_id` / `table_id` and the caption `C###`.
3. Reusing one block for many claims is allowed; each claim still lists its own
   `sources` array.
4. Abstract repetition, alt text, code comments, and analysis cards are **not**
   independent paper evidence.
5. If OCR/extraction confidence is `low`, set claim `confidence` to `low` or
   `medium` and write `uncertainty_note_zh`.
6. `block_id` points only to an `S###` or `C###` block. Put visual IDs in
   `figure_id` / `table_id`; verify that the anchor page equals the source
   block page.
7. Normalize whitespace, ligatures, and line-break hyphenation, then verify
   every non-judgment `original_en` is a bounded span of its cited blocks.
   A plausible paraphrase is not a source quote.
8. Claim confidence cannot exceed the weakest cited source. Quoted numerics
   from low-confidence OCR are blockers; medium OCR is draft-only.

`paper_evidence` rows link the `author_claim` rows they support through
`supports_claim_ids`. A `reader_judgment` cites its inputs through
`depends_on_claim_ids`; it does not invent an English sentence to impersonate
paper text.

## Numeric rules

- **No invented numbers.**
- Every measurement-like token in ledger or report surfaces (bare QPS/ms/GB,
  K/M/B suffixes, comma ints, ASCII `x` / `×`, percentages) needs typed
  `numeric` or a valid `numeric_ref` / linked ledger numeric. Year / Fig /
  Section / model / arXiv identities themselves skip, but must not sink
  neighboring metrics.
- `quoted`: copy the paper value; keep unit, source anchor, and required
  `display` that appears in EN/ZH.
- `derived`: supply machine-readable `expression` and typed `inputs` with units
  and **source-bound** inputs (production blocks `external_note` inputs);
  verifier recomputes via AST. Relative comparisons need baseline **and**
  treatment inputs; unit sanity is checked.
- `chart_estimate`: reading from a plot without a printed number; require
  `uncertainty_note_zh` and the figure anchor.
- Rounding notes go in `rounding` or `uncertainty_note_zh`.
- Numeric values supported only by chart ink are estimates even when they look
  visually exact.

## Hedge / strength (non-RJ)

English hedges (`may` / `might` / `suggest` / `associated` / `correlated` /
`up to` / `on average`) and negations (`no` / `not` / `cannot`) must not become
Chinese strengtheners (`证明` / `必然` / `导致` / `因果` / `全部` / `always`) or
lose negation. `reader_judgment` is checked separately (no forged quote EN).

## Contradiction handling

- If two claims conflict, keep both rows and link via `contradicts_claim_ids`.
- Prefer stating the conflict in `reader_judgment`; do not silently drop one side.
- Delivery blockers (see `verify_bundle.py` / `qa_report.json.blockers`):
  `DANGLING_BLOCK` / wrong-page anchors, `SPAN_MISMATCH`, `CONFIDENCE_FLOOR`,
  `ROLE_GRAPH`, `NUMERIC_UNTYPED` / derived mismatch / `ORPHAN_DISPLAY`,
  missing bilingual pair, missing asset/crop provenance, code snapshot/drift/
  exact-proof failures, unresolved placeholders in production prose.

## Good / bad patterns

**Good:** Chinese explanation + bounded English + `p.5 S014`, Fig. 3 caption.  
**Bad:** Chinese-only conclusion; title/abstract used as body evidence;
figure claim without caption/figure ID; treating repository behavior as paper
evidence without an explicit paper anchor.
