from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from alpha_agent_system.agents.daily_cache_agent import DailyCacheAgent
from alpha_agent_system.agents.searcher_agent import SearcherAgent
from alpha_agent_system.agents.type_n_runner_agent import TypeNRunnerAgent
from alpha_agent_system.core.llm_client import LLMClient


def run_daily_cache_agent(
    trade_date: str,
    daily_cache_root: str | Path,
    run_dir: str | Path,
    llm_client: LLMClient | None = None,
    max_steps: int = 6,
) -> dict[str, Any]:
    agent = DailyCacheAgent(
        trade_date=trade_date,
        daily_cache_project_root=daily_cache_root,
        run_dir=run_dir,
        llm_client=llm_client,
        max_steps=max_steps,
    )
    result = agent.run()
    return {"ok": True, "tool": "run_daily_cache_agent", "agent_result": result}


def run_type_n_runner_agent(
    trade_date: str,
    type_n_root: str | Path,
    run_dir: str | Path,
    llm_client: LLMClient | None = None,
    max_steps: int = 8,
) -> dict[str, Any]:
    agent = TypeNRunnerAgent(
        trade_date=trade_date,
        type_n_project_root=type_n_root,
        run_dir=run_dir,
        llm_client=llm_client,
        max_steps=max_steps,
    )
    result = agent.run()
    return {"ok": bool(result.get("ok")), "tool": "run_type_n_runner_agent", "agent_result": result}


def run_searcher_agent(
    trade_date: str,
    type_n_root: str | Path,
    run_dir: str | Path,
    llm_client: LLMClient | None = None,
    max_steps: int = 8,
) -> dict[str, Any]:
    agent = SearcherAgent(
        trade_date=trade_date,
        type_n_project_root=type_n_root,
        run_dir=run_dir,
        llm_client=llm_client,
        max_steps=max_steps,
    )
    result = agent.run()
    return {"ok": bool(result.get("ok")), "tool": "run_searcher_agent", "agent_result": result}


def generate_data_mining_report(
    trade_date: str,
    daily_cache_result_path: str | Path,
    search_result_path: str | Path,
    workflow_status_path: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    daily_cache_result = _read_json(Path(daily_cache_result_path))
    search_result = _read_json(Path(search_result_path))
    workflow_status = _compute_workflow_status([daily_cache_result, search_result])

    status = {
        "trade_date": trade_date,
        "ok": workflow_status != "failed",
        "status": workflow_status,
        "daily_cache": daily_cache_result,
        "search": search_result,
        "warnings": [],
    }
    cache_status = daily_cache_result.get("status", {})
    if not daily_cache_result.get("ok"):
        status["warnings"].append("DailyCacheAgent did not report ok.")
    if cache_status.get("error"):
        status["warnings"].append(str(cache_status["error"]))
    for warning in cache_status.get("warnings", []):
        status["warnings"].append(str(warning))
    if "update_daily_cache.py yet" in str(cache_status.get("error", "")):
        status["warnings"].append("daily-cache adapter is pending real CLI integration.")
    status["warnings"] = _dedupe(status["warnings"])

    workflow_path = Path(workflow_status_path).resolve()
    report_path = Path(output_path).resolve()
    workflow_path.parent.mkdir(parents=True, exist_ok=True)
    workflow_path.write_text(json.dumps(status, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    lines = [
        "# Data Mining Group Report",
        "",
        f"- Trade date: {trade_date}",
        f"- Workflow status: {status['status']}",
        f"- Workflow OK: {status['ok']}",
        f"- Workflow status: `{workflow_path}`",
        "",
        "## Daily Cache",
        "",
        f"- Status: {cache_status.get('status')}",
        f"- OK: {daily_cache_result.get('ok')}",
        f"- Can continue: {daily_cache_result.get('can_continue')}",
        f"- Report: `{daily_cache_result.get('cache_report_path', '')}`",
        "",
        "## Type-N Search",
        "",
        f"- OK: {search_result.get('ok')}",
        f"- Candidates: `{search_result.get('final_candidates_path') or search_result.get('candidates_path', '')}`",
        f"- Summary: `{search_result.get('report_path') or search_result.get('summary_path', '')}`",
        "",
    ]
    if status["warnings"]:
        lines.extend(["## Warnings", ""])
        lines.extend(f"- {warning}" for warning in status["warnings"])
        lines.append("")
    lines.extend(["## Final Answer", "", str(search_result.get("final_answer", "")), ""])

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return {
        "ok": True,
        "tool": "generate_data_mining_report",
        "workflow_status_path": str(workflow_path),
        "output_path": str(report_path),
        "warnings": status["warnings"],
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.resolve().read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Failed to read JSON: {path}: {exc}"}


def _compute_workflow_status(agent_results: list[dict[str, Any]]) -> str:
    failed_hard = any(not result.get("ok") and not result.get("can_continue", False) for result in agent_results)
    if failed_hard:
        return "failed"

    has_warning = any(not result.get("ok") and result.get("can_continue", False) for result in agent_results)
    if has_warning:
        return "completed_with_warnings"

    return "success"


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
