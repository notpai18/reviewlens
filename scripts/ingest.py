"""Index processed reviews into Qdrant.

Usage:
    python scripts/ingest.py --rebuild     # drop + recreate the collection, then index
    python scripts/ingest.py               # create if missing, upsert (idempotent)

Prints the number of indexed points; running it twice prints the same number.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from reviewlens.agent.factory import build_qdrant  # noqa: E402
from reviewlens.config import get_settings  # noqa: E402
from reviewlens.search.ingest import rebuild_index  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ingest")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rebuild", action="store_true", help="drop and recreate the collection")
    parser.add_argument("--parquet", type=Path, default=None, help="override reviews.parquet path")
    args = parser.parse_args()

    settings = get_settings()
    parquet = args.parquet or (settings.data_meta_path.parent / "reviews.parquet")
    if not parquet.exists():
        logger.error("Parquet not found: %s (run `make data` first)", parquet)
        return 1

    client = build_qdrant(settings)
    logger.info("Indexing %s into %s (%s)", parquet, settings.qdrant_collection, settings.qdrant_url)

    def progress(done: int, total: int) -> None:
        logger.info("  indexed %d/%d", done, total)

    count = rebuild_index(
        client,
        settings.qdrant_collection,
        parquet,
        recreate=args.rebuild,
        on_progress=progress,
    )
    print(f"Indexed points in '{settings.qdrant_collection}': {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
