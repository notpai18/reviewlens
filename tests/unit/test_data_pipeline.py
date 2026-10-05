"""Unit tests for Phase 1 data pipeline scripts.

Tests:
1. PII redaction (email, phone, URL regex).
2. ASCII / language filtering.
3. Record normalization and validation.
4. Parquet and metadata generation.
5. DuckDB warehouse creation and data quality checks.
6. CSV importer fallback.
7. Dataset profiler.
"""

from pathlib import Path

import duckdb
import pandas as pd

from scripts.build_warehouse import build_warehouse
from scripts.clean_reviews import clean_reviews, clean_single_record, is_mostly_ascii, redact_pii
from scripts.import_csv import import_csv_to_jsonl
from scripts.profile_reviews import profile_game_data


class TestPIIRedaction:
    """Test PII redaction regular expressions."""

    def test_redact_email(self) -> None:
        text = "Contact me at user.name@domain.co.uk for help"
        redacted = redact_pii(text)
        assert "[EMAIL]" in redacted
        assert "user.name@domain.co.uk" not in redacted

    def test_redact_url(self) -> None:
        text = "Check this site https://supercell.com/clash or www.google.com/search"
        redacted = redact_pii(text)
        assert "[URL]" in redacted
        assert "https://" not in redacted
        assert "www." not in redacted

    def test_redact_phone(self) -> None:
        text = "Call support at +1-800-555-1234 or (555) 234-5678"
        redacted = redact_pii(text)
        assert "[PHONE]" in redacted
        assert "800-555-1234" not in redacted


class TestASCIIFiltering:
    """Test ASCII threshold checking."""

    def test_pure_ascii(self) -> None:
        assert is_mostly_ascii("Great game, love the new card balance update!")

    def test_ascii_with_minor_emojis(self) -> None:
        # Long text with 1-2 emojis should be >= 90% ASCII
        text = "This game is absolutely amazing and fun to play with friends! 🎮🔥"
        assert is_mostly_ascii(text, threshold=0.90)

    def test_non_ascii_rejected(self) -> None:
        # Non-ASCII text (e.g. Cyrillic, Chinese, Arabic)
        text = "Отличная игра, очень нравится графика и геймплей!"
        assert not is_mostly_ascii(text, threshold=0.90)

        chinese_text = "这款游戏太好玩了，强烈推荐给大家！"
        assert not is_mostly_ascii(chinese_text, threshold=0.90)


class TestRecordCleaning:
    """Test single record cleaning and validation."""

    def test_valid_record(self) -> None:
        raw = {
            "review_id": "rev_001",
            "game": "Clash Royale",
            "score": 5,
            "thumbs_up": 10,
            "app_version": "v1.2.3",
            "timestamp": "2024-03-15T12:00:00Z",
            "content": "Awesome update, fixed all lag issues!",
            "dev_replied": True,
        }
        cleaned = clean_single_record(raw)
        assert cleaned is not None
        assert cleaned["review_id"] == "rev_001"
        assert cleaned["game"] == "Clash Royale"
        assert cleaned["rating"] == 5
        assert cleaned["thumbs_up"] == 10
        assert cleaned["app_version"] == "v1.2.3"
        assert cleaned["content_words"] == 6
        assert cleaned["dev_replied"] is True

    def test_normalize_empty_version(self) -> None:
        raw = {
            "review_id": "rev_002",
            "game": "Clash Royale",
            "score": 4,
            "app_version": "None",
            "timestamp": "2024-03-15T12:00:00Z",
            "content": "Pretty good game overall.",
        }
        cleaned = clean_single_record(raw)
        assert cleaned is not None
        assert cleaned["app_version"] is None

    def test_reject_invalid_rating(self) -> None:
        raw = {
            "review_id": "rev_003",
            "score": 6,
            "timestamp": "2024-03-15T12:00:00Z",
            "content": "Invalid rating test.",
        }
        assert clean_single_record(raw) is None

    def test_reject_empty_content(self) -> None:
        raw = {
            "review_id": "rev_004",
            "score": 3,
            "timestamp": "2024-03-15T12:00:00Z",
            "content": "   ",
        }
        assert clean_single_record(raw) is None


class TestPipelineEndToEnd:
    """Test full pipeline using test fixture."""

    def test_clean_reviews_and_warehouse_build(self, tmp_path: Path) -> None:
        fixture_path = Path("tests/fixtures/reviews_fixture.jsonl")
        parquet_path = tmp_path / "reviews.parquet"
        meta_path = tmp_path / "meta.json"
        db_path = tmp_path / "test.duckdb"

        # 1. Clean reviews
        count, meta = clean_reviews(
            input_paths=[fixture_path],
            out_parquet=parquet_path,
            out_meta=meta_path,
            min_version_count=2,
        )
        assert count == 60
        assert parquet_path.exists()
        assert meta_path.exists()

        # Check meta.json structure
        assert "games" in meta
        assert len(meta["games"]) == 2
        assert "data_end_date" in meta

        # 2. Build warehouse
        success = build_warehouse(
            parquet_path=parquet_path,
            db_path=db_path,
            is_fixture=True,
        )
        assert success is True
        assert db_path.exists()

        # Check duckdb queries
        con = duckdb.connect(str(db_path), read_only=True)
        row_count = con.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
        assert row_count == 60

        games = con.execute("SELECT DISTINCT game FROM reviews ORDER BY game").fetchall()
        assert len(games) == 2
        assert [g[0] for g in games] == ["Brawl Stars", "Clash Royale"]
        con.close()


class TestImportCSV:
    """Test fallback CSV importer."""

    def test_import_csv(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "reviews.csv"
        out_jsonl = tmp_path / "imported.jsonl"

        df = pd.DataFrame(
            [
                {
                    "reviewId": "csv_1",
                    "score": 5,
                    "thumbsUpCount": 3,
                    "reviewCreatedVersion": "2.0.1",
                    "at": "2024-04-01 10:00:00",
                    "content": "Loved the new champion update!",
                    "replyContent": "Thanks for playing!",
                },
                {
                    "reviewId": "csv_2",
                    "score": 1,
                    "thumbsUpCount": 0,
                    "reviewCreatedVersion": None,
                    "at": "2024-04-02 11:00:00",
                    "content": "Crashing on startup constantly.",
                    "replyContent": None,
                },
            ]
        )
        df.to_csv(csv_file, index=False)

        imported_count = import_csv_to_jsonl(
            csv_path=csv_file,
            game_name="Clash Royale",
            out_path=out_jsonl,
        )
        assert imported_count == 2
        assert out_jsonl.exists()


class TestProfiler:
    """Test dataset profiling logic."""

    def test_profile_game_data(self) -> None:
        records = [
            {
                "review_id": f"id_{i}",
                "game": "Clash Royale",
                "score": (i % 5) + 1,
                "timestamp": f"2024-01-{(i % 28) + 1:02d}T10:00:00Z",
                "content": "Great match balance and competitive mechanics!",
                "app_version": "5.1.0",
            }
            for i in range(30)
        ]
        res = profile_game_data(records, "Clash Royale", min_count=20)
        assert res["count"] == 30
        assert res["count_pass"] is True
        assert res["version_pct"] == 100.0
        assert 5 in res["rating_dist"]
