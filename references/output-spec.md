# Output specification

Schemas use **JSON Schema Draft 2020-12**. Files live under `schemas/`.
Formal reading defaults to `meta.execution_mode=single`. Multi-agent reading
requires `execution_mode=user_requested_multi` plus `user_opt_in_note`. Legacy
`single_agent: true` remains accepted.

## Bundle layout

```text
<output>/<paper-slug>/
├── source.pdf
├── report.json          # schemas/report.schema.json
├── report.html
├── report.pdf
├── source_map.json      # schemas/source-map.schema.json
├── claim_ledger.json    # schemas/claim-ledger.schema.json
├── qa_report.json
├── assets/
├── translation_notes.md
├── repo_snapshot.json   # when code_repo=present
└── code_map.json        # when code_repo=present; schemas/code-map.schema.json
```

Delivery is blocked when required bilingual fields, source IDs, assets, or
in-repo code paths are missing (`verify_bundle.py`).

## Stable IDs

| Prefix | Meaning |
|--------|---------|
| `S###` | Prose / structure block |
| `C###` | Caption block |
| `F###` | Figure |
| `T###` | Table |
| `CL###` | Claim ledger row |
| `CM###` | Code mapping row |

Pages are **1-based**. Anchors always include `page` + `block_id`; add
`figure_id` / `table_id` when the evidence is a figure or table.

## Bilingual contract

Every load-bearing report/claim item uses:

```json
{
  "original_en": "verbatim or tightly bounded English",
  "explanation_zh": "faithful Chinese explanation",
  "sources": [{ "page": 3, "block_id": "S021" }]
}
```

- `original_en`: bounded quote, not a free rewrite.
- `explanation_zh`: selective explanation, not full-paper translation.
- Do not use announcement labels such as “本文的关键洞见是” or “Takeaway:”.

## Numeric provenance (`claim_ledger.numeric`)

| `kind` | Required extras |
|--------|-----------------|
| `quoted` | `value`, `unit`, `sources` (≥1); no `expression` |
| `derived` | `value`, `unit`, `expression`, `inputs` (≥1), `sources` (≥1) |
| `chart_estimate` | `value`, `unit`, `sources` (≥1), `uncertainty_note_zh` |

Never invent numbers. Chart reads must stay `chart_estimate`.

## Code map statuses

`exact` | `partial` | `mismatch` | `unverified`. Require `match_basis`. Name-only
and `token_or_number_hit` cannot be `exact`. `exact` needs in-range symbol or
matching `code_excerpt` (≤20 lines), snapshot binding, and is blocked on dirty /
`likely_drift` / `unrelated` unless `user_waived` with notes.

---

## Minimal examples

### `source_map.json` — valid

```json
{
  "schema_version": "1.0",
  "paper": {
    "title": "Example Systems Paper",
    "source_type": "pdf-text",
    "source_path": "source.pdf",
    "page_count": 1,
    "language": "en"
  },
  "blocks": [
    {
      "id": "S001",
      "page": 1,
      "type": "paragraph",
      "order": 1,
      "text": "We reduce probe cost by graph routing.",
      "confidence": "high"
    }
  ],
  "pages": [{ "page": 1, "block_ids": ["S001"] }]
}
```

### `source_map.json` — invalid

Missing `page_count`; block id `X1` fails `^(S|C)[0-9]{3,}$`.

```json
{
  "schema_version": "1.0",
  "paper": {
    "title": "Bad",
    "source_type": "pdf-text",
    "source_path": "source.pdf"
  },
  "blocks": [
    { "id": "X1", "page": 1, "type": "paragraph", "order": 1, "text": "x", "confidence": "high" }
  ],
  "pages": [{ "page": 1, "block_ids": ["X1"] }]
}
```

### `claim_ledger.json` — valid (quoted number)

```json
{
  "schema_version": "1.0",
  "paper_slug": "example",
  "claims": [
    {
      "id": "CL001",
      "role": "paper_evidence",
      "original_en": "Throughput reaches 28.7K QPS at R@10=0.95.",
      "explanation_zh": "在 R@10=0.95 时吞吐达到 28.7K QPS。",
      "sources": [{ "page": 8, "block_id": "S042", "table_id": "T002" }],
      "confidence": "high",
      "numeric": {
        "kind": "quoted",
        "value": 28700,
        "unit": "QPS",
        "display": "28.7K QPS",
        "sources": [{ "page": 8, "block_id": "S042", "table_id": "T002" }]
      }
    }
  ]
}
```

### `claim_ledger.json` — invalid (derived without expression)

```json
{
  "schema_version": "1.0",
  "paper_slug": "example",
  "claims": [
    {
      "id": "CL002",
      "role": "reader_judgment",
      "original_en": "about 10x faster",
      "explanation_zh": "大约快十倍",
      "sources": [{ "page": 1, "block_id": "S001" }],
      "confidence": "low",
      "numeric": {
        "kind": "derived",
        "value": 10,
        "unit": "x",
        "sources": [{ "page": 1, "block_id": "S001" }]
      }
    }
  ]
}
```

### `code_map.json` — valid

```json
{
  "schema_version": "1.0",
  "paper_slug": "example",
  "repo": { "root": "D:/repos/example", "head": "abcdef1234567890", "dirty": false },
  "mappings": [
    {
      "id": "CM001",
      "claim_id": "CL001",
      "status": "partial",
      "paper_statement_en": "Block-aligned scheduling reduces probes.",
      "paper_explanation_zh": "块对齐调度减少探测次数。",
      "paper_sources": [{ "page": 4, "block_id": "S018" }],
      "code_refs": [
        {
          "path": "src/index.cpp",
          "line_start": 120,
          "line_end": 168,
          "symbol": "BlockPlanner::plan",
          "role_en": "implements block-aligned scheduling",
          "role_zh": "实现块对齐调度"
        }
      ],
      "confidence": "medium"
    }
  ]
}
```

### `code_map.json` — invalid

`status: exact` with `name_match_only: true` is forbidden; `unverified`
without `notes_zh` is forbidden.

```json
{
  "schema_version": "1.0",
  "paper_slug": "example",
  "repo": { "root": "D:/repos/example", "head": "abcdef1234567890" },
  "mappings": [
    {
      "id": "CM002",
      "claim_id": "CL001",
      "status": "exact",
      "name_match_only": true,
      "paper_statement_en": "Uses HNSW.",
      "paper_explanation_zh": "使用 HNSW。",
      "paper_sources": [{ "page": 2, "block_id": "S005" }],
      "code_refs": [
        {
          "path": "src/hnsw.h",
          "line_start": 1,
          "line_end": 2,
          "role_en": "header name",
          "role_zh": "仅文件名相似"
        }
      ]
    }
  ]
}
```

### `report.json` — valid (minimal)

```json
{
  "schema_version": "1.0",
  "meta": {
    "paper_slug": "example",
    "title_en": "Example Systems Paper",
    "title_zh": "示例系统论文",
    "depth": "brief",
    "generated_at": "2026-07-20T00:00:00+08:00",
    "single_agent": true,
    "code_repo": "absent"
  },
  "overview": {
    "summary_zh": "该工作用图路由降低探测成本。",
    "audience_zh": "向量检索系统研究者。",
    "key_points": [
      {
        "original_en": "We reduce probe cost by graph routing.",
        "explanation_zh": "通过图路由降低探测成本。",
        "sources": [{ "page": 1, "block_id": "S001" }]
      }
    ]
  },
  "problem": {
    "items": [
      {
        "original_en": "Dense centroid scan dominates latency.",
        "explanation_zh": "稠密中心扫描主导延迟。",
        "sources": [{ "page": 1, "block_id": "S001" }]
      }
    ]
  },
  "argument_map": {
    "nodes": [
      {
        "label_zh": "核心主张",
        "original_en": "Graph routing replaces full centroid scans.",
        "explanation_zh": "图路由替代全量中心扫描。",
        "sources": [{ "page": 1, "block_id": "S001" }]
      }
    ]
  },
  "method": {
    "items": [
      {
        "original_en": "Build a centroid graph offline.",
        "explanation_zh": "离线构建中心图。",
        "sources": [{ "page": 1, "block_id": "S001" }]
      }
    ]
  },
  "selected_figures_tables": [],
  "evidence_audit": {
    "items": [
      {
        "original_en": "Figure 1 reports QPS vs recall.",
        "explanation_zh": "图 1 报告吞吐随召回变化。",
        "sources": [{ "page": 1, "block_id": "C001", "figure_id": "F001" }],
        "verdict_zh": "支持延迟主张的方向，但缺少误差条。"
      }
    ]
  },
  "contributions": {
    "items": [
      {
        "original_en": "A centroid-graph router for IVF search.",
        "explanation_zh": "面向 IVF 检索的中心图路由器。",
        "sources": [{ "page": 1, "block_id": "S001" }]
      }
    ]
  },
  "limitations": {
    "items": [
      {
        "original_en": "Evaluated on two datasets only.",
        "explanation_zh": "仅在两个数据集上评估。",
        "sources": [{ "page": 1, "block_id": "S001" }]
      }
    ]
  },
  "reproduction": {
    "status": "applicable",
    "checklist": [
      {
        "step_zh": "按第 8 节默认参数构建索引。",
        "original_en": "Build the index with Section 8 defaults.",
        "sources": [{ "page": 1, "block_id": "S001" }]
      }
    ]
  },
  "terminology": [
    { "term_en": "nprobe", "term_zh": "探测列表数" }
  ]
}
```

For a narrative survey with no reproduction procedure, do not invent an
English step. Use an anchored classification:

```json
{
  "reproduction": {
    "status": "not_applicable",
    "reason_zh": "该文为叙述性综述，没有提出需要复现的新实验流程。",
    "sources": [{ "page": 1, "block_id": "S001" }],
    "checklist": []
  }
}
```

### `report.json` — invalid

Missing `explanation_zh` on a key point; `single_agent` must be `true`.

```json
{
  "schema_version": "1.0",
  "meta": {
    "paper_slug": "example",
    "title_en": "Example",
    "title_zh": "示例",
    "depth": "brief",
    "generated_at": "2026-07-20T00:00:00+08:00",
    "single_agent": false
  },
  "overview": {
    "summary_zh": "摘要",
    "audience_zh": "读者",
    "key_points": [
      {
        "original_en": "We reduce probe cost.",
        "sources": [{ "page": 1, "block_id": "S001" }]
      }
    ]
  }
}
```

## Report sections (required)

1. One-page Chinese overview  
2. Problem and significance  
3. Argument map  
4. Method / design / algorithm  
5. Selected figures and tables  
6. Experiment and evidence audit  
7. Contributions and boundaries  
8. Limitations and likely reviewer objections  
9. Reproduction checklist  
10. Terminology table  
11. Optional paper↔code correspondence when `code_repo=present`
