from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from alpha_agent_system.agents.data_mining_group_agent import DataMiningGroupAgent
from alpha_agent_system.core.llm_client import LLMClient


def resolve_trade_date(
    date_text: str | None = None,
    resolved_date: str | None = None,
    intent: str | None = None,
    confidence: float | None = None,
) -> dict[str, Any]:
    raw_text = (date_text or "今天").strip()
    validated_date = _validate_resolved_date(resolved_date)
    fallback_used = False
    if validated_date:
        trade_date = validated_date
    else:
        trade_date = _fallback_resolve_trade_date(raw_text)
        fallback_used = True

    return {
        "ok": True,
        "tool": "resolve_trade_date",
        "date_text": raw_text,
        "llm_resolved_date": resolved_date,
        "trade_date": trade_date,
        "intent": intent,
        "confidence": confidence,
        "fallback_used": fallback_used,
        "note": "LLM 先做日期语义解析；工具校验 resolved_date，必要时 fallback；尚未接入交易日历",
    }


def run_data_mining_group_agent(
    trade_date: str,
    daily_cache_root: str | Path,
    type_n_root: str | Path,
    run_dir: str | Path,
    llm_client: LLMClient | None = None,
    max_steps: int = 8,
) -> dict[str, Any]:
    agent = DataMiningGroupAgent(
        trade_date=trade_date,
        daily_cache_root=daily_cache_root,
        type_n_root=type_n_root,
        run_dir=run_dir,
        llm_client=llm_client,
        max_steps=max_steps,
    )
    result = agent.run()
    return {
        "ok": bool(result.get("ok")),
        "tool": "run_data_mining_group_agent",
        "trade_date": trade_date,
        "workflow_status_path": result.get("workflow_status_path"),
        "data_mining_report_path": result.get("data_mining_report_path"),
        "agent_result": result,
    }


def read_workflow_status(path: str | Path) -> dict[str, Any]:
    resolved_path = Path(path).resolve()
    if not resolved_path.exists():
        return {
            "ok": False,
            "tool": "read_workflow_status",
            "error": f"workflow_status.json not found: {resolved_path}",
            "path": str(resolved_path),
        }

    try:
        status = json.loads(resolved_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "tool": "read_workflow_status",
            "error": f"Failed to read workflow status JSON: {exc}",
            "path": str(resolved_path),
        }

    return {
        "ok": True,
        "tool": "read_workflow_status",
        "path": str(resolved_path),
        "workflow_status": status,
    }


def _extract_explicit_date(text: str) -> str | None:
    match = re.search(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})", text)
    if not match:
        match = re.search(r"(20\d{2})(\d{2})(\d{2})", text)
    if match:
        year, month, day = (int(part) for part in match.groups())
        return datetime(year, month, day).date().isoformat()

    month_day_match = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?", text)
    if month_day_match:
        month, day = (int(part) for part in month_day_match.groups())
        return datetime(date.today().year, month, day).date().isoformat()

    slash_match = re.search(r"(?<!\d)(\d{1,2})/(\d{1,2})(?!\d)", text)
    if slash_match:
        month, day = (int(part) for part in slash_match.groups())
        return datetime(date.today().year, month, day).date().isoformat()

    return None


def _validate_resolved_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def _fallback_resolve_trade_date(raw_text: str) -> str:
    today = date.today()
    explicit_date = _extract_explicit_date(raw_text)
    if explicit_date:
        return explicit_date
    if "昨天" in raw_text or "昨日" in raw_text:
        return (today - timedelta(days=1)).isoformat()
    return today.isoformat()
