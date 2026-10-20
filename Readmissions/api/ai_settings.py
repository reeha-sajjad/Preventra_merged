"""
Settings -> AI: which model serves each AI feature (api/llm.py).

    GET  /api/ai/status     on or off per feature - any signed-in user, so the
                            app hides AI controls that would only fail
    GET  /api/ai/settings   providers, features, what serves each (superadmin)
    PUT  /api/ai/settings   change them (superadmin)
    POST /api/ai/models     a provider's models, live from the provider
    POST /api/ai/test       one small call, and whether the model can use tools

Superadmin only, except /status. Keys are written, never read back: they are
stored encrypted (api/secret_box.py) and the page only learns whether one is
set and where from (the settings page or the server's environment).
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api import llm, llm_providers, secret_box

router = APIRouter(prefix="/api/ai", tags=["ai settings"])


def _superadmin(request: Request) -> dict:
    user = request.state.user
    if user.get("role") != "superadmin":
        raise HTTPException(status_code=403, detail="Only the superadmin chooses AI models")
    return user


def _public() -> dict:
    doc = llm.settings_doc()
    status = llm.status()
    providers = {}
    for name, label in llm_providers.LABELS.items():
        stored = llm._provider(doc, name)
        creds = llm.credentials(doc, name)
        providers[name] = {"label": label, "local": name in llm_providers.LOCAL,
                           "configured": creds is not None, "source": creds["source"] if creds else None,
                           "base_url": stored.get("base_url") or "",
                           "patient_data": llm.patient_data_allowed(doc, name)}
    features = {name: {"label": spec["label"], "sends_patient_data": spec["patient_data"],
                       "needs_tools": spec["tools"], **status[name],
                       "chosen": bool(((doc.get("features") or {}).get(name) or {}).get("model"))}
                for name, spec in llm.FEATURES.items()}
    return {"enabled": doc.get("enabled", True), "providers": providers, "features": features,
            "key_set": secret_box.ready(), "updated_by": doc.get("updated_by"), "updated_at": doc.get("updated_at"),
            "default_ollama_url": llm_providers.DEFAULT_OLLAMA_URL}


@router.get("/status")
def ai_status():
    return {name: {"on": s["on"]} for name, s in llm.status().items()}


@router.get("/settings")
def get_settings(request: Request):
    _superadmin(request)
    return _public()


class ProviderIn(BaseModel):
    api_key: Optional[str] = Field(default=None, max_length=500)
    clear_key: bool = False
    base_url: Optional[str] = Field(default=None, max_length=300)
    patient_data: Optional[bool] = None


class FeatureIn(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = Field(default=None, max_length=200)


class SettingsIn(BaseModel):
    enabled: Optional[bool] = None
    providers: Optional[dict[str, ProviderIn]] = None
    features: Optional[dict[str, Optional[FeatureIn]]] = None


@router.put("/settings")
def put_settings(payload: SettingsIn, request: Request):
    """Only what is sent changes. A feature sent as null goes back to the
    default (Gemini from the server's environment, if there is a key there)."""
    user = _superadmin(request)
    problems, update, unset = [], {}, {}
    if payload.enabled is not None:
        update["enabled"] = payload.enabled
    for name, p in (payload.providers or {}).items():
        if name not in llm_providers.LABELS:
            problems.append(f"providers: no provider '{name}'")
            continue
        if p.api_key:
            try:
                update[f"providers.{name}.api_key_enc"] = secret_box.encrypt(p.api_key.strip())
            except secret_box.SecretKeyMissing:
                raise HTTPException(status_code=503, detail="INGEST_SECRET_KEY is not set on the server, so a key "
                                                            "cannot be stored safely. Set it, or give the key to the "
                                                            "server as an environment variable instead")
        elif p.clear_key:
            unset[f"providers.{name}.api_key_enc"] = ""
        if p.base_url is not None:
            url = p.base_url.strip().rstrip("/")
            if url and not url.startswith(("http://", "https://")):
                problems.append(f"{llm_providers.LABELS[name]}: the address must start with http:// or https://")
            update[f"providers.{name}.base_url"] = url
        if p.patient_data is not None:
            update[f"providers.{name}.patient_data"] = p.patient_data
    for name, f in (payload.features or {}).items():
        if name not in llm.FEATURES:
            problems.append(f"features: no feature '{name}'")
        elif f is None or not (f.provider or f.model):
            unset[f"features.{name}"] = ""
        elif f.provider not in llm_providers.LABELS or not (f.model or "").strip():
            problems.append(f"{llm.FEATURES[name]['label']}: choose a provider and a model")
        else:
            update[f"features.{name}"] = {"provider": f.provider, "model": f.model.strip()}
    if problems:
        raise HTTPException(status_code=422, detail=problems)
    change = {"$set": {**update, "updated_by": user.get("email"), "updated_at": datetime.utcnow()}}
    if unset:
        change["$unset"] = unset
    llm._get_db()[llm.SETTINGS].update_one({"_id": llm.SETTINGS_ID}, change, upsert=True)
    llm.invalidate()
    return _public()


class ProbeIn(BaseModel):
    provider: str
    api_key: Optional[str] = Field(default=None, max_length=500)     # one not saved yet, to try it first
    base_url: Optional[str] = Field(default=None, max_length=300)
    model: Optional[str] = Field(default=None, max_length=200)


def _client(payload: ProbeIn):
    if payload.provider not in llm_providers.LABELS:
        raise HTTPException(status_code=422, detail=f"No provider '{payload.provider}'")
    stored = llm.credentials(llm.settings_doc(), payload.provider) or {}
    key = (payload.api_key or "").strip() or stored.get("api_key", "")
    url = (payload.base_url or "").strip() or stored.get("base_url", "")
    if payload.provider != "ollama" and not key:
        raise HTTPException(status_code=422, detail=f"{llm_providers.LABELS[payload.provider]} has no key yet")
    return llm_providers.make(payload.provider, key, url)


def _short(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc)[:300]}"


@router.post("/models")
def list_models(payload: ProbeIn, request: Request):
    _superadmin(request)
    client = _client(payload)
    try:
        return {"models": client.list_models()}
    except Exception as exc:                                             # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not list models: {_short(exc)}")


_PROBE_TOOL = {"name": "get_ward_census", "description": "Number of patients on a hospital ward.",
               "parameters": {"type": "object", "properties": {"ward": {"type": "string"}}, "required": ["ward"]}}


@router.post("/test")
def test_model(payload: ProbeIn, request: Request):
    """A one-line answer, then a question only the tool can answer: whether the
    model replies, how fast, and whether it calls tools (the chatbot needs it)."""
    _superadmin(request)
    if not payload.model:
        raise HTTPException(status_code=422, detail="Choose a model to test")
    client = _client(payload)
    started = time.monotonic()
    try:
        reply = client.complete(payload.model, "Answer in one word.", [{"role": "user", "content": "Say OK."}],
                                timeout_s=60)
    except Exception as exc:                                             # noqa: BLE001
        return {"ok": False, "error": _short(exc)}
    seconds = round(time.monotonic() - started, 1)
    try:
        call = client.complete(payload.model, "Use the tool to answer.",
                               [{"role": "user", "content": "How many patients are on ward 7B?"}],
                               tools=[_PROBE_TOOL], timeout_s=60).tool_call
        tools_ok = bool(call and call.get("name") == _PROBE_TOOL["name"])
    except Exception:                                                    # noqa: BLE001
        tools_ok = False
    return {"ok": True, "reply": reply.text[:200], "seconds": seconds, "tools_ok": tools_ok}
