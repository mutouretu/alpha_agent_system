# SearcherAgent Task Decomposition

## Core Boundary

`alpha_agent_system` owns semantic entry and Agent orchestration.

`type_n_search` owns deterministic strategy capabilities: model loading, feature building, scoring, phase calculations, reviewer post-processing, final ranking, and report data generation.

SearcherAgent orchestrates strategy stages. It does not orchestrate model details.

## First-Level Strategy Tasks

SearcherAgent can call only these Type-N two-phase stage tools:

1. `run_phase1_scan`
2. `build_phase1_pool`
3. `run_phase2_filter`
4. `merge_final_candidates`
5. `generate_two_phase_report`

`phase1` and `phase2` are first-level strategy stages, but they are not separate Agents. SearcherAgent remains the single search owner for the Type-N workflow.

## Phase1 Scan

`run_phase1_scan` is a batch task, not a single-anchor-date task.

SearcherAgent should call `run_phase1_scan` once for a Type-N two-phase run. It must not call it once per historical trading day.

The loop over past anchor dates belongs inside `type_n_search`. That tool decides anchor dates from `target_date` and `anchor_lookback_days`, loads Phase1 models once, scans all anchor dates, and writes `phase1_hits.csv`.

## Phase1 Pool

`build_phase1_pool` only aggregates `phase1_hits.csv` into stock-level `phase1_pool.csv`.

It does not run Phase1 models and does not rescan historical dates.

## Phase2 Filter

`run_phase2_filter` reads `phase1_pool.csv`, builds target-date features only for pool stocks, loads Phase2 models, and writes `phase2_scores.csv`.

Reviewer configuration can be passed to this stage, for example:

```text
run_phase2_filter(..., reviewer_config="ma120_trend_soft")
```

Reviewer logic remains inside `type_n_search`.

## Reviewer Boundary

Reviewer is a Phase2 or final-merge post-processing configuration. It is not a first-level Agent task.

Do not add SearcherAgent tools such as:

```text
run_review
review_ma60
review_ma120
review_overhang
review_volume
compare_reviewers
```

## Final Merge

`merge_final_candidates` merges `phase1_pool.csv` and `phase2_scores.csv`.

`final_merge_config` may control final scoring, sorting, or filtering, but it must not expose reviewer variants as independent Agent actions.

## Report

`generate_two_phase_report` reads all stage outputs and status JSON files, then writes `type_n_two_phase_report.md`.

## Fast Path And Decomposed Path

The existing one-shot full workflow remains the production fast path when available:

```text
run_type_n_two_phase
```

The decomposed path is used to validate SearcherAgent orchestration:

```text
run_phase1_scan
-> build_phase1_pool
-> run_phase2_filter
-> merge_final_candidates
-> generate_two_phase_report
```

Both paths should coexist.

## Output Contract

SearcherAgent writes outputs under:

```text
runs/data_mining_group/{date}/search/
```

Expected files:

```text
search_trace.jsonl
task_plan.json
phase1_hits.csv
phase1_status.json
phase1_pool.csv
pool_status.json
phase2_scores.csv
phase2_status.json
final_candidates.csv
final_status.json
type_n_two_phase_report.md
final_answer.md
```

`task_plan.json` records each first-level task status and output path.

## Principles

1. Agent 编排策略阶段，不编排模型细节。
2. phase1 / phase2 是一级策略阶段。
3. `run_phase1_scan` 是批处理任务，不是单日任务。
4. SearcherAgent 不应该对过去 N 个交易日调用 N 次 `run_phase1_scan`。
5. 过去 N 个交易日的循环属于 `type_n_search` 内部实现细节。
6. `build_phase1_pool` 只负责聚合 `phase1_hits`，不再运行 phase1 模型。
7. reviewer 是 phase2 或 final merge 内部的后处理配置，不是一级 Agent 任务。
8. SearcherAgent 负责任务拆解和阶段调度。
9. `type_n_search` 负责模型、特征和评分计算。
10. `alpha_agent_system` 负责语义入口和 Agent 编排。
11. 保留一键 full workflow 作为 fallback，但新增 decomposed workflow 用于 Agent 编排验证。
