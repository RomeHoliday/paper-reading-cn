#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Integration self-test for paper-reading-cn toolchain.

Runs the five script --selftest entry points (extract/crop/repo/verify/render)
plus this file's own integration checks. Does not re-invoke selftest.py
recursively.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent

SCRIPT_SELFTESTS = (
    "extract_pdf.py",
    "crop_asset.py",
    "repo_snapshot.py",
    "verify_bundle.py",
    "render_report.py",
)

# Production surfaces scanned for accidental default Grok/subagent dispatch.
PRODUCTION_GLOBS = (
    "SKILL.md",
    "manifest.yaml",
    "static/**/*.md",
    "references/**/*.md",
    "schemas/**/*.json",
    "scripts/*.py",
)

# Matches that indicate a default-dispatch instruction (not opt-in / forbid text).
DISPATCH_PATTERNS = [
    re.compile(r"(?i)dispatch\s+grok"),
    re.compile(r"(?i)spawn\s+grok"),
    re.compile(r"(?i)parallel\s+grok"),
    re.compile(r"(?i)use\s+grok\s+subagents?\s+for\s+(formal|production|reading)"),
    re.compile(r"(?i)always\s+dispatch\s+(task|subagents?|grok)"),
    re.compile(r"默认派\s*Grok"),
    re.compile(r"正式阅读.*派\s*Grok"),
]

# Lines containing these are treated as forbid / opt-in documentation (allowed).
ALLOW_LINE_HINTS = (
    "do not dispatch",
    "do not launch",
    "unless the user explicitly",
    "explicit opt-in",
    "explicitly requests",
    "single-agent",
    "single agent",
    "execution_mode",
    "opt-in",
    "not opt-in",
    "禁止",
    "不要派",
    "不要启动",
    "除非用户明确",
    "显式",
    "单代理",
    "single_agent",
)


def _utf8_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def _run(args: List[str], *, cwd: Optional[Path] = None) -> Tuple[int, str]:
    proc = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(cwd or SKILL_ROOT),
        check=False,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out


def _load_yaml_paths(manifest_text: str) -> List[str]:
    """Minimal path harvest from manifest.yaml without PyYAML."""
    paths: List[str] = []
    for line in manifest_text.splitlines():
        s = line.strip()
        if s.startswith("- ") and ("/" in s or s.endswith(".md") or s.endswith(".json") or s.endswith(".py")):
            item = s[2:].strip().strip("'\"")
            # Skip prose conditions
            if item.startswith("condition:") or " " in item and not item.endswith((".md", ".json", ".py", ".yaml")):
                continue
            if any(item.endswith(ext) for ext in (".md", ".json", ".py", ".yaml", ".yml")):
                paths.append(item)
            elif "/" in item and not item.endswith(":"):
                # axis value paths like static/fragments/...
                if " " not in item:
                    paths.append(item)
        m = re.match(r"^([a-zA-Z0-9_./-]+\.(?:md|json|py|yaml|yml))\s*$", s)
        if m:
            paths.append(m.group(1))
        # key: path forms
        m2 = re.match(
            r"^[a-zA-Z0-9_]+:\s*(static/[^\s]+|references/[^\s]+|scripts/[^\s]+|schemas/[^\s]+)\s*$",
            s,
        )
        if m2:
            paths.append(m2.group(1))
    # Dedup preserve order
    seen = set()
    out: List[str] = []
    for p in paths:
        p = p.strip().rstrip(",")
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _check_manifest_and_schemas(failures: List[str]) -> None:
    manifest = SKILL_ROOT / "manifest.yaml"
    if not manifest.is_file():
        failures.append("manifest.yaml missing")
        return
    text = manifest.read_text(encoding="utf-8")
    if "always_load" not in text or "paper-reading-cn" not in text.replace("_", "-") and "name: paper-reading-cn" not in text:
        if "name: paper-reading-cn" not in text:
            failures.append("manifest.yaml missing name: paper-reading-cn")

    for rel in _load_yaml_paths(text):
        # scripts: extract_pdf: scripts/... already captured
        path = SKILL_ROOT / rel
        if not path.exists():
            failures.append(f"manifest path missing: {rel}")

    schema_dir = SKILL_ROOT / "schemas"
    for name in (
        "source-map.schema.json",
        "claim-ledger.schema.json",
        "code-map.schema.json",
        "report.schema.json",
    ):
        sp = schema_dir / name
        if not sp.is_file():
            failures.append(f"schema missing: {name}")
            continue
        try:
            json.loads(sp.read_text(encoding="utf-8"))
        except Exception as exc:
            failures.append(f"schema JSON invalid {name}: {exc}")

    evals = SKILL_ROOT / "evals" / "evals.json"
    if not evals.is_file():
        failures.append("evals/evals.json missing")
    else:
        try:
            data = json.loads(evals.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or "evals" not in data:
                failures.append("evals.json missing evals array")
        except Exception as exc:
            failures.append(f"evals.json invalid: {exc}")

    skill = SKILL_ROOT / "SKILL.md"
    if not skill.is_file():
        failures.append("SKILL.md missing")
    else:
        lines = skill.read_text(encoding="utf-8").splitlines()
        if len(lines) >= 500:
            failures.append(f"SKILL.md has {len(lines)} lines (must be < 500)")


def _iter_production_files() -> List[Path]:
    files: List[Path] = []
    for pattern in PRODUCTION_GLOBS:
        if "*" in pattern:
            files.extend(SKILL_ROOT.glob(pattern))
        else:
            p = SKILL_ROOT / pattern
            if p.is_file():
                files.append(p)
    # Unique
    out: List[Path] = []
    seen = set()
    for f in files:
        key = str(f.resolve())
        if key not in seen and f.is_file():
            seen.add(key)
            out.append(f)
    return out


def _scan_grok_dispatch(failures: List[str]) -> None:
    hits: List[str] = []
    for path in _iter_production_files():
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        rel = path.relative_to(SKILL_ROOT).as_posix()
        # Skip this selftest scanner's own pattern strings
        if path.name == "selftest.py":
            continue
        for i, line in enumerate(text.splitlines(), 1):
            low = line.lower()
            if any(h in low or h in line for h in ALLOW_LINE_HINTS):
                continue
            for pat in DISPATCH_PATTERNS:
                if pat.search(line):
                    hits.append(f"{rel}:{i}: {line.strip()[:120]}")
                    break
    if hits:
        failures.append("default Grok/subagent dispatch wording in production:")
        failures.extend(f"  {h}" for h in hits[:20])


def _integration_render_checks(failures: List[str]) -> Tuple[str, str]:
    """Returns (renderer_mode, code_cards)."""
    sys.path.insert(0, str(SCRIPTS))
    from verify_bundle import _write_bundle, verify_bundle  # type: ignore
    from render_report import (  # type: ignore
        CJK_PROBE,
        discover_browser,
        render_bundle,
    )

    renderer_mode = "html-only-tested"
    code_cards = "fail"
    css_src = SKILL_ROOT / "assets" / "report.css"

    with tempfile.TemporaryDirectory(prefix="prc-selftest-") as tmp:
        tmp_path = Path(tmp)
        valid = _write_bundle(tmp_path / "valid", kind="valid")
        if css_src.is_file():
            shutil.copy2(css_src, valid / "assets" / "report.css")

        code, qa = verify_bundle(valid, production=True)
        if code != 0:
            failures.append(f"e2e valid verify expected 0 got {code}")
        if not (valid / "qa_report.json").is_file():
            failures.append("e2e qa_report.json missing after verify")

        rcode = render_bundle(valid, html_only=True, skip_verify=False)
        if rcode != 0:
            failures.append(f"e2e html-only render expected 0 got {rcode}")
        html_path = valid / "report.html"
        if not html_path.is_file():
            failures.append("e2e report.html missing")
        else:
            html = html_path.read_text(encoding="utf-8")
            if "https://" in html or "<script" in html.lower():
                failures.append("e2e HTML has remote URL or script")
            if "execution_mode=" not in html:
                failures.append("e2e HTML missing execution_mode")
            elif (
                "execution_mode=single" not in html
                and "execution_mode=single_agent" not in html
            ):
                failures.append("e2e HTML execution_mode not single/single_agent")

        qa_path = valid / "qa_report.json"
        if qa_path.is_file():
            data = json.loads(qa_path.read_text(encoding="utf-8"))
            st = (data.get("render") or {}).get("status")
            if st != "html_only":
                failures.append(f"e2e html-only render status expected html_only got {st}")

        for kind in ("missing_zh", "bad_source_id", "missing_asset", "placeholder"):
            b = _write_bundle(tmp_path / kind, kind=kind)
            code, _ = verify_bundle(b, production=True)
            if code != 1:
                failures.append(f"e2e {kind} verify expected 1 got {code}")
            rcode = render_bundle(b, html_only=True, skip_verify=False)
            if rcode != 3:
                failures.append(f"e2e {kind} render gate expected 3 got {rcode}")

        # Code-map HTML rendering
        code_bundle = _write_bundle(tmp_path / "codehtml", kind="valid")
        if css_src.is_file():
            shutil.copy2(css_src, code_bundle / "assets" / "report.css")
        repo = tmp_path / "mini-repo"
        (repo / "src").mkdir(parents=True)
        (repo / "src" / "planner.py").write_text(
            "def plan():\n    return 64\n", encoding="utf-8"
        )
        head = "c" * 40
        code_map: Dict[str, Any] = {
            "schema_version": "1.0",
            "snapshot_id": "RS001",
            "commit": head,
            "dirty": True,
            "version_alignment_status": "same_repo_unknown_rev",
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
                            "code_excerpt": "def plan():\n    return 64\n",
                            "role_en": "implements planner stub",
                            "role_zh": "实现规划器桩代码",
                        }
                    ],
                    "uncertainty_notes_zh": "dirty tree；不证明性能。",
                }
            ],
        }
        (code_bundle / "code_map.json").write_text(
            json.dumps(code_map, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        rep = json.loads((code_bundle / "report.json").read_text(encoding="utf-8"))
        rep["meta"]["code_repo"] = "present"
        rep["meta"]["execution_mode"] = "single"
        fig = rep["selected_figures_tables"][0]
        fig["alt_text"] = "QPS vs recall scatter"
        fig["crop"] = {
            "method": "explicit_bbox",
            "status": "approximate",
            "page": 1,
            "bbox_pdf_points": [10, 20, 200, 300],
            "zoom": 2.5,
            "effective_dpi": 180,
            "source_sha256": "d" * 64,
        }
        (code_bundle / "report.json").write_text(
            json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        rcode = render_bundle(code_bundle, html_only=True, skip_verify=True)
        if rcode != 0:
            failures.append(f"e2e code-map html expected 0 got {rcode}")
        else:
            chtml = (code_bundle / "report.html").read_text(encoding="utf-8")
            need = [
                "execution_mode=single",
                "version_alignment=",
                "dirty=true",
                "partial",
                "symbol_and_behavior",
                "src/planner.py",
                "实现规划器桩代码",
                "return 64",
                "不能证明论文性能",
                'alt="QPS vs recall scatter"',
                "status=approximate",
                CJK_PROBE,
            ]
            missing = [n for n in need if n not in chtml]
            if missing:
                failures.append(f"e2e code-map/figure HTML missing: {missing}")
            else:
                code_cards = "ok"

        found = discover_browser()
        if found:
            valid2 = _write_bundle(tmp_path / "valid_pdf", kind="valid")
            if css_src.is_file():
                shutil.copy2(css_src, valid2 / "assets" / "report.css")
            rep = json.loads((valid2 / "report.json").read_text(encoding="utf-8"))
            rep["meta"]["execution_mode"] = "single"
            (valid2 / "report.json").write_text(
                json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            pcode = render_bundle(valid2, html_only=False, skip_verify=True)
            pdf = valid2 / "report.pdf"
            qa_path = valid2 / "qa_report.json"
            try:
                data = json.loads(qa_path.read_text(encoding="utf-8")) if qa_path.is_file() else {}
            except Exception:
                data = {}
            render_qa = data.get("render") or {}
            if pcode == 0 and pdf.is_file() and pdf.read_bytes()[:4] == b"%PDF":
                renderer_mode = "html+pdf"
                if render_qa.get("status") != "complete":
                    failures.append(
                        f"e2e PDF status expected complete got {render_qa.get('status')}"
                    )
                val = render_qa.get("validation") or {}
                if val.get("fitz_available"):
                    if not val.get("title_zh_found"):
                        failures.append("e2e PDF QA title_zh not found")
                    if not val.get("cjk_probe_found"):
                        failures.append("e2e PDF QA CJK probe not found")
                    if val.get("page_count") is not None and int(val["page_count"]) < 1:
                        failures.append("e2e PDF page_count < 1")
                print(f"e2e PDF OK via {render_qa.get('product')} validation={val.get('page_count')}")
            else:
                print(f"e2e PDF skipped/failed code={pcode} (HTML still tested)")
        else:
            print("e2e PDF skipped: no Edge/Chrome discovered")

    return renderer_mode, code_cards


def main() -> int:
    _utf8_stdout()
    parser = argparse.ArgumentParser(description="paper-reading-cn integration selftest")
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="Alias; always runs tests (does not recurse into selftest.py)",
    )
    parser.parse_args()

    failures: List[str] = []
    py = sys.executable

    # Five script selftests (not including selftest.py — avoid recursion)
    for script in SCRIPT_SELFTESTS:
        code, out = _run([py, str(SCRIPTS / script), "--selftest"])
        print(out.rstrip())
        if code != 0:
            failures.append(f"{script} --selftest exit {code}")

    _check_manifest_and_schemas(failures)
    _scan_grok_dispatch(failures)
    renderer_mode, code_cards = _integration_render_checks(failures)

    # Sixth: this integration module itself (already running; record pass/fail)
    integration = "pass" if not failures else "fail"

    if failures:
        print("selftest.py FAIL", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        print(
            f"E5_SELFTEST integration={integration} renderer={renderer_mode} "
            f"code_cards={code_cards}"
        )
        return 1

    print("selftest.py PASS")
    print(
        f"E5_SELFTEST integration=pass renderer={renderer_mode} code_cards={code_cards}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
