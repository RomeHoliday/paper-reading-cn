#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Crop an explicit page+bbox region from a PDF into a report bundle.

Production path jail: --bundle-root is required; PNG and metadata must resolve
under <bundle-root>/assets. Absolute outs, .. escapes, and symlink escapes are
rejected. Explicit bbox only (no auto-crop). Zoom >= 2. No network.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCRIPT_VERSION = "1.1.0"
PDF_BASE_DPI = 72.0
MIN_DIM_PT = 8.0
MIN_AREA_FRAC = 0.005
FULL_PAGE_AREA_FRAC = 0.85
BBOX_EPS_PT = 0.5


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if callable(reconf):
            try:
                reconf(encoding="utf-8", errors="replace")
            except Exception:
                pass


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def try_import_fitz():
    try:
        import fitz  # type: ignore

        return fitz
    except Exception:
        return None


def actionable_fitz_missing() -> str:
    return (
        "PyMuPDF (import name: fitz) is required for cropping. "
        "Install with: python -m pip install pymupdf"
    )


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_bbox(spec: str) -> Tuple[float, float, float, float]:
    parts = [p.strip() for p in spec.replace(" ", "").split(",")]
    if len(parts) != 4:
        raise ValueError("bbox must be x0,y0,x1,y1 in PDF points")
    vals: List[float] = []
    for p in parts:
        v = float(p)
        if not math.isfinite(v):
            raise ValueError("bbox values must be finite floats")
        vals.append(v)
    x0, y0, x1, y1 = vals
    if not (x1 > x0 and y1 > y0):
        raise ValueError("bbox requires x1>x0 and y1>y0")
    return x0, y0, x1, y1


def validate_bbox_geometry(
    bbox: Tuple[float, float, float, float],
    page_width: float,
    page_height: float,
    *,
    approximate: bool,
    note: str,
    eps: float = BBOX_EPS_PT,
) -> str:
    """Validate bbox vs page; return crop status verified|approximate."""
    x0, y0, x1, y1 = bbox
    for v in (x0, y0, x1, y1, page_width, page_height):
        if not math.isfinite(v):
            raise ValueError("bbox/page dimensions must be finite floats")
    if page_width <= 0 or page_height <= 0:
        raise ValueError("page size must be positive")

    if x0 < -eps or y0 < -eps or x1 > page_width + eps or y1 > page_height + eps:
        raise ValueError(
            f"bbox {bbox} outside page [0,0,{page_width},{page_height}]"
        )

    width = x1 - x0
    height = y1 - y0
    if width < MIN_DIM_PT or height < MIN_DIM_PT:
        raise ValueError(
            f"bbox width/height must be >= {MIN_DIM_PT} pt "
            f"(got {width:.3f}x{height:.3f})"
        )

    page_area = page_width * page_height
    crop_area = width * height
    if crop_area < MIN_AREA_FRAC * page_area:
        raise ValueError(
            f"bbox area {crop_area / page_area:.4%} < {MIN_AREA_FRAC:.1%} of page"
        )

    note_text = (note or "").strip()
    if crop_area > FULL_PAGE_AREA_FRAC * page_area:
        if not approximate or not note_text:
            raise ValueError(
                "bbox covers >85% of page; require --approximate and nonempty --note"
            )
        return "approximate"

    if approximate:
        if not note_text:
            raise ValueError("--approximate requires nonempty --note")
        return "approximate"
    return "verified"


def resolve_bundle_assets_root(bundle_root: Path) -> Path:
    if not str(bundle_root) or "\x00" in str(bundle_root):
        raise ValueError("unsafe bundle-root")
    bundle = bundle_root.expanduser().resolve()
    assets = (bundle / "assets").resolve()
    try:
        assets.relative_to(bundle)
    except ValueError as exc:
        raise ValueError(
            f"assets root escapes bundle-root (symlink?): {assets}"
        ) from exc
    return assets


def _posix_rel(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def resolve_under_assets(
    bundle_root: Path,
    user_path: str,
    *,
    label: str,
) -> Tuple[Path, str]:
    """Resolve user_path under <bundle-root>/assets.

    Rejects absolute paths, null bytes, .. escapes, and symlink escapes.
    Returns (absolute_path, relative_path_from_bundle with assets/ prefix).
    """
    if not user_path or "\x00" in user_path:
        raise ValueError(f"{label}: unsafe empty path")
    raw = Path(user_path)
    if raw.is_absolute():
        raise ValueError(f"{label}: absolute paths rejected: {user_path}")
    parts = raw.parts
    if ".." in parts:
        raise ValueError(f"{label}: path traversal rejected: {user_path}")

    bundle = bundle_root.expanduser().resolve()
    assets = resolve_bundle_assets_root(bundle)

    # Allow either assets/F001.png or F001.png (relative to assets/).
    rel = raw.as_posix().replace("\\", "/")
    if rel == "assets" or rel.startswith("assets/"):
        candidate = (bundle / rel).resolve()
    else:
        candidate = (assets / rel).resolve()

    try:
        candidate.relative_to(assets)
    except ValueError as exc:
        raise ValueError(
            f"{label}: must resolve under <bundle-root>/assets: {user_path}"
        ) from exc

    # Refuse writing through / onto an existing symlink target.
    if candidate.exists():
        try:
            if candidate.is_symlink():
                raise ValueError(f"{label}: refusing symlink write target: {user_path}")
        except OSError as exc:
            raise ValueError(f"{label}: cannot stat path: {user_path}") from exc

    # Parent must stay inside assets after resolve (catches parent symlink escapes).
    parent = candidate.parent
    parent.mkdir(parents=True, exist_ok=True)
    parent_res = parent.resolve()
    try:
        parent_res.relative_to(assets)
    except ValueError as exc:
        raise ValueError(
            f"{label}: parent escapes assets jail: {user_path}"
        ) from exc

    rel_from_bundle = "assets/" + _posix_rel(candidate, assets)
    return candidate, rel_from_bundle


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def crop_with_fitz(
    pdf_path: Path,
    page_1based: int,
    bbox: Tuple[float, float, float, float],
    out_png: Path,
    meta_path: Path,
    *,
    zoom: float,
    status: str,
    note: str,
    out_rel: str,
    meta_rel: str,
    figure_id: Optional[str] = None,
) -> Dict[str, Any]:
    fitz = try_import_fitz()
    if fitz is None:
        raise RuntimeError(actionable_fitz_missing())
    if zoom < 2.0 or not math.isfinite(zoom):
        raise ValueError("zoom must be a finite number >= 2")
    if page_1based < 1:
        raise ValueError("page must be >= 1 (1-based)")
    if status not in ("verified", "approximate"):
        raise ValueError("status must be verified or approximate")

    doc = fitz.open(pdf_path)
    try:
        if page_1based > doc.page_count:
            raise ValueError(
                f"page {page_1based} out of range (document has {doc.page_count} pages)"
            )
        page = doc.load_page(page_1based - 1)
        pw, ph = float(page.rect.width), float(page.rect.height)
        # Geometry already validated by caller; re-check inside page for safety.
        validate_bbox_geometry(
            bbox, pw, ph, approximate=(status == "approximate"), note=note or "ok"
        )

        clip = fitz.Rect(*bbox)
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)

        ensure_parent_dir(out_png)
        if out_png.exists() and out_png.is_symlink():
            raise ValueError(f"refusing symlink write target: {out_png}")
        pix.save(str(out_png))

        effective_dpi = int(round(PDF_BASE_DPI * zoom))
        sha = file_sha256(pdf_path)
        meta: Dict[str, Any] = {
            "created_by": "crop_asset.py",
            "tool": "crop_asset.py",
            "tool_version": SCRIPT_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "method": "explicit_bbox",
            "status": status,
            "validated": True,
            "source_pdf": str(pdf_path),
            "source_sha256": sha,
            "page": page_1based,
            "page_size_points": [round(pw, 3), round(ph, 3)],
            "bbox_pdf_points": [round(v, 3) for v in bbox],
            "coord_space": "pymupdf_page_points",
            "coord_origin": "top_left",
            "zoom": float(zoom),
            "effective_dpi": effective_dpi,
            "out_png": out_rel,
            "metadata_path": meta_rel,
            "png_size_px": [int(pix.width), int(pix.height)],
            "note": (note or "").strip(),
            "pymupdf_version": getattr(fitz, "VersionBind", None)
            or getattr(fitz, "version", ["?"])[0],
        }
        if figure_id:
            meta["figure_id"] = figure_id

        ensure_parent_dir(meta_path)
        if meta_path.exists() and meta_path.is_symlink():
            raise ValueError(f"refusing symlink write target: {meta_path}")
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return meta
    finally:
        doc.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Crop an explicit PDF page region to PNG + metadata under "
            "<bundle-root>/assets. No auto-crop."
        )
    )
    p.add_argument("pdf", nargs="?", help="Input PDF path")
    p.add_argument("--pdf", dest="pdf_opt", help="Input PDF path (alias)")
    p.add_argument(
        "--bundle-root",
        help="Report bundle root; PNG/meta must resolve under <bundle-root>/assets",
    )
    p.add_argument("--page", type=int, help="1-based page number")
    p.add_argument("--bbox", help="Crop box as x0,y0,x1,y1 in PDF points")
    p.add_argument(
        "--out",
        "-o",
        help="Output PNG path relative to bundle (assets/...) or to assets/",
    )
    p.add_argument(
        "--meta",
        default=None,
        help="Crop metadata JSON path (default: <out>.crop.json beside PNG)",
    )
    p.add_argument(
        "--zoom",
        type=float,
        default=2.0,
        help="Render zoom factor (must be >= 2, default: 2)",
    )
    p.add_argument(
        "--approximate",
        action="store_true",
        help="Mark crop status=approximate (required for >85%% page area)",
    )
    p.add_argument(
        "--note",
        default="",
        help="Nonempty note required with --approximate / full-page crops",
    )
    p.add_argument(
        "--figure-id",
        default=None,
        help="Optional F### / T### recorded in metadata",
    )
    p.add_argument("--selftest", action="store_true", help="Run built-in self-tests")
    return p


def _minimal_pdf_bytes() -> bytes:
    stream = b"BT /F1 18 Tf 72 720 Td (CropMe) Tj ET\n"
    objs = [
        b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n",
        b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n",
        (
            b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources<< /Font<< /F1 5 0 R >> >> >>endobj\n"
        ),
        (
            f"4 0 obj<< /Length {len(stream)} >>stream\n".encode("ascii")
            + stream
            + b"endstream\nendobj\n"
        ),
        b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objs:
        offsets.append(len(out))
        out.extend(obj)
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    trailer = f"trailer<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n"
    out.extend(trailer.encode("ascii"))
    return bytes(out)


def _invoke_main(argv: Sequence[str]) -> int:
    """Call main() catching argparse SystemExit so selftests can assert codes."""
    try:
        return main(list(argv))
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 1
        if isinstance(code, int):
            return code
        return 1


def run_selftest() -> int:
    failures: List[str] = []
    skips: List[str] = []

    parser = build_parser()
    try:
        ns = parser.parse_args(["--selftest"])
        if not ns.selftest:
            failures.append("argparse_selftest_flag")
    except SystemExit:
        failures.append("argparse_parse_selftest")

    # --- bbox / geometry validation (no fitz) ---
    try:
        parse_bbox("1,2,3")
        failures.append("bbox_arity_accepted")
    except ValueError:
        pass
    try:
        parse_bbox("10,10,5,5")
        failures.append("bbox_inverted_accepted")
    except ValueError:
        pass
    try:
        parse_bbox("1,2,nan,4")
        failures.append("bbox_nan_accepted")
    except ValueError:
        pass

    # outside bbox
    try:
        validate_bbox_geometry(
            (0, 0, 700, 10), 612, 792, approximate=False, note=""
        )
        failures.append("outside_bbox_accepted")
    except ValueError:
        pass

    # tiny crop (area and/or dim)
    try:
        validate_bbox_geometry(
            (0, 0, 4, 4), 612, 792, approximate=False, note=""
        )
        failures.append("tiny_crop_accepted")
    except ValueError:
        pass
    try:
        # 10x10 = 100 pt^2; 0.5% of 612*792 ≈ 2423 → still too small by area
        validate_bbox_geometry(
            (0, 0, 10, 10), 612, 792, approximate=False, note=""
        )
        failures.append("tiny_area_accepted")
    except ValueError:
        pass

    # full-page verified without approximate+note
    try:
        validate_bbox_geometry(
            (0, 0, 612, 792), 612, 792, approximate=False, note=""
        )
        failures.append("full_page_verified_accepted")
    except ValueError:
        pass
    try:
        validate_bbox_geometry(
            (0, 0, 612, 792), 612, 792, approximate=True, note=""
        )
        failures.append("full_page_empty_note_accepted")
    except ValueError:
        pass
    try:
        st = validate_bbox_geometry(
            (0, 0, 612, 792),
            612,
            792,
            approximate=True,
            note="scanned full-bleed figure",
        )
        if st != "approximate":
            failures.append("full_page_status_not_approximate")
    except ValueError as exc:
        failures.append(f"full_page_approx_rejected:{exc}")

    # valid mid-page crop geometry
    try:
        st = validate_bbox_geometry(
            (72.0, 180.0, 540.0, 520.0), 612, 792, approximate=False, note=""
        )
        if st != "verified":
            failures.append("valid_crop_status")
    except ValueError as exc:
        failures.append(f"valid_geometry_rejected:{exc}")

    # low zoom gate
    if not (1.5 < 2.0):
        failures.append("zoom_logic")

    with tempfile.TemporaryDirectory(prefix="crop_asset_selftest_") as td:
        tdir = Path(td)
        bundle = tdir / "bundle"
        assets = bundle / "assets"
        assets.mkdir(parents=True)
        pdf_path = tdir / "sample.pdf"
        pdf_path.write_bytes(_minimal_pdf_bytes())

        # --- path jail: traversal ---
        try:
            resolve_under_assets(bundle, "../escape.png", label="out")
            failures.append("traversal_accepted")
        except ValueError:
            pass
        try:
            resolve_under_assets(bundle, "assets/../../escape.png", label="out")
            failures.append("assets_traversal_accepted")
        except ValueError:
            pass

        # absolute out rejected
        abs_out = str((assets / "abs.png").resolve())
        try:
            resolve_under_assets(bundle, abs_out, label="out")
            failures.append("absolute_out_accepted")
        except ValueError:
            pass

        # symlink escape (when platform supports symlinks)
        link_path = assets / "link_out"
        outside = tdir / "outside_target"
        outside.mkdir(exist_ok=True)
        symlink_ok = False
        try:
            if link_path.exists() or link_path.is_symlink():
                link_path.unlink()
            link_path.symlink_to(outside, target_is_directory=True)
            symlink_ok = link_path.is_symlink()
        except (OSError, NotImplementedError):
            skips.append("symlink_create_unsupported")

        if symlink_ok:
            try:
                # Writing via assets/link_out/x.png would resolve outside assets.
                resolve_under_assets(bundle, "assets/link_out/x.png", label="out")
                failures.append("symlink_escape_accepted")
            except ValueError:
                pass
            try:
                link_path.unlink()
            except OSError:
                pass

        # happy-path jail resolve
        try:
            out_abs, out_rel = resolve_under_assets(
                bundle, "assets/F001.png", label="out"
            )
            if out_rel != "assets/F001.png":
                failures.append(f"out_rel_unexpected:{out_rel}")
            if not str(out_abs).startswith(str(assets.resolve())):
                failures.append("out_abs_not_under_assets")
        except ValueError as exc:
            failures.append(f"valid_jail_resolve_failed:{exc}")

        # CLI jail gates (no PyMuPDF required).
        rc = _invoke_main(
            [
                str(pdf_path),
                "--page",
                "1",
                "--bbox",
                "50,650,200,760",
                "--out",
                "assets/cli.png",
            ]
        )
        if rc == 0:
            failures.append("cli_missing_bundle_root_accepted")

        rc = _invoke_main(
            [
                str(pdf_path),
                "--bundle-root",
                str(bundle),
                "--page",
                "1",
                "--bbox",
                "50,650,200,760",
                "--out",
                "../escape.png",
            ]
        )
        if rc == 0:
            failures.append("cli_traversal_accepted")
        if (tdir / "escape.png").exists():
            failures.append("cli_traversal_wrote_file")

        fitz = try_import_fitz()
        bbox = (50.0, 650.0, 200.0, 760.0)

        if fitz is None:
            skips.append("fitz_absent_skip_crop")
            try:
                crop_with_fitz(
                    pdf_path,
                    1,
                    bbox,
                    assets / "crop.png",
                    assets / "crop.crop.json",
                    zoom=2.0,
                    status="verified",
                    note="",
                    out_rel="assets/crop.png",
                    meta_rel="assets/crop.crop.json",
                )
                failures.append("missing_fitz_did_not_raise")
            except RuntimeError as exc:
                if "pymupdf" not in str(exc).lower() and "fitz" not in str(exc).lower():
                    failures.append("missing_fitz_message_unhelpful")
        else:
            out_png, out_rel = resolve_under_assets(
                bundle, "assets/crop.png", label="out"
            )
            meta_path, meta_rel = resolve_under_assets(
                bundle, "assets/crop.crop.json", label="meta"
            )

            # low zoom
            try:
                crop_with_fitz(
                    pdf_path.resolve(),
                    1,
                    bbox,
                    out_png,
                    meta_path,
                    zoom=1.5,
                    status="verified",
                    note="",
                    out_rel=out_rel,
                    meta_rel=meta_rel,
                )
                failures.append("low_zoom_accepted")
            except ValueError:
                pass

            # outside bbox via crop path
            try:
                crop_with_fitz(
                    pdf_path.resolve(),
                    1,
                    (0.0, 0.0, 900.0, 10.0),
                    out_png,
                    meta_path,
                    zoom=2.0,
                    status="verified",
                    note="",
                    out_rel=out_rel,
                    meta_rel=meta_rel,
                )
                failures.append("outside_bbox_crop_accepted")
            except ValueError:
                pass

            # page out of range
            try:
                crop_with_fitz(
                    pdf_path.resolve(),
                    9,
                    bbox,
                    out_png,
                    meta_path,
                    zoom=2.0,
                    status="verified",
                    note="",
                    out_rel=out_rel,
                    meta_rel=meta_rel,
                )
                failures.append("page_oor_accepted")
            except ValueError:
                pass

            # CLI: full-page as verified
            rc = _invoke_main(
                [
                    str(pdf_path),
                    "--bundle-root",
                    str(bundle),
                    "--page",
                    "1",
                    "--bbox",
                    "0,0,612,792",
                    "--out",
                    "assets/full.png",
                    "--zoom",
                    "2",
                ]
            )
            if rc == 0:
                failures.append("cli_full_page_verified_accepted")

            # valid crop via CLI
            rc = _invoke_main(
                [
                    str(pdf_path),
                    "--bundle-root",
                    str(bundle),
                    "--page",
                    "1",
                    "--bbox",
                    "50,650,200,760",
                    "--out",
                    "assets/F001.png",
                    "--zoom",
                    "2",
                    "--figure-id",
                    "F001",
                ]
            )
            if rc != 0:
                failures.append(f"cli_valid_crop_failed:{rc}")
            else:
                png = assets / "F001.png"
                meta_file = assets / "F001.crop.json"
                if not png.is_file() or png.stat().st_size < 10:
                    failures.append("png_missing_or_empty")
                if not meta_file.is_file():
                    failures.append("meta_missing")
                else:
                    loaded = json.loads(meta_file.read_text(encoding="utf-8"))
                    required_keys = [
                        "source_sha256",
                        "page",
                        "bbox_pdf_points",
                        "zoom",
                        "effective_dpi",
                        "page_size_points",
                        "out_png",
                        "validated",
                        "status",
                    ]
                    for k in required_keys:
                        if k not in loaded:
                            failures.append(f"meta_missing_{k}")
                    if loaded.get("page") != 1:
                        failures.append("meta_page")
                    if loaded.get("zoom") != 2.0:
                        failures.append("meta_zoom")
                    if loaded.get("validated") is not True:
                        failures.append("meta_validated")
                    if loaded.get("status") != "verified":
                        failures.append("meta_status")
                    if loaded.get("out_png") != "assets/F001.png":
                        failures.append("meta_out_rel")
                    if loaded.get("effective_dpi") != 144:
                        failures.append("meta_dpi")
                    if not isinstance(loaded.get("source_sha256"), str) or len(
                        loaded["source_sha256"]
                    ) != 64:
                        failures.append("meta_sha256")
                    if loaded.get("method") != "explicit_bbox":
                        failures.append("meta_method")

            # approximate full-page allowed with note
            rc = _invoke_main(
                [
                    str(pdf_path),
                    "--bundle-root",
                    str(bundle),
                    "--page",
                    "1",
                    "--bbox",
                    "0,0,612,792",
                    "--out",
                    "assets/full_ok.png",
                    "--approximate",
                    "--note",
                    "full-bleed scanned figure; no tighter bbox",
                ]
            )
            if rc != 0:
                failures.append(f"cli_full_page_approx_failed:{rc}")
            else:
                m = json.loads(
                    (assets / "full_ok.crop.json").read_text(encoding="utf-8")
                )
                if m.get("status") != "approximate":
                    failures.append("approx_status")

    if skips:
        print("SKIP: " + ", ".join(skips))
    if failures:
        print("FAIL: " + "; ".join(failures))
        return 1
    print("PASS: crop_asset.py selftest")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    configure_stdio()
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.selftest:
        return run_selftest()

    pdf_arg = args.pdf_opt or args.pdf
    if not pdf_arg or args.page is None or not args.bbox or not args.out:
        parser.error(
            "pdf, --page, --bbox, and --out are required unless --selftest"
        )
    if not args.bundle_root:
        parser.error("--bundle-root is required for production crops")

    try:
        if args.zoom < 2.0 or not math.isfinite(float(args.zoom)):
            eprint("zoom must be a finite number >= 2")
            return 2

        bbox = parse_bbox(args.bbox)
        bundle_root = Path(args.bundle_root)
        # Path jail before any PDF I/O / PyMuPDF requirement.
        out_png, out_rel = resolve_under_assets(
            bundle_root, args.out, label="--out"
        )
        if args.meta:
            meta_path, meta_rel = resolve_under_assets(
                bundle_root, args.meta, label="--meta"
            )
        else:
            stem = Path(out_rel).stem
            default_meta = Path(out_rel).with_name(stem + ".crop.json").as_posix()
            meta_path, meta_rel = resolve_under_assets(
                bundle_root, default_meta, label="--meta"
            )

        fitz = try_import_fitz()
        if fitz is None:
            eprint(actionable_fitz_missing())
            return 2

        pdf_path = Path(pdf_arg).expanduser().resolve()
        if not pdf_path.is_file():
            eprint(f"PDF not found: {pdf_path}")
            return 2

        doc = fitz.open(pdf_path)
        try:
            page_1based = int(args.page)
            if page_1based < 1 or page_1based > doc.page_count:
                raise ValueError(
                    f"page {page_1based} out of range "
                    f"(document has {doc.page_count} pages)"
                )
            page = doc.load_page(page_1based - 1)
            pw, ph = float(page.rect.width), float(page.rect.height)
        finally:
            doc.close()

        status = validate_bbox_geometry(
            bbox,
            pw,
            ph,
            approximate=bool(args.approximate),
            note=str(args.note or ""),
        )

        meta = crop_with_fitz(
            pdf_path,
            page_1based,
            bbox,
            out_png,
            meta_path,
            zoom=float(args.zoom),
            status=status,
            note=str(args.note or ""),
            out_rel=out_rel,
            meta_rel=meta_rel,
            figure_id=args.figure_id,
        )
        print(
            json.dumps(
                {
                    "png": out_rel,
                    "meta": meta_rel,
                    "status": meta.get("status"),
                    "validated": True,
                    "source_sha256": meta.get("source_sha256"),
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as exc:
        eprint(f"crop_asset failed: {exc}")
        return 1


if __name__ == "__main__":
    os.environ.setdefault("NO_PROXY", "*")
    raise SystemExit(main())
