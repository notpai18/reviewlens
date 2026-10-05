"""Prompt rendering utilities.

Prompts are Markdown files with {placeholder} syntax.
We use str.replace per-placeholder, NOT str.format — this avoids issues with
literal JSON braces in prompt text.
"""

from __future__ import annotations

import re
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent


def load_prompt(name: str) -> str:
    """Load a prompt template from the prompts directory."""
    path = _PROMPTS_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


def render_prompt(template: str, **kwargs: str) -> str:
    """Render a prompt template by replacing {key} placeholders.

    Single-pass substitution (one regex over the template), so text inserted for one
    placeholder is never re-scanned for others. This matters because values can be
    untrusted (user questions, review text). Placeholders without a supplied value, and
    literal JSON braces, are left untouched. Does not use str.format.
    """
    if not kwargs:
        return template
    pattern = re.compile("|".join(re.escape("{" + k + "}") for k in kwargs))
    return pattern.sub(lambda m: kwargs[m.group(0)[1:-1]], template)


def load_and_render(name: str, **kwargs: str) -> str:
    """Load a prompt file and render it with the given keyword arguments."""
    template = load_prompt(name)
    return render_prompt(template, **kwargs)
