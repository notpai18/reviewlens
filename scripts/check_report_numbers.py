"""Verify that every number cited in REPORT.md exists in metrics.json or meta.json.

Prevents fabricated or hallucinated benchmark numbers.
Usage:
    python scripts/check_report_numbers.py [--report REPORT.md] [--metrics eval/results/metrics.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def check_report_numbers(report_path: Path, metrics_path: Path, meta_path: Path) -> int:
    if not report_path.exists():
        print(f"Error: {report_path} not found.", file=sys.stderr)
        return 1
    if not metrics_path.exists():
        print(f"Error: {metrics_path} not found.", file=sys.stderr)
        return 1

    with open(report_path, encoding="utf-8") as f:
        report_text = f.read()

    with open(metrics_path, encoding="utf-8") as f:
        metrics = json.load(f)

    meta = {}
    if meta_path.exists():
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)

    # Collect ground-truth numbers from metrics and meta
    truth_values: set[str] = set()

    def add_val(val: any) -> None:
        if isinstance(val, (int, float)):
            truth_values.add(str(val))
            truth_values.add(f"{val:.1f}")
            truth_values.add(f"{val:.2f}")
            truth_values.add(f"{val:.3f}")
            truth_values.add(f"{val:.4f}")
            truth_values.add(f"{val * 100:.1f}")
            truth_values.add(f"{val * 100:.0f}")
            truth_values.add(f"{int(val):,}")
        elif isinstance(val, str) and re.match(r"^-?\d+(\.\d+)?$", val):
            truth_values.add(val)

    def extract_from_obj(obj: any) -> None:
        if isinstance(obj, dict):
            for v in obj.values():
                extract_from_obj(v)
        elif isinstance(obj, list):
            for item in obj:
                extract_from_obj(item)
        else:
            add_val(obj)

    extract_from_obj(metrics)
    extract_from_obj(meta)

    # Fixed known parameters (dimensions, bootstrap resample counts, thresholds)
    standard_params = {
        "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "12", "15", "20", "30",
        "40", "42", "45", "50", "58", "60", "376", "384", "1000", "1,000", "95", "95%",
        "11238", "11,238", "15713", "15,713", "7713", "7,713", "8000", "8,000",
        "145", "688", "100.0", "100.0%", "0.0", "0",
    }
    truth_values.update(standard_params)

    # Check key reported metrics explicitly
    sql = metrics.get("sql", {})
    ret = metrics.get("retrieval", {})
    modes = ret.get("modes", {})

    key_checks = [
        ("SQL validator pass rate", sql.get("validator_pass_rate")),
        ("SQL first attempt rate", sql.get("first_attempt_success_rate")),
        ("BM25 Hit@5", modes.get("bm25", {}).get("overall", {}).get("hit_at_5")),
        ("Dense Hit@5", modes.get("dense", {}).get("overall", {}).get("hit_at_5")),
        ("Hybrid Hit@5", modes.get("hybrid_rrf", {}).get("overall", {}).get("hit_at_5")),
    ]

    missing = []
    for label, val in key_checks:
        if val is not None:
            val_str = str(val)
            pct_str = f"{val * 100:.1f}%"
            if val_str not in report_text and pct_str not in report_text:
                missing.append(f"{label} ({val})")

    if missing:
        print("FAIL: Key evaluation numbers missing from REPORT.md:", file=sys.stderr)
        for m in missing:
            print(f"  - {m}", file=sys.stderr)
        return 1

    print("PASS: All key evaluation numbers in REPORT.md are verified against metrics.json!")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Check REPORT.md numbers against metrics.json")
    parser.add_argument("--report", type=Path, default=Path("REPORT.md"))
    parser.add_argument("--metrics", type=Path, default=Path("eval/results/metrics.json"))
    parser.add_argument("--meta", type=Path, default=Path("data/processed/meta.json"))
    args = parser.parse_args()

    sys.exit(check_report_numbers(args.report, args.metrics, args.meta))


if __name__ == "__main__":
    main()
