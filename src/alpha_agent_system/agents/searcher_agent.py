from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from alpha_agent_system.core.agent_loop import AgentLoop
from alpha_agent_system.core.llm_client import LLMClient
from alpha_agent_system.core.tool_registry import ToolRegistry
from alpha_agent_system.core.trace import TraceWriter
from alpha_agent_system.prompts.searcher_prompt import SEARCHER_SYSTEM_PROMPT
from alpha_agent_system.tools.type_n_tool import (
    build_phase1_pool,
    generate_two_phase_report,
    merge_final_candidates,
    run_phase1_scan,
    run_phase2_filter,
)


TASK_NAMES = [
    "run_phase1_scan",
    "build_phase1_pool",
    "run_phase2_filter",
    "merge_final_candidates",
    "generate_two_phase_report",
]


class SearcherAgent:
    def __init__(
        self,
        trade_date: str,
        type_n_project_root: str | Path,
        run_dir: str | Path,
        llm_client: LLMClient | None = None,
        max_steps: int = 8,
    ) -> None:
        self.trade_date = trade_date
        self.type_n_project_root = Path(type_n_project_root).resolve()
        self.run_dir = Path(run_dir).resolve()
        self.trace_path = self.run_dir / "search_trace.jsonl"
        self.task_plan_path = self.run_dir / "task_plan.json"
        self.phase1_hits_path = self.run_dir / "phase1_hits.csv"
        self.phase1_status_path = self.run_dir / "phase1_status.json"
        self.phase1_pool_path = self.run_dir / "phase1_pool.csv"
        self.pool_status_path = self.run_dir / "pool_status.json"
        self.phase2_scores_path = self.run_dir / "phase2_scores.csv"
        self.phase2_status_path = self.run_dir / "phase2_status.json"
        self.final_candidates_path = self.run_dir / "final_candidates.csv"
        self.final_status_path = self.run_dir / "final_status.json"
        self.report_path = self.run_dir / "type_n_two_phase_report.md"
        self.final_answer_path = self.run_dir / "final_answer.md"
        self.llm_client = llm_client or LLMClient()
        self.max_steps = max_steps

    def run(self) -> dict[str, Any]:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._init_task_plan()

        registry = ToolRegistry()
        registry.register("run_phase1_scan", self._run_phase1_scan)
        registry.register("build_phase1_pool", self._build_phase1_pool)
        registry.register("run_phase2_filter", self._run_phase2_filter)
        registry.register("merge_final_candidates", self._merge_final_candidates)
        registry.register("generate_two_phase_report", self._generate_two_phase_report)

        loop = AgentLoop(
            llm_client=self.llm_client,
            tool_registry=registry,
            trace_writer=TraceWriter(self.trace_path),
            system_prompt=SEARCHER_SYSTEM_PROMPT,
            max_steps=self.max_steps,
        )
        result = loop.run(self._build_task())
        final_answer = self._format_final_answer(result)
        self.final_answer_path.write_text(final_answer + "\n", encoding="utf-8")

        return {
            "ok": bool(result["finished"]) and self.report_path.exists(),
            "trade_date": self.trade_date,
            "run_dir": str(self.run_dir),
            "search_trace_path": str(self.trace_path),
            "task_plan_path": str(self.task_plan_path),
            "phase1_hits_path": str(self.phase1_hits_path),
            "phase1_status_path": str(self.phase1_status_path),
            "phase1_pool_path": str(self.phase1_pool_path),
            "pool_status_path": str(self.pool_status_path),
            "phase2_scores_path": str(self.phase2_scores_path),
            "phase2_status_path": str(self.phase2_status_path),
            "final_candidates_path": str(self.final_candidates_path),
            "final_status_path": str(self.final_status_path),
            "report_path": str(self.report_path),
            "final_answer_path": str(self.final_answer_path),
            "final_answer": final_answer,
            "last_result": result["last_result"],
        }

    def _build_task(self) -> str:
        return (
            "请执行 Type-N two_phase 搜索的 decomposed workflow。\n"
            f"target_date: {self.trade_date}\n"
            f"type_n_project_root: {self.type_n_project_root}\n"
            f"search_run_dir: {self.run_dir}\n"
            f"search_trace_path: {self.trace_path}\n"
            f"task_plan_path: {self.task_plan_path}\n"
            f"phase1_hits_path: {self.phase1_hits_path}\n"
            f"phase1_pool_path: {self.phase1_pool_path}\n"
            f"phase2_scores_path: {self.phase2_scores_path}\n"
            f"final_candidates_path: {self.final_candidates_path}\n"
            f"report_path: {self.report_path}\n"
            "默认 reviewer_config 使用 ma120_trend_soft；默认 final_merge_config 使用 default。"
        )

    def _run_phase1_scan(
        self,
        target_date: str | None = None,
        anchor_lookback_days: int = 20,
        phase1_top_n: int = 20,
        output_path: str | None = None,
        status_path: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        resolved_output = self._path_in_run_dir(output_path or self.phase1_hits_path)
        resolved_status = self._path_in_run_dir(status_path or self.phase1_status_path)
        if resolved_output is None or resolved_status is None:
            return self._reject_path("run_phase1_scan")
        result = run_phase1_scan(
            project_root=self.type_n_project_root,
            target_date=target_date or self.trade_date,
            anchor_lookback_days=anchor_lookback_days,
            phase1_top_n=phase1_top_n,
            raw_daily_dir=self._default_raw_daily_dir(),
            output_path=resolved_output,
            status_path=resolved_status,
        )
        self._record_task("run_phase1_scan", result, resolved_output)
        return result

    def _build_phase1_pool(
        self,
        phase1_hits_path: str | None = None,
        output_path: str | None = None,
        status_path: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        resolved_hits = self._path_in_run_dir(phase1_hits_path or self.phase1_hits_path)
        resolved_output = self._path_in_run_dir(output_path or self.phase1_pool_path)
        resolved_status = self._path_in_run_dir(status_path or self.pool_status_path)
        if resolved_hits is None or resolved_output is None or resolved_status is None:
            return self._reject_path("build_phase1_pool")
        result = build_phase1_pool(
            project_root=self.type_n_project_root,
            phase1_hits_path=resolved_hits,
            target_date=self.trade_date,
            output_path=resolved_output,
            status_path=resolved_status,
        )
        self._record_task("build_phase1_pool", result, resolved_output)
        return result

    def _run_phase2_filter(
        self,
        target_date: str | None = None,
        phase1_pool_path: str | None = None,
        reviewer_config: str | None = "ma120_trend_soft",
        output_path: str | None = None,
        status_path: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        resolved_pool = self._path_in_run_dir(phase1_pool_path or self.phase1_pool_path)
        resolved_output = self._path_in_run_dir(output_path or self.phase2_scores_path)
        resolved_status = self._path_in_run_dir(status_path or self.phase2_status_path)
        if resolved_pool is None or resolved_output is None or resolved_status is None:
            return self._reject_path("run_phase2_filter")
        result = run_phase2_filter(
            project_root=self.type_n_project_root,
            target_date=target_date or self.trade_date,
            phase1_pool_path=resolved_pool,
            raw_daily_dir=self._default_raw_daily_dir(),
            reviewer_config=reviewer_config,
            output_path=resolved_output,
            status_path=resolved_status,
        )
        self._record_task("run_phase2_filter", result, resolved_output)
        return result

    def _merge_final_candidates(
        self,
        phase1_pool_path: str | None = None,
        phase2_scores_path: str | None = None,
        final_merge_config: str | None = "default",
        output_path: str | None = None,
        status_path: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        resolved_pool = self._path_in_run_dir(phase1_pool_path or self.phase1_pool_path)
        resolved_scores = self._path_in_run_dir(phase2_scores_path or self.phase2_scores_path)
        resolved_output = self._path_in_run_dir(output_path or self.final_candidates_path)
        resolved_status = self._path_in_run_dir(status_path or self.final_status_path)
        if resolved_pool is None or resolved_scores is None or resolved_output is None or resolved_status is None:
            return self._reject_path("merge_final_candidates")
        result = merge_final_candidates(
            project_root=self.type_n_project_root,
            phase1_pool_path=resolved_pool,
            phase2_scores_path=resolved_scores,
            final_merge_config=final_merge_config,
            output_path=resolved_output,
            status_path=resolved_status,
        )
        self._record_task("merge_final_candidates", result, resolved_output)
        return result

    def _generate_two_phase_report(
        self,
        output_path: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        resolved_output = self._path_in_run_dir(output_path or self.report_path)
        if resolved_output is None:
            return self._reject_path("generate_two_phase_report")
        result = generate_two_phase_report(
            project_root=self.type_n_project_root,
            phase1_hits_path=self.phase1_hits_path,
            phase1_pool_path=self.phase1_pool_path,
            phase2_scores_path=self.phase2_scores_path,
            final_candidates_path=self.final_candidates_path,
            status_paths=[
                self.phase1_status_path,
                self.pool_status_path,
                self.phase2_status_path,
                self.final_status_path,
            ],
            output_path=resolved_output,
        )
        self._record_task("generate_two_phase_report", result, resolved_output)
        return result

    def _default_raw_daily_dir(self) -> Path:
        candidates = [
            self.type_n_project_root.parent / "shared_data" / "raw" / "daily" / "parquet_daily_cache_5-12",
            self.type_n_project_root.parent / "shared_data" / "raw" / "daily" / "parquet_daily_cache",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate.resolve()
        return candidates[0].resolve()

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

    def _init_task_plan(self) -> None:
        plan = {
            "strategy": "type_n",
            "workflow": "two_phase",
            "mode": "decomposed",
            "target_date": self.trade_date,
            "tasks": [
                {"name": name, "status": "pending", "output": self._default_task_output(name)}
                for name in TASK_NAMES
            ],
        }
        self._write_json(self.task_plan_path, plan)

    def _record_task(self, name: str, result: dict[str, Any], output_path: Path) -> None:
        plan = self._read_json(self.task_plan_path)
        status_payload = result.get("status", {})
        task_status = str(status_payload.get("status") or ("success" if result.get("ok") else "failed"))
        for task in plan.get("tasks", []):
            if task.get("name") != name:
                continue
            task["status"] = task_status
            task["ok"] = bool(result.get("ok"))
            task["output"] = output_path.name
            task["returncode"] = result.get("returncode")
            if status_payload.get("warnings"):
                task["warnings"] = status_payload.get("warnings")
            if status_payload.get("errors"):
                task["errors"] = status_payload.get("errors")
            break
        self._write_json(self.task_plan_path, plan)

    def _default_task_output(self, name: str) -> str:
        return {
            "run_phase1_scan": "phase1_hits.csv",
            "build_phase1_pool": "phase1_pool.csv",
            "run_phase2_filter": "phase2_scores.csv",
            "merge_final_candidates": "final_candidates.csv",
            "generate_two_phase_report": "type_n_two_phase_report.md",
        }[name]

    def _reject_path(self, tool: str) -> dict[str, Any]:
        return {"ok": False, "tool": tool, "error": "all output/input paths must stay inside search run_dir"}

    def _format_final_answer(self, loop_result: dict[str, Any]) -> str:
        final_count = self._count_csv_rows(self.final_candidates_path)
        return "\n".join(
            [
                str(loop_result["final_answer"]),
                "",
                f"search_trace: {self.trace_path}",
                f"task_plan: {self.task_plan_path}",
                f"report: {self.report_path}",
                f"final_candidates: {self.final_candidates_path}",
                f"final_candidates_count: {final_count}",
            ]
        )

    @staticmethod
    def _count_csv_rows(path: Path) -> int:
        try:
            return int(len(pd.read_csv(path)))
        except Exception:  # noqa: BLE001
            return 0

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
