from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the data mining group multi-agent workflow.")
    parser.add_argument("--date", required=True, help="Trade date, for example 2026-05-23.")
    parser.add_argument("--daily-cache-root", required=True, help="Path to the external daily-cache project.")
    parser.add_argument("--type-n-root", required=True, help="Path to the external type_n_search project.")
    parser.add_argument("--max-steps", type=int, default=8, help="Maximum group-agent LLM/tool loop steps.")
    args = parser.parse_args()

    daily_cache_root = _resolve_path(args.daily_cache_root)
    type_n_root = _resolve_path(args.type_n_root)
    run_dir = PROJECT_ROOT / "runs" / "data_mining_group" / args.date

    from alpha_agent_system.agents.data_mining_group_agent import DataMiningGroupAgent

    agent = DataMiningGroupAgent(
        trade_date=args.date,
        daily_cache_root=daily_cache_root,
        type_n_root=type_n_root,
        run_dir=run_dir,
        max_steps=args.max_steps,
    )
    result = agent.run()

    print(f"run_dir: {result['run_dir']}")
    print(f"group_trace: {result['group_trace_path']}")
    print(f"workflow_status: {result['workflow_status_path']}")
    print(f"data_mining_report: {result['data_mining_report_path']}")
    print(f"final_answer: {result['final_answer_path']}")
    print(f"daily_cache_run_dir: {result['daily_cache_run_dir']}")
    print(f"search_run_dir: {result['search_run_dir']}")


def _resolve_path(path: str) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate.resolve()


if __name__ == "__main__":
    main()
