# alpha_agent_system

External LLM-in-the-loop agent system for orchestrating Type-N stock scanning through the separate `type_n_search` project.

This project is intentionally a thin upper-layer scheduler:

- It does not modify `type_n_search`.
- It only calls registered whitelist tools.
- It does not place orders or connect to broker APIs.
- It does not tune model parameters.

## Setup

```bash
cd alpha_agent_system
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Set the provider and API key in `.env`.

For OpenAI:

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4.1-mini
```

For DeepSeek:

```env
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=...
DEEPSEEK_MODEL=deepseek-chat
```

`LLM_PROVIDER` supports `openai` and `deepseek`. OpenAI uses `client.responses.create(...)`. DeepSeek uses the OpenAI Python SDK with `base_url=https://api.deepseek.com`; if `responses.create(...)` is unavailable, it automatically falls back to `chat.completions.create(...)`.

## Semantic Command

Recommended natural-language entry point:

```bash
python scripts/run_semantic_command.py "执行今天的 type-n 选股"
```

If the virtual environment is not activated:

```bash
.venv/bin/python scripts/run_semantic_command.py "执行今天的 type-n 选股"
```

Project paths are read from `configs/projects.yaml`:

```yaml
projects:
  daily_cache:
    root: "../build-daily-cache"
  type_n_search:
    root: "../type_n_search"
```

Agent step limits are read from `configs/agents.yaml`.

Daily data used by SearcherAgent is configured in `configs/projects.yaml`:

```yaml
data:
  raw_daily_dir: "../shared_data/raw/daily/parquet_daily_cache"
```

Debug overrides are still available:

```bash
python scripts/run_semantic_command.py "执行今天的 type-n 选股" \
  --daily-cache-root ../build-daily-cache \
  --type-n-root ../type_n_search
```

## Direct Type-N Run

```bash
python scripts/run_type_n_agent.py \
  --date 2026-05-23 \
  --type-n-root ../type_n_search
```

If the virtual environment is not activated, run the same command with the venv interpreter:

```bash
.venv/bin/python scripts/run_type_n_agent.py \
  --date 2026-05-23 \
  --type-n-root ../type_n_search
```

Outputs are written to:

```text
runs/type_n/YYYY-MM-DD/
  trace.jsonl
  candidates.csv
  summary.md
  final_answer.md
```

The first Type-N tool adapter expects `type_n_search` to expose:

```bash
python scripts/run_scan.py --date YYYY-MM-DD --output /path/to/candidates.csv
```

If that script does not exist yet, the agent returns a clear adapter error without changing `type_n_search`.

## Type-N Validation

SearcherAgent now supports two validation modes around the decomposed workflow:

- Production mode: real strategy mode. It uses `anchor_lookback_days`, `reviewer_config`, and normal final scoring/sorting.
- Compat mode: validation mode. It checks whether decomposed output can reproduce the fast path under equivalent parameters; it can infer `anchor_start_date` and `phase1_top_n` from fast path directory names such as `target_2026-05-12_from_2026-04-27_top20`.

Recommended comparison commands:

```bash
python scripts/compare_two_phase_modes.py \
  --date 2026-05-12 \
  --type-n-root ../type_n_search \
  --comparison-mode compat
```

```bash
python scripts/compare_two_phase_modes.py \
  --date 2026-05-12 \
  --type-n-root ../type_n_search \
  --comparison-mode production
```

Before committing, run:

```bash
python3 -m py_compile scripts/compare_two_phase_modes.py
python3 -m py_compile src/alpha_agent_system/agents/searcher_agent.py
python3 -m py_compile src/alpha_agent_system/tools/type_n_tool.py
```

In `type_n_search`, run:

```bash
python3 -m py_compile scripts/run_type_n_task.py
python3 -m py_compile src/pipelines/type_n_tasks.py
```

End-to-end validation:

```bash
python scripts/run_semantic_command.py "执行今天的 type-n 选股"
```

Check that `command_trace` calls DataMiningGroupAgent, `group_trace` calls SearcherAgent, `search_trace` contains the five stage actions, `final_candidates.csv` exists, and no task in `task_plan.json` remains pending.
