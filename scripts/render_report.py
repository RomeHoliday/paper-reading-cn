#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render report.json → self-contained report.html → optional headless PDF.

Exit codes (render path):
  0 — success (HTML; PDF if not --html-only)
  2 — usage / bad input
  3 — verify blockers / asset jail
  4 — browser not found (HTML kept)
  5 — print failed (HTML kept)
  6 — invalid PDF (HTML kept)
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_BLOCKED = 3
EXIT_NO_BROWSER = 4
EXIT_PRINT_FAILED = 5
EXIT_PDF_INVALID = 6

SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSS = SKILL_ROOT / "assets" / "report.css"

# Probe string embedded for post-print selectable-text QA (CJK).
CJK_PROBE = "中文精读探针"

REMOTE_URL_RE = re.compile(r"(?i)(https?:)?//|[a-z]+://")
SCRIPT_TAG_RE = re.compile(r"(?i)<\s*script\b")
# CSS must not contain HTML breakout, imports, or any url()/data:/expression().
CSS_STYLE_CLOSE_RE = re.compile(r"(?i)</\s*style\b")
CSS_HTML_BRACKET_RE = re.compile(r"[<>]")
CSS_IMPORT_RE = re.compile(r"(?i)@import\b")
CSS_URL_RE = re.compile(r"(?i)url\s*\(")
CSS_EXPRESSION_RE = re.compile(r"(?i)expression\s*\(")
CSS_DATA_RE = re.compile(r"(?i)data\s*:")
CSS_JS_RE = re.compile(r"(?i)javascript\s*:")
CSS_FILE_RE = re.compile(r"(?i)file\s*:")

SAFE_FALLBACK_CSS = "/* blocked unsafe stylesheet */\nbody{font-family:sans-serif;}\n"
GENERIC_FIGURE_ALT = "figure asset"

# A4 in PDF points (1/72 inch); allow ±2 pt.
A4_WIDTH_PT = 595.27
A4_HEIGHT_PT = 841.89
A4_TOL_PT = 2.0


def _css_policy_errors(css: str) -> List[str]:
    """Fail-closed CSS policy: reject breakout, HTML, imports, url/data/expression."""
    errors: List[str] = []
    if CSS_STYLE_CLOSE_RE.search(css):
        errors.append("stylesheet contains </style breakout")
    if CSS_HTML_BRACKET_RE.search(css):
        errors.append("stylesheet contains HTML brackets")
    if CSS_IMPORT_RE.search(css):
        errors.append("stylesheet contains @import")
    if CSS_URL_RE.search(css):
        errors.append("stylesheet contains url(")
    if CSS_EXPRESSION_RE.search(css):
        errors.append("stylesheet contains expression()")
    if CSS_DATA_RE.search(css):
        errors.append("stylesheet contains data:")
    if CSS_JS_RE.search(css):
        errors.append("stylesheet contains javascript:")
    if CSS_FILE_RE.search(css):
        errors.append("stylesheet contains file:")
    if REMOTE_URL_RE.search(css):
        errors.append("stylesheet contains remote URL")
    if SCRIPT_TAG_RE.search(css):
        errors.append("stylesheet contains script tag")
    return errors


def _browser_usable(path: Path) -> bool:
    if not path.is_file():
        return False
    if os.name != "nt":
        return os.access(path, os.X_OK)
    return True


def _remove_pdf_artifacts(bundle: Path) -> None:
    """Delete report.pdf and *.partial so non-complete paths never leave a PDF."""
    for p in (bundle / "report.pdf", bundle / "report.pdf.partial"):
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass


def _atomic_write_text(path: Path, text: str) -> None:
    partial = path.with_suffix(path.suffix + ".partial")
    if partial.exists():
        try:
            partial.unlink()
        except OSError:
            pass
    partial.write_text(text, encoding="utf-8")
    try:
        if path.exists():
            path.unlink()
        partial.replace(path)
    except OSError:
        if partial.exists():
            try:
                partial.unlink()
            except OSError:
                pass
        raise


def _utf8_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _esc(text: Any) -> str:
    return html.escape("" if text is None else str(text), quote=True)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _try_import_fitz():
    try:
        import fitz  # type: ignore

        return fitz
    except Exception:
        return None


def _resolve_asset(bundle: Path, rel: str) -> Tuple[Optional[Path], Optional[str]]:
    if not isinstance(rel, str) or not rel.strip():
        return None, "empty asset path"
    if REMOTE_URL_RE.search(rel) or rel.lower().startswith("javascript:"):
        return None, f"remote or unsafe asset URL: {rel}"
    p = Path(rel.replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts:
        return None, f"asset path escapes bundle: {rel}"
    norm = p.as_posix()
    if not norm.startswith("assets/"):
        return None, f"asset must be under assets/: {rel}"
    try:
        resolved = (bundle / p).resolve()
        resolved.relative_to(bundle.resolve())
    except Exception:
        return None, f"asset path escapes bundle: {rel}"
    if not resolved.is_file():
        return None, f"asset missing: {rel}"
    return resolved, None


def _file_uri(path: Path) -> str:
    abs_path = path.resolve().as_posix()
    if abs_path.startswith("/"):
        return "file://" + quote(abs_path, safe="/:")
    return "file:///" + quote(abs_path, safe="/:")


def discover_browser(explicit: Optional[Path] = None) -> Optional[Tuple[Path, str]]:
    """Resolve browser. Explicit --browser or PAPER_READING_BROWSER fail closed (no fallback)."""
    env = os.environ.get("PAPER_READING_BROWSER")

    if explicit is not None:
        if not _browser_usable(explicit):
            return None
        try:
            return explicit.resolve(), "custom"
        except Exception:
            return explicit, "custom"

    if env:
        env_path = Path(env)
        if not _browser_usable(env_path):
            return None
        try:
            return env_path.resolve(), "env"
        except Exception:
            return env_path, "env"

    candidates: List[Tuple[Path, str]] = []
    for name, product in (("msedge", "edge"), ("chrome", "chrome"), ("google-chrome", "chrome")):
        found = shutil.which(name)
        if found:
            candidates.append((Path(found), product))

    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local = os.environ.get("LocalAppData", "")
    hard = [
        (Path(pf86) / "Microsoft" / "Edge" / "Application" / "msedge.exe", "edge"),
        (Path(pf) / "Microsoft" / "Edge" / "Application" / "msedge.exe", "edge"),
        (Path(pf) / "Google" / "Chrome" / "Application" / "chrome.exe", "chrome"),
        (Path(pf86) / "Google" / "Chrome" / "Application" / "chrome.exe", "chrome"),
    ]
    if local:
        hard.append(
            (Path(local) / "Google" / "Chrome" / "Application" / "chrome.exe", "chrome")
        )
    candidates.extend(hard)

    seen = set()
    for path, product in candidates:
        try:
            key = str(path.resolve())
        except Exception:
            key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if _browser_usable(path):
            return path, product
    return None


def _load_css(bundle: Path) -> str:
    local = bundle / "assets" / "report.css"
    if local.is_file():
        return local.read_text(encoding="utf-8")
    if DEFAULT_CSS.is_file():
        return DEFAULT_CSS.read_text(encoding="utf-8")
    return "body{font-family:sans-serif;}"


def _anchors_html(sources: Any) -> str:
    if not isinstance(sources, list) or not sources:
        return ""
    chips = []
    for s in sources:
        if not isinstance(s, dict):
            continue
        page = s.get("page")
        bid = s.get("block_id") or ""
        fid = s.get("figure_id") or ""
        tid = s.get("table_id") or ""
        parts = []
        if page is not None:
            parts.append(f"p.{page}")
        if bid:
            parts.append(str(bid))
        if fid:
            parts.append(str(fid))
        if tid:
            parts.append(str(tid))
        if parts:
            chips.append(f"<span>{_esc(' / '.join(parts))}</span>")
    if not chips:
        return ""
    return '<div class="anchors">' + "".join(chips) + "</div>"


def _claim_card(item: Dict[str, Any], title: Optional[str] = None) -> str:
    en = item.get("original_en") or item.get("en_span") or ""
    zh = item.get("explanation_zh") or item.get("zh_text") or item.get("verdict_zh") or ""
    bits = ['<div class="claim-card">']
    if title:
        bits.append(f"<h3>{_esc(title)}</h3>")
    if en:
        bits.append(f'<div class="claim-en">{_esc(en)}</div>')
    if zh:
        bits.append(f'<div class="claim-zh">{_esc(zh)}</div>')
    bits.append(_anchors_html(item.get("sources")))
    cid = item.get("claim_id")
    if cid:
        bits.append(f'<div class="anchors"><span>{_esc(cid)}</span></div>')
    bits.append("</div>")
    return "\n".join(bits)


def _execution_mode_label(meta: Dict[str, Any]) -> str:
    """Prefer meta.execution_mode; retain legacy single_agent for compatibility."""
    em = meta.get("execution_mode")
    if isinstance(em, str) and em.strip():
        return em.strip()
    if meta.get("single_agent") is True:
        return "single_agent"
    if meta.get("single_agent") is False:
        return "multi_agent"
    return "single_agent"


def _short_excerpt(text: Any, *, max_lines: int = 12, max_chars: int = 900) -> str:
    if text is None:
        return ""
    s = str(text).replace("\r\n", "\n").replace("\r", "\n")
    lines = s.split("\n")
    if len(lines) > max_lines:
        s = "\n".join(lines[:max_lines]) + "\n…"
    if len(s) > max_chars:
        s = s[: max_chars - 1] + "…"
    return s


def _load_code_map(bundle: Path) -> Optional[Dict[str, Any]]:
    path = bundle / "code_map.json"
    if not path.is_file():
        return None
    try:
        obj = _load_json(path)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _crop_meta_html(card: Dict[str, Any], bundle: Path) -> str:
    crop = card.get("crop")
    if not isinstance(crop, dict):
        asset_rel = str(card.get("asset_path") or card.get("asset") or "")
        if asset_rel:
            rel = Path(asset_rel.replace("\\", "/"))
            candidates = [
                bundle / (rel.as_posix() + ".crop.json"),
                bundle / rel.with_suffix(".crop.json"),
            ]
            for sc in candidates:
                if sc.is_file():
                    try:
                        loaded = _load_json(sc)
                        if isinstance(loaded, dict):
                            crop = loaded
                            break
                    except Exception:
                        pass
    if not isinstance(crop, dict):
        return ""
    status = crop.get("status") or ""
    page = crop.get("page")
    bbox = crop.get("bbox_pdf_points") or crop.get("bbox")
    zoom = crop.get("zoom")
    dpi = crop.get("effective_dpi") or crop.get("dpi")
    sha = crop.get("source_sha256") or crop.get("source_hash") or ""
    bits = ['<div class="crop-meta">']
    bits.append(f"<strong>裁剪</strong>：status={_esc(status)}")
    if page is not None:
        bits.append(f" · page={_esc(page)}")
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        bbox_s = ",".join(str(x) for x in bbox)
        bits.append(f" · bbox=[{_esc(bbox_s)}]")
    if zoom is not None:
        bits.append(f" · zoom={_esc(zoom)}")
    if dpi is not None:
        bits.append(f" · DPI={_esc(dpi)}")
    if sha:
        short = str(sha)[:12] + ("…" if len(str(sha)) > 12 else "")
        bits.append(f" · source_sha256={_esc(short)}")
    bits.append("</div>")
    return "".join(bits)


def _code_map_section_html(code_map: Dict[str, Any]) -> List[str]:
    """Render implementation-correspondence from current code_map.json."""
    parts: List[str] = []
    align = code_map.get("version_alignment_status") or ""
    commit = code_map.get("commit") or ((code_map.get("repo") or {}).get("head") or "")
    dirty = code_map.get("dirty")
    if dirty is None:
        dirty = (code_map.get("repo") or {}).get("dirty")
    snap = code_map.get("snapshot_id") or ""

    warn_bits = []
    if align:
        warn_bits.append(f"version_alignment={align}")
    if commit:
        warn_bits.append(f"commit={str(commit)[:12]}")
    if dirty:
        warn_bits.append("dirty=true")
    if snap:
        warn_bits.append(f"snapshot={snap}")
    if warn_bits:
        cls = "code-align-warn" if dirty or align in ("likely_drift", "unrelated") else "code-align"
        parts.append(f'<p class="{cls}">{_esc(" · ".join(warn_bits))}</p>')
    parts.append(
        '<p class="code-disclaimer">'
        + _esc(
            "代码对应仅说明实现位置与机制/配置关系；仓库证据不能证明论文性能或实验结果。"
        )
        + "</p>"
    )

    for mapping in code_map.get("mappings") or []:
        if not isinstance(mapping, dict):
            continue
        mid = mapping.get("id") or mapping.get("map_id") or ""
        status = mapping.get("status") or "unverified"
        basis = mapping.get("match_basis") or ""
        en = mapping.get("paper_statement_en") or ""
        zh = mapping.get("paper_explanation_zh") or ""
        sources = mapping.get("paper_sources") or mapping.get("sources") or []
        notes = mapping.get("uncertainty_notes_zh") or mapping.get("notes_zh") or ""

        parts.append('<div class="code-card keep-together">')
        parts.append(
            f'<div class="code-badges">'
            f'<span class="badge status-{_esc(status)}">{_esc(status)}</span>'
            + (f'<span class="badge">{_esc(basis)}</span>' if basis else "")
            + (f'<span class="badge">{_esc(mid)}</span>' if mid else "")
            + f'<span class="badge">{_esc(mapping.get("claim_id") or "")}</span>'
            + "</div>"
        )
        if en:
            parts.append(f'<div class="claim-en">{_esc(en)}</div>')
        if zh:
            parts.append(f'<div class="claim-zh">{_esc(zh)}</div>')
        parts.append(_anchors_html(sources))
        if notes:
            parts.append(f'<p class="uncertainty">{_esc(notes)}</p>')

        for ref in mapping.get("code_refs") or []:
            if not isinstance(ref, dict):
                continue
            path = ref.get("path") or ""
            ls = ref.get("line_start")
            le = ref.get("line_end")
            loc = f"{path}:{ls}–{le}" if ls is not None and le is not None else str(path)
            symbol = ref.get("symbol") or ""
            role_en = ref.get("role_en") or ""
            role_zh = ref.get("role_zh") or ""
            excerpt = _short_excerpt(ref.get("code_excerpt") or "")
            parts.append('<div class="code-ref">')
            parts.append(f'<div class="code-loc"><code>{_esc(loc)}</code></div>')
            if symbol:
                parts.append(f'<div class="code-symbol">symbol: <code>{_esc(symbol)}</code></div>')
            if role_en:
                parts.append(f'<div class="role-en">{_esc(role_en)}</div>')
            if role_zh:
                parts.append(f'<div class="role-zh">{_esc(role_zh)}</div>')
            if excerpt:
                parts.append(f"<pre><code>{_esc(excerpt)}</code></pre>")
            parts.append("</div>")
        parts.append("</div>")

    unmapped = code_map.get("unmapped_paper_claims") or []
    if unmapped:
        parts.append("<h3>未映射主张</h3><ul>")
        for u in unmapped:
            if not isinstance(u, dict):
                continue
            note = u.get("note_zh") or u.get("reason_zh") or u.get("reason") or ""
            parts.append(
                f"<li>{_esc(u.get('claim_id') or '')}: {_esc(note)}</li>"
            )
        parts.append("</ul>")
    return parts


SECTION_SPECS: List[Tuple[str, str, str]] = [
    ("sec-overview", "概览", "overview"),
    ("sec-problem", "问题与意义", "problem"),
    ("sec-argument", "论证结构", "argument_map"),
    ("sec-method", "方法与设计", "method"),
    ("sec-figures", "关键图表", "selected_figures_tables"),
    ("sec-evidence", "实验与证据审计", "evidence_audit"),
    ("sec-contrib", "贡献与边界", "contributions"),
    ("sec-limits", "局限与审稿质疑", "limitations"),
    ("sec-repro", "复现清单", "reproduction"),
    ("sec-terms", "术语表", "terminology"),
    ("sec-code", "论文与代码对应", "code_correspondence"),
]


def render_html(
    bundle: Path,
    report: Dict[str, Any],
    *,
    code_map: Optional[Dict[str, Any]] = None,
) -> Tuple[str, List[str], List[str]]:
    """Return (html, errors, warnings). errors non-empty ⇒ do not write HTML / PDF."""
    errors: List[str] = []
    warnings: List[str] = []
    meta = report.get("meta") or {}
    if not isinstance(meta, dict):
        meta = {}
    title_zh = meta.get("title_zh") or "精读报告"
    title_en = meta.get("title_en") or ""
    css = _load_css(bundle)
    css_errs = _css_policy_errors(css)
    if css_errs:
        # Fail closed: never inline unsafe bundle/skill CSS (no unsafe override).
        errors.extend(css_errs)
        css = SAFE_FALLBACK_CSS

    if code_map is None:
        code_map = _load_code_map(bundle)

    has_code = bool(code_map) or bool(report.get("code_correspondence")) or (
        (meta.get("code_repo") or "absent") == "present"
    )

    parts: List[str] = [
        "<!DOCTYPE html>",
        '<html lang="zh-CN">',
        "<head>",
        '<meta charset="utf-8"/>',
        f"<title>{_esc(title_zh)}</title>",
        "<style>",
        css,
        "</style>",
        "</head>",
        "<body>",
        f"<h1>{_esc(title_zh)}</h1>",
    ]
    if title_en:
        parts.append(f'<p class="meta">{_esc(title_en)}</p>')
    meta_bits = []
    emode = _execution_mode_label(meta)
    meta_bits.append(f"execution_mode={emode}")
    for k in ("depth", "paper_type", "source_format", "generated_at"):
        if meta.get(k):
            meta_bits.append(f"{k}={meta[k]}")
    # Legacy field retained for compatibility display when present
    if "single_agent" in meta:
        meta_bits.append(f"single_agent={meta.get('single_agent')}")
    if meta_bits:
        parts.append(f'<p class="meta">{_esc(" · ".join(meta_bits))}</p>')

    toc_items = []
    for sid, title, key in SECTION_SPECS:
        if key == "code_correspondence" and not has_code:
            continue
        toc_items.append(f'<li><a href="#{_esc(sid)}">{_esc(title)}</a></li>')
    parts.append('<nav class="toc"><strong>目录</strong><ol>')
    parts.extend(toc_items)
    parts.append("</ol></nav>")

    parts.append('<section id="sec-overview">')
    parts.append("<h2>概览</h2>")
    ov = report.get("overview") or {}
    if isinstance(ov, dict):
        if ov.get("summary_zh"):
            parts.append(f"<p>{_esc(ov['summary_zh'])}</p>")
        if ov.get("audience_zh"):
            parts.append(f"<p><strong>读者</strong>：{_esc(ov['audience_zh'])}</p>")
        for item in ov.get("key_points") or []:
            if isinstance(item, dict):
                parts.append(_claim_card(item))
    parts.append("</section>")

    parts.append('<section id="sec-problem"><h2>问题与意义</h2>')
    for item in ((report.get("problem") or {}).get("items") or []):
        if isinstance(item, dict):
            parts.append(_claim_card(item))
    parts.append("</section>")

    parts.append('<section id="sec-argument"><h2>论证结构</h2>')
    am = report.get("argument_map") or {}
    if isinstance(am, dict) and am.get("intro_zh"):
        parts.append(f"<p>{_esc(am['intro_zh'])}</p>")
    for node in (am.get("nodes") if isinstance(am, dict) else None) or []:
        if isinstance(node, dict):
            parts.append(_claim_card(node, title=str(node.get("label_zh") or "节点")))
    parts.append("</section>")

    parts.append('<section id="sec-method"><h2>方法与设计</h2>')
    method = report.get("method") or {}
    for item in (method.get("items") if isinstance(method, dict) else None) or []:
        if isinstance(item, dict):
            parts.append(_claim_card(item))
    for item in (method.get("algorithm_steps") if isinstance(method, dict) else None) or []:
        if isinstance(item, dict):
            parts.append(_claim_card(item))
    parts.append("</section>")

    parts.append('<section id="sec-figures"><h2>关键图表</h2>')
    for card in report.get("selected_figures_tables") or []:
        if not isinstance(card, dict):
            continue
        asset_rel = card.get("asset_path") or card.get("asset") or ""
        resolved, err = _resolve_asset(bundle, str(asset_rel))
        if err:
            errors.append(err)
            continue
        assert resolved is not None
        rel_uri = Path(str(asset_rel).replace("\\", "/")).as_posix()
        # alt_text only; missing alt is a verifier concern — use safe generic + warning
        alt = card.get("alt_text")
        if not (isinstance(alt, str) and alt.strip()):
            alt = GENERIC_FIGURE_ALT
            aid = card.get("asset_id") or rel_uri
            warnings.append(
                f"figure {aid}: missing alt_text; using generic alt (verifier should require alt_text)"
            )
        parts.append('<div class="figure-card keep-together">')
        parts.append(f'<h3>{_esc(card.get("asset_id") or "")}</h3>')
        parts.append(f'<img src="{_esc(rel_uri)}" alt="{_esc(alt)}"/>')
        parts.append(f'<div class="caption-en">{_esc(card.get("caption_en") or "")}</div>')
        parts.append(f'<div class="caption-zh">{_esc(card.get("caption_zh") or "")}</div>')
        parts.append(_crop_meta_html(card, bundle))
        if card.get("axes_encoding_zh"):
            parts.append(f'<p>{_esc(card["axes_encoding_zh"])}</p>')
        if card.get("reading_zh"):
            parts.append(f'<p class="reading-zh">{_esc(card["reading_zh"])}</p>')
        parts.append(
            f'<p><strong>支持</strong>：{_esc(card.get("evidence_supported_zh") or "")}</p>'
        )
        parts.append(
            f'<p><strong>不支持</strong>：{_esc(card.get("evidence_not_supported_zh") or "")}</p>'
        )
        parts.append(_anchors_html(card.get("sources")))
        parts.append("</div>")
    parts.append("</section>")

    parts.append('<section id="sec-evidence"><h2>实验与证据审计</h2>')
    for item in ((report.get("evidence_audit") or {}).get("items") or []):
        if isinstance(item, dict):
            parts.append(_claim_card(item))
            if item.get("verdict_zh"):
                parts.append(f'<p><strong>判断</strong>：{_esc(item["verdict_zh"])}</p>')
    parts.append("</section>")

    parts.append('<section id="sec-contrib"><h2>贡献与边界</h2>')
    for item in ((report.get("contributions") or {}).get("items") or []):
        if isinstance(item, dict):
            parts.append(_claim_card(item))
    parts.append("</section>")

    parts.append('<section id="sec-limits"><h2>局限与审稿质疑</h2>')
    lim = report.get("limitations") or {}
    for item in (lim.get("items") if isinstance(lim, dict) else None) or []:
        if isinstance(item, dict):
            parts.append(_claim_card(item))
    for item in (lim.get("reviewer_objections") if isinstance(lim, dict) else None) or []:
        if isinstance(item, dict):
            parts.append(_claim_card(item, title="可能质疑"))
    parts.append("</section>")

    reproduction = report.get("reproduction") or {}
    parts.append('<section id="sec-repro"><h2>复现清单</h2>')
    if reproduction.get("status") == "not_applicable":
        parts.append(
            '<div class="status-note"><strong>不适用：</strong>'
            + _esc(reproduction.get("reason_zh") or "")
            + _anchors_html(reproduction.get("sources"))
            + "</div>"
        )
    parts.append('<ul class="checklist">')
    for item in (reproduction.get("checklist") or []):
        if not isinstance(item, dict):
            continue
        step = item.get("step_zh") or ""
        en = item.get("original_en") or ""
        parts.append(
            f"<li>{_esc(step)}"
            + (f'<div class="claim-en">{_esc(en)}</div>' if en else "")
            + _anchors_html(item.get("sources"))
            + "</li>"
        )
    parts.append("</ul></section>")

    parts.append('<section id="sec-terms"><h2>术语表</h2>')
    parts.append(
        '<table class="terms"><thead><tr><th>English</th><th>中文</th><th>注</th></tr></thead><tbody>'
    )
    for row in report.get("terminology") or []:
        if not isinstance(row, dict):
            continue
        parts.append(
            "<tr>"
            f"<td>{_esc(row.get('term_en') or row.get('term') or '')}</td>"
            f"<td>{_esc(row.get('term_zh') or row.get('preferred_zh') or '')}</td>"
            f"<td>{_esc(row.get('note_zh') or '')}</td>"
            "</tr>"
        )
    parts.append("</tbody></table></section>")

    if has_code:
        parts.append('<section id="sec-code"><h2>论文与代码对应</h2>')
        if code_map:
            parts.extend(_code_map_section_html(code_map))
        cc = report.get("code_correspondence")
        if isinstance(cc, dict):
            if cc.get("summary_zh"):
                parts.append(f"<p>{_esc(cc['summary_zh'])}</p>")
            for item in cc.get("items") or []:
                if isinstance(item, dict):
                    parts.append(_claim_card(item))
            mids = cc.get("mapping_ids") or []
            if mids and not code_map:
                parts.append("<p>映射：" + _esc(", ".join(str(m) for m in mids)) + "</p>")
        parts.append("</section>")

    parts.append(
        f'<p class="footer-note cjk-probe">{_esc(CJK_PROBE)} · '
        f"Generated by paper-reading-cn · {_esc(_now_iso())} · local assets only</p>"
    )
    parts.append("</body></html>")
    html_out = "\n".join(parts)

    if SCRIPT_TAG_RE.search(html_out):
        errors.append("emitted HTML contains <script>")
    for m in re.finditer(r'(?i)\b(?:src|href)=["\']([^"\']+)["\']', html_out):
        href = m.group(1)
        if href.startswith("#") or href.startswith("assets/"):
            continue
        low = href.lower()
        if (
            low.startswith("data:")
            or low.startswith("javascript:")
            or low.startswith("file:")
            or REMOTE_URL_RE.search(href)
        ):
            errors.append(f"remote or unsafe URL in HTML: {href}")

    return html_out, errors, warnings


def _merge_qa(bundle: Path, render_obj: Dict[str, Any]) -> None:
    path = bundle / "qa_report.json"
    base: Dict[str, Any] = {}
    if path.is_file():
        try:
            base = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            base = {}
    base["render"] = render_obj
    if render_obj.get("delivery"):
        base["delivery"] = render_obj["delivery"]
    base["generated_at"] = base.get("generated_at") or _now_iso()
    path.write_text(json.dumps(base, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_pdf(
    pdf_path: Path,
    *,
    title_zh: str,
    skip_fitz_qa: bool = False,
) -> Tuple[bool, Dict[str, Any], List[str], List[str]]:
    """Post-print PDF QA. Returns (ok, validation, errors, warnings)."""
    errors: List[str] = []
    warnings: List[str] = []
    validation: Dict[str, Any] = {
        "magic_pdf": False,
        "size": 0,
        "page_count": None,
        "text_chars": None,
        "media_box": None,
        "page_size": "A4",
        "fitz_available": False,
        "title_zh_found": False,
        "cjk_probe_found": False,
    }

    if not pdf_path.is_file():
        errors.append("pdf missing")
        return False, validation, errors, warnings

    size = pdf_path.stat().st_size
    validation["size"] = size
    if size < 1024:
        errors.append("pdf size < 1 KiB")

    magic = pdf_path.read_bytes()[:4]
    validation["magic_pdf"] = magic == b"%PDF"
    if magic != b"%PDF":
        errors.append("pdf magic invalid")

    # --skip-pdf-qa and missing fitz can NEVER yield delivery=complete.
    if skip_fitz_qa:
        validation["fitz_available"] = False
        errors.append(
            "PDF QA skipped (--skip-pdf-qa); delivery cannot be complete"
        )
        return False, validation, errors, warnings

    fitz = _try_import_fitz()
    if fitz is None:
        validation["fitz_available"] = False
        errors.append(
            "PyMuPDF (fitz) required for PDF delivery=complete; not importable. "
            "Install: python -m pip install pymupdf"
        )
        return False, validation, errors, warnings

    validation["fitz_available"] = True
    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        errors.append(f"fitz open failed: {exc}")
        return False, validation, errors, warnings

    try:
        page_count = doc.page_count
        validation["page_count"] = page_count
        if page_count < 1:
            errors.append("page_count < 1")

        media_boxes: List[List[float]] = []
        text_parts: List[str] = []
        for i in range(page_count):
            page = doc.load_page(i)
            rect = page.mediabox
            # mediabox: x0,y0,x1,y1
            w = abs(float(rect.x1) - float(rect.x0))
            h = abs(float(rect.y1) - float(rect.y0))
            media_boxes.append([float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)])
            # Accept portrait A4 or landscape A4
            ok_a4 = (
                abs(w - A4_WIDTH_PT) <= A4_TOL_PT and abs(h - A4_HEIGHT_PT) <= A4_TOL_PT
            ) or (
                abs(w - A4_HEIGHT_PT) <= A4_TOL_PT and abs(h - A4_WIDTH_PT) <= A4_TOL_PT
            )
            if not ok_a4:
                errors.append(
                    f"page {i + 1} MediaBox not A4 (±{A4_TOL_PT}pt): {w:.2f}x{h:.2f}"
                )
            text_parts.append(page.get_text("text") or "")
        validation["media_box"] = media_boxes[0] if media_boxes else None
        full_text = "\n".join(text_parts)
        validation["text_chars"] = len(full_text)
        if title_zh and title_zh in full_text:
            validation["title_zh_found"] = True
        else:
            errors.append("extractable text missing title_zh")
        if CJK_PROBE in full_text:
            validation["cjk_probe_found"] = True
        else:
            # Also accept title_zh itself as CJK probe when it contains CJK
            if title_zh and re.search(r"[\u4e00-\u9fff]", title_zh) and title_zh in full_text:
                validation["cjk_probe_found"] = True
                warnings.append("CJK probe marker absent; title_zh CJK used as probe")
            else:
                errors.append("extractable text missing CJK probe/title")
        if validation["text_chars"] is not None and validation["text_chars"] < 8:
            errors.append("extractable text too short")
    finally:
        doc.close()

    return (len(errors) == 0), validation, errors, warnings


def print_pdf(
    browser: Path,
    html_path: Path,
    pdf_path: Path,
    *,
    timeout_sec: int = 120,
) -> Tuple[bool, str]:
    partial = pdf_path.with_suffix(pdf_path.suffix + ".partial")
    if partial.exists():
        try:
            partial.unlink()
        except OSError:
            pass

    user_data = Path(tempfile.mkdtemp(prefix="prc-browser-ud-"))
    html_uri = _file_uri(html_path)

    def _run(headless_flag: str) -> subprocess.CompletedProcess:
        args = [
            str(browser),
            headless_flag,
            "--disable-gpu",
            "--allow-file-access-from-files",
            "--no-pdf-header-footer",
            f"--user-data-dir={user_data}",
            f"--print-to-pdf={partial}",
            "--print-to-pdf-no-header",
            html_uri,
        ]
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            check=False,
        )

    try:
        try:
            proc = _run("--headless=new")
            if proc.returncode != 0:
                proc = _run("--headless")
            if proc.returncode != 0:
                err = (proc.stderr or proc.stdout or "")[-800:]
                if partial.exists():
                    try:
                        partial.unlink()
                    except OSError:
                        pass
                return False, f"browser exit {proc.returncode}: {err}"
        except subprocess.TimeoutExpired:
            if partial.exists():
                try:
                    partial.unlink()
                except OSError:
                    pass
            return False, "print timeout"
        except OSError as exc:
            return False, f"browser launch failed: {exc}"

        if not partial.is_file() or partial.stat().st_size < 1024:
            if partial.exists():
                try:
                    partial.unlink()
                except OSError:
                    pass
            return False, "pdf missing or too small"
        magic = partial.read_bytes()[:4]
        if magic != b"%PDF":
            try:
                partial.unlink()
            except OSError:
                pass
            return False, "pdf magic invalid"
        try:
            if pdf_path.exists():
                pdf_path.unlink()
            partial.replace(pdf_path)
        except OSError as exc:
            return False, f"pdf replace failed (close viewer?): {exc}"
        return True, "ok"
    finally:
        try:
            shutil.rmtree(user_data, ignore_errors=True)
        except Exception:
            pass


def render_bundle(
    bundle: Path,
    *,
    html_only: bool = False,
    browser: Optional[Path] = None,
    timeout_sec: int = 120,
    skip_verify: bool = False,
    skip_pdf_qa: bool = False,
) -> int:
    if not bundle.is_dir():
        print(f"error: bundle not found: {bundle}", file=sys.stderr)
        return EXIT_USAGE
    bundle = bundle.resolve()
    report_path = bundle / "report.json"
    if not report_path.is_file():
        print("error: report.json missing", file=sys.stderr)
        _merge_qa(
            bundle,
            {
                "status": "error",
                "code": "bad_report_json",
                "delivery": "failed",
                "message": "report.json missing",
            },
        )
        return EXIT_USAGE

    try:
        report = _load_json(report_path)
    except Exception as exc:
        print(f"error: bad report.json: {exc}", file=sys.stderr)
        _merge_qa(
            bundle,
            {
                "status": "error",
                "code": "bad_report_json",
                "delivery": "failed",
                "message": str(exc),
            },
        )
        return EXIT_USAGE
    if not isinstance(report, dict):
        print("error: report.json must be an object", file=sys.stderr)
        return EXIT_USAGE

    # Quarantine stale PDF artifacts at render start (any non-complete path
    # must not leave a prior complete PDF beside delivery!=complete).
    _remove_pdf_artifacts(bundle)

    if not skip_verify:
        try:
            from verify_bundle import verify_bundle as _verify
        except ImportError:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from verify_bundle import verify_bundle as _verify  # type: ignore

        vcode, vqa = _verify(bundle, production=True)
        if vcode == 2:
            _remove_pdf_artifacts(bundle)
            return EXIT_USAGE
        if vcode != 0:
            _remove_pdf_artifacts(bundle)
            _merge_qa(
                bundle,
                {
                    "status": "blocked",
                    "code": "verify_failed",
                    "delivery": "failed",
                    "blocker_count": vqa.get("blocker_count"),
                    "blockers": vqa.get("blockers"),
                },
            )
            print(
                f"render_report: blocked by verify ({vqa.get('blocker_count')} blockers)",
                file=sys.stderr,
            )
            return EXIT_BLOCKED

    code_map = _load_code_map(bundle)
    html_text, errors, html_warnings = render_html(bundle, report, code_map=code_map)
    html_path = bundle / "report.html"

    if errors:
        # Fail closed before HTML write: do not leave new/partial HTML.
        for stale in (html_path, html_path.with_suffix(html_path.suffix + ".partial")):
            if stale.exists():
                try:
                    stale.unlink()
                except OSError:
                    pass
        _remove_pdf_artifacts(bundle)
        _merge_qa(
            bundle,
            {
                "status": "blocked",
                "code": "asset_or_safety",
                "delivery": "failed",
                "errors": errors,
                "warnings": html_warnings,
            },
        )
        print("render_report: blocked (HTML not written):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return EXIT_BLOCKED

    _atomic_write_text(html_path, html_text)

    assets_css = bundle / "assets" / "report.css"
    if not assets_css.is_file() and DEFAULT_CSS.is_file():
        assets_css.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(DEFAULT_CSS, assets_css)

    meta = report.get("meta") if isinstance(report.get("meta"), dict) else {}
    title_zh = str((meta or {}).get("title_zh") or "")

    if html_only:
        _remove_pdf_artifacts(bundle)
        _merge_qa(
            bundle,
            {
                "status": "html_only",
                "code": "html_only",
                "delivery": "html_only",
                "html": str(html_path),
                "pdf": None,
                "warnings": html_warnings,
                "execution_mode": _execution_mode_label(meta or {}),
            },
        )
        print(f"render_report: HTML OK delivery=html_only → {html_path}")
        return EXIT_OK

    found = discover_browser(browser)
    if not found:
        _remove_pdf_artifacts(bundle)
        msg = (
            "Explicit --browser or PAPER_READING_BROWSER missing/unexecutable"
            if (browser is not None or os.environ.get("PAPER_READING_BROWSER"))
            else "Install Microsoft Edge or Google Chrome, or set PAPER_READING_BROWSER"
        )
        _merge_qa(
            bundle,
            {
                "status": "skip",
                "code": "browser_not_found",
                "delivery": "html_only",
                "html": str(html_path),
                "message": msg,
                "warnings": html_warnings,
            },
        )
        print(
            f"render_report: browser not found; HTML kept; delivery=html_only. {msg}",
            file=sys.stderr,
        )
        return EXIT_NO_BROWSER

    browser_path, product = found
    pdf_path = bundle / "report.pdf"
    ok, msg = print_pdf(browser_path, html_path, pdf_path, timeout_sec=timeout_sec)
    if not ok:
        _remove_pdf_artifacts(bundle)
        code = EXIT_PRINT_FAILED
        if "magic" in msg or "small" in msg or "missing" in msg:
            code = EXIT_PDF_INVALID
        _merge_qa(
            bundle,
            {
                "status": "error",
                "code": "print_failed" if code == EXIT_PRINT_FAILED else "pdf_invalid",
                "delivery": "failed",
                "message": msg,
                "browser": str(browser_path),
                "product": product,
                "html": str(html_path),
                "warnings": html_warnings,
            },
        )
        print(f"render_report: PDF failed: {msg}", file=sys.stderr)
        return code

    qa_ok, validation, qa_errors, qa_warnings = validate_pdf(
        pdf_path, title_zh=title_zh, skip_fitz_qa=skip_pdf_qa
    )
    all_warnings = list(html_warnings) + list(qa_warnings)
    if not qa_ok:
        # Keep HTML; never leave PDF when QA fails / skipped / fitz missing.
        _remove_pdf_artifacts(bundle)
        status = "unverified" if skip_pdf_qa or not validation.get("fitz_available") else "error"
        code_name = (
            "pdf_qa_skipped"
            if skip_pdf_qa
            else (
                "pdf_qa_fitz_missing"
                if not validation.get("fitz_available")
                else "pdf_qa_failed"
            )
        )
        _merge_qa(
            bundle,
            {
                "status": status,
                "code": code_name,
                "delivery": "failed",
                "browser": str(browser_path),
                "product": product,
                "html": str(html_path),
                "pdf": None,
                "validation": validation,
                "errors": qa_errors,
                "warnings": all_warnings,
            },
        )
        print("render_report: PDF QA failed (PDF removed):", file=sys.stderr)
        for e in qa_errors:
            print(f"  - {e}", file=sys.stderr)
        return EXIT_PDF_INVALID

    # Complete requires fitz-backed page_count>=1, A4, selectable title_zh+CJK.
    if (
        not validation.get("fitz_available")
        or validation.get("page_count") is None
        or int(validation.get("page_count") or 0) < 1
        or not validation.get("title_zh_found")
        or not validation.get("cjk_probe_found")
    ):
        _remove_pdf_artifacts(bundle)
        _merge_qa(
            bundle,
            {
                "status": "unverified",
                "code": "pdf_qa_incomplete",
                "delivery": "failed",
                "browser": str(browser_path),
                "product": product,
                "html": str(html_path),
                "pdf": None,
                "validation": validation,
                "errors": ["complete requires fitz page_count/A4/title_zh/CJK"],
                "warnings": all_warnings,
            },
        )
        print("render_report: PDF incomplete for delivery=complete; PDF removed", file=sys.stderr)
        return EXIT_PDF_INVALID

    _merge_qa(
        bundle,
        {
            "status": "complete",
            "code": "ok",
            "delivery": "complete",
            "browser": str(browser_path),
            "product": product,
            "html": str(html_path),
            "pdf": str(pdf_path),
            "validation": validation,
            "warnings": all_warnings,
            "execution_mode": _execution_mode_label(meta or {}),
        },
    )
    print(f"render_report: HTML+PDF OK delivery=complete → {pdf_path}")
    return EXIT_OK


def _write_code_map_fixture(bundle: Path, repo: Path, head: str) -> None:
    excerpt = "def plan():\n    return 64\n"
    code_map = {
        "schema_version": "1.0",
        "snapshot_id": "RS001",
        "commit": head,
        "dirty": False,
        "version_alignment_status": "same_repo_unknown_rev",
        "paper_slug": "example",
        "repo": {"root": str(repo.resolve()), "head": head, "dirty": False},
        "mappings": [
            {
                "id": "CM001",
                "claim_id": "CL001",
                "status": "partial",
                "match_basis": "symbol_and_behavior",
                "paper_statement_en": "We reduce probe cost by graph routing.",
                "paper_explanation_zh": "通过图路由降低探测成本。",
                "paper_sources": [{"page": 1, "block_id": "S001"}],
                "code_refs": [
                    {
                        "path": "src/planner.py",
                        "line_start": 1,
                        "line_end": 2,
                        "symbol": "plan",
                        "code_excerpt": excerpt,
                        "role_en": "implements planner stub",
                        "role_zh": "实现规划器桩代码",
                    }
                ],
                "uncertainty_notes_zh": "对照仓库快照；不证明论文吞吐。",
            }
        ],
    }
    (bundle / "code_map.json").write_text(
        json.dumps(code_map, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (bundle / "repo_snapshot.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "snapshot_id": "RS001",
                "repo_root": str(repo.resolve()),
                "git": {"available": True, "commit": head, "dirty": False},
                "version_alignment": {"status": "same_repo_unknown_rev"},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def run_selftest() -> int:
    _utf8_stdout()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from verify_bundle import _write_bundle  # type: ignore

    failures: List[str] = []
    pdf_status = "skip"
    code_cards = "fail"

    with tempfile.TemporaryDirectory(prefix="prc-render-") as tmp:
        tmp_path = Path(tmp)
        valid = _write_bundle(tmp_path / "valid", kind="valid")
        if DEFAULT_CSS.is_file():
            shutil.copy2(DEFAULT_CSS, valid / "assets" / "report.css")

        # HTML happy path (skip verify: fixture may lag schema fields owned elsewhere)
        code = render_bundle(valid, html_only=True, skip_verify=True)
        if code != EXIT_OK:
            failures.append(f"html-only valid expected 0 got {code}")
        qa = {}
        if (valid / "qa_report.json").is_file():
            qa = json.loads((valid / "qa_report.json").read_text(encoding="utf-8"))
        render_qa = qa.get("render") or {}
        if render_qa.get("status") != "html_only":
            failures.append(
                f"html-only status expected html_only got {render_qa.get('status')}"
            )
        if render_qa.get("delivery") != "html_only":
            failures.append(
                f"html-only delivery expected html_only got {render_qa.get('delivery')}"
            )

        html_path = valid / "report.html"
        if not html_path.is_file():
            failures.append("report.html missing")
        else:
            text = html_path.read_text(encoding="utf-8")
            if "<script" in text.lower():
                failures.append("script tag leaked")
            if "https://" in text or "http://" in text:
                failures.append("remote URL leaked")
            if "示例系统论文" not in text:
                failures.append("title_zh missing in HTML")
            if 'id="sec-overview"' not in text:
                failures.append("section id missing")
            if "execution_mode=" not in text:
                failures.append("execution_mode not displayed")
            elif "execution_mode=single" not in text and "execution_mode=single_agent" not in text:
                failures.append("execution_mode missing single/single_agent value")
            if CJK_PROBE not in text:
                failures.append("CJK probe missing from HTML")

        # Annotate figure + execution_mode for HTML feature checks (skip verify:
        # meta.execution_mode may be outside report.schema additionalProperties)
        rep = json.loads((valid / "report.json").read_text(encoding="utf-8"))
        rep["meta"]["execution_mode"] = "single"
        fig = rep["selected_figures_tables"][0]
        fig["alt_text"] = "Scatter of QPS versus recall"
        fig["crop"] = {
            "method": "explicit_bbox",
            "status": "verified",
            "page": 1,
            "bbox_pdf_points": [72.0, 400.0, 540.0, 720.0],
            "zoom": 2.0,
            "effective_dpi": 144.0,
            "source_sha256": "a" * 64,
        }
        (valid / "report.json").write_text(
            json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        code_feat = render_bundle(valid, html_only=True, skip_verify=True)
        if code_feat != EXIT_OK:
            failures.append(f"feature html render expected 0 got {code_feat}")
        else:
            text = (valid / "report.html").read_text(encoding="utf-8")
            if "execution_mode=single" not in text:
                failures.append("execution_mode not displayed")
            if 'alt="Scatter of QPS versus recall"' not in text:
                failures.append("alt_text not used for img alt")
            if "status=verified" not in text or "bbox=" not in text:
                failures.append("crop provenance missing from figure card")

            # Escape test
            rep2 = json.loads((valid / "report.json").read_text(encoding="utf-8"))
            rep2["overview"]["key_points"][0][
                "original_en"
            ] = "<script>alert(1)</script> & </body>"
            (valid / "report.json").write_text(
                json.dumps(rep2, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            code2 = render_bundle(valid, html_only=True, skip_verify=True)
            text2 = (valid / "report.html").read_text(encoding="utf-8")
            if "<script>alert(1)</script>" in text2:
                failures.append("HTML not escaped")
            if "&lt;script&gt;" not in text2:
                failures.append("expected escaped script entities")
            if code2 != EXIT_OK:
                failures.append(f"escape re-render failed: {code2}")

        # Asset jail — no new HTML on safety fail
        bad = _write_bundle(tmp_path / "jail", kind="valid")
        if DEFAULT_CSS.is_file():
            shutil.copy2(DEFAULT_CSS, bad / "assets" / "report.css")
        rep = json.loads((bad / "report.json").read_text(encoding="utf-8"))
        rep["selected_figures_tables"][0]["asset_path"] = "../outside.png"
        (bad / "report.json").write_text(
            json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        code = render_bundle(bad, html_only=True, skip_verify=True)
        if code != EXIT_BLOCKED:
            failures.append(f"asset jail expected 3 got {code}")
        if (bad / "report.html").is_file():
            failures.append("asset jail left new report.html")

        # Missing bilingual should block via verify
        miss = _write_bundle(tmp_path / "miss", kind="missing_zh")
        code = render_bundle(miss, html_only=True, skip_verify=False)
        if code != EXIT_BLOCKED:
            failures.append(f"verify gate expected 3 got {code}")

        # --- R6 P0-1: CSS </style> breakout fail-closed ---
        style_b = _write_bundle(tmp_path / "style_break", kind="valid")
        (style_b / "assets").mkdir(parents=True, exist_ok=True)
        (style_b / "assets" / "report.css").write_text(
            "body{color:#000}\n</style><img src=x onerror=alert(1)><style>\n",
            encoding="utf-8",
        )
        code = render_bundle(style_b, html_only=True, skip_verify=True)
        if code != EXIT_BLOCKED:
            failures.append(f"style breakout expected 3 got {code}")
        if (style_b / "report.html").is_file():
            html_leak = (style_b / "report.html").read_text(encoding="utf-8")
            if "onerror" in html_leak.lower():
                failures.append("style breakout payload written to HTML")
            failures.append("style breakout left report.html")
        html_probe, errs_probe, _ = render_html(
            style_b,
            json.loads((style_b / "report.json").read_text(encoding="utf-8")),
        )
        if not errs_probe:
            failures.append("style breakout render_html errors empty")
        if "onerror" in html_probe.lower() and "</style><img" in html_probe.lower():
            failures.append("style breakout still inlined into returned HTML")

        # --- R6 P1-3: CSS data: url fail-closed ---
        data_b = _write_bundle(tmp_path / "css_data", kind="valid")
        (data_b / "assets").mkdir(parents=True, exist_ok=True)
        (data_b / "assets" / "report.css").write_text(
            "body{background:url(data:text/html,hi)}",
            encoding="utf-8",
        )
        code = render_bundle(data_b, html_only=True, skip_verify=True)
        if code != EXIT_BLOCKED:
            failures.append(f"css data: url expected 3 got {code}")
        if (data_b / "report.html").is_file():
            failures.append("css data: url left report.html")

        # --- R6 P1-4 renderer: missing alt_text → generic, not caption ---
        alt_b = _write_bundle(tmp_path / "alt_generic", kind="valid")
        if DEFAULT_CSS.is_file():
            shutil.copy2(DEFAULT_CSS, alt_b / "assets" / "report.css")
        rep = json.loads((alt_b / "report.json").read_text(encoding="utf-8"))
        fig = rep["selected_figures_tables"][0]
        fig.pop("alt_text", None)
        fig["caption_en"] = "Figure 1: QPS vs recall."
        (alt_b / "report.json").write_text(
            json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        code = render_bundle(alt_b, html_only=True, skip_verify=True)
        if code != EXIT_OK:
            failures.append(f"missing alt_text render expected 0 got {code}")
        else:
            ahtml = (alt_b / "report.html").read_text(encoding="utf-8")
            if f'alt="{GENERIC_FIGURE_ALT}"' not in ahtml:
                failures.append("missing alt_text did not use generic figure asset alt")
            if 'alt="Figure 1: QPS vs recall."' in ahtml:
                failures.append("missing alt_text fell back to caption_en")
            aqa = json.loads((alt_b / "qa_report.json").read_text(encoding="utf-8"))
            warns = (aqa.get("render") or {}).get("warnings") or []
            if not any("alt_text" in str(w) for w in warns):
                failures.append("missing alt_text warning absent from qa_report")

        # --- R6 P0-2A: stale PDF removed on html-only ---
        stale_html = _write_bundle(tmp_path / "stale_html", kind="valid")
        if DEFAULT_CSS.is_file():
            shutil.copy2(DEFAULT_CSS, stale_html / "assets" / "report.css")
        prior_pdf = b"%PDF" + b"\0" * 2000
        (stale_html / "report.pdf").write_bytes(prior_pdf)
        (stale_html / "qa_report.json").write_text(
            json.dumps({"render": {"delivery": "complete"}}, ensure_ascii=False),
            encoding="utf-8",
        )
        code = render_bundle(stale_html, html_only=True, skip_verify=True)
        if code != EXIT_OK:
            failures.append(f"stale pdf html-only expected 0 got {code}")
        if (stale_html / "report.pdf").exists():
            failures.append("stale pdf remains after html-only")
        sha = json.loads((stale_html / "qa_report.json").read_text(encoding="utf-8"))
        if (sha.get("render") or {}).get("delivery") != "html_only":
            failures.append("stale html-only delivery not html_only")

        # --- R6 P0-2B / P1-1: print fail removes stale PDF; missing browser no fallback ---
        miss_br = _write_bundle(tmp_path / "miss_browser", kind="valid")
        if DEFAULT_CSS.is_file():
            shutil.copy2(DEFAULT_CSS, miss_br / "assets" / "report.css")
        (miss_br / "report.pdf").write_bytes(prior_pdf)
        code = render_bundle(
            miss_br,
            html_only=False,
            skip_verify=True,
            browser=Path(r"C:\missing_prc_browser_does_not_exist.exe"),
        )
        if code != EXIT_NO_BROWSER:
            failures.append(f"explicit missing browser expected 4 got {code}")
        if (miss_br / "report.pdf").exists():
            failures.append("stale pdf remains after explicit browser missing")

        fail_br = _write_bundle(tmp_path / "print_fail", kind="valid")
        if DEFAULT_CSS.is_file():
            shutil.copy2(DEFAULT_CSS, fail_br / "assets" / "report.css")
        (fail_br / "report.pdf").write_bytes(prior_pdf)
        fail_cmd = tmp_path / "fail_browser.cmd"
        fail_cmd.write_text("@echo off\r\nexit /b 1\r\n", encoding="utf-8")
        code = render_bundle(
            fail_br, html_only=False, skip_verify=True, browser=fail_cmd
        )
        if code not in (EXIT_PRINT_FAILED, EXIT_PDF_INVALID):
            failures.append(f"print fail expected 5/6 got {code}")
        if (fail_br / "report.pdf").exists():
            failures.append("stale pdf remains after print fail")

        # --- R6 P0-3: fake magic PDF + --skip-pdf-qa never complete ---
        skip_b = _write_bundle(tmp_path / "skip_qa", kind="valid")
        if DEFAULT_CSS.is_file():
            shutil.copy2(DEFAULT_CSS, skip_b / "assets" / "report.css")
        fake_py = tmp_path / "fake_print_browser.py"
        fake_py.write_text(
            "import sys\n"
            "from pathlib import Path\n"
            "out=None\n"
            "for a in sys.argv[1:]:\n"
            "    if a.startswith('--print-to-pdf='):\n"
            "        out=Path(a.split('=',1)[1])\n"
            "if out:\n"
            "    out.write_bytes(b'%PDF'+b'\\0'*1200)\n"
            "sys.exit(0)\n",
            encoding="utf-8",
        )
        fake_cmd = tmp_path / "fake_print_browser.cmd"
        fake_cmd.write_text(
            f'@echo off\r\n"{sys.executable}" "{fake_py}" %*\r\n',
            encoding="utf-8",
        )
        code = render_bundle(
            skip_b,
            html_only=False,
            skip_verify=True,
            skip_pdf_qa=True,
            browser=fake_cmd,
        )
        if code != EXIT_PDF_INVALID:
            failures.append(f"skip-pdf-qa fake magic expected 6 got {code}")
        sqa = {}
        if (skip_b / "qa_report.json").is_file():
            sqa = json.loads((skip_b / "qa_report.json").read_text(encoding="utf-8"))
        srender = sqa.get("render") or {}
        if srender.get("delivery") == "complete":
            failures.append("skip-pdf-qa set delivery=complete")
        if (skip_b / "report.pdf").exists():
            failures.append("skip-pdf-qa left report.pdf")

        # validate_pdf skip / missing-fitz gate directly
        magic_pdf = tmp_path / "magic_only.pdf"
        magic_pdf.write_bytes(b"%PDF" + b"\0" * 1200)
        ok_skip, _, err_skip, _ = validate_pdf(
            magic_pdf, title_zh="示例", skip_fitz_qa=True
        )
        if ok_skip:
            failures.append("validate_pdf(skip_fitz_qa=True) returned ok")
        if not any("skip" in e.lower() or "cannot be complete" in e.lower() for e in err_skip):
            failures.append("validate_pdf skip_fitz_qa missing complete-ban error")

        # Code-map HTML cards (skip verify — fixture may be incomplete vs verifier)
        code_bundle = _write_bundle(tmp_path / "codehtml", kind="valid")
        if DEFAULT_CSS.is_file():
            shutil.copy2(DEFAULT_CSS, code_bundle / "assets" / "report.css")
        repo = tmp_path / "mini-repo"
        repo.mkdir()
        (repo / "src").mkdir()
        (repo / "src" / "planner.py").write_text(
            "def plan():\n    return 64\n", encoding="utf-8"
        )
        head = "b" * 40
        _write_code_map_fixture(code_bundle, repo, head)
        rep = json.loads((code_bundle / "report.json").read_text(encoding="utf-8"))
        rep["meta"]["code_repo"] = "present"
        rep["meta"]["execution_mode"] = "single"
        (code_bundle / "report.json").write_text(
            json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        code = render_bundle(code_bundle, html_only=True, skip_verify=True)
        if code != EXIT_OK:
            failures.append(f"code-map html render expected 0 got {code}")
        else:
            chtml = (code_bundle / "report.html").read_text(encoding="utf-8")
            checks = [
                ("sec-code", 'id="sec-code"' in chtml),
                ("status", "partial" in chtml),
                ("match_basis", "symbol_and_behavior" in chtml),
                ("paper_en", "We reduce probe cost by graph routing." in chtml),
                ("paper_zh", "通过图路由降低探测成本。" in chtml),
                ("path_lines", "src/planner.py:1–2" in chtml or "src/planner.py:1" in chtml),
                ("symbol", "plan" in chtml),
                ("role_zh", "实现规划器桩代码" in chtml),
                ("excerpt", "return 64" in chtml),
                ("no_perf_proof", "不能证明论文性能" in chtml),
                ("align", "version_alignment=" in chtml),
            ]
            missing = [name for name, ok in checks if not ok]
            if missing:
                failures.append(f"code-map HTML missing: {missing}")
            else:
                code_cards = "ok"

        # Optional PDF + QA when browser present (fitz required for complete)
        found = discover_browser()
        fitz_mod = _try_import_fitz()
        if found and fitz_mod is not None:
            fresh = _write_bundle(tmp_path / "pdfvalid", kind="valid")
            if DEFAULT_CSS.is_file():
                shutil.copy2(DEFAULT_CSS, fresh / "assets" / "report.css")
            rep = json.loads((fresh / "report.json").read_text(encoding="utf-8"))
            rep["meta"]["execution_mode"] = "single"
            (fresh / "report.json").write_text(
                json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            code = render_bundle(fresh, html_only=False, skip_verify=True)
            qa = {}
            if (fresh / "qa_report.json").is_file():
                qa = json.loads((fresh / "qa_report.json").read_text(encoding="utf-8"))
            render_qa = qa.get("render") or {}
            if code == EXIT_OK and (fresh / "report.pdf").is_file():
                if render_qa.get("delivery") != "complete":
                    failures.append(
                        f"PDF success delivery expected complete got {render_qa.get('delivery')}"
                    )
                val = render_qa.get("validation") or {}
                if not val.get("fitz_available"):
                    failures.append("PDF complete without fitz_available")
                if val.get("page_count") is None or int(val["page_count"]) < 1:
                    failures.append("PDF page_count < 1")
                if not val.get("title_zh_found"):
                    failures.append("PDF QA missing title_zh")
                if not val.get("cjk_probe_found"):
                    failures.append("PDF QA missing CJK probe")
                pdf_status = "validated"
            elif code in (EXIT_NO_BROWSER, EXIT_PRINT_FAILED):
                pdf_status = "skip"
            elif code == EXIT_PDF_INVALID:
                failures.append(
                    f"PDF invalid: {render_qa.get('errors') or render_qa.get('message')}"
                )
                pdf_status = "fail"
            else:
                failures.append(f"PDF path unexpected code {code}")
        elif found and fitz_mod is None:
            # Browser present but fitz missing → must not complete
            fresh = _write_bundle(tmp_path / "pdf_nofitz", kind="valid")
            if DEFAULT_CSS.is_file():
                shutil.copy2(DEFAULT_CSS, fresh / "assets" / "report.css")
            code = render_bundle(fresh, html_only=False, skip_verify=True)
            if code == EXIT_OK:
                failures.append("missing fitz allowed delivery complete")
            if (fresh / "report.pdf").exists():
                failures.append("missing fitz left report.pdf")
            pdf_status = "fail"
        else:
            pdf_status = "skip"

        print(
            f"render_report --selftest renderer_mode="
            f"{'html+pdf' if pdf_status == 'validated' else 'html-only-tested'} "
            f"pdf={pdf_status} code_cards={code_cards}"
        )

    if failures:
        print("render_report --selftest FAIL", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("render_report --selftest PASS")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    _utf8_stdout()
    parser = argparse.ArgumentParser(description="Render paper-reading-cn report HTML/PDF")
    parser.add_argument("--bundle", type=Path, help="Bundle directory")
    parser.add_argument(
        "--html-only",
        action="store_true",
        help="Write report.html only; delivery=html_only (no PDF)",
    )
    parser.add_argument(
        "--pdf",
        action="store_true",
        help="Attempt PDF print (default when --html-only is not set)",
    )
    parser.add_argument("--browser", type=Path, default=None)
    parser.add_argument("--timeout-sec", type=int, default=120)
    parser.add_argument(
        "--skip-verify",
        action="store_true",
        help="Skip verify_bundle gate (debug only)",
    )
    parser.add_argument(
        "--skip-pdf-qa",
        action="store_true",
        help="Skip PyMuPDF QA; never sets delivery=complete (PDF removed)",
    )
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.selftest:
        return run_selftest()
    if not args.bundle:
        print("error: --bundle is required (or use --selftest)", file=sys.stderr)
        return EXIT_USAGE
    html_only = bool(args.html_only)
    if args.pdf and args.html_only:
        print("error: --pdf and --html-only are mutually exclusive", file=sys.stderr)
        return EXIT_USAGE
    return render_bundle(
        args.bundle,
        html_only=html_only,
        browser=args.browser,
        timeout_sec=args.timeout_sec,
        skip_verify=args.skip_verify,
        skip_pdf_qa=args.skip_pdf_qa,
    )


if __name__ == "__main__":
    raise SystemExit(main())
