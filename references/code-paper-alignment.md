# Paper↔code alignment

Applies only when `code_repo=present`. Bind every mapping to a mechanical
`repo_snapshot.json`. Repository facts never upgrade a paper claim and
**never prove paper performance** (QPS/latency/throughput stay
paper-anchored or separate `reader_judgment` with run notes).

## Workflow

1. Run `repo_snapshot.py` → `repo_snapshot.json` (HEAD/commit, branch,
   tag-at-head if any, dirty sample, tracked total vs listed, exclusions).
2. Set `version_alignment` on the snapshot / `version_alignment_status` on
   the map; surface it in the report code-section header.
3. Map selected `CL###` claims → `code_map.json` with `match_basis`.
4. `verify_bundle.py` checks snapshot binding, path jail, exclusions, line
   ranges, and exact proof rules.

## Snapshot binding (required on `code_map.json`)

| Field | Rule |
|-------|------|
| `snapshot_id` | Required; matches the bound `repo_snapshot.json` id (snapshot id also required) |
| `commit` | Equals snapshot `git.commit` / `git.head`; live HEAD must resolve and match |
| `dirty` | Equals snapshot dirty flag **and** live porcelain dirty |
| `version_alignment_status` | One of `matched_tag`, `same_repo_unknown_rev`, `likely_drift`, `unrelated`, `user_asserted` |

`exact` requires `git.is_git` + `status=ok`. It is forbidden when alignment ∈
{`likely_drift`,`unrelated`}, when dirty (live or artifact), on non-git mounts,
or on secret/vendor paths, unless `match_basis=user_waived` with an explicit note.

## Mapping record

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
    "paper_sources": [{ "page": 4, "block_id": "S018" }],
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

Paper side always requires English statement, Chinese explanation, and
source anchors (`paper_sources` / `sources`).

## Status lattice

| Status | Meaning | Mechanical minimum |
|--------|---------|-------------------|
| `exact` | Named mechanism/param matches cited code behavior (and value/structure where applicable) | path under focus_root, not excluded/secret; lines in range with **(line_end−line_start+1)≤20 always**; `symbol` in slice **or** `code_excerpt` equals on-disk slice; alignment not drift/unrelated (unless waived); never performance proof in `role_*`/notes |
| `partial` | Related code; incomplete, stub, renamed, or token/number hit | path+range; may lack symbol |
| `mismatch` | Same key/symbol family, conflicting value/behavior | path+range for the conflicting fact |
| `unverified` | No adequate evidence (or name collision only) | may omit refs; require notes |

## `match_basis` rules

| Basis | Max status |
|-------|------------|
| `symbol_and_behavior` | `exact` (if proof holds) |
| `param_key_and_value` | `exact` (if proof holds) |
| `param_key_value_conflict` | `mismatch` |
| `token_or_number_hit` | `partial` or `unverified` — **never** `exact` |
| `name_only` | `unverified` (or ≤`partial`) — **never** `exact` |
| `readme_claim` | ≤`partial` |
| `user_waived` | per explicit user note |

Also: `name_match_only: true` (legacy) ⇒ not `exact`.

Excluded paths (`vendor/`, `third_party/`, `build/`, etc.) cannot be `exact`.
Secret path parts (`.ssh`, `secrets`, `private`, `credentials`) and SSH key
basenames (`id_rsa` / `id_dsa` / `id_ecdsa` / `id_ed25519`, …) are always
blocked: never read or render `code_excerpt` from them.

## `code_excerpt` guidance

- Store the exact on-disk slice for `line_start`–`line_end`.
- `exact` always requires ≤20 lines; longer ranges must stay `partial`.
- Verifier rejects excerpts that do not equal the file slice.
- Never excerpt secret-denylist paths.

## What not to do

- Infer correspondence from README marketing or filename alone.
- Label `exact` from digit collisions or layout tokens
  (`token_or_number_hit`).
- Treat tests as proof of the paper’s experimental setup unless the paper
  says so.
- Narrate local binaries or defaults as proving published QPS/latency.
- Expose secrets (keys, tokens, `.p12`, keystores); snapshot lists redacted
  paths only—never copy contents into the bundle or report.
- Let bilingual `role_zh` widen paper guarantees (“完整证明了论文结果”).
