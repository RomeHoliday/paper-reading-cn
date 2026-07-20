# Output contract

## Bundle layout

Default output root is `<workspace>/readings/` unless the user supplies another
path. Derive an ASCII-safe slug from arXiv ID, DOI suffix, PDF stem, or title;
fall back to `paper-YYYYMMDD-HHMMSS`. Never overwrite an existing bundle
silently. Reuse it only after confirming the source hash matches and the user
requested an in-place rerun; otherwise append `__run-YYYYMMDD-HHMMSS`.

```text
<output>/<paper-slug>/
├── source.pdf
├── report.json
├── report.html
├── report.pdf
├── source_map.json
├── claim_ledger.json
├── qa_report.json
├── assets/
├── translation_notes.md
├── repo_snapshot.json   # only when code_repo=present
└── code_map.json        # only when code_repo=present
```

All paths are relative to the paper-slug directory unless noted.

## Required report sections (`report.json` → HTML/PDF)

| Section | Content |
|---------|---------|
| Overview | One-page Chinese overview of problem, method, and evidence stance |
| Problem | Research problem and significance |
| Argument map | Premises → design → evidence → claims |
| Method | Method / design / algorithm essentials |
| Figures & tables | Selected key figures/tables with bilingual captions |
| Evidence audit | Experiment setup, metrics, baselines, what is/isn't supported |
| Contributions | Contributions and explicit boundaries |
| Limitations | Limitations and likely reviewer objections |
| Reproduction | Reproduction checklist |
| Terminology | Recurring term table (EN ↔ ZH, optional note) |
| Code map | Optional; required when `code_repo=present` |

Do not use announcement labels (“本文的关键洞见是”, “Takeaway:”). State substance.

## Load-bearing item shape

Every selected claim, caption, method statement, limitation, contribution, and
code correspondence must include:

- English source text (bounded / verbatim where feasible);
- Chinese explanation;
- `sources[]` with `page` and `block_id` (plus `figure_id` / `table_id` when
  relevant);
- `role`: `author_claim` | `paper_evidence` | `reader_judgment`;
- `confidence` when extraction or OCR is uncertain.

Claim IDs use `CL###`. Citeable source blocks use `S###` / `C###`; figures and
tables use separate `F###` / `T###` IDs on the same anchor.

## Figure / table blocks

Each selected figure or table must include:

- original English caption;
- Chinese caption explanation;
- axes / encoding notes (figures) or column semantics (tables);
- evidence supported;
- evidence not supported;
- source `page` and `F###` / `T###` / `C###`;
- asset path under `assets/` when a crop exists.

## Claim ledger

`claim_ledger.json` lists load-bearing claims with bilingual fields, anchors,
optional numeric provenance (`expression`, `inputs`, `sources`), and role
labels. Derived numbers without expression+inputs are blockers.

## Repository artifacts (when present)

- `repo_snapshot.json`: factual snapshot only (path, HEAD, dirty, inventory).
- `code_map.json`: each entry has `claim_id`, `status`
  (`exact`|`partial`|`mismatch`|`unverified`), bilingual paper fields, and
  `code_refs[]` with `path`, `line_start`, `line_end`, optional `symbol`,
  `role_en`, `role_zh`.

## QA and gates

`qa_report.json` must record:

- detected axes;
- script pass/fail/skip;
- missing bilingual fields, dangling IDs, missing assets;
- OCR uncertainty summary;
- repo mapping coverage and drift warnings.

**Block `report.pdf` delivery** when required bilingual fields, source IDs,
span checks, confidence floors, role-graph links, numeric provenance, assets /
crop provenance, or (if repo present) valid code snapshot/path/status proofs
are missing. `verify_bundle.py` always writes `qa_report.json` and fails closed
on blockers. Disclose blockers in chat and in `qa_report.json`.

Paper-type P0 rules (baseline fairness, causal-strength ceilings, theorem
hedges, survey honesty) remain mandatory agent review gates where they require
semantic judgment. The deterministic verifier enforces their representable
fields, but a verifier pass never waives the loaded paper-type checklist.

`report.meta.execution_mode` is `single` (default) or `user_requested_multi`
(requires `user_opt_in_note`). Legacy `single_agent: true` maps to `single`.

## Pre-response verification checklist

Before the final user-facing reply:

- [ ] `source_map.json` parses and IDs are stable
- [ ] `claim_ledger.json` bilingual + anchors for every load-bearing claim
- [ ] `report.json` has all required sections for the depth budget
- [ ] every referenced asset exists under `assets/`
- [ ] `report.html` and `report.pdf` exist (or PDF blocker disclosed)
- [ ] `translation_notes.md` and `qa_report.json` written
- [ ] if repo present: `repo_snapshot.json` + `code_map.json` with valid statuses
- [ ] no default subagent dispatch occurred unless the user requested it
