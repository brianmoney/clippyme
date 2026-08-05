"""AI caption optimization for a batch of clips via any OpenAI-compatible API.

The results page lets the user type a short "context" for the series of clips
(e.g. "fitness channel, host is Coach Mike, hype tone, end with a CTA"). Each
clip's OWN transcript segments plus that context are sent to a user-configured
OpenAI-compatible ``/chat/completions`` endpoint (OpenAI, OpenRouter, Ollama,
LM Studio, ...) which returns an optimized publish caption per clip. The user
can then edit any caption in the grid or publish modal.

Prompt building + tolerant response parsing + clip→payload mapping are pure
(host-unit-tested, no cv2/ML imports). The HTTP call is a minimal ``requests``
POST with no third-party SDK so arbitrary providers work unchanged. The
endpoint is user-configurable and trusted-origin gated; local/private hosts
are deliberately allowed because self-hosted LLMs on the LAN (Ollama, LM
Studio) are a first-class target of this feature.
"""
from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger("clippyme")

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"

MAX_CONTEXT_CHARS = 2000
MAX_SEGMENTS = 600
MAX_CAPTION_CHARS = 400

SYSTEM_PROMPT = (
    "You are an expert short-form video caption writer for TikTok, Instagram "
    "Reels and YouTube Shorts. Write a scroll-stopping caption in the SAME "
    "language as the transcript. Keep it under ~150 characters before "
    "hashtags: a punchy hook or question line, optionally one emoji, then 2-4 "
    "relevant hashtags. Never invent facts about the video. Use the user "
    "context to set the tone, channel style and any call to action."
)


class CaptionAIError(Exception):
    """Wraps any provider/HTTP failure from the caption endpoint."""

    def __init__(self, message: str, status_code: int | None = None,
                 body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def build_caption_messages(*, context: str, clip_title: str,
                           segments: list[dict], clip_duration: float) -> list[dict]:
    """Build the chat-completion messages for one clip. Pure.

    ``segments`` are {index, text, start, end} in CLIP-RELATIVE seconds (what
    ``/api/transcript`` returns). The model is asked for strict JSON so the
    tolerant parser can extract it from any provider's output.
    """
    lines = []
    for seg in (segments or [])[:MAX_SEGMENTS]:
        try:
            st = float(seg.get("start", 0))
            en = float(seg.get("end", 0))
        except (TypeError, ValueError):
            continue
        txt = (seg.get("text", "") or "").replace("\n", " ").strip()
        lines.append(f"[{st:.2f}-{en:.2f}] {txt}")
    transcript_block = "\n".join(lines) if lines else "(no transcript available)"
    context_block = (context or "").strip()[:MAX_CONTEXT_CHARS]

    user = ""
    if context_block:
        user += (
            "CONTEXT (the creator's series notes — follow the tone/style):\n"
            f"{context_block}\n\n"
        )
    user += (
        f"CLIP TITLE: {(clip_title or '').strip() or 'Untitled clip'}\n"
        f"CLIP DURATION: {clip_duration:.2f}s\n"
        "CLIP TRANSCRIPT:\n"
        f"{transcript_block}\n\n"
        "Return ONLY strict JSON in this exact shape and nothing else:\n"
        '{"caption": "<the optimized caption string>"}'
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def parse_caption_response(text: str) -> str:
    """Extract the caption string from a provider's response. Pure + tolerant.

    Prefers a strict JSON object with a ``caption``/``text`` field (some
    providers also wrap it in ```json fences); falls back to the raw text
    trimmed at a sentence boundary so prose-speaking local models still work.
    Never raises; returns "" on unusable output.
    """
    if not text:
        return ""
    raw = text.strip()
    start = raw.find("{")
    end = raw.rfind("}")
    parsed_json = None
    if start != -1 and end > start:
        try:
            parsed_json = json.loads(raw[start:end + 1])
        except (ValueError, TypeError):
            parsed_json = None
    if isinstance(parsed_json, dict):
        for key in ("caption", "text", "caption_text"):
            value = parsed_json.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:MAX_CAPTION_CHARS]
        # A well-formed JSON object without a usable caption field is a model
        # failure (error object, empty reply) — never fall through to prose.
        return ""
    # Prose fallback — strip fences / "json" markers, collapse whitespace.
    cleaned = re.sub(r"```", "", raw)
    cleaned = re.sub(r"^json\s*", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return ""
    if len(cleaned) <= MAX_CAPTION_CHARS:
        return cleaned
    cut = cleaned[:MAX_CAPTION_CHARS]
    boundary = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "), cut.rfind("; "))
    return cut[: boundary + 1] if boundary > 0 else cut


def build_clip_payloads(transcript: dict, shorts: list[dict], indices: list[int],
                        context: str = "") -> list[dict]:
    """Map ``shorts`` positions → per-clip payloads for :func:`optimize_captions`.

    Pure. Skips out-of-range indices (the caller validates first). Each clip
    carries its own transcript segments (clip-relative) so the model only sees
    what is actually said in that clip.
    """
    from clippyme.domain.smartcut_ops import clip_transcript_segments

    payloads = []
    for index in indices:
        if index < 0 or index >= len(shorts):
            continue
        clip = shorts[index]
        start, end = clip.get("start", 0), clip.get("end", 0)
        try:
            duration = max(0.0, float(end) - float(start))
        except (TypeError, ValueError):
            duration = 0.0
        segments = clip_transcript_segments(transcript, start, end)
        title = (clip.get("video_title_for_youtube_short") or clip.get("title")
                 or f"Clip {index + 1}")
        payloads.append({
            "index": index,
            "title": title,
            "duration": duration,
            "segments": segments,
            "context": context,
        })
    return payloads


def _chat_url(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/") or DEFAULT_BASE_URL
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _post_chat(payload: dict, base_url: str, api_key: str,
               timeout: float) -> tuple[str, str]:
    """One OpenAI-compatible chat-completion POST.

    Returns ``(content, reasoning_content)``. Raises ``CaptionAIError`` on
    transport/HTTP/shape failures (never returns an error as text).
    """
    import requests

    url = _chat_url(base_url)
    headers = {"Content-Type": "application/json", "User-Agent": "ClippyMe/1.0"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise CaptionAIError(f"Caption AI request failed: {exc}", status_code=502)
    if resp.status_code != 200:
        snippet = (resp.text or "")[:400]
        status = resp.status_code if resp.status_code < 500 else 502
        raise CaptionAIError(
            f"Caption AI returned HTTP {resp.status_code}: {snippet}",
            status_code=status, body=snippet,
        )
    try:
        data = resp.json()
    except ValueError:
        raise CaptionAIError("Caption AI returned a non-JSON response", status_code=502)
    choices = data.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        raise CaptionAIError("Caption AI response had no choices", status_code=502)
    message = choices[0].get("message") or {}
    content = (message.get("content") or "").strip()
    reasoning = (message.get("reasoning_content") or "").strip()
    return content, reasoning


def _chat_completion(base_url: str, api_key: str, model: str,
                     messages: list[dict], timeout: float = 90.0) -> str:
    """One OpenAI-compatible chat-completion call. Returns the content text.

    Reasoning-first models (e.g. ``deepseek-v4-pro``) can spend the whole
    token budget "thinking" and return empty ``content`` (``finish=length``).
    When that happens we retry once with chain-of-thought disabled so the model
    answers directly; providers that reject the ``thinking`` param fall back to
    a large token budget for the reasoning to finish.
    """
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.8,
        "max_tokens": 1024,
    }
    content, reasoning = _post_chat(payload, base_url, api_key, timeout)
    if content or not reasoning:
        return content
    logger.warning(
        "caption_ai: model=%s spent its token budget reasoning (content empty) — "
        "retrying with chain-of-thought disabled", model,
    )
    try:
        content, _ = _post_chat(
            {**payload, "thinking": {"type": "disabled"}}, base_url, api_key, timeout)
        if content:
            return content
    except CaptionAIError as exc:
        if exc.status_code != 400:  # 400 = provider doesn't know `thinking`
            raise
    content, _ = _post_chat(
        {**payload, "max_tokens": 8192}, base_url, api_key, timeout)
    return content


def optimize_captions(*, base_url: str, api_key: str, model: str,
                      clips: list[dict], timeout: float = 90.0) -> list[dict]:
    """Generate one caption per clip. Returns ``[{"index": i, "caption": str}]``.

    Fail-fast: a provider/auth/rate error fails the whole batch so the user
    sees the real reason once instead of silently missing captions.
    """
    if not api_key:
        raise CaptionAIError(
            "OpenAI-compatible API key not configured (Settings → AI captions)",
            status_code=400,
        )
    if not clips:
        return []
    results = []
    for clip in clips:
        messages = build_caption_messages(
            context=clip.get("context", ""),
            clip_title=clip.get("title"),
            segments=clip.get("segments") or [],
            clip_duration=float(clip.get("duration") or 0),
        )
        text = _chat_completion(base_url, api_key, model, messages, timeout=timeout)
        caption = parse_caption_response(text)
        logger.info("caption_ai: clip=%s caption_chars=%d",
                    clip.get("index"), len(caption))
        results.append({"index": clip.get("index"), "caption": caption})
    return results
