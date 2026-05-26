from __future__ import annotations

from pathlib import Path
from typing import Any

from alpha_agent_system.core.agent_loop import AgentLoop
from alpha_agent_system.core.llm_client import LLMClient
from alpha_agent_system.core.tool_registry import ToolRegistry
from alpha_agent_system.core.trace import TraceWriter
from alpha_agent_system.prompts.type_n_runner_prompt import TYPE_N_RUNNER_SYSTEM_PROMPT
from alpha_agent_system.tools.file_tools import DEFAULT_REQUIRED_COLUMNS, read_csv_head, validate_csv
from alpha_agent_system.tools.report_tools import generate_type_n_summary
from alpha_agent_system.tools.type_n_tool import run_type_n_search, run_type_n_two_phase_search


class TypeNRunnerAgent:
    def __init__(
        self,
        trade_date: str,
        type_n_project_root: str | Path,
        run_dir: str | Path,
        llm_client: LLMClient | None = None,
        max_steps: int = 8,
        search_mode: str = "single_phase",
    ) -> None:
        self.trade_date = trade_date
        self.type_n_project_root = Path(type_n_project_root).resolve()
        self.run_dir = Path(run_dir).resolve()
        self.candidates_path = self.run_dir / "candidates.csv"
        self.two_phase_output_dir = self.run_dir / "two_phase"
        self.summary_path = self.run_dir / "summary.md"
        self.final_answer_path = self.run_dir / "final_answer.md"
        self.trace_path = self.run_dir / "trace.jsonl"
        self.llm_client = llm_client or LLMClient()
        self.max_steps = max_steps
        self.search_mode = search_mode

    def run(self) -> dict[str, Any]:
        self.run_dir.mkdir(parents=True, exist_ok=True)

        registry = ToolRegistry()
        registry.register("run_type_n_search", self._run_type_n_search)
        registry.register("run_type_n_two_phase_search", self._run_type_n_two_phase_search)
        registry.register("validate_csv", self._validate_csv)
        registry.register("read_csv_head", self._read_csv_head)
        registry.register("generate_type_n_summary", self._generate_type_n_summary)

        loop = AgentLoop(
            llm_client=self.llm_client,
            tool_registry=registry,
            trace_writer=TraceWriter(self.trace_path),
            system_prompt=self._system_prompt(),
            max_steps=self.max_steps,
        )
        result = loop.run(self._build_task())
        self.final_answer_path.write_text(str(result["final_answer"]) + "\n", encoding="utf-8")
        workflow_ok = bool(result["finished"] and self.candidates_path.exists() and self.summary_path.exists())
        missing_outputs = [
            str(path)
            for path in [self.candidates_path, self.summary_path]
            if not path.exists()
        ]

        return {
            "ok": workflow_ok,
            "can_continue": False if not workflow_ok else True,
            "search_mode": self.search_mode,
            "trade_date": self.trade_date,
            "run_dir": str(self.run_dir),
            "trace_path": str(self.trace_path),
            "candidates_path": str(self.candidates_path),
            "two_phase_output_dir": str(self.two_phase_output_dir),
            "summary_path": str(self.summary_path),
            "final_answer_path": str(self.final_answer_path),
            "final_answer": result["final_answer"],
            "last_result": result["last_result"],
            "missing_outputs": missing_outputs,
            "error": "TypeNRunnerAgent did not generate required outputs." if missing_outputs else None,
        }

    def _build_task(self) -> str:
        return (
            "请完成一次 Type-N 选股扫描工作流。\n"
            f"search_mode: {self.search_mode}\n"
            f"trade_date: {self.trade_date}\n"
            f"type_n_project_root: {self.type_n_project_root}\n"
            f"candidates_path: {self.candidates_path}\n"
            f"two_phase_output_dir: {self.two_phase_output_dir}\n"
            f"summary_path: {self.summary_path}\n"
            "required_columns: trade_date, ts_code, name, model_score\n"
            "所有输出文件必须位于本次 run_dir 内。"
        )

    def _system_prompt(self) -> str:
        if self.search_mode != "two_phase":
            return TYPE_N_RUNNER_SYSTEM_PROMPT
        return (
            TYPE_N_RUNNER_SYSTEM_PROMPT
            + "\n\n当前任务模式是 two_phase。\n"
            "你必须调用 run_type_n_two_phase_search 生成两阶段结果，不要用单阶段 run_type_n_search。\n"
            "两阶段工具会调用 type_n_search/scripts/run_type_n_two_phase.py，并把 final_candidates.csv 标准化为 candidates.csv。\n"
            "调用两阶段工具后，继续 validate_csv、read_csv_head、generate_type_n_summary，然后 finish。"
        )

    def _run_type_n_search(
        self,
        trade_date: str | None = None,
        output_path: str | None = None,
        candidates_path: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        resolved_output = self._path_in_run_dir(output_path or candidates_path or self.candidates_path)
        if resolved_output is None:
            return {"ok": False, "tool": "run_type_n_search", "error": "output_path must stay inside run_dir"}
        return run_type_n_search(
            project_root=self.type_n_project_root,
            trade_date=trade_date or self.trade_date,
            output_path=resolved_output,
        )

    def _run_type_n_two_phase_search(
        self,
        trade_date: str | None = None,
        output_dir: str | None = None,
        candidates_path: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        # Keep adapter artifacts in a stable subdirectory even if the LLM proposes another run-local path.
        resolved_output_dir = self.two_phase_output_dir.resolve()
        resolved_candidates = self._path_in_run_dir(candidates_path or self.candidates_path)
        if resolved_output_dir is None or resolved_candidates is None:
            return {
                "ok": False,
                "tool": "run_type_n_two_phase_search",
                "error": "output_dir and candidates_path must stay inside run_dir",
            }
        return run_type_n_two_phase_search(
            project_root=self.type_n_project_root,
            trade_date=trade_date or self.trade_date,
            output_dir=resolved_output_dir,
            candidates_path=resolved_candidates,
        )

    def _validate_csv(
        self,
        path: str | None = None,
        required_columns: list[str] | None = None,
        candidates_path: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        resolved_path = self._path_in_run_dir(path or candidates_path or self.candidates_path)
        if resolved_path is None:
            return {"ok": False, "tool": "validate_csv", "error": "path must stay inside run_dir"}
        return validate_csv(resolved_path, required_columns or DEFAULT_REQUIRED_COLUMNS)

    def _read_csv_head(
        self,
        path: str | None = None,
        n: int = 20,
        candidates_path: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        resolved_path = self._path_in_run_dir(path or candidates_path or self.candidates_path)
        if resolved_path is None:
            return {"ok": False, "tool": "read_csv_head", "error": "path must stay inside run_dir"}
        return read_csv_head(resolved_path, n=n)

    def _generate_type_n_summary(
        self,
        candidates_path: str | None = None,
        output_path: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        resolved_candidates = self._path_in_run_dir(candidates_path or self.candidates_path)
        resolved_output = self._path_in_run_dir(output_path or self.summary_path)
        if resolved_candidates is None or resolved_output is None:
            return {
                "ok": False,
                "tool": "generate_type_n_summary",
                "error": "candidates_path and output_path must stay inside run_dir",
            }
        return generate_type_n_summary(resolved_candidates, resolved_output)

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
