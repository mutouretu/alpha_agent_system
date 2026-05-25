from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd


def run_phase1_scan(
    project_root: str | Path,
    target_date: str,
    output_path: str | Path,
    status_path: str | Path,
    anchor_lookback_days: int = 20,
    phase1_top_n: int = 20,
    raw_daily_dir: str | Path | None = None,
    phase1_lgbm_model_dir: str | Path | None = None,
    phase1_xgb_model_dir: str | Path | None = None,
    anchor_start_date: str | None = None,
) -> dict[str, Any]:
    args: dict[str, Any] = {
        "target-date": target_date,
        "anchor-lookback-days": anchor_lookback_days,
        "phase1-top-n": phase1_top_n,
        "raw-daily-dir": raw_daily_dir,
        "phase1-lgbm-model-dir": phase1_lgbm_model_dir,
        "phase1-xgb-model-dir": phase1_xgb_model_dir,
        "anchor-start-date": anchor_start_date,
        "output-path": output_path,
        "status-path": status_path,
    }
    return _run_type_n_task(project_root, "phase1-scan", args, status_path, "run_phase1_scan")


def build_phase1_pool(
    project_root: str | Path,
    phase1_hits_path: str | Path,
    output_path: str | Path,
    status_path: str | Path,
    target_date: str | None = None,
) -> dict[str, Any]:
    args: dict[str, Any] = {
        "phase1-hits-path": phase1_hits_path,
        "target-date": target_date,
        "output-path": output_path,
        "status-path": status_path,
    }
    return _run_type_n_task(project_root, "build-pool", args, status_path, "build_phase1_pool")


def run_phase2_filter(
    project_root: str | Path,
    target_date: str,
    phase1_pool_path: str | Path,
    output_path: str | Path,
    status_path: str | Path,
    raw_daily_dir: str | Path | None = None,
    phase2_lgbm_model_dir: str | Path | None = None,
    phase2_xgb_model_dir: str | Path | None = None,
    reviewer_config: str | None = None,
) -> dict[str, Any]:
    args: dict[str, Any] = {
        "target-date": target_date,
        "phase1-pool-path": phase1_pool_path,
        "raw-daily-dir": raw_daily_dir,
        "phase2-lgbm-model-dir": phase2_lgbm_model_dir,
        "phase2-xgb-model-dir": phase2_xgb_model_dir,
        "reviewer-config": reviewer_config,
        "output-path": output_path,
        "status-path": status_path,
    }
    return _run_type_n_task(project_root, "phase2-filter", args, status_path, "run_phase2_filter")


def merge_final_candidates(
    project_root: str | Path,
    phase1_pool_path: str | Path,
    phase2_scores_path: str | Path,
    output_path: str | Path,
    status_path: str | Path,
    final_merge_config: str | None = "default",
    sort_fields: str | list[str] | None = None,
) -> dict[str, Any]:
    args: dict[str, Any] = {
        "phase1-pool-path": phase1_pool_path,
        "phase2-scores-path": phase2_scores_path,
        "final-merge-config": final_merge_config,
        "sort-fields": _format_sort_fields(sort_fields),
        "output-path": output_path,
        "status-path": status_path,
    }
    return _run_type_n_task(project_root, "merge-final", args, status_path, "merge_final_candidates")


def generate_two_phase_report(
    project_root: str | Path,
    phase1_hits_path: str | Path,
    phase1_pool_path: str | Path,
    phase2_scores_path: str | Path,
    final_candidates_path: str | Path,
    status_paths: list[str | Path],
    output_path: str | Path,
) -> dict[str, Any]:
    args: dict[str, Any] = {
        "phase1-hits-path": phase1_hits_path,
        "phase1-pool-path": phase1_pool_path,
        "phase2-scores-path": phase2_scores_path,
        "final-candidates-path": final_candidates_path,
        "status-path": status_paths,
        "output-path": output_path,
    }
    return _run_type_n_task(project_root, "report", args, None, "generate_two_phase_report")


def run_type_n_search(project_root: str | Path, trade_date: str, output_path: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    resolved_output = Path(output_path).resolve()
    command_info = _build_scan_command(root, trade_date, resolved_output)

    if command_info is None:
        return {
            "ok": False,
            "tool": "run_type_n_search",
            "error": (
                "type_n_search exposes neither scripts/run_scan.py nor src/pipelines/run_scan.py; "
                "please adapt command in type_n_tool.py"
            ),
            "project_root": str(root),
            "expected_scripts": [
                str(root / "scripts" / "run_scan.py"),
                str(root / "src" / "pipelines" / "run_scan.py"),
            ],
        }

    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    cmd = command_info["cmd"]
    completed = subprocess.run(
        cmd,
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )

    normalize_result: dict[str, Any] | None = None
    if completed.returncode == 0:
        normalize_result = _normalize_candidates_csv(resolved_output, trade_date)

    return {
        "ok": completed.returncode == 0 and bool(normalize_result and normalize_result["ok"]),
        "tool": "run_type_n_search",
        "adapter": command_info["adapter"],
        "command": cmd,
        "cwd": str(root),
        "returncode": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
        "output_path": str(resolved_output),
        "normalize_result": normalize_result,
    }


def _build_scan_command(root: Path, trade_date: str, output_path: Path) -> dict[str, Any] | None:
    python_executable = _select_project_python(root)
    legacy_script = root / "scripts" / "run_scan.py"
    if legacy_script.exists():
        return {
            "adapter": "scripts/run_scan.py",
            "cmd": [
                python_executable,
                str(legacy_script),
                "--date",
                trade_date,
                "--output",
                str(output_path),
            ],
        }

    pipeline_script = root / "src" / "pipelines" / "run_scan.py"
    if pipeline_script.exists():
        cmd = [
            python_executable,
            str(pipeline_script),
            "--asof-date",
            trade_date,
            "--output-path",
            str(output_path),
        ]
        model_dir = _select_existing_model_dir(root)
        if model_dir is not None:
            cmd.extend(["--model-dir", str(model_dir)])
        return {
            "adapter": "src/pipelines/run_scan.py",
            "cmd": cmd,
        }

    return None


def _run_type_n_task(
    project_root: str | Path,
    subcommand: str,
    args: dict[str, Any],
    status_path: str | Path | None,
    tool_name: str,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    script = root / "scripts" / "run_type_n_task.py"
    if not script.exists():
        return {
            "ok": False,
            "tool": tool_name,
            "error": f"type_n_search task CLI not found: {script}",
            "project_root": str(root),
        }

    cmd = [_select_project_python(root), str(script), subcommand]
    for key, value in args.items():
        _append_cli_arg(cmd, key, value)

    completed = subprocess.run(
        cmd,
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    status = _read_status(status_path) if status_path else _parse_stdout_status(completed.stdout)
    return {
        "ok": completed.returncode == 0 and bool(status.get("ok", completed.returncode == 0)),
        "tool": tool_name,
        "task": subcommand,
        "command": cmd,
        "cwd": str(root),
        "returncode": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
        "status": status,
    }


def _append_cli_arg(cmd: list[str], key: str, value: Any) -> None:
    if value is None:
        return
    option = f"--{key}"
    if isinstance(value, bool):
        if value:
            cmd.append(option)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            if item is not None:
                cmd.extend([option, str(item)])
        return
    cmd.extend([option, str(value)])


def _format_sort_fields(sort_fields: str | list[str] | tuple[str, ...] | None) -> str | None:
    if sort_fields is None:
        return None
    if isinstance(sort_fields, str):
        return sort_fields
    return ",".join(str(field) for field in sort_fields if field)


def _read_status(status_path: str | Path | None) -> dict[str, Any]:
    if not status_path:
        return {}
    try:
        return json.loads(Path(status_path).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"failed to read status file: {status_path}: {exc}"}


def _parse_stdout_status(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            payload = json.loads(text)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _select_project_python(root: Path) -> str:
    candidates = [
        root / ".venv" / "bin" / "python",
        root / ".venv" / "bin" / "python3",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _select_existing_model_dir(root: Path) -> Path | None:
    preferred_model_dirs = [
        root / "outputs" / "models" / "phase2_pullback" / "lr_fastdrop_15k_w150",
        root / "outputs" / "models" / "phase2_pullback" / "lr_2025q4_balanced_w150",
        root / "outputs" / "models" / "phase2_pullback" / "lr_simple_10k_w150",
    ]
    for model_dir in preferred_model_dirs:
        if (model_dir / "model.pkl").exists():
            return model_dir
    return None


def _normalize_candidates_csv(path: Path, trade_date: str) -> dict[str, Any]:
    actual_path = _find_scan_output(path, trade_date)
    if actual_path is None:
        return {
            "ok": False,
            "error": f"Scan command completed but output CSV was not created: {path}",
        }

    try:
        df = pd.read_csv(actual_path)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": f"Failed to read scan output CSV: {exc}",
            "path": str(path),
        }

    renamed_columns: dict[str, str] = {}
    if "trade_date" not in df.columns and "asof_date" in df.columns:
        df["trade_date"] = df["asof_date"]
        renamed_columns["asof_date"] = "trade_date"
    if "model_score" not in df.columns and "score" in df.columns:
        df["model_score"] = df["score"]
        renamed_columns["score"] = "model_score"
    if "trade_date" not in df.columns:
        df["trade_date"] = trade_date
    if "name" not in df.columns:
        df["name"] = df["ts_code"] if "ts_code" in df.columns else ""
    else:
        df["name"] = df["name"].fillna("")
        if "ts_code" in df.columns:
            df.loc[df["name"].astype(str).str.len() == 0, "name"] = df["ts_code"]

    preferred_columns = ["trade_date", "ts_code", "name", "model_score"]
    ordered_columns = [column for column in preferred_columns if column in df.columns]
    ordered_columns.extend(column for column in df.columns if column not in ordered_columns)
    df = df.loc[:, ordered_columns]
    df.to_csv(path, index=False)

    missing_columns = [column for column in preferred_columns if column not in df.columns]
    return {
        "ok": not missing_columns and not df.empty,
        "path": str(path),
        "source_path": str(actual_path),
        "row_count": int(len(df)),
        "columns": list(df.columns),
        "missing_columns": missing_columns,
        "renamed_columns": renamed_columns,
    }


def _find_scan_output(path: Path, trade_date: str) -> Path | None:
    if path.exists():
        return path

    dated_path = path.with_name(f"{path.stem}_{trade_date}{path.suffix}")
    if dated_path.exists():
        return dated_path

    compact_date = trade_date.replace("-", "")
    compact_dated_path = path.with_name(f"{path.stem}_{compact_date}{path.suffix}")
    if compact_dated_path.exists():
        return compact_dated_path

    return None
