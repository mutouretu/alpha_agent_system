from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the external Type-N LLM agent.")
    parser.add_argument("--date", required=True, help="Trade date, for example 2026-05-23.")
    parser.add_argument("--type-n-root", default=None, help="Path to the external type_n_search project.")
    parser.add_argument("--max-steps", type=int, default=8, help="Maximum LLM/tool loop steps.")
    args = parser.parse_args()

    type_n_root = Path(args.type_n_root).expanduser() if args.type_n_root else _load_type_n_root()
    if not type_n_root.is_absolute():
        type_n_root = (PROJECT_ROOT / type_n_root).resolve()

    from alpha_agent_system.agents.type_n_runner_agent import TypeNRunnerAgent

    run_dir = PROJECT_ROOT / "runs" / "type_n" / args.date
    agent = TypeNRunnerAgent(
        trade_date=args.date,
        type_n_project_root=type_n_root,
        run_dir=run_dir,
        max_steps=args.max_steps,
    )
    result = agent.run()

    print(f"run_dir: {result['run_dir']}")
    print(f"trace: {result['trace_path']}")
    print(f"candidates: {result['candidates_path']}")
    print(f"summary: {result['summary_path']}")
    print(f"final_answer: {result['final_answer_path']}")


def _load_type_n_root() -> Path:
    import yaml

    config_path = PROJECT_ROOT / "configs" / "projects.yaml"
    with config_path.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    root = config.get("projects", {}).get("type_n_search", {}).get("root")
    if not root:
        raise RuntimeError("type_n_search root not configured. Pass --type-n-root or edit configs/projects.yaml.")
    return Path(root)


if __name__ == "__main__":
    main()
