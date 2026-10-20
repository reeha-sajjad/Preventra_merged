"""
The model providers behind api/llm.py, each behind the same two calls:

    complete(model, system, messages, tools, json_schema, temperature, timeout_s) -> Reply
    list_models() -> [model names]

    gemini     Google Gemini (google-genai)
    anthropic  Anthropic Claude (anthropic)
    openai     OpenAI, or any server that speaks its API (openai)
    ollama     a local model served by Ollama - its OpenAI-compatible API

`messages` are {"role": "user" | "assistant", "content": text}. `tools` are
{"name", "description", "parameters": JSON Schema}; a reply that calls one has
`tool_call` = {"name", "args"}. With `json_schema` (a JSON Schema, or True for
any JSON object) the reply's text is a JSON object.

Nothing here decides which provider or model a feature uses, or whether it may
see patient details - that is api/llm.py.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional


@dataclass
class Reply:
    text: str = ""
    tool_call: Optional[dict] = None


LABELS = {"gemini": "Google Gemini", "anthropic": "Anthropic Claude", "openai": "OpenAI",
          "ollama": "Local model (Ollama)"}
LOCAL = {"ollama"}
DEFAULT_OLLAMA_URL = "http://localhost:11434"
# Claude needs a cap on every reply. Model Studio's pipeline code is the
# longest; below ~21,000 the SDK still allows a reply without streaming.
MAX_TOKENS = 16000


class ProviderError(RuntimeError):
    """A provider call failed; the message is safe to show an admin."""


def failure_kind(exc: Exception) -> Optional[str]:
    """Why a call failed, as each SDK reports it: "busy" (rate limit, overload,
    a dropped connection - try again in a moment), "slow" (it timed out - a
    lighter model is the better next try), "missing" (no such model on this
    key), or None for a real error."""
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    name = type(exc).__name__.lower()
    text = str(exc).upper()
    if status == 404 or "NOT_FOUND" in text:
        return "missing"
    if status in (408, 504) or "timeout" in name or "DEADLINE_EXCEEDED" in text:
        return "slow"
    if status in (409, 429, 500, 502, 503, 529) \
            or any(word in name for word in ("connection", "ratelimit", "overloaded", "internalserver")) \
            or any(word in text for word in ("RESOURCE_EXHAUSTED", "UNAVAILABLE", "OVERLOADED")):
        return "busy"
    return None


def is_transient(exc: Exception) -> bool:
    """Worth trying again in a moment."""
    return failure_kind(exc) in ("busy", "slow")


class Gemini:
    def __init__(self, api_key: str, **_):
        self.api_key = api_key

    # Every client below is held in a `with` for the whole call: google-genai's
    # Client closes its connection when it is garbage-collected, so
    # `make_client().models.generate_content(...)` fails with "the client has
    # been closed" before the request is sent.
    def _client(self, timeout_s: float):
        from google import genai
        from google.genai import types
        return genai.Client(api_key=self.api_key, http_options=types.HttpOptions(
            timeout=int(timeout_s * 1000), retry_options=types.HttpRetryOptions(attempts=1)))

    def complete(self, model, system, messages, tools=None, json_schema=None, temperature=None,
                 timeout_s=30) -> Reply:
        from google.genai import types
        contents = [types.Content(role="user" if m["role"] == "user" else "model",
                                  parts=[types.Part(text=m["content"])]) for m in messages]
        config = {"system_instruction": system or None, "temperature": temperature}
        if tools:
            config["tools"] = [types.Tool(function_declarations=[types.FunctionDeclaration(
                name=t["name"], description=t.get("description", ""), parameters_json_schema=t["parameters"])
                for t in tools])]
            config["tool_config"] = types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="AUTO"))
        if json_schema is not None:
            config["response_mime_type"] = "application/json"
            if isinstance(json_schema, dict):
                config["response_json_schema"] = json_schema
        with self._client(timeout_s) as client:
            response = client.models.generate_content(
                model=model, contents=contents,
                config=types.GenerateContentConfig(**{k: v for k, v in config.items() if v is not None}))
        candidates = response.candidates or []
        for part in (candidates[0].content.parts or []) if candidates and candidates[0].content else []:
            if part.function_call:
                return Reply(tool_call={"name": part.function_call.name, "args": dict(part.function_call.args or {})})
        return Reply(text=(response.text or "").strip())

    def list_models(self) -> list:
        out = []
        with self._client(20) as client:
            for m in client.models.list():
                actions = getattr(m, "supported_actions", None) or []
                if not actions or "generateContent" in actions:
                    out.append(m.name.removeprefix("models/"))
        return sorted(out)


# ---------------------------------------------------------------- Anthropic
class Anthropic:
    def __init__(self, api_key: str, **_):
        self.api_key = api_key

    def _client(self, timeout_s: float):
        import anthropic
        return anthropic.Anthropic(api_key=self.api_key, timeout=timeout_s, max_retries=0)

    @staticmethod
    def _turns(messages: list) -> list:
        """Claude wants turns that alternate and open with the user: merge
        neighbours from the same side and drop any leading assistant turns."""
        turns = []
        for m in messages:
            role = "user" if m["role"] == "user" else "assistant"
            if turns and turns[-1]["role"] == role:
                turns[-1]["content"] += "\n\n" + m["content"]
            elif turns or role == "user":
                turns.append({"role": role, "content": m["content"]})
        return turns or [{"role": "user", "content": "."}]

    def complete(self, model, system, messages, tools=None, json_schema=None, temperature=None,
                 timeout_s=30) -> Reply:
        kwargs = {"model": model, "max_tokens": MAX_TOKENS, "messages": self._turns(messages)}
        if system:
            kwargs["system"] = system
        if temperature is not None:
            kwargs["temperature"] = temperature
        if isinstance(json_schema, dict):
            # Claude has no JSON mode: a single required tool whose input is the
            # answer gives a well-formed object every time.
            kwargs["tools"] = [{"name": "answer", "description": "Return the answer in this shape.",
                                "input_schema": json_schema}]
            kwargs["tool_choice"] = {"type": "tool", "name": "answer"}
        elif tools:
            kwargs["tools"] = [{"name": t["name"], "description": t.get("description", ""),
                                "input_schema": t["parameters"]} for t in tools]
            kwargs["tool_choice"] = {"type": "auto"}
        if json_schema is True:
            kwargs["system"] = (system + "\n\n" if system else "") + "Answer with one JSON object only."
        with self._client(timeout_s) as client:
            response = client.messages.create(**kwargs)
        text = []
        for block in response.content:
            if block.type == "tool_use":
                if isinstance(json_schema, dict):
                    return Reply(text=json.dumps(block.input))
                return Reply(tool_call={"name": block.name, "args": dict(block.input or {})})
            if block.type == "text":
                text.append(block.text)
        return Reply(text="".join(text).strip())

    def list_models(self) -> list:
        with self._client(20) as client:
            return sorted(m.id for m in client.models.list(limit=100))


# ------------------------------------------------- OpenAI and compatible ones
_NOT_CHAT = ("embedding", "whisper", "tts", "dall-e", "moderation", "davinci", "babbage", "transcribe",
             "realtime", "audio", "image", "search")


class OpenAICompatible:
    def __init__(self, api_key: str = "", base_url: str = "", local: bool = False, **_):
        self.api_key = api_key or ("ollama" if local else "")
        self.base_url = base_url or None
        self.local = local

    def _client(self, timeout_s: float):
        from openai import OpenAI
        return OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=timeout_s, max_retries=0)

    def complete(self, model, system, messages, tools=None, json_schema=None, temperature=None,
                 timeout_s=30) -> Reply:
        msgs = ([{"role": "system", "content": system}] if system else []) + \
            [{"role": "user" if m["role"] == "user" else "assistant", "content": m["content"]} for m in messages]
        kwargs = {"model": model, "messages": msgs}
        if tools:
            kwargs["tools"] = [{"type": "function", "function": {
                "name": t["name"], "description": t.get("description", ""), "parameters": t["parameters"]}}
                for t in tools]
            kwargs["tool_choice"] = "auto"
        if isinstance(json_schema, dict):
            kwargs["response_format"] = {"type": "json_schema",
                                         "json_schema": {"name": "answer", "schema": json_schema}}
        elif json_schema is True:
            kwargs["response_format"] = {"type": "json_object"}
        # OpenAI's reasoning models refuse a temperature; local models use it.
        if temperature is not None and self.local:
            kwargs["temperature"] = temperature
        with self._client(timeout_s) as client:
            response = client.chat.completions.create(**kwargs)
        message = response.choices[0].message
        if getattr(message, "tool_calls", None):
            call = message.tool_calls[0]
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            return Reply(tool_call={"name": call.function.name, "args": args})
        return Reply(text=(message.content or "").strip())

    def list_models(self) -> list:
        with self._client(20) as client:
            names = [m.id for m in client.models.list()]
        if not self.local:
            names = [n for n in names if not any(word in n for word in _NOT_CHAT)]
        return sorted(names)


def ollama_url(base_url: Optional[str]) -> str:
    """Ollama's OpenAI-compatible address: the server's own address plus /v1."""
    base = (base_url or DEFAULT_OLLAMA_URL).rstrip("/")
    return base if base.endswith("/v1") else base + "/v1"


def make(provider: str, api_key: str = "", base_url: str = ""):
    if provider == "gemini":
        return Gemini(api_key)
    if provider == "anthropic":
        return Anthropic(api_key)
    if provider == "openai":
        return OpenAICompatible(api_key, base_url)
    if provider == "ollama":
        return OpenAICompatible("", ollama_url(base_url), local=True)
    raise ProviderError(f"Unknown AI provider '{provider}'")
