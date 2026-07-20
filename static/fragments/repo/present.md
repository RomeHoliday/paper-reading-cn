# Repository: present

The user supplied or mounted a code repository. Paper↔code correspondence is
required for selected implementation-relevant claims. Formal reading stays
single-agent unless the user explicitly requests otherwise.

## Mandatory artifacts

1. Run `scripts/repo_snapshot.py` → `repo_snapshot.json`  
   Record absolute path, Git HEAD/commit, branch, tag-at-head when cheap,
   dirty flag + dirty tracked-file sample, tracked total vs listed,
   truncation, language counts, README/config/entrypoint *candidates*, and
   exclusions (vendor/generated/build + secret redactions). Snapshot facts
   must not claim semantic correspondence. Never open or copy secret
   contents. Repository facts never prove paper performance.

2. Build `code_map.json` bound to that snapshot. Required top-level fields:
   `snapshot_id`, `commit`, `dirty`, `version_alignment_status`, `mappings`.

   ```json
   {
     "schema_version": "1.0",
    "snapshot_id": "RS0123456789ab",
     "commit": "abc1234deadbeef",
     "dirty": false,
     "version_alignment_status": "same_repo_unknown_rev",
     "mappings": [{
       "id": "CM001",
       "claim_id": "CL003",
       "status": "exact",
       "match_basis": "symbol_and_behavior",
       "paper_statement_en": "...",
       "paper_explanation_zh": "...",
       "paper_sources": [{"page": 4, "block_id": "S088"}],
       "code_refs": [{
         "path": "src/index.cpp",
         "line_start": 120,
         "line_end": 168,
         "symbol": "BlockPlanner::plan",
         "code_excerpt": "...≤20 lines exact file slice...",
         "role_en": "implements block-aligned scheduling",
         "role_zh": "实现块对齐调度"
       }]
     }]
   }
   ```

## Version alignment (surface in section header)

| Status | Meaning |
|--------|---------|
| `matched_tag` | HEAD matches a paper-relevant tag |
| `same_repo_unknown_rev` | Same repo, revision relationship unclear |
| `likely_drift` | Code likely diverged from the described artifact |
| `unrelated` | Mount does not appear to be the paper’s code |
| `user_asserted` | User explicitly pinned the revision |

`exact` is forbidden for `likely_drift` / `unrelated` unless
`match_basis=user_waived` with a note.

## Status definitions (required)

| Status | Meaning |
|--------|---------|
| `exact` | Mechanical proof: path in focus root, not excluded; lines in range; `symbol` in slice **or** `code_excerpt` equals on-disk slice (≤20 lines); paper EN/ZH + anchors present. |
| `partial` | Related implementation exists but differs, is incomplete, or is only a token/number hit. |
| `mismatch` | Cited code conflicts with the paper statement. |
| `unverified` | Insufficient evidence—default when uncertain. |

Every mapped item **must** use one of these four statuses.

## `match_basis` hard rules

- `name_only` → never `exact`
- `token_or_number_hit` → `partial` or `unverified` only
- Excluded paths (vendor/third_party/build/secrets/…) → never `exact`
- Prefer `symbol_and_behavior` or `param_key_and_value` for real matches

## Mapping rules

- Paper side always: `paper_statement_en` + `paper_explanation_zh` + anchors.
- Require concrete `path` + `line_start`/`line_end`; add `symbol` and/or
  `code_excerpt` (≤20 lines) for `exact`.
- Config/default claims must point at the definition, not only a call site.
- Repository evidence never upgrades a paper claim and never proves
  published QPS/latency/throughput.
- Incomplete inspection → `unverified` or `partial`.
- If HEAD drifts from the snapshot after mapping, re-snapshot / re-verify.
- Include a code-correspondence section summarizing status counts; call out
  `mismatch` and alignment uncertainty.

## Gate

Missing `code_map.json`, missing snapshot binding, invalid statuses, name-only
or token-hit labeled `exact`, excluded-path `exact`, dangling paths, or
out-of-range lines are **blocking** for `report.pdf` when
`code_repo=present`.
