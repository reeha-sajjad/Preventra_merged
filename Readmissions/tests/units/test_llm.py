"""The AI layer: which model serves each feature (api/llm.py), the superadmin's
settings (api/ai_settings.py), and each provider's request format
(api/llm_providers.py) - against stand-in SDK clients, never a real provider.

Same two hospitals and accounts as test_isolation.py.
"""
from types import SimpleNamespace

import pytest

from api import ai_settings, chatbot_llm, llm, llm_providers
from tests.units.test_isolation import A1, as_user, main, world  # noqa: F401


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for var in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OLLAMA_BASE_URL", "OPENAI_BASE_URL",
                "GEMINI_MODEL", "STUDIO_GEMINI_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("INGEST_SECRET_KEY", "test-key")
    llm.invalidate()
    yield
    llm.invalidate()


class FakeProvider:
    """Records each call and answers from a script."""
    def __init__(self, replies=None, models=("model-a", "model-b")):
        self.calls, self.replies, self.models = [], list(replies or []), list(models)

    def complete(self, model, system, messages, tools=None, json_schema=None, temperature=None, timeout_s=30):
        self.calls.append({"model": model, "system": system, "messages": messages, "tools": tools,
                           "json_schema": json_schema})
        reply = self.replies.pop(0) if self.replies else llm_providers.Reply(text="OK")
        if isinstance(reply, Exception):
            raise reply
        return reply

    def list_models(self):
        return self.models


class Held(SimpleNamespace):
    """A stand-in SDK client, used the way the adapters must use a real one:
    held open in a `with` for the whole call."""
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def use(monkeypatch, fake):
    seen = {}

    def make(provider, api_key="", base_url=""):
        seen.update(provider=provider, api_key=api_key, base_url=base_url)
        return fake
    monkeypatch.setattr(llm_providers, "make", make)
    return seen


# ------------------------------------------------------------ who serves what
def test_nothing_configured_means_every_feature_is_off(main, world):
    assert {f: s["on"] for f, s in llm.status().items()} == {f: False for f in llm.FEATURES}
    with pytest.raises(llm.LLMUnavailable, match="No AI model is chosen"):
        llm.complete("chatbot", prompt="hi")


def test_a_gemini_key_on_the_server_keeps_working_as_before(main, world, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    status = llm.status()
    assert all(s["on"] for s in status.values())
    assert status["chatbot"]["model"] == "gemini-flash-lite-latest" and status["studio"]["model"] == "gemini-flash-latest"
    seen = use(monkeypatch, FakeProvider())
    assert llm.complete("chatbot", prompt="hi").text == "OK"
    assert seen == {"provider": "gemini", "api_key": "env-key", "base_url": ""}


def test_a_cloud_provider_needs_clearing_before_it_sees_patient_details(main, world, monkeypatch):
    ops = as_user(main, world, "ops@team.test")
    r = ops.put("/api/ai/settings", json={
        "providers": {"anthropic": {"api_key": "sk-ant-secret"}},
        "features": {f: {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"} for f in llm.FEATURES}})
    assert r.status_code == 200, r.text
    features = r.json()["features"]
    assert not features["chatbot"]["on"] and "not cleared to receive patient details" in features["chatbot"]["reason"]
    assert not features["insights"]["on"] and features["studio"]["on"] and features["data"]["on"]

    ops.put("/api/ai/settings", json={"providers": {"anthropic": {"patient_data": True}}})
    seen = use(monkeypatch, FakeProvider())
    llm.complete("chatbot", prompt="hi")
    assert seen["provider"] == "anthropic" and seen["api_key"] == "sk-ant-secret"


def test_keys_are_stored_encrypted_and_never_sent_back(main, world):
    ops = as_user(main, world, "ops@team.test")
    r = ops.put("/api/ai/settings", json={"providers": {"openai": {"api_key": "sk-openai-secret"}}})
    assert "sk-openai-secret" not in r.text
    assert "sk-openai-secret" not in str(world["db"][llm.SETTINGS].find_one())
    provider = r.json()["providers"]["openai"]
    assert provider["configured"] and provider["source"] == "settings"
    r = ops.put("/api/ai/settings", json={"providers": {"openai": {"clear_key": True}}})
    assert not r.json()["providers"]["openai"]["configured"]


def test_a_key_cannot_be_saved_without_the_server_key(main, world, monkeypatch):
    monkeypatch.delenv("INGEST_SECRET_KEY")
    r = as_user(main, world, "ops@team.test").put("/api/ai/settings",
                                                   json={"providers": {"gemini": {"api_key": "k"}}})
    assert r.status_code == 503


def test_a_local_model_is_cleared_for_patient_details(main, world, monkeypatch):
    ops = as_user(main, world, "ops@team.test")
    ops.put("/api/ai/settings", json={"providers": {"ollama": {"base_url": "http://ollama:11434"}},
                                     "features": {"chatbot": {"provider": "ollama", "model": "qwen2.5:14b"}}})
    seen = use(monkeypatch, FakeProvider())
    llm.complete("chatbot", prompt="hi")
    assert seen == {"provider": "ollama", "api_key": "", "base_url": "http://ollama:11434"}


def test_switching_ai_off_turns_off_every_feature(main, world, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    ops = as_user(main, world, "ops@team.test")
    ops.put("/api/ai/settings", json={"enabled": False})
    assert ops.get("/api/ai/status").json() == {f: {"on": False} for f in llm.FEATURES}
    assert as_user(main, world, "cm@a.test").get("/api/ai/status").status_code == 200


def test_only_the_superadmin_chooses_models(main, world):
    for email in ("admin@a.test", "cm@a.test", "doc.a@a.test"):
        client = as_user(main, world, email)
        assert client.get("/api/ai/settings").status_code == 403
        assert client.put("/api/ai/settings", json={"enabled": False}).status_code == 403
    bad = as_user(main, world, "ops@team.test").put("/api/ai/settings", json={
        "providers": {"nowhere": {}}, "features": {"chatbot": {"provider": "gemini", "model": ""}}})
    assert bad.status_code == 422


def test_models_are_listed_and_a_model_is_tested(main, world, monkeypatch):
    probe = llm_providers.Reply(tool_call={"name": "get_ward_census", "args": {"ward": "7B"}})
    use(monkeypatch, FakeProvider(replies=[llm_providers.Reply(text="OK"), probe]))
    ops = as_user(main, world, "ops@team.test")
    assert ops.post("/api/ai/models", json={"provider": "openai", "api_key": "sk-try"}).json()["models"] == \
        ["model-a", "model-b"]
    result = ops.post("/api/ai/test", json={"provider": "openai", "api_key": "sk-try", "model": "model-a"}).json()
    assert result["ok"] and result["tools_ok"] and result["reply"] == "OK"
    assert ops.post("/api/ai/models", json={"provider": "anthropic"}).status_code == 422   # no key anywhere


# ------------------------------------------------------------------- calls
def test_a_busy_provider_is_tried_again_and_a_real_error_is_not(main, world, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    busy = RuntimeError("429 RESOURCE_EXHAUSTED")
    fake = FakeProvider(replies=[busy, busy, llm_providers.Reply(text="done")])
    use(monkeypatch, fake)
    notes = []
    assert llm.complete("studio", prompt="p", on_retry=notes.append).text == "done"
    assert len(fake.calls) == 3 and len(notes) == 2
    use(monkeypatch, FakeProvider(replies=[ValueError("bad request")]))
    with pytest.raises(ValueError):
        llm.complete("studio", prompt="p")


def test_the_chatbot_picks_its_function_through_whichever_model(main, world, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    call = llm_providers.Reply(tool_call={"name": "count_patients_by_risk_band", "args": {"risk_band": "high"}})
    fake = FakeProvider(replies=[call, llm_providers.Reply(text="NONE")])
    use(monkeypatch, fake)
    assert chatbot_llm.select_function_call("how many high risk?") == call.tool_call
    tools = fake.calls[0]["tools"]
    assert len(tools) == len(chatbot_llm.FUNCTION_DECLARATIONS)
    band = next(t for t in tools if t["name"] == "count_patients_by_risk_band")
    assert band["parameters"]["type"] == "object"
    assert band["parameters"]["properties"]["risk_band"] == {
        "type": "string", "enum": ["low", "medium", "high"],
        "description": "Optional. Restrict the count to a single risk band."}
    assert chatbot_llm.select_function_call("tell me a joke") is None


def test_cost_figures_come_back_with_ai_off(main, world):
    ops = as_user(main, world, "ops@team.test")
    r = ops.post("/api/ai-insights", json={"patient_id": A1, "risk_score": 55, "risk_band": "High"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ai"] is False and body["counterfactual_explanation"] == []
    assert body["estimated_readmission_cost_usd"] > 0 and "net_roi_usd" in body


def test_genai_schemas_become_plain_json_schema():
    from google.genai import types
    schema = types.Schema(type=types.Type.OBJECT, properties={
        "n": types.Schema(type=types.Type.INTEGER, nullable=True),
        "tags": types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING))})
    assert llm.json_schema(schema) == {"type": "object", "properties": {
        "n": {"type": ["integer", "null"]}, "tags": {"type": "array", "items": {"type": "string"}}}}


# ------------------------------------------------------- provider formats
def test_claude_gets_alternating_turns_and_json_through_a_required_tool(monkeypatch):
    sent = {}

    class Messages:
        def create(self, **kwargs):
            sent.update(kwargs)
            return SimpleNamespace(content=[SimpleNamespace(type="tool_use", name="answer", input={"x": 1})])
    claude = llm_providers.Anthropic("key")
    monkeypatch.setattr(claude, "_client", lambda timeout_s: Held(messages=Messages()))
    turns = [{"role": "assistant", "content": "hello"}, {"role": "user", "content": "a"},
             {"role": "user", "content": "b"}, {"role": "assistant", "content": "c"}]
    reply = claude.complete("claude-sonnet-5-5", "sys", turns, json_schema={"type": "object"})
    assert reply.text == '{"x": 1}'
    assert sent["messages"] == [{"role": "user", "content": "a\n\nb"}, {"role": "assistant", "content": "c"}]
    assert sent["tool_choice"] == {"type": "tool", "name": "answer"} and sent["system"] == "sys"


def test_claude_is_never_sent_a_temperature(monkeypatch):
    """Model Studio asks for 0.2; current Claude models refuse any temperature
    with a 400, which sent every studio run to the name/type rules."""
    sent = {}

    class Messages:
        def create(self, **kwargs):
            sent.update(kwargs)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text='{"a": 1}')])
    claude = llm_providers.Anthropic("key")
    monkeypatch.setattr(claude, "_client", lambda timeout_s: Held(messages=Messages()))
    reply = claude.complete("claude-sonnet-5-5", "sys", [{"role": "user", "content": "q"}],
                            json_schema=True, temperature=0.2)
    assert reply.text == '{"a": 1}' and "temperature" not in sent


def test_openai_and_ollama_tool_calls_and_addresses(monkeypatch):
    sent = {}
    call = SimpleNamespace(function=SimpleNamespace(name="count", arguments='{"band": "high"}'))

    class Completions:
        def create(self, **kwargs):
            sent.update(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[call], content=None))])
    fake = Held(chat=SimpleNamespace(completions=Completions()))
    local = llm_providers.make("ollama", base_url="http://ollama:11434")
    assert local.base_url == "http://ollama:11434/v1" and local.local
    monkeypatch.setattr(local, "_client", lambda timeout_s: fake)
    reply = local.complete("qwen2.5:14b", "sys", [{"role": "user", "content": "q"}],
                           tools=[{"name": "count", "description": "d", "parameters": {"type": "object"}}],
                           temperature=0.2)
    assert reply.tool_call == {"name": "count", "args": {"band": "high"}}
    assert sent["messages"][0] == {"role": "system", "content": "sys"} and sent["temperature"] == 0.2
    cloud = llm_providers.make("openai", api_key="k")
    monkeypatch.setattr(cloud, "_client", lambda timeout_s: fake)
    sent.clear()
    cloud.complete("gpt-x", "", [{"role": "user", "content": "q"}], temperature=0.2, json_schema=True)
    assert "temperature" not in sent and sent["response_format"] == {"type": "json_object"}


def test_gemini_gets_its_tools_as_json_schema(monkeypatch):
    from google.genai import types
    sent = {}

    class Models:
        def generate_content(self, model, contents, config):
            sent.update(model=model, contents=contents, config=config)
            part = types.Part(function_call=types.FunctionCall(name="count", args={"band": "high"}))
            return SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))], text=None)
    gemini = llm_providers.Gemini("key")
    monkeypatch.setattr(gemini, "_client", lambda timeout_s: Held(models=Models()))
    reply = gemini.complete("gemini-flash-latest", "sys", [{"role": "assistant", "content": "a"},
                                                            {"role": "user", "content": "q"}],
                            tools=[{"name": "count", "description": "d", "parameters": {"type": "object"}}])
    assert reply.tool_call == {"name": "count", "args": {"band": "high"}}
    assert [c.role for c in sent["contents"]] == ["model", "user"]
    assert sent["config"].tools[0].function_declarations[0].parameters_json_schema == {"type": "object"}


def test_busy_errors_are_recognised_whatever_the_sdk():
    assert llm_providers.is_transient(SimpleNamespace(status_code=529) and type("E", (Exception,), {"status_code": 529})())
    assert llm_providers.is_transient(RuntimeError("503 UNAVAILABLE"))
    assert llm_providers.is_transient(type("APITimeoutError", (Exception,), {})())
    assert not llm_providers.is_transient(ValueError("invalid model"))


def test_a_gemini_client_stays_open_for_the_whole_call(monkeypatch):
    """google-genai's Client closes its connection when it is garbage-collected.
    An adapter that drops the client before the request ("make().models.
    generate_content(...)") failed every Gemini call with "the client has been
    closed" - and Model Studio quietly fell back to its name rules."""
    import gc
    from google import genai

    class Connection:
        closed = False

    class Models:
        def __init__(self, connection):
            self.connection = connection

        def generate_content(self, **_):
            gc.collect()
            if self.connection.closed:
                raise RuntimeError("Cannot send a request, as the client has been closed.")
            return SimpleNamespace(candidates=[], text='{"ok": true}')

        def list(self):
            gc.collect()
            if self.connection.closed:
                raise RuntimeError("Cannot send a request, as the client has been closed.")
            return [SimpleNamespace(name="models/gemini-flash-latest", supported_actions=["generateContent"])]

    class Client:                                  # shaped like google.genai.Client
        def __init__(self, **_):
            self._connection = Connection()
            self.models = Models(self._connection)

        def close(self):
            self._connection.closed = True

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()

        def __del__(self):
            self.close()
    monkeypatch.setattr(genai, "Client", Client)
    gemini = llm_providers.Gemini("key")
    assert gemini.complete("gemini-flash-latest", "", [{"role": "user", "content": "q"}], json_schema=True).text \
        == '{"ok": true}'
    assert gemini.list_models() == ["gemini-flash-latest"]


def test_a_slow_or_missing_studio_model_hands_over_to_the_lighter_one(main, world, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    slow = type("ServerError", (Exception,), {"code": 504})("504 DEADLINE_EXCEEDED")
    fake = FakeProvider([slow, llm_providers.Reply(text="{}")])
    use(monkeypatch, fake)
    said = []
    assert llm.complete("studio", prompt="plan", on_retry=said.append).text == "{}"
    assert [c["model"] for c in fake.calls] == ["gemini-flash-latest", "gemini-flash-lite-latest"]
    assert "did not answer in time; trying gemini-flash-lite-latest" in said[0]

    missing = type("ClientError", (Exception,), {"code": 404})("404 NOT_FOUND")
    fake = FakeProvider([missing, llm_providers.Reply(text="{}")])
    use(monkeypatch, fake)
    assert llm.complete("studio", prompt="read").text == "{}"
    assert [c["model"] for c in fake.calls] == ["gemini-flash-latest", "gemini-flash-lite-latest"]


def test_busy_models_are_retried_and_only_gemini_has_a_fallback(main, world, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    busy = type("ServerError", (Exception,), {"code": 503})("503 UNAVAILABLE")
    fake = FakeProvider([busy, busy, llm_providers.Reply(text="{}")])
    use(monkeypatch, fake)
    assert llm.complete("studio", prompt="plan", retries=3).text == "{}"
    assert [c["model"] for c in fake.calls] == ["gemini-flash-latest"] * 3      # busy: same model, again
    fake = FakeProvider([ValueError("bad request")])
    use(monkeypatch, fake)
    with pytest.raises(ValueError):
        llm.complete("studio", prompt="plan")
    assert len(fake.calls) == 1                                                 # a real error is raised at once
    assert llm._fallback("studio", "anthropic", "claude-x") is None
    assert llm._fallback("chatbot", "gemini", "gemini-flash-lite-latest") is None
