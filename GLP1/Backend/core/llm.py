"""
Gemini client wrapper for the chatbot.

Uses the `google-genai` SDK. One client is constructed at import; callers
invoke `chat()` with a list of internal message dicts and receive back the
raw response so the router can inspect function calls.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Iterable

from google import genai
from google.genai import types
from google.genai.errors import APIError

from core.config import settings

logger = logging.getLogger("chatbot.llm")

_client: genai.Client | None = None


def get_client() -> genai.Client:
    global _client
    if _client is None:
        if not settings.google_api_key:
            raise RuntimeError(
                "GOOGLE_API_KEY is not set. Add it to Backend/.env to enable the chatbot."
            )
        _client = genai.Client(api_key=settings.google_api_key)
    return _client


def _to_content(msg: dict) -> types.Content:
    """Convert an internal `{role, content, ...}` message to a Gemini Content.

    Roles map:
      user      → user
      assistant → model
      function  → user (with function_response Part)

    Assistant turns that came back from Gemini carry the raw `model_content`
    Content object. Passing it back verbatim preserves opaque fields Gemini
    requires on round-trip (thought_signature, etc.) — required for tool use
    in Gemini 3.x+.
    """
    role = msg["role"]

    if role == "function":
        return types.Content(
            role="user",
            parts=[
                types.Part.from_function_response(
                    name=msg["name"],
                    response=msg["response"] if isinstance(msg["response"], dict) else {"result": msg["response"]},
                )
            ],
        )

    if role == "assistant" and msg.get("model_content") is not None:
        return msg["model_content"]

    if role == "assistant" and msg.get("function_calls"):
        parts = []
        if msg.get("content"):
            parts.append(types.Part.from_text(text=msg["content"]))
        for call in msg["function_calls"]:
            parts.append(
                types.Part.from_function_call(name=call["name"], args=call.get("args") or {})
            )
        return types.Content(role="model", parts=parts)

    gemini_role = "model" if role == "assistant" else "user"
    return types.Content(role=gemini_role, parts=[types.Part.from_text(text=msg.get("content") or "")])


def _to_contents(messages: Iterable[dict]) -> list[types.Content]:
    """Convert the whole conversation. When Gemini asks for several tools in one
    turn, all their results must go back together in ONE user turn, in the
    order they were asked for. Sent as separate turns, Gemini 3.x rejects the
    request (400 INVALID_ARGUMENT, "corrupted thought signature")."""
    contents: list[types.Content] = []
    previous_was_function = False
    for msg in messages:
        content = _to_content(msg)
        is_function = msg["role"] == "function"
        if is_function and previous_was_function:
            contents[-1].parts.extend(content.parts)
        else:
            contents.append(content)
        previous_was_function = is_function
    return contents


def _extract_function_calls(response: Any) -> list[dict]:
    calls: list[dict] = []
    for cand in getattr(response, "candidates", None) or []:
        content = getattr(cand, "content", None)
        if not content:
            continue
        for part in getattr(content, "parts", None) or []:
            fc = getattr(part, "function_call", None)
            if fc and getattr(fc, "name", None):
                calls.append({"name": fc.name, "args": dict(fc.args or {})})
    return calls


def _extract_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if text:
        return text.strip()
    chunks = []
    for cand in getattr(response, "candidates", None) or []:
        content = getattr(cand, "content", None)
        if not content:
            continue
        for part in getattr(content, "parts", None) or []:
            piece = getattr(part, "text", None)
            if piece:
                chunks.append(piece)
    return "".join(chunks).strip()


async def chat(
    messages: Iterable[dict],
    tools: list[types.Tool] | None,
    system_instruction: str,
) -> dict:
    """Send a conversation to Gemini and return `{text, function_calls, raw}`.

    Retries once with backoff on transient (429/5xx) errors. Any auth/config
    failure surfaces as a `RuntimeError` — the router catches and returns a
    friendly reply so the widget never white-screens.
    """
    client = get_client()
    contents = _to_contents(messages)
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        tools=tools or [],
        temperature=0.2,
    )

    delay = 1.0
    for attempt in range(2):
        try:
            response = await client.aio.models.generate_content(
                model=settings.gemini_model,
                contents=contents,
                config=config,
            )
            model_content = None
            candidates = getattr(response, "candidates", None) or []
            if candidates:
                model_content = getattr(candidates[0], "content", None)
            return {
                "text": _extract_text(response),
                "function_calls": _extract_function_calls(response),
                "model_content": model_content,
                "raw": response,
            }
        except APIError as exc:
            status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
            transient = status in (429, 500, 502, 503, 504)
            if transient and attempt == 0:
                logger.warning("Gemini transient error %s, retrying in %.1fs", status, delay)
                await asyncio.sleep(delay)
                delay *= 2
                continue
            logger.exception("Gemini call failed (status=%s)", status)
            raise