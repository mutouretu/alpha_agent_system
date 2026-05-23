from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any


def run_type_n_search(project_root: str | Path, trade_date: str, output_path: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    scan_script = root / "scripts" / "run_scan.py"
    resolved_output = Path(output_path).resolve()

    if not scan_script.exists():
        return {
            "ok": False,
            "tool": "run_type_n_search",
            "error": "type_n_search does not expose scripts/run_scan.py yet; please adapt command in type_n_tool.py",
            "project_root": str(root),
            "expected_script": str(scan_script),
        }

    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(scan_script),
        "--date",
        trade_date,
        "--output",
        str(resolved_output),
    ]
    completed = subprocess.run(
        cmd,
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )

    return {
        "ok": completed.returncode == 0,
        "tool": "run_type_n_search",
        "command": cmd,
        "cwd": str(root),
        "returncode": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
        "output_path": str(resolved_output),
    }
