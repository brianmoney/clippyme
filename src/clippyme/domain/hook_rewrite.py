"""Regenerate a clip's hook text via Gemini in a target language.

The pipeline writes ``viral_hook_text`` in the transcript's language (see the
LANGUAGE RULE in ``gemini_request.py``), but a mixed-language video can make
the model emit hooks in the wrong language. This lets the user ask Gemini to
rewrite a clip's hook in a specific language using the clip's own transcript,
so the overlay copy gets fixed without hand-writing it.

Prompt building + response parsing are pure (host-unit-tested). The Gemini call
is the same thin ``google-genai`` wrapper as ``clip_edit_ai`` — no cv2, so this
stays importable on the host.
"""
from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

# Guardrails — keep an authenticated request from handing the model an
# unbounded transcript or hook.
MAX_SEGMENTS = 600
MAX_HOOK_CHARS = 160


def build_hook_prompt(*, segments: list[dict], current_hook: str,
                      target_language: str, clip_duration: float) -> str:
    """Build the Gemini prompt. Pure.

    ``segments`` are {index, text, start, end} in CLIP-RELATIVE seconds
    (exactly what ``/api/transcript`` returns). The model is asked for a new
    scroll-stopping hook in ``target_language`` grounded in the clip's own
    transcript, keeping the meaning of the (wrong-language) ``current_hook``.
    """
    lines = []
    for s in (segments or [])[:MAX_SEGMENTS]:
        try:
            st = float(s.get("start", 0))
            en = float(s.get("end", 0))
        except (TypeError, ValueError):
            continue
        txt = (s.get("text", "") or "").replace("\n", " ").strip()
        if not txt:
            continue
        lines.append(f"[{st:.2f}-{en:.2f}] {txt}")
    transcript_block = "\n".join(lines) if lines else "(no transcript available)"
    current = (current_hook or "").strip()[:MAX_HOOK_CHARS]

    return (
        "You write scroll-stopping hook text — the bold overlay shown on "
        "short-form clips for TikTok, Instagram Reels and YouTube Shorts.\n\n"
        f"CLIP DURATION: {clip_duration:.2f}s\n"
        f"CURRENT HOOK (written in the wrong language — keep its meaning, do "
        f"not copy it verbatim):\n"
        f"\"{current or '(none)'}\"\n\n"
        "CLIP TRANSCRIPT:\n"
        f"{transcript_block}\n\n"
        f"Write a NEW hook in {target_language}.\n"
        "Rules:\n"
        "- 3-8 words, standalone overlay copy — NOT a transcript quote.\n"
        "- Use one of the proven patterns: curiosity gap, POV / relatable, "
        "counter-intuitive claim, direct question, number / stakes, or warning.\n"
        "- TEASE the clip's content without spoiling the payoff. Same meaning "
        "as the current hook where possible, but grounded in this transcript.\n"
        "- Title Case or Sentence case, never ALL CAPS.\n"
        f"- Write ONLY in {target_language}.\n\n"
        "Return ONLY strict JSON in this exact shape and nothing else:\n"
        '{"hook_text": "<the new hook string>"}'
    )


def parse_hook_response(text: str) -> str:
    """Extract the hook string from the model's response. Pure + tolerant.

    Prefers a strict JSON object with a ``hook_text`` field (some providers
    wrap it in ```json fences); falls back to the raw text trimmed, so
    prose-speaking local models still work. Never raises; returns "" on
    unusable output.
    """
    if not text:
        return ""
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = re.sub(r"^json\s*", "", raw, flags=re.IGNORECASE).strip()
    # Locate the JSON object by first '{' / last '}' — linear, can't backtrack
    # on adversarial model output.
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(raw[start:end + 1])
        except (ValueError, TypeError):
            obj = None
        if isinstance(obj, dict):
            for key in ("hook_text", "hook", "text"):
                value = obj.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()[:MAX_HOOK_CHARS]
            # A well-formed JSON object without a usable hook field is a model
            # failure — never fall through to prose.
            return ""
    cleaned = re.sub(r"```", "", raw)
    cleaned = re.sub(r"^json\s*", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:MAX_HOOK_CHARS]


def rewrite_hook(*, api_key: str, model: str, segments: list[dict],
                 current_hook: str, target_language: str,
                 clip_duration: float) -> dict:
    """Ask Gemini to rewrite the hook in ``target_language``.

    Returns ``{"hook_text": str, "error": str}``. Network/SDK failures are
    swallowed into an empty hook_text with the error — a failed rewrite must
    not 500 the editor, and the endpoint can surface the reason to the UI.
    """
    if not api_key:
        return {"hook_text": "", "error": "Gemini API key not configured."}
    prompt = build_hook_prompt(
        segments=segments,
        current_hook=current_hook,
        target_language=target_language,
        clip_duration=clip_duration,
    )
    try:
        from google import genai

        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(model=model, contents=prompt)
        text = getattr(resp, "text", "") or ""
        hook = parse_hook_response(text)
        logger.info(
            "hook_rewrite: model=%s lang=%s hook_chars=%d",
            model, target_language, len(hook),
        )
        if hook:
            return {"hook_text": hook, "error": ""}
        return {"hook_text": "", "error": "The model returned an empty hook."}
    except Exception as e:  # pragma: no cover — network path
        from clippyme.pipeline.gemini_service import _redact_key

        logger.warning("hook_rewrite failed: %s", e)
        return {"hook_text": "", "error": f"AI hook rewrite failed: {_redact_key(str(e))}"}
