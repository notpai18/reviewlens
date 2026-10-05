"""Generate 60 evaluation queries for the retrieval benchmark (Section 11.2).

Creates data/eval/retrieval_queries.yaml with 3 buckets of queries:
  - 20 Lexical queries (no LLM, 2-3 rare tokens + game name)
  - 20 Semantic queries (describe the review without using its distinctive words)
  - 20 Mixed queries (natural search queries)

Usage:
    python scripts/make_retrieval_queries.py
    python scripts/make_retrieval_queries.py --fixture   # for testing on fixture data
"""

from __future__ import annotations

import argparse
import random
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from reviewlens.config import get_settings

STOP_WORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "with",
    "by", "about", "against", "between", "into", "through", "during", "before",
    "after", "above", "below", "from", "up", "down", "is", "are", "was", "were",
    "be", "been", "being", "have", "has", "had", "do", "does", "did", "game",
    "play", "it", "this", "that", "these", "those", "i", "me", "my", "we", "our",
    "you", "your", "he", "him", "his", "she", "her", "they", "them", "their",
    "very", "much", "too", "so", "just", "really", "not", "no", "can", "will",
}


def _tokenize(text: str) -> list[str]:
    return [w.lower() for w in re.findall(r"\b[a-zA-Z]{3,}\b", text) if w.lower() not in STOP_WORDS]


def build_lexical_queries(
    records: list[dict[str, Any]], count: int = 20, seed: int = 42
) -> list[dict[str, Any]]:
    """Build lexical queries using 2-3 rare tokens whose doc frequency is between 2 and 20."""
    rng = random.Random(seed)

    # Compute doc frequency per token
    doc_tokens: list[set[str]] = [set(_tokenize(r["text"])) for r in records]
    df_counter: Counter[str] = Counter()
    for tokens in doc_tokens:
        df_counter.update(tokens)

    # Filter tokens with df between 2 and 20 (or slightly relaxed if fixture is small)
    max_df = max(3, min(20, len(records) // 3))
    min_df = 2
    rare_tokens = [tok for tok, count_df in df_counter.items() if min_df <= count_df <= max_df]

    queries: list[dict[str, Any]] = []
    shuffled_records = list(records)
    rng.shuffle(shuffled_records)

    for r in shuffled_records:
        if len(queries) >= count:
            break
        r_tokens = [t for t in set(_tokenize(r["text"])) if t in rare_tokens]
        if len(r_tokens) >= 2:
            chosen = rng.sample(r_tokens, min(3, len(r_tokens)))
            # Relevant reviews = all indexed reviews containing all chosen tokens
            relevant: list[str] = []
            for other_r in records:
                other_tokens = set(_tokenize(other_r["text"]))
                if set(chosen).issubset(other_tokens):
                    relevant.append(str(other_r["review_id"]))

            if relevant:
                game = str(r["game"])
                q_text = f"{game} {' '.join(chosen)}"
                queries.append(
                    {
                        "id": f"L{len(queries) + 1:02d}",
                        "bucket": "lexical",
                        "game": game,
                        "query": q_text,
                        "relevant_review_ids": relevant,
                    }
                )

    return queries


async def build_semantic_and_mixed_queries(
    records: list[dict[str, Any]],
    llm: Any,
    count_per_bucket: int = 20,
    seed: int = 42,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Generate semantic and mixed queries using LLM (or deterministic fallback)."""
    rng = random.Random(seed)
    shuffled = list(records)
    rng.shuffle(shuffled)

    semantic_queries: list[dict[str, Any]] = []
    mixed_queries: list[dict[str, Any]] = []

    for r in shuffled:
        if len(semantic_queries) >= count_per_bucket and len(mixed_queries) >= count_per_bucket:
            break

        game = str(r["game"])
        text = str(r["text"])[:300]
        rid = str(r["review_id"])

        if len(semantic_queries) < count_per_bucket:
            # Generate semantic query: describe without using exact distinctive words
            if llm is not None:
                try:
                    res = await llm.generate_text(
                        system="You write concise search queries for a player review search engine.",
                        prompt=f"Review for {game}:\n\"{text}\"\n\nWrite a 4-8 word search query describing what this review is about WITHOUT using any of its specific unique words. Output only the query.",
                    )
                    q_sem = res.text.strip().strip('"')
                except Exception:
                    q_sem = f"{game} player thoughts and feedback"
            else:
                words = _tokenize(text)
                q_sem = f"{game} general player experience" if not words else f"{game} feedback regarding {words[0]}"

            semantic_queries.append(
                {
                    "id": f"S{len(semantic_queries) + 1:02d}",
                    "bucket": "semantic",
                    "game": game,
                    "query": q_sem,
                    "relevant_review_ids": [rid],
                }
            )

        if len(mixed_queries) < count_per_bucket:
            # Generate mixed query
            if llm is not None:
                try:
                    res = await llm.generate_text(
                        system="You write concise natural search queries for mobile game reviews.",
                        prompt=f"Review for {game}:\n\"{text}\"\n\nWrite a natural 3-6 word search query that someone looking for this review would search for. Output only the query.",
                    )
                    q_mix = res.text.strip().strip('"')
                except Exception:
                    words = _tokenize(text)[:3]
                    q_mix = f"{game} {' '.join(words)}"
            else:
                words = _tokenize(text)[:3]
                q_mix = f"{game} {' '.join(words)}" if words else f"{game} player review"

            mixed_queries.append(
                {
                    "id": f"M{len(mixed_queries) + 1:02d}",
                    "bucket": "mixed",
                    "game": game,
                    "query": q_mix,
                    "relevant_review_ids": [rid],
                }
            )

    return semantic_queries, mixed_queries


def main() -> int:
    import asyncio

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", action="store_true", help="use fixture reviews")
    parser.add_argument("--out", type=Path, default=Path("data/eval/retrieval_queries.yaml"))
    args = parser.parse_args()

    settings = get_settings()
    parquet_path = Path("data/processed/reviews.parquet")
    if not parquet_path.exists():
        print(f"Error: {parquet_path} not found. Run clean_reviews.py first.")
        return 1

    df = pd.read_parquet(parquet_path)
    # Only indexed reviews (content_words >= 8)
    df = df[df["content_words"] >= 8]
    records = [{"review_id": str(r.review_id), "game": str(r.game), "text": str(r.content)} for r in df.itertuples()]

    target_count = 10 if args.fixture else 20

    print(f"Building {target_count * 3} retrieval queries from {len(records)} reviews...")
    lexical = build_lexical_queries(records, count=target_count)

    llm = None
    if not args.fixture and settings.gemini_api_key:
        from reviewlens.llm.gemini import GeminiLLM
        llm = GeminiLLM(api_key=settings.gemini_api_key)

    semantic, mixed = asyncio.run(build_semantic_and_mixed_queries(records, llm, count_per_bucket=target_count))

    all_queries = lexical + semantic + mixed
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        yaml.dump(all_queries, f, sort_keys=False)

    print(f"Wrote {len(all_queries)} queries to {args.out}")
    print(f"  Lexical: {len(lexical)}")
    print(f"  Semantic: {len(semantic)}")
    print(f"  Mixed: {len(mixed)}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
