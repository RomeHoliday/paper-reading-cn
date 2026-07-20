#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Snapshot Git/repository facts into repo_snapshot.json.

Records paths, HEAD, branch, tag-at-head (cheap), dirty sample, tracked
totals vs listed, language extension counts, and README/config/entrypoint
*candidates* only. Makes no paper↔code semantic claims. No network. Caps
file count. Excludes vendor/generated/build dirs and secret-like paths;
never opens or copies secret contents.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

SCRIPT_VERSION = "1.1.0"
DEFAULT_MAX_FILES = 5000
DIRTY_SAMPLE_LIMIT = 32

# Directory path parts excluded from inventory (vendor / generated / build).
EXCLUDED_DIR_PARTS: Tuple[str, ...] = (
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
)

EXCLUDED_DIR_PREFIXES: Tuple[str, ...] = (
    "bazel-",
)

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

# Word-ish secret markers; avoid tokenize/tokenizer and similar FPs.
_SECRET_WORD_RE = re.compile(
    r"(?i)(^|[^a-z0-9])(secrets?|tokens?|credentials?)([^a-z0-9]|$)"
)
_TOKENIZE_FP_RE = re.compile(r"(?i)tokeniz")

README_NAMES = {
    "readme",
    "readme.md",
    "readme.rst",
    "readme.txt",
    "readme.markdown",
}
CONFIG_NAMES = {
    "pyproject.toml",
    "setup.cfg",
    "setup.py",
    "package.json",
    "cargo.toml",
    "go.mod",
    "cmakelists.txt",
    "makefile",
    "meson.build",
    "environment.yml",
    "requirements.txt",
    "poetry.lock",
    "pipfile",
    "pipfile.lock",
    "tsconfig.json",
    "dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    ".gitignore",
    "manifest.yaml",
    "skill.md",
}
ENTRYPOINT_NAMES = {
    "main.py",
    "app.py",
    "cli.py",
    "__main__.py",
    "main.go",
    "main.rs",
    "main.cpp",
    "main.cc",
    "main.c",
    "index.js",
    "index.ts",
    "index.mjs",
    "server.py",
    "manage.py",
}


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


def safe_resolve(path: Path) -> Path:
    raw = str(path)
    if not raw or "\x00" in raw:
        raise ValueError(f"unsafe path: {raw!r}")
    return path.expanduser().resolve()


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def path_parts_posix(rel_posix: str) -> List[str]:
    return [p for p in rel_posix.replace("\\", "/").split("/") if p]


def is_excluded_dir_path(rel_posix: str) -> bool:
    parts = [p.lower() for p in path_parts_posix(rel_posix)]
    # Exclude if any directory component matches (file's parent parts).
    dir_parts = parts[:-1] if len(parts) > 1 else parts
    for p in dir_parts:
        if p in EXCLUDED_DIR_PARTS:
            return True
        if any(p.startswith(pref) for pref in EXCLUDED_DIR_PREFIXES):
            return True
    # Bare excluded dir entries themselves.
    if parts and parts[0] in EXCLUDED_DIR_PARTS:
        return True
    if parts and any(parts[0].startswith(pref) for pref in EXCLUDED_DIR_PREFIXES):
        return True
    return False


def is_secret_path(rel_posix: str) -> bool:
    """True for secret-like paths. Never open these; list as redacted only."""
    rel = rel_posix.replace("\\", "/")
    name = Path(rel).name
    name_l = name.lower()

    if name_l.startswith(".env"):
        return True
    if name_l.endswith(SECRET_EXT):
        return True
    if name_l.startswith(("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519")):
        return True
    if "service-account" in name_l or "service_account" in name_l:
        return True
    if name_l in {"credentials", "credentials.json", "credentials.yaml", "credentials.yml"}:
        return True
    if name_l.startswith("credentials.") or name_l.endswith(".credentials"):
        return True

    # secret / token / credential as path-name words; skip tokenize* FPs.
    if _SECRET_WORD_RE.search(name_l):
        if _TOKENIZE_FP_RE.search(name_l):
            return False
        return True

    parts = [p.lower() for p in path_parts_posix(rel)]
    if any(p in {".ssh", "secrets", "private"} for p in parts):
        return True
    return False


def run_git(
    repo: Path, args: Sequence[str], timeout: float = 60.0
) -> Tuple[int, str, str]:
    """Run git with network discouraged via env; never use shell=True."""
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    cmd = ["git", "-C", str(repo), *args]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", "git executable not found"
    except subprocess.TimeoutExpired:
        return 124, "", "git command timed out"


def git_commit_with_ident(
    repo: Path, message: str, name: str = "selftest", email: str = "selftest@example.com"
) -> Tuple[int, str, str]:
    """Commit using per-command -c identity; never mutates repo git config."""
    return run_git(
        repo,
        [
            "-c",
            f"user.name={name}",
            "-c",
            f"user.email={email}",
            "commit",
            "-m",
            message,
        ],
    )


def detect_git_status(repo: Path) -> Dict[str, Any]:
    empty: Dict[str, Any] = {
        "available": False,
        "is_git": False,
        "status": "path_not_directory",
        "head": None,
        "commit": None,
        "commit_short": None,
        "dirty": None,
        "dirty_file_count": 0,
        "dirty_files_sample": [],
        "branch": None,
        "tag_at_head": None,
    }
    if not repo.is_dir():
        return empty

    code, out, err = run_git(repo, ["rev-parse", "--is-inside-work-tree"])
    if code == 127:
        empty.update(
            {
                "status": "git_not_installed",
                "detail": err.strip(),
            }
        )
        return empty
    if code != 0 or out.strip().lower() != "true":
        empty.update(
            {
                "status": "not_a_git_repository",
                "detail": (err or out).strip() or None,
            }
        )
        return empty

    code_h, head, err_h = run_git(repo, ["rev-parse", "HEAD"])
    head_val: Optional[str]
    if code_h != 0:
        head_val = None
        head_status = "no_commits"
    else:
        head_val = head.strip()
        head_status = "ok"

    commit_short = head_val[:7] if head_val else None

    # Branch (cheap).
    branch: Optional[str] = None
    code_b, branch_out, _ = run_git(repo, ["rev-parse", "--abbrev-ref", "HEAD"])
    if code_b == 0:
        b = branch_out.strip()
        if b and b != "HEAD":
            branch = b
        elif b == "HEAD":
            branch = "HEAD"  # detached

    # Tag at HEAD (cheap exact match only).
    tag_at_head: Optional[str] = None
    if head_val:
        code_t, tags_out, _ = run_git(repo, ["tag", "--points-at", "HEAD"])
        if code_t == 0:
            tags = [t for t in tags_out.splitlines() if t.strip()]
            if tags:
                tag_at_head = tags[0].strip()

    # Dirty tracked files only (no untracked enumeration of secrets).
    dirty_sample: List[str] = []
    dirty_count = 0
    dirty: Optional[bool] = None
    code_d, dirty_out, _ = run_git(
        repo, ["status", "--porcelain", "--untracked-files=no"]
    )
    if code_d == 0:
        lines = [ln for ln in dirty_out.splitlines() if ln.strip()]
        dirty = bool(lines)
        for ln in lines:
            # porcelain: XY PATH or XY ORIG -> PATH
            path_part = ln[3:] if len(ln) >= 4 else ln
            if " -> " in path_part:
                path_part = path_part.split(" -> ", 1)[1]
            path_part = path_part.strip().replace("\\", "/")
            if not path_part:
                continue
            dirty_count += 1
            if len(dirty_sample) < DIRTY_SAMPLE_LIMIT:
                dirty_sample.append(path_part)
    else:
        dirty = None

    return {
        "available": True,
        "is_git": True,
        "status": head_status if head_status != "ok" else "ok",
        "head": head_val,
        "commit": head_val,
        "commit_short": commit_short,
        "dirty": dirty,
        "dirty_file_count": dirty_count,
        "dirty_files_sample": dirty_sample,
        "branch": branch,
        "tag_at_head": tag_at_head,
        "detail": (err_h.strip() if head_status == "no_commits" else None),
    }


def list_tracked_files(
    repo: Path, max_files: int
) -> Tuple[List[str], bool, List[str], List[str], int]:
    """Return (listed, truncated, secret_redactions, vendor_excluded, tracked_total).

    tracked_total = count of git ls-files entries (all tracked paths).
    listed excludes secrets and vendor/generated dirs, then applies max_files.
    Never opens file contents.
    """
    code, out, err = run_git(repo, ["ls-files", "-z"])
    if code != 0:
        raise RuntimeError(f"git ls-files failed: {err.strip() or out.strip()}")

    raw = [p for p in out.split("\0") if p]
    tracked_total = len(raw)
    files: List[str] = []
    secrets: List[str] = []
    vendor: List[str] = []
    truncated = False
    for rel in raw:
        rel_posix = rel.replace("\\", "/")
        if rel_posix == ".git" or rel_posix.startswith(".git/"):
            continue
        if is_secret_path(rel_posix):
            secrets.append(rel_posix)
            continue
        if is_excluded_dir_path(rel_posix):
            vendor.append(rel_posix)
            continue
        if len(files) >= max_files:
            truncated = True
            continue
        files.append(rel_posix)
    return files, truncated, secrets, vendor, tracked_total


def extension_counts(files: Sequence[str]) -> Dict[str, int]:
    c: Counter[str] = Counter()
    for f in files:
        suf = Path(f).suffix.lower()
        key = suf if suf else "<none>"
        c[key] += 1
    return dict(sorted(c.items(), key=lambda kv: (-kv[1], kv[0])))


def classify_candidates(files: Sequence[str]) -> Dict[str, List[str]]:
    def uniq(xs: List[str]) -> List[str]:
        seen: Set[str] = set()
        out: List[str] = []
        for x in xs:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out

    readmes: List[str] = []
    configs: List[str] = []
    entrypoints: List[str] = []
    for f in files:
        name = Path(f).name.lower()
        if name in README_NAMES:
            readmes.append(f)
        if name in CONFIG_NAMES:
            configs.append(f)
        if name in ENTRYPOINT_NAMES:
            entrypoints.append(f)

    return {
        "readme_candidates": uniq(readmes),
        "config_candidates": uniq(configs),
        "entrypoint_candidates": uniq(entrypoints),
    }


def snapshot_repo(repo: Path, max_files: int) -> Dict[str, Any]:
    abs_path = str(repo)
    git_info = detect_git_status(repo)
    snapshot_material = "\0".join(
        [
            abs_path,
            str(git_info.get("commit") or "non-git"),
            str(git_info.get("dirty")),
            *[str(p) for p in (git_info.get("dirty_files_sample") or [])],
        ]
    )
    snapshot_id = "RS" + hashlib.sha256(
        snapshot_material.encode("utf-8", errors="replace")
    ).hexdigest()[:12]
    excl_dirs = list(EXCLUDED_DIR_PARTS) + [f"{p}*" for p in EXCLUDED_DIR_PREFIXES]
    base: Dict[str, Any] = {
        "tool": "repo_snapshot.py",
        "tool_version": SCRIPT_VERSION,
        "schema_version": "1.0",
        "snapshot_id": snapshot_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "absolute_path": abs_path,
        "repo_root": abs_path,
        "focus_root": abs_path,
        "git": git_info,
        "disclaimer": (
            "Factual repository inventory only. "
            "No paper↔code semantic correspondence is asserted. "
            "Repository facts never prove paper performance claims."
        ),
        "tracked_files": [],
        "tracked_file_count": 0,
        "tracked_files_total": 0,
        "tracked_files_listed": 0,
        "truncated": False,
        "max_files": max_files,
        "language_extension_counts": {},
        "readme_candidates": [],
        "config_candidates": [],
        "entrypoint_candidates": [],
        "excluded_secret_paths": [],
        "excluded_dirs": excl_dirs,
        "exclusions": {
            "dir_parts": excl_dirs,
            "skipped_file_count": 0,
            "secret_redactions": [],
            "vendor_or_generated": [],
        },
        "limits": {
            "max_tracked_files": max_files,
            "truncated": False,
        },
        "version_alignment": {
            "status": "same_repo_unknown_rev",
            "paper_version_hint_en": None,
            "code_version_hint_en": None,
            "user_note": None,
        },
    }

    if not git_info.get("is_git"):
        base["status"] = git_info.get("status", "not_a_git_repository")
        return base

    files, truncated, secrets, vendor, tracked_total = list_tracked_files(
        repo, max_files
    )
    cands = classify_candidates(files)
    skipped = len(secrets) + len(vendor)
    # If truncated mid-list, remaining unlisted non-excluded paths also count.
    if truncated:
        # Approximate: total - secrets - vendor - listed
        unlisted = max(0, tracked_total - len(secrets) - len(vendor) - len(files))
        skipped += unlisted

    code_hint = None
    if git_info.get("commit_short"):
        dirty_n = git_info.get("dirty_file_count") or 0
        code_hint = f"HEAD {git_info['commit_short']}, {dirty_n} dirty tracked files"

    base.update(
        {
            "status": "ok",
            "tracked_files": files,
            "tracked_file_count": len(files),
            "tracked_files_total": tracked_total,
            "tracked_files_listed": len(files),
            "truncated": truncated,
            "language_extension_counts": extension_counts(files),
            "readme_candidates": cands["readme_candidates"],
            "config_candidates": cands["config_candidates"],
            "entrypoint_candidates": cands["entrypoint_candidates"],
            "excluded_secret_paths": secrets,
            "exclusions": {
                "dir_parts": excl_dirs,
                "skipped_file_count": skipped,
                "secret_redactions": secrets,
                "vendor_or_generated": vendor[:200],
                "vendor_or_generated_count": len(vendor),
            },
            "limits": {
                "max_tracked_files": max_files,
                "truncated": truncated,
            },
            "version_alignment": {
                "status": "same_repo_unknown_rev",
                "paper_version_hint_en": None,
                "code_version_hint_en": code_hint,
                "user_note": None,
            },
        }
    )
    if git_info.get("status") and git_info["status"] not in {"ok"}:
        base["status"] = git_info["status"]
        base["git"] = git_info
    return base


def write_json(path: Path, data: Dict[str, Any]) -> None:
    ensure_parent_dir(path)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Write repo_snapshot.json with Git facts only (no paper-code claims)."
    )
    p.add_argument("repo_pos", nargs="?", help="Repository root path")
    p.add_argument("--repo", dest="repo_opt", help="Repository root path")
    p.add_argument(
        "--out",
        "-o",
        default="repo_snapshot.json",
        help="Output JSON path (default: repo_snapshot.json)",
    )
    p.add_argument(
        "--max-files",
        type=int,
        default=DEFAULT_MAX_FILES,
        help=f"Cap tracked files listed (default: {DEFAULT_MAX_FILES})",
    )
    p.add_argument("--selftest", action="store_true", help="Run built-in self-tests")
    return p


def _local_git_config_keys(repo: Path) -> Set[str]:
    code, out, _ = run_git(repo, ["config", "--local", "--list"])
    if code != 0:
        return set()
    keys: Set[str] = set()
    for line in out.splitlines():
        if "=" in line:
            keys.add(line.split("=", 1)[0].strip())
    return keys


def run_selftest() -> int:
    failures: List[str] = []

    parser = build_parser()
    try:
        ns = parser.parse_args(["--selftest"])
        if not ns.selftest:
            failures.append("argparse_selftest_flag")
    except SystemExit:
        failures.append("argparse_parse_selftest")

    # Secret denylist + false-positive guards.
    for path in (
        ".env",
        ".env.local",
        "id_rsa",
        "creds.pem",
        "app.p12",
        "release.keystore",
        "api_token.txt",
        "my_secret.yaml",
        "credentials.json",
        "auth_token.json",
    ):
        if not is_secret_path(path):
            failures.append(f"secret_missed:{path}")
    for path in ("src/main.py", "tokenize.py", "src/tokenizer.py", "tokenize_utils.py"):
        if is_secret_path(path):
            failures.append(f"false_secret:{path}")

    # Vendor exclusion helper.
    if not is_excluded_dir_path("vendor/pkg/a.py"):
        failures.append("vendor_not_excluded")
    if not is_excluded_dir_path("third_party/faiss/Index.h"):
        failures.append("third_party_not_excluded")
    if not is_excluded_dir_path("build/CMakeCache.txt"):
        failures.append("build_not_excluded")
    if is_excluded_dir_path("src/build_utils.py"):
        failures.append("false_vendor_build_utils")

    with tempfile.TemporaryDirectory(prefix="repo_snapshot_selftest_") as td:
        tdir = Path(td)

        # Non-git directory.
        nongit = tdir / "nongit"
        nongit.mkdir()
        (nongit / "README.md").write_text("x", encoding="utf-8")
        snap = snapshot_repo(safe_resolve(nongit), max_files=100)
        if snap.get("git", {}).get("is_git") is not False:
            failures.append("nongit_marked_git")
        if snap.get("status") != "not_a_git_repository":
            failures.append(f"nongit_status:{snap.get('status')}")
        if "No paper" not in snap.get("disclaimer", "") and "no paper" not in snap.get(
            "disclaimer", ""
        ).lower():
            failures.append("missing_disclaimer")
        if "performance" not in snap.get("disclaimer", "").lower():
            failures.append("missing_perf_disclaimer")

        # Git repo fixture — never mutate git config; use per-command -c.
        repo = tdir / "repo"
        repo.mkdir()
        code, _, err = run_git(repo, ["init"])
        if code != 0:
            if code == 127:
                print("SKIP: git_not_installed (git fixture tests)")
            else:
                failures.append(f"git_init_failed:{err}")
        else:
            keys_before = _local_git_config_keys(repo)

            (repo / "README.md").write_text("# demo\n", encoding="utf-8")
            (repo / "main.py").write_text("print('hi')\n", encoding="utf-8")
            (repo / "tokenize.py").write_text("# not a secret\n", encoding="utf-8")
            (repo / "pyproject.toml").write_text(
                "[project]\nname='demo'\n", encoding="utf-8"
            )
            # Tracked secret (must redact; never open contents in snapshot).
            (repo / ".env").write_text("SECRET=do-not-copy\n", encoding="utf-8")
            (repo / "api_token.txt").write_text("tok_live_xxx\n", encoding="utf-8")
            # Vendor / build paths (tracked but excluded from listing).
            (repo / "vendor").mkdir()
            (repo / "vendor" / "lib.py").write_text("x=1\n", encoding="utf-8")
            (repo / "third_party").mkdir()
            (repo / "third_party" / "ext.cpp").write_text("//x\n", encoding="utf-8")
            (repo / "build").mkdir()
            (repo / "build" / "out.o").write_text("obj\n", encoding="utf-8")

            add_paths = [
                "README.md",
                "main.py",
                "tokenize.py",
                "pyproject.toml",
                ".env",
                "api_token.txt",
                "vendor/lib.py",
                "third_party/ext.cpp",
                "build/out.o",
            ]
            run_git(repo, ["add", *add_paths])
            c2, _, e2 = git_commit_with_ident(repo, "init")
            if c2 != 0:
                failures.append(f"git_commit_failed:{e2}")
            else:
                keys_after = _local_git_config_keys(repo)
                # No config mutation: user.* must not appear as new local keys.
                for forbidden in ("user.name", "user.email"):
                    if forbidden in keys_after and forbidden not in keys_before:
                        failures.append(f"config_mutated:{forbidden}")
                if keys_after - keys_before:
                    # Allow nothing beyond what init may have written before commit;
                    # commit path must not add user.* — already checked.
                    pass

                out_path = tdir / "repo_snapshot.json"
                data = snapshot_repo(safe_resolve(repo), max_files=100)
                write_json(out_path, data)
                loaded = json.loads(out_path.read_text(encoding="utf-8"))

                # JSON parse round-trip already done; check structure.
                if not re.fullmatch(r"RS[0-9a-f]{12}", str(loaded.get("snapshot_id") or "")):
                    failures.append("missing_or_invalid_snapshot_id")
                if not loaded.get("absolute_path"):
                    failures.append("missing_absolute_path")
                git = loaded.get("git") or {}
                if not git.get("head") and not git.get("commit"):
                    failures.append("missing_head")
                if git.get("dirty") is not False:
                    failures.append(f"unexpected_dirty:{git}")
                if git.get("branch") in (None, ""):
                    failures.append("missing_branch")
                # tag_at_head may be null — field must exist.
                if "tag_at_head" not in git:
                    failures.append("missing_tag_at_head_field")

                files = loaded.get("tracked_files") or []
                if ".env" in files or "api_token.txt" in files:
                    failures.append("tracked_secret_listed")
                redactions = (loaded.get("exclusions") or {}).get(
                    "secret_redactions"
                ) or loaded.get("excluded_secret_paths") or []
                if ".env" not in redactions or "api_token.txt" not in redactions:
                    failures.append("tracked_secret_not_redacted")
                # Must never embed secret file bodies.
                blob = json.dumps(loaded)
                if "do-not-copy" in blob or "tok_live_xxx" in blob:
                    failures.append("secret_contents_leaked")

                if "vendor/lib.py" in files or "third_party/ext.cpp" in files:
                    failures.append("vendor_listed")
                if "build/out.o" in files:
                    failures.append("build_listed")
                if "tokenize.py" not in files:
                    failures.append("tokenize_false_positive_excluded")
                if "README.md" not in files or "main.py" not in files:
                    failures.append("tracked_files_incomplete")

                total = loaded.get("tracked_files_total")
                listed = loaded.get("tracked_files_listed", loaded.get("tracked_file_count"))
                if not isinstance(total, int) or total < len(files):
                    failures.append(f"tracked_total_bad:{total}")
                if listed != len(files):
                    failures.append(f"tracked_listed_mismatch:{listed}")

                if ".py" not in loaded.get("language_extension_counts", {}):
                    failures.append("ext_counts")
                if "README.md" not in loaded.get("readme_candidates", []):
                    failures.append("readme_candidate")
                if "main.py" not in loaded.get("entrypoint_candidates", []):
                    failures.append("entrypoint_candidate")
                if "pyproject.toml" not in loaded.get("config_candidates", []):
                    failures.append("config_candidate")

                tiny = snapshot_repo(safe_resolve(repo), max_files=1)
                if not tiny.get("truncated") or tiny.get("tracked_file_count") != 1:
                    failures.append("max_files_cap")
                if not (tiny.get("limits") or {}).get("truncated"):
                    failures.append("limits_truncated_missing")

                # Dirty tracked sample (modify tracked file; do not use config).
                (repo / "main.py").write_text("print('dirty')\n", encoding="utf-8")
                dirty_snap = snapshot_repo(safe_resolve(repo), max_files=100)
                dgit = dirty_snap.get("git") or {}
                if dgit.get("dirty") is not True:
                    failures.append("dirty_not_detected")
                sample = dgit.get("dirty_files_sample") or []
                if "main.py" not in sample:
                    failures.append(f"dirty_sample_missing:{sample}")
                if (dgit.get("dirty_file_count") or 0) < 1:
                    failures.append("dirty_count_zero")

                # Re-check no config mutation after dirty snapshot path.
                keys_final = _local_git_config_keys(repo)
                for forbidden in ("user.name", "user.email"):
                    if forbidden in keys_final and forbidden not in keys_before:
                        failures.append(f"config_mutated_late:{forbidden}")

        # Path safety.
        try:
            safe_resolve(Path("a\x00b"))
            failures.append("nul_path_accepted")
        except ValueError:
            pass

    if failures:
        print("FAIL: " + "; ".join(failures))
        return 1
    print("PASS: repo_snapshot.py selftest")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    configure_stdio()
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.selftest:
        return run_selftest()

    repo_arg = args.repo_opt or args.repo_pos
    if not repo_arg:
        parser.error("repo is required unless --selftest")

    try:
        if args.max_files < 1:
            eprint("--max-files must be >= 1")
            return 2
        repo = safe_resolve(Path(repo_arg))
        out_path = safe_resolve(Path(args.out))
        data = snapshot_repo(repo, int(args.max_files))
        write_json(out_path, data)
        print(str(out_path))
        return 0
    except Exception as exc:
        eprint(f"repo_snapshot failed: {exc}")
        return 1


if __name__ == "__main__":
    os.environ.setdefault("NO_PROXY", "*")
    raise SystemExit(main())
