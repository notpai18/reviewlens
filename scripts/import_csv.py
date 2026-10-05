"""Fallback script to import Google Play reviews from a CSV dataset.

Converts CSV columns to the standardized JSONL raw format without storing user names or avatars.
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("import_csv")


def import_csv_to_jsonl(
    csv_path: Path,
    game_name: str,
    out_path: Path,
    id_col: str = "reviewId",
    score_col: str = "score",
    thumbs_col: str = "thumbsUpCount",
    version_col: str = "reviewCreatedVersion",
    date_col: str = "at",
    content_col: str = "content",
    reply_col: str = "replyContent",
) -> int:
    """Import CSV file into standard JSONL format."""
    out_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info(f"Reading CSV from {csv_path}...")
    df = pd.read_csv(csv_path)

    logger.info(f"Loaded {len(df)} rows from CSV. Processing columns...")

    # Column fallback resolution
    cols = {c.lower(): c for c in df.columns}

    def find_col(preferred: str, fallbacks: list[str]) -> str | None:
        if preferred in df.columns:
            return preferred
        if preferred.lower() in cols:
            return cols[preferred.lower()]
        for fb in fallbacks:
            if fb in df.columns:
                return fb
            if fb.lower() in cols:
                return cols[fb.lower()]
        return None

    actual_id = find_col(id_col, ["review_id", "id", "reviewid"])
    actual_score = find_col(score_col, ["rating", "stars", "score"])
    actual_thumbs = find_col(thumbs_col, ["thumbs_up", "thumbsup", "likes", "thumbsupcount"])
    actual_version = find_col(
        version_col, ["app_version", "version", "reviewcreatedversion", "appversion"]
    )
    actual_date = find_col(date_col, ["timestamp", "date", "created_at", "at", "time"])
    actual_content = find_col(content_col, ["text", "review", "content", "review_text"])
    actual_reply = find_col(reply_col, ["reply", "developer_reply", "replycontent", "dev_replied"])

    if not actual_content or not actual_score:
        raise ValueError(
            f"CSV must contain review content and score/rating columns. Found: {list(df.columns)}"
        )

    written = 0
    seen_ids: set[str] = set()

    with open(out_path, "w", encoding="utf-8") as f:
        for idx, row in df.iterrows():
            rid = (
                str(row[actual_id])
                if actual_id and pd.notna(row[actual_id])
                else f"import_{idx}"
            )
            if rid in seen_ids:
                continue
            seen_ids.add(rid)

            content_text = str(row[actual_content]) if pd.notna(row[actual_content]) else ""
            if not content_text.strip():
                continue

            try:
                score_val = int(row[actual_score])
            except (ValueError, TypeError):
                continue

            thumbs_val = 0
            if actual_thumbs and pd.notna(row[actual_thumbs]):
                try:
                    thumbs_val = int(row[actual_thumbs])
                except (ValueError, TypeError):
                    thumbs_val = 0

            version_val = (
                str(row[actual_version])
                if actual_version and pd.notna(row[actual_version])
                else None
            )

            date_val = str(row[actual_date]) if actual_date and pd.notna(row[actual_date]) else ""
            try:
                dt = pd.to_datetime(date_val, utc=True)
                date_str = dt.isoformat()
            except Exception:
                date_str = str(date_val)

            dev_replied_val = False
            if actual_reply and pd.notna(row[actual_reply]):
                dev_replied_val = bool(row[actual_reply])

            record: dict[str, Any] = {
                "review_id": rid,
                "game": game_name,
                "score": score_val,
                "thumbs_up": thumbs_val,
                "app_version": version_val,
                "timestamp": date_str,
                "content": content_text,
                "dev_replied": dev_replied_val,
            }

            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1

    logger.info(f"Successfully imported {written} reviews into {out_path}")
    return written


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Import Google Play reviews from CSV to JSONL")
    parser.add_argument("--csv", required=True, type=Path, help="Input CSV file path")
    parser.add_argument("--game", required=True, help="Display game name")
    parser.add_argument("--out", required=True, type=Path, help="Output JSONL path")

    args = parser.parse_args()
    import_csv_to_jsonl(csv_path=args.csv, game_name=args.game, out_path=args.out)


if __name__ == "__main__":
    main()
