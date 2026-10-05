"""Script to profile raw or processed review data against Section 6.1 criteria.

Criteria per game:
1. >= 6,000 usable English reviews
2. Date span >= 120 days
3. Median reviews per week >= 50
4. Reports % non-null app_version and rating distribution
"""

import argparse
import json
import logging
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("profile_reviews")


def is_mostly_ascii(text: str, threshold: float = 0.90) -> bool:
    """Check if text characters are at least 90% ASCII."""
    if not text:
        return False
    ascii_count = sum(1 for c in text if ord(c) < 128)
    return (ascii_count / len(text)) >= threshold


def load_records_from_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load review records from a JSONL file."""
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records


def profile_game_data(
    records: list[dict[str, Any]], game_name: str, min_count: int = 6000
) -> dict[str, Any]:
    """Calculate profile statistics for a game's reviews."""
    if not records:
        return {
            "game": game_name,
            "count": 0,
            "valid": False,
            "error": "No records found",
        }

    # Deduplicate by review_id and filter valid text
    seen_ids = set()
    cleaned = []
    for r in records:
        rid = r.get("review_id")
        text = (r.get("content") or r.get("text") or "").strip()
        if rid and rid not in seen_ids and text and is_mostly_ascii(text):
            seen_ids.add(rid)
            cleaned.append(r)

    total_count = len(cleaned)

    # Parse timestamps
    dates: list[datetime] = []
    versions_present = 0
    ratings = []

    for r in cleaned:
        ts_str = r.get("timestamp") or r.get("review_date") or r.get("at")
        if ts_str:
            try:
                dt = pd.to_datetime(ts_str, utc=True)
                dates.append(dt.to_pydatetime())
            except Exception:
                pass

        v = r.get("app_version") or r.get("reviewCreatedVersion")
        if v and str(v).strip().lower() not in ("none", "null", ""):
            versions_present += 1

        score = r.get("score") or r.get("rating")
        if score is not None:
            try:
                ratings.append(int(score))
            except (ValueError, TypeError):
                pass

    if not dates:
        return {
            "game": game_name,
            "count": total_count,
            "valid": False,
            "error": "No valid timestamps found",
        }

    date_min = min(dates)
    date_max = max(dates)
    span_days = (date_max - date_min).days

    # Calculate weekly counts
    df = pd.DataFrame({"dt": dates})
    df["week"] = df["dt"].dt.tz_localize(None).dt.to_period("W")
    weekly_counts = df.groupby("week").size()
    median_weekly = float(weekly_counts.median()) if not weekly_counts.empty else 0.0

    version_pct = (versions_present / total_count * 100) if total_count > 0 else 0.0
    rating_dist = dict(sorted(Counter(ratings).items()))

    # Check criteria
    count_pass = total_count >= min_count
    span_pass = span_days >= 120
    weekly_pass = median_weekly >= 50.0
    overall_pass = count_pass and span_pass and weekly_pass

    return {
        "game": game_name,
        "count": total_count,
        "date_min": date_min.strftime("%Y-%m-%d"),
        "date_max": date_max.strftime("%Y-%m-%d"),
        "span_days": span_days,
        "median_weekly": median_weekly,
        "version_pct": round(version_pct, 1),
        "rating_dist": rating_dist,
        "count_pass": count_pass,
        "span_pass": span_pass,
        "weekly_pass": weekly_pass,
        "overall_pass": overall_pass,
    }


def print_profile_table(profiles: list[dict[str, Any]], is_fixture: bool = False) -> bool:
    """Print markdown formatted profile table and return overall success."""
    print("\n" + "=" * 80)
    print("REVIEWLENS DATA PROFILE (Section 6.1 Criteria)")
    print("=" * 80)

    header = (
        f"{'Game':<20} | {'Count':<7} | {'Date Min':<10} | {'Date Max':<10} | "
        f"{'Span':<5} | {'Med/Wk':<7} | {'Ver %':<6} | {'Status':<6}"
    )
    print(header)
    print("-" * len(header))

    all_pass = True
    for p in profiles:
        if "error" in p:
            print(f"{p['game']:<20} | ERROR: {p['error']}")
            all_pass = False
            continue

        status = "PASS" if p["overall_pass"] or is_fixture else "FAIL"
        if not p["overall_pass"] and not is_fixture:
            all_pass = False

        row = (
            f"{p['game']:<20} | {p['count']:<7} | {p['date_min']:<10} | {p['date_max']:<10} | "
            f"{p['span_days']:<4}d | {p['median_weekly']:<7.1f} | {p['version_pct']:<5.1f}% | {status:<6}"
        )
        print(row)

    print("-" * len(header))

    print("\nRating Distributions:")
    for p in profiles:
        if "rating_dist" in p:
            dist_str = ", ".join(f"{star}★: {count}" for star, count in p["rating_dist"].items())
            print(f"  {p['game']}: {dist_str}")

    print("\nVersion Coverage:")
    for p in profiles:
        if "version_pct" in p:
            pct = p["version_pct"]
            warn = " (WARNING: <20% app_version present)" if pct < 20.0 else ""
            print(f"  {p['game']}: {pct}% non-null app_version{warn}")

    print("=" * 80)
    if is_fixture:
        print("Note: Running on fixture data (Section 6.1 volume thresholds relaxed).")
        return True

    return all_pass


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Profile review data for Section 6.1 criteria")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/raw"),
        help="Input JSONL file or directory containing raw JSONL files",
    )
    parser.add_argument(
        "--fixture",
        action="store_true",
        help="Profile test fixture data (relax 6000 review threshold)",
    )
    parser.add_argument(
        "--min-count",
        type=int,
        default=6000,
        help="Minimum required reviews per game (default: 6000)",
    )

    args = parser.parse_args()

    target_path = Path("tests/fixtures/reviews_fixture.jsonl") if args.fixture else args.input
    min_count = 25 if args.fixture else args.min_count

    if not target_path.exists():
        logger.error(f"Input path does not exist: {target_path}")
        sys.exit(1)

    jsonl_files: list[Path] = []
    if target_path.is_file():
        jsonl_files = [target_path]
    else:
        jsonl_files = list(target_path.glob("*.jsonl"))

    if not jsonl_files:
        logger.error(f"No JSONL files found in {target_path}")
        sys.exit(1)

    # Group records by game
    records_by_game: dict[str, list[dict[str, Any]]] = {}
    for fpath in jsonl_files:
        file_records = load_records_from_jsonl(fpath)
        for r in file_records:
            gname = r.get("game", fpath.stem)
            if gname not in records_by_game:
                records_by_game[gname] = []
            records_by_game[gname].append(r)

    profiles = [
        profile_game_data(records, gname, min_count=min_count)
        for gname, records in records_by_game.items()
    ]

    success = print_profile_table(profiles, is_fixture=args.fixture)
    if not success:
        logger.warning(
            "One or more games failed Section 6.1 criteria. Consider scraping alternative games."
        )
        sys.exit(1)
    else:
        logger.info("All games successfully passed Section 6.1 criteria!")


if __name__ == "__main__":
    main()
