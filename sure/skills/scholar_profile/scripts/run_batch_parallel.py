#!/usr/bin/env python3
"""
Batch parallel scholar profile pipeline.
Runs the full pipeline for multiple scholars in parallel.
"""

import argparse
import csv
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple


@dataclass
class ScholarRow:
    index: int
    author: str
    dblp_url: str
    sources_file: str


def safe_name(author: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", (author or "").strip())
    name = name.strip("_")
    return name or "unknown_author"


def load_rows(csv_path: Path) -> List[ScholarRow]:
    rows: List[ScholarRow] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required = {"author", "dblp_url", "sources_file"}
        if not required.issubset(set(reader.fieldnames or [])):
            raise RuntimeError(
                f"CSV must contain headers: {sorted(required)}; got: {reader.fieldnames}"
            )
        for i, item in enumerate(reader, 1):
            author = (item.get("author") or "").strip()
            dblp_url = (item.get("dblp_url") or "").strip()
            sources_file = (item.get("sources_file") or "").strip()
            if not author or not dblp_url or not sources_file:
                continue
            rows.append(ScholarRow(i, author, dblp_url, sources_file))
    return rows


def run_cmd(cmd: List[str], env: Dict[str, str], log_fp) -> None:
    log_fp.write(f"\n$ {' '.join(cmd)}\n")
    log_fp.flush()
    proc = subprocess.run(
        cmd,
        env=env,
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed (exit={proc.returncode}): {' '.join(cmd)}")


def run_cmd_with_proxy_mode(
    step_name: str,
    base_cmd: List[str],
    env: Dict[str, str],
    log_fp,
    proxy_mode: str,
    proxy_args: List[str],
) -> None:
    if proxy_mode == "off":
        run_cmd(base_cmd, env, log_fp)
        return

    if proxy_mode == "on":
        if proxy_args:
            run_cmd(base_cmd + proxy_args, env, log_fp)
        else:
            run_cmd(base_cmd, env, log_fp)
        return

    if proxy_mode == "auto":
        if proxy_args:
            try:
                log_fp.write(f"[INFO] {step_name}: try with proxy first\n")
                log_fp.flush()
                run_cmd(base_cmd + proxy_args, env, log_fp)
                return
            except Exception:
                log_fp.write(f"[WARN] {step_name}: failed with proxy, retry without proxy\n")
                log_fp.flush()
        run_cmd(base_cmd, env, log_fp)
        return

    raise RuntimeError(f"Unknown proxy mode: {proxy_mode}")


def _sources_file_is_valid(path: Path) -> bool:
    """Check if sources file exists and contains at least one URL."""
    if not path.exists():
        return False
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
        return "http" in content
    except Exception:
        return False


def run_one_scholar(
    row: ScholarRow,
    project_root: Path,
    python_bin: str,
    env: Dict[str, str],
    max_papers: int,
    core_limit: int,
    var_limit: int,
    fallback_core_limit: int,
    strict_evidence: str,
    skip_existing: bool,
    proxy_mode: str,
    proxy_args: List[str],
    auto_discover_sources: bool = True,
) -> Tuple[str, bool, str]:
    safe = safe_name(row.author)
    outdir = project_root / "runs" / safe
    outdir.mkdir(parents=True, exist_ok=True)
    done_flag = outdir / "system_prompt.docx"
    log_path = outdir / "pipeline.log"
    sources_file = Path(row.sources_file)
    if not sources_file.is_absolute():
        sources_file = project_root / sources_file
    sources_file = sources_file.resolve()

    if skip_existing and done_flag.exists():
        return row.author, True, f"skip: {done_flag} already exists"

    # Auto-discover sources when file is missing or has no valid URLs
    if auto_discover_sources and not _sources_file_is_valid(sources_file):
        try:
            from build_scholar_sources import collect_and_save_sources
            tavily_key = (env.get("TAVILY_API_KEY") or "").strip()
            print(f"[INFO] Auto-discovering sources for {row.author}...")
            ok, msg = collect_and_save_sources(
                row.author, row.dblp_url, str(sources_file), tavily_key
            )
            if ok:
                print(f"[INFO] Sources discovered: {msg}")
            else:
                print(f"[WARN] Sources discovery failed: {msg}")
        except Exception as exc:
            print(f"[WARN] Sources discovery error for {row.author}: {exc}")

    if not sources_file.exists():
        return row.author, False, f"sources file not found: {sources_file}"

    mainline_json = outdir / "mainline_graph.json"
    digest_docx = outdir / "scholar_report.docx"
    digest_json = outdir / "paper_digest_scored.json"
    prompt_docx = outdir / "system_prompt.docx"

    with log_path.open("a", encoding="utf-8", errors="ignore") as log_fp:
        log_fp.write(
            f"\n===== [{time.strftime('%Y-%m-%d %H:%M:%S')}] Start {row.author} "
            f"(row={row.index}) =====\n"
        )
        log_fp.flush()
        try:
            run_cmd_with_proxy_mode(
                step_name="mainline",
                base_cmd=[
                    python_bin,
                    str(project_root / "scholar_mainline_builder.py"),
                    "--author",
                    row.author,
                    "--source-file",
                    str(sources_file),
                    "--dblp-url",
                    row.dblp_url,
                    "--output",
                    str(mainline_json),
                    "--api-key",
                    env["OPENAI_API_KEY"],
                    "--base-url",
                    env["LLM_BASE_URL"],
                    "--model",
                    env["LLM_MODEL"],
                ],
                env=env,
                log_fp=log_fp,
                proxy_mode=proxy_mode,
                proxy_args=proxy_args,
            )
            run_cmd_with_proxy_mode(
                step_name="digest",
                base_cmd=[
                    python_bin,
                    str(project_root / "scholar_author_digest.py"),
                    "--dblp-url",
                    row.dblp_url,
                    "--mainline-graph",
                    str(mainline_json),
                    "--max-papers",
                    str(max_papers),
                    "--output",
                    str(digest_docx),
                    "--json-output",
                    str(digest_json),
                ],
                env=env,
                log_fp=log_fp,
                proxy_mode=proxy_mode,
                proxy_args=proxy_args,
            )
            run_cmd_with_proxy_mode(
                step_name="prompt",
                base_cmd=[
                    python_bin,
                    str(project_root / "professor_system_prompt_builder.py"),
                    "--input-json",
                    str(digest_json),
                    "--mainline-graph",
                    str(mainline_json),
                    "--sources-file",
                    str(sources_file),
                    "--core-limit",
                    str(core_limit),
                    "--var-limit",
                    str(var_limit),
                    "--fallback-core-limit",
                    str(fallback_core_limit),
                    "--strict-evidence",
                    strict_evidence,
                    "--api-key",
                    env["OPENAI_API_KEY"],
                    "--base-url",
                    env["LLM_BASE_URL"],
                    "--model",
                    env["LLM_MODEL"],
                    "--output",
                    str(prompt_docx),
                ],
                env=env,
                log_fp=log_fp,
                proxy_mode=proxy_mode,
                proxy_args=proxy_args,
            )
        except Exception as exc:
            log_fp.write(f"[ERROR] {type(exc).__name__}: {exc}\n")
            log_fp.flush()
            return row.author, False, str(exc)

        log_fp.write(
            f"===== [{time.strftime('%Y-%m-%d %H:%M:%S')}] Done {row.author} =====\n"
        )
        log_fp.flush()
    return row.author, True, f"ok: {prompt_docx}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run scholar profile pipeline in parallel without SLURM array."
    )
    parser.add_argument("--csv", default="scholars.csv", help="CSV path with author,dblp_url,sources_file")
    parser.add_argument("--max-workers", type=int, default=16, help="Parallel scholars, default 16")
    parser.add_argument("--python-bin", default=sys.executable, help="Python executable used for child scripts")
    parser.add_argument("--project-root", default="", help="Project root directory (default: script dir)")
    parser.add_argument("--max-papers", type=int, default=1000, help="Forwarded to scholar_author_digest.py")
    parser.add_argument("--core-limit", type=int, default=20, help="Forwarded to professor_system_prompt_builder.py")
    parser.add_argument("--var-limit", type=int, default=8, help="Forwarded to professor_system_prompt_builder.py")
    parser.add_argument(
        "--fallback-core-limit",
        type=int,
        default=8,
        help="Forwarded to professor_system_prompt_builder.py",
    )
    parser.add_argument(
        "--strict-evidence",
        default="false",
        help="Forwarded to professor_system_prompt_builder.py",
    )
    parser.add_argument(
        "--skip-existing",
        default="true",
        help="Skip scholar if runs/<author>/system_prompt.docx exists (true/false)",
    )
    parser.add_argument(
        "--auto-discover-sources",
        default="true",
        help="Auto-discover sources when sources file is missing or empty (true/false)",
    )
    args = parser.parse_args()

    project_root = Path(args.project_root).expanduser().resolve() if args.project_root else Path(__file__).resolve().parent
    csv_path = Path(args.csv).expanduser()
    if not csv_path.is_absolute():
        csv_path = project_root / csv_path
    csv_path = csv_path.resolve()
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    env = os.environ.copy()
    env["OPENAI_API_KEY"] = api_key
    env["LLM_BASE_URL"] = (os.getenv("LLM_BASE_URL") or "http://58.210.177.113:8888/v1").strip()
    env["LLM_MODEL"] = (os.getenv("LLM_MODEL") or "mimo-v2-flash").strip()
    proxy_mode = (os.getenv("PROXY_MODE") or "auto").strip().lower()
    http_proxy_value = (
        os.getenv("PIPELINE_HTTP_PROXY")
        or os.getenv("HTTP_PROXY")
        or os.getenv("http_proxy")
        or ""
    ).strip()
    https_proxy_value = (
        os.getenv("PIPELINE_HTTPS_PROXY")
        or os.getenv("HTTPS_PROXY")
        or os.getenv("https_proxy")
        or ""
    ).strip()
    proxy_args: List[str] = []
    if http_proxy_value:
        proxy_args += ["--http-proxy", http_proxy_value]
    if https_proxy_value:
        proxy_args += ["--https-proxy", https_proxy_value]

    max_workers = max(1, int(args.max_workers))
    skip_existing = str(args.skip_existing).strip().lower() in {"1", "true", "yes", "y", "on"}
    auto_discover = str(args.auto_discover_sources).strip().lower() in {"1", "true", "yes", "y", "on"}

    # Tavily API key (for auto-discovering sources)
    tavily_key = (os.getenv("TAVILY_API_KEY") or "").strip()
    if tavily_key:
        env["TAVILY_API_KEY"] = tavily_key

    rows = load_rows(csv_path)
    if not rows:
        raise RuntimeError(f"No valid rows found in CSV: {csv_path}")

    print(f"[INFO] Project root: {project_root}")
    print(f"[INFO] CSV: {csv_path}")
    print(f"[INFO] Scholars: {len(rows)}")
    print(f"[INFO] Parallel workers: {max_workers}")
    print(f"[INFO] skip_existing: {skip_existing}")
    print(f"[INFO] auto_discover_sources: {auto_discover}")
    print(f"[INFO] LLM_BASE_URL: {env['LLM_BASE_URL']}")
    print(f"[INFO] LLM_MODEL: {env['LLM_MODEL']}")
    print(f"[INFO] PROXY_MODE: {proxy_mode}")
    print(f"[INFO] TAVILY_API_KEY: {'set' if tavily_key else 'not set'}")
    print(f"[INFO] Proxy args configured: {'yes' if proxy_args else 'no'}")

    lock = threading.Lock()
    failed: List[Tuple[str, str]] = []
    done_count = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(
                run_one_scholar,
                row,
                project_root,
                args.python_bin,
                env,
                args.max_papers,
                args.core_limit,
                args.var_limit,
                args.fallback_core_limit,
                args.strict_evidence,
                skip_existing,
                proxy_mode,
                proxy_args,
                auto_discover,
            )
            for row in rows
        ]

        for fut in as_completed(futures):
            author, ok, msg = fut.result()
            with lock:
                done_count += 1
                prefix = "OK" if ok else "FAIL"
                print(f"[{prefix}] ({done_count}/{len(rows)}) {author} -> {msg}")
                if not ok:
                    failed.append((author, msg))

    print("\n[SUMMARY]")
    print(f"Total: {len(rows)}")
    print(f"Failed: {len(failed)}")
    if failed:
        for author, reason in failed:
            print(f"- {author}: {reason}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
