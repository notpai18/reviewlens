"""Topical retrieval precision for saved /ask responses (read-only proxy metric).

For each saved response ``<dir>/<topic>.json`` this reports:

* retrieval precision - share of ALL retrieved reviews whose full text matches the
  topic pattern below;
* citation precision - share of the reviews actually cited by findings that match.

The topic patterns are fixed here, in advance, and are deliberately simple word
matches. This is a proxy (a review can be on topic without using these words), used
only to compare before/after on the same three questions. It does NOT use the golden
set and nothing in the system is tuned against it.

Usage: python scripts/topic_precision.py eval/results/topic_check/before [after ...]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pandas as pd

TOPIC_PATTERNS = {
    # Original 3 topics
    "combat": re.compile(r"\b(combat|battl\w*|fight\w*|attack\w*)\b", re.I),
    "ads": re.compile(r"\b(ads?|adverts?|advertis\w*|commercials?)\b", re.I),
    "crashes": re.compile(r"\b(crash\w*|freez\w*|froze|force[- ]clos\w*)\b", re.I),
    # 5 held-out topics
    "battery": re.compile(r"\b(batter\w*|drain\w*|overheat\w*)\b", re.I),
    "matchmaking": re.compile(r"\b(match-?making|matched|opponent\w*|matchup\w*)\b", re.I),
    "login": re.compile(r"\b(log[- ]?in|sign[- ]?in|logged|logging|account|password)\b", re.I),
    "support": re.compile(r"\b(support|customer[- ]service|ticket\w*|refund\w*|contact\w*)\b", re.I),
    "lag": re.compile(r"\b(lag\w*|latency|ping|stutter\w*|delay\w*|fps|frame[- ]rate)\b", re.I),
}


def _ids(resp: dict) -> tuple[list[str], list[str]]:
    retrieved: dict[str, None] = {}
    for c in resp.get("citations", []):
        if c["type"] == "review":
            retrieved.setdefault(c["id"].removeprefix("REV:"), None)
    for r in resp.get("retrieved", []):
        retrieved.setdefault(r["review_id"], None)
    cited: dict[str, None] = {}
    for f in (resp.get("answer") or {}).get("findings", []):
        for eid in f["evidence_ids"]:
            if eid.startswith("REV:"):
                cited.setdefault(eid.removeprefix("REV:"), None)
    return list(retrieved), list(cited)


def _precision(ids: list[str], texts: dict[str, str], pat: re.Pattern[str]) -> tuple[int, int]:
    return sum(1 for i in ids if pat.search(texts.get(i, ""))), len(ids)


def main(dirs: list[str]) -> int:
    df = pd.read_parquet("data/processed/reviews.parquet", columns=["review_id", "content"])
    texts = dict(zip(df["review_id"], df["content"], strict=True))
    for d in dirs:
        print(f"\n## {d}")
        print("topic        retrieval_precision   citation_precision")
        tot = [0, 0, 0, 0]
        for topic, pat in TOPIC_PATTERNS.items():
            topic_file = Path(d) / f"{topic}.json"
            if not topic_file.exists():
                continue
            resp = json.loads(topic_file.read_text())
            retrieved, cited = _ids(resp)
            rh, rn = _precision(retrieved, texts, pat)
            ch, cn = _precision(cited, texts, pat)
            print(
                f"{topic:<12} {rh}/{rn} = {rh / rn:.2f}          {ch}/{cn} = {ch / max(cn, 1):.2f}"
            )
            tot = [tot[0] + rh, tot[1] + rn, tot[2] + ch, tot[3] + cn]
        if tot[1] > 0:
            print(
                f"{'overall':<12} {tot[0]}/{tot[1]} = {tot[0] / tot[1]:.2f}          "
                f"{tot[2]}/{tot[3]} = {tot[2] / max(tot[3], 1):.2f}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
