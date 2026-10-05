"""Script to fetch Google Play store reviews for a game.

Requirements (from Section 6.2):
- Uses google-play-scraper (reviews, lang="en", country="us", sort=Sort.NEWEST, count=200 per call)
- Polite: sleep 1-2 s between calls
- Resumable: appends to JSONL, stores continuation token in sidecar file
- Stops at --target or when no token remains
- Keep only: review_id, score, thumbs_up, app_version, timestamp, text/content, dev_replied
- NEVER stores user names or user avatars.
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from google_play_scraper import Sort, reviews
from google_play_scraper.features.reviews import _ContinuationToken

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("fetch_reviews")


def load_token_sidecar(sidecar_path: Path) -> _ContinuationToken | None:
    """Load continuation token from sidecar JSON file if exists."""
    if not sidecar_path.exists():
        return None
    try:
        with open(sidecar_path, encoding="utf-8") as f:
            data = json.load(f)
        if not data.get("token"):
            return None
        return _ContinuationToken(
            token=data["token"],
            lang=data.get("lang", "en"),
            country=data.get("country", "us"),
            sort=Sort(data.get("sort", Sort.NEWEST.value)),
            count=data.get("count", 200),
            filter_score_with=data.get("filter_score_with"),
            filter_device_with=data.get("filter_device_with"),
        )
    except Exception as e:
        logger.warning(f"Could not load sidecar token from {sidecar_path}: {e}")
        return None


def save_token_sidecar(sidecar_path: Path, token: _ContinuationToken | None) -> None:
    """Save continuation token to sidecar JSON file."""
    if token is None:
        if sidecar_path.exists():
            sidecar_path.unlink()
        return
    try:
        data = {
            "token": token.token,
            "lang": token.lang,
            "country": token.country,
            "sort": token.sort.value if hasattr(token.sort, "value") else token.sort,
            "count": token.count,
            "filter_score_with": token.filter_score_with,
            "filter_device_with": token.filter_device_with,
        }
        with open(sidecar_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.warning(f"Could not save sidecar token to {sidecar_path}: {e}")


def extract_review_record(raw: dict[str, Any], game_name: str) -> dict[str, Any]:
    """Extract required fields from raw Google Play review dictionary.

    Explicitly excludes user names and avatars for privacy (Section 6.2).
    """
    ts = raw.get("at")
    if isinstance(ts, datetime):
        ts_str = ts.isoformat()
    elif ts:
        ts_str = str(ts)
    else:
        ts_str = datetime.utcnow().isoformat()

    return {
        "review_id": str(raw.get("reviewId", "")),
        "game": game_name,
        "score": int(raw.get("score", 0)),
        "thumbs_up": int(raw.get("thumbsUpCount", 0)),
        "app_version": raw.get("reviewCreatedVersion") or raw.get("appVersion"),
        "timestamp": ts_str,
        "content": str(raw.get("content", "")),
        "dev_replied": bool(raw.get("replyContent") or raw.get("repliedAt")),
    }


def fetch_reviews(
    app_id: str,
    game_name: str,
    target: int = 8000,
    out_path: Path = Path("data/raw/reviews.jsonl"),
    lang: str = "en",
    country: str = "us",
    count_per_call: int = 200,
    sleep_s: float = 1.5,
) -> int:
    """Fetch reviews with polite rate limiting and resumability."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path = out_path.with_suffix(".token.json")

    existing_ids: set[str] = set()
    total_fetched = 0

    if out_path.exists():
        with open(out_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        record = json.loads(line)
                        if "review_id" in record:
                            existing_ids.add(record["review_id"])
                    except json.JSONDecodeError:
                        continue
        total_fetched = len(existing_ids)
        logger.info(f"Resuming: found {total_fetched} existing reviews in {out_path}")

    continuation = load_token_sidecar(sidecar_path)
    if continuation:
        logger.info("Resuming with saved continuation token")

    batch_num = 0

    with open(out_path, "a", encoding="utf-8") as f:
        while total_fetched < target:
            batch_num += 1
            logger.info(
                f"Fetching batch {batch_num} for '{game_name}' ({app_id})... (current total: {total_fetched}/{target})"
            )

            try:
                raw_reviews, next_token = reviews(
                    app_id,
                    lang=lang,
                    country=country,
                    sort=Sort.NEWEST,
                    count=count_per_call,
                    continuation_token=continuation,
                )
            except Exception as e:
                logger.error(f"Error fetching batch: {e}")
                break

            if not raw_reviews:
                logger.info("No more reviews returned from Google Play.")
                break

            new_in_batch = 0
            for r in raw_reviews:
                record = extract_review_record(r, game_name)
                rid = record["review_id"]
                if rid and rid not in existing_ids:
                    existing_ids.add(rid)
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    f.flush()
                    total_fetched += 1
                    new_in_batch += 1

                if total_fetched >= target:
                    break

            logger.info(
                f"Batch {batch_num}: added {new_in_batch} new reviews (total now: {total_fetched})"
            )

            continuation = next_token
            save_token_sidecar(sidecar_path, continuation)

            if continuation is None or not getattr(continuation, "token", None):
                logger.info("Reached end of reviews stream (no further continuation token).")
                break

            if total_fetched >= target:
                logger.info(f"Target of {target} reviews reached.")
                break

            time.sleep(sleep_s)

    logger.info(f"Finished fetching for {game_name}. Total reviews saved: {total_fetched}")
    return total_fetched


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Fetch Google Play reviews for a game")
    parser.add_argument(
        "--app-id", required=True, help="Google Play package ID (e.g. com.supercell.clashroyale)"
    )
    parser.add_argument(
        "--game", required=True, help="Display game name (e.g. 'Clash Royale')"
    )
    parser.add_argument(
        "--target", type=int, default=8000, help="Target number of reviews (default: 8000)"
    )
    parser.add_argument(
        "--out", required=True, type=Path, help="Output JSONL path (e.g. data/raw/clash_royale.jsonl)"
    )
    parser.add_argument("--lang", default="en", help="Language code (default: en)")
    parser.add_argument("--country", default="us", help="Country code (default: us)")
    parser.add_argument(
        "--count", type=int, default=200, help="Reviews per request (default: 200)"
    )
    parser.add_argument(
        "--sleep", type=float, default=1.5, help="Sleep between calls in seconds (default: 1.5)"
    )

    args = parser.parse_args()

    fetch_reviews(
        app_id=args.app_id,
        game_name=args.game,
        target=args.target,
        out_path=args.out,
        lang=args.lang,
        country=args.country,
        count_per_call=args.count,
        sleep_s=args.sleep,
    )


if __name__ == "__main__":
    main()
