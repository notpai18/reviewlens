"""Tests for prompt rendering (spec 12: every prompt renders; JSON braces stay intact)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import reviewlens.prompts.renderer as renderer
from reviewlens.prompts.renderer import load_and_render, load_prompt, render_prompt

PROMPTS_DIR = Path(renderer.__file__).parent
PROMPT_FILES = sorted(p.name for p in PROMPTS_DIR.glob("*.md"))

# A placeholder is {identifier}; literal JSON like {"a": 1} never matches this.
PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


def test_prompt_files_exist() -> None:
    assert {"plan.md", "sql_generate.md", "sql_repair.md", "synthesize.md"} <= set(PROMPT_FILES)


@pytest.mark.parametrize("name", PROMPT_FILES)
def test_every_prompt_renders_with_dummy_values(name: str) -> None:
    template = load_prompt(name)
    keys = sorted(set(PLACEHOLDER.findall(template)))
    dummy = {k: f"<<{k.upper()}>>" for k in keys}
    rendered = load_and_render(name, **dummy)

    # every placeholder replaced, none left over
    assert PLACEHOLDER.findall(rendered) == []
    for k in keys:
        assert f"<<{k.upper()}>>" in rendered


@pytest.mark.parametrize("name", PROMPT_FILES)
def test_json_braces_survive_rendering(name: str) -> None:
    template = load_prompt(name)
    keys = sorted(set(PLACEHOLDER.findall(template)))
    rendered = render_prompt(template, **dict.fromkeys(keys, "x"))
    # braces that were not placeholders must be unchanged
    literal_before = template
    for k in keys:
        literal_before = literal_before.replace("{" + k + "}", "x")
    assert rendered == literal_before
    assert rendered.count("{") == rendered.count("}")


def test_render_basic() -> None:
    assert render_prompt("Hello {name}, {count} msgs", name="Al", count="5") == "Hello Al, 5 msgs"


def test_render_keeps_json_braces() -> None:
    out = render_prompt('S: {schema}\n{"key": "value"}', schema="X")
    assert out == 'S: X\n{"key": "value"}'


def test_render_missing_placeholder_left_untouched() -> None:
    assert render_prompt("Hi {name}, {missing}", name="Al") == "Hi Al, {missing}"


def test_render_does_not_reinterpret_substituted_text() -> None:
    # Untrusted text containing another placeholder must be inserted verbatim,
    # regardless of kwarg order.
    for kwargs in (
        {"question": "{other}", "other": "SECRET"},
        {"other": "SECRET", "question": "{other}"},
    ):
        out = render_prompt("Q: {question} | O: {other}", **kwargs)
        assert out == "Q: {other} | O: SECRET"


def test_load_prompt_missing_file() -> None:
    with pytest.raises(FileNotFoundError):
        load_prompt("does_not_exist.md")
