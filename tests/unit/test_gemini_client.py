"""Tests for GeminiLLM using a mocked google-genai client (no network, no API key).

Covers spec 12 `test_llm_client.py`: retry on 429 then success; no retry on 400;
structured parse with code fences; one repair then LLMOutputError; usage tracker.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from google.genai import errors as genai_errors
from pydantic import BaseModel

from reviewlens.llm.gemini import (
    GeminiLLM,
    LLMOutputError,
    _TokenBucket,
    is_retryable,
    is_schema_rejection,
    parse_json_from_text,
)
from reviewlens.llm.usage import DiskCache, UsageTracker, cache_key


class Answer(BaseModel):
    value: str
    count: int


def _resp(text: str, pt: int = 10, ct: int = 5) -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        usage_metadata=SimpleNamespace(prompt_token_count=pt, candidates_token_count=ct),
    )


def _api_error(code: int) -> genai_errors.APIError:
    cls = genai_errors.ClientError if code < 500 else genai_errors.ServerError
    return cls(code, {"error": {"message": f"boom {code}", "status": "X"}})


class ScriptedClient:
    """Mimics client.aio.models.generate_content with a scripted list of outcomes."""

    def __init__(self, outcomes: list[Any]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []
        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=self._generate))

    async def _generate(self, *, model: str, contents: str, config: Any) -> Any:
        self.calls.append({"model": model, "contents": contents, "config": config})
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def make_llm(client: ScriptedClient, **kw: Any) -> GeminiLLM:
    return GeminiLLM(
        client=client,
        rpm_limit=6000,
        timeout_s=5,
        backoff_initial=0.0,
        backoff_jitter=0.0,
        **kw,
    )


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# ---------------------------------------------------------------- predicates


def test_is_retryable_classification() -> None:
    assert is_retryable(_api_error(429))
    assert is_retryable(_api_error(500))
    assert is_retryable(_api_error(503))
    assert is_retryable(TimeoutError())
    assert not is_retryable(_api_error(400))
    assert not is_retryable(_api_error(401))
    assert not is_retryable(_api_error(404))
    assert not is_retryable(ValueError("429 in message must not count"))


def test_is_schema_rejection_classification() -> None:
    assert is_schema_rejection(_api_error(400))
    assert not is_schema_rejection(_api_error(429))
    assert not is_schema_rejection(_api_error(500))
    assert is_schema_rejection(TypeError("unsupported schema"))
    assert not is_schema_rejection(KeyError("x"))


def test_parse_json_from_text_variants() -> None:
    assert parse_json_from_text('{"a": 1}') == {"a": 1}
    assert parse_json_from_text('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_from_text('```\n{"a": 1}') == {"a": 1}  # unterminated fence
    with pytest.raises(ValueError):
        parse_json_from_text("not json")


# ---------------------------------------------------------------- retries


def test_retry_on_429_then_success_text() -> None:
    client = ScriptedClient([_api_error(429), _api_error(429), _resp("hello")])
    llm = make_llm(client)
    result = run(llm.generate_text(system="s", prompt="p"))
    assert result.text == "hello"
    assert len(client.calls) == 3
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 5


def test_retry_on_5xx_stops_after_max_attempts() -> None:
    client = ScriptedClient([_api_error(503)] * 4)
    llm = make_llm(client)
    with pytest.raises(genai_errors.ServerError):
        run(llm.generate_text(system="s", prompt="p"))
    assert len(client.calls) == 4  # max 4 attempts


def test_no_retry_on_400() -> None:
    client = ScriptedClient([_api_error(400), _resp("never reached")])
    llm = make_llm(client)
    with pytest.raises(genai_errors.ClientError):
        run(llm.generate_text(system="s", prompt="p"))
    assert len(client.calls) == 1


def test_retry_on_timeout() -> None:
    client = ScriptedClient([TimeoutError(), _resp("ok")])
    llm = make_llm(client)
    assert run(llm.generate_text(system="s", prompt="p")).text == "ok"
    assert len(client.calls) == 2


def test_real_wait_for_timeout_is_retried() -> None:
    async def slow(**_: Any) -> Any:
        await asyncio.sleep(5)

    client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=slow)))
    llm = GeminiLLM(
        client=client,
        rpm_limit=6000,
        timeout_s=0.05,  # type: ignore[arg-type]
        max_attempts=2,
        backoff_initial=0.0,
        backoff_jitter=0.0,
    )
    with pytest.raises(TimeoutError):
        run(llm.generate_text(system="s", prompt="p"))


# ---------------------------------------------------------------- structured


def test_structured_native_success() -> None:
    client = ScriptedClient([_resp('{"value": "a", "count": 2}')])
    llm = make_llm(client)
    out = run(llm.generate_structured(system="s", prompt="p", schema=Answer))
    assert out.data == Answer(value="a", count=2)
    assert len(client.calls) == 1
    # native call carries the schema
    assert client.calls[0]["config"].response_schema is Answer


def test_structured_parses_code_fences() -> None:
    client = ScriptedClient([_resp('```json\n{"value": "f", "count": 1}\n```')])
    out = run(make_llm(client).generate_structured(system="s", prompt="p", schema=Answer))
    assert out.data.value == "f"  # type: ignore[attr-defined]


def test_structured_schema_rejection_falls_back_to_json_mode() -> None:
    client = ScriptedClient([_api_error(400), _resp('{"value": "b", "count": 3}')])
    out = run(make_llm(client).generate_structured(system="SYS", prompt="p", schema=Answer))
    assert out.data.count == 3  # type: ignore[attr-defined]
    assert len(client.calls) == 2
    fallback_cfg = client.calls[1]["config"]
    assert fallback_cfg.response_schema is None
    assert fallback_cfg.response_mime_type == "application/json"
    # schema is pasted into the system prompt in fallback mode
    assert "count" in fallback_cfg.system_instruction
    assert fallback_cfg.system_instruction.startswith("SYS")


def test_structured_outage_is_not_treated_as_schema_rejection() -> None:
    # 5xx after retries must propagate; it must NOT silently switch to the fallback path.
    client = ScriptedClient([_api_error(500)] * 4 + [_resp('{"value": "x", "count": 1}')])
    with pytest.raises(genai_errors.ServerError):
        run(make_llm(client).generate_structured(system="s", prompt="p", schema=Answer))
    assert len(client.calls) == 4


def test_structured_one_repair_then_success() -> None:
    client = ScriptedClient([_resp("garbage"), _resp('{"value": "r", "count": 9}')])
    llm = make_llm(client)
    out = run(llm.generate_structured(system="s", prompt="p", schema=Answer))
    assert out.data.count == 9  # type: ignore[attr-defined]
    assert len(client.calls) == 2
    assert "could not be parsed" in client.calls[1]["contents"]
    # tokens accumulated across both calls
    assert out.prompt_tokens == 20
    assert out.completion_tokens == 10


def test_structured_one_repair_then_llm_output_error() -> None:
    client = ScriptedClient([_resp("garbage"), _resp("still garbage"), _resp("unused")])
    with pytest.raises(LLMOutputError):
        run(make_llm(client).generate_structured(system="s", prompt="p", schema=Answer))
    assert len(client.calls) == 2  # exactly ONE repair call, no more


def test_structured_validation_failure_triggers_repair() -> None:
    # valid JSON but wrong shape -> pydantic ValidationError -> repair
    client = ScriptedClient([_resp('{"value": "x"}'), _resp('{"value": "x", "count": 4}')])
    out = run(make_llm(client).generate_structured(system="s", prompt="p", schema=Answer))
    assert out.data.count == 4  # type: ignore[attr-defined]


# ---------------------------------------------------------------- usage + cache


def test_usage_tracker_counts_calls_and_tokens() -> None:
    usage = UsageTracker()
    client = ScriptedClient([_resp("a", 7, 3), _api_error(429), _resp("b", 1, 1)])
    llm = make_llm(client, usage=usage)
    run(llm.generate_text(system="s", prompt="p1"))
    run(llm.generate_text(system="s", prompt="p2"))
    # a retried 429 is not a successful call and consumes no tokens
    assert usage.calls == 2
    assert usage.prompt_tokens == 8
    assert usage.completion_tokens == 4
    assert usage.total_tokens == 12
    assert usage.snapshot()["total_tokens"] == 12


def test_disk_cache_hit_avoids_second_call(tmp_path: Path) -> None:
    cache = DiskCache(tmp_path / "c")
    client = ScriptedClient([_resp('{"value": "c", "count": 1}')])
    usage = UsageTracker()
    llm = make_llm(client, cache=cache, usage=usage)
    first = run(llm.generate_structured(system="s", prompt="p", schema=Answer))
    second = run(llm.generate_structured(system="s", prompt="p", schema=Answer))
    assert first.data == second.data
    assert len(client.calls) == 1
    assert usage.cache_hits == 1


def test_disk_cache_text_and_key_sensitivity(tmp_path: Path) -> None:
    cache = DiskCache(tmp_path)
    client = ScriptedClient([_resp("one"), _resp("two")])
    llm = make_llm(client, cache=cache)
    assert run(llm.generate_text(system="s", prompt="p")).text == "one"
    assert run(llm.generate_text(system="s", prompt="p")).text == "one"  # cached
    assert run(llm.generate_text(system="s", prompt="DIFFERENT")).text == "two"
    k1 = cache_key(model="m", system="s", prompt="p", schema_name="A", temperature=0.0)
    k2 = cache_key(model="m", system="s", prompt="p", schema_name="B", temperature=0.0)
    k3 = cache_key(model="m", system="s", prompt="p", schema_name="A", temperature=0.5)
    assert len({k1, k2, k3}) == 3


def test_disk_cache_ignores_corrupt_entries(tmp_path: Path) -> None:
    cache = DiskCache(tmp_path)
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "list.json").write_text("[1]", encoding="utf-8")
    assert cache.get("bad") is None
    assert cache.get("list") is None
    assert cache.get("missing") is None


def test_corrupt_structured_cache_entry_is_recomputed(tmp_path: Path) -> None:
    cache = DiskCache(tmp_path)
    key = cache_key(
        model="gemini-2.0-flash-exp", system="s", prompt="p", schema_name="Answer", temperature=0.0
    )
    cache.put(key, {"data": {"wrong": "shape"}})
    client = ScriptedClient([_resp('{"value": "ok", "count": 1}')])
    out = run(
        make_llm(client, cache=cache).generate_structured(system="s", prompt="p", schema=Answer)
    )
    assert out.data.value == "ok"  # type: ignore[attr-defined]
    assert len(client.calls) == 1


# ---------------------------------------------------------------- misc


def test_model_name_property_and_empty_text() -> None:
    client = ScriptedClient([SimpleNamespace(text=None, usage_metadata=None)])
    llm = make_llm(client)
    assert llm.model_name == "gemini-2.0-flash-exp"
    res = run(llm.generate_text(system="s", prompt="p"))
    assert res.text == ""
    assert res.prompt_tokens == 0


def test_token_bucket_throttles() -> None:
    async def go() -> float:
        bucket = _TokenBucket(rpm=600)  # 10/s, capacity 600
        bucket._tokens = 0.0
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        await bucket.acquire()
        return loop.time() - t0

    assert run(go()) >= 0.05  # had to wait for a refill (~0.1s)


def test_token_bucket_allows_burst_within_capacity() -> None:
    async def go() -> float:
        bucket = _TokenBucket(rpm=600)
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        for _ in range(5):
            await bucket.acquire()
        return loop.time() - t0

    assert run(go()) < 0.05
