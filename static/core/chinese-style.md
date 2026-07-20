# Chinese writing style

Write for a technically literate Chinese reader who may cross-check the English
paper. Prefer clarity and precision over flourish.

## Voice and structure

- Use direct academic Chinese. Lead with the substance of the claim or finding.
- Prefer short-to-medium sentences. One idea per sentence when numbers or
  conditions are involved.
- Keep section flow: overview → problem → argument → method → evidence →
  boundaries → limitations → reproduction → terminology → (optional) code map.
- Do not pad to hit the depth page budget. Cut commentary before cutting
  anchored evidence.

## Forbidden announcement patterns

Do not open or punctuate paragraphs with empty labels such as:

- “本文的关键洞见是”
- “核心贡献在于” / “关键结论如下”
- “Takeaway:” / “Key takeaway” / “Key insight:”
- “值得注意的是：” as a filler opener
- “值得一提的是” as a filler opener
- “总而言之，” / “综上所述，” as a closing slogan without new content
- “首先/其次/最后” chains that restate headings without adding substance

State the finding, constraint, or objection directly.

## Terminology

- Prefer established Chinese technical terms when they are standard in the
  field; otherwise keep the English term and give a short Chinese gloss on
  first use.
- Preserve model names, system names, dataset names, metrics, symbols, and
  units unchanged (e.g. `Recall@10`, `nprobe`, `QPS`).
- Build a terminology table for recurring terms; reuse the same Chinese gloss
  throughout the report and claim ledger.
- Do not “localize” citation keys, figure IDs, or file paths.

## Numbers, hedges, and judgments

- Keep numerals, units, and comparison directions exactly as in the source
  unless deriving a value with an explicit expression.
- Preserve hedging: “may”, “suggests”, “up to”, “on average” → matching Chinese
  hedges; do not harden soft claims.
- Mark `reader_judgment` in Chinese as assessment (“读者判断：…” or an
  equivalent clear marker), never as if it were paper text.
- Flag OCR-uncertain text with “【OCR不确定】” or a structured confidence field.

## Bilingual presentation in the PDF

For load-bearing items in the rendered report, show:

1. Chinese explanation as the primary readable line;
2. English source as a clearly bounded quotation or tight paraphrase labeled
   as source;
3. anchor footer: page + block ID (+ figure/table ID).

Do not bury anchors only in JSON; the human-readable PDF must remain
checkable against the paper.

## Tone toward limitations and reviewers

Be specific and fair. Name the missing baseline, confound, assumption, or
scope limit. Avoid moralizing or promotional language. Likely reviewer
objections should be concrete enough that an author could act on them.
