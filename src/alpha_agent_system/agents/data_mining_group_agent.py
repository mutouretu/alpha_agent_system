from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from alpha_agent_system.core.agent_loop import AgentLoop
from alpha_agent_system.core.llm_client import LLMClient
from alpha_agent_system.core.tool_registry import ToolRegistry
from alpha_agent_system.core.trace import TraceWriter
from alpha_agent_system.prompts.data_mining_group_prompt import DATA_MINING_GROUP_SYSTEM_PROMPT
from alpha_agent_system.tools.agent_tools import (
    generate_data_mining_report,
    run_daily_cache_agent,
    run_searcher_agent,
)


class DataMiningGroupAgent:
    def __init__(
        self,
        trade_date: str,
        daily_cache_root: str | Path,
        type_n_root: str | Path,
        run_dir: str | Path,
        llm_client: LLMClient | None = None,
        max_steps: int = 8,
    ) -> None:
        self.trade_date = trade_date
        self.daily_cache_root = Path(daily_cache_root).resolve()
        self.type_n_root = Path(type_n_root).resolve()
        self.run_dir = Path(run_dir).resolve()
        self.daily_cache_run_dir = self.run_dir / "daily_cache"
        self.search_run_dir = self.run_dir / "search"
        self.group_trace_path = self.run_dir / "group_trace.jsonl"
        self.workflow_status_path = self.run_dir / "workflow_status.json"
        self.report_path = self.run_dir / "data_mining_report.md"
        self.final_answer_path = self.run_dir / "final_answer.md"
        self.daily_cache_result_path = self.run_dir / "daily_cache_result.json"
        self.search_result_path = self.run_dir / "search_result.json"
        self.llm_client = llm_client or LLMClient()
        self.max_steps = max_steps

    def run(self) -> dict[str, Any]:
        self.run_dir.mkdir(parents=True, exist_ok=True)

        registry = ToolRegistry()
        registry.register("run_daily_cache_agent", self._run_daily_cache_agent)
        registry.register("run_searcher_agent", self._run_searcher_agent)
        registry.register("generate_data_mining_report", self._generate_data_mining_report)

        loop = AgentLoop(
            llm_client=self.llm_client,
            tool_registry=registry,
            trace_writer=TraceWriter(self.group_trace_path),
            system_prompt=DATA_MINING_GROUP_SYSTEM_PROMPT,
            max_steps=self.max_steps,
        )
        result = loop.run(self._build_task())
        self._ensure_report()
        workflow_status = self._read_workflow_status()
        final_answer = self._format_final_answer(result["final_answer"], workflow_status)
        self.final_answer_path.write_text(final_answer + "\n", encoding="utf-8")

        return {
            "ok": self.workflow_status_path.exists(),
            "status": workflow_status.get("status", "unknown"),
            "trade_date": self.trade_date,
            "run_dir": str(self.run_dir),
            "group_trace_path": str(self.group_trace_path),
            "workflow_status_path": str(self.workflow_status_path),
            "data_mining_report_path": str(self.report_path),
            "final_answer_path": str(self.final_answer_path),
            "daily_cache_run_dir": str(self.daily_cache_run_dir),
            "search_run_dir": str(self.search_run_dir),
            "final_answer": final_answer,
            "last_result": result["last_result"],
        }

    def _build_task(self) -> str:
        return (
            "请协调 DailyCacheAgent 和 SearcherAgent 完成每日数据挖掘流程。\n"
            f"trade_date: {self.trade_date}\n"
            f"daily_cache_root: {self.daily_cache_root}\n"
            f"type_n_root: {self.type_n_root}\n"
            f"daily_cache_run_dir: {self.daily_cache_run_dir}\n"
            f"search_run_dir: {self.search_run_dir}\n"
            f"workflow_status_path: {self.workflow_status_path}\n"
            f"data_mining_report_path: {self.report_path}\n"
            "只调用下级 Agent 工具，不直接调用底层项目脚本。"
        )

    def _run_daily_cache_agent(self, **_: Any) -> dict[str, Any]:
        result = run_daily_cache_agent(
            trade_date=self.trade_date,
            daily_cache_root=self.daily_cache_root,
            run_dir=self.daily_cache_run_dir,
            llm_client=self.llm_client,
            max_steps=6,
        )
        self._write_json(self.daily_cache_result_path, result.get("agent_result", result))
        return result

    def _run_searcher_agent(self, **_: Any) -> dict[str, Any]:
        result = run_searcher_agent(
            trade_date=self.trade_date,
            type_n_root=self.type_n_root,
            run_dir=self.search_run_dir,
            llm_client=self.llm_client,
            max_steps=8,
        )
        self._write_json(self.search_result_path, result.get("agent_result", result))
        return result

    def _generate_data_mining_report(self, **_: Any) -> dict[str, Any]:
        return generate_data_mining_report(
            trade_date=self.trade_date,
            daily_cache_result_path=self.daily_cache_result_path,
            search_result_path=self.search_result_path,
            workflow_status_path=self.workflow_status_path,
            output_path=self.report_path,
        )

    def _ensure_report(self) -> None:
        if not self.workflow_status_path.exists() and self.daily_cache_result_path.exists() and self.search_result_path.exists():
            generate_data_mining_report(
                trade_date=self.trade_date,
                daily_cache_result_path=self.daily_cache_result_path,
                search_result_path=self.search_result_path,
                workflow_status_path=self.workflow_status_path,
                output_path=self.report_path,
            )
        if not self.workflow_status_path.exists():
            status = {
                "trade_date": self.trade_date,
                "ok": False,
                "status": "failed",
                "daily_cache": self._read_json_file(self.daily_cache_result_path),
                "search": self._read_json_file(self.search_result_path),
                "warnings": ["workflow_status.json was generated by fallback after group agent failure."],
            }
            self.workflow_status_path.write_text(
                json.dumps(status, ensure_ascii=False, indent=2, default=str) + "\n",
                encoding="utf-8",
            )
        if not self.report_path.exists():
            status = self._read_workflow_status()
            lines = [
                "# Data Mining Group Report",
                "",
                f"- Trade date: {self.trade_date}",
                f"- Workflow status: {status.get('status')}",
                f"- Workflow OK: {status.get('ok')}",
                f"- Workflow status: `{self.workflow_status_path}`",
                "",
                "## Warnings",
                "",
            ]
            lines.extend(f"- {warning}" for warning in status.get("warnings", []))
            self.report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    def _read_workflow_status(self) -> dict[str, Any]:
        try:
            return json.loads(self.workflow_status_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {"status": "failed", "ok": False, "warnings": ["workflow_status.json was not generated."]}

    @staticmethod
    def _read_json_file(path: Path) -> dict[str, Any]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "can_continue": False, "error": f"Failed to read JSON: {path}: {exc}"}

    @staticmethod
    def _format_final_answer(agent_answer: Any, workflow_status: dict[str, Any]) -> str:
        warnings = workflow_status.get("warnings", [])
        lines = [
            f"workflow_status = {workflow_status.get('status', 'unknown')}",
            "",
            str(agent_answer),
        ]
        if warnings:
            lines.extend(["", "warnings:"])
            lines.extend(f"- {warning}" for warning in warnings)
        return "\n".join(lines)
