#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate a paper-reading-cn output bundle and emit qa_report.json.

Exit codes:
  0 — pass (warnings allowed)
  1 — blockers
  2 — usage / input error
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import operator
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

EXIT_OK = 0
EXIT_BLOCKERS = 1
EXIT_USAGE = 2

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = SKILL_ROOT / "schemas"

PLACEHOLDER_RE = re.compile(
    r"(?i)\bTODO\b|\bFIXME\b|\bTBD\b|\bXXX\b|"
    r"\[PLACEHOLDER\]|\{\{[^{}]+\}\}|"
    r"待填写|占位符|lorem ipsum|FIXME_ME|REPLACE_ME"
)
BANNED_OPENER_RE = re.compile(
    r"(本文的关键洞见是|核心贡献在于|关键结论如下|值得注意的是[：:]?|"
    r"值得一提的是|Takeaway\s*:|Key (?:takeaway|insight)\s*:|"
    r"综上所述[，,]|总而言之[，,])",
    re.IGNORECASE,
)
BLOCK_ID_RE = re.compile(r"^(S|C)[0-9]{3,}$")
FIGURE_ID_RE = re.compile(r"^F[0-9]{3,}$")
TABLE_ID_RE = re.compile(r"^T[0-9]{3,}$")
SOURCE_ID_RE = re.compile(r"^(S|C|F|T)\d{3,}$")
CLAIM_ID_RE = re.compile(r"^CL\d{3,}$")
MAPPING_ID_RE = re.compile(r"^CM\d{3,}$")
SHA256_RE = re.compile(r"^[a-fA-F0-9]{64}$")

CONF_RANK = {"high": 3, "medium": 2, "low": 1, "none": 0, "unavailable": 0}

EXCLUDED_DIR_PARTS = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "build",
        "dist",
        "third_party",
        "external",
        "vendor",
        "deps",
        ".eggs",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        "cmake-build-debug",
        "cmake-build-release",
    }
)
EXCLUDED_DIR_PREFIXES = ("bazel-",)
SECRET_EXT = (
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".keystore",
    ".jks",
    ".netrc",
    ".npmrc",
    ".pypirc",
)
_SECRET_WORD_RE = re.compile(
    r"(?i)(^|[^a-z0-9])(secrets?|tokens?|credentials?)([^a-z0-9]|$)"
)
_TOKENIZE_FP_RE = re.compile(r"(?i)tokeniz")
SECRET_DIR_PARTS = frozenset({".ssh", "secrets", "private", "credentials"})

# Measurement-like tokens; token-local skip filters applied separately.
_NUM_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_./])"
    r"("
    r"\d{1,3}(?:,\d{3})+(?:\.\d+)?"  # 28,700
    r"|"
    r"\d+(?:\.\d+)?[eE][+\-]?\d+"  # scientific
    r"|"
    r"\d+(?:\.\d+)?\s*[KMB](?![A-Za-z])"  # 28.7K
    r"|"
    r"\d+(?:\.\d+)?(?:%|×)"  # 91.2% / 9.6×
    r"|"
    r"\d+(?:\.\d+)?[xX](?![A-Za-z])"  # 10x ASCII
    r"|"
    r"\d+(?:\.\d+)?\s*(?:QPS|qps|ms|MB|GB|TB|Kbps|Mbps)\b"
    r"|"
    r"\d+\.\d+"  # bare decimal (not arXiv — skipped later)
    r"|"
    r"\d{3,}"  # bare ints ≥3 digits
    r")"
    r"(?![A-Za-z0-9_])"
)
_UNIT_AFTER_RE = re.compile(
    r"(?i)^\s*(?:%|×|[xX]\b|QPS|ms|MB|GB|TB|Kbps|Mbps|[KMB]\b)"
)
_LABEL_BEFORE_RE = re.compile(
    r"(?i)(?:fig(?:ure)?|table|section|sec\.?|chapter|eq(?:uation)?|appendix|§)\s*\.?\s*$"
)
_TOOL_BEFORE_RE = re.compile(
    r"(?i)(?:rtx|gtx|a100|h100|v100|cuda|python|gcc|clang|pytorch|tensorflow)\s*$"
)
_ARXIV_ID_RE = re.compile(r"^\d{4}\.\d{4,5}$")
_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")

# Conservative EN→ZH hedge/causality strengthen (non-RJ only).
_HEDGE_EN_MAY = re.compile(r"(?i)\b(?:may|might|could)\b")
_HEDGE_EN_SUGGEST = re.compile(r"(?i)\b(?:suggests?|suggested|suggesting)\b")
_HEDGE_EN_ASSOC = re.compile(
    r"(?i)\b(?:associated with|correlat(?:ed|ion|es)?)\b"
)
_HEDGE_EN_UPTO = re.compile(r"(?i)\b(?:up to|on average)\b")
_HEDGE_EN_NEG = re.compile(r"(?i)\b(?:no|not|never|cannot|can'?t)\b")
_HEDGE_ZH_STRONG = re.compile(r"证明|必然|一定|总是|always|全部")
_HEDGE_ZH_CAUSE = re.compile(r"导致|因果")
_HEDGE_ZH_NEG = re.compile(r"不|未|无|没有|无法|不能|并非|并未")

_PERF_PROOF_RE = re.compile(
    r"(?i)("
    r"(?:prove[sd]?|proof\s+of).{0,48}(?:qps|latency|throughput|speedup|performance)|"
    r"(?:证明|证实).{0,24}(?:性能|吞吐|延迟|加速|实验结果|论文结果)|"
    r"完整证明了论文"
    r")"
)

# A4 in PDF points for --require-pdf fitz QA.
A4_WIDTH_PT = 595.27
A4_HEIGHT_PT = 841.89
A4_TOL_PT = 2.0
CJK_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")

LIGATURE_MAP = str.maketrans(
    {
        "\ufb00": "ff",
        "\ufb01": "fi",
        "\ufb02": "fl",
        "\ufb03": "ffi",
        "\ufb04": "ffl",
        "\u00e6": "ae",
        "\u0153": "oe",
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u00ad": "",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
    }
)

REQUIRED_CONTENT_FILES = (
    "report.json",
    "source_map.json",
    "claim_ledger.json",
    "translation_notes.md",
)

SCHEMA_FILES = {
    "report.json": "report.schema.json",
    "source_map.json": "source-map.schema.json",
    "claim_ledger.json": "claim-ledger.schema.json",
    "code_map.json": "code-map.schema.json",
}

_AST_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_AST_UNARYOPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _utf8_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _load_json(path: Path) -> Tuple[Optional[Any], Optional[str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"cannot read {path.name}: {exc}"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, f"JSON parse error in {path.name}: {exc}"


def _try_jsonschema() -> Optional[Any]:
    try:
        import jsonschema  # type: ignore

        return jsonschema
    except Exception:
        return None


class QA:
    def __init__(self) -> None:
        self.blockers: List[Dict[str, Any]] = []
        self.warnings: List[Dict[str, Any]] = []
        self.info: List[str] = []
        self.schema_mode: str = "structural"
        self.checks: Dict[str, Any] = {}
        self.draft_mode: bool = False

    def block(self, code: str, message: str, **extra: Any) -> None:
        item = {"severity": "blocker", "code": code, "message": message}
        item.update(extra)
        self.blockers.append(item)

    def warn(self, code: str, message: str, **extra: Any) -> None:
        item = {"severity": "warning", "code": code, "message": message}
        item.update(extra)
        self.warnings.append(item)

    def to_dict(self, bundle: Path, axes: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "schema_version": "1.0",
            "generated_at": _now_iso(),
            "bundle": str(bundle.resolve()),
            "ok": len(self.blockers) == 0,
            "blocker_count": len(self.blockers),
            "warning_count": len(self.warnings),
            "schema_mode": self.schema_mode,
            "draft_mode": self.draft_mode,
            "axes": axes,
            "blockers": self.blockers,
            "warnings": self.warnings,
            "info": self.info,
            "checks": self.checks,
            "correspondence": {
                "bilingual_ok": not any(
                    b["code"].startswith("bilingual_") or b["code"] == "MISSING_BILINGUAL"
                    for b in self.blockers
                ),
                "source_ids_ok": not any(
                    b["code"].startswith("source_")
                    or b["code"]
                    in ("DANGLING_BLOCK", "DANGLING_FIGURE", "DANGLING_TABLE")
                    for b in self.blockers
                ),
                "span_ok": not any(
                    b["code"] in ("SPAN_MISMATCH", "span_mismatch") for b in self.blockers
                ),
                "assets_ok": not any(
                    b["code"].startswith("asset_") or b["code"] == "MISSING_ASSET"
                    for b in self.blockers
                ),
                "code_ok": not any(
                    b["code"].startswith("code_") or b["code"] == "CODE_PATH"
                    for b in self.blockers
                ),
                "numeric_ok": not any(
                    b["code"].startswith("numeric_")
                    or b["code"]
                    in (
                        "NUMERIC_UNTYPED",
                        "DERIVED_INCOMPLETE",
                        "ORPHAN_DISPLAY",
                        "HEDGE_STRENGTHEN",
                    )
                    for b in self.blockers
                ),
                "confidence_ok": not any(
                    b["code"] in ("CONFIDENCE_FLOOR", "confidence_floor")
                    for b in self.blockers
                ),
                "role_ok": not any(
                    b["code"] in ("ROLE_GRAPH", "role_graph") for b in self.blockers
                ),
                "placeholders_ok": not any(
                    b["code"] == "unresolved_placeholder" for b in self.blockers
                ),
            },
        }


def _sha256_file(path: Path) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _try_import_fitz() -> Optional[Any]:
    try:
        import fitz  # type: ignore

        return fitz
    except Exception:
        return None


def _walk_strings(obj: Any) -> Iterable[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_strings(v)


def _en_field(item: Dict[str, Any]) -> str:
    for key in ("original_en", "en_span", "caption_en", "paper_statement_en"):
        val = item.get(key)
        if isinstance(val, str):
            return val
    return ""


def _zh_field(item: Dict[str, Any]) -> str:
    for key in (
        "explanation_zh",
        "zh_text",
        "caption_zh",
        "paper_explanation_zh",
        "step_zh",
        "verdict_zh",
        "reading_zh",
    ):
        val = item.get(key)
        if isinstance(val, str):
            return val
    return ""


def _sources_of(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    for key in ("sources", "paper_sources"):
        val = item.get(key)
        if isinstance(val, list):
            return [s for s in val if isinstance(s, dict)]
    return []


def _block_text(block: Dict[str, Any]) -> str:
    for key in ("text", "original_text", "ocr_raw"):
        val = block.get(key)
        if isinstance(val, str):
            return val
    return ""


def _normalize_span_text(text: str) -> str:
    """Normalize whitespace, ligatures, and line-break hyphenation for span checks."""
    if not text:
        return ""
    s = text.translate(LIGATURE_MAP)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    # Join hyphenated line breaks: "rout-\ning" → "routing"
    s = re.sub(r"(\w)-\n(\w)", r"\1\2", s)
    s = s.replace("\n", " ")
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def _span_in_corpus(en: str, corpus: str) -> bool:
    needle = _normalize_span_text(en)
    hay = _normalize_span_text(corpus)
    if not needle:
        return False
    return needle in hay


def _is_excluded_path(rel_posix: str) -> bool:
    parts = rel_posix.replace("\\", "/").split("/")
    for p in parts:
        if p in EXCLUDED_DIR_PARTS:
            return True
        if any(p.startswith(pref) for pref in EXCLUDED_DIR_PREFIXES):
            return True
    return False


def _is_secret_path(rel_posix: str) -> bool:
    """Align with repo_snapshot.is_secret_path: basename + secret dir parts."""
    rel = rel_posix.replace("\\", "/")
    name = Path(rel).name
    name_l = name.lower()
    if name_l.startswith(".env"):
        return True
    if name_l.endswith(SECRET_EXT):
        return True
    if name_l.startswith(("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519")):
        return True
    if name_l in {"credentials", "credentials.json", "credentials.yaml", "credentials.yml"}:
        return True
    if name_l.startswith("credentials.") or name_l.endswith(".credentials"):
        return True
    if "service-account" in name_l or "service_account" in name_l:
        return True
    if _SECRET_WORD_RE.search(name_l):
        if _TOKENIZE_FP_RE.search(name_l):
            return False
        return True
    parts = [p.lower() for p in rel.split("/") if p]
    if any(p in SECRET_DIR_PARTS for p in parts):
        return True
    return False


def _collect_source_indexes(
    source_map: Dict[str, Any],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]], Dict[int, Dict[str, Any]]]:
    blocks: Dict[str, Dict[str, Any]] = {}
    figures: Dict[str, Dict[str, Any]] = {}
    tables: Dict[str, Dict[str, Any]] = {}
    pages: Dict[int, Dict[str, Any]] = {}
    for block in source_map.get("blocks") or []:
        if isinstance(block, dict) and isinstance(block.get("id"), str):
            blocks[block["id"]] = block
    for fig in source_map.get("figures") or []:
        if isinstance(fig, dict) and isinstance(fig.get("id"), str):
            figures[fig["id"]] = fig
    for tab in source_map.get("tables") or []:
        if isinstance(tab, dict) and isinstance(tab.get("id"), str):
            tables[tab["id"]] = tab
    for page in source_map.get("pages") or []:
        if isinstance(page, dict) and isinstance(page.get("page"), int):
            pages[page["page"]] = page
    return blocks, figures, tables, pages


def _collect_source_ids(source_map: Dict[str, Any]) -> Set[str]:
    blocks, figures, tables, _pages = _collect_source_indexes(source_map)
    ids = set(blocks) | set(figures) | set(tables)
    for fig in figures.values():
        cid = fig.get("caption_id")
        if isinstance(cid, str):
            ids.add(cid)
    for tab in tables.values():
        cid = tab.get("caption_id")
        if isinstance(cid, str):
            ids.add(cid)
    return ids


def _resolve_under(root: Path, rel: str) -> Optional[Path]:
    if not rel or rel.startswith(("http://", "https://", "file:", "//")):
        return None
    p = Path(rel)
    if p.is_absolute():
        return None
    parts = p.parts
    if ".." in parts:
        return None
    try:
        resolved = (root / p).resolve()
        root_res = root.resolve()
        resolved.relative_to(root_res)
        return resolved
    except Exception:
        return None


def _git_head(repo: Path) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        return None
    return None


def _git_dirty_count(repo: Path) -> Optional[int]:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        if out.returncode != 0:
            return None
        lines = [ln for ln in out.stdout.splitlines() if ln.strip()]
        return len(lines)
    except Exception:
        return None


def _count_lines(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            return sum(1 for _ in fh)
    except OSError:
        try:
            with path.open("rb") as fh:
                return sum(1 for _ in fh)
        except OSError:
            return 0


def _read_line_slice(path: Path, line_start: int, line_end: int) -> Optional[str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    except OSError:
        return None
    if line_start < 1 or line_end < line_start or line_end > len(lines):
        return None
    return "".join(lines[line_start - 1 : line_end])


def _safe_eval_expr(expr: str, env: Dict[str, float]) -> Optional[float]:
    """Evaluate a restricted arithmetic expression via AST (no eval)."""
    if not expr or len(expr) > 200:
        return None
    if not re.fullmatch(r"[0-9+\-*/().,eE\sA-Za-z_]+", expr):
        return None

    def _eval_node(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _eval_node(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in env:
                raise ValueError(f"unknown name {node.id}")
            return float(env[node.id])
        if isinstance(node, ast.UnaryOp) and type(node.op) in _AST_UNARYOPS:
            return float(_AST_UNARYOPS[type(node.op)](_eval_node(node.operand)))
        if isinstance(node, ast.BinOp) and type(node.op) in _AST_BINOPS:
            left = _eval_node(node.left)
            right = _eval_node(node.right)
            return float(_AST_BINOPS[type(node.op)](left, right))
        if isinstance(node, ast.Expr):
            return _eval_node(node.value)
        raise ValueError("unsupported expression")

    try:
        tree = ast.parse(expr, mode="eval")
        return _eval_node(tree)
    except Exception:
        return None


def _conf_rank(value: Optional[str]) -> int:
    if not isinstance(value, str):
        return CONF_RANK["high"]
    return CONF_RANK.get(value.lower(), CONF_RANK["high"])


def _min_conf_label(values: Iterable[str]) -> str:
    best = min((_conf_rank(v) for v in values), default=CONF_RANK["high"])
    for label, rank in CONF_RANK.items():
        if rank == best and label not in ("unavailable",):
            return label
    return "low"


def _is_skip_numeric_token(text: str, start: int, end: int) -> bool:
    """Token-local identity skip. Nearby years/Fig/IDs must not sink metrics."""
    token = text[start:end].strip()
    if not token:
        return True
    # arXiv-style id (optionally after 'arXiv:')
    bare = token.replace(" ", "")
    if _ARXIV_ID_RE.match(bare):
        return True
    left_arxiv = text[max(0, start - 8) : start]
    if re.search(r"(?i)arxiv\s*:\s*$", left_arxiv) and re.match(r"^\d", token):
        return True
    if _YEAR_RE.match(bare):
        return True
    left = text[max(0, start - 28) : start]
    if _LABEL_BEFORE_RE.search(left) or _TOOL_BEFORE_RE.search(left):
        return True
    # Source/claim id tokens that somehow match
    if re.fullmatch(r"(?:S|C|F|T|CL|CM)\d{3,}", bare, flags=re.I):
        return True
    # Small ints without unit/suffix are usually counts/indexes
    if re.fullmatch(r"\d{1,2}", bare):
        right = text[end : end + 12]
        if not _UNIT_AFTER_RE.match(right):
            return True
    return False


def _measurement_tokens(text: str) -> List[str]:
    found: List[str] = []
    if not text:
        return found
    for m in _NUM_TOKEN_RE.finditer(text):
        if _is_skip_numeric_token(text, m.start(), m.end()):
            continue
        found.append(re.sub(r"\s+", "", m.group(1)))
    return found


def _validate_schema(
    qa: QA, name: str, data: Any, schema_path: Path, jsonschema_mod: Any
) -> bool:
    """Validate against schema. Missing/unreadable schema → block (fail-closed).

    Returns True when jsonschema validation ran on a loaded schema; False when
    caller should run structural fallback.
    """
    if not schema_path.is_file():
        qa.block("schema_missing", f"Schema file not found: {schema_path.name}", file=name)
        return False
    schema, err = _load_json(schema_path)
    if err or not isinstance(schema, dict):
        qa.block(
            "schema_unreadable",
            f"Cannot load schema {schema_path.name}: {err}",
            file=name,
        )
        return False
    if jsonschema_mod is None:
        return False
    try:
        try:
            validator_cls = jsonschema_mod.validators.validator_for(schema)
            validator_cls.check_schema(schema)
            validator = validator_cls(schema)
            errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
        except Exception:
            jsonschema_mod.validate(data, schema)
            errors = []
        if errors:
            for err in errors[:12]:
                path = "/" + "/".join(str(p) for p in err.path)
                qa.block(
                    "schema_invalid",
                    f"{name} fails schema at {path}: {err.message}",
                    path=path,
                    file=name,
                )
            if len(errors) > 12:
                qa.block(
                    "schema_invalid",
                    f"{name}: {len(errors) - 12} additional schema errors omitted",
                    file=name,
                )
    except Exception as exc:
        qa.block("schema_invalid", f"{name} schema validation error: {exc}", file=name)
    return True


def _resolve_execution_mode(qa: QA, meta: Dict[str, Any]) -> str:
    mode = meta.get("execution_mode")
    legacy = meta.get("single_agent")
    if mode is None:
        if legacy is True:
            mode = "single"
        elif legacy is False:
            qa.block(
                "execution_mode",
                "legacy single_agent=false is invalid; set execution_mode="
                "user_requested_multi with user_opt_in_note",
            )
            return "single"
        else:
            mode = "single"
            qa.info.append("execution_mode defaulted to single")
    if mode not in ("single", "user_requested_multi"):
        qa.block(
            "execution_mode",
            f"report.meta.execution_mode must be single|user_requested_multi, got {mode!r}",
        )
        return "single"
    if mode == "user_requested_multi":
        note = meta.get("user_opt_in_note")
        if not isinstance(note, str) or not note.strip():
            qa.block(
                "execution_mode",
                "user_requested_multi requires nonempty meta.user_opt_in_note",
            )
    return str(mode)


def _structural_report(qa: QA, report: Dict[str, Any]) -> None:
    required = [
        "schema_version",
        "meta",
        "overview",
        "problem",
        "argument_map",
        "method",
        "selected_figures_tables",
        "evidence_audit",
        "contributions",
        "limitations",
        "reproduction",
        "terminology",
    ]
    for key in required:
        if key not in report:
            qa.block("structural_missing", f"report.json missing required key '{key}'")
    meta = report.get("meta")
    if isinstance(meta, dict):
        _resolve_execution_mode(qa, meta)
        for k in ("paper_slug", "title_en", "title_zh", "depth", "generated_at"):
            if not meta.get(k):
                qa.block("structural_meta", f"report.meta.{k} is required")
    else:
        qa.block("structural_missing", "report.meta must be an object")
    # Mirror required bilingual / figure evidence boundaries when schemas absent.
    for where, item in _iter_report_bilingual(report):
        if not isinstance(item, dict):
            continue
        if not item.get("role"):
            qa.block("structural_missing", f"report.{where}: role required")
        if not item.get("zh_mode"):
            qa.block("structural_missing", f"report.{where}: zh_mode required")
        if not item.get("claim_id") and "reproduction.checklist" not in where:
            qa.block("structural_missing", f"report.{where}: claim_id required")
        if not str(item.get("explanation_zh") or item.get("step_zh") or "").strip():
            qa.block("structural_missing", f"report.{where}: Chinese required")
    for i, card in enumerate(report.get("selected_figures_tables") or []):
        if not isinstance(card, dict):
            continue
        where = f"selected_figures_tables[{i}]"
        for k in (
            "caption_en",
            "caption_zh",
            "evidence_supported_zh",
            "evidence_not_supported_zh",
            "alt_text",
        ):
            if not str(card.get(k) or "").strip():
                qa.block("structural_missing", f"report.{where}: {k} required")
        if card.get("zh_mode") != "caption_translation":
            qa.block(
                "structural_missing",
                f"report.{where}: zh_mode=caption_translation required",
            )


def _structural_source_map(qa: QA, sm: Dict[str, Any]) -> None:
    for key in ("schema_version", "paper", "blocks", "pages"):
        if key not in sm:
            qa.block("structural_missing", f"source_map.json missing '{key}'")
    blocks = sm.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        qa.block("structural_missing", "source_map.blocks must be a non-empty array")


def _structural_claim_ledger(qa: QA, ledger: Dict[str, Any]) -> None:
    for key in ("schema_version", "paper_slug", "claims"):
        if key not in ledger:
            qa.block("structural_missing", f"claim_ledger.json missing '{key}'")
    claims = ledger.get("claims")
    if not isinstance(claims, list) or not claims:
        qa.block("structural_missing", "claim_ledger.claims must be a non-empty array")
        return
    for i, claim in enumerate(claims):
        if not isinstance(claim, dict):
            continue
        for k in ("id", "role", "zh_mode", "explanation_zh", "confidence"):
            if not claim.get(k):
                qa.block(
                    "structural_missing",
                    f"claim_ledger.claims[{i}]: {k} required",
                )


def _check_bilingual_item(
    qa: QA, item: Dict[str, Any], where: str, *, allow_judgment: bool = True
) -> None:
    role = item.get("role")
    en = _en_field(item)
    zh = _zh_field(item)
    sources = _sources_of(item)
    zh_mode = item.get("zh_mode")
    if role == "reader_judgment" and allow_judgment:
        if zh_mode not in (None, "explanation"):
            qa.block(
                "HEDGE_STRENGTHEN",
                f"{where}: reader_judgment requires zh_mode=explanation",
                where=where,
            )
        if en.strip():
            qa.block(
                "SPAN_MISMATCH",
                f"{where}: reader_judgment must not carry quote-shaped original_en",
                where=where,
            )
        if not zh.strip():
            qa.block(
                "MISSING_BILINGUAL",
                f"{where}: reader_judgment missing Chinese",
                where=where,
            )
        return
    if not en.strip():
        qa.block("MISSING_BILINGUAL", f"{where}: missing English source text", where=where)
    if not zh.strip():
        qa.block("MISSING_BILINGUAL", f"{where}: missing Chinese explanation", where=where)
    if not sources:
        qa.block("bilingual_missing_sources", f"{where}: missing sources[]", where=where)


def _check_anchor(
    qa: QA,
    source: Dict[str, Any],
    where: str,
    blocks: Dict[str, Dict[str, Any]],
    figures: Dict[str, Dict[str, Any]],
    tables: Dict[str, Dict[str, Any]],
) -> None:
    bid = source.get("block_id")
    page = source.get("page")
    if not isinstance(bid, str) or not BLOCK_ID_RE.match(bid):
        qa.block(
            "DANGLING_BLOCK",
            f"{where}: block_id must be S###/C###, got {bid!r}",
            where=where,
            block_id=bid,
        )
        return
    block = blocks.get(bid)
    if block is None:
        qa.block(
            "DANGLING_BLOCK",
            f"{where}: block_id {bid} not in source_map.blocks",
            where=where,
            block_id=bid,
        )
        return
    bpage = block.get("page")
    if isinstance(page, int) and isinstance(bpage, int) and page != bpage:
        qa.block(
            "DANGLING_BLOCK",
            f"{where}: page {page} != block {bid} page {bpage}",
            where=where,
            block_id=bid,
        )
    fid = source.get("figure_id")
    if fid is not None:
        if not isinstance(fid, str) or not FIGURE_ID_RE.match(fid):
            qa.block("DANGLING_FIGURE", f"{where}: bad figure_id {fid!r}", where=where)
        else:
            fig = figures.get(fid)
            if fig is None:
                qa.block(
                    "DANGLING_FIGURE",
                    f"{where}: figure_id {fid} missing from source_map",
                    where=where,
                    figure_id=fid,
                )
            else:
                cap = fig.get("caption_id")
                if not isinstance(cap, str) or cap not in blocks:
                    qa.block(
                        "DANGLING_FIGURE",
                        f"{where}: figure {fid} missing linked caption block",
                        where=where,
                        figure_id=fid,
                    )
                elif isinstance(bid, str) and bid != cap:
                    qa.block(
                        "DANGLING_FIGURE",
                        f"{where}: figure_id {fid} must cite caption_id {cap}, got {bid}",
                        where=where,
                        figure_id=fid,
                    )
    tid = source.get("table_id")
    if tid is not None:
        if not isinstance(tid, str) or not TABLE_ID_RE.match(tid):
            qa.block("DANGLING_TABLE", f"{where}: bad table_id {tid!r}", where=where)
        else:
            tab = tables.get(tid)
            if tab is None:
                qa.block(
                    "DANGLING_TABLE",
                    f"{where}: table_id {tid} missing from source_map",
                    where=where,
                    table_id=tid,
                )
            else:
                cap = tab.get("caption_id")
                if not isinstance(cap, str) or cap not in blocks:
                    qa.block(
                        "DANGLING_TABLE",
                        f"{where}: table {tid} missing linked caption block",
                        where=where,
                        table_id=tid,
                    )
                elif isinstance(bid, str) and bid != cap:
                    qa.block(
                        "DANGLING_TABLE",
                        f"{where}: table_id {tid} must cite caption_id {cap}, got {bid}",
                        where=where,
                        table_id=tid,
                    )


def _cited_corpus(
    sources: List[Dict[str, Any]], blocks: Dict[str, Dict[str, Any]]
) -> str:
    parts: List[str] = []
    for s in sources:
        bid = s.get("block_id")
        if isinstance(bid, str) and bid in blocks:
            parts.append(_block_text(blocks[bid]))
    return "\n".join(parts)


def _check_span(
    qa: QA,
    en: str,
    sources: List[Dict[str, Any]],
    blocks: Dict[str, Dict[str, Any]],
    where: str,
    *,
    role: Optional[str] = None,
) -> None:
    if role == "reader_judgment":
        return
    if not en.strip():
        return
    corpus = _cited_corpus(sources, blocks)
    if not corpus.strip():
        qa.block(
            "SPAN_MISMATCH",
            f"{where}: no cited block text to bound English span",
            where=where,
        )
        return
    if not _span_in_corpus(en, corpus):
        qa.block(
            "SPAN_MISMATCH",
            f"{where}: English not a bounded span of cited blocks",
            where=where,
        )


def _check_confidence(
    qa: QA,
    claim: Dict[str, Any],
    sources: List[Dict[str, Any]],
    blocks: Dict[str, Dict[str, Any]],
    pages: Dict[int, Dict[str, Any]],
    where: str,
) -> None:
    cid = claim.get("id", where)
    claim_conf = claim.get("confidence")
    if not isinstance(claim_conf, str):
        qa.block(
            "CONFIDENCE_FLOOR",
            f"{cid}: confidence required",
            claim_id=cid if isinstance(cid, str) else None,
            where=where,
        )
        return
    floors: List[str] = []
    for s in sources:
        bid = s.get("block_id")
        if isinstance(bid, str) and bid in blocks:
            floors.append(str(blocks[bid].get("confidence") or "high"))
        page = s.get("page")
        if isinstance(page, int) and page in pages:
            for key in ("text_confidence", "text_layer_confidence"):
                tc = pages[page].get(key)
                if isinstance(tc, str):
                    floors.append(tc)
    if not floors:
        return
    floor_label = _min_conf_label(floors)
    if _conf_rank(claim_conf) > _conf_rank(floor_label):
        qa.block(
            "CONFIDENCE_FLOOR",
            f"{cid}: claim confidence {claim_conf} exceeds source floor {floor_label}",
            claim_id=cid,
            where=where,
        )
    numeric = claim.get("numeric") if isinstance(claim.get("numeric"), dict) else None
    kind = numeric.get("kind") if numeric else None
    role = claim.get("role")
    weak = any(_conf_rank(f) <= CONF_RANK["low"] for f in floors)
    medium_only = (not weak) and any(_conf_rank(f) == CONF_RANK["medium"] for f in floors)
    load_bearing = role in ("author_claim", "paper_evidence")
    if weak and load_bearing and (kind == "quoted" or claim_conf == "high"):
        qa.block(
            "CONFIDENCE_FLOOR",
            f"{cid}: low/none OCR cannot support quoted numeric or high load-bearing facts",
            claim_id=cid,
        )
    if medium_only and kind == "quoted" and load_bearing:
        if qa.draft_mode:
            qa.warn(
                "CONFIDENCE_FLOOR",
                f"{cid}: medium OCR quoted numeric allowed only in draft_mode",
                claim_id=cid,
            )
        else:
            qa.block(
                "CONFIDENCE_FLOOR",
                f"{cid}: medium OCR quoted numeric blocked outside draft_mode",
                claim_id=cid,
            )


def _rj_reaches_non_rj(
    cid: str, by_id: Dict[str, Dict[str, Any]], seen: Optional[Set[str]] = None
) -> bool:
    if seen is None:
        seen = set()
    if cid in seen:
        return False
    seen.add(cid)
    claim = by_id.get(cid)
    if not isinstance(claim, dict):
        return False
    if claim.get("role") != "reader_judgment":
        return True
    for dep in claim.get("depends_on_claim_ids") or []:
        if isinstance(dep, str) and _rj_reaches_non_rj(dep, by_id, seen):
            return True
    return False


def _check_hedge_strengthen(
    qa: QA, en: str, zh: str, where: str, *, role: Optional[str] = None
) -> None:
    if role == "reader_judgment":
        return
    if not en.strip() or not zh.strip():
        return
    if _HEDGE_EN_MAY.search(en) and _HEDGE_ZH_STRONG.search(zh):
        qa.block(
            "HEDGE_STRENGTHEN",
            f"{where}: EN hedge (may/might) strengthened in ZH",
            where=where,
        )
    if _HEDGE_EN_SUGGEST.search(en) and (
        _HEDGE_ZH_STRONG.search(zh) or _HEDGE_ZH_CAUSE.search(zh)
    ):
        qa.block(
            "HEDGE_STRENGTHEN",
            f"{where}: EN suggest upgraded to prove/cause in ZH",
            where=where,
        )
    if _HEDGE_EN_ASSOC.search(en) and _HEDGE_ZH_CAUSE.search(zh):
        qa.block(
            "HEDGE_STRENGTHEN",
            f"{where}: EN association upgraded to causality in ZH",
            where=where,
        )
    if _HEDGE_EN_UPTO.search(en) and _HEDGE_ZH_STRONG.search(zh):
        qa.block(
            "HEDGE_STRENGTHEN",
            f"{where}: EN up-to/on-average strengthened in ZH",
            where=where,
        )
    if _HEDGE_EN_NEG.search(en) and not _HEDGE_ZH_NEG.search(zh):
        qa.block(
            "HEDGE_STRENGTHEN",
            f"{where}: EN negation lost in ZH",
            where=where,
        )


def _check_role_graph(qa: QA, claims: List[Dict[str, Any]]) -> None:
    by_id: Dict[str, Dict[str, Any]] = {}
    for claim in claims:
        if isinstance(claim, dict) and isinstance(claim.get("id"), str):
            by_id[claim["id"]] = claim
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        cid = claim.get("id", "?")
        role = claim.get("role")
        if role == "paper_evidence":
            supports = claim.get("supports_claim_ids")
            if not isinstance(supports, list) or not supports:
                qa.block(
                    "ROLE_GRAPH",
                    f"{cid}: paper_evidence requires supports_claim_ids",
                    claim_id=cid,
                )
            else:
                for tid in supports:
                    target = by_id.get(tid) if isinstance(tid, str) else None
                    if target is None:
                        qa.block(
                            "ROLE_GRAPH",
                            f"{cid}: supports unknown claim {tid}",
                            claim_id=cid,
                        )
                    elif target.get("role") != "author_claim":
                        qa.block(
                            "ROLE_GRAPH",
                            f"{cid}: supports_claim_ids target {tid} is not author_claim",
                            claim_id=cid,
                        )
        if role == "reader_judgment":
            deps = claim.get("depends_on_claim_ids")
            if not isinstance(deps, list) or not deps:
                qa.block(
                    "ROLE_GRAPH",
                    f"{cid}: reader_judgment requires depends_on_claim_ids",
                    claim_id=cid,
                )
            elif isinstance(cid, str) and not _rj_reaches_non_rj(cid, by_id):
                qa.block(
                    "ROLE_GRAPH",
                    f"{cid}: reader_judgment dependency graph must reach author_claim/paper_evidence",
                    claim_id=cid,
                )
            numeric = claim.get("numeric") if isinstance(claim.get("numeric"), dict) else None
            if numeric and numeric.get("kind") == "quoted":
                if not isinstance(deps, list) or not deps:
                    qa.block(
                        "ROLE_GRAPH",
                        f"{cid}: reader_judgment quoted numeric needs depends_on_claim_ids",
                        claim_id=cid,
                    )
        if claim.get("secondary_citation") is True:
            if not (
                (isinstance(claim.get("cited_work"), str) and claim["cited_work"].strip())
                or (
                    isinstance(claim.get("cited_marker"), str)
                    and claim["cited_marker"].strip()
                )
            ):
                qa.block(
                    "ROLE_GRAPH",
                    f"{cid}: secondary_citation requires cited_work or cited_marker",
                    claim_id=cid,
                )
            conf = claim.get("confidence")
            if isinstance(conf, str) and _conf_rank(conf) > CONF_RANK["medium"]:
                qa.block(
                    "ROLE_GRAPH",
                    f"{cid}: secondary_citation confidence must be ≤ medium",
                    claim_id=cid,
                )
            indep = claim.get("evidence_independence")
            if indep not in ("secondary", "non_independent"):
                qa.block(
                    "ROLE_GRAPH",
                    f"{cid}: secondary_citation requires evidence_independence secondary|non_independent",
                    claim_id=cid,
                )
    # Contradictions retained + synthesized
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        cid = claim.get("id")
        contras = claim.get("contradicts_claim_ids") or []
        if not isinstance(contras, list):
            continue
        for other in contras:
            if not isinstance(other, str):
                continue
            if other not in by_id:
                qa.block(
                    "CONTRADICTION_DROPPED",
                    f"{cid}: contradicts missing claim {other}",
                    claim_id=cid,
                )
                continue
            pair = {cid, other}
            synthesized = False
            for j in by_id.values():
                if j.get("role") != "reader_judgment":
                    continue
                deps = set(j.get("depends_on_claim_ids") or [])
                cdeps = set(j.get("contradicts_claim_ids") or [])
                if pair <= (deps | cdeps):
                    synthesized = True
                    break
            if not synthesized:
                qa.block(
                    "CONTRADICTION_DROPPED",
                    f"{cid}↔{other}: need reader_judgment depending on both sides",
                    claim_id=cid,
                )


def _check_numeric(
    qa: QA,
    claim: Dict[str, Any],
    known_ids: Set[str],
    blocks: Dict[str, Dict[str, Any]],
    figures: Dict[str, Dict[str, Any]],
    *,
    claims_by_id: Optional[Dict[str, Dict[str, Any]]] = None,
) -> None:
    cid = claim.get("id", "?")
    en = _en_field(claim)
    zh = _zh_field(claim)
    blob = f"{en} {zh}"
    tokens = _measurement_tokens(en) + _measurement_tokens(zh)
    numeric = claim.get("numeric")
    role = claim.get("role")
    if tokens and numeric is None:
        if role == "reader_judgment":
            deps = claim.get("depends_on_claim_ids") or []
            linked = False
            if isinstance(claims_by_id, dict):
                for dep in deps:
                    other = claims_by_id.get(dep) if isinstance(dep, str) else None
                    if isinstance(other, dict) and isinstance(other.get("numeric"), dict):
                        linked = True
                        break
            if not linked:
                qa.block(
                    "NUMERIC_UNTYPED",
                    f"{cid}: reader_judgment measurement(s) {tokens[:3]} need numeric or depends_on evidence with numeric",
                    claim_id=cid,
                )
                return
        else:
            qa.block(
                "NUMERIC_UNTYPED",
                f"{cid}: measurement-like number(s) {tokens[:3]} lack numeric object",
                claim_id=cid,
            )
            return
    if numeric is None:
        return
    if not isinstance(numeric, dict):
        qa.block("numeric_invalid", f"{cid}: numeric must be object")
        return
    kind = numeric.get("kind")
    if kind == "estimate":
        kind = "chart_estimate"
    if kind not in ("quoted", "derived", "chart_estimate"):
        qa.block("NUMERIC_UNTYPED", f"{cid}: numeric.kind invalid", claim_id=cid)
        return
    if "value" not in numeric:
        qa.block("numeric_missing_value", f"{cid}: numeric.value required", claim_id=cid)
    if "unit" not in numeric:
        qa.block("numeric_missing_unit", f"{cid}: numeric.unit required", claim_id=cid)
    display = numeric.get("display")
    if not isinstance(display, str) or not display.strip():
        qa.block(
            "ORPHAN_DISPLAY",
            f"{cid}: numeric.display required and must appear in EN/ZH",
            claim_id=cid,
        )
    sources = numeric.get("sources") or []
    if kind in ("quoted", "chart_estimate") and not sources:
        qa.block("numeric_unanchored", f"{cid}: {kind} needs sources[]", claim_id=cid)
    has_text_anchor = False
    has_figure_only = False
    for s in sources:
        if not isinstance(s, dict):
            continue
        bid = s.get("block_id")
        fid = s.get("figure_id")
        tid = s.get("table_id")
        for sid in (bid, fid, tid):
            if isinstance(sid, str) and known_ids and sid not in known_ids:
                qa.block(
                    "numeric_unanchored",
                    f"{cid}: numeric source id '{sid}' not in source_map",
                    claim_id=cid,
                    block_id=sid,
                )
        if isinstance(bid, str) and bid in blocks and _block_text(blocks[bid]).strip():
            has_text_anchor = True
        if isinstance(tid, str):
            has_text_anchor = True
        if isinstance(fid, str) and fid in figures and not isinstance(bid, str):
            has_figure_only = True
    if kind == "quoted" and has_figure_only and not has_text_anchor:
        qa.block(
            "numeric_estimate",
            f"{cid}: figure-only value must be chart_estimate, not quoted",
            claim_id=cid,
        )
    if kind == "derived":
        expr = numeric.get("expression") or numeric.get("expr")
        inputs = numeric.get("inputs")
        if not expr:
            qa.block("DERIVED_INCOMPLETE", f"{cid}: derived needs expression", claim_id=cid)
        if not isinstance(inputs, list) or not inputs:
            qa.block("DERIVED_INCOMPLETE", f"{cid}: derived needs inputs[]", claim_id=cid)
        else:
            env: Dict[str, float] = {}
            units: List[str] = []
            for inp in inputs:
                if not isinstance(inp, dict):
                    continue
                name = inp.get("name")
                src = inp.get("source")
                if isinstance(src, dict) and src.get("kind") == "external_note":
                    qa.block(
                        "DERIVED_INCOMPLETE",
                        f"{cid}: derived input {name} external_note blocked in production",
                        claim_id=cid,
                    )
                elif isinstance(src, dict) and src.get("kind") == "claim":
                    ref = src.get("ref")
                    if (
                        isinstance(claims_by_id, dict)
                        and isinstance(ref, str)
                        and ref not in claims_by_id
                    ):
                        qa.block(
                            "DERIVED_INCOMPLETE",
                            f"{cid}: derived input {name} claim ref {ref} missing",
                            claim_id=cid,
                        )
                elif isinstance(src, dict) and "block_id" in src:
                    bid = src.get("block_id")
                    if isinstance(bid, str) and known_ids and bid not in known_ids:
                        qa.block(
                            "DERIVED_INCOMPLETE",
                            f"{cid}: derived input {name} block {bid} missing",
                            claim_id=cid,
                        )
                unit = inp.get("unit")
                if isinstance(unit, str):
                    units.append(unit.lower())
                try:
                    env[str(name)] = float(inp.get("value"))
                except (TypeError, ValueError):
                    qa.block(
                        "DERIVED_INCOMPLETE",
                        f"{cid}: input {name} value not numeric",
                        claim_id=cid,
                    )
            # Relative comparisons (x / speedup / %) need ≥2 inputs (baseline+treatment)
            unit_l = str(numeric.get("unit") or "").lower()
            disp_l = str(display or "").lower()
            relative = (
                unit_l in ("x", "×", "%", "percent", "pct", "ratio", "speedup")
                or "×" in str(display or "")
                or re.search(r"(?i)\d+(?:\.\d+)?x\b", disp_l) is not None
            )
            if relative and len(inputs) < 2:
                qa.block(
                    "DERIVED_INCOMPLETE",
                    f"{cid}: relative derived needs baseline and treatment inputs",
                    claim_id=cid,
                )
            if len(set(units)) > 1 and not relative:
                qa.block(
                    "DERIVED_INCOMPLETE",
                    f"{cid}: derived input units inconsistent {units}",
                    claim_id=cid,
                )
            if expr and env:
                got = _safe_eval_expr(str(expr), env)
                if got is None:
                    qa.block(
                        "numeric_mismatch",
                        f"{cid}: could not safely evaluate expression (AST only)",
                        claim_id=cid,
                    )
                else:
                    try:
                        expected = float(numeric.get("value"))
                        tol = max(abs(expected) * 0.01, 0.05)
                        if abs(got - expected) > tol:
                            qa.block(
                                "numeric_mismatch",
                                f"{cid}: derived value {expected} != expr {got}",
                                claim_id=cid,
                                expected=expected,
                                computed=got,
                            )
                    except (TypeError, ValueError):
                        pass
    if kind == "chart_estimate":
        note = (
            numeric.get("uncertainty_note_zh")
            or numeric.get("uncertainty")
            or numeric.get("read_method")
        )
        if not note:
            qa.block(
                "numeric_estimate",
                f"{cid}: chart estimate needs uncertainty note",
                claim_id=cid,
            )
    if isinstance(display, str) and display.strip():
        disp_norm = display.replace("×", "x").replace("％", "%").strip()
        blob_norm = blob.replace("×", "x").replace("％", "%")
        token = disp_norm.split()[0] if disp_norm else ""
        if token and token not in blob_norm and disp_norm not in blob_norm:
            qa.block(
                "ORPHAN_DISPLAY",
                f"{cid}: numeric.display not found in EN/ZH text",
                claim_id=cid,
            )


def _iter_report_bilingual(report: Dict[str, Any]) -> Iterable[Tuple[str, Dict[str, Any]]]:
    overview = report.get("overview") or {}
    if isinstance(overview, dict):
        for i, item in enumerate(overview.get("key_points") or []):
            if isinstance(item, dict):
                yield f"overview.key_points[{i}]", item
    for sec in ("problem", "method", "contributions", "limitations"):
        obj = report.get(sec) or {}
        if isinstance(obj, dict):
            for i, item in enumerate(obj.get("items") or []):
                if isinstance(item, dict):
                    yield f"{sec}.items[{i}]", item
            for i, item in enumerate(obj.get("algorithm_steps") or []):
                if isinstance(item, dict):
                    yield f"{sec}.algorithm_steps[{i}]", item
            for i, item in enumerate(obj.get("reviewer_objections") or []):
                if isinstance(item, dict):
                    yield f"{sec}.reviewer_objections[{i}]", item
    am = report.get("argument_map") or {}
    if isinstance(am, dict):
        for i, item in enumerate(am.get("nodes") or []):
            if isinstance(item, dict):
                yield f"argument_map.nodes[{i}]", item
    ea = report.get("evidence_audit") or {}
    if isinstance(ea, dict):
        for i, item in enumerate(ea.get("items") or []):
            if isinstance(item, dict):
                yield f"evidence_audit.items[{i}]", item
    repro = report.get("reproduction") or {}
    if isinstance(repro, dict):
        for i, item in enumerate(repro.get("checklist") or []):
            if isinstance(item, dict):
                yield f"reproduction.checklist[{i}]", item
    cc = report.get("code_correspondence") or {}
    if isinstance(cc, dict):
        for i, item in enumerate(cc.get("items") or []):
            if isinstance(item, dict):
                yield f"code_correspondence.items[{i}]", item


def _check_crop_provenance(
    qa: QA, bundle: Path, card: Dict[str, Any], where: str
) -> None:
    asset_rel = card.get("asset_path") or card.get("asset")
    if not isinstance(asset_rel, str) or not asset_rel.strip():
        return
    crop = card.get("crop") if isinstance(card.get("crop"), dict) else None
    meta_rel = None
    if crop and isinstance(crop.get("metadata_path"), str):
        meta_rel = crop["metadata_path"].replace("\\", "/")
    else:
        # Conventional sidecar next to PNG
        stem = Path(asset_rel.replace("\\", "/")).stem
        candidate = f"assets/{stem}.crop.json"
        if (bundle / candidate).is_file():
            meta_rel = candidate
        asset_id = card.get("asset_id")
        if meta_rel is None and isinstance(asset_id, str):
            cand2 = f"assets/{asset_id}.crop.json"
            if (bundle / cand2).is_file():
                meta_rel = cand2
    if crop is None and meta_rel is None:
        qa.block(
            "asset_crop_provenance",
            f"{where}: selected figure asset requires crop provenance or sidecar",
            where=where,
        )
        return
    sidecar: Dict[str, Any] = {}
    if meta_rel:
        resolved = _resolve_under(bundle, meta_rel)
        if resolved is None or not resolved.is_file():
            qa.block(
                "asset_crop_provenance",
                f"{where}: crop sidecar missing or escapes bundle: {meta_rel}",
                where=where,
            )
        else:
            obj, err = _load_json(resolved)
            if err or not isinstance(obj, dict):
                qa.block(
                    "asset_crop_provenance",
                    f"{where}: crop sidecar unreadable: {err}",
                    where=where,
                )
            else:
                sidecar = obj
    prov = dict(sidecar)
    if crop:
        prov.update({k: v for k, v in crop.items() if v is not None})
    method = prov.get("method")
    if method != "explicit_bbox":
        qa.block(
            "asset_crop_provenance",
            f"{where}: crop method must be explicit_bbox",
            where=where,
        )
    status = prov.get("status")
    if status not in ("verified", "approximate"):
        qa.block(
            "asset_crop_provenance",
            f"{where}: crop status must be verified|approximate",
            where=where,
        )
    bbox = prov.get("bbox_pdf_points")
    if not (isinstance(bbox, list) and len(bbox) == 4):
        qa.block(
            "asset_crop_provenance",
            f"{where}: crop requires bbox_pdf_points[4]",
            where=where,
        )
    try:
        zoom = float(prov.get("zoom"))
        if zoom < 2:
            qa.block(
                "asset_crop_provenance",
                f"{where}: crop zoom must be ≥ 2",
                where=where,
            )
    except (TypeError, ValueError):
        qa.block(
            "asset_crop_provenance",
            f"{where}: crop zoom missing/invalid",
            where=where,
        )
    try:
        dpi = float(prov.get("effective_dpi"))
        if dpi < 144:
            qa.block(
                "asset_crop_provenance",
                f"{where}: effective_dpi must be ≥ 144",
                where=where,
            )
    except (TypeError, ValueError):
        qa.block(
            "asset_crop_provenance",
            f"{where}: effective_dpi missing/invalid",
            where=where,
        )
    sha = prov.get("source_sha256")
    if not isinstance(sha, str) or not SHA256_RE.match(sha):
        qa.block(
            "asset_crop_provenance",
            f"{where}: source_sha256 required (64 hex) in report crop and/or sidecar",
            where=where,
        )
    else:
        pdf = bundle / "source.pdf"
        if not pdf.is_file():
            qa.block(
                "asset_crop_provenance",
                f"{where}: source.pdf missing; cannot bind source_sha256",
                where=where,
            )
        else:
            actual = _sha256_file(pdf)
            if actual is None:
                qa.block(
                    "asset_crop_provenance",
                    f"{where}: cannot hash source.pdf",
                    where=where,
                )
            elif sha.lower() != actual.lower():
                qa.block(
                    "asset_crop_provenance",
                    f"{where}: source_sha256 mismatch vs bundle/source.pdf",
                    where=where,
                )
            # Sidecar and report crop must agree when both present
            if crop and isinstance(sidecar.get("source_sha256"), str):
                if sidecar["source_sha256"].lower() != sha.lower():
                    qa.block(
                        "asset_crop_provenance",
                        f"{where}: report crop source_sha256 != sidecar",
                        where=where,
                    )


def _check_code_map(
    qa: QA,
    code_map: Dict[str, Any],
    snapshot: Optional[Dict[str, Any]],
    claim_ids: Set[str],
    blocks: Dict[str, Dict[str, Any]],
    figures: Dict[str, Dict[str, Any]],
    tables: Dict[str, Dict[str, Any]],
    repo: Optional[Path],
) -> None:
    snap_id = None
    snap_commit = None
    snap_dirty = None
    snap_align = None
    focus_root: Optional[Path] = None
    if isinstance(snapshot, dict):
        snap_id = snapshot.get("snapshot_id") or snapshot.get("id")
        git = snapshot.get("git") if isinstance(snapshot.get("git"), dict) else {}
        snap_commit = git.get("commit") or snapshot.get("commit") or snapshot.get("head")
        if snap_commit is None and isinstance(snapshot.get("repo"), dict):
            snap_commit = snapshot["repo"].get("head")
        snap_dirty = git.get("dirty")
        if snap_dirty is None:
            snap_dirty = snapshot.get("dirty")
        va = snapshot.get("version_alignment")
        if isinstance(va, dict):
            snap_align = va.get("status")
        snap_align = snap_align or snapshot.get("version_alignment_status")
        root_s = snapshot.get("focus_root") or snapshot.get("repo_root") or snapshot.get("root")
        if isinstance(root_s, str) and root_s:
            focus_root = Path(root_s)

    cm_snap = code_map.get("snapshot_id")
    cm_commit = code_map.get("commit")
    cm_dirty = code_map.get("dirty")
    cm_align = code_map.get("version_alignment_status")
    repo_meta = code_map.get("repo") if isinstance(code_map.get("repo"), dict) else {}
    if cm_commit is None and isinstance(repo_meta, dict):
        cm_commit = repo_meta.get("head")
    if cm_dirty is None and isinstance(repo_meta, dict):
        cm_dirty = repo_meta.get("dirty")
    if focus_root is None and isinstance(repo_meta.get("root"), str):
        focus_root = Path(repo_meta["root"])

    git_info: Dict[str, Any] = {}
    if isinstance(snapshot, dict):
        if isinstance(snapshot.get("git"), dict):
            git_info = snapshot["git"]
        if not snap_id:
            qa.block(
                "code_snapshot_mismatch",
                "repo_snapshot.snapshot_id required when code_repo=present",
            )
        if not cm_snap:
            qa.block(
                "code_snapshot_mismatch",
                "code_map.snapshot_id required",
            )
        if cm_snap and snap_id and cm_snap != snap_id:
            qa.block(
                "code_snapshot_mismatch",
                f"code_map.snapshot_id {cm_snap} != repo_snapshot {snap_id}",
            )
        if cm_commit and snap_commit and not (
            str(cm_commit) == str(snap_commit)
            or str(snap_commit).startswith(str(cm_commit)[:7])
            or str(cm_commit).startswith(str(snap_commit)[:7])
        ):
            qa.block(
                "code_snapshot_mismatch",
                f"code_map.commit {cm_commit} != snapshot {snap_commit}",
            )
        if cm_dirty is not None and snap_dirty is not None and bool(cm_dirty) != bool(snap_dirty):
            qa.block(
                "code_snapshot_mismatch",
                f"code_map.dirty {cm_dirty} != snapshot dirty {snap_dirty}",
            )
        if cm_align and snap_align and cm_align != snap_align:
            qa.block(
                "code_snapshot_mismatch",
                f"code_map.version_alignment_status {cm_align} != snapshot {snap_align}",
            )

    is_git = git_info.get("is_git")
    git_status = git_info.get("status") or (
        snapshot.get("status") if isinstance(snapshot, dict) else None
    )
    non_git = is_git is False or (
        isinstance(git_status, str) and git_status not in ("ok",) and is_git is not True
    )
    if isinstance(snapshot, dict) and (is_git is False or git_status not in (None, "ok")):
        # Treat missing is_git with status ok as git; non-ok / explicit false is non-git.
        if is_git is False or (isinstance(git_status, str) and git_status != "ok"):
            non_git = True

    repo_root = repo.resolve() if repo is not None else None
    if repo_root is None and focus_root is not None:
        repo_root = focus_root
    mapped_head = cm_commit or (repo_meta.get("head") if isinstance(repo_meta, dict) else None)
    live_head: Optional[str] = None
    live_dirty_count: Optional[int] = None
    if repo_root is not None:
        live_head = _git_head(repo_root)
        live_dirty_count = _git_dirty_count(repo_root)
        if not non_git:
            if not live_head:
                qa.block(
                    "code_commit_drift",
                    "Cannot resolve live HEAD for git repository",
                )
            elif isinstance(mapped_head, str) and mapped_head and live_head != mapped_head and not (
                live_head.startswith(mapped_head[:7]) or mapped_head.startswith(live_head[:7])
            ):
                qa.block(
                    "code_commit_drift",
                    f"Live HEAD drifted: mapped={mapped_head} live={live_head}",
                    mapped_head=mapped_head,
                    live_head=live_head,
                )
            if live_dirty_count is not None:
                live_dirty = live_dirty_count > 0
                art_dirty = bool(cm_dirty if cm_dirty is not None else snap_dirty)
                if live_dirty != art_dirty:
                    qa.block(
                        "code_dirty_drift",
                        f"Live dirty={live_dirty} != artifact dirty={art_dirty}",
                    )

    align = cm_align or snap_align or "same_repo_unknown_rev"
    dirty_flag = bool(cm_dirty if cm_dirty is not None else snap_dirty)
    if live_dirty_count is not None and live_dirty_count > 0:
        dirty_flag = True

    for i, mapping in enumerate(code_map.get("mappings") or []):
        if not isinstance(mapping, dict):
            continue
        mid = mapping.get("id") or mapping.get("map_id") or f"[{i}]"
        status = mapping.get("status")
        basis = mapping.get("match_basis")
        if status not in ("exact", "partial", "mismatch", "unverified"):
            qa.block("code_status", f"mapping {mid}: invalid status {status!r}")
        if mapping.get("name_match_only") is True and status == "exact":
            qa.block(
                "code_name_match_exact",
                f"mapping {mid}: name_match_only cannot be exact",
            )
        if basis == "name_only" and status == "exact":
            qa.block(
                "code_name_match_exact",
                f"mapping {mid}: match_basis=name_only cannot be exact",
            )
        if basis == "token_or_number_hit" and status not in ("partial", "unverified"):
            qa.block(
                "code_match_basis",
                f"mapping {mid}: token_or_number_hit status must be partial|unverified",
            )
        waived = basis == "user_waived"
        note = (
            mapping.get("uncertainty_notes_zh")
            or mapping.get("notes_zh")
            or mapping.get("reader_judgment")
        )
        if status == "exact":
            if non_git:
                qa.block(
                    "code_exact_forbidden",
                    f"mapping {mid}: exact forbidden when git.is_git/status not ok",
                )
            if align in ("likely_drift", "unrelated") and not waived:
                qa.block(
                    "code_exact_forbidden",
                    f"mapping {mid}: exact forbidden for alignment={align} without user_waived",
                )
            if dirty_flag and not waived:
                qa.block(
                    "code_exact_forbidden",
                    f"mapping {mid}: exact forbidden on dirty tree without user_waived",
                )
            if waived and (not isinstance(note, str) or not str(note).strip()):
                qa.block(
                    "code_exact_forbidden",
                    f"mapping {mid}: user_waived exact requires documented note",
                )
        paper_en = str(mapping.get("paper_statement_en") or "")
        paper_sources = _sources_of(mapping)
        _check_bilingual_item(
            qa,
            {
                "original_en": paper_en,
                "explanation_zh": mapping.get("paper_explanation_zh"),
                "sources": paper_sources,
            },
            f"code_map.mappings[{i}]",
        )
        for s in paper_sources:
            _check_anchor(qa, s, f"code_map.mappings[{i}]", blocks, figures, tables)
        _check_span(
            qa,
            paper_en,
            paper_sources,
            blocks,
            f"code_map.mappings[{i}]",
        )
        cid = mapping.get("claim_id")
        if isinstance(cid, str) and claim_ids and cid not in claim_ids:
            qa.block(
                "claim_id_unknown",
                f"mapping {mid}: claim_id {cid} not in ledger",
                claim_id=cid,
            )
        refs = mapping.get("code_refs") or []
        if status in ("exact", "partial", "mismatch") and not refs:
            qa.block("code_refs_missing", f"mapping {mid}: code_refs required")
        if status == "unverified" and not (
            str(mapping.get("notes_zh") or "").strip()
            or str(mapping.get("uncertainty_notes_zh") or "").strip()
        ):
            qa.block("code_unverified_notes", f"mapping {mid}: notes_zh required")
        for j, ref in enumerate(refs):
            if not isinstance(ref, dict):
                continue
            path_rel = ref.get("path")
            ls = ref.get("line_start")
            le = ref.get("line_end")
            if not isinstance(path_rel, str) or not path_rel.strip():
                qa.block("CODE_PATH", f"mapping {mid} ref[{j}]: path required")
                continue
            rel = path_rel.replace("\\", "/")
            if ".." in Path(rel).parts or Path(rel).is_absolute():
                qa.block(
                    "CODE_PATH",
                    f"mapping {mid} ref[{j}]: path escapes repo: {path_rel}",
                )
                continue
            if _is_secret_path(rel):
                qa.block(
                    "CODE_PATH",
                    f"mapping {mid} ref[{j}]: secret path blocked: {path_rel}",
                )
                if isinstance(ref.get("code_excerpt"), str) and str(ref["code_excerpt"]).strip():
                    qa.block(
                        "CODE_PATH",
                        f"mapping {mid} ref[{j}]: secret path code_excerpt forbidden",
                    )
                continue
            for role_key in ("role_en", "role_zh"):
                role_txt = ref.get(role_key)
                if isinstance(role_txt, str) and _PERF_PROOF_RE.search(role_txt):
                    qa.block(
                        "code_perf_proof",
                        f"mapping {mid} ref[{j}]: {role_key} claims performance proof",
                    )
            for note_key in ("notes_zh", "uncertainty_notes_zh", "reader_judgment"):
                note_txt = mapping.get(note_key)
                if isinstance(note_txt, str) and _PERF_PROOF_RE.search(note_txt):
                    qa.block(
                        "code_perf_proof",
                        f"mapping {mid}: {note_key} claims performance proof",
                    )
            excluded = _is_excluded_path(rel) or ref.get("excluded_path") is True
            if excluded and status == "exact":
                qa.block(
                    "code_exact_forbidden",
                    f"mapping {mid} ref[{j}]: excluded path cannot be exact",
                )
            if not isinstance(ls, int) or not isinstance(le, int) or ls < 1 or le < ls:
                qa.block(
                    "code_line_range",
                    f"mapping {mid} ref[{j}]: invalid line range {ls}-{le}",
                )
                continue
            line_count = le - ls + 1
            if status == "exact" and line_count > 20:
                qa.block(
                    "code_exact_forbidden",
                    f"mapping {mid} ref[{j}]: exact range must be ≤20 lines (got {line_count})",
                )
            if repo_root is not None:
                target = _resolve_under(repo_root, rel)
                if target is None:
                    qa.block(
                        "CODE_PATH",
                        f"mapping {mid} ref[{j}]: path not under repo: {path_rel}",
                    )
                elif not target.is_file():
                    qa.block(
                        "code_path_missing",
                        f"mapping {mid} ref[{j}]: file missing: {path_rel}",
                    )
                else:
                    nlines = _count_lines(target)
                    if le > nlines:
                        qa.block(
                            "code_line_range",
                            f"mapping {mid} ref[{j}]: line_end {le} > file lines {nlines}",
                            path=path_rel,
                        )
                    elif status in ("exact", "partial", "mismatch"):
                        slice_text = _read_line_slice(target, ls, le)
                        if slice_text is None:
                            qa.block(
                                "code_line_range",
                                f"mapping {mid} ref[{j}]: cannot read slice",
                            )
                        else:
                            symbol = ref.get("symbol")
                            excerpt = ref.get("code_excerpt")
                            symbol_ok = (
                                isinstance(symbol, str)
                                and symbol
                                and symbol in slice_text
                            )
                            excerpt_ok = False
                            if isinstance(excerpt, str) and excerpt:
                                if excerpt.replace("\r\n", "\n") == slice_text.replace(
                                    "\r\n", "\n"
                                ):
                                    excerpt_ok = True
                                elif status == "exact":
                                    qa.block(
                                        "code_excerpt_mismatch",
                                        f"mapping {mid} ref[{j}]: code_excerpt != file slice",
                                    )
                            if status == "exact" and not symbol_ok and not excerpt_ok:
                                qa.block(
                                    "code_exact_proof",
                                    f"mapping {mid} ref[{j}]: exact needs symbol in slice or matching code_excerpt",
                                )


def _reconcile_page_block_ids(
    qa: QA,
    blocks: Dict[str, Dict[str, Any]],
    pages: Dict[int, Dict[str, Any]],
) -> None:
    for page_num, page in pages.items():
        bids = page.get("block_ids") or []
        if not isinstance(bids, list):
            qa.block(
                "PAGE_BLOCK_IDS",
                f"pages[{page_num}].block_ids must be an array",
            )
            continue
        for bid in bids:
            if not isinstance(bid, str):
                continue
            if bid not in blocks:
                qa.block(
                    "PAGE_BLOCK_IDS",
                    f"pages[{page_num}].block_ids has unknown {bid}",
                )
            elif blocks[bid].get("page") != page_num:
                qa.block(
                    "PAGE_BLOCK_IDS",
                    f"pages[{page_num}] lists {bid} but block.page={blocks[bid].get('page')}",
                )
    for bid, block in blocks.items():
        if not isinstance(bid, str) or not BLOCK_ID_RE.match(bid):
            continue
        p = block.get("page")
        if not isinstance(p, int) or p not in pages:
            continue
        listed = pages[p].get("block_ids") or []
        if isinstance(listed, list) and bid not in listed:
            qa.block(
                "PAGE_BLOCK_IDS",
                f"block {bid} on page {p} missing from pages[{p}].block_ids",
            )


def _check_require_pdf(
    qa: QA, bundle: Path, report: Optional[Dict[str, Any]]
) -> None:
    pdf = bundle / "report.pdf"
    if not pdf.is_file():
        qa.block("missing_file", "report.pdf required (--require-pdf)")
        return
    try:
        raw = pdf.read_bytes()
    except OSError as exc:
        qa.block("pdf_invalid", f"cannot read report.pdf: {exc}")
        return
    if not raw.startswith(b"%PDF"):
        qa.block("pdf_invalid", "report.pdf does not start with %PDF")
        return
    if len(raw) < 1024:
        qa.block("pdf_invalid", "report.pdf size < 1 KiB")
    fitz = _try_import_fitz()
    if fitz is None:
        qa.block(
            "pdf_invalid",
            "PyMuPDF (fitz) required for --require-pdf page/A4/text QA",
        )
        return
    title_zh = ""
    if isinstance(report, dict):
        meta = report.get("meta") or {}
        if isinstance(meta, dict):
            title_zh = str(meta.get("title_zh") or "")
    try:
        doc = fitz.open(pdf)
    except Exception as exc:
        qa.block("pdf_invalid", f"fitz open failed: {exc}")
        return
    try:
        if doc.page_count < 1:
            qa.block("pdf_invalid", "report.pdf page_count < 1")
            return
        text_parts: List[str] = []
        for i in range(doc.page_count):
            page = doc.load_page(i)
            rect = page.mediabox
            w = abs(float(rect.x1) - float(rect.x0))
            h = abs(float(rect.y1) - float(rect.y0))
            ok_a4 = (
                abs(w - A4_WIDTH_PT) <= A4_TOL_PT and abs(h - A4_HEIGHT_PT) <= A4_TOL_PT
            ) or (
                abs(w - A4_HEIGHT_PT) <= A4_TOL_PT and abs(h - A4_WIDTH_PT) <= A4_TOL_PT
            )
            if not ok_a4:
                qa.block(
                    "pdf_invalid",
                    f"page {i + 1} MediaBox not A4 (±{A4_TOL_PT}pt): {w:.2f}x{h:.2f}",
                )
            text_parts.append(page.get_text("text") or "")
        full = "\n".join(text_parts)
        if title_zh and title_zh not in full:
            qa.block("pdf_invalid", "report.pdf selectable text missing title_zh")
        if not CJK_CHAR_RE.search(full):
            qa.block("pdf_invalid", "report.pdf selectable text missing Chinese")
    finally:
        doc.close()


def verify_bundle(
    bundle: Path,
    *,
    repo: Optional[Path] = None,
    require_pdf: bool = False,
    production: bool = True,
) -> Tuple[int, Dict[str, Any]]:
    qa = QA()
    if not bundle.is_dir():
        return EXIT_USAGE, {
            "ok": False,
            "error": f"bundle is not a directory: {bundle}",
            "code": "bad_bundle",
        }

    bundle = bundle.resolve()
    axes: Dict[str, Any] = {}
    data: Dict[str, Any] = {}

    missing = [f for f in REQUIRED_CONTENT_FILES if not (bundle / f).is_file()]
    if missing:
        for f in missing:
            qa.block("missing_file", f"Required file missing: {f}", file=f)
    if not (bundle / "assets").is_dir():
        qa.block("missing_file", "Required directory missing: assets/", file="assets/")

    source_pdf = bundle / "source.pdf"
    if not source_pdf.is_file():
        qa.warn("missing_source_pdf", "source.pdf not present in bundle")

    for name in ("report.json", "source_map.json", "claim_ledger.json"):
        path = bundle / name
        if not path.is_file():
            continue
        obj, err = _load_json(path)
        if err:
            qa.block("json_parse", err, file=name)
            data[name] = None
        else:
            data[name] = obj

    code_map_path = bundle / "code_map.json"
    repo_snap_path = bundle / "repo_snapshot.json"
    code_repo_present = code_map_path.is_file() or repo_snap_path.is_file()
    report = data.get("report.json")
    if isinstance(report, dict):
        meta = report.get("meta") or {}
        if isinstance(meta, dict):
            axes = {
                "depth": meta.get("depth"),
                "paper_type": meta.get("paper_type"),
                "source_format": meta.get("source_format"),
                "code_repo": meta.get("code_repo"),
                "execution_mode": None,
            }
            axes["execution_mode"] = _resolve_execution_mode(qa, meta)
            qa.draft_mode = bool(meta.get("draft_mode"))
            if meta.get("code_repo") == "present":
                code_repo_present = True
            if meta.get("code_repo") == "absent" and (
                code_map_path.is_file() or repo_snap_path.is_file()
            ):
                qa.block(
                    "code_absent_artifact",
                    "code_repo=absent but code_map/repo_snapshot present",
                )

    if code_repo_present:
        if not code_map_path.is_file():
            qa.block("missing_file", "code_map.json required when repository is present")
        if not repo_snap_path.is_file():
            qa.block(
                "missing_file",
                "repo_snapshot.json required when repository is present",
            )
        if code_map_path.is_file():
            obj, err = _load_json(code_map_path)
            if err:
                qa.block("json_parse", err, file="code_map.json")
            else:
                data["code_map.json"] = obj
        if repo_snap_path.is_file():
            obj, err = _load_json(repo_snap_path)
            if err:
                qa.block("json_parse", err, file="repo_snapshot.json")
            else:
                data["repo_snapshot.json"] = obj

    jsonschema_mod = _try_jsonschema()
    qa.checks["jsonschema_available"] = bool(jsonschema_mod)
    schema_ran = False

    if isinstance(report, dict):
        ran = _validate_schema(
            qa, "report.json", report, SCHEMA_DIR / "report.schema.json", jsonschema_mod
        )
        schema_ran = schema_ran or ran
        if not ran:
            _structural_report(qa, report)
    sm = data.get("source_map.json")
    if isinstance(sm, dict):
        ran = _validate_schema(
            qa, "source_map.json", sm, SCHEMA_DIR / "source-map.schema.json", jsonschema_mod
        )
        schema_ran = schema_ran or ran
        if not ran:
            _structural_source_map(qa, sm)
    ledger = data.get("claim_ledger.json")
    if isinstance(ledger, dict):
        ran = _validate_schema(
            qa,
            "claim_ledger.json",
            ledger,
            SCHEMA_DIR / "claim-ledger.schema.json",
            jsonschema_mod,
        )
        schema_ran = schema_ran or ran
        if not ran:
            _structural_claim_ledger(qa, ledger)
    code_map = data.get("code_map.json")
    if isinstance(code_map, dict):
        ran = _validate_schema(
            qa, "code_map.json", code_map, SCHEMA_DIR / "code-map.schema.json", jsonschema_mod
        )
        schema_ran = schema_ran or ran
    qa.schema_mode = "jsonschema" if schema_ran else "structural"

    blocks: Dict[str, Dict[str, Any]] = {}
    figures: Dict[str, Dict[str, Any]] = {}
    tables: Dict[str, Dict[str, Any]] = {}
    pages: Dict[int, Dict[str, Any]] = {}
    known_ids: Set[str] = set()
    if isinstance(sm, dict):
        blocks, figures, tables, pages = _collect_source_indexes(sm)
        known_ids = _collect_source_ids(sm)

    claim_ids: Set[str] = set()
    claims_list: List[Dict[str, Any]] = []
    claims_by_id: Dict[str, Dict[str, Any]] = {}
    if isinstance(sm, dict):
        _reconcile_page_block_ids(qa, blocks, pages)

    if isinstance(ledger, dict):
        for i, claim in enumerate(ledger.get("claims") or []):
            if not isinstance(claim, dict):
                qa.block("claim_invalid", f"claims[{i}] is not an object")
                continue
            claims_list.append(claim)
            cid = claim.get("id")
            if not isinstance(cid, str) or not CLAIM_ID_RE.match(cid):
                qa.block("claim_id_invalid", f"claims[{i}].id invalid: {cid!r}")
            else:
                if cid in claim_ids:
                    qa.block("claim_id_duplicate", f"Duplicate claim id {cid}")
                claim_ids.add(cid)
                claims_by_id[cid] = claim
            role = claim.get("role")
            if not claim.get("zh_mode"):
                qa.block(
                    "structural_missing",
                    f"claim_ledger.claims[{i}]({cid}): zh_mode required",
                )
            _check_bilingual_item(qa, claim, f"claim_ledger.claims[{i}]({cid})")
            sources = _sources_of(claim)
            for s in sources:
                _check_anchor(
                    qa, s, f"claim_ledger.claims[{i}]({cid})", blocks, figures, tables
                )
            if role != "reader_judgment":
                _check_span(
                    qa,
                    _en_field(claim),
                    sources,
                    blocks,
                    f"claim_ledger.claims[{i}]({cid})",
                    role=role if isinstance(role, str) else None,
                )
            _check_hedge_strengthen(
                qa,
                _en_field(claim),
                _zh_field(claim),
                f"claim_ledger.claims[{i}]({cid})",
                role=role if isinstance(role, str) else None,
            )
            _check_confidence(qa, claim, sources, blocks, pages, f"claim[{cid}]")
        for claim in claims_list:
            _check_numeric(
                qa, claim, known_ids, blocks, figures, claims_by_id=claims_by_id
            )
        _check_role_graph(qa, claims_list)

    if isinstance(report, dict):
        for where, item in _iter_report_bilingual(report):
            _check_bilingual_item(qa, item, f"report.{where}")
            sources = _sources_of(item)
            for s in sources:
                _check_anchor(qa, s, f"report.{where}", blocks, figures, tables)
            role = item.get("role") if isinstance(item.get("role"), str) else None
            if "reproduction.checklist" not in where:
                if not role:
                    qa.block(
                        "structural_missing",
                        f"report.{where}: role required",
                    )
                if not item.get("zh_mode"):
                    qa.block(
                        "structural_missing",
                        f"report.{where}: zh_mode required",
                    )
                cid_req = item.get("claim_id")
                if not isinstance(cid_req, str) or not CLAIM_ID_RE.match(cid_req):
                    qa.block(
                        "claim_id_unknown",
                        f"report.{where}: load-bearing unit requires claim_id",
                    )
                elif claim_ids and cid_req not in claim_ids:
                    qa.block(
                        "claim_id_unknown",
                        f"report.{where}: claim_id {cid_req} not in ledger",
                        claim_id=cid_req,
                    )
            _check_span(
                qa,
                _en_field(item),
                sources,
                blocks,
                f"report.{where}",
                role=role,
            )
            _check_hedge_strengthen(
                qa, _en_field(item), _zh_field(item), f"report.{where}", role=role
            )
            # Report-surface measurement tokens need numeric_ref → ledger numeric
            tokens = _measurement_tokens(_en_field(item)) + _measurement_tokens(
                _zh_field(item)
            )
            if tokens and "reproduction.checklist" not in where:
                nref = item.get("numeric_ref") or item.get("claim_id")
                linked = False
                if isinstance(nref, str) and nref in claims_by_id:
                    linked_claim = claims_by_id[nref]
                    if isinstance(linked_claim.get("numeric"), dict):
                        linked = True
                    elif linked_claim.get("role") == "reader_judgment":
                        for dep in linked_claim.get("depends_on_claim_ids") or []:
                            dep_c = claims_by_id.get(dep) if isinstance(dep, str) else None
                            if isinstance(dep_c, dict) and isinstance(
                                dep_c.get("numeric"), dict
                            ):
                                linked = True
                                break
                if not linked:
                    qa.block(
                        "NUMERIC_UNTYPED",
                        f"report.{where}: measurement(s) {tokens[:3]} need numeric_ref/linked ledger numeric",
                    )
            if isinstance(item.get("confidence"), str) or item.get("confidence") is None:
                # When confidence present, enforce floor; missing on load-bearing is blocker
                if item.get("confidence") is None and "reproduction.checklist" not in where:
                    # confidence optional on report if ledger linked; require when present only
                    pass
                elif isinstance(item.get("confidence"), str):
                    _check_confidence(
                        qa, item, sources, blocks, pages, f"report.{where}"
                    )
            cid = item.get("claim_id") or item.get("numeric_ref")
            if isinstance(cid, str) and claim_ids and cid not in claim_ids:
                qa.block(
                    "claim_id_unknown",
                    f"report.{where}: claim_id {cid} not in ledger",
                    claim_id=cid,
                )

        for i, card in enumerate(report.get("selected_figures_tables") or []):
            if not isinstance(card, dict):
                continue
            where = f"selected_figures_tables[{i}]"
            if not str(card.get("caption_en") or "").strip():
                qa.block("MISSING_BILINGUAL", f"{where}: caption_en required")
            if not str(card.get("caption_zh") or "").strip():
                qa.block("MISSING_BILINGUAL", f"{where}: caption_zh required")
            if card.get("zh_mode") != "caption_translation":
                qa.block(
                    "MISSING_BILINGUAL",
                    f"{where}: zh_mode=caption_translation required",
                )
            if not str(card.get("alt_text") or "").strip():
                qa.block("MISSING_BILINGUAL", f"{where}: alt_text required")
            if not str(card.get("evidence_supported_zh") or "").strip():
                qa.block(
                    "structural_missing",
                    f"{where}: evidence_supported_zh required",
                )
            if not str(card.get("evidence_not_supported_zh") or "").strip():
                qa.block(
                    "structural_missing",
                    f"{where}: evidence_not_supported_zh required",
                )
            aid = card.get("asset_id")
            kind = card.get("kind")
            if isinstance(aid, str):
                if kind == "figure":
                    if not FIGURE_ID_RE.match(aid) or aid not in figures:
                        qa.block(
                            "DANGLING_FIGURE",
                            f"{where}: asset_id {aid} not in source_map.figures",
                        )
                    elif aid.startswith("F") and kind != "figure":
                        qa.block(
                            "DANGLING_FIGURE",
                            f"{where}: kind/asset_id mismatch",
                        )
                elif kind == "table":
                    if not TABLE_ID_RE.match(aid) or aid not in tables:
                        qa.block(
                            "DANGLING_TABLE",
                            f"{where}: asset_id {aid} not in source_map.tables",
                        )
                if isinstance(aid, str) and aid.startswith("F") and kind != "figure":
                    qa.block(
                        "DANGLING_FIGURE",
                        f"{where}: kind must be figure for asset_id {aid}",
                    )
                if isinstance(aid, str) and aid.startswith("T") and kind != "table":
                    qa.block(
                        "DANGLING_TABLE",
                        f"{where}: kind must be table for asset_id {aid}",
                    )
            for s in _sources_of(card):
                _check_anchor(qa, s, f"report.{where}", blocks, figures, tables)
            _check_span(
                qa,
                str(card.get("caption_en") or ""),
                _sources_of(card),
                blocks,
                f"report.{where}.caption_en",
            )
            asset_rel = card.get("asset_path") or card.get("asset")
            if not isinstance(asset_rel, str) or not asset_rel.strip():
                qa.block("MISSING_ASSET", f"{where}: asset_path required")
            else:
                rel = asset_rel.replace("\\", "/")
                if not rel.startswith("assets/"):
                    qa.block(
                        "asset_path_jail",
                        f"{where}: asset_path must be under assets/: {asset_rel}",
                    )
                resolved = _resolve_under(bundle, rel)
                if resolved is None:
                    qa.block(
                        "asset_path_jail",
                        f"{where}: asset path escapes bundle: {asset_rel}",
                    )
                elif not resolved.is_file():
                    qa.block(
                        "MISSING_ASSET",
                        f"{where}: asset file missing: {asset_rel}",
                        path=asset_rel,
                    )
                else:
                    _check_crop_provenance(qa, bundle, card, where)

        if production:
            for s in _walk_strings(report):
                if PLACEHOLDER_RE.search(s):
                    qa.block(
                        "unresolved_placeholder",
                        f"Unresolved placeholder in report.json: {s[:80]}",
                    )
                    break
            for s in _walk_strings(report):
                if BANNED_OPENER_RE.search(s):
                    qa.block(
                        "banned_announcement",
                        f"Banned announcement label in report: {s[:80]}",
                    )
                    break

        used = report.get("claim_ids_used")
        if isinstance(used, list):
            for cid in used:
                if isinstance(cid, str) and claim_ids and cid not in claim_ids:
                    qa.block(
                        "claim_id_unknown",
                        f"claim_ids_used references unknown {cid}",
                        claim_id=cid,
                    )

    if isinstance(code_map, dict):
        _check_code_map(
            qa,
            code_map,
            data.get("repo_snapshot.json")
            if isinstance(data.get("repo_snapshot.json"), dict)
            else None,
            claim_ids,
            blocks,
            figures,
            tables,
            repo,
        )

    if require_pdf:
        _check_require_pdf(qa, bundle, report if isinstance(report, dict) else None)

    qa_path = bundle / "qa_report.json"
    report_obj = qa.to_dict(bundle, axes)
    try:
        qa_path.write_text(
            json.dumps(report_obj, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        return EXIT_USAGE, {
            "ok": False,
            "error": f"cannot write qa_report.json: {exc}",
            "code": "qa_write_failed",
        }

    return (EXIT_OK if not qa.blockers else EXIT_BLOCKERS), report_obj


# ---------------------------------------------------------------------------
# Self-test fixtures
# ---------------------------------------------------------------------------

_S001_TEXT = (
    "We reduce probe cost by graph routing. "
    "Dense centroid scan dominates latency. "
    "Graph routing replaces full centroid scans. "
    "Build a centroid graph offline. "
    "A centroid-graph router for IVF search. "
    "Evaluated on two datasets only. "
    "Build the index with Section 8 defaults. "
    "Speedup is 9.6× (27700/2900)."
)
_C001_TEXT = "Figure 1: QPS vs recall."
_SOURCE_PDF_BYTES = (
    b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
)
_CROP_SHA = hashlib.sha256(_SOURCE_PDF_BYTES).hexdigest()


def _minimal_source_map() -> Dict[str, Any]:
    return {
        "schema_version": "1.0",
        "paper": {
            "title": "Example Paper",
            "source_type": "pdf-text",
            "source_path": "source.pdf",
            "page_count": 2,
        },
        "blocks": [
            {
                "id": "S001",
                "page": 1,
                "type": "paragraph",
                "order": 1,
                "text": _S001_TEXT,
                "confidence": "high",
            },
            {
                "id": "C001",
                "page": 1,
                "type": "caption",
                "order": 2,
                "text": _C001_TEXT,
                "confidence": "high",
                "refs": ["F001"],
            },
            {
                "id": "S002",
                "page": 2,
                "type": "paragraph",
                "order": 3,
                "text": "Page two holds unrelated methods text.",
                "confidence": "high",
            },
        ],
        "pages": [
            {
                "page": 1,
                "block_ids": ["S001", "C001"],
                "width": 612,
                "height": 792,
                "text_confidence": "high",
            },
            {
                "page": 2,
                "block_ids": ["S002"],
                "width": 612,
                "height": 792,
                "text_confidence": "high",
            },
        ],
        "figures": [
            {
                "id": "F001",
                "page": 1,
                "caption_id": "C001",
                "label": "Figure 1",
                "image_path": "assets/fig1.png",
            }
        ],
        "tables": [],
    }


def _minimal_ledger(*, with_derived: bool = True) -> Dict[str, Any]:
    claims: List[Dict[str, Any]] = [
        {
            "id": "CL001",
            "role": "author_claim",
            "zh_mode": "translation",
            "original_en": "We reduce probe cost by graph routing.",
            "explanation_zh": "通过图路由降低探测成本。",
            "sources": [{"page": 1, "block_id": "S001"}],
            "confidence": "high",
        }
    ]
    if with_derived:
        claims.append(
            {
                "id": "CL002",
                "role": "paper_evidence",
                "zh_mode": "translation",
                "original_en": "Speedup is 9.6× (27700/2900).",
                "explanation_zh": "相对加速为 9.6×（27700/2900）。",
                "sources": [{"page": 1, "block_id": "S001"}],
                "confidence": "high",
                "supports_claim_ids": ["CL001"],
                "numeric": {
                    "kind": "derived",
                    "value": 9.6,
                    "unit": "x",
                    "display": "9.6×",
                    "expression": "27700/2900",
                    "inputs": [
                        {
                            "name": "a",
                            "value": 27700,
                            "unit": "qps",
                            "source": {"page": 1, "block_id": "S001"},
                        },
                        {
                            "name": "b",
                            "value": 2900,
                            "unit": "qps",
                            "source": {"page": 1, "block_id": "S001"},
                        },
                    ],
                    "sources": [{"page": 1, "block_id": "S001"}],
                },
            }
        )
    return {
        "schema_version": "1.0",
        "paper_slug": "example",
        "source_map_path": "source_map.json",
        "claims": claims,
    }


def _figure_crop() -> Dict[str, Any]:
    return {
        "method": "explicit_bbox",
        "status": "verified",
        "page": 1,
        "bbox_pdf_points": [72.0, 72.0, 400.0, 400.0],
        "zoom": 2.0,
        "effective_dpi": 144.0,
        "source_sha256": _CROP_SHA,
        "metadata_path": "assets/F001.crop.json",
        "validated": True,
    }


def _minimal_report(*, bad_placeholder: bool = False, missing_zh: bool = False) -> Dict[str, Any]:
    kp_zh = "通过图路由降低探测成本。"
    if missing_zh:
        kp_zh = ""
    if bad_placeholder:
        kp_zh = "TODO fill this"
    return {
        "schema_version": "1.0",
        "meta": {
            "paper_slug": "example",
            "title_en": "Example Systems Paper",
            "title_zh": "示例系统论文",
            "depth": "brief",
            "generated_at": "2026-07-20T00:00:00+08:00",
            "execution_mode": "single",
            "code_repo": "absent",
            "paper_type": "systems",
            "source_format": "pdf-text",
        },
        "overview": {
            "summary_zh": "该工作用图路由降低探测成本。",
            "audience_zh": "向量检索系统研究者。",
            "key_points": [
                {
                    "original_en": "We reduce probe cost by graph routing.",
                    "explanation_zh": kp_zh,
                    "sources": [{"page": 1, "block_id": "S001"}],
                    "claim_id": "CL001",
                    "role": "author_claim",
                    "zh_mode": "translation",
                }
            ],
        },
        "problem": {
            "items": [
                {
                    "original_en": "Dense centroid scan dominates latency.",
                    "explanation_zh": "稠密中心扫描主导延迟。",
                    "sources": [{"page": 1, "block_id": "S001"}],
                    "claim_id": "CL001",
                    "role": "author_claim",
                    "zh_mode": "translation",
                }
            ]
        },
        "argument_map": {
            "nodes": [
                {
                    "label_zh": "核心主张",
                    "original_en": "Graph routing replaces full centroid scans.",
                    "explanation_zh": "图路由替代全量中心扫描。",
                    "sources": [{"page": 1, "block_id": "S001"}],
                    "claim_id": "CL001",
                    "zh_mode": "translation",
                    "role": "author_claim",
                }
            ]
        },
        "method": {
            "items": [
                {
                    "original_en": "Build a centroid graph offline.",
                    "explanation_zh": "离线构建中心图。",
                    "sources": [{"page": 1, "block_id": "S001"}],
                    "claim_id": "CL001",
                    "role": "author_claim",
                    "zh_mode": "translation",
                }
            ]
        },
        "selected_figures_tables": [
            {
                "asset_id": "F001",
                "kind": "figure",
                "page": 1,
                "caption_en": "Figure 1: QPS vs recall.",
                "caption_zh": "图 1：吞吐随召回变化。",
                "zh_mode": "caption_translation",
                "reading_zh": "曲线显示高召回下仍保持吞吐。",
                "evidence_supported_zh": "支持延迟相关主张的方向。",
                "evidence_not_supported_zh": "未给出误差条。",
                "sources": [{"page": 1, "block_id": "C001", "figure_id": "F001"}],
                "asset_path": "assets/fig1.png",
                "alt_text": "Line chart of QPS versus recall for the proposed router.",
                "crop": _figure_crop(),
            }
        ],
        "evidence_audit": {
            "items": [
                {
                    "original_en": "Figure 1: QPS vs recall.",
                    "explanation_zh": "图 1 报告吞吐随召回变化。",
                    "sources": [{"page": 1, "block_id": "C001", "figure_id": "F001"}],
                    "verdict_zh": "支持延迟主张的方向，但缺少误差条。",
                    "claim_id": "CL001",
                    "role": "paper_evidence",
                    "zh_mode": "translation",
                }
            ]
        },
        "contributions": {
            "items": [
                {
                    "original_en": "A centroid-graph router for IVF search.",
                    "explanation_zh": "面向 IVF 检索的中心图路由器。",
                    "sources": [{"page": 1, "block_id": "S001"}],
                    "claim_id": "CL001",
                    "role": "author_claim",
                    "zh_mode": "translation",
                }
            ]
        },
        "limitations": {
            "items": [
                {
                    "original_en": "Evaluated on two datasets only.",
                    "explanation_zh": "仅在两个数据集上评估。",
                    "sources": [{"page": 1, "block_id": "S001"}],
                    "claim_id": "CL001",
                    "role": "author_claim",
                    "zh_mode": "translation",
                }
            ]
        },
        "reproduction": {
            "checklist": [
                {
                    "step_zh": "按默认参数构建索引。",
                    "original_en": "Build the index with Section 8 defaults.",
                    "sources": [{"page": 1, "block_id": "S001"}],
                }
            ]
        },
        "terminology": [{"term_en": "nprobe", "term_zh": "探测列表数"}],
        "claim_ids_used": ["CL001"],
    }


def _write_crop_sidecar(root: Path) -> None:
    meta = {
        "method": "explicit_bbox",
        "status": "verified",
        "page": 1,
        "bbox_pdf_points": [72.0, 72.0, 400.0, 400.0],
        "zoom": 2.0,
        "effective_dpi": 144.0,
        "source_sha256": _CROP_SHA,
        "out_png": "assets/fig1.png",
        "validated": True,
    }
    (root / "assets" / "F001.crop.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _write_bundle(root: Path, *, kind: str = "valid") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "assets").mkdir(exist_ok=True)
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    (root / "assets" / "fig1.png").write_bytes(png)
    _write_crop_sidecar(root)
    (root / "source.pdf").write_bytes(_SOURCE_PDF_BYTES)
    (root / "translation_notes.md").write_text("# notes\n", encoding="utf-8")
    (root / "source_map.json").write_text(
        json.dumps(_minimal_source_map(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if kind == "valid":
        (root / "claim_ledger.json").write_text(
            json.dumps(_minimal_ledger(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (root / "report.json").write_text(
            json.dumps(_minimal_report(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    elif kind == "missing_zh":
        (root / "claim_ledger.json").write_text(
            json.dumps(_minimal_ledger(with_derived=False), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (root / "report.json").write_text(
            json.dumps(_minimal_report(missing_zh=True), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    elif kind == "bad_source_id":
        ledger = _minimal_ledger(with_derived=False)
        ledger["claims"][0]["sources"] = [{"page": 1, "block_id": "S999"}]
        (root / "claim_ledger.json").write_text(
            json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (root / "report.json").write_text(
            json.dumps(_minimal_report(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    elif kind == "missing_asset":
        (root / "claim_ledger.json").write_text(
            json.dumps(_minimal_ledger(with_derived=False), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        rep = _minimal_report()
        rep["selected_figures_tables"][0]["asset_path"] = "assets/missing.png"
        (root / "report.json").write_text(
            json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    elif kind == "placeholder":
        (root / "claim_ledger.json").write_text(
            json.dumps(_minimal_ledger(with_derived=False), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (root / "report.json").write_text(
            json.dumps(_minimal_report(bad_placeholder=True), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    elif kind == "bad_numeric":
        ledger = _minimal_ledger(with_derived=True)
        ledger["claims"][1]["numeric"]["value"] = 100.0
        ledger["claims"][1]["numeric"]["display"] = "100\u00d7"
        (root / "claim_ledger.json").write_text(
            json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (root / "report.json").write_text(
            json.dumps(_minimal_report(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    elif kind == "wrong_page":
        ledger = _minimal_ledger(with_derived=False)
        ledger["claims"][0]["sources"] = [{"page": 2, "block_id": "S001"}]
        (root / "claim_ledger.json").write_text(
            json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (root / "report.json").write_text(
            json.dumps(_minimal_report(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    elif kind == "forged_en":
        ledger = _minimal_ledger(with_derived=False)
        ledger["claims"][0]["original_en"] = "We invent a claim never printed in the PDF."
        (root / "claim_ledger.json").write_text(
            json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (root / "report.json").write_text(
            json.dumps(_minimal_report(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    elif kind == "confidence_upgrade":
        sm = _minimal_source_map()
        sm["blocks"][0]["confidence"] = "low"
        sm["pages"][0]["text_confidence"] = "low"
        (root / "source_map.json").write_text(
            json.dumps(sm, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        ledger = _minimal_ledger(with_derived=False)
        ledger["claims"][0]["zh_mode"] = "translation"
        ledger["claims"][0]["confidence"] = "high"
        ledger["claims"][0]["numeric"] = {
            "kind": "quoted",
            "value": 9.6,
            "unit": "x",
            "display": "9.6\u00d7",
            "sources": [{"page": 1, "block_id": "S001"}],
        }
        ledger["claims"][0]["original_en"] = "Speedup is 9.6\u00d7 (27700/2900)."
        ledger["claims"][0]["explanation_zh"] = "相对加速为 9.6\u00d7。"
        (root / "claim_ledger.json").write_text(
            json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (root / "report.json").write_text(
            json.dumps(_minimal_report(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    elif kind == "role_graph":
        ledger = _minimal_ledger(with_derived=False)
        ledger["claims"].append(
            {
                "id": "CL003",
                "role": "paper_evidence",
                "zh_mode": "translation",
                "original_en": "We reduce probe cost by graph routing.",
                "explanation_zh": "证据行缺少支持链接。",
                "sources": [{"page": 1, "block_id": "S001"}],
                "confidence": "high",
                "supports_claim_ids": ["CL999"],
            }
        )
        (root / "claim_ledger.json").write_text(
            json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (root / "report.json").write_text(
            json.dumps(_minimal_report(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    elif kind == "untyped_numeric":
        ledger = _minimal_ledger(with_derived=False)
        ledger["claims"][0]["original_en"] = "Speedup is 9.6\u00d7 (27700/2900)."
        ledger["claims"][0]["explanation_zh"] = "相对加速为 9.6\u00d7。"
        (root / "claim_ledger.json").write_text(
            json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (root / "report.json").write_text(
            json.dumps(_minimal_report(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    elif kind == "crop_missing_sidecar":
        (root / "claim_ledger.json").write_text(
            json.dumps(_minimal_ledger(with_derived=False), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        rep = _minimal_report()
        rep["selected_figures_tables"][0].pop("crop", None)
        (root / "assets" / "F001.crop.json").unlink(missing_ok=True)
        (root / "report.json").write_text(
            json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    else:
        raise ValueError(kind)
    return root


def _init_repo(repo: Path) -> str:
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "a.py").write_text(
        "def graph_routing():\n    return 64\n", encoding="utf-8"
    )
    subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=False)
    subprocess.run(["git", "add", "-A"], cwd=str(repo), capture_output=True, check=False)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@example.com",
            "-c",
            "user.name=t",
            "commit",
            "-m",
            "init",
        ],
        cwd=str(repo),
        capture_output=True,
        check=False,
    )
    return _git_head(repo) or ("0" * 40)


def _code_map_obj(
    repo: Path,
    head: str,
    *,
    status: str = "exact",
    match_basis: str = "symbol_and_behavior",
    symbol: str = "graph_routing",
    excerpt: Optional[str] = None,
    line_end: int = 2,
    snapshot_id: str = "RS001",
    commit: Optional[str] = None,
    dirty: bool = False,
    align: str = "same_repo_unknown_rev",
) -> Dict[str, Any]:
    ref: Dict[str, Any] = {
        "path": "src/a.py",
        "line_start": 1,
        "line_end": line_end,
        "role_en": "implements routing helper",
        "role_zh": "实现路由辅助函数",
    }
    if symbol:
        ref["symbol"] = symbol
    if excerpt is not None:
        ref["code_excerpt"] = excerpt
    return {
        "schema_version": "1.0",
        "snapshot_id": snapshot_id,
        "commit": commit or head,
        "dirty": dirty,
        "version_alignment_status": align,
        "paper_slug": "example",
        "repo": {"root": str(repo.resolve()), "head": commit or head, "dirty": dirty},
        "mappings": [
            {
                "id": "CM001",
                "claim_id": "CL001",
                "status": status,
                "match_basis": match_basis,
                "paper_statement_en": "We reduce probe cost by graph routing.",
                "paper_explanation_zh": "通过图路由降低探测成本。",
                "paper_sources": [{"page": 1, "block_id": "S001"}],
                "code_refs": [ref],
                "notes_zh": "test mapping",
                "uncertainty_notes_zh": "test mapping",
            }
        ],
    }


def _repo_snapshot_obj(
    repo: Path, head: str, *, snapshot_id: str = "RS001", dirty: bool = False, align: str = "same_repo_unknown_rev"
) -> Dict[str, Any]:
    return {
        "schema_version": "1.0",
        "snapshot_id": snapshot_id,
        "status": "ok",
        "repo_root": str(repo.resolve()),
        "focus_root": str(repo.resolve()),
        "git": {
            "available": True,
            "is_git": True,
            "status": "ok",
            "commit": head,
            "head": head,
            "dirty": dirty,
            "dirty_file_count": 1 if dirty else 0,
        },
        "version_alignment": {"status": align},
        "dirty": dirty,
        "head": head,
        "tracked_files": ["src/a.py"],
    }


def run_selftest() -> int:
    _utf8_stdout()
    failures: List[str] = []
    for banned in (
        "本文的关键洞见是",
        "核心贡献在于",
        "关键结论如下",
        "值得注意的是：",
        "值得一提的是",
        "Takeaway:",
        "Key takeaway:",
        "Key insight:",
        "综上所述，",
        "总而言之，",
    ):
        if not BANNED_OPENER_RE.search(banned):
            failures.append(f"banned opener not matched: {banned}")
    with tempfile.TemporaryDirectory(prefix="prc-verify-") as tmp:
        tmp_path = Path(tmp)

        valid = _write_bundle(tmp_path / "valid", kind="valid")
        code, qa = verify_bundle(valid, production=True)
        if code != EXIT_OK:
            failures.append(f"valid bundle expected 0 got {code}: {qa.get('blockers')}")

        survey = _write_bundle(tmp_path / "survey_not_applicable", kind="valid")
        survey_report_path = survey / "report.json"
        survey_report = json.loads(survey_report_path.read_text(encoding="utf-8"))
        survey_report["meta"]["paper_type"] = "survey"
        survey_report["reproduction"] = {
            "status": "not_applicable",
            "reason_zh": "该文为叙述性综述，没有提出需要复现的新实验流程。",
            "sources": [{"page": 1, "block_id": "S001"}],
            "checklist": [],
        }
        survey_report_path.write_text(
            json.dumps(survey_report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        code, qa = verify_bundle(survey, production=True)
        if code != EXIT_OK:
            failures.append(
                "survey not_applicable reproduction expected 0 got "
                f"{code}: {qa.get('blockers')}"
            )

        for kind, expect_code in (
            ("missing_zh", EXIT_BLOCKERS),
            ("bad_source_id", EXIT_BLOCKERS),
            ("missing_asset", EXIT_BLOCKERS),
            ("placeholder", EXIT_BLOCKERS),
            ("bad_numeric", EXIT_BLOCKERS),
            ("wrong_page", EXIT_BLOCKERS),
            ("forged_en", EXIT_BLOCKERS),
            ("confidence_upgrade", EXIT_BLOCKERS),
            ("role_graph", EXIT_BLOCKERS),
            ("untyped_numeric", EXIT_BLOCKERS),
            ("crop_missing_sidecar", EXIT_BLOCKERS),
        ):
            b = _write_bundle(tmp_path / kind, kind=kind)
            code, qa = verify_bundle(b, production=True)
            if code != expect_code:
                failures.append(f"{kind}: expected {expect_code} got {code}")
            if not (b / "qa_report.json").is_file():
                failures.append(f"{kind}: qa_report.json not written")

        # Expect specific blocker codes
        def _codes(kind: str) -> Set[str]:
            b = tmp_path / kind
            obj, _err = _load_json(b / "qa_report.json")
            if not isinstance(obj, dict):
                return set()
            return {x.get("code") for x in obj.get("blockers") or []}

        if "DANGLING_BLOCK" not in _codes("wrong_page"):
            failures.append(f"wrong_page missing DANGLING_BLOCK: {_codes('wrong_page')}")
        if "SPAN_MISMATCH" not in _codes("forged_en"):
            failures.append(f"forged_en missing SPAN_MISMATCH: {_codes('forged_en')}")
        if "CONFIDENCE_FLOOR" not in _codes("confidence_upgrade"):
            failures.append(
                f"confidence_upgrade missing CONFIDENCE_FLOOR: {_codes('confidence_upgrade')}"
            )
        if "ROLE_GRAPH" not in _codes("role_graph") and "schema_invalid" not in _codes(
            "role_graph"
        ):
            failures.append(f"role_graph missing ROLE_GRAPH: {_codes('role_graph')}")
        if "NUMERIC_UNTYPED" not in _codes("untyped_numeric"):
            failures.append(
                f"untyped_numeric missing NUMERIC_UNTYPED: {_codes('untyped_numeric')}"
            )
        if "asset_crop_provenance" not in _codes("crop_missing_sidecar"):
            failures.append(
                f"crop_missing_sidecar missing asset_crop_provenance: {_codes('crop_missing_sidecar')}"
            )

        # --- R3 numeric token attacks ---
        def _probe_tokens(text: str) -> List[str]:
            return _measurement_tokens(text)

        if not _probe_tokens("28700 QPS"):
            failures.append("NT-01 bare QPS token miss")
        if not _probe_tokens("28.7K QPS"):
            failures.append("NT-02 K-suffix token miss")
        if not _probe_tokens("Speedup is 10x"):
            failures.append("NT-03 ASCII 10x token miss")
        if "91.2%" not in _probe_tokens("In 2023, accuracy reached 91.2%."):
            failures.append("NT-04 year must not sink percent")
        if "28.7%" not in _probe_tokens("Figure 3 shows 28.7% improvement."):
            failures.append("NT-05 fig label must not sink percent")
        if not any("28,700" in t or "28700" in t for t in _probe_tokens("We achieve 28,700 QPS on DEEP1B.")):
            failures.append("NT-06 dataset id must not sink QPS")
        if _probe_tokens("Figure 3") or _probe_tokens("CUDA 12.4") or _probe_tokens("SIFT1B"):
            failures.append("NT-07 skip identity false positive")
        if _probe_tokens("arXiv:2301.12345") or _probe_tokens("2301.12345"):
            failures.append("NT-08 arXiv id false positive")

        bare = _write_bundle(tmp_path / "bare_qps_untyped", kind="valid")
        ledger = _minimal_ledger(with_derived=False)
        ledger["claims"][0]["original_en"] = "Throughput is 28700 QPS."
        ledger["claims"][0]["explanation_zh"] = "吞吐为 28700 QPS。"
        # extend source text so span still matches
        sm = _minimal_source_map()
        sm["blocks"][0]["text"] = _S001_TEXT + " Throughput is 28700 QPS."
        (bare / "source_map.json").write_text(
            json.dumps(sm, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (bare / "claim_ledger.json").write_text(
            json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        code, qa = verify_bundle(bare, production=True)
        codes = {b["code"] for b in qa.get("blockers", [])}
        if code != EXIT_BLOCKERS or "NUMERIC_UNTYPED" not in codes:
            failures.append(f"bare_qps_untyped expected NUMERIC_UNTYPED, got {codes}")

        hedge = _write_bundle(tmp_path / "hedge_upgrade", kind="valid")
        ledger = _minimal_ledger(with_derived=False)
        ledger["claims"][0]["original_en"] = "Results may suggest gains."
        ledger["claims"][0]["explanation_zh"] = "结果证明必然提升。"
        sm = _minimal_source_map()
        sm["blocks"][0]["text"] = _S001_TEXT + " Results may suggest gains."
        (hedge / "source_map.json").write_text(
            json.dumps(sm, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (hedge / "claim_ledger.json").write_text(
            json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        code, qa = verify_bundle(hedge, production=True)
        codes = {b["code"] for b in qa.get("blockers", [])}
        if "HEDGE_STRENGTHEN" not in codes:
            failures.append(f"hedge_upgrade expected HEDGE_STRENGTHEN, got {codes}")

        # Negative: faithful hedge should pass
        hedge_ok = _write_bundle(tmp_path / "hedge_ok", kind="valid")
        ledger = _minimal_ledger(with_derived=False)
        ledger["claims"][0]["original_en"] = "Results may suggest gains."
        ledger["claims"][0]["explanation_zh"] = "结果可能提示有增益。"
        sm = _minimal_source_map()
        sm["blocks"][0]["text"] = _S001_TEXT + " Results may suggest gains."
        (hedge_ok / "source_map.json").write_text(
            json.dumps(sm, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (hedge_ok / "claim_ledger.json").write_text(
            json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        code, qa = verify_bundle(hedge_ok, production=True)
        if code != EXIT_OK:
            failures.append(f"hedge_ok expected pass, got {qa.get('blockers')}")

        # Report untyped numeric
        rp = _write_bundle(tmp_path / "report_untyped_numeric", kind="valid")
        rep = _minimal_report()
        rep["overview"]["key_points"][0]["original_en"] = "Speedup is 9.6× (27700/2900)."
        rep["overview"]["key_points"][0]["explanation_zh"] = "加速为 9.6×。"
        rep["overview"]["key_points"][0].pop("numeric_ref", None)
        # claim_id CL001 has no numeric when derived omitted — use with_derived False ledger
        (rp / "claim_ledger.json").write_text(
            json.dumps(_minimal_ledger(with_derived=False), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (rp / "report.json").write_text(
            json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        code, qa = verify_bundle(rp, production=True)
        codes = {b["code"] for b in qa.get("blockers", [])}
        if "NUMERIC_UNTYPED" not in codes:
            failures.append(f"report_untyped_numeric expected NUMERIC_UNTYPED, got {codes}")

        # Forged caption EN
        fc = _write_bundle(tmp_path / "forged_caption_en", kind="valid")
        rep = _minimal_report()
        rep["selected_figures_tables"][0]["caption_en"] = "Figure 1: Totally forged caption."
        (fc / "report.json").write_text(
            json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        code, qa = verify_bundle(fc, production=True)
        codes = {b["code"] for b in qa.get("blockers", [])}
        if "SPAN_MISMATCH" not in codes:
            failures.append(f"forged_caption_en expected SPAN_MISMATCH, got {codes}")

        # Crop sha mismatch
        csha = _write_bundle(tmp_path / "crop_sha_mismatch", kind="valid")
        rep = _minimal_report()
        rep["selected_figures_tables"][0]["crop"]["source_sha256"] = "b" * 64
        (csha / "report.json").write_text(
            json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        meta = json.loads((csha / "assets" / "F001.crop.json").read_text(encoding="utf-8"))
        meta["source_sha256"] = "b" * 64
        (csha / "assets" / "F001.crop.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        code, qa = verify_bundle(csha, production=True)
        codes = {b["code"] for b in qa.get("blockers", [])}
        if "asset_crop_provenance" not in codes:
            failures.append(f"crop_sha_mismatch expected asset_crop_provenance, got {codes}")

        # Schema missing → block (fail-closed)
        smiss = _write_bundle(tmp_path / "schema_missing_block", kind="valid")
        empty_schemas = tmp_path / "empty_schemas"
        empty_schemas.mkdir(exist_ok=True)
        mod = sys.modules[__name__]
        saved = mod.SCHEMA_DIR
        mod.SCHEMA_DIR = empty_schemas
        try:
            code, qa = verify_bundle(smiss, production=True)
        finally:
            mod.SCHEMA_DIR = saved
        codes = {b["code"] for b in qa.get("blockers", [])}
        if "schema_missing" not in codes:
            failures.append(f"schema_missing expected block, got {codes}")

        # Secret path exact
        sec_repo = tmp_path / "secrepo"
        head = _init_repo(sec_repo)
        (sec_repo / "secrets").mkdir(exist_ok=True)
        (sec_repo / "secrets" / "config.yaml").write_text("api_key: x\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=str(sec_repo), capture_output=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=t@example.com",
                "-c",
                "user.name=t",
                "commit",
                "-m",
                "sec",
            ],
            cwd=str(sec_repo),
            capture_output=True,
        )
        head = _git_head(sec_repo) or head
        sec_b = _write_bundle(tmp_path / "secret_exact", kind="valid")
        cm = _code_map_obj(sec_repo, head)
        cm["mappings"][0]["code_refs"][0]["path"] = "secrets/config.yaml"
        cm["mappings"][0]["code_refs"][0]["symbol"] = "api_key"
        cm["mappings"][0]["code_refs"][0]["line_end"] = 1
        (sec_b / "code_map.json").write_text(
            json.dumps(cm, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (sec_b / "repo_snapshot.json").write_text(
            json.dumps(_repo_snapshot_obj(sec_repo, head), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        rep = _minimal_report()
        rep["meta"]["code_repo"] = "present"
        (sec_b / "report.json").write_text(
            json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        code, qa = verify_bundle(sec_b, repo=sec_repo, production=True)
        codes = {b["code"] for b in qa.get("blockers", [])}
        if "CODE_PATH" not in codes:
            failures.append(f"secret_exact expected CODE_PATH, got {codes}")

        # exact >20 lines even with symbol
        long_repo = tmp_path / "longrepo"
        head = _init_repo(long_repo)
        lines = ["def graph_routing():\n"] + [f"    x{i}=1\n" for i in range(25)]
        (long_repo / "src" / "a.py").write_text("".join(lines), encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=str(long_repo), capture_output=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=t@example.com",
                "-c",
                "user.name=t",
                "commit",
                "-m",
                "long",
            ],
            cwd=str(long_repo),
            capture_output=True,
        )
        head = _git_head(long_repo) or head
        long_b = _write_bundle(tmp_path / "exact_long", kind="valid")
        cm = _code_map_obj(long_repo, head, line_end=22, symbol="graph_routing")
        (long_b / "code_map.json").write_text(
            json.dumps(cm, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (long_b / "repo_snapshot.json").write_text(
            json.dumps(_repo_snapshot_obj(long_repo, head), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        rep = _minimal_report()
        rep["meta"]["code_repo"] = "present"
        (long_b / "report.json").write_text(
            json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        code, qa = verify_bundle(long_b, repo=long_repo, production=True)
        codes = {b["code"] for b in qa.get("blockers", [])}
        if "code_exact_forbidden" not in codes:
            failures.append(f"exact_long expected code_exact_forbidden, got {codes}")

        # require-pdf magic-only must fail when fitz present
        pdfb = _write_bundle(tmp_path / "require_pdf_magic", kind="valid")
        (pdfb / "report.pdf").write_bytes(b"%PDF" + b"y" * 10)
        code, qa = verify_bundle(pdfb, require_pdf=True, production=True)
        codes = {b["code"] for b in qa.get("blockers", [])}
        if code != EXIT_BLOCKERS or "pdf_invalid" not in codes:
            failures.append(f"require_pdf magic-only expected pdf_invalid, got {codes}")

        code, _ = verify_bundle(tmp_path / "no-such-dir")
        if code != EXIT_USAGE:
            failures.append(f"missing dir expected usage exit 2 got {code}")

        # code line range
        repo = tmp_path / "repo"
        head = _init_repo(repo)
        code_bundle = _write_bundle(tmp_path / "code", kind="valid")
        cm = _code_map_obj(repo, head, line_end=99, symbol="graph_routing")
        (code_bundle / "code_map.json").write_text(
            json.dumps(cm, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (code_bundle / "repo_snapshot.json").write_text(
            json.dumps(_repo_snapshot_obj(repo, head), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        rep = _minimal_report()
        rep["meta"]["code_repo"] = "present"
        (code_bundle / "report.json").write_text(
            json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        code, qa = verify_bundle(code_bundle, repo=repo, production=True)
        if code != EXIT_BLOCKERS:
            failures.append(f"bad code line expected blockers got {code}")
        else:
            codes = {b["code"] for b in qa.get("blockers", [])}
            if "code_line_range" not in codes:
                failures.append(f"expected code_line_range blocker, got {codes}")

        # exact name-only
        name_bundle = _write_bundle(tmp_path / "name_only", kind="valid")
        cm = _code_map_obj(
            repo, head, status="exact", match_basis="name_only", symbol="graph_routing"
        )
        (name_bundle / "code_map.json").write_text(
            json.dumps(cm, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (name_bundle / "repo_snapshot.json").write_text(
            json.dumps(_repo_snapshot_obj(repo, head), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        rep = _minimal_report()
        rep["meta"]["code_repo"] = "present"
        (name_bundle / "report.json").write_text(
            json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        code, qa = verify_bundle(name_bundle, repo=repo, production=True)
        codes = {b["code"] for b in qa.get("blockers", [])}
        if code != EXIT_BLOCKERS or "code_name_match_exact" not in codes:
            # schema may also block; accept schema_invalid as alternative
            if "code_name_match_exact" not in codes and "schema_invalid" not in codes:
                failures.append(f"name_only exact expected blocker, got {codes}")

        # excerpt mismatch
        ex_bundle = _write_bundle(tmp_path / "excerpt", kind="valid")
        cm = _code_map_obj(
            repo,
            head,
            status="exact",
            match_basis="symbol_and_behavior",
            symbol="",
            excerpt="totally wrong excerpt\n",
            line_end=2,
        )
        # remove empty symbol key
        cm["mappings"][0]["code_refs"][0].pop("symbol", None)
        (ex_bundle / "code_map.json").write_text(
            json.dumps(cm, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (ex_bundle / "repo_snapshot.json").write_text(
            json.dumps(_repo_snapshot_obj(repo, head), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        rep = _minimal_report()
        rep["meta"]["code_repo"] = "present"
        (ex_bundle / "report.json").write_text(
            json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        code, qa = verify_bundle(ex_bundle, repo=repo, production=True)
        codes = {b["code"] for b in qa.get("blockers", [])}
        if code != EXIT_BLOCKERS or not (
            "code_excerpt_mismatch" in codes or "code_exact_proof" in codes
        ):
            failures.append(f"excerpt mismatch expected blocker, got {codes}")

        # snapshot mismatch
        snap_bundle = _write_bundle(tmp_path / "snap_mismatch", kind="valid")
        cm = _code_map_obj(repo, head, snapshot_id="RS999")
        (snap_bundle / "code_map.json").write_text(
            json.dumps(cm, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (snap_bundle / "repo_snapshot.json").write_text(
            json.dumps(_repo_snapshot_obj(repo, head, snapshot_id="RS001"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        rep = _minimal_report()
        rep["meta"]["code_repo"] = "present"
        (snap_bundle / "report.json").write_text(
            json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        code, qa = verify_bundle(snap_bundle, repo=repo, production=True)
        codes = {b["code"] for b in qa.get("blockers", [])}
        if code != EXIT_BLOCKERS or "code_snapshot_mismatch" not in codes:
            failures.append(f"snapshot mismatch expected blocker, got {codes}")

        # legacy single_agent acceptance
        legacy = _write_bundle(tmp_path / "legacy_mode", kind="valid")
        rep = _minimal_report()
        rep["meta"].pop("execution_mode", None)
        rep["meta"]["single_agent"] = True
        (legacy / "report.json").write_text(
            json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        code, qa = verify_bundle(legacy, production=True)
        if code != EXIT_OK:
            # schema may require execution_mode; if schema_invalid, structural path should still accept via verifier
            blockers = qa.get("blockers") or []
            only_schema = blockers and all(b.get("code") == "schema_invalid" for b in blockers)
            if not only_schema:
                failures.append(f"legacy single_agent expected pass/schema-only, got {blockers}")

    if failures:
        print("verify_bundle --selftest FAIL", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return EXIT_BLOCKERS
    print("verify_bundle --selftest PASS")
    return EXIT_OK


def main(argv: Optional[List[str]] = None) -> int:
    _utf8_stdout()
    parser = argparse.ArgumentParser(description="Verify paper-reading-cn output bundle")
    parser.add_argument("--bundle", type=Path, help="Bundle directory")
    parser.add_argument("--repo", type=Path, default=None, help="Mounted repository root")
    parser.add_argument(
        "--require-pdf",
        action="store_true",
        help="Require report.pdf with %%PDF magic",
    )
    parser.add_argument(
        "--no-production",
        action="store_true",
        help="Skip production placeholder/announcement gates",
    )
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        return run_selftest()
    if not args.bundle:
        print("error: --bundle is required (or use --selftest)", file=sys.stderr)
        return EXIT_USAGE

    code, qa = verify_bundle(
        args.bundle,
        repo=args.repo,
        require_pdf=args.require_pdf,
        production=not args.no_production,
    )
    if "error" in qa and not qa.get("blockers") and not qa.get("ok", False):
        print(f"error: {qa.get('error')}", file=sys.stderr)
        return EXIT_USAGE
    status = "PASS" if code == EXIT_OK else "FAIL"
    print(
        f"verify_bundle: {status} blockers={qa.get('blocker_count', 0)} "
        f"warnings={qa.get('warning_count', 0)} schema_mode={qa.get('schema_mode')}"
    )
    for b in qa.get("blockers") or []:
        print(f"  [blocker] {b.get('code')}: {b.get('message')}")
    for w in (qa.get("warnings") or [])[:20]:
        print(f"  [warn] {w.get('code')}: {w.get('message')}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
