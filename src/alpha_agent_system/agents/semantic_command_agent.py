from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from alpha_agent_system.core.agent_loop import AgentLoop
from alpha_agent_system.core.llm_client import LLMClient
from alpha_agent_system.core.tool_registry import ToolRegistry
from alpha_agent_system.core.trace import TraceWriter
from alpha_agent_system.prompts.semantic_command_prompt import SEMANTIC_COMMAND_SYSTEM_PROMPT
from alpha_agent_system.tools.command_tools import (
    read_workflow_status,
    resolve_trade_date,
    run_data_mining_group_agent,
)


class SemanticCommandAgent:
    def __init__(
        self,
        command: str,
        daily_cache_root: str | Path,
        type_n_root: str | Path,
        run_dir: str | Path,
        project_root: str | Path,
        llm_client: LLMClient | None = None,
        max_steps: int = 8,
        group_max_steps: int = 8,
    ) -> None:
        self.command = command
        self.daily_cache_root = Path(daily_cache_root).resolve()
        self.type_n_root = Path(type_n_root).resolve()
        self.run_dir = Path(run_dir).resolve()
        self.project_root = Path(project_root).resolve()
        self.command_trace_path = self.run_dir / "command_trace.jsonl"
        self.command_result_path = self.run_dir / "command_result.json"
        self.final_answer_path = self.run_dir / "final_answer.md"
        self.llm_client = llm_client or LLMClient()
        self.max_steps = max_steps
        self.group_max_steps = group_max_steps
        self.resolved_trade_date: str | None = None
        self.search_mode: str = self._infer_search_mode_from_command(command)
        self.group_result: dict[str, Any] | None = None
        self.workflow_status: dict[str, Any] | None = None

    def run(self) -> dict[str, Any]:
        self.run_dir.mkdir(parents=True, exist_ok=True)

        registry = ToolRegistry()
        registry.register("resolve_trade_date", self._resolve_trade_date)
        registry.register("run_data_mining_group_agent", self._run_data_mining_group_agent)
        registry.register("read_workflow_status", self._read_workflow_status)

        loop = AgentLoop(
            llm_client=self.llm_client,
            tool_registry=registry,
            trace_writer=TraceWriter(self.command_trace_path),
            system_prompt=SEMANTIC_COMMAND_SYSTEM_PROMPT,
            max_steps=self.max_steps,
        )
        result = loop.run(self._build_task())
        command_result = self._build_command_result(result)
        self._write_json(self.command_result_path, command_result)
        final_answer = self._format_final_answer(result["final_answer"], command_result)
        self.final_answer_path.write_text(final_answer + "\n", encoding="utf-8")

        return {
            "ok": bool(command_result.get("ok")),
            "status": command_result.get("status"),
            "run_dir": str(self.run_dir),
            "command_trace_path": str(self.command_trace_path),
            "command_result_path": str(self.command_result_path),
            "final_answer_path": str(self.final_answer_path),
            "trade_date": self.resolved_trade_date,
            "final_answer": final_answer,
        }

    def _build_task(self) -> str:
        return (
            "请解析并执行用户自然语言命令。\n"
            f"user_command: {self.command}\n"
            f"daily_cache_root: {self.daily_cache_root}\n"
            f"type_n_root: {self.type_n_root}\n"
            f"semantic_command_run_dir: {self.run_dir}\n"
            f"data_mining_group_runs_root: {self.project_root / 'runs' / 'data_mining_group'}\n"
            "如果用户明确说“两阶段”“二阶段”“two phase”，search_mode 应为 two_phase。\n"
            "只能调用 DataMiningGroupAgent 这个小组级 Agent，不能直接调用底层项目脚本。"
        )

    def _resolve_trade_date(
        self,
        date_text: str | None = None,
        resolved_date: str | None = None,
        intent: str | None = None,
        confidence: float | None = None,
        search_mode: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        if search_mode in {"single_phase", "two_phase"}:
            self.search_mode = search_mode
        elif intent and ("two_phase" in intent or "两阶段" in intent or "二阶段" in intent):
            self.search_mode = "two_phase"
        result = resolve_trade_date(
            date_text=date_text or self.command,
            resolved_date=resolved_date,
            intent=intent,
            confidence=confidence,
        )
        if result.get("ok"):
            self.resolved_trade_date = str(result["trade_date"])
        return result

    def _run_data_mining_group_agent(
        self,
        trade_date: str | None = None,
        search_mode: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        resolved_date = trade_date or self.resolved_trade_date
        if not resolved_date:
            return {
                "ok": False,
                "tool": "run_data_mining_group_agent",
                "error": "trade_date is required. Call resolve_trade_date first.",
            }
        if search_mode in {"single_phase", "two_phase"}:
            self.search_mode = search_mode

        self.resolved_trade_date = resolved_date
        group_run_dir = self.project_root / "runs" / "data_mining_group" / resolved_date
        result = run_data_mining_group_agent(
            trade_date=resolved_date,
            daily_cache_root=self.daily_cache_root,
            type_n_root=self.type_n_root,
            run_dir=group_run_dir,
            llm_client=self.llm_client,
            max_steps=self.group_max_steps,
            search_mode=self.search_mode,
        )
        self.group_result = result
        return result

    def _read_workflow_status(self, path: str | None = None, **_: Any) -> dict[str, Any]:
        workflow_path = path
        if workflow_path is None and self.group_result:
            workflow_path = self.group_result.get("workflow_status_path") or self.group_result.get("agent_result", {}).get(
                "workflow_status_path"
            )
        if workflow_path is None:
            return {"ok": False, "tool": "read_workflow_status", "error": "workflow status path is missing."}

        resolved_path = self._path_in_allowed_outputs(workflow_path)
        if resolved_path is None:
            return {"ok": False, "tool": "read_workflow_status", "error": "path must stay inside allowed run outputs."}

        result = read_workflow_status(resolved_path)
        if result.get("ok"):
            self.workflow_status = result["workflow_status"]
        return result

    def _build_command_result(self, loop_result: dict[str, Any]) -> dict[str, Any]:
        status = self.workflow_status or {}
        group_agent_result = self.group_result.get("agent_result", {}) if self.group_result else {}
        return {
            "ok": bool(status.get("ok", loop_result.get("finished", False))),
            "status": status.get("status", "unknown" if self.group_result else "not_executed"),
            "command": self.command,
            "trade_date": self.resolved_trade_date,
            "search_mode": self.search_mode,
            "semantic_run_dir": str(self.run_dir),
            "command_trace_path": str(self.command_trace_path),
            "workflow_status": status,
            "data_mining_group_result": group_agent_result,
            "loop_result": loop_result,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }

    @staticmethod
    def _format_final_answer(agent_answer: Any, command_result: dict[str, Any]) -> str:
        lines = [
            f"command_status = {command_result.get('status')}",
            f"trade_date = {command_result.get('trade_date')}",
            f"search_mode = {command_result.get('search_mode')}",
            "",
            str(agent_answer),
        ]
        group_result = command_result.get("data_mining_group_result", {})
        if group_result:
            lines.extend(
                [
                    "",
                    f"workflow_status_path: {group_result.get('workflow_status_path')}",
                    f"data_mining_report_path: {group_result.get('data_mining_report_path')}",
                ]
            )
        return "\n".join(lines)

    def _path_in_allowed_outputs(self, path: str | Path) -> Path | None:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.project_root / candidate
        resolved = candidate.resolve()
        allowed_roots = [
            self.project_root / "runs" / "semantic_commands",
            self.project_root / "runs" / "data_mining_group",
        ]
        for allowed_root in allowed_roots:
            try:
                resolved.relative_to(allowed_root.resolve())
                return resolved
            except ValueError:
                continue
        return None

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    @staticmethod
    def _infer_search_mode_from_command(command: str) -> str:
        lowered = command.lower()
        if any(token in lowered for token in ["两阶段", "二阶段", "two-phase", "two phase", "2-phase", "2 phase"]):
            return "two_phase"
        return "single_phase"
