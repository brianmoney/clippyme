"""OpenAI/Anthropic-compatible client for viral-moment detection.

Command Code's Provider API exposes one catalog behind three shapes: OpenAI
``/chat/completions`` (GPT + open models), Anthropic ``/messages`` (Claude), and
a models list that reports which route serves each id. This module owns the
provider-neutral request/response shaping, model discovery and the retry/fallback
loop; ``pipeline.main`` still owns the prompt and the JSON parser, so both
providers feed the exact same contract.

Transport is ``requests`` (already a runtime dependency for AI captions) and is
imported lazily so this module stays host-testable. No SDK, no global state.
"""
from __future__ import annotations

import os
import re
import time

DEFAULT_BASE_URL = "https://api.commandcode.ai/provider/v1"

# Used only when neither env nor Settings picks a model. Claude Sonnet is the
# best general-purpose selector in the catalog; any id from the live list works.
DEFAULT_MODEL = "claude-sonnet-5"

DEFAULT_MAX_TOKENS = 16384
LIST_MODELS_TIMEOUT_S = 20.0
REQUEST_TIMEOUT_S = 240.0

# Provider error bodies can echo credentials back. Redact the configured key and
# common token shapes before the message reaches logs or the Settings UI.
_COMMON_KEY_RE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{8,}|AIza[0-9A-Za-z_\-]{20,}|cmd_[A-Za-z0-9_\-]{8,})"
)


class ViralAIError(RuntimeError):
    """One provider call failed. ``status_code`` drives retry/skip decisions."""

    def __init__(self, message: str, *, status_code: int | None = None, body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def normalize_base_url(base_url: str | None) -> str:
    """Return a base URL without a trailing slash (or the provider default)."""
    return (base_url or "").strip().rstrip("/") or DEFAULT_BASE_URL


def _url(base_url: str | None, path: str) -> str:
    return f"{normalize_base_url(base_url)}/{path.lstrip('/')}"


def is_anthropic_model(model: str | None) -> bool:
    """Claude ids answer only on ``/messages`` in the Command Code catalog."""
    return (model or "").strip().lower().startswith("claude")


def endpoint_for_model(model: str | None, supported_endpoints=None) -> str:
    """Pick ``messages`` or ``chat`` for a catalog entry.

    ``supported_endpoints`` comes from ``/v1/models`` and is authoritative when
    present; otherwise fall back to the id convention. ``chat/completions`` is
    preferred when both are offered (widest compatibility, no Anthropic-only
    request fields).
    """
    endpoints = supported_endpoints or []
    if "/chat/completions" in endpoints:
        return "chat"
    if "/messages" in endpoints:
        return "messages"
    return "messages" if is_anthropic_model(model) else "chat"


def _redact(text: str, api_key: str | None = None) -> str:
    text = str(text or "")
    if api_key and len(api_key) >= 6:
        text = text.replace(api_key, "***REDACTED***")
    return _COMMON_KEY_RE.sub("***REDACTED***", text)


def build_model_chain(primary_model: str, fallback_models: str | None = None) -> list[str]:
    """De-duplicated primary → fallback model list (empty entries dropped)."""
    primary = (primary_model or "").strip()
    raw = fallback_models or ""
    models = [primary, *(part.strip() for part in str(raw).split(","))]
    return list(dict.fromkeys(model for model in models if model))


def _reasoning_effort() -> str:
    """Provider-level thinking budget for OpenAI-style chat models.

    CommandCode accepts off|low|medium|high|xhigh|max. GLM-5.3 Flash can spend
    its entire output budget thinking and return no content (or time out with a
    provider 524) on large discovery/review prompts; "low" keeps reasoning
    bounded while still producing the JSON. Unknown/unsupported values are
    dropped automatically by the 400 retry in ``_generate_once``.
    """
    return str(os.getenv("VIRAL_REASONING_EFFORT", "low") or "").strip()


def build_openai_payload(prompt: str, model: str, reasoning_effort: str | None = None) -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": DEFAULT_MAX_TOKENS,
    }
    effort = reasoning_effort if reasoning_effort is not None else _reasoning_effort()
    if effort:
        payload["reasoning_effort"] = effort
    return payload


def build_anthropic_payload(prompt: str, model: str) -> dict:
    # Recent Claude models reject `temperature` ("deprecated for this model"),
    # so leave sampling at the provider default.
    return {
        "model": model,
        "max_tokens": DEFAULT_MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    }


def _content_text(content) -> str:
    """Flatten OpenAI's string-or-parts content into plain text."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                if isinstance(part.get("text"), str):
                    parts.append(part["text"])
                elif isinstance(part.get("content"), str):
                    parts.append(part["content"])
            elif isinstance(part, str):
                parts.append(part)
        return "".join(parts).strip()
    return ""


def extract_openai_response(data: dict) -> dict:
    choices = data.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        raise ViralAIError("provider response had no choices", status_code=502)
    message = choices[0].get("message") or {}
    usage = data.get("usage") or {}
    return {
        "text": _content_text(message.get("content")),
        "reasoning": _content_text(
            message.get("reasoning_content") or message.get("reasoning")
        ),
        "finish_reason": choices[0].get("finish_reason") or "",
        "input_tokens": int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
        "output_tokens": int(
            usage.get("completion_tokens") or usage.get("output_tokens") or 0
        ),
    }


def extract_anthropic_response(data: dict) -> dict:
    blocks = data.get("content") or []
    texts = [
        block.get("text", "")
        for block in blocks
        if isinstance(block, dict) and block.get("type", "text") == "text"
    ]
    usage = data.get("usage") or {}
    return {
        "text": "".join(texts).strip(),
        "reasoning": "",
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
    }


def _post_json(url: str, payload: dict, api_key: str, timeout: float) -> dict:
    import requests

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "ClippyMe/1.0",
    }
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise ViralAIError(f"provider request failed: {exc}", status_code=502) from exc
    if response.status_code != 200:
        body = (response.text or "")[:400]
        raise ViralAIError(
            f"provider returned HTTP {response.status_code}: {body}",
            status_code=response.status_code,
            body=body,
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise ViralAIError("provider returned a non-JSON response", status_code=502) from exc
    if not isinstance(data, dict):
        raise ViralAIError("provider returned an unexpected JSON shape", status_code=502)
    return data


def _generate_once(prompt: str, model: str, *, api_key: str, base_url: str,
                   timeout: float) -> dict:
    """One call routed to the endpoint that serves this model."""
    if is_anthropic_model(model):
        data = _post_json(
            _url(base_url, "messages"), build_anthropic_payload(prompt, model),
            api_key, timeout,
        )
        result = extract_anthropic_response(data)
        result["endpoint"] = "messages"
    else:
        payload = build_openai_payload(prompt, model)
        try:
            data = _post_json(
                _url(base_url, "chat/completions"), payload, api_key, timeout,
            )
        except ViralAIError as exc:
            if exc.status_code != 400:
                raise
            # Some reasoning models reject sampling knobs or `max_tokens`.
            # Retry once with only the required fields before giving up on the
            # model (a 400 can also mean "unsupported model", in which case the
            # second failure propagates and the chain advances).
            data = _post_json(
                _url(base_url, "chat/completions"),
                {"model": model, "messages": [{"role": "user", "content": prompt}]},
                api_key, timeout,
            )
        result = extract_openai_response(data)
        result["endpoint"] = "chat"
        # Reasoning-first models can spend the whole budget thinking: either
        # returning empty content or a truncated JSON body (finish_reason
        # "length"). Retry with chain-of-thought disabled; providers that don't
        # know the field fall through to a larger output budget.
        truncated = result.get("finish_reason") == "length"
        if truncated or not result["text"]:
            try:
                retry = _post_json(
                    _url(base_url, "chat/completions"),
                    {**payload, "thinking": {"type": "disabled"}},
                    api_key, timeout,
                )
                result = extract_openai_response(retry)
                result["endpoint"] = "chat"
            except ViralAIError as exc:
                if exc.status_code != 400:
                    raise
            if result.get("finish_reason") == "length" or not result["text"]:
                retry = _post_json(
                    _url(base_url, "chat/completions"),
                    {**payload, "thinking": {"type": "disabled"},
                     "max_tokens": DEFAULT_MAX_TOKENS * 2},
                    api_key, timeout,
                )
                result = extract_openai_response(retry)
                result["endpoint"] = "chat"
    result["model"] = model
    return result


def _is_unavailable_model_error(exc: ViralAIError) -> bool:
    """400/403/404/422 normally mean this model can't serve us — try the next one.

    403 is included because coding plans gate individual models; a *plan-wide*
    "upgrade_required" 403 is caught by the caller before this check.
    """
    return exc.status_code in (400, 403, 404, 422)


def _is_account_permission_error(exc: ViralAIError) -> bool:
    if exc.status_code == 401:
        return True
    return exc.status_code == 403 and "upgrade_required" in (exc.body or "").lower()


def is_rate_limit_error(exc) -> bool:
    return getattr(exc, "status_code", None) == 429 or "429" in str(exc)


def _retryable(exc: ViralAIError) -> bool:
    return exc.status_code is None or exc.status_code == 429 or exc.status_code >= 500


def backoff_seconds(rate_limited: bool, attempt: int) -> int:
    """429 → 10/20/40s; transient → 2/4/8s (attempt is 0-based)."""
    base = 10 if rate_limited else 2
    return base * (2 ** attempt)


def generate_text(
    prompt: str,
    *,
    api_key: str,
    base_url: str | None = None,
    models: list[str] | None = None,
    max_attempts: int = 3,
    timeout: float = REQUEST_TIMEOUT_S,
    sleep_fn=time.sleep,
    log_fn=print,
    generate_fn=None,
) -> dict:
    """Generate text, retrying transient failures and falling through models.

    Returns ``{"text", "model", "input_tokens", "output_tokens", "endpoint"}``.
    Auth/permission errors and malformed requests that are not model-specific
    are raised immediately; a 429 or 5xx moves to the next model after the
    per-model attempts are spent.
    """
    chain = [model for model in (models or []) if model]
    if not chain:
        raise ViralAIError("No models configured", status_code=400)
    if not api_key:
        raise ViralAIError("API key missing", status_code=401)

    attempts = max(1, int(max_attempts))
    last_error: ViralAIError | None = None
    call = generate_fn or _generate_once

    for model_index, model in enumerate(chain):
        for attempt in range(attempts):
            try:
                result = call(
                    prompt, model, api_key=api_key,
                    base_url=normalize_base_url(base_url), timeout=timeout,
                )
                return result
            except ViralAIError as exc:
                last_error = exc
                if _is_account_permission_error(exc):
                    raise ViralAIError(
                        _redact(str(exc), api_key), status_code=exc.status_code,
                        body=_redact(exc.body, api_key),
                    ) from exc
                if _is_unavailable_model_error(exc):
                    log_fn(f"⏭️  Model {model} unavailable; skipping it")
                    break
                if not _retryable(exc):
                    raise
                rate_limited = is_rate_limit_error(exc)
                if rate_limited:
                    log_fn(f"🔀 Model {model} rate-limited; switching model")
                    break
                if attempt < attempts - 1:
                    wait = backoff_seconds(rate_limited, attempt)
                    log_fn(
                        f"⚠️  Model {model} transient error "
                        f"(attempt {attempt + 1}/{attempts}): {_redact(str(exc), api_key)}. "
                        f"Retrying in {wait}s..."
                    )
                    sleep_fn(wait)
        if model_index < len(chain) - 1:
            log_fn(f"🔀 Model {model} unavailable — trying {chain[model_index + 1]}")
    if last_error is not None:
        raise ViralAIError(
            _redact(str(last_error), api_key),
            status_code=last_error.status_code,
            body=_redact(last_error.body, api_key),
        )
    raise ViralAIError("No models configured", status_code=400)


def list_available_models(api_key: str | None, base_url: str | None = None,
                          *, timeout: float = LIST_MODELS_TIMEOUT_S,
                          get_fn=None) -> dict:
    """Return the provider catalog shaped for the dashboard.

    Result: ``{"models": [{"name", "display_name", "endpoint", "context_length"}],
    "error": "..."}``. The models endpoint is public, but a key is still required
    here so an unconfigured Settings panel shows a clear message rather than a
    surprise request.
    """
    if not api_key:
        return {"models": [], "error": "API Key missing"}

    if get_fn is None:
        import requests

        def get_fn(url, headers, timeout):  # noqa: ANN001 - requests.get shim
            return requests.get(url, headers=headers, timeout=timeout)

    url = _url(base_url, "models")
    try:
        response = get_fn(
            url,
            {"Authorization": f"Bearer {api_key}", "User-Agent": "ClippyMe/1.0"},
            timeout,
        )
    except Exception as exc:  # noqa: BLE001 - transport errors become UI text
        return {"models": [], "error": _redact(str(exc), api_key)}

    if getattr(response, "status_code", 200) != 200:
        body = (getattr(response, "text", "") or "")[:200]
        return {
            "models": [],
            "error": _redact(f"HTTP {response.status_code}: {body}", api_key),
        }
    try:
        data = response.json()
    except ValueError:
        return {"models": [], "error": "provider returned a non-JSON model list"}

    items = data.get("data") if isinstance(data, dict) else None
    if items is None and isinstance(data, dict):
        items = data.get("models")
    if not isinstance(items, list):
        return {"models": [], "error": "provider returned an unexpected model list"}

    models = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("id")
        if not name:
            continue
        endpoints = item.get("supported_endpoints") or []
        models.append({
            "name": name,
            "display_name": item.get("name") or name,
            "endpoint": endpoint_for_model(name, endpoints),
            "context_length": item.get("context_length"),
        })
    return {"models": models}
