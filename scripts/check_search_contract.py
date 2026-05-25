from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


REQUIRED_FILES = [
    "search_trace.jsonl",
    "task_plan.json",
    "phase1_hits.csv",
    "phase1_status.json",
    "phase1_pool.csv",
    "pool_status.json",
    "phase2_scores.csv",
    "phase2_status.json",
    "final_candidates.csv",
    "final_status.json",
    "type_n_two_phase_report.md",
    "final_answer.md",
]

REQUIRED_TASKS = [
    "run_phase1_scan",
    "build_phase1_pool",
    "run_phase2_filter",
    "merge_final_candidates",
    "generate_two_phase_report",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Check SearcherAgent decomposed workflow output contract.")
    parser.add_argument("--search-run-dir", required=True, help="SearcherAgent run directory containing search outputs.")
    args = parser.parse_args()

    search_run_dir = Path(args.search_run_dir).expanduser().resolve()
    result = check_contract(search_run_dir)

    result_path = search_run_dir / "contract_check_result.json"
    report_path = search_run_dir / "contract_check_report.md"
    result["result_path"] = str(result_path)
    result["report_path"] = str(report_path)

    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    report_path.write_text(render_report(result), encoding="utf-8")

    print(json.dumps({"ok": result["ok"], "status": result["status"], "result_path": str(result_path), "report_path": str(report_path)}, ensure_ascii=False))


def check_contract(search_run_dir: Path) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    files: dict[str, dict[str, Any]] = {}

    for filename in REQUIRED_FILES:
        path = search_run_dir / filename
        info: dict[str, Any] = {"path": str(path), "exists": path.exists()}
        if not path.exists():
            errors.append(f"missing required file: {filename}")
        elif filename.endswith(".csv"):
            info.update(_check_csv(path, errors))
        elif filename.endswith(".json"):
            info.update(_check_json(path, errors))
        elif filename.endswith(".jsonl"):
            info.update(_check_jsonl(path, errors))
        else:
            info["bytes"] = path.stat().st_size
        files[filename] = info

    task_plan_check = _check_task_plan(search_run_dir / "task_plan.json", warnings, errors)
    trace_check = _check_trace_actions(search_run_dir / "search_trace.jsonl", warnings, errors)
    final_check = _check_final_candidates(search_run_dir / "final_candidates.csv", warnings, errors)

    ok = not errors
    status = "success" if ok and not warnings else "warning" if ok else "failed"
    return {
        "ok": ok,
        "status": status,
        "search_run_dir": str(search_run_dir),
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "files": files,
        "task_plan": task_plan_check,
        "trace": trace_check,
        "final_candidates": final_check,
        "warnings": warnings,
        "errors": errors,
    }


def _check_csv(path: Path, errors: list[str]) -> dict[str, Any]:
    try:
        df = pd.read_csv(path)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"CSV unreadable: {path.name}: {exc}")
        return {"readable": False, "rows": None, "columns": []}
    return {"readable": True, "rows": int(len(df)), "columns": list(df.columns)}


def _check_json(path: Path, errors: list[str]) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"JSON unreadable: {path.name}: {exc}")
        return {"readable": False}
    return {"readable": True, "keys": sorted(data) if isinstance(data, dict) else []}


def _check_jsonl(path: Path, errors: list[str]) -> dict[str, Any]:
    rows = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"JSONL unreadable: {path.name}: {exc}")
        return {"readable": False, "rows": 0}
    return {"readable": True, "rows": len(rows)}


def _check_task_plan(path: Path, warnings: list[str], errors: list[str]) -> dict[str, Any]:
    if not path.exists():
        return {"ok": False, "missing_tasks": REQUIRED_TASKS, "tasks": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"task_plan unreadable: {exc}")
        return {"ok": False, "missing_tasks": REQUIRED_TASKS, "tasks": []}
    raw_tasks = data.get("tasks", []) if isinstance(data, dict) else []
    task_names = [str(task.get("name")) for task in raw_tasks if isinstance(task, dict)]
    missing = [name for name in REQUIRED_TASKS if name not in task_names]
    if missing:
        errors.append(f"task_plan missing tasks: {missing}")
    failed = [task for task in raw_tasks if isinstance(task, dict) and str(task.get("status")) not in {"success", "empty"}]
    if failed:
        warnings.append(f"task_plan has non-success tasks: {[task.get('name') for task in failed]}")
    return {"ok": not missing, "tasks": raw_tasks, "missing_tasks": missing}


def _check_trace_actions(path: Path, warnings: list[str], errors: list[str]) -> dict[str, Any]:
    if not path.exists():
        return {"ok": False, "actions": [], "missing_actions": REQUIRED_TASKS}
    actions: list[str] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            decision = row.get("decision", {}) if isinstance(row, dict) else {}
            action = decision.get("action") if isinstance(decision, dict) else None
            if action:
                actions.append(str(action))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"search_trace unreadable: {exc}")
        return {"ok": False, "actions": actions, "missing_actions": REQUIRED_TASKS}

    missing = [name for name in REQUIRED_TASKS if name not in actions]
    if missing:
        errors.append(f"search_trace missing actions: {missing}")
    forbidden = [name for name in actions if name in {"run_review", "review_ma60", "review_ma120", "compare_reviewers"}]
    if forbidden:
        errors.append(f"search_trace contains forbidden reviewer actions: {forbidden}")
    return {"ok": not missing and not forbidden, "actions": actions, "missing_actions": missing, "forbidden_actions": forbidden}


def _check_final_candidates(path: Path, warnings: list[str], errors: list[str]) -> dict[str, Any]:
    if not path.exists():
        return {"ok": False, "rows": 0, "columns": []}
    try:
        df = pd.read_csv(path)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"final_candidates unreadable: {exc}")
        return {"ok": False, "rows": 0, "columns": []}

    if df.empty:
        warnings.append("final_candidates.csv is empty")

    columns = set(df.columns)
    missing: list[str] = []
    if "ts_code" not in columns:
        missing.append("ts_code")
    if "target_date" not in columns and "asof_date" not in columns:
        missing.append("target_date_or_asof_date")
    if "final_score" not in columns and "phase2_score_mean" not in columns:
        missing.append("final_score_or_phase2_score_mean")
    if missing:
        errors.append(f"final_candidates missing required columns: {missing}")

    return {
        "ok": not missing,
        "rows": int(len(df)),
        "columns": list(df.columns),
        "missing_columns": missing,
        "duplicate_ts_code_count": _duplicate_count(df, "ts_code"),
        "nan_columns": _nan_summary(df),
    }


def _duplicate_count(df: pd.DataFrame, column: str) -> int:
    if column not in df.columns:
        return 0
    return int(df[column].duplicated().sum())


def _nan_summary(df: pd.DataFrame) -> dict[str, int]:
    return {column: int(count) for column, count in df.isna().sum().items() if int(count) > 0}


def render_report(result: dict[str, Any]) -> str:
    lines = [
        "# Search Contract Check",
        "",
        f"- status: {result['status']}",
        f"- ok: {result['ok']}",
        f"- search_run_dir: `{result['search_run_dir']}`",
        "",
        "## Required Files",
        "",
    ]
    for name, info in result["files"].items():
        suffix = "ok" if info.get("exists") and info.get("readable", True) else "missing/unreadable"
        rows = f", rows={info.get('rows')}" if "rows" in info else ""
        lines.append(f"- {name}: {suffix}{rows}")

    lines.extend(["", "## Task Plan", ""])
    for task in result["task_plan"].get("tasks", []):
        lines.append(f"- {task.get('name')}: {task.get('status')}")

    lines.extend(["", "## Trace Actions", ""])
    lines.append(", ".join(result["trace"].get("actions", [])) or "_No actions found._")

    lines.extend(["", "## Warnings", ""])
    lines.extend([f"- {warning}" for warning in result["warnings"]] or ["- None"])
    lines.extend(["", "## Errors", ""])
    lines.extend([f"- {error}" for error in result["errors"]] or ["- None"])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
