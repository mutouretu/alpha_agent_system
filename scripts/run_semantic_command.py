from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a natural-language alpha agent command.")
    parser.add_argument("command_text", nargs="?", help="Natural-language command, for example: 执行今天的 type-n 选股")
    parser.add_argument("--command", dest="command_flag", default=None, help="Natural-language command.")
    parser.add_argument("--daily-cache-root", default=None, help="Override path to the external daily-cache project.")
    parser.add_argument("--type-n-root", default=None, help="Override path to the external type_n_search project.")
    parser.add_argument("--max-steps", type=int, default=None, help="Override maximum semantic-command loop steps.")
    parser.add_argument("--group-max-steps", type=int, default=None, help="Override maximum DataMiningGroupAgent loop steps.")
    args = parser.parse_args()

    command = args.command_flag or args.command_text
    if not command:
        parser.error("command is required as a positional argument or via --command")

    projects_config = _load_yaml(PROJECT_ROOT / "configs" / "projects.yaml")
    agents_config = _load_yaml(PROJECT_ROOT / "configs" / "agents.yaml")

    daily_cache_root = _resolve_path(
        args.daily_cache_root
        or _get_config_value(projects_config, ["projects", "daily_cache", "root"], "projects.daily_cache.root")
    )
    type_n_root = _resolve_path(
        args.type_n_root
        or _get_config_value(projects_config, ["projects", "type_n_search", "root"], "projects.type_n_search.root")
    )
    max_steps = args.max_steps or int(
        _get_config_value(agents_config, ["agents", "semantic_command", "max_steps"], "agents.semantic_command.max_steps")
    )
    group_max_steps = args.group_max_steps or int(
        _get_config_value(agents_config, ["agents", "data_mining_group", "max_steps"], "agents.data_mining_group.max_steps")
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = PROJECT_ROOT / "runs" / "semantic_commands" / timestamp

    from alpha_agent_system.agents.semantic_command_agent import SemanticCommandAgent

    agent = SemanticCommandAgent(
        command=command,
        daily_cache_root=daily_cache_root,
        type_n_root=type_n_root,
        run_dir=run_dir,
        project_root=PROJECT_ROOT,
        max_steps=max_steps,
        group_max_steps=group_max_steps,
    )
    result = agent.run()

    print(f"run_dir: {result['run_dir']}")
    print(f"command_trace: {result['command_trace_path']}")
    print(f"command_result: {result['command_result_path']}")
    print(f"final_answer: {result['final_answer_path']}")
    print(f"status: {result['status']}")
    print(f"trade_date: {result['trade_date']}")


def _resolve_path(path: str) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate.resolve()


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        raise RuntimeError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise RuntimeError(f"Config file must contain a mapping: {path}")
    return data


def _get_config_value(config: dict, keys: list[str], display_name: str) -> object:
    value: object = config
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            raise RuntimeError(f"Missing config value `{display_name}`. Provide it in config or pass a CLI override.")
        value = value[key]
    if value in (None, ""):
        raise RuntimeError(f"Config value `{display_name}` is empty. Provide it in config or pass a CLI override.")
    return value


if __name__ == "__main__":
    main()
