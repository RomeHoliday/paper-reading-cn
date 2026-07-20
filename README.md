# paper-reading-cn

面向学术论文的**选择性中文 PDF 精读报告** Cursor Skill：抓重点、留英文锚点，不是全文逐段翻译。

A Cursor Agent Skill that produces a **selective, source-grounded Chinese PDF reading report** for one academic paper (PDF / DOI / arXiv / publisher HTML), with optional paper↔code correspondence. It is **not** a full bilingual translation.

**Copyright © 2026 xym** · MIT License · see [LICENSE](LICENSE) and [NOTICE](NOTICE)

---

## What it does

- Selective analytical Chinese report (`brief` / `standard` / `deep`)
- Load-bearing claims keep English source text, Chinese explanation, and exact paper anchors
- Optional code-repo mapping (`exact` / `partial` / `mismatch` / `unverified`)
- Deterministic helpers under `scripts/` for extract → verify → render
- Single-agent by default (no multi-agent dispatch unless you ask)

For paragraph-by-paragraph bilingual reading, use a different skill (e.g. nature-reader).

---

## Install

Copy or clone this skill into your Cursor skills directory as `paper-reading-cn`:

| OS | Path |
|----|------|
| Linux / macOS | `~/.cursor/skills/paper-reading-cn` |
| Windows | `%USERPROFILE%\.cursor\skills\paper-reading-cn` |

```bash
# Example: clone into the skills folder
git clone <THIS_REPO_URL> ~/.cursor/skills/paper-reading-cn
```

Windows (PowerShell):

```powershell
git clone <THIS_REPO_URL> "$env:USERPROFILE\.cursor\skills\paper-reading-cn"
```

Restart Cursor or start a new Agent chat so the skill is picked up. Confirm `SKILL.md` and `manifest.yaml` sit at the skill root.

---

## Dependencies

| Requirement | Role |
|-------------|------|
| **Python 3** | Script toolchain |
| **pymupdf** | PDF text/layout extraction (`pip install pymupdf`) — see [NOTICE](NOTICE) (AGPL runtime) |
| **Microsoft Edge or Google Chrome** (optional) | Headless HTML → PDF via `render_report.py` |
| **jsonschema** (optional) | Stricter JSON schema checks in verify |

```bash
python -m pip install -r requirements.txt
```

Smoke-test the toolchain (from the skill root):

```bash
python scripts/selftest.py
```

---

## Usage

In Cursor Agent chat, ask for a selective Chinese reading report. Example triggers:

**Chinese**

- 读论文出报告
- 中文精读报告
- 论文重点解读 PDF
- 生成论文阅读 PDF
- 对照代码读论文

**English**

- reading report for this PDF / DOI / arXiv
- selective Chinese PDF reading report
- paper↔code mapping / correspondence

Attach or point to a PDF, DOI, arXiv id/link, or HTML page. Optionally mount a code repo for correspondence. Depth keywords: 简述/brief · 精读/standard (default) · 深读/deep.

---

## Layout

```
paper-reading-cn/
  SKILL.md          # router
  manifest.yaml     # axes + fragment map
  static/           # core + fragments
  scripts/          # extract / crop / repo / verify / render / selftest
  schemas/          # JSON schemas
  references/       # on-demand deep docs
  assets/           # report CSS
  evals/            # skill eval prompts
```

---

## Copyright caution

For copyrighted publisher PDFs, keep chat responses short and point to the local output bundle. Reproduce substantial source text in local artifacts only for user-provided files or clearly lawful open-access content.

---

## License

Copyright (c) 2026 **xym**. Released under the [MIT License](LICENSE).

Third-party runtime notes: [NOTICE](NOTICE).
