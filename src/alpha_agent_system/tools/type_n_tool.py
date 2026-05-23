from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd


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
    legacy_script = root / "scripts" / "run_scan.py"
    if legacy_script.exists():
        return {
            "adapter": "scripts/run_scan.py",
            "cmd": [
                sys.executable,
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
            sys.executable,
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
