"""
The one way the product calls a language model, whichever the superadmin chose.

Each AI feature asks for a model by what it is, not by provider:

    chatbot    the chat assistant: picks a predefined query, then phrases the
               result. Sees the caller's patients' details.
    insights   the explanations on a patient's page (cost return and "what
               would lower this risk"; each week's change). Sees one patient's
               scores, risk factors and dates.
    studio     Model Studio's plan, pipeline code and verdict wording. Sees
               column summaries only, never a patient row.
    data       reading uploaded files on the Data page. Column summaries only.

Which provider and model serves each one is set on Settings -> AI by the
superadmin (api/ai_settings.py, stored in the `ai_settings` collection). With
nothing set there, a GEMINI_API_KEY in the server's environment serves every
feature as it always has, so an existing deployment keeps working.

Two switches keep patient details where they belong:
  - AI off: every feature is unavailable, and the app hides its AI controls.
  - a provider must be cleared to receive patient details before the chatbot
    or the patient-page insights use it. A local model (Ollama) is cleared by
    default, since nothing leaves the server; a cloud provider is cleared by
    the superadmin, once the hospital has a data agreement with it.

Providers and their SDKs are in api/llm_providers.py.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Optional

from api import llm_providers, secret_box
from api.llm_providers import LABELS, LOCAL, Reply  # noqa: F401  (Reply is part of this module's interface)

SETTINGS = "ai_settings"
SETTINGS_ID = "global"
CACHE_SECONDS = 30

FEATURES = {
    "chatbot": {"label": "Chatbot", "patient_data": True, "tools": True, "timeout_s": 15,
                "env_model": ("GEMINI_MODEL", "gemini-flash-lite-latest")},
    "insights": {"label": "Explanations on patient pages", "patient_data": True, "tools": False, "timeout_s": 45,
                 "env_model": ("GEMINI_MODEL", "gemini-flash-lite-latest")},
    "studio": {"label": "Model Studio", "patient_data": False, "tools": False,
               "timeout_s": int(os.environ.get("STUDIO_GEMINI_TIMEOUT_MS", "180000")) / 1000,
               "env_model": ("STUDIO_GEMINI_MODEL", "gemini-flash-latest"),
               "fallback": ("GEMINI_MODEL", "gemini-flash-lite-latest")},
    # The Data page waits for this one while the administrator watches, so the
    # quick model reads files by default; the rules have read them already.
    "data": {"label": "Reading uploaded files", "patient_data": False, "tools": False, "timeout_s": 60,
             "env_model": ("GEMINI_MODEL", "gemini-flash-lite-latest")},
}
ENV_KEYS = {"gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}
ENV_URLS = {"openai": "OPENAI_BASE_URL", "ollama": "OLLAMA_BASE_URL"}


class LLMUnavailable(RuntimeError):
    """No model may serve this feature now; the message says why."""


# ---------------------------------------------------------------- settings
_get_db = None
_cache: dict = {"at": 0.0, "doc": None}


def bind(get_db) -> None:
    """api/main.py binds its database getter, as for the ingest API."""
    global _get_db
    _get_db = get_db
    invalidate()


def invalidate() -> None:
    _cache.update(at=0.0, doc=None)


def settings_doc() -> dict:
    """The stored settings, read at most every CACHE_SECONDS per process. An
    unreachable database means none stored, not a crash."""
    if _get_db is None:
        return {}
    if _cache["doc"] is not None and time.monotonic() - _cache["at"] < CACHE_SECONDS:
        return _cache["doc"]
    try:
        doc = _get_db()[SETTINGS].find_one({"_id": SETTINGS_ID}) or {}
    except Exception:                                                   # noqa: BLE001
        doc = {}
    _cache.update(at=time.monotonic(), doc=doc)
    return doc


def _provider(doc: dict, provider: str) -> dict:
    return (doc.get("providers") or {}).get(provider) or {}


def credentials(doc: dict, provider: str) -> Optional[dict]:
    """The key (or, for Ollama, the address) a provider is reached with - the
    one entered on the settings page, else the server's environment - and
    where it came from. None when there is none."""
    p = _provider(doc, provider)
    if provider == "ollama":
        url = p.get("base_url") or os.environ.get(ENV_URLS["ollama"], "")
        return {"api_key": "", "base_url": url, "source": "settings" if p.get("base_url") else "environment"} \
            if url else None
    key, source = "", None
    if p.get("api_key_enc"):
        try:
            key, source = secret_box.decrypt(p["api_key_enc"]), "settings"
        except (secret_box.SecretKeyMissing, secret_box.SecretUnreadable):
            key = ""
    if not key and os.environ.get(ENV_KEYS.get(provider, ""), ""):
        key, source = os.environ[ENV_KEYS[provider]], "environment"
    if not key:
        return None
    return {"api_key": key, "base_url": p.get("base_url") or os.environ.get(ENV_URLS.get(provider, ""), ""),
            "source": source}


def patient_data_allowed(doc: dict, provider: str) -> bool:
    """May this provider be sent patient details? As the superadmin set it;
    otherwise yes for a local model, and yes for a Gemini key that was already
    in the server's environment before this setting existed (so an existing
    deployment keeps working); no for any other cloud provider."""
    p = _provider(doc, provider)
    if "patient_data" in p:
        return bool(p["patient_data"])
    if provider in LOCAL:
        return True
    return provider == "gemini" and not p.get("api_key_enc") and bool(os.environ.get("GEMINI_API_KEY"))


def choice(doc: dict, feature: str) -> Optional[tuple]:
    """(provider, model) for a feature: the superadmin's, else Gemini from the
    environment, else None."""
    picked = (doc.get("features") or {}).get(feature) or {}
    if picked.get("provider") and picked.get("model"):
        return picked["provider"], picked["model"]
    if os.environ.get("GEMINI_API_KEY"):
        var, default = FEATURES[feature]["env_model"]
        return "gemini", os.environ.get(var, default)
    return None


def resolve(feature: str) -> tuple:
    """({provider, model, api_key, base_url, source}, "") when the feature may
    run now, else (None, why not)."""
    doc = settings_doc()
    if doc.get("enabled") is False:
        return None, "AI is switched off"
    picked = choice(doc, feature)
    if not picked:
        return None, "No AI model is chosen"
    provider, model = picked
    creds = credentials(doc, provider)
    if not creds:
        return None, (f"{LABELS.get(provider, provider)} has no address" if provider == "ollama"
                      else f"{LABELS.get(provider, provider)} has no key")
    if FEATURES[feature]["patient_data"] and not patient_data_allowed(doc, provider):
        return None, f"{LABELS.get(provider, provider)} is not cleared to receive patient details"
    return {"provider": provider, "model": model, "fallback": _fallback(feature, provider, model), **creds}, ""


def _fallback(feature: str, provider: str, model: str) -> Optional[str]:
    """The lighter Gemini model a feature turns to when its own is slow,
    overloaded or not on the key - as Model Studio always has. Gemini only:
    other providers' model names are the superadmin's to choose."""
    if provider != "gemini" or "fallback" not in FEATURES[feature]:
        return None
    var, default = FEATURES[feature]["fallback"]
    other = os.environ.get(var, default)
    return other if other and other != model else None


def available(feature: str) -> bool:
    return resolve(feature)[0] is not None


def status() -> dict:
    """Per feature: on or off, why, and what serves it."""
    out = {}
    for feature in FEATURES:
        cfg, why = resolve(feature)
        picked = choice(settings_doc(), feature)
        out[feature] = {"on": cfg is not None, "reason": why,
                        "provider": picked[0] if picked else None, "model": picked[1] if picked else None}
    return out


# ------------------------------------------------------------------- calls
def complete(feature: str, *, system: str = "", messages: Optional[list] = None, prompt: Optional[str] = None,
             tools: Optional[list] = None, json_schema=None, temperature: Optional[float] = None,
             retries: int = 2, on_retry=None) -> Reply:
    """One model call for a feature, with the model its settings choose.
    Rate limits and overloads are tried again (up to `retries` times, waiting
    3, 8 then 15 seconds). A model that stays busy, times out or is not on the
    key hands over to the feature's fallback model, when it has one; anything
    else is raised. LLMUnavailable when the feature may not run at all."""
    cfg, why = resolve(feature)
    if cfg is None:
        raise LLMUnavailable(why)
    client = llm_providers.make(cfg["provider"], cfg.get("api_key", ""), cfg.get("base_url", ""))
    msgs = messages if messages is not None else [{"role": "user", "content": prompt or ""}]
    waits = (0, 3, 8, 15)[:retries + 1]
    models = [cfg["model"]] + ([cfg["fallback"]] if cfg.get("fallback") else [])
    label = LABELS.get(cfg["provider"], cfg["provider"])
    for i, model in enumerate(models):
        after = models[i + 1] if i + 1 < len(models) else None
        for attempt, wait in enumerate(waits):
            if wait:
                time.sleep(wait)
            try:
                return client.complete(model, system, msgs, tools=tools, json_schema=json_schema,
                                       temperature=temperature, timeout_s=FEATURES[feature]["timeout_s"])
            except Exception as exc:                                     # noqa: BLE001
                kind = llm_providers.failure_kind(exc)
                if kind is None:
                    raise
                # A timeout or a missing model is not worth repeating when a
                # lighter model is there to try; a busy one gets its retries.
                if after and (kind in ("slow", "missing") or attempt == len(waits) - 1):
                    if on_retry:
                        on_retry(f"{label} ({model}) {'is not available' if kind == 'missing' else 'did not answer in time' if kind == 'slow' else 'is busy'}; "
                                 f"trying {after}.")
                    break
                if kind == "missing" or attempt == len(waits) - 1:
                    raise
                if on_retry:
                    on_retry(f"{label} ({model}) is busy; retrying.")
    raise LLMUnavailable("no attempt was made")                          # unreachable


def parse_json(text: str):
    """A JSON reply, tolerating a ```json fence around it."""
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    return json.loads(fenced.group(1) if fenced else text)


def complete_json(feature: str, *, schema=True, **kwargs):
    """complete() whose reply is a JSON object, parsed."""
    return parse_json(complete(feature, json_schema=schema, **kwargs).text)


def json_schema(schema) -> dict:
    """A google-genai types.Schema (or its dict) as plain JSON Schema: lower-case
    types, `nullable` as a null type. The chatbot's tools are declared in the
    genai types; every provider takes this form."""
    data = schema.model_dump(exclude_none=True, mode="json") if hasattr(schema, "model_dump") else dict(schema)

    def fix(node):
        if isinstance(node, list):
            return [fix(n) for n in node]
        if not isinstance(node, dict):
            return node
        out = {k: fix(v) for k, v in node.items() if k not in ("nullable", "property_ordering")}
        if isinstance(out.get("type"), str):
            out["type"] = out["type"].lower()
            if node.get("nullable"):
                out["type"] = [out["type"], "null"]
        return out
    return fix(data)


def tool_spec(declaration) -> dict:
    """A google-genai FunctionDeclaration as {"name", "description", "parameters"}."""
    return {"name": declaration.name, "description": declaration.description or "",
            "parameters": json_schema(declaration.parameters) if declaration.parameters
            else {"type": "object", "properties": {}}}
