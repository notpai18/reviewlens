"""Split retrieved evidence into cited vs. merely-retrieved for the API response."""

from __future__ import annotations

from typing import Any

from reviewlens.api.schemas import CitationSchema, RetrievedInfo
from reviewlens.models import FinalAnswer, RetrievedReview

SNIPPET_CHARS = 200


def split_evidence(
    answer: FinalAnswer | None,
    sql_result: Any,
    docs: list[RetrievedReview],
) -> tuple[list[CitationSchema], list[RetrievedInfo]]:
    """Return ``(citations, remaining_retrieved)``.

    ``citations`` lists only evidence ids that a (verified) finding actually cites, in
    first-cited order, one entry per id. ``remaining_retrieved`` holds the retrieved
    reviews that no finding cites, in retrieval order.
    """
    cited_ids: dict[str, None] = {}
    for finding in answer.findings if answer else []:
        for eid in finding.evidence_ids:
            cited_ids.setdefault(eid, None)

    by_id = {f"REV:{d.review_id}": d for d in docs}
    citations: list[CitationSchema] = []
    for eid in cited_ids:
        if eid == "SQL#1" and sql_result and sql_result.result:
            citations.append(CitationSchema(id=eid, type="sql", label="SQL Query"))
        elif eid in by_id:
            d = by_id[eid]
            citations.append(
                CitationSchema(
                    id=eid,
                    type="review",
                    snippet=d.text[:SNIPPET_CHARS],
                    game=d.game,
                    date=d.review_date.isoformat(),
                    rating=d.rating,
                )
            )

    remaining = [
        RetrievedInfo(
            review_id=d.review_id,
            rank_score=float(d.score) if d.score is not None else 0.0,
            snippet=d.text[:SNIPPET_CHARS],
            game=d.game,
            date=d.review_date.isoformat(),
            rating=d.rating,
        )
        for d in docs
        if f"REV:{d.review_id}" not in cited_ids
    ]
    return citations, remaining
