from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


DEFAULT_REQUIRED_COLUMNS = ["trade_date", "ts_code", "name", "model_score"]


def validate_csv(path: str | Path, required_columns: list[str] | None = None) -> dict[str, Any]:
    resolved_path = Path(path).resolve()
    required = required_columns or DEFAULT_REQUIRED_COLUMNS

    if not resolved_path.exists():
        return {
            "ok": False,
            "tool": "validate_csv",
            "error": f"CSV file not found: {resolved_path}",
            "path": str(resolved_path),
            "required_columns": required,
        }

    try:
        df = pd.read_csv(resolved_path)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "tool": "validate_csv",
            "error": f"Failed to read CSV: {exc}",
            "path": str(resolved_path),
            "required_columns": required,
        }

    missing = [column for column in required if column not in df.columns]
    return {
        "ok": not missing and not df.empty,
        "tool": "validate_csv",
        "path": str(resolved_path),
        "row_count": int(len(df)),
        "columns": list(df.columns),
        "required_columns": required,
        "missing_columns": missing,
        "is_empty": bool(df.empty),
    }


def read_csv_head(path: str | Path, n: int = 20) -> dict[str, Any]:
    resolved_path = Path(path).resolve()
    if not resolved_path.exists():
        return {
            "ok": False,
            "tool": "read_csv_head",
            "error": f"CSV file not found: {resolved_path}",
            "path": str(resolved_path),
        }

    try:
        df = pd.read_csv(resolved_path)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "tool": "read_csv_head",
            "error": f"Failed to read CSV: {exc}",
            "path": str(resolved_path),
        }

    safe_n = max(1, min(int(n), 100))
    return {
        "ok": True,
        "tool": "read_csv_head",
        "path": str(resolved_path),
        "row_count": int(len(df)),
        "columns": list(df.columns),
        "rows": df.head(safe_n).where(pd.notna(df.head(safe_n)), None).to_dict(orient="records"),
    }
