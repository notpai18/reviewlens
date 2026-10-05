"""Gemini LLM client via the google-genai SDK.

Source of API knowledge: installed ``google-genai`` 2.28.0 (``help()`` / source inspection):
``client.aio.models.generate_content`` and ``google.genai.errors.APIError`` (has ``.code``).

Behaviour (spec 9.7):
- tenacity retries (max 4, exponential backoff with jitter) on 429 / 5xx / timeouts only;
  other 4xx errors are never retried.
- Token-bucket rate limit (``rpm_limit``) and a semaphore of 4 concurrent calls.
- Structured output: native ``response_schema`` first. If the SDK/model rejects the schema
  (HTTP 400 or SDK conversion error) fall back to JSON mode with the schema pasted into the
  system prompt. On a parse failure make exactly one repair call, then raise ``LLMOutputError``.
- Optional per-request ``UsageTracker`` and optional ``DiskCache`` (eval only).
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, TypeVar

import google.genai as genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from pydantic import BaseModel, ValidationError
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from reviewlens.llm.usage import DiskCache, UsageTracker, cache_key
from reviewlens.models import LLMResult, LLMStructured

T = TypeVar("T", bound=BaseModel)


class LLMOutputError(Exception):
    """Raised when LLM output cannot be parsed after the single repair attempt."""


def is_retryable(exc: BaseException) -> bool:
    """True for 429, 5xx and timeouts. Every other error (incl. other 4xx) is final."""
    if isinstance(exc, genai_errors.APIError):
        code = getattr(exc, "code", None)
        return isinstance(code, int) and (code == 429 or code >= 500)
    return isinstance(exc, TimeoutError | asyncio.TimeoutError)


def is_schema_rejection(exc: BaseException) -> bool:
    """True when the SDK or API refused the Pydantic response_schema (not an outage)."""
    if isinstance(exc, genai_errors.APIError):
        return getattr(exc, "code", None) == 400
    return isinstance(exc, TypeError | ValueError) and not isinstance(exc, ValidationError)


def parse_json_from_text(text: str) -> Any:
    """Parse JSON, stripping Markdown code fences if present."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        inner = lines[1:-1] if lines and lines[-1].strip() == "```" else lines[1:]
        text = "\n".join(inner).strip()
    return json.loads(text)


class _TokenBucket:
    """Simple async token bucket (requests per minute)."""

    def __init__(self, rpm: int) -> None:
        self._capacity = float(rpm)
        self._tokens = float(rpm)
        self._refill_rate = rpm / 60.0  # tokens per second
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            self._tokens = min(
                self._capacity, self._tokens + (now - self._last_refill) * self._refill_rate
            )
            self._last_refill = now
            if self._tokens < 1:
                await asyncio.sleep((1 - self._tokens) / self._refill_rate)
                self._tokens = 0.0
                self._last_refill = time.monotonic()
            else:
                self._tokens -= 1.0


class GeminiLLM:
    """Google Gemini client implementing the ``LLMClient`` protocol."""

    def __init__(
        self,
        api_key: str | list[str] = "",
        model: str = "gemini-2.5-flash-lite",
        rpm_limit: int = 10,
        timeout_s: int = 45,
        *,
        client: Any | None = None,
        usage: UsageTracker | None = None,
        cache: DiskCache | None = None,
        max_attempts: int = 4,
        backoff_initial: float = 1.0,
        backoff_jitter: float = 1.0,
    ) -> None:
        self._keys: list[str] = []
        if isinstance(api_key, list):
            self._keys = [k.strip() for k in api_key if k.strip()]
        elif isinstance(api_key, str) and api_key.strip():
            self._keys = [k.strip() for k in api_key.split(",") if k.strip()]

        if client is not None:
            self._clients = [client]
        elif self._keys:
            self._clients = [genai.Client(api_key=k) for k in self._keys]
        else:
            self._clients = [genai.Client(api_key="")]

        self._active_key_idx = 0
        self._client = self._clients[0]
        self._model = model
        self._timeout_s = timeout_s
        self._bucket = _TokenBucket(rpm_limit)
        self._semaphore = asyncio.Semaphore(4)
        self.usage = usage if usage is not None else UsageTracker()
        self._cache = cache
        self._max_attempts = max_attempts
        self._backoff_initial = backoff_initial
        self._backoff_jitter = backoff_jitter

    @property
    def model_name(self) -> str:
        return self._model

    async def _call_once(
        self, prompt: str, config: genai_types.GenerateContentConfig
    ) -> tuple[str, int, int]:
        await self._bucket.acquire()
        async with self._semaphore:
            num_clients = len(self._clients)
            for attempt in range(num_clients):
                active_client = self._clients[self._active_key_idx]
                try:
                    response = await asyncio.wait_for(
                        active_client.aio.models.generate_content(
                            model=self._model, contents=prompt, config=config
                        ),
                        timeout=self._timeout_s,
                    )
                    text = response.text or ""
                    um = getattr(response, "usage_metadata", None)
                    pt = int(getattr(um, "prompt_token_count", 0) or 0)
                    ct = int(getattr(um, "candidates_token_count", 0) or 0)
                    return text, pt, ct
                except genai_errors.APIError as e:
                    code = getattr(e, "code", None)
                    if code == 429 and num_clients > 1 and attempt < num_clients - 1:
                        self._active_key_idx = (self._active_key_idx + 1) % num_clients
                        self._client = self._clients[self._active_key_idx]
                        continue
                    raise
            raise RuntimeError("All configured clients failed.")

    async def _call(
        self, prompt: str, config: genai_types.GenerateContentConfig
    ) -> tuple[str, int, int]:
        """One logical call with retries on 429/5xx/timeouts. Records usage."""
        retrying = AsyncRetrying(
            retry=retry_if_exception(is_retryable),
            stop=stop_after_attempt(self._max_attempts),
            wait=wait_exponential_jitter(
                initial=self._backoff_initial, max=30, jitter=self._backoff_jitter
            ),
            reraise=True,
        )
        async for attempt in retrying:
            with attempt:
                text, pt, ct = await self._call_once(prompt, config)
        self.usage.record(pt, ct)
        return text, pt, ct

    async def generate_text(
        self,
        *,
        system: str,
        prompt: str,
        temperature: float = 0.0,
    ) -> LLMResult:
        key = cache_key(
            model=self._model,
            system=system,
            prompt=prompt,
            schema_name="",
            temperature=temperature,
        )
        t0 = time.monotonic()
        cached = self._cache.get(key) if self._cache else None
        if cached is not None:
            self.usage.record_cache_hit()
            return LLMResult(
                text=str(cached["text"]),
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=0,
                model=self._model,
            )
        config = genai_types.GenerateContentConfig(
            system_instruction=system, temperature=temperature
        )
        text, pt, ct = await self._call(prompt, config)
        if self._cache:
            self._cache.put(key, {"text": text})
        return LLMResult(
            text=text,
            prompt_tokens=pt,
            completion_tokens=ct,
            latency_ms=int((time.monotonic() - t0) * 1000),
            model=self._model,
        )

    async def generate_structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[T],
        temperature: float = 0.0,
    ) -> LLMStructured:
        key = cache_key(
            model=self._model,
            system=system,
            prompt=prompt,
            schema_name=schema.__name__,
            temperature=temperature,
        )
        t0 = time.monotonic()
        cached = self._cache.get(key) if self._cache else None
        if cached is not None:
            try:
                data = schema.model_validate(cached["data"])
            except (KeyError, ValidationError):
                data = None
            if data is not None:
                self.usage.record_cache_hit()
                return LLMStructured(
                    data=data, prompt_tokens=0, completion_tokens=0, latency_ms=0, model=self._model
                )

        schema_json = json.dumps(schema.model_json_schema(), indent=2)
        json_system = (
            f"{system}\n\nReturn valid JSON matching this schema:\n```json\n{schema_json}\n```"
        )
        json_config = genai_types.GenerateContentConfig(
            system_instruction=json_system,
            temperature=temperature,
            response_mime_type="application/json",
        )
        pt_total = 0
        ct_total = 0

        # 1) Native structured output; fall back to JSON mode only if the schema is rejected.
        native_config = genai_types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            response_mime_type="application/json",
            response_schema=schema,
        )
        try:
            text, pt, ct = await self._call(prompt, native_config)
        except Exception as exc:
            if not is_schema_rejection(exc):
                raise
            text, pt, ct = await self._call(prompt, json_config)
        pt_total += pt
        ct_total += ct

        # 2) Parse; on failure make exactly ONE repair call, then raise LLMOutputError.
        try:
            data = schema.model_validate(parse_json_from_text(text))
        except (ValidationError, json.JSONDecodeError) as parse_exc:
            repair_prompt = (
                f"Your previous response could not be parsed. Error: {parse_exc}\n"
                f"Previous response:\n{text}\n\nOriginal prompt:\n{prompt}\n\n"
                "Return only valid JSON."
            )
            text2, pt2, ct2 = await self._call(repair_prompt, json_config)
            pt_total += pt2
            ct_total += ct2
            try:
                data = schema.model_validate(parse_json_from_text(text2))
            except (ValidationError, json.JSONDecodeError) as repair_exc:
                raise LLMOutputError(
                    f"LLM output could not be parsed after one repair: {repair_exc}"
                ) from repair_exc

        if self._cache:
            self._cache.put(key, {"data": data.model_dump(mode="json")})
        return LLMStructured(
            data=data,
            prompt_tokens=pt_total,
            completion_tokens=ct_total,
            latency_ms=int((time.monotonic() - t0) * 1000),
            model=self._model,
        )
