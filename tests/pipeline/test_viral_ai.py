"""Host tests for the provider-neutral viral-detection client.

No network: transport is injected or ``_post_json`` is monkeypatched. Pins the
model-routing rules (Claude → /messages, everything else → /chat/completions),
the fallback/retry policy, and the redaction of credentials from error text.
"""
from types import SimpleNamespace

import pytest

import clippyme.pipeline.viral_ai as v


def test_base_url_default_and_trailing_slash():
    assert v.normalize_base_url("") == v.DEFAULT_BASE_URL
    assert v.normalize_base_url("https://x.test/v1/") == "https://x.test/v1"
    assert v._url("https://x.test/v1/", "models") == "https://x.test/v1/models"


def test_endpoint_routing():
    assert v.endpoint_for_model("claude-sonnet-5") == "messages"
    assert v.endpoint_for_model("gpt-6-sol") == "chat"
    # The live catalog is authoritative when it lists a route.
    assert v.endpoint_for_model("weird", ["/messages"]) == "messages"
    assert v.endpoint_for_model("weird", ["/messages", "/chat/completions"]) == "chat"


def test_payload_shapes():
    openai = v.build_openai_payload("hi", "gpt-6-sol")
    assert openai["messages"] == [{"role": "user", "content": "hi"}]
    assert openai["reasoning_effort"] == "low"
    anthropic = v.build_anthropic_payload("hi", "claude-sonnet-5")
    assert anthropic["max_tokens"] > 0
    assert anthropic["messages"] == [{"role": "user", "content": "hi"}]


def test_extract_openai_text_and_usage():
    data = {
        "choices": [{"message": {"content": [{"type": "text", "text": "a"}, {"text": "b"}]}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 7},
    }
    out = v.extract_openai_response(data)
    assert out["text"] == "ab"
    assert out["input_tokens"] == 12 and out["output_tokens"] == 7


def test_extract_anthropic_text_and_usage():
    data = {
        "content": [{"type": "text", "text": "hello"}, {"type": "tool_use"}],
        "usage": {"input_tokens": 3, "output_tokens": 4},
    }
    out = v.extract_anthropic_response(data)
    assert out["text"] == "hello"
    assert out["input_tokens"] == 3 and out["output_tokens"] == 4


def test_extract_openai_without_choices_raises():
    with pytest.raises(v.ViralAIError):
        v.extract_openai_response({})


def _resp(status=200, payload=None, text=""):
    return SimpleNamespace(status_code=status, json=lambda: payload, text=text)


def test_list_models_missing_key_short_circuits():
    def boom(*args, **kwargs):
        raise AssertionError("must not call the network without a key")

    assert v.list_available_models(None, get_fn=boom) == {
        "models": [], "error": "API Key missing",
    }


def test_list_models_shapes_catalog_and_routes():
    catalog = {
        "data": [
            {"id": "claude-sonnet-5", "name": "Claude Sonnet 5",
             "supported_endpoints": ["/messages"], "context_length": 1_000_000},
            {"id": "gpt-6-sol", "name": "GPT-6 Sol",
             "supported_endpoints": ["/chat/completions", "/responses"]},
            {"id": "", "name": "ignored"},
        ]
    }
    out = v.list_available_models("k-test-key", get_fn=lambda *a, **k: _resp(payload=catalog))
    assert [m["name"] for m in out["models"]] == ["claude-sonnet-5", "gpt-6-sol"]
    assert out["models"][0]["endpoint"] == "messages"
    assert out["models"][1]["endpoint"] == "chat"


def test_list_models_error_redacts_key():
    key = "cmd_secret_value_123"
    out = v.list_available_models(
        key, get_fn=lambda *a, **k: _resp(status=401, text=f"bad key {key}")
    )
    assert out["models"] == []
    assert key not in out["error"]


def test_generate_text_returns_first_success():
    calls = []

    def fake_generate(prompt, model, *, api_key, base_url, timeout):
        calls.append((prompt, model, base_url))
        return {"text": "ok", "model": model, "input_tokens": 1,
                "output_tokens": 2, "endpoint": "chat"}

    out = v.generate_text("prompt", api_key="k", base_url="https://x/v1/",
                          models=["a"], generate_fn=fake_generate)
    assert out["text"] == "ok"
    assert calls == [("prompt", "a", "https://x/v1")]


def test_generate_text_moves_to_next_model_on_rate_limit():
    seen = []

    def fake_generate(prompt, model, *, api_key, base_url, timeout):
        seen.append(model)
        if model == "first":
            raise v.ViralAIError("429 quota", status_code=429)
        return {"text": "ok", "model": model, "input_tokens": 0,
                "output_tokens": 0, "endpoint": "chat"}

    out = v.generate_text("p", api_key="k", models=["first", "second"],
                          generate_fn=fake_generate, log_fn=lambda *_: None)
    assert out["model"] == "second"
    assert seen == ["first", "second"]


def test_generate_text_retries_transient_then_succeeds():
    attempts = {"n": 0}

    def fake_generate(prompt, model, *, api_key, base_url, timeout):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise v.ViralAIError("503 unavailable", status_code=503)
        return {"text": "ok", "model": model, "input_tokens": 0,
                "output_tokens": 0, "endpoint": "chat"}

    waited = []
    out = v.generate_text("p", api_key="k", models=["m"], generate_fn=fake_generate,
                          sleep_fn=waited.append, log_fn=lambda *_: None)
    assert out["text"] == "ok"
    assert attempts["n"] == 2
    assert waited == [2]


def test_generate_text_raises_auth_immediately():
    def fake_generate(*args, **kwargs):
        raise v.ViralAIError("401 bad key sk-abcdefgh", status_code=401)

    with pytest.raises(v.ViralAIError) as err:
        v.generate_text("p", api_key="sk-abcdefgh", models=["a", "b"],
                        generate_fn=fake_generate, log_fn=lambda *_: None)
    assert err.value.status_code == 401
    assert "sk-abcdefgh" not in str(err.value)


def test_generate_text_skips_unavailable_model():
    def fake_generate(prompt, model, *, api_key, base_url, timeout):
        if model == "missing":
            raise v.ViralAIError("400 unsupported_model", status_code=400)
        return {"text": "ok", "model": model, "input_tokens": 0,
                "output_tokens": 0, "endpoint": "chat"}

    out = v.generate_text("p", api_key="k", models=["missing", "good"],
                          generate_fn=fake_generate, log_fn=lambda *_: None)
    assert out["model"] == "good"


def test_generate_once_routes_by_model_and_retries_empty_reasoning(monkeypatch):
    calls = []

    def fake_post(url, payload, api_key, timeout):
        calls.append((url, payload))
        if url.endswith("/messages"):
            return {"content": [{"type": "text", "text": "claude text"}],
                    "usage": {"input_tokens": 1, "output_tokens": 2}}
        if len(calls) == 1:
            return {"choices": [{"message": {"content": "", "reasoning_content": "thinking"}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 4}}
        return {"choices": [{"message": {"content": "openai text"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 6}}

    monkeypatch.setattr(v, "_post_json", fake_post)

    claude = v._generate_once("p", "claude-sonnet-5", api_key="k",
                              base_url="https://x/v1", timeout=1)
    assert claude["text"] == "claude text"
    assert calls[-1][0] == "https://x/v1/messages"

    calls.clear()
    openai = v._generate_once("p", "gpt-6-sol", api_key="k",
                              base_url="https://x/v1", timeout=1)
    assert openai["text"] == "openai text"
    # Second call disables chain-of-thought.
    assert calls[1][1]["thinking"] == {"type": "disabled"}


def test_reasoning_effort_is_configurable(monkeypatch):
    monkeypatch.setenv("VIRAL_REASONING_EFFORT", "off")
    assert v.build_openai_payload("hi", "z-ai/glm-5.3-flash")["reasoning_effort"] == "off"
    monkeypatch.setenv("VIRAL_REASONING_EFFORT", "")
    assert "reasoning_effort" not in v.build_openai_payload("hi", "z-ai/glm-5.3-flash")


def test_build_model_chain_dedupes_and_drops_blanks():
    assert v.build_model_chain("a", " b , a,,c ") == ["a", "b", "c"]


def test_generate_once_retries_400_with_minimal_payload(monkeypatch):
    calls = []

    def fake_post(url, payload, api_key, timeout):
        calls.append(payload)
        if len(calls) == 1:
            raise v.ViralAIError("400 unsupported parameter", status_code=400)
        return {"choices": [{"message": {"content": "ok"}}], "usage": {}}

    monkeypatch.setattr(v, "_post_json", fake_post)
    out = v._generate_once("prompt", "gpt-6-sol", api_key="k",
                           base_url="https://x/v1", timeout=1)
    assert out["text"] == "ok"
    assert "temperature" in calls[0]
    assert calls[1] == {"model": "gpt-6-sol",
                        "messages": [{"role": "user", "content": "prompt"}]}


def test_generate_text_skips_model_specific_403_but_not_plan_403():
    def fake_generate(prompt, model, *, api_key, base_url, timeout):
        if model == "gated":
            raise v.ViralAIError("403 model not in plan", status_code=403,
                                 body="permission_error: model not in plan")
        return {"text": "ok", "model": model, "input_tokens": 0,
                "output_tokens": 0, "endpoint": "chat"}

    out = v.generate_text("p", api_key="k", models=["gated", "good"],
                          generate_fn=fake_generate, log_fn=lambda *_: None)
    assert out["model"] == "good"

    def upgrade_required(*args, **kwargs):
        raise v.ViralAIError("403 upgrade", status_code=403, body="upgrade_required")

    with pytest.raises(v.ViralAIError) as err:
        v.generate_text("p", api_key="k", models=["a", "b"],
                        generate_fn=upgrade_required, log_fn=lambda *_: None)
    assert err.value.status_code == 403


def test_generate_once_retries_truncated_reasoning_output(monkeypatch):
    calls = []

    def fake_post(url, payload, api_key, timeout):
        calls.append(payload)
        if len(calls) == 1:
            return {
                "choices": [{
                    "message": {"content": '### JSON ###\n{"candidates": [',
                                "reasoning_content": "very long chain of thought"},
                    "finish_reason": "length",
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": 8192},
            }
        return {
            "choices": [{"message": {"content": '{"candidates": []}'},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 50},
        }

    monkeypatch.setattr(v, "_post_json", fake_post)
    out = v._generate_once("prompt", "z-ai/glm-5.3-flash", api_key="k",
                           base_url="https://x/v1", timeout=1)
    assert out["text"] == '{"candidates": []}'
    assert calls[1]["thinking"] == {"type": "disabled"}
