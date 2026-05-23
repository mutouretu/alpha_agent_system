from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


def generate_type_n_summary(candidates_path: str | Path, output_path: str | Path) -> dict[str, Any]:
    resolved_candidates = Path(candidates_path).resolve()
    resolved_output = Path(output_path).resolve()

    if not resolved_candidates.exists():
        return {
            "ok": False,
            "tool": "generate_type_n_summary",
            "error": f"Candidates CSV not found: {resolved_candidates}",
            "candidates_path": str(resolved_candidates),
        }

    try:
        df = pd.read_csv(resolved_candidates)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "tool": "generate_type_n_summary",
            "error": f"Failed to read candidates CSV: {exc}",
            "candidates_path": str(resolved_candidates),
        }

    trade_date = _infer_trade_date(df, resolved_candidates)
    top_df = df.head(20)
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    content = [
        "# Type-N Scan Summary",
        "",
        f"- Date: {trade_date}",
        f"- Candidate count: {len(df)}",
        f"- Candidates file: `{resolved_candidates}`",
        f"- Report file: `{resolved_output}`",
        "",
        "## Top 20 Candidates",
        "",
        _to_markdown_table(top_df),
        "",
    ]
    resolved_output.write_text("\n".join(content), encoding="utf-8")

    return {
        "ok": True,
        "tool": "generate_type_n_summary",
        "trade_date": trade_date,
        "candidate_count": int(len(df)),
        "candidates_path": str(resolved_candidates),
        "output_path": str(resolved_output),
    }


def _infer_trade_date(df: pd.DataFrame, candidates_path: Path) -> str:
    if "trade_date" in df.columns and not df.empty:
        return str(df["trade_date"].iloc[0])
    return candidates_path.parent.name


def _to_markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "No candidates."

    columns = [str(column) for column in df.columns]
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for _, row in df.iterrows():
        values = [_format_cell(row[column]) for column in df.columns]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def _format_cell(value: Any) -> str:
    if pd.isna(value):
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")
