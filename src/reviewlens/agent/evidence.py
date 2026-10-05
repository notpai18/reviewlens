"""Format retrieved reviews and SQL results into XML-like evidence blocks.

Everything that originates from the database or from review text is untrusted
(Section 9.5): angle brackets are escaped so that data can never open or close
an evidence tag, control characters are stripped, and long text is truncated.
"""

from __future__ import annotations

import json
import re

from reviewlens.models import FinalAnswer, FindingKind, RetrievedReview
from reviewlens.sql.generate import SQLToolResult

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_EVIDENCE_TAG_ID = re.compile(r'<(?:sql|review) id="([^"]+)"')


def _escape(text: str, attr: bool = False) -> str:
    """Escape &, < and > (and, for attribute values, double quotes)."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return text.replace('"', "&quot;") if attr else text


def _clean(text: str, max_length: int = 500, attr: bool = False) -> str:
    """Strip control characters, truncate, then escape."""
    text = _CONTROL_CHARS.sub("", text)
    if len(text) > max_length:
        text = text[:max_length] + "..."
    return _escape(text, attr=attr)


def _clean_cell(value: object, max_length: int = 500) -> object:
    """Sanitise one SQL result cell (strings only; numbers/None pass through)."""
    if isinstance(value, str):
        # Escaping happens once, on the serialised block below; here we only strip/truncate.
        value = _CONTROL_CHARS.sub("", value)
        return value[:max_length] + "..." if len(value) > max_length else value
    return value


def format_evidence(
    sql_result: SQLToolResult | None,
    docs_result: list[RetrievedReview] | None,
    sql_question: str | None = None,
    max_sql_rows: int = 50,
) -> str:
    """Format the evidence block. All data lives inside ``<evidence>...</evidence>``."""
    blocks: list[str] = []

    if sql_result and sql_result.result:
        r = sql_result.result
        rows = [[_clean_cell(v) for v in row] for row in r.rows[:max_sql_rows]]
        # JSON-serialise, then escape the whole payload so untrusted SQL values cannot
        # contain a literal tag.
        rows_json = _escape(json.dumps(rows, ensure_ascii=False, default=str))
        cols = _clean(",".join(r.columns), max_length=300, attr=True)
        question_attr = (
            f' question="{_clean(sql_question, 200, attr=True)}"' if sql_question else ""
        )
        blocks.append(
            f'<sql id="SQL#1"{question_attr} row_count="{r.row_count}" '
            f'columns="{cols}">{rows_json}</sql>'
        )

    for rev in docs_result or []:
        clean_text = _clean(rev.text, max_length=500)
        ver = _clean(rev.app_version or "", max_length=40, attr=True)
        game = _clean(rev.game, max_length=80, attr=True)
        rid = _clean(rev.review_id, max_length=80, attr=True)
        blocks.append(
            f'<review id="REV:{rid}" game="{game}" '
            f'date="{rev.review_date.isoformat()}" rating="{rev.rating}" version="{ver}">'
            f"{clean_text}</review>"
        )

    if not blocks:
        return "<evidence>\n</evidence>"

    return "<evidence>\n" + "\n".join(blocks) + "\n</evidence>"


def valid_evidence_ids(evidence: str) -> set[str]:
    """Return the ids of the evidence tags actually present in ``evidence``.

    Because all untrusted text has ``<`` escaped, review/SQL content can never forge a
    tag, so only real evidence blocks can contribute an id here.
    """
    return set(_EVIDENCE_TAG_ID.findall(evidence))


def render_answer_markdown(answer: FinalAnswer) -> str:
    """Deterministically render a ``FinalAnswer`` as markdown (no LLM involved)."""
    parts: list[str] = [answer.summary.strip()]

    if answer.findings:
        lines = ["", "**Findings**"]
        for f in answer.findings:
            cites = " ".join(f"[{eid}]" for eid in f.evidence_ids)
            label = "Observed" if f.kind == FindingKind.OBSERVED else "Players say"
            lines.append(f"- *{label}:* {f.statement.strip()} {cites}".rstrip())
        parts.append("\n".join(lines))

    if answer.caveats:
        parts.append("\n".join(["", "**Caveats**", *[f"- {c}" for c in answer.caveats]]))

    if answer.followups:
        parts.append(
            "\n".join(["", "**You could also ask**", *[f"- {q}" for q in answer.followups]])
        )

    return "\n".join(parts).strip()
