"""Thin LLM client wrapper around the OpenAI-compatible API at OPENAI_BASE_URL.

The Frankenstein engine talks to one function: complete_json(prompt, schema).
That function returns a parsed Pydantic model or raises.

For offline dev / tests, swap with StubLLM that returns canned responses.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Type, TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """Raised when the LLM can't produce a valid response after retries."""


def _client() -> OpenAI:
    api_key = os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("OPENAI_BASE_URL")
    if not api_key:
        raise LLMError("OPENAI_API_KEY not set in environment")
    return OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)


def _model_name() -> str:
    return os.environ.get("OPENAI_MODEL", "gpt-4o")


def complete_json(
    system_prompt: str,
    user_prompt: str,
    response_model: Type[T],
    *,
    max_retries: int = 2,
    temperature: float = 0.0,
) -> T:
    """Ask the LLM for JSON conforming to a Pydantic schema, return parsed model.

    Retries on validation failure. On final failure raises LLMError with the
    last error attached so the calling agent can use it for reflexion.
    """
    client = _client()
    schema_json = json.dumps(response_model.model_json_schema())

    last_err: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        attempt_prompt = user_prompt
        if attempt > 0 and last_err is not None:
            attempt_prompt = (
                user_prompt
                + "\n\n---\nYour previous response failed validation:\n"
                + str(last_err)
                + "\nFix the JSON and try again. Output ONLY the JSON object, no prose."
            )

        resp = client.chat.completions.create(
            model=_model_name(),
            temperature=temperature,
            messages=[
                {"role": "system", "content": system_prompt + "\n\nYou must respond with JSON only. Schema:\n" + schema_json},
                {"role": "user", "content": attempt_prompt},
            ],
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or ""

        try:
            data: Any = json.loads(raw)
            return response_model.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as e:
            last_err = e
            continue

    raise LLMError(f"LLM failed to produce valid {response_model.__name__} after {max_retries + 1} attempts: {last_err}")


# --- stub for offline dev / tests ---

class StubLLM:
    """In-process LLM substitute. Returns the next canned response from a queue.

    Usage:
        stub = StubLLM()
        stub.queue_response({"name": "ESP32", "voltage": "3.3V", ...})
        result = stub.complete_json(system, user, Schematic)  # returns parsed Schematic
    """

    def __init__(self) -> None:
        self._queue: list[BaseModel] = []
        self.calls: list[tuple[str, str, Type[BaseModel]]] = []

    def queue_response(self, model: BaseModel) -> None:
        self._queue.append(model)

    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: Type[T],
        **_kwargs: Any,
    ) -> T:
        self.calls.append((system_prompt, user_prompt, response_model))
        if not self._queue:
            raise LLMError(f"StubLLM: no queued response for {response_model.__name__}")
        canned = self._queue.pop(0)
        # Validate the canned response matches the requested schema — fails loud
        # if a test queues the wrong type.
        if not isinstance(canned, response_model):
            raise LLMError(
                f"StubLLM: queued {type(canned).__name__} but caller wanted {response_model.__name__}"
            )
        return canned  # type: ignore[return-value]