"""Host-unit tests for AI caption optimization — pure prompt + parse + payloads."""
import requests

from clippyme.domain.caption_ai import (
    CaptionAIError,
    _chat_completion,
    build_caption_messages,
    build_clip_payloads,
    parse_caption_response,
)

SEGS = [
    {"index": 0, "text": "Hello and welcome back", "start": 0.0, "end": 2.0},
    {"index": 1, "text": "today we break down the launch", "start": 2.0, "end": 5.0},
]


def test_prompt_embeds_context_and_transcript():
    msgs = build_caption_messages(
        context="fitness channel, hype tone, CTA at the end",
        clip_title="PUSH day",
        segments=SEGS,
        clip_duration=5.0,
    )
    system, user = msgs
    assert "caption writer" in system["content"]
    assert "fitness channel" in user["content"]
    assert "PUSH day" in user["content"]
    assert "[0.00-2.00]" in user["content"]
    assert "5.00s" in user["content"]
    assert '"caption"' in user["content"]


def test_prompt_omits_context_when_empty():
    msgs = build_caption_messages(context="", clip_title="PUSH day", segments=SEGS, clip_duration=5.0)
    assert "CONTEXT" not in msgs[1]["content"]


def test_prompt_truncates_long_context():
    msgs = build_caption_messages(
        context="x" * 5000, clip_title="t", segments=SEGS, clip_duration=5.0)
    assert "x" * 2000 in msgs[1]["content"]
    assert "x" * 2001 not in msgs[1]["content"]


def test_prompt_handles_missing_transcript():
    msgs = build_caption_messages(context="", clip_title="t", segments=[], clip_duration=5.0)
    assert "(no transcript available)" in msgs[1]["content"]


def test_parse_plain_json():
    assert parse_caption_response('{"caption": "PUSH harder with Coach Mike!"}') == "PUSH harder with Coach Mike!"


def test_parse_accepts_alternate_keys_and_fences():
    assert parse_caption_response('```json\n{"text": "keep pushing"}\n```') == "keep pushing"
    assert parse_caption_response('{"caption_text": "a caption"}') == "a caption"


def test_parse_trims_prose_fallback_at_boundary():
    long = "word " * 200
    out = parse_caption_response(long)
    assert len(out) <= 400
    assert parse_caption_response("", ) == ""
    assert parse_caption_response("nonsense without json")  # non-empty prose fallback


def test_parse_drops_unusable_output():
    assert parse_caption_response("") == ""
    assert parse_caption_response("{}") == ""
    assert parse_caption_response('{"error": "bad"}') == ""


def test_build_clip_payloads_maps_positions_and_clip_relative_segments():
    transcript = {"language": "en", "segments": [{"words": [
        {"start": 0.0, "end": 1.0, "word": "one"},
        {"start": 1.0, "end": 2.0, "word": "two"},
        {"start": 10.0, "end": 11.0, "word": "three"},
    ]}]}
    shorts = [
        {"start": 0.0, "end": 5.0, "video_title_for_youtube_short": "Clip A"},
        {"start": 9.0, "end": 12.0, "title": "Clip B"},
    ]
    payloads = build_clip_payloads(transcript, shorts, [0, 1], context="ctx")
    assert [p["index"] for p in payloads] == [0, 1]
    assert payloads[0]["title"] == "Clip A"
    assert payloads[1]["title"] == "Clip B"
    assert payloads[0]["duration"] == 5.0
    # Only the words inside clip 1's window are sent, times clip-relative.
    texts = " ".join(s["text"] for s in payloads[1]["segments"])
    assert "three" in texts and "one" not in texts
    assert all(p["context"] == "ctx" for p in payloads)


def test_build_clip_payloads_skips_out_of_range():
    assert build_clip_payloads({}, [{"start": 0, "end": 1}], [0, 5]) == [{"index": 0,
        "title": "Clip 1", "duration": 1.0, "segments": [], "context": ""}]


class _FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.text = ""
        self._payload = payload

    def json(self):
        return self._payload


def test_reasoning_model_budget_exhausted_retries_with_thinking_disabled(monkeypatch):
    """deepseek-v4-pro burns its budget reasoning (empty content) → retry with
    chain-of-thought disabled returns the caption directly."""
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(json)
        if json.get("thinking") == {"type": "disabled"}:
            return _FakeResp(200, {"choices": [{"message": {
                "role": "assistant", "content": '{"caption": "Great caption #history"}'}}]})
        return _FakeResp(200, {"choices": [{"message": {
            "role": "assistant", "content": "",
            "reasoning_content": "Thinking about the transcript for a while..."}}]})

    monkeypatch.setattr(requests, "post", fake_post)
    out = _chat_completion("https://x/v1", "k", "deepseek-v4-pro",
                           [{"role": "user", "content": "hi"}])
    assert out == '{"caption": "Great caption #history"}'
    assert len(calls) == 2
    assert calls[1]["thinking"] == {"type": "disabled"}


def test_reasoning_retry_falls_back_to_large_budget_when_param_rejected(monkeypatch):
    """A provider that 400s on `thinking` gets the big-budget retry instead."""
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(json)
        if json.get("thinking") == {"type": "disabled"}:
            return _FakeResp(400, {"error": "unknown parameter"})
        if json.get("max_tokens") == 8192:
            return _FakeResp(200, {"choices": [{"message": {
                "role": "assistant", "content": '{"caption": "Slow but works"}'}}]})
        return _FakeResp(200, {"choices": [{"message": {
            "role": "assistant", "content": "", "reasoning_content": "..."}}]})

    monkeypatch.setattr(requests, "post", fake_post)
    out = _chat_completion("https://x/v1", "k", "deepseek-v4-pro",
                           [{"role": "user", "content": "hi"}])
    assert out == '{"caption": "Slow but works"}'
    assert [c.get("max_tokens") for c in calls] == [1024, 1024, 8192]


def test_non_reasoning_model_returns_content_directly(monkeypatch):
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(json)
        return _FakeResp(200, {"choices": [{"message": {
            "role": "assistant", "content": '{"caption": "Plain model answer"}'}}]})

    monkeypatch.setattr(requests, "post", fake_post)
    out = _chat_completion("https://x/v1", "k", "gpt-4o-mini",
                           [{"role": "user", "content": "hi"}])
    assert out == '{"caption": "Plain model answer"}'
    assert len(calls) == 1


def test_chat_http_error_raises(monkeypatch):
    def fake_post(url, json=None, headers=None, timeout=None):
        return _FakeResp(429, {"error": {"message": "rate limited"}})

    monkeypatch.setattr(requests, "post", fake_post)
    try:
        _chat_completion("https://x/v1", "k", "m", [{"role": "user", "content": "hi"}])
        assert False, "expected CaptionAIError"
    except CaptionAIError as exc:
        assert exc.status_code == 429
        assert "429" in str(exc)
