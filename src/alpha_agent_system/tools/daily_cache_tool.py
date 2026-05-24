from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


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

    candidate_dirs = [
        root / "custom_pipeline" / "input",
        root / "data",
        root / "outputs",
        root / "cache",
    ]
    existing_data_dirs = [path for path in candidate_dirs if path.exists()]
    data_files = []
    for data_dir in existing_data_dirs:
        data_files.extend(str(path) for path in sorted(data_dir.glob("*"))[:20] if path.is_file())

    latest_data_date = _detect_latest_data_date(data_files)
    warnings = []
    missing_reason = None
    if not existing_data_dirs:
        missing_reason = "No known daily-cache data directory was found; adapter may need project-specific mapping."
        warnings.append(missing_reason)

    status = {
        "ok": bool(existing_data_dirs),
        "tool": "check_daily_cache_status",
        "status": "success" if existing_data_dirs else "warning",
        "can_continue": True,
        "project_root": str(root),
        "trade_date": trade_date,
        "latest_data_date": latest_data_date,
        "data_root": str(existing_data_dirs[0]) if existing_data_dirs else None,
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
    update_script = root / "scripts" / "update_daily_cache.py"

    if not update_script.exists():
        result = {
            "ok": False,
            "tool": "run_daily_cache_update",
            "status": "warning",
            "can_continue": True,
            "trade_date": trade_date,
            "project_root": str(root),
            "latest_data_date": _detect_latest_data_date(_sample_project_files(root)),
            "data_root": _detect_data_root(root),
            "sample_data_files": _sample_project_files(root),
            "missing_reason": "daily-cache update CLI is not exposed yet.",
            "warnings": ["daily-cache adapter is pending real CLI integration."],
            "output_status_path": str(status_path),
            "error": "daily-cache does not expose scripts/update_daily_cache.py yet; please adapt command in daily_cache_tool.py",
        }
        _write_json(status_path, result)
        return result

    status_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(update_script),
        "--date",
        trade_date,
        "--output-status",
        str(status_path),
    ]
    completed = subprocess.run(
        cmd,
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )

    result = {
        "ok": completed.returncode == 0,
        "tool": "run_daily_cache_update",
        "status": "success" if completed.returncode == 0 else "failed",
        "can_continue": completed.returncode == 0,
        "trade_date": trade_date,
        "project_root": str(root),
        "latest_data_date": _detect_latest_data_date(_sample_project_files(root)),
        "data_root": _detect_data_root(root),
        "sample_data_files": _sample_project_files(root),
        "missing_reason": None if completed.returncode == 0 else "daily-cache update command failed.",
        "warnings": [],
        "command": cmd,
        "returncode": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
        "output_status_path": str(status_path),
    }
    if not status_path.exists():
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


def _detect_data_root(root: Path) -> str | None:
    candidate_dirs = [
        root / "custom_pipeline" / "input",
        root / "data",
        root / "outputs",
        root / "cache",
    ]
    for candidate in candidate_dirs:
        if candidate.exists():
            return str(candidate)
    return None


def _sample_project_files(root: Path) -> list[str]:
    data_root = _detect_data_root(root)
    if data_root is None:
        return []
    return [str(path) for path in sorted(Path(data_root).glob("*"))[:20] if path.is_file()]


def _detect_latest_data_date(files: list[str]) -> str | None:
    date_tokens: list[str] = []
    for file_path in files:
        stem = Path(file_path).stem
        for token in stem.replace("-", "_").split("_"):
            if len(token) == 8 and token.isdigit():
                date_tokens.append(f"{token[:4]}-{token[4:6]}-{token[6:]}")
    return max(date_tokens) if date_tokens else None
