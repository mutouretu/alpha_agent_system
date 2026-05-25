from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


FINAL_CANDIDATE_NAMES = ["final_candidates.csv", "review_candidates.csv", "tracking_target_snapshot.csv", "candidates.csv"]
PHASE1_POOL_NAMES = ["phase1_pool.csv", "phase1_pool_anchor.csv", "phase1_top_anchor.csv"]
PHASE2_SCORE_NAMES = ["phase2_scores.csv", "phase2_pool_scores.csv", "tracking_target_snapshot.csv"]
PHASE1_HIT_NAMES = ["phase1_hits.csv", "phase1_daily_top_anchor.csv"]
FAST_PATH_DIR_RE = re.compile(r"target_(\d{4}-\d{2}-\d{2})_from_(\d{4}-\d{2}-\d{2})_top(\d+)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare fast path and decomposed Type-N two-phase outputs.")
    parser.add_argument("--date", required=True, help="Trade date, for example 2026-05-12.")
    parser.add_argument("--type-n-root", required=True, help="Path to type_n_search.")
    parser.add_argument("--alpha-root", default=None, help="Path to alpha_agent_system. Defaults to this project root.")
    parser.add_argument("--output-dir", default=None, help="Output directory. Defaults to runs/validation/{date}.")
    parser.add_argument("--comparison-mode", choices=["compat", "production"], default="compat")
    parser.add_argument("--target-date", default=None)
    parser.add_argument("--anchor-start-date", default=None)
    parser.add_argument("--anchor-lookback-days", type=int, default=None)
    parser.add_argument("--phase1-top-n", type=int, default=None)
    parser.add_argument("--reviewer-config", default=None)
    parser.add_argument("--final-merge-config", default=None)
    parser.add_argument("--sort-fields", default=None, help="Comma-separated final sort fields.")
    parser.add_argument("--decomposed-run-dir", default=None, help="Use a specific decomposed output directory.")
    args = parser.parse_args()

    alpha_root = Path(args.alpha_root).expanduser().resolve() if args.alpha_root else Path(__file__).resolve().parents[1]
    type_n_root = Path(args.type_n_root).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else alpha_root / "runs" / "validation" / args.date
    output_dir.mkdir(parents=True, exist_ok=True)

    decomposed_run_dir = Path(args.decomposed_run_dir).expanduser().resolve() if args.decomposed_run_dir else None
    result = compare_modes(
        date=args.date,
        type_n_root=type_n_root,
        alpha_root=alpha_root,
        output_dir=output_dir,
        comparison_mode=args.comparison_mode,
        target_date=args.target_date,
        anchor_start_date=args.anchor_start_date,
        anchor_lookback_days=args.anchor_lookback_days,
        phase1_top_n=args.phase1_top_n,
        reviewer_config=args.reviewer_config,
        final_merge_config=args.final_merge_config,
        sort_fields=args.sort_fields,
        decomposed_run_dir=decomposed_run_dir,
    )
    result_path = output_dir / "two_phase_mode_comparison.json"
    report_path = output_dir / "two_phase_mode_comparison.md"
    result["result_path"] = str(result_path)
    result["report_path"] = str(report_path)
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    report_path.write_text(render_report(result), encoding="utf-8")

    print(json.dumps({"ok": result["ok"], "status": result["status"], "result_path": str(result_path), "report_path": str(report_path)}, ensure_ascii=False))


def compare_modes(
    *,
    date: str,
    type_n_root: Path,
    alpha_root: Path,
    output_dir: Path,
    comparison_mode: str,
    target_date: str | None = None,
    anchor_start_date: str | None = None,
    anchor_lookback_days: int | None = None,
    phase1_top_n: int | None = None,
    reviewer_config: str | None = None,
    final_merge_config: str | None = None,
    sort_fields: str | None = None,
    decomposed_run_dir: Path | None = None,
) -> dict[str, Any]:
    warnings: list[str] = []
    errors: list[str] = []

    fast_dir = _discover_fast_path_dir(type_n_root, date)
    fast_params = _parse_fast_path_params(fast_dir)
    config = _build_comparison_config(
        date=date,
        comparison_mode=comparison_mode,
        fast_params=fast_params,
        target_date=target_date,
        anchor_start_date=anchor_start_date,
        anchor_lookback_days=anchor_lookback_days,
        phase1_top_n=phase1_top_n,
        reviewer_config=reviewer_config,
        final_merge_config=final_merge_config,
        sort_fields=sort_fields,
    )
    decomposed_run = None
    if decomposed_run_dir is not None:
        decomposed_dir = decomposed_run_dir
    elif comparison_mode == "compat":
        decomposed_dir, decomposed_run = _ensure_compat_decomposed_run(
            type_n_root=type_n_root,
            alpha_root=alpha_root,
            output_dir=output_dir,
            config=config,
            warnings=warnings,
            errors=errors,
        )
    else:
        decomposed_dir = _discover_decomposed_dir(alpha_root, date)
    fast = _load_mode_outputs("fast_path", fast_dir, warnings)
    decomposed = _load_mode_outputs("decomposed_path", decomposed_dir, warnings)

    comparison = _compare_final_candidates(fast, decomposed, warnings)
    comparison["phase1_pool_count"] = {
        "fast_path": _row_count(fast.get("phase1_pool")),
        "decomposed_path": _row_count(decomposed.get("phase1_pool")),
        "delta": _count_delta(fast.get("phase1_pool"), decomposed.get("phase1_pool")),
    }
    comparison["phase2_scores_count"] = {
        "fast_path": _row_count(fast.get("phase2_scores")),
        "decomposed_path": _row_count(decomposed.get("phase2_scores")),
        "delta": _count_delta(fast.get("phase2_scores"), decomposed.get("phase2_scores")),
    }
    phase_diagnostics = _build_phase_diagnostics(fast, decomposed, warnings, config)
    parameter_alignment = _build_parameter_alignment(
        config=config,
        fast=fast,
        decomposed=decomposed,
        phase_diagnostics=phase_diagnostics,
    )
    for mismatch in parameter_alignment.get("parameter_mismatch", []):
        warnings.append(f"parameter_mismatch: {mismatch}")
    if comparison_mode == "production" and parameter_alignment.get("parameter_mismatch"):
        warnings.append(
            "production mode allows differences from fast path; likely causes include longer lookback, reviewer_config, or final scoring/sorting"
        )

    if not fast["final_exists"]:
        warnings.append("fast path final candidates were not found")
    elif not fast["final_candidates_csv_exists"]:
        warnings.append(f"fast path final_candidates.csv was not found; compared using {fast['selected_final_filename']}")
    if not decomposed["final_exists"]:
        warnings.append("decomposed path final candidates were not found")
    elif not decomposed["final_candidates_csv_exists"]:
        warnings.append(
            f"decomposed path final_candidates.csv was not found; compared using {decomposed['selected_final_filename']}"
        )

    for label in ["final_candidate_count", "phase1_pool_count", "phase2_scores_count"]:
        delta = comparison.get(label, {}).get("delta")
        if delta not in (None, 0):
            warnings.append(f"{label} differs between modes: delta={delta}")

    status = "success" if not warnings and not errors else "warning" if not errors else "failed"
    return {
        "ok": not errors,
        "status": status,
        "date": date,
        "comparison_mode": comparison_mode,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "type_n_root": str(type_n_root),
        "alpha_root": str(alpha_root),
        "comparison_config": config,
        "parameter_alignment": parameter_alignment,
        "decomposed_run": decomposed_run,
        "fast_path": _strip_dataframes(fast),
        "decomposed_path": _strip_dataframes(decomposed),
        "comparison": comparison,
        "phase_diagnostics": phase_diagnostics,
        "warnings": list(dict.fromkeys(warnings)),
        "errors": errors,
    }


def _parse_fast_path_params(fast_dir: Path | None) -> dict[str, Any]:
    if fast_dir is None:
        return {}
    match = FAST_PATH_DIR_RE.search(fast_dir.name)
    if not match:
        return {}
    target_date, anchor_start_date, top_n = match.groups()
    return {
        "target_date": target_date,
        "anchor_start_date": anchor_start_date,
        "phase1_top_n": int(top_n),
        "source": "fast_path_dir_name",
    }


def _build_comparison_config(
    *,
    date: str,
    comparison_mode: str,
    fast_params: dict[str, Any],
    target_date: str | None,
    anchor_start_date: str | None,
    anchor_lookback_days: int | None,
    phase1_top_n: int | None,
    reviewer_config: str | None,
    final_merge_config: str | None,
    sort_fields: str | None,
) -> dict[str, Any]:
    parsed_sort_fields = _parse_sort_fields(sort_fields)
    if comparison_mode == "compat":
        resolved_target_date = target_date or fast_params.get("target_date") or date
        resolved_anchor_start_date = anchor_start_date or fast_params.get("anchor_start_date")
        resolved_phase1_top_n = phase1_top_n or fast_params.get("phase1_top_n") or 20
        resolved_reviewer_config = "none" if reviewer_config is None else reviewer_config
        resolved_final_merge_config = final_merge_config or "fast_compatible"
        resolved_sort_fields = parsed_sort_fields or ["phase2_score_mean", "phase2_score_min"]
        resolved_anchor_lookback_days = anchor_lookback_days or 20
    else:
        resolved_target_date = target_date or date
        resolved_anchor_start_date = anchor_start_date
        resolved_phase1_top_n = phase1_top_n or 20
        resolved_reviewer_config = reviewer_config or "ma120_trend_soft"
        resolved_final_merge_config = final_merge_config or "default"
        resolved_sort_fields = parsed_sort_fields or ["final_score", "adjusted_phase2_score", "phase2_score_mean"]
        resolved_anchor_lookback_days = anchor_lookback_days or 20

    return {
        "comparison_mode": comparison_mode,
        "target_date": resolved_target_date,
        "anchor_start_date": resolved_anchor_start_date,
        "anchor_lookback_days": resolved_anchor_lookback_days,
        "phase1_top_n": resolved_phase1_top_n,
        "reviewer_config_fast": "none",
        "reviewer_config_decomposed": resolved_reviewer_config,
        "final_merge_config_decomposed": resolved_final_merge_config,
        "final_sort_fields_fast": ["phase2_score_mean", "phase2_score_min"],
        "final_sort_fields_decomposed": resolved_sort_fields,
        "anchor_selection_mode": "anchor_start_date" if resolved_anchor_start_date else "anchor_lookback_days",
        "fast_path_params": fast_params,
    }


def _ensure_compat_decomposed_run(
    *,
    type_n_root: Path,
    alpha_root: Path,
    output_dir: Path,
    config: dict[str, Any],
    warnings: list[str],
    errors: list[str],
) -> tuple[Path, dict[str, Any] | None]:
    run_dir = output_dir / "decomposed_compat"
    if _compat_run_is_current(run_dir, config):
        return run_dir, {"ok": True, "status": "reused", "run_dir": str(run_dir)}

    run_dir.mkdir(parents=True, exist_ok=True)
    python_exe = _select_project_python(type_n_root)
    script = type_n_root / "scripts" / "run_type_n_task.py"
    raw_daily_dir = _default_raw_daily_dir(type_n_root, alpha_root, warnings)
    if not script.exists():
        errors.append(f"type_n_search task CLI not found: {script}")
        return run_dir, {"ok": False, "status": "missing_cli", "run_dir": str(run_dir)}

    commands = [
        [
            python_exe,
            str(script),
            "phase1-scan",
            "--target-date",
            config["target_date"],
            "--anchor-lookback-days",
            str(config["anchor_lookback_days"]),
            "--phase1-top-n",
            str(config["phase1_top_n"]),
            "--raw-daily-dir",
            str(raw_daily_dir),
            "--output-path",
            str(run_dir / "phase1_hits.csv"),
            "--status-path",
            str(run_dir / "phase1_status.json"),
        ],
        [
            python_exe,
            str(script),
            "build-pool",
            "--phase1-hits-path",
            str(run_dir / "phase1_hits.csv"),
            "--target-date",
            config["target_date"],
            "--output-path",
            str(run_dir / "phase1_pool.csv"),
            "--status-path",
            str(run_dir / "pool_status.json"),
        ],
        [
            python_exe,
            str(script),
            "phase2-filter",
            "--target-date",
            config["target_date"],
            "--phase1-pool-path",
            str(run_dir / "phase1_pool.csv"),
            "--raw-daily-dir",
            str(raw_daily_dir),
            "--reviewer-config",
            config["reviewer_config_decomposed"],
            "--output-path",
            str(run_dir / "phase2_scores.csv"),
            "--status-path",
            str(run_dir / "phase2_status.json"),
        ],
        [
            python_exe,
            str(script),
            "merge-final",
            "--phase1-pool-path",
            str(run_dir / "phase1_pool.csv"),
            "--phase2-scores-path",
            str(run_dir / "phase2_scores.csv"),
            "--final-merge-config",
            config["final_merge_config_decomposed"],
            "--sort-fields",
            ",".join(config["final_sort_fields_decomposed"]),
            "--output-path",
            str(run_dir / "final_candidates.csv"),
            "--status-path",
            str(run_dir / "final_status.json"),
        ],
        [
            python_exe,
            str(script),
            "report",
            "--phase1-hits-path",
            str(run_dir / "phase1_hits.csv"),
            "--phase1-pool-path",
            str(run_dir / "phase1_pool.csv"),
            "--phase2-scores-path",
            str(run_dir / "phase2_scores.csv"),
            "--final-candidates-path",
            str(run_dir / "final_candidates.csv"),
            "--status-path",
            str(run_dir / "phase1_status.json"),
            "--status-path",
            str(run_dir / "pool_status.json"),
            "--status-path",
            str(run_dir / "phase2_status.json"),
            "--status-path",
            str(run_dir / "final_status.json"),
            "--output-path",
            str(run_dir / "type_n_two_phase_report.md"),
        ],
    ]
    if config.get("anchor_start_date"):
        phase1_cmd = commands[0]
        phase1_cmd.extend(["--anchor-start-date", str(config["anchor_start_date"])])

    steps: list[dict[str, Any]] = []
    for command in commands:
        completed = subprocess.run(command, cwd=type_n_root, check=False, capture_output=True, text=True)
        step = {
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout[-2000:],
            "stderr": completed.stderr[-2000:],
        }
        steps.append(step)
        if completed.returncode != 0:
            errors.append(f"compat decomposed task failed: {' '.join(command[:4])}")
            return run_dir, {"ok": False, "status": "failed", "run_dir": str(run_dir), "steps": steps}

    return run_dir, {"ok": True, "status": "ran", "run_dir": str(run_dir), "steps": steps}


def _compat_run_is_current(run_dir: Path, config: dict[str, Any]) -> bool:
    if not (run_dir / "final_candidates.csv").exists():
        return False
    status = _read_json(run_dir / "phase1_status.json")
    if str(status.get("target_date")) != str(config["target_date"]):
        return False
    try:
        current_top_n = int(status.get("phase1_top_n", -1))
    except (TypeError, ValueError):
        current_top_n = -1
    if current_top_n != int(config["phase1_top_n"]):
        return False
    anchor_dates = [str(date) for date in status.get("anchor_dates", [])]
    if config.get("anchor_start_date") and (not anchor_dates or anchor_dates[0] != str(config["anchor_start_date"])):
        return False
    final_status = _read_json(run_dir / "final_status.json")
    status_sort = final_status.get("sort_fields") or []
    return list(status_sort) == list(config["final_sort_fields_decomposed"])


def _build_parameter_alignment(
    *,
    config: dict[str, Any],
    fast: dict[str, Any],
    decomposed: dict[str, Any],
    phase_diagnostics: dict[str, Any],
) -> dict[str, Any]:
    anchor = phase_diagnostics.get("anchor_dates", {})
    fast_dates = anchor.get("fast_anchor_dates", [])
    dec_dates = anchor.get("decomposed_anchor_dates", [])
    anchor_dates_equal = fast_dates == dec_dates
    sorting = phase_diagnostics.get("sorting_fields", {})
    fast_sort = sorting.get("fast_path", {}).get("selected_sort_fields", [])
    dec_sort = sorting.get("decomposed_path", {}).get("selected_sort_fields", [])
    mismatches = []
    if not anchor_dates_equal:
        mismatches.append("anchor_dates differ")
    if config.get("reviewer_config_fast") != config.get("reviewer_config_decomposed"):
        mismatches.append("reviewer_config differs")
    if fast_sort != dec_sort:
        mismatches.append("final_sort_fields differ")
    return {
        "comparison_mode": config["comparison_mode"],
        "anchor_selection_mode": config["anchor_selection_mode"],
        "fast_anchor_dates": fast_dates,
        "decomposed_anchor_dates": dec_dates,
        "anchor_dates_equal": anchor_dates_equal,
        "reviewer_config_fast": config.get("reviewer_config_fast"),
        "reviewer_config_decomposed": config.get("reviewer_config_decomposed"),
        "final_sort_fields_fast": fast_sort or config.get("final_sort_fields_fast"),
        "final_sort_fields_decomposed": dec_sort or config.get("final_sort_fields_decomposed"),
        "parameter_mismatch": mismatches,
        "fast_path_dir": fast.get("directory"),
        "decomposed_path_dir": decomposed.get("directory"),
    }


def _discover_fast_path_dir(type_n_root: Path, date: str) -> Path | None:
    candidates = [
        type_n_root / "outputs" / "predictions" / "type_n_two_phase" / date,
        type_n_root / "outputs" / "predictions" / "two_phase" / date,
        type_n_root / "outputs" / "predictions" / "two_phase" / f"target_{date}",
        type_n_root / "outputs" / "predictions" / "phase_tracking" / f"target_{date}_from_2026-04-27_top20",
        type_n_root / "outputs" / "predictions" / "phase_tracking",
    ]
    for candidate in candidates:
        if _contains_any(candidate, FINAL_CANDIDATE_NAMES):
            return candidate

    matches = []
    predictions_root = type_n_root / "outputs" / "predictions"
    if predictions_root.exists():
        for path in predictions_root.rglob("*"):
            if path.is_dir() and (date in str(path)) and _contains_any(path, FINAL_CANDIDATE_NAMES):
                matches.append(path)
    return _latest_dir(matches)


def _discover_decomposed_dir(alpha_root: Path, date: str) -> Path | None:
    candidates = [
        alpha_root / "runs" / "data_mining_group" / date / "search",
        alpha_root / "runs" / "data_mining_group" / f"{date}-agent-segment-test" / "search",
        alpha_root / "runs" / "data_mining_group" / date / "search_segment_test",
    ]
    for candidate in candidates:
        if _contains_any(candidate, FINAL_CANDIDATE_NAMES):
            return candidate

    root = alpha_root / "runs" / "data_mining_group"
    matches = []
    if root.exists():
        for path in root.rglob("final_candidates.csv"):
            if date in str(path):
                matches.append(path.parent)
    return _latest_dir(matches)


def _load_mode_outputs(name: str, directory: Path | None, warnings: list[str]) -> dict[str, Any]:
    final_path = _find_first_existing(directory, FINAL_CANDIDATE_NAMES)
    phase1_hits_path = _find_first_existing(directory, PHASE1_HIT_NAMES)
    phase1_pool_path = _find_first_existing(directory, PHASE1_POOL_NAMES)
    phase2_scores_path = _find_first_existing(directory, PHASE2_SCORE_NAMES)
    final_df = _read_csv(final_path, warnings, f"{name}.final_candidates") if final_path else None
    phase1_hits_df = _read_csv(phase1_hits_path, warnings, f"{name}.phase1_hits") if phase1_hits_path else None
    phase1_pool_df = _read_csv(phase1_pool_path, warnings, f"{name}.phase1_pool") if phase1_pool_path else None
    phase2_scores_df = _read_csv(phase2_scores_path, warnings, f"{name}.phase2_scores") if phase2_scores_path else None

    return {
        "name": name,
        "directory": str(directory) if directory else None,
        "final_path": str(final_path) if final_path else None,
        "final_exists": final_path is not None,
        "final_candidates_csv_exists": bool(directory and (directory / "final_candidates.csv").exists()),
        "selected_final_filename": final_path.name if final_path else None,
        "phase1_hits_path": str(phase1_hits_path) if phase1_hits_path else None,
        "selected_phase1_hits_filename": phase1_hits_path.name if phase1_hits_path else None,
        "phase1_pool_path": str(phase1_pool_path) if phase1_pool_path else None,
        "phase2_scores_path": str(phase2_scores_path) if phase2_scores_path else None,
        "final": final_df,
        "phase1_hits": phase1_hits_df,
        "phase1_pool": phase1_pool_df,
        "phase2_scores": phase2_scores_df,
        "final_summary": _summarize_df(final_df),
        "phase1_hits_summary": _summarize_df(phase1_hits_df),
        "phase1_pool_summary": _summarize_df(phase1_pool_df),
        "phase2_scores_summary": _summarize_df(phase2_scores_df),
    }


def _build_phase_diagnostics(
    fast: dict[str, Any],
    decomposed: dict[str, Any],
    warnings: list[str],
    config: dict[str, Any],
) -> dict[str, Any]:
    fast_hits = _with_anchor_date(fast.get("phase1_hits"))
    dec_hits = _with_anchor_date(decomposed.get("phase1_hits"))
    fast_pool = fast.get("phase1_pool")
    dec_pool = decomposed.get("phase1_pool")
    fast_phase2 = _with_ts_code(fast.get("phase2_scores"))
    dec_phase2 = _with_ts_code(decomposed.get("phase2_scores"))

    anchor_diag = _diagnose_anchor_dates(fast_hits, dec_hits)
    phase1_top_n = _diagnose_phase1_top_n(fast_hits, dec_hits)
    pool_diag = _diagnose_pool_overlap(fast_pool, dec_pool)
    phase2_input_diag = {
        "fast_path": _diagnose_phase2_inputs(fast_pool, fast_phase2),
        "decomposed_path": _diagnose_phase2_inputs(dec_pool, dec_phase2),
    }
    sorting_diag = {
        "fast_path": _diagnose_sorting_fields(fast.get("final"), preferred=config["final_sort_fields_fast"]),
        "decomposed_path": _diagnose_sorting_fields(
            decomposed.get("final"),
            preferred=config["final_sort_fields_decomposed"],
        ),
    }

    if anchor_diag["only_in_fast"] or anchor_diag["only_in_decomposed"]:
        warnings.append(
            "anchor_dates differ: "
            f"only_in_fast={anchor_diag['only_in_fast']}, only_in_decomposed={anchor_diag['only_in_decomposed']}"
        )
    if not phase1_top_n["fast_path"].get("all_anchor_counts_equal_20", False):
        warnings.append("fast path Phase1 hits are not consistently top20 per anchor_date")
    if not phase1_top_n["decomposed_path"].get("all_anchor_counts_equal_20", False):
        warnings.append("decomposed path Phase1 hits are not consistently top20 per anchor_date")
    if phase2_input_diag["decomposed_path"].get("phase2_not_in_pool_count", 0) > 0:
        warnings.append("decomposed phase2_scores contains ts_code values not present in phase1_pool")
    if pool_diag.get("pool_count_delta") not in (None, 0):
        warnings.append(f"phase1_pool count delta detected: {pool_diag.get('pool_count_delta')}")
    if sorting_diag["fast_path"].get("selected_sort_fields") != sorting_diag["decomposed_path"].get("selected_sort_fields"):
        warnings.append(
            "sorting fields differ: "
            f"fast={sorting_diag['fast_path'].get('selected_sort_fields')}, "
            f"decomposed={sorting_diag['decomposed_path'].get('selected_sort_fields')}"
        )

    return {
        "phase1_hits_files": {
            "fast_path": fast.get("selected_phase1_hits_filename"),
            "decomposed_path": decomposed.get("selected_phase1_hits_filename"),
        },
        "anchor_dates": anchor_diag,
        "phase1_top_n": phase1_top_n,
        "phase1_pool": pool_diag,
        "phase2_inputs": phase2_input_diag,
        "sorting_fields": sorting_diag,
    }


def _compare_final_candidates(fast: dict[str, Any], decomposed: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    fast_df = fast.get("final")
    dec_df = decomposed.get("final")
    result: dict[str, Any] = {
        "final_candidate_count": {
            "fast_path": _row_count(fast_df),
            "decomposed_path": _row_count(dec_df),
            "delta": _count_delta(fast_df, dec_df),
        },
        "top30_ts_code_overlap": None,
        "final_score_diff": None,
        "missing_columns": {},
        "duplicate_ts_code": {
            "fast_path": _duplicate_count(fast_df, "ts_code"),
            "decomposed_path": _duplicate_count(dec_df, "ts_code"),
        },
        "nan_columns": {
            "fast_path": _nan_summary(fast_df),
            "decomposed_path": _nan_summary(dec_df),
        },
    }
    if fast_df is None or dec_df is None:
        return result

    fast_cols = set(fast_df.columns)
    dec_cols = set(dec_df.columns)
    result["missing_columns"] = {
        "missing_in_fast_path": sorted(dec_cols - fast_cols),
        "missing_in_decomposed_path": sorted(fast_cols - dec_cols),
    }

    if "ts_code" in fast_cols and "ts_code" in dec_cols:
        fast_top = fast_df["ts_code"].dropna().astype(str).head(30).tolist()
        dec_top = dec_df["ts_code"].dropna().astype(str).head(30).tolist()
        overlap = sorted(set(fast_top) & set(dec_top))
        ratio = len(overlap) / 30 if fast_top or dec_top else 0.0
        result["top30_ts_code_overlap"] = {
            "count": len(overlap),
            "ratio": ratio,
            "overlap_ts_codes": overlap,
            "fast_only": [code for code in fast_top if code not in overlap],
            "decomposed_only": [code for code in dec_top if code not in overlap],
        }
        if len(overlap) < 20:
            warnings.append(f"Top 30 overlap is low: {len(overlap)}/30")

    if "ts_code" in fast_cols and "ts_code" in dec_cols and "final_score" in fast_cols and "final_score" in dec_cols:
        merged = fast_df[["ts_code", "final_score"]].merge(
            dec_df[["ts_code", "final_score"]],
            on="ts_code",
            how="inner",
            suffixes=("_fast", "_decomposed"),
        )
        if not merged.empty:
            diff = (pd.to_numeric(merged["final_score_fast"], errors="coerce") - pd.to_numeric(merged["final_score_decomposed"], errors="coerce")).abs()
            result["final_score_diff"] = {
                "matched_count": int(len(merged)),
                "mean_abs_diff": float(diff.mean(skipna=True)),
                "max_abs_diff": float(diff.max(skipna=True)),
                "median_abs_diff": float(diff.median(skipna=True)),
            }

    return result


def _with_anchor_date(df: pd.DataFrame | None) -> pd.DataFrame | None:
    if df is None:
        return None
    out = df.copy()
    if "anchor_date" not in out.columns:
        if "asof_date" in out.columns:
            out["anchor_date"] = out["asof_date"].astype(str)
        elif "target_date" in out.columns:
            out["anchor_date"] = out["target_date"].astype(str)
    if "anchor_date" in out.columns:
        out["anchor_date"] = out["anchor_date"].astype(str)
    return out


def _with_ts_code(df: pd.DataFrame | None) -> pd.DataFrame | None:
    if df is None or "ts_code" not in df.columns:
        return df
    out = df.copy()
    out["ts_code"] = out["ts_code"].astype(str)
    return out


def _diagnose_anchor_dates(fast_hits: pd.DataFrame | None, dec_hits: pd.DataFrame | None) -> dict[str, Any]:
    fast_dates = _anchor_dates(fast_hits)
    dec_dates = _anchor_dates(dec_hits)
    return {
        "fast_anchor_dates": fast_dates,
        "decomposed_anchor_dates": dec_dates,
        "only_in_fast": sorted(set(fast_dates) - set(dec_dates)),
        "only_in_decomposed": sorted(set(dec_dates) - set(fast_dates)),
        "overlap": sorted(set(fast_dates) & set(dec_dates)),
    }


def _diagnose_phase1_top_n(fast_hits: pd.DataFrame | None, dec_hits: pd.DataFrame | None) -> dict[str, Any]:
    return {
        "fast_path": _phase1_top_n_summary(fast_hits),
        "decomposed_path": _phase1_top_n_summary(dec_hits),
    }


def _phase1_top_n_summary(df: pd.DataFrame | None) -> dict[str, Any]:
    if df is None or "anchor_date" not in df.columns:
        return {
            "readable": df is not None,
            "count_by_anchor_date": {},
            "all_anchor_counts_equal_20": False,
            "min_count": None,
            "max_count": None,
            "suspect_not_grouped_top_n": None,
        }
    counts = {str(k): int(v) for k, v in df.groupby("anchor_date").size().sort_index().items()}
    values = list(counts.values())
    all_equal_20 = bool(values) and all(value == 20 for value in values)
    return {
        "readable": True,
        "count_by_anchor_date": counts,
        "all_anchor_counts_equal_20": all_equal_20,
        "min_count": min(values) if values else None,
        "max_count": max(values) if values else None,
        "suspect_not_grouped_top_n": bool(values) and (len(counts) <= 1 and max(values) > 20),
    }


def _diagnose_pool_overlap(fast_pool: pd.DataFrame | None, dec_pool: pd.DataFrame | None) -> dict[str, Any]:
    fast_codes = _ts_code_list(fast_pool)
    dec_codes = _ts_code_list(dec_pool)
    overlap = sorted(set(fast_codes) & set(dec_codes))
    return {
        "fast_count": len(fast_codes),
        "decomposed_count": len(dec_codes),
        "pool_count_delta": len(dec_codes) - len(fast_codes) if fast_pool is not None and dec_pool is not None else None,
        "overlap_count": len(overlap),
        "overlap_ratio_vs_fast": len(overlap) / len(set(fast_codes)) if fast_codes else None,
        "overlap_ratio_vs_decomposed": len(overlap) / len(set(dec_codes)) if dec_codes else None,
        "fast_only": sorted(set(fast_codes) - set(dec_codes)),
        "decomposed_only": sorted(set(dec_codes) - set(fast_codes)),
    }


def _diagnose_phase2_inputs(pool_df: pd.DataFrame | None, phase2_df: pd.DataFrame | None) -> dict[str, Any]:
    pool_codes = set(_ts_code_list(pool_df))
    phase2_codes = set(_ts_code_list(phase2_df))
    not_in_pool = sorted(phase2_codes - pool_codes)
    pool_not_scored = sorted(pool_codes - phase2_codes)
    return {
        "pool_count": len(pool_codes),
        "phase2_scores_count": len(phase2_codes),
        "phase2_subset_of_pool": not bool(not_in_pool) if phase2_df is not None and pool_df is not None else None,
        "phase2_not_in_pool_count": len(not_in_pool),
        "phase2_not_in_pool": not_in_pool,
        "pool_not_scored_count": len(pool_not_scored),
        "pool_not_scored": pool_not_scored,
    }


def _diagnose_sorting_fields(df: pd.DataFrame | None, preferred: list[str]) -> dict[str, Any]:
    if df is None:
        return {"available_fields": [], "selected_sort_fields": [], "missing_preferred_fields": preferred}
    available = [field for field in preferred if field in df.columns]
    missing = [field for field in preferred if field not in df.columns]
    monotonic: dict[str, bool] = {}
    for field in available:
        values = pd.to_numeric(df[field], errors="coerce")
        monotonic[field] = bool(values.dropna().is_monotonic_decreasing)
    return {
        "preferred_order": preferred,
        "available_fields": available,
        "missing_preferred_fields": missing,
        "selected_sort_fields": available,
        "monotonic_decreasing": monotonic,
    }


def _anchor_dates(df: pd.DataFrame | None) -> list[str]:
    if df is None or "anchor_date" not in df.columns:
        return []
    return sorted(df["anchor_date"].dropna().astype(str).unique().tolist())


def _ts_code_list(df: pd.DataFrame | None) -> list[str]:
    if df is None or "ts_code" not in df.columns:
        return []
    return df["ts_code"].dropna().astype(str).tolist()


def _contains_any(directory: Path, names: list[str]) -> bool:
    return directory.exists() and directory.is_dir() and any((directory / name).exists() for name in names)


def _find_first_existing(directory: Path | None, names: list[str]) -> Path | None:
    if directory is None:
        return None
    for name in names:
        path = directory / name
        if path.exists():
            return path
    return None


def _latest_dir(paths: list[Path]) -> Path | None:
    if not paths:
        return None
    return sorted(paths, key=lambda path: path.stat().st_mtime, reverse=True)[0]


def _read_csv(path: Path, warnings: list[str], label: str) -> pd.DataFrame | None:
    try:
        return pd.read_csv(path)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"{label} unreadable: {path}: {exc}")
        return None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _parse_sort_fields(sort_fields: str | None) -> list[str]:
    if not sort_fields:
        return []
    return [field.strip() for field in sort_fields.split(",") if field.strip()]


def _select_project_python(root: Path) -> str:
    for candidate in [root / ".venv" / "bin" / "python", root / ".venv" / "bin" / "python3"]:
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _default_raw_daily_dir(type_n_root: Path, alpha_root: Path, warnings: list[str]) -> Path:
    configured = _configured_raw_daily_dir(alpha_root)
    if configured is not None and configured.exists():
        return configured.resolve()

    candidates = [
        type_n_root.parent / "shared_data" / "raw" / "daily" / "parquet_daily_cache",
        type_n_root.parent / "shared_data" / "raw" / "daily" / "parquet_daily_cache_5-12",
    ]
    for candidate in candidates:
        if candidate.exists():
            if candidate.name == "parquet_daily_cache_5-12":
                warnings.append(f"raw_daily_dir fell back to test cache path: {candidate.resolve()}")
            return candidate.resolve()
    warnings.append(f"raw_daily_dir fallback path does not exist yet: {candidates[0].resolve()}")
    return candidates[0].resolve()


def _configured_raw_daily_dir(alpha_root: Path) -> Path | None:
    config_path = alpha_root / "configs" / "projects.yaml"
    try:
        import yaml

        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return None
    raw_value = (config.get("data") or {}).get("raw_daily_dir")
    if not raw_value:
        return None
    path = Path(raw_value)
    if not path.is_absolute():
        path = alpha_root / path
    return path.resolve()


def _summarize_df(df: pd.DataFrame | None) -> dict[str, Any]:
    if df is None:
        return {"exists": False, "rows": None, "columns": []}
    return {
        "exists": True,
        "rows": int(len(df)),
        "columns": list(df.columns),
        "duplicate_ts_code_count": _duplicate_count(df, "ts_code"),
        "nan_columns": _nan_summary(df),
    }


def _strip_dataframes(mode: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in mode.items()
        if key not in {"final", "phase1_hits", "phase1_pool", "phase2_scores"}
    }


def _row_count(df: pd.DataFrame | None) -> int | None:
    return None if df is None else int(len(df))


def _count_delta(left: pd.DataFrame | None, right: pd.DataFrame | None) -> int | None:
    if left is None or right is None:
        return None
    return int(len(right) - len(left))


def _duplicate_count(df: pd.DataFrame | None, column: str) -> int | None:
    if df is None or column not in df.columns:
        return None
    return int(df[column].duplicated().sum())


def _nan_summary(df: pd.DataFrame | None) -> dict[str, int] | None:
    if df is None:
        return None
    return {column: int(count) for column, count in df.isna().sum().items() if int(count) > 0}


def render_report(result: dict[str, Any]) -> str:
    comparison = result["comparison"]
    diagnostics = result.get("phase_diagnostics", {})
    alignment = result.get("parameter_alignment", {})
    config = result.get("comparison_config", {})
    lines = [
        "# Two Phase Mode Comparison",
        "",
        f"- status: {result['status']}",
        f"- ok: {result['ok']}",
        f"- date: {result['date']}",
        f"- comparison_mode: {result.get('comparison_mode')}",
        f"- anchor_selection_mode: {alignment.get('anchor_selection_mode')}",
        f"- anchor_dates_equal: {alignment.get('anchor_dates_equal')}",
        f"- reviewer_config_fast: {alignment.get('reviewer_config_fast')}",
        f"- reviewer_config_decomposed: {alignment.get('reviewer_config_decomposed')}",
        f"- final_sort_fields_fast: {alignment.get('final_sort_fields_fast')}",
        f"- final_sort_fields_decomposed: {alignment.get('final_sort_fields_decomposed')}",
        f"- fast_path_dir: `{result['fast_path'].get('directory')}`",
        f"- decomposed_path_dir: `{result['decomposed_path'].get('directory')}`",
        f"- fast final file: `{result['fast_path'].get('selected_final_filename')}`",
        f"- decomposed final file: `{result['decomposed_path'].get('selected_final_filename')}`",
        f"- fast phase1 hits file: `{result['fast_path'].get('selected_phase1_hits_filename')}`",
        f"- decomposed phase1 hits file: `{result['decomposed_path'].get('selected_phase1_hits_filename')}`",
        f"- target_date: {config.get('target_date')}",
        f"- anchor_start_date: {config.get('anchor_start_date') or ''}",
        f"- anchor_lookback_days: {config.get('anchor_lookback_days')}",
        f"- phase1_top_n: {config.get('phase1_top_n')}",
        "",
        "## Counts",
        "",
        f"- final candidates: fast={comparison['final_candidate_count']['fast_path']}, decomposed={comparison['final_candidate_count']['decomposed_path']}, delta={comparison['final_candidate_count']['delta']}",
        f"- phase1 pool: fast={comparison['phase1_pool_count']['fast_path']}, decomposed={comparison['phase1_pool_count']['decomposed_path']}, delta={comparison['phase1_pool_count']['delta']}",
        f"- phase2 scores: fast={comparison['phase2_scores_count']['fast_path']}, decomposed={comparison['phase2_scores_count']['decomposed_path']}, delta={comparison['phase2_scores_count']['delta']}",
        "",
        "## Top 30 Overlap",
        "",
    ]
    overlap = comparison.get("top30_ts_code_overlap")
    if overlap is None:
        lines.append("- unavailable")
    else:
        lines.append(f"- overlap: {overlap['count']}/30 ({overlap['ratio']:.2%})")
        lines.append(f"- overlap_ts_codes: {', '.join(overlap['overlap_ts_codes'])}")

    lines.extend(["", "## Final Score Difference", ""])
    score_diff = comparison.get("final_score_diff")
    if score_diff is None:
        lines.append("- unavailable")
    else:
        for key, value in score_diff.items():
            lines.append(f"- {key}: {value}")

    lines.extend(["", "## Phase-Level Diagnostics", ""])
    lines.extend(
        [
            "### Parameter Alignment",
            "",
            f"- comparison_mode: {alignment.get('comparison_mode')}",
            f"- anchor_selection_mode: {alignment.get('anchor_selection_mode')}",
            f"- anchor_dates_equal: {alignment.get('anchor_dates_equal')}",
            f"- parameter_mismatch: {', '.join(alignment.get('parameter_mismatch', [])) or 'None'}",
            "",
        ]
    )
    anchor = diagnostics.get("anchor_dates", {})
    lines.extend(
        [
            "### Anchor Dates",
            "",
            f"- fast_anchor_dates: {', '.join(anchor.get('fast_anchor_dates', []))}",
            f"- decomposed_anchor_dates: {', '.join(anchor.get('decomposed_anchor_dates', []))}",
            f"- only_in_fast: {', '.join(anchor.get('only_in_fast', [])) or 'None'}",
            f"- only_in_decomposed: {', '.join(anchor.get('only_in_decomposed', [])) or 'None'}",
            "",
            "### Phase1 Top-N By Anchor Date",
            "",
        ]
    )
    top_n = diagnostics.get("phase1_top_n", {})
    for label in ["fast_path", "decomposed_path"]:
        summary = top_n.get(label, {})
        lines.append(f"- {label}: all_anchor_counts_equal_20={summary.get('all_anchor_counts_equal_20')}, min={summary.get('min_count')}, max={summary.get('max_count')}")
        counts = summary.get("count_by_anchor_date", {})
        if counts:
            lines.append(f"  counts: {json.dumps(counts, ensure_ascii=False)}")

    pool = diagnostics.get("phase1_pool", {})
    lines.extend(
        [
            "",
            "### Phase1 Pool",
            "",
            f"- fast_count: {pool.get('fast_count')}",
            f"- decomposed_count: {pool.get('decomposed_count')}",
            f"- pool_count_delta: {pool.get('pool_count_delta')}",
            f"- overlap_count: {pool.get('overlap_count')}",
            f"- fast_only_count: {len(pool.get('fast_only', []))}",
            f"- decomposed_only_count: {len(pool.get('decomposed_only', []))}",
        ]
    )

    phase2_inputs = diagnostics.get("phase2_inputs", {})
    lines.extend(["", "### Phase2 Inputs", ""])
    for label in ["fast_path", "decomposed_path"]:
        item = phase2_inputs.get(label, {})
        lines.append(
            f"- {label}: subset_of_pool={item.get('phase2_subset_of_pool')}, "
            f"phase2_not_in_pool_count={item.get('phase2_not_in_pool_count')}, "
            f"pool_not_scored_count={item.get('pool_not_scored_count')}"
        )

    sorting = diagnostics.get("sorting_fields", {})
    lines.extend(["", "### Sorting Fields", ""])
    for label in ["fast_path", "decomposed_path"]:
        item = sorting.get(label, {})
        lines.append(
            f"- {label}: selected={item.get('selected_sort_fields')}, "
            f"available={item.get('available_fields')}, "
            f"missing={item.get('missing_preferred_fields')}"
        )

    lines.extend(["", "## Warnings", ""])
    lines.extend([f"- {warning}" for warning in result["warnings"]] or ["- None"])
    lines.extend(["", "## Errors", ""])
    lines.extend([f"- {error}" for error in result["errors"]] or ["- None"])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
