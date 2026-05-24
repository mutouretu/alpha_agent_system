from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from alpha_agent_system.core.agent_loop import AgentLoop
from alpha_agent_system.core.llm_client import LLMClient
from alpha_agent_system.core.tool_registry import ToolRegistry
from alpha_agent_system.core.trace import TraceWriter
from alpha_agent_system.prompts.daily_cache_prompt import DAILY_CACHE_SYSTEM_PROMPT
from alpha_agent_system.tools.daily_cache_tool import (
    check_daily_cache_status,
    generate_cache_report,
    run_daily_cache_update,
)


class DailyCacheAgent:
    def __init__(
        self,
        trade_date: str,
        daily_cache_project_root: str | Path,
        run_dir: str | Path,
        llm_client: LLMClient | None = None,
        max_steps: int = 6,
    ) -> None:
        self.trade_date = trade_date
        self.daily_cache_project_root = Path(daily_cache_project_root).resolve()
        self.run_dir = Path(run_dir).resolve()
        self.trace_path = self.run_dir / "trace.jsonl"
        self.status_path = self.run_dir / "cache_status.json"
        self.report_path = self.run_dir / "cache_report.md"
        self.final_answer_path = self.run_dir / "final_answer.md"
        self.llm_client = llm_client or LLMClient()
        self.max_steps = max_steps

    def run(self) -> dict[str, Any]:
        self.run_dir.mkdir(parents=True, exist_ok=True)

        registry = ToolRegistry()
        registry.register("check_daily_cache_status", self._check_daily_cache_status)
        registry.register("run_daily_cache_update", self._run_daily_cache_update)
        registry.register("generate_cache_report", self._generate_cache_report)

        loop = AgentLoop(
            llm_client=self.llm_client,
            tool_registry=registry,
            trace_writer=TraceWriter(self.trace_path),
            system_prompt=DAILY_CACHE_SYSTEM_PROMPT,
            max_steps=self.max_steps,
        )
        result = loop.run(self._build_task())
        self._ensure_status_and_report(result)
        self.final_answer_path.write_text(str(result["final_answer"]) + "\n", encoding="utf-8")

        status = self._read_status()
        return {
            "ok": bool(status.get("ok", result["finished"])),
            "can_continue": bool(status.get("can_continue", True)),
            "trade_date": self.trade_date,
            "run_dir": str(self.run_dir),
            "trace_path": str(self.trace_path),
            "cache_status_path": str(self.status_path),
            "cache_report_path": str(self.report_path),
            "final_answer_path": str(self.final_answer_path),
            "final_answer": result["final_answer"],
            "status": status,
            "last_result": result["last_result"],
        }

    def _build_task(self) -> str:
        return (
            "请完成 daily-cache 数据检查/更新工作流。\n"
            f"trade_date: {self.trade_date}\n"
            f"daily_cache_project_root: {self.daily_cache_project_root}\n"
            f"cache_status_path: {self.status_path}\n"
            f"cache_report_path: {self.report_path}\n"
            "所有输出文件必须位于本次 daily_cache run_dir 内。"
        )

    def _check_daily_cache_status(self, trade_date: str | None = None, **_: Any) -> dict[str, Any]:
        return check_daily_cache_status(
            project_root=self.daily_cache_project_root,
            trade_date=trade_date or self.trade_date,
        )

    def _run_daily_cache_update(
        self,
        trade_date: str | None = None,
        output_status_path: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        resolved_status = self._path_in_run_dir(output_status_path or self.status_path)
        if resolved_status is None:
            return {"ok": False, "tool": "run_daily_cache_update", "error": "output_status_path must stay inside run_dir"}
        return run_daily_cache_update(
            project_root=self.daily_cache_project_root,
            trade_date=trade_date or self.trade_date,
            output_status_path=resolved_status,
        )

    def _generate_cache_report(
        self,
        status_path: str | None = None,
        output_path: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        resolved_status = self._path_in_run_dir(status_path or self.status_path)
        resolved_output = self._path_in_run_dir(output_path or self.report_path)
        if resolved_status is None or resolved_output is None:
            return {
                "ok": False,
                "tool": "generate_cache_report",
                "error": "status_path and output_path must stay inside run_dir",
            }
        return generate_cache_report(resolved_status, resolved_output)

    def _ensure_status_and_report(self, result: dict[str, Any]) -> None:
        if not self.status_path.exists():
            fallback_status = {
                "ok": False,
                "can_continue": True,
                "trade_date": self.trade_date,
                "project_root": str(self.daily_cache_project_root),
                "warning": "DailyCacheAgent finished without cache_status.json; generated fallback status.",
                "agent_result": result,
            }
            self.status_path.write_text(
                json.dumps(fallback_status, ensure_ascii=False, indent=2, default=str) + "\n",
                encoding="utf-8",
            )
        if not self.report_path.exists():
            generate_cache_report(self.status_path, self.report_path)

    def _read_status(self) -> dict[str, Any]:
        try:
            return json.loads(self.status_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {"ok": False, "can_continue": True, "error": f"Failed to read status: {self.status_path}"}

    def _path_in_run_dir(self, path: str | Path) -> Path | None:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.run_dir / candidate
        resolved = candidate.resolve()
        try:
            resolved.relative_to(self.run_dir)
        except ValueError:
            return None
        return resolved
