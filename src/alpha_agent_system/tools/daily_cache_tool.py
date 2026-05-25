from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


def check_daily_cache_status(project_root: str | Path, trade_date: str) -> dict[str, Any]:
    root = Path(project_root).resolve()
    if not root.exists():
        return {
            "ok": False,
            "tool": "check_daily_cache_status",
            "status": "failed",
            "can_continue": False,
            "project_root": str(root),
            "trade_date": trade_date,
            "latest_data_date": None,
            "data_root": None,
            "sample_data_files": [],
            "warnings": [],
            "error": f"daily-cache project root not found: {root}",
        }

    shared_cache_dir = _detect_shared_daily_cache_dir(root)
    candidate_dirs = [path for path in [shared_cache_dir, root / "custom_pipeline" / "input"] if path and path.exists()]
    existing_data_dirs = candidate_dirs
    data_files = []
    for data_dir in existing_data_dirs:
        data_files.extend(str(path) for path in sorted(data_dir.glob("*"))[:20] if path.is_file())

    latest_data_date = _detect_latest_shared_date(shared_cache_dir) if shared_cache_dir else _detect_latest_data_date(data_files)
    trade_date_present = _shared_cache_has_date(shared_cache_dir, trade_date) if shared_cache_dir else False
    warnings = []
    missing_reason = None
    if not existing_data_dirs:
        missing_reason = "No shared daily-cache directory was found."
        warnings.append(missing_reason)

    status = {
        "ok": bool(existing_data_dirs) and (trade_date_present or latest_data_date is not None),
        "tool": "check_daily_cache_status",
        "status": "success" if existing_data_dirs else "warning",
        "can_continue": True,
        "project_root": str(root),
        "trade_date": trade_date,
        "latest_data_date": latest_data_date,
        "trade_date_present": trade_date_present,
        "data_root": str(shared_cache_dir) if shared_cache_dir else str(existing_data_dirs[0]) if existing_data_dirs else None,
        "sample_data_files": data_files,
        "missing_reason": missing_reason,
        "warnings": warnings,
        "existing_data_dirs": [str(path) for path in existing_data_dirs],
    }
    return status


def run_daily_cache_update(
    project_root: str | Path,
    trade_date: str,
    output_status_path: str | Path,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    status_path = Path(output_status_path).resolve()
    status_path.parent.mkdir(parents=True, exist_ok=True)

    if not root.exists():
        result = _status_failed(
            root=root,
            trade_date=trade_date,
            status_path=status_path,
            error=f"daily-cache project root not found: {root}",
        )
        _write_json(status_path, result)
        return result

    shared_cache_dir = _detect_shared_daily_cache_dir(root)
    if shared_cache_dir is None:
        result = _status_failed(
            root=root,
            trade_date=trade_date,
            status_path=status_path,
            error="shared daily-cache directory not found.",
            can_continue=False,
        )
        _write_json(status_path, result)
        return result

    if _shared_cache_has_date(shared_cache_dir, trade_date):
        result = {
            "ok": True,
            "tool": "run_daily_cache_update",
            "status": "up_to_date",
            "can_continue": True,
            "trade_date": trade_date,
            "project_root": str(root),
            "latest_data_date": _detect_latest_shared_date(shared_cache_dir),
            "data_root": str(shared_cache_dir),
            "trade_date_present": True,
            "sample_data_files": _sample_project_files(root),
            "warnings": [],
            "output_status_path": str(status_path),
        }
        _write_json(status_path, result)
        return result

    compact_date = _compact_trade_date(trade_date)
    increment_path = root / "custom_pipeline" / "input" / f"daily_increment_{compact_date}.parquet"
    failed_dates_path = root / "custom_pipeline" / "input" / f"failed_trade_dates_{compact_date}.json"
    main_script = root / "main.py"
    if not main_script.exists():
        result = _status_failed(
            root=root,
            trade_date=trade_date,
            status_path=status_path,
            error=f"daily-cache main.py not found: {main_script}",
        )
        _write_json(status_path, result)
        return result

    env = _load_project_env(root)
    cmd = _build_python_command(root) + [
        str(main_script),
        "--start-date",
        compact_date,
        "--end-date",
        compact_date,
        "--output",
        str(increment_path),
        "--failed-dates-output",
        str(failed_dates_path),
    ]
    completed = subprocess.run(cmd, cwd=root, check=False, capture_output=True, text=True, env=env)

    failed_trade_dates = _read_failed_dates(failed_dates_path)
    increment_stats = _inspect_increment(increment_path)
    warnings: list[str] = []
    errors: list[str] = []
    if completed.returncode != 0:
        errors.append("daily-cache download command failed.")
    if failed_trade_dates:
        warnings.append(f"Tushare returned incomplete dates: {sorted(failed_trade_dates)}")
    if increment_stats.get("rows", 0) == 0:
        warnings.append("No increment rows were downloaded; Tushare may not have published this trade date yet.")

    merge_result: dict[str, Any] | None = None
    if completed.returncode == 0 and increment_stats.get("rows", 0) > 0 and not failed_trade_dates:
        merge_result = _merge_increment_into_shared_cache(
            root=root,
            base_dir=shared_cache_dir,
            increment_path=increment_path,
            trade_date=compact_date,
        )
        if not merge_result.get("ok"):
            errors.extend(str(item) for item in merge_result.get("errors", []))
            warnings.extend(str(item) for item in merge_result.get("warnings", []))

    ok = completed.returncode == 0 and not errors and bool(merge_result and merge_result.get("ok"))
    can_continue = _shared_cache_has_date(shared_cache_dir, trade_date) or bool(_detect_latest_shared_date(shared_cache_dir))
    result = {
        "ok": ok,
        "tool": "run_daily_cache_update",
        "status": "success" if ok else "warning" if can_continue else "failed",
        "can_continue": can_continue,
        "trade_date": trade_date,
        "project_root": str(root),
        "latest_data_date": _detect_latest_shared_date(shared_cache_dir),
        "data_root": str(shared_cache_dir),
        "trade_date_present": _shared_cache_has_date(shared_cache_dir, trade_date),
        "increment_path": str(increment_path),
        "failed_dates_path": str(failed_dates_path),
        "failed_trade_dates": failed_trade_dates,
        "increment_stats": increment_stats,
        "merge_result": merge_result,
        "sample_data_files": _sample_project_files(root),
        "missing_reason": None if ok else "daily-cache update did not complete fully.",
        "warnings": warnings,
        "errors": errors,
        "command": cmd,
        "returncode": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
        "output_status_path": str(status_path),
    }
    _write_json(status_path, result)
    return result


def generate_cache_report(status_path: str | Path, output_path: str | Path) -> dict[str, Any]:
    resolved_status = Path(status_path).resolve()
    resolved_output = Path(output_path).resolve()

    if not resolved_status.exists():
        return {
            "ok": False,
            "tool": "generate_cache_report",
            "error": f"cache_status.json not found: {resolved_status}",
            "status_path": str(resolved_status),
        }

    try:
        status = json.loads(resolved_status.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "tool": "generate_cache_report",
            "error": f"Failed to read cache status JSON: {exc}",
            "status_path": str(resolved_status),
        }

    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Daily Cache Report",
        "",
        f"- Trade date: {status.get('trade_date', 'unknown')}",
        f"- Project root: `{status.get('project_root', 'unknown')}`",
        f"- Status: {status.get('status', 'unknown')}",
        f"- OK: {status.get('ok')}",
        f"- Can continue: {status.get('can_continue')}",
        f"- Latest data date: {status.get('latest_data_date')}",
        f"- Data root: `{status.get('data_root')}`",
        f"- Status file: `{resolved_status}`",
        "",
    ]
    if status.get("error"):
        lines.extend(["## Error", "", str(status["error"]), ""])
    if status.get("missing_reason"):
        lines.extend(["## Missing Reason", "", str(status["missing_reason"]), ""])
    if status.get("warnings"):
        lines.extend(["## Warnings", ""])
        lines.extend(f"- {warning}" for warning in status["warnings"])
        lines.append("")
    if status.get("errors"):
        lines.extend(["## Errors", ""])
        lines.extend(f"- {error}" for error in status["errors"])
        lines.append("")
    if status.get("increment_stats"):
        lines.extend(["## Increment", ""])
        for key, value in status["increment_stats"].items():
            lines.append(f"- {key}: {value}")
        lines.append("")
    if status.get("merge_result"):
        lines.extend(["## Merge", ""])
        for key, value in status["merge_result"].items():
            lines.append(f"- {key}: {value}")
        lines.append("")
    if status.get("existing_data_dirs"):
        lines.extend(["## Existing Data Directories", ""])
        lines.extend(f"- `{path}`" for path in status["existing_data_dirs"])
        lines.append("")
    if status.get("sample_data_files"):
        lines.extend(["## Sample Data Files", ""])
        lines.extend(f"- `{path}`" for path in status["sample_data_files"][:20])
        lines.append("")

    resolved_output.write_text("\n".join(lines), encoding="utf-8")
    return {
        "ok": True,
        "tool": "generate_cache_report",
        "status_path": str(resolved_status),
        "output_path": str(resolved_output),
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _status_failed(
    *,
    root: Path,
    trade_date: str,
    status_path: Path,
    error: str,
    can_continue: bool = True,
) -> dict[str, Any]:
    return {
        "ok": False,
        "tool": "run_daily_cache_update",
        "status": "failed",
        "can_continue": can_continue,
        "trade_date": trade_date,
        "project_root": str(root),
        "latest_data_date": _detect_latest_shared_date(_detect_shared_daily_cache_dir(root)),
        "data_root": _detect_data_root(root),
        "sample_data_files": _sample_project_files(root),
        "warnings": [],
        "errors": [error],
        "output_status_path": str(status_path),
        "error": error,
    }


def _detect_data_root(root: Path) -> str | None:
    shared_cache_dir = _detect_shared_daily_cache_dir(root)
    candidate_dirs = [shared_cache_dir] if shared_cache_dir else []
    candidate_dirs.extend([root / "custom_pipeline" / "input", root / "data", root / "outputs", root / "cache"])
    for candidate in candidate_dirs:
        if candidate.exists():
            return str(candidate)
    return None


def _sample_project_files(root: Path) -> list[str]:
    data_root = _detect_data_root(root)
    if data_root is None:
        return []
    return [str(path) for path in sorted(Path(data_root).glob("*"))[:20] if path.is_file()]


def _detect_shared_daily_cache_dir(root: Path) -> Path | None:
    daily_root = root.parent / "shared_data" / "raw" / "daily"
    if not daily_root.exists():
        return None
    preferred = daily_root / "parquet_daily_cache_5-12"
    if preferred.exists():
        return preferred
    plain = daily_root / "parquet_daily_cache"
    if plain.exists():
        return plain
    candidates = sorted(
        [path for path in daily_root.glob("parquet_daily_cache*") if path.is_dir()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _compact_trade_date(trade_date: str) -> str:
    return str(trade_date).replace("-", "")


def _display_trade_date(trade_date: str) -> str:
    value = str(trade_date)
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return value


def _shared_cache_has_date(cache_dir: Path | None, trade_date: str) -> bool:
    if cache_dir is None or not cache_dir.exists():
        return False
    target = _display_trade_date(trade_date)
    for path in cache_dir.glob("*.parquet"):
        try:
            dates = pd.read_parquet(path, columns=["trade_date"])
        except Exception:  # noqa: BLE001
            continue
        if target in pd.to_datetime(dates["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d").dropna().values:
            return True
    return False


def _detect_latest_shared_date(cache_dir: Path | None) -> str | None:
    if cache_dir is None or not cache_dir.exists():
        return None
    latest: str | None = None
    for path in cache_dir.glob("*.parquet"):
        try:
            dates = pd.read_parquet(path, columns=["trade_date"])
        except Exception:  # noqa: BLE001
            continue
        normalized = pd.to_datetime(dates["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d").dropna()
        if normalized.empty:
            continue
        current = str(normalized.max())
        latest = current if latest is None or current > latest else latest
    return latest


def _load_project_env(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env_path = root / ".env"
    if not env_path.exists():
        return env
    for line in env_path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def _build_python_command(root: Path) -> list[str]:
    venv_python = root / ".venv" / "bin" / "python"
    if venv_python.exists():
        return [str(venv_python)]
    uv_lock = root / "uv.lock"
    if uv_lock.exists():
        return ["uv", "run", "python"]
    return [sys.executable]


def _read_failed_dates(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _inspect_increment(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "rows": 0, "symbols": 0}
    try:
        df = pd.read_parquet(path, columns=["ts_code", "trade_date"])
    except Exception as exc:  # noqa: BLE001
        return {"exists": True, "rows": 0, "symbols": 0, "error": str(exc)}
    dates = pd.to_datetime(df["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d").dropna()
    return {
        "exists": True,
        "rows": int(len(df)),
        "symbols": int(df["ts_code"].nunique()) if "ts_code" in df.columns else 0,
        "min_date": None if dates.empty else str(dates.min()),
        "max_date": None if dates.empty else str(dates.max()),
    }


def _merge_increment_into_shared_cache(
    *,
    root: Path,
    base_dir: Path,
    increment_path: Path,
    trade_date: str,
) -> dict[str, Any]:
    merge_script = root / "custom_pipeline" / "tools" / "merge_daily_increment.py"
    if not merge_script.exists():
        return {"ok": False, "warnings": [], "errors": [f"merge script not found: {merge_script}"]}

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    temp_output_dir = base_dir.with_name(f".{base_dir.name}_merge_tmp_{trade_date}_{timestamp}")
    backup_dir = base_dir.with_name(f"{base_dir.name}_backup_before_{trade_date}_{timestamp}")
    cmd = _build_python_command(root) + [
        str(merge_script),
        "--base-dir",
        str(base_dir),
        "--increment",
        str(increment_path),
        "--output-dir",
        str(temp_output_dir),
    ]
    completed = subprocess.run(cmd, cwd=root, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        return {
            "ok": False,
            "warnings": [],
            "errors": ["merge_daily_increment.py failed."],
            "command": cmd,
            "returncode": completed.returncode,
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
        }

    try:
        shutil.move(str(base_dir), str(backup_dir))
        shutil.move(str(temp_output_dir), str(base_dir))
    except Exception as exc:  # noqa: BLE001
        if not base_dir.exists() and backup_dir.exists():
            shutil.move(str(backup_dir), str(base_dir))
        return {
            "ok": False,
            "warnings": [],
            "errors": [f"failed to replace shared cache directory: {exc}"],
            "backup_dir": str(backup_dir),
            "temp_output_dir": str(temp_output_dir),
        }

    return {
        "ok": True,
        "command": cmd,
        "returncode": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
        "base_dir": str(base_dir),
        "backup_dir": str(backup_dir),
        "temp_output_dir": str(temp_output_dir),
    }


def _detect_latest_data_date(files: list[str]) -> str | None:
    date_tokens: list[str] = []
    for file_path in files:
        stem = Path(file_path).stem
        for token in stem.replace("-", "_").split("_"):
            if len(token) == 8 and token.isdigit():
                date_tokens.append(f"{token[:4]}-{token[4:6]}-{token[6:]}")
    return max(date_tokens) if date_tokens else None
