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

Set `OPENAI_API_KEY` in `.env`. `OPENAI_MODEL` defaults to `gpt-5.5`.

## Run

```bash
python scripts/run_type_n_agent.py \
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
