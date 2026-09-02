"""Host-unit tests for hook rewrite — pure prompt + parse."""
from clippyme.domain.hook_rewrite import build_hook_prompt, parse_hook_response


SEGS = [
    {"index": 0, "text": "I was doing everything wrong", "start": 0.0, "end": 2.5},
    {"index": 1, "text": "then I found the trick", "start": 2.5, "end": 5.0},
]


def test_prompt_includes_transcript_times_language_and_current_hook():
    p = build_hook_prompt(
        segments=SEGS, current_hook="Stavo sbagliando tutto",
        target_language="en", clip_duration=5.0)
    assert "[0.00-2.50]" in p
    assert "Write a NEW hook in en" in p
    assert "Stavo sbagliando tutto" in p
    assert "5.00s" in p


def test_prompt_skips_empty_segments_and_truncates_hook():
    segs = [SEGS[0], {"index": 2, "text": "  ", "start": 5.0, "end": 6.0}]
    p = build_hook_prompt(
        segments=segs, current_hook="x" * 500, target_language="it",
        clip_duration=5.0)
    assert "[0.00-2.50]" in p
    assert "x" * 161 not in p
    assert "it" in p


def test_parse_plain_json():
    assert parse_hook_response('{"hook_text": "The lie everyone believes"}') \
        == "The lie everyone believes"


def test_parse_strips_fences_and_prose():
    txt = 'Sure!\n```json\n{"hook_text": "Guess what happened"}\n```'
    assert parse_hook_response(txt) == "Guess what happened"


def test_parse_accepts_hook_or_text_fields():
    assert parse_hook_response('{"hook": "3 things nobody tells you"}') \
        == "3 things nobody tells you"
    assert parse_hook_response('{"text": "Stop scrolling if..."}') == "Stop scrolling if..."


def test_parse_prose_fallback():
    assert parse_hook_response("This ends his career") == "This ends his career"


def test_parse_garbage_and_empty():
    assert parse_hook_response("not json at all {") == "not json at all {"
    assert parse_hook_response("") == ""
    assert parse_hook_response('{"error": "bad"}') == ""


def test_parse_truncates_to_max_chars():
    out = parse_hook_response('{"hook_text": "' + "a" * 500 + '"}')
    assert len(out) == 160
