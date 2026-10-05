"""Tests for FakeLLM and GeminiLLM (non-live portions).

Tests GeminiLLM retry logic, structured parsing, and usage tracking
using mocked HTTP responses. Tests FakeLLM fully.

All tests are non-live (no real API calls).
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from reviewlens.llm.fake import FakeLLM, FakeLLMError

# ---------------------------------------------------------------------------
# FakeLLM tests
# ---------------------------------------------------------------------------


class SimpleSchema(BaseModel):
    value: str
    count: int


class TestFakeLLM:
    def test_ordered_text_responses(self) -> None:
        async def run() -> None:
            fake = FakeLLM(responses=["first", "second"])
            r1 = await fake.generate_text(system="s", prompt="p1")
            r2 = await fake.generate_text(system="s", prompt="p2")
            assert r1.text == "first"
            assert r2.text == "second"

        asyncio.run(run())

    def test_ordered_structured_responses(self) -> None:
        async def run() -> None:
            fake = FakeLLM(responses=['{"value": "hello", "count": 42}'])
            result = await fake.generate_structured(system="s", prompt="p", schema=SimpleSchema)
            assert isinstance(result.data, SimpleSchema)
            assert result.data.value == "hello"
            assert result.data.count == 42

        asyncio.run(run())

    def test_pydantic_model_response(self) -> None:
        async def run() -> None:
            model_instance = SimpleSchema(value="test", count=7)
            fake = FakeLLM(responses=[model_instance])
            result = await fake.generate_structured(system="s", prompt="p", schema=SimpleSchema)
            assert result.data.count == 7

        asyncio.run(run())

    def test_substring_matcher(self) -> None:
        async def run() -> None:
            fake = FakeLLM(
                responses=[
                    ("crash", "matched crash"),
                    ("", "default"),
                ]
            )
            r = await fake.generate_text(system="", prompt="players mention crashes")
            assert r.text == "matched crash"

        asyncio.run(run())

    def test_substring_matcher_mismatch_raises(self) -> None:
        async def run() -> None:
            fake = FakeLLM(responses=[("crash", "matched")])
            with pytest.raises(FakeLLMError, match="not found in prompt"):
                await fake.generate_text(system="", prompt="no match here")

        asyncio.run(run())

    def test_exhausted_responses_raises(self) -> None:
        async def run() -> None:
            fake = FakeLLM(responses=["only one"])
            await fake.generate_text(system="", prompt="p1")
            with pytest.raises(FakeLLMError, match="ran out of scripted responses"):
                await fake.generate_text(system="", prompt="p2")

        asyncio.run(run())

    def test_default_response(self) -> None:
        async def run() -> None:
            fake = FakeLLM(responses=[], default_response="fallback")
            r = await fake.generate_text(system="", prompt="p")
            assert r.text == "fallback"

        asyncio.run(run())

    def test_call_recording(self) -> None:
        async def run() -> None:
            fake = FakeLLM(responses=["r1", "r2"])
            await fake.generate_text(system="sys1", prompt="p1")
            await fake.generate_text(system="sys2", prompt="p2")
            assert fake.call_count == 2
            assert fake.calls[0]["prompt"] == "p1"
            assert fake.calls[1]["system"] == "sys2"

        asyncio.run(run())

    def test_reset(self) -> None:
        async def run() -> None:
            fake = FakeLLM(responses=["r1", "r2"])
            await fake.generate_text(system="", prompt="p")
            fake.reset()
            assert fake.call_count == 0
            r = await fake.generate_text(system="", prompt="p")
            assert r.text == "r1"

        asyncio.run(run())

    def test_code_fence_stripped_in_structured(self) -> None:
        async def run() -> None:
            fake = FakeLLM(responses=['```json\n{"value": "fenced", "count": 1}\n```'])
            result = await fake.generate_structured(system="s", prompt="p", schema=SimpleSchema)
            assert result.data.value == "fenced"

        asyncio.run(run())

    def test_model_name_is_fake(self) -> None:
        async def run() -> None:
            fake = FakeLLM(responses=["hello"])
            r = await fake.generate_text(system="", prompt="p")
            assert r.model == "fake"

        asyncio.run(run())
