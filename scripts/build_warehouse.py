"""Script to build DuckDB warehouse table and run data quality checks.

Requirements (Sections 6.4 & 6.5):
1. Single table `reviews`:
   - review_id VARCHAR PRIMARY KEY
   - game VARCHAR
   - review_date DATE
   - rating INTEGER (1-5)
   - thumbs_up INTEGER
   - app_version VARCHAR (nullable)
   - content VARCHAR
   - content_words INTEGER
   - dev_replied BOOLEAN
2. Data Quality Checks (exits non-zero if any check fails):
   - review_id unique
   - rating in 1..5
   - no null review_date, content, game
   - exactly 2 games
   - each game passes Section 6.1 thresholds (unless --fixture)
   - app_version non-null share printed (warn if < 20%)
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

try:
    from scripts.clean_reviews import clean_reviews
except ImportError:
    from clean_reviews import clean_reviews  # type: ignore[no-redef]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("build_warehouse")


def build_warehouse(
    parquet_path: Path = Path("data/processed/reviews.parquet"),
    db_path: Path = Path("data/warehouse/reviewlens.duckdb"),
    is_fixture: bool = False,
) -> bool:
    """Build DuckDB warehouse from parquet file and execute quality checks."""
    if not parquet_path.exists():
        logger.error(f"Parquet file does not exist: {parquet_path}")
        return False

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    logger.info(f"Connecting to DuckDB at {db_path}...")
    con = duckdb.connect(str(db_path))

    # Create reviews table from parquet
    con.execute(f"""
        CREATE TABLE reviews AS
        SELECT
            review_id,
            game,
            review_date,
            rating,
            thumbs_up,
            app_version,
            content,
            content_words,
            dev_replied
        FROM read_parquet('{parquet_path}');
    """)

    logger.info("Created table 'reviews'. Running data quality checks...")

    # Quality Check 1: review_id unique
    dup_res = con.execute("""
        SELECT COUNT(review_id) - COUNT(DISTINCT review_id) AS dup_count
        FROM reviews;
    """).fetchone()
    dup_count = dup_res[0] if dup_res else 0
    if dup_count > 0:
        logger.error(f"FAIL: Found {dup_count} duplicate review_ids in reviews table!")
        con.close()
        return False
    logger.info("PASS: All review_ids are unique.")

    # Quality Check 2: rating in 1..5
    invalid_rating = con.execute("""
        SELECT COUNT(*) FROM reviews WHERE rating < 1 OR rating > 5 OR rating IS NULL;
    """).fetchone()
    invalid_rating_count = invalid_rating[0] if invalid_rating else 0
    if invalid_rating_count > 0:
        logger.error(f"FAIL: Found {invalid_rating_count} rows with invalid rating (not in 1..5)!")
        con.close()
        return False
    logger.info("PASS: All ratings are valid integers in 1..5.")

    # Quality Check 3: no null review_date, content, game
    null_res = con.execute("""
        SELECT
            SUM(CASE WHEN review_date IS NULL THEN 1 ELSE 0 END) AS null_dates,
            SUM(CASE WHEN content IS NULL OR TRIM(content) = '' THEN 1 ELSE 0 END) AS null_content,
            SUM(CASE WHEN game IS NULL OR TRIM(game) = '' THEN 1 ELSE 0 END) AS null_game
        FROM reviews;
    """).fetchone()
    if null_res and any(c > 0 for c in null_res):
        logger.error(
            f"FAIL: Found nulls in mandatory fields: dates={null_res[0]}, content={null_res[1]}, game={null_res[2]}"
        )
        con.close()
        return False
    logger.info("PASS: No null review_date, content, or game fields.")

    # Quality Check 4: exactly 2 games
    games_res = con.execute("""
        SELECT game, COUNT(*) AS count,
               MIN(review_date) AS min_d, MAX(review_date) AS max_d,
               (MAX(review_date) - MIN(review_date)) AS span_days,
               ROUND(100.0 * COUNT(app_version) / COUNT(*), 1) AS ver_pct
        FROM reviews
        GROUP BY game
        ORDER BY game;
    """).fetchall()

    if len(games_res) != 2:
        logger.error(
            f"FAIL: Expected exactly 2 games in warehouse, found {len(games_res)}: {[g[0] for g in games_res]}"
        )
        con.close()
        return False
    logger.info(f"PASS: Exactly 2 games present: {[g[0] for g in games_res]}.")

    # Quality Check 5 & 6: Volume, date span, median weekly reviews, version share
    all_games_pass = True
    print("\n" + "=" * 80)
    print("WAREHOUSE DATA QUALITY SUMMARY")
    print("=" * 80)

    for g_name, count, min_d, max_d, span_days, ver_pct in games_res:
        # Calculate weekly median for game
        gdf = con.execute(
            "SELECT review_date FROM reviews WHERE game = ?", [g_name]
        ).df()
        gdf["dt"] = pd.to_datetime(gdf["review_date"])
        gdf["week"] = gdf["dt"].dt.to_period("W")
        med_weekly = float(gdf.groupby("week").size().median())

        min_req_count = 25 if is_fixture else 6000
        min_req_span = 120

        count_ok = count >= min_req_count
        span_ok = span_days >= min_req_span
        weekly_ok = med_weekly >= 50.0 or is_fixture

        game_ok = count_ok and span_ok and weekly_ok
        if not game_ok and not is_fixture:
            all_games_pass = False

        status = "PASS" if game_ok or is_fixture else "FAIL"
        warn_ver = " [WARN: <20% version coverage]" if ver_pct < 20.0 else ""

        print(
            f"Game: {g_name:<18} | Count: {count:<6} | Dates: {min_d} to {max_d} ({span_days}d) | "
            f"Med/Wk: {med_weekly:<5.1f} | Ver%: {ver_pct}%{warn_ver} | {status}"
        )

    print("=" * 80 + "\n")

    con.close()

    if not all_games_pass and not is_fixture:
        logger.error("FAIL: One or more games failed Section 6.1 thresholds!")
        return False

    logger.info("PASS: Warehouse build and all data quality checks succeeded!")
    return True


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Build DuckDB reviews warehouse table")
    parser.add_argument(
        "--parquet",
        type=Path,
        default=Path("data/processed/reviews.parquet"),
        help="Input Parquet path",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/warehouse/reviewlens.duckdb"),
        help="Output DuckDB path",
    )
    parser.add_argument(
        "--fixture",
        action="store_true",
        help="Build warehouse directly from tests/fixtures/reviews_fixture.jsonl (for CI/testing)",
    )

    args = parser.parse_args()

    if args.fixture:
        logger.info("Running in --fixture mode: cleaning fixture data first...")
        fixture_path = Path("tests/fixtures/reviews_fixture.jsonl")
        clean_reviews(
            input_paths=[fixture_path],
            out_parquet=args.parquet,
            out_meta=Path("data/processed/meta.json"),
            min_version_count=2,
        )

    success = build_warehouse(
        parquet_path=args.parquet,
        db_path=args.db,
        is_fixture=args.fixture,
    )

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
