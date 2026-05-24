from __future__ import annotations

import json
from typing import Any

from alpha_agent_system.core.llm_client import LLMClient
from alpha_agent_system.core.tool_registry import ToolRegistry
from alpha_agent_system.core.trace import TraceWriter


class AgentLoop:
    """Minimal JSON-action agent loop backed by a whitelist tool registry."""

    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        trace_writer: TraceWriter,
        system_prompt: str,
        max_steps: int = 8,
    ) -> None:
        self.llm_client = llm_client
        self.tool_registry = tool_registry
        self.trace_writer = trace_writer
        self.system_prompt = system_prompt
        self.max_steps = max_steps

    def run(self, task: str) -> dict[str, Any]:
        messages: list[dict[str, str]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": task},
        ]

        for step in range(1, self.max_steps + 1):
            try:
                raw_response = self.llm_client.complete(messages)
                decision = self._parse_decision(raw_response)
            except Exception as exc:  # noqa: BLE001
                result = {"ok": False, "error": f"LLM response failed: {exc}"}
                self.trace_writer.write({"step": step, "event": "llm_error", "result": result})
                return {"finished": False, "final_answer": result["error"], "last_result": result}

            action = str(decision.get("action", ""))
            args = decision.get("args", {})
            if not isinstance(args, dict):
                args = {}

            if action == "finish":
                finish_args = decision.get("args", {})
                if not isinstance(finish_args, dict):
                    finish_args = {}
                final_answer = str(finish_args.get("summary") or decision.get("thought", "finished"))
                self.trace_writer.write(
                    {
                        "step": step,
                        "event": "finish",
                        "raw_response": raw_response,
                        "decision": decision,
                        "result": {"ok": True},
                    }
                )
                return {"finished": True, "final_answer": final_answer, "last_result": {"ok": True}}

            tool_result = self.tool_registry.call(action, args)
            self.trace_writer.write(
                {
                    "step": step,
                    "event": "tool_call",
                    "raw_response": raw_response,
                    "decision": decision,
                    "result": tool_result,
                }
            )

            messages.append({"role": "assistant", "content": raw_response})
            messages.append(
                {
                    "role": "user",
                    "content": "Tool result JSON:\n" + json.dumps(tool_result, ensure_ascii=False, default=str),
                }
            )

        final_answer = f"Agent stopped after reaching max_steps={self.max_steps}."
        result = {"ok": False, "error": final_answer}
        self.trace_writer.write({"step": self.max_steps + 1, "event": "max_steps", "result": result})
        return {"finished": False, "final_answer": final_answer, "last_result": result}

    @staticmethod
    def _parse_decision(raw_response: str) -> dict[str, Any]:
        text = raw_response.strip()
        if text.startswith("```"):
            text = text.strip("`").strip()
            if text.startswith("json"):
                text = text[4:].strip()
        if not text.startswith("{"):
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end >= start:
                text = text[start : end + 1]

        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("LLM response must be a JSON object.")
        if "action" not in data:
            raise ValueError("LLM response missing required field: action.")
        if "args" not in data:
            data["args"] = {}
        return data
