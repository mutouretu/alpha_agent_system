from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


ToolResult = dict[str, Any]
ToolFn = Callable[..., ToolResult]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    fn: ToolFn
    description: str = ""


class ToolRegistry:
    """Whitelist registry for tools an LLM agent may call."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, name: str, fn: ToolFn, description: str = "") -> None:
        if name in self._tools:
            raise ValueError(f"Tool already registered: {name}")
        self._tools[name] = ToolSpec(name=name, fn=fn, description=description)

    def allowed_tools(self) -> list[str]:
        return sorted(self._tools)

    def call(self, name: str, args: dict[str, Any]) -> ToolResult:
        if name not in self._tools:
            return {
                "ok": False,
                "error": f"Tool is not registered: {name}",
                "allowed_tools": self.allowed_tools(),
            }

        try:
            return self._tools[name].fn(**args)
        except TypeError as exc:
            return {"ok": False, "tool": name, "error": f"Invalid tool arguments: {exc}"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "tool": name, "error": str(exc)}
