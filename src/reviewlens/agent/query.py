"""Search-query hygiene for the review search tool.

The planner is asked to emit only topical content words in ``docs_query``, but LLMs
still sometimes pad queries with generic words ("feedback", "complaints", "players say").
Those words match a large share of all reviews, so they drag unrelated reviews into the
results. ``clean_docs_query`` is the deterministic backstop: it removes such words.
"""

from __future__ import annotations

import re

# Generic words describing *the act of reviewing* rather than the topic being asked about.
_GENERIC_WORDS = frozenset(
    """
    feedback feedbacks opinion opinions comment comments complaint complaints complain
    complains complaining complained praise praises praising praised review reviews
    reviewer reviewers reviewed player players user users gamer gamers people customer
    customers say says saying said talk talks talking talked mention mentions mentioning
    mentioned think thinks thinking thought feel feels feeling felt discuss discusses
    discussing discussed commentary sentiment sentiments experience experiences
    """.split()
)

# Plain function words that carry no topical signal.
_FUNCTION_WORDS = frozenset(
    """
    a an the and or but nor of in on at to for from by with without about around into
    over under between among is are was were be been being am do does did doing have has
    had having what which who whom whose when where why how that this these those there
    here it its they them their theirs we our you your i me my he she his her not no
    also very really just some any all most more much many such as if then than so
    """.split()
)

STOPWORDS = _GENERIC_WORDS | _FUNCTION_WORDS

# Multi-word topical phrases that must be preserved intact so they do not lose meaning
# (e.g. "log in" -> "log", "pay to win" -> "pay win", "customer service" -> "service").
_PROTECTED_PHRASES = (
    r"pay[\s\-]+to[\s\-]+win",
    r"free[\s\-]+to[\s\-]+play",
    r"log[\s\-]+in",
    r"logging[\s\-]+in",
    r"logged[\s\-]+in",
    r"sign[\s\-]+in",
    r"signing[\s\-]+in",
    r"signed[\s\-]+in",
    r"customer[\s\-]+service",
    r"customer[\s\-]+support",
    r"lost[\s\-]+progress",
    r"save[\s\-]+progress",
)
_PHRASE_PATTERN = re.compile(r"\b(" + "|".join(_PROTECTED_PHRASES) + r")\b", re.IGNORECASE)

_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\-]*")


def _clean_candidate(candidate: str) -> list[str]:
    placeholders: dict[str, str] = {}

    def _mask(match: re.Match[str]) -> str:
        key = f"PHRASETOKEN{len(placeholders)}"
        placeholders[key] = match.group(0)
        return key

    masked = _PHRASE_PATTERN.sub(_mask, candidate)
    tokens = _TOKEN.findall(masked)
    return [
        placeholders.get(t, t) for t in tokens if t in placeholders or t.lower() not in STOPWORDS
    ]


def clean_docs_query(query: str, fallback: str | None = None) -> str:
    """Return ``query`` with generic/stop words removed, preserving order and spelling.

    Preserves topical multi-word phrases (e.g. "pay to win", "log in", "customer service",
    "lost progress") that would otherwise lose meaning if constituent function/generic
    words were dropped.

    If nothing topical is left, try ``fallback`` (cleaned the same way); if that is also
    empty, return the original ``query`` unchanged so the search never runs on an empty
    string.
    """
    for candidate in (query, fallback):
        if not candidate:
            continue
        kept = _clean_candidate(candidate)
        if kept:
            return " ".join(kept)
    return query
