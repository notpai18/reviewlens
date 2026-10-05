"""Script to clean raw reviews and generate reviews.parquet and meta.json.

Requirements (Section 6.3):
1. Drop duplicate review_ids; drop empty or whitespace-only text.
2. Redact emails, phone numbers, and URLs (regex) in the text.
3. Keep rows whose text is mostly ASCII (>= 90% of chars) as an English filter.
4. Normalize empty/blank app_version to None (NULL).
   review_date = UTC date of timestamp.
   content_words = number of whitespace-separated tokens.
   dev_replied = boolean.
5. Add game and review_id as string.
6. Write data/processed/reviews.parquet and data/processed/meta.json.
"""

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("clean_reviews")

# Redaction regular expressions
EMAIL_REGEX = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
URL_REGEX = re.compile(r"https?://\S+|www\.\S+")
PHONE_REGEX = re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")


def redact_pii(text: str) -> str:
    """Redact emails, URLs, and phone numbers from review text."""
    if not text:
        return ""
    text = EMAIL_REGEX.sub("[EMAIL]", text)
    text = URL_REGEX.sub("[URL]", text)
    text = PHONE_REGEX.sub("[PHONE]", text)
    return text


def is_mostly_ascii(text: str, threshold: float = 0.90) -> bool:
    """Check if at least 90% of characters are ASCII."""
    if not text:
        return False
    ascii_count = sum(1 for c in text if ord(c) < 128)
    return (ascii_count / len(text)) >= threshold


def clean_single_record(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Clean and validate a single review record."""
    rid = str(raw.get("review_id", "")).strip()
    if not rid:
        return None

    raw_text = str(raw.get("content") or raw.get("text") or "").strip()
    if not raw_text:
        return None

    if not is_mostly_ascii(raw_text, threshold=0.90):
        return None

    cleaned_text = redact_pii(raw_text).strip()
    if not cleaned_text:
        return None

    game_name = str(raw.get("game", "Unknown")).strip()

    # Parse rating / score
    try:
        score_val = int(raw.get("score") or raw.get("rating", 0))
        if score_val < 1 or score_val > 5:
            return None
    except (ValueError, TypeError):
        return None

    # Thumbs up
    try:
        thumbs_val = int(raw.get("thumbs_up") or raw.get("thumbsUpCount", 0))
    except (ValueError, TypeError):
        thumbs_val = 0

    # App version normalization
    v_raw = raw.get("app_version") or raw.get("reviewCreatedVersion")
    if v_raw is None or str(v_raw).strip().lower() in ("", "none", "null", "nan"):
        app_version = None
    else:
        app_version = str(v_raw).strip()

    # Parse timestamp & review_date (UTC)
    ts_val = raw.get("timestamp") or raw.get("at")
    if not ts_val:
        return None
    try:
        dt = pd.to_datetime(ts_val, utc=True)
        review_date = dt.date()
    except Exception:
        return None

    content_words = len(cleaned_text.split())
    dev_replied = bool(raw.get("dev_replied") or raw.get("replyContent"))

    return {
        "review_id": rid,
        "game": game_name,
        "review_date": review_date,
        "rating": score_val,
        "thumbs_up": thumbs_val,
        "app_version": app_version,
        "content": cleaned_text,
        "content_words": content_words,
        "dev_replied": dev_replied,
    }


def clean_reviews(
    input_paths: list[Path],
    out_parquet: Path = Path("data/processed/reviews.parquet"),
    out_meta: Path = Path("data/processed/meta.json"),
    min_version_count: int = 30,
) -> tuple[int, dict[str, Any]]:
    """Clean all review files and produce parquet and meta.json."""
    out_parquet.parent.mkdir(parents=True, exist_ok=True)
    out_meta.parent.mkdir(parents=True, exist_ok=True)

    seen_ids: set[str] = set()
    cleaned_rows: list[dict[str, Any]] = []

    for in_path in input_paths:
        logger.info(f"Processing {in_path}...")
        with open(in_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                cleaned = clean_single_record(record)
                if cleaned and cleaned["review_id"] not in seen_ids:
                    seen_ids.add(cleaned["review_id"])
                    cleaned_rows.append(cleaned)

    if not cleaned_rows:
        raise ValueError("No valid records found after cleaning!")

    logger.info(f"Total valid unique cleaned reviews: {len(cleaned_rows)}")

    df = pd.DataFrame(cleaned_rows)
    # Ensure types
    df["review_id"] = df["review_id"].astype(str)
    df["game"] = df["game"].astype(str)
    df["review_date"] = pd.to_datetime(df["review_date"]).dt.date
    df["rating"] = df["rating"].astype(int)
    df["thumbs_up"] = df["thumbs_up"].astype(int)
    df["app_version"] = df["app_version"].astype(object)
    df["content"] = df["content"].astype(str)
    df["content_words"] = df["content_words"].astype(int)
    df["dev_replied"] = df["dev_replied"].astype(bool)

    # Save to Parquet using PyArrow
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, out_parquet)
    logger.info(f"Wrote {len(df)} rows to Parquet: {out_parquet}")

    # Build meta.json
    games_meta = []
    overall_date_max = df["review_date"].max().strftime("%Y-%m-%d")

    for game_name, gdf in df.groupby("game"):
        g_n = len(gdf)
        g_min = gdf["review_date"].min().strftime("%Y-%m-%d")
        g_max = gdf["review_date"].max().strftime("%Y-%m-%d")

        # Top 12 versions with n >= min_version_count
        v_nonnull = gdf[gdf["app_version"].notna()]
        v_counts = v_nonnull.groupby("app_version").size()
        v_qualifying = v_counts[v_counts >= min_version_count].sort_values(ascending=False).head(12)

        versions_list = []
        for v_name, v_n in v_qualifying.items():
            v_rows = v_nonnull[v_nonnull["app_version"] == v_name]
            first_seen = v_rows["review_date"].min().strftime("%Y-%m-%d")
            last_seen = v_rows["review_date"].max().strftime("%Y-%m-%d")
            versions_list.append(
                {
                    "version": str(v_name),
                    "first_seen": first_seen,
                    "last_seen": last_seen,
                    "n": int(v_n),
                }
            )

        games_meta.append(
            {
                "game": str(game_name),
                "n": int(g_n),
                "date_min": g_min,
                "date_max": g_max,
                "versions": versions_list,
            }
        )

    meta_dict = {
        "games": games_meta,
        "data_end_date": overall_date_max,
    }

    with open(out_meta, "w", encoding="utf-8") as f:
        json.dump(meta_dict, f, indent=2)
    logger.info(f"Wrote metadata to {out_meta}")

    return len(cleaned_rows), meta_dict


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Clean raw review JSONL files")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/raw"),
        help="Input JSONL file or raw directory (default: data/raw)",
    )
    parser.add_argument(
        "--out-parquet",
        type=Path,
        default=Path("data/processed/reviews.parquet"),
        help="Output Parquet path",
    )
    parser.add_argument(
        "--out-meta",
        type=Path,
        default=Path("data/processed/meta.json"),
        help="Output meta.json path",
    )
    parser.add_argument(
        "--fixture",
        action="store_true",
        help="Use test fixture reviews_fixture.jsonl and relax min version threshold",
    )

    args = parser.parse_args()

    if args.fixture:
        input_paths = [Path("tests/fixtures/reviews_fixture.jsonl")]
        min_version = 2  # Low threshold for 60-row test fixture
    elif args.input.is_file():
        input_paths = [args.input]
        min_version = 30
    else:
        input_paths = list(args.input.glob("*.jsonl"))
        min_version = 30

    if not input_paths:
        logger.error(f"No JSONL files found at {args.input}")
        sys.exit(1)

    clean_reviews(
        input_paths=input_paths,
        out_parquet=args.out_parquet,
        out_meta=args.out_meta,
        min_version_count=min_version,
    )


if __name__ == "__main__":
    main()
