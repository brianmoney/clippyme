"""Host tests for the pure two-stage discovery/review/ranking logic."""
import json
from pathlib import Path

import pytest

from clippyme.pipeline import two_stage_ops as ops
from clippyme.pipeline.cut_ops import flatten_words

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "viral_transcript.json"


def load_transcript():
    return json.loads(FIXTURE.read_text())


def load_words():
    return flatten_words(load_transcript())


def candidate(raw, words, min_duration=15, max_duration=60):
    return ops.normalize_candidate(
        raw, words, words[-1]["end"],
        min_duration=min_duration, max_duration=max_duration,
    )


def test_chunk_words_covers_every_word_with_overlap():
    words = [{"start": i * 0.5, "end": i * 0.5 + 0.4, "word": f"w{i}"} for i in range(30)]
    chunks = ops.chunk_words(words, max_words=10, overlap_words=3)
    assert chunks[0]["start_index"] == 0
    assert chunks[-1]["end_index"] == 30
    for previous, current in zip(chunks, chunks[1:]):
        # overlap means the next chunk starts before the previous one ended
        assert current["start_index"] < previous["end_index"]
    covered = set()
    for chunk in chunks:
        covered.update(range(chunk["start_index"], chunk["end_index"]))
    assert covered == set(range(30))


def test_chunk_words_short_transcript_is_one_chunk():
    words = [{"start": i, "end": i + 0.5, "word": "x"} for i in range(5)]
    chunks = ops.chunk_words(words, max_words=100, overlap_words=10)
    assert len(chunks) == 1
    assert chunks[0]["end_index"] == 5


def test_normalize_candidate_anchors_to_word_indices_and_is_stable():
    words = load_words()
    raw = {
        "word_start_index": 10,
        "word_end_index": 60,
        "excerpt": "irrelevant model text",
        "opening_line": "ignored too",
        "strongest_quote": "not in transcript",
        "why": "A specific, surprising story with a clear payoff.",
        "category": "story",
    }
    first = candidate(raw, words)
    second = candidate(raw, words)
    assert first["id"] == second["id"]
    assert first["id"].startswith("vc_")
    assert first["start"] == pytest.approx(words[10]["start"])
    assert first["end"] == pytest.approx(words[60]["end"])
    # The excerpt/quote come from the REAL words, not the model's claims.
    assert words[10]["word"] in first["excerpt"]
    assert "not in transcript" not in first["strongest_quote"]
    assert first["excerpt_verified"] is False
    assert first["category"] == "story"
    assert first["context_before"]
    assert first["context_after"]


def test_normalize_candidate_snaps_raw_timestamps_to_words():
    words = load_words()
    raw = {"start": words[5]["start"] + 0.2, "end": words[15]["end"] - 0.2, "why": "x"}
    out = candidate(raw, words)
    assert out is not None
    assert out["start"] == pytest.approx(words[5]["start"])
    assert out["end"] == pytest.approx(words[15]["end"])


def test_normalize_candidate_rejects_invalid_shapes():
    words = load_words()
    assert candidate({"start": 10, "end": 5}, words) is None
    assert candidate({"start": "nope", "end": 30}, words) is None
    assert candidate({"start": 10, "end": 10.5}, words) is None  # too short
    assert ops.normalize_candidate({"start": 1, "end": 20}, [], None) is None


def test_parse_discovery_response_handles_malformed_and_valid_entries():
    words = load_words()
    raw = {
        "candidates": [
            {"word_start_index": 0, "word_end_index": 40, "why": "strong opening"},
            {"word_start_index": 50, "word_end_index": 20, "why": "inverted is fixed"},
            {"start": "bad", "end": "worse"},
            {"word_start_index": 20, "word_end_index": 80, "why": "second moment"},
        ]
    }
    text = "reasoning...\n### JSON ###\n" + json.dumps(raw)
    candidates, stats = ops.parse_discovery_response(
        text, words, words[-1]["end"], min_duration=10, max_duration=45
    )
    assert len(candidates) == 3
    assert stats["raw_count"] == 4
    assert stats["rejected"] == 1
    assert all(c["start"] < c["end"] for c in candidates)


def test_parse_discovery_response_rejects_non_json():
    words = load_words()
    candidates, stats = ops.parse_discovery_response("not json", words, None)
    assert candidates == []
    assert stats["rejected"] == 0
    assert "error" in stats


def test_consolidate_removes_near_duplicates_but_keeps_distinct_overlaps():
    words = load_words()
    base = candidate({"word_start_index": 10, "word_end_index": 50, "why": "a"}, words)
    duplicate = candidate({"word_start_index": 10, "word_end_index": 50, "why": "b"}, words)
    alternative = candidate({"word_start_index": 35, "word_end_index": 80, "why": "different choice"}, words)
    kept, stats = ops.consolidate_candidates([base, duplicate, alternative], max_candidates=10)
    assert len(kept) == 2
    assert stats["duplicates_removed"] == 1


def test_consolidate_cap_is_round_robin_across_chunks():
    words = load_words()
    items = []
    for chunk in range(3):
        for order in range(4):
            start = chunk * 20 + order * 2
            item = candidate({"word_start_index": start, "word_end_index": start + 30, "why": "x"}, words)
            item["chunk_index"] = chunk
            item["discovery_order"] = order
            items.append(item)
    kept, stats = ops.consolidate_candidates(items, max_candidates=6)
    assert len(kept) == 6
    assert stats["capped_out"] == 6
    # Every chunk must be represented before any chunk gets a third slot.
    assert {c["chunk_index"] for c in kept} == {0, 1, 2}


def review_payload(ids, quality=8.0, selected=True, shortlist=None, pairwise=None):
    evaluations = []
    for cid in ids:
        evaluations.append({
            "candidate_id": cid,
            "scores": {key: quality for key in ops.RUBRIC_KEYS},
            "payoff_too_late": False,
            "requires_background": False,
            "better_opening_exists": False,
            "shorter_edit_better": False,
            "duplicate_of": None,
            "hook_misrepresents": False,
            "suggested_word_start_index": None,
            "suggested_word_end_index": None,
            "boundary_note": "",
            "selected": selected,
            "rejection_reason": "" if selected else "not compelling enough",
            "why_clip": "It has a specific reveal and a clear payoff for a cold viewer.",
            "opening_strength": "The first line is a bold claim.",
            "viewer_payoff": "A concrete number and a reversal.",
            "payoff_quote": "the number was negative forty thousand",
            "weaknesses": "Slightly long setup.",
        })
    return {
        "evaluations": evaluations,
        "pairwise": pairwise or [],
        "shortlist": shortlist if shortlist is not None else ids,
    }


def test_parse_review_scores_and_ranking_threshold():
    words = load_words()
    a = candidate({"word_start_index": 0, "word_end_index": 40, "why": "a"}, words)
    b = candidate({"word_start_index": 60, "word_end_index": 100, "why": "b"}, words)
    payload = review_payload([a["id"], b["id"]], quality=4.5)  # below 6.0
    result = ops.parse_review_response(
        "### JSON ###\n" + json.dumps(payload), [a, b], min_quality=6.0, max_selected=8
    )
    assert result["selected"] == []
    assert result["stats"]["rejected"] == 2
    assert "below threshold" in result["rejected"][0]["reason"]


def test_parse_review_duplicate_and_misleading_hook_are_rejected():
    words = load_words()
    a = candidate({"word_start_index": 0, "word_end_index": 40, "why": "a"}, words)
    b = candidate({"word_start_index": 60, "word_end_index": 100, "why": "b"}, words)
    payload = review_payload([a["id"], b["id"]], quality=9.0)
    payload["evaluations"][1]["duplicate_of"] = a["id"]
    payload["evaluations"][0]["hook_misrepresents"] = True
    result = ops.parse_review_response(
        "### JSON ###\n" + json.dumps(payload), [a, b], min_quality=6.0, max_selected=8
    )
    assert result["selected"] == []
    assert result["stats"]["evaluated"] == 2


def test_parse_review_pairwise_tiebreak_and_max_selected():
    words = load_words()
    a = candidate({"word_start_index": 0, "word_end_index": 40, "why": "a"}, words)
    b = candidate({"word_start_index": 60, "word_end_index": 100, "why": "b"}, words)
    payload = review_payload([a["id"], b["id"]], quality=7.0, shortlist=[a["id"], b["id"]])
    payload["pairwise"] = [{"a": a["id"], "b": b["id"], "winner": b["id"], "reason": "stronger cold open"}]
    result = ops.parse_review_response(
        "### JSON ###\n" + json.dumps(payload), [a, b], min_quality=6.0, max_selected=1
    )
    assert result["selected"][0]["candidate_id"] == b["id"]


def test_parse_review_missing_candidate_is_rejected_not_ignored():
    words = load_words()
    a = candidate({"word_start_index": 0, "word_end_index": 40, "why": "a"}, words)
    b = candidate({"word_start_index": 60, "word_end_index": 100, "why": "b"}, words)
    payload = review_payload([a["id"]], quality=8.0)
    result = ops.parse_review_response(
        "### JSON ###\n" + json.dumps(payload), [a, b], min_quality=6.0
    )
    assert result["stats"]["rejected"] == 1
    assert result["rejected"][0]["candidate_id"] == b["id"]


def test_refine_boundaries_uses_suggestions_and_enforces_duration():
    words = load_words()
    a = candidate({"word_start_index": 0, "word_end_index": 80, "why": "a"}, words)
    evaluation = {
        "candidate_id": a["id"],
        "quality_score": 8.0,
        "suggested_word_start_index": 10,
        "suggested_word_end_index": 50,
        "boundary_note": "start after the intro",
    }
    refined = ops.refine_boundaries(
        [evaluation], {a["id"]: a}, words,
        video_duration=words[-1]["end"], min_duration=10, max_duration=25,
    )
    assert len(refined) == 1
    assert refined[0]["start"] == pytest.approx(words[10]["start"])
    assert refined[0]["end"] <= words[10]["start"] + 25.5
    assert refined[0]["boundary_changed"] is True


def test_dedupe_final_keeps_first_of_overlapping_pair():
    a = {"candidate_id": "a", "start": 0, "end": 30}
    b = {"candidate_id": "b", "start": 5, "end": 35}
    kept, dropped = ops.dedupe_final([a, b], similarity_threshold=0.5)
    assert [item["candidate_id"] for item in kept] == ["a"]
    assert dropped[0]["candidate_id"] == "b"


def test_fallback_copy_is_grounded_and_build_final_clip_fields():
    words = load_words()
    cand = candidate({"word_start_index": 0, "word_end_index": 50, "why": "specific reveal"}, words)
    evaluation = {
        "candidate_id": cand["id"], "quality_score": 8.4, "rank": 1,
        "scores": {key: 8.4 for key in ops.RUBRIC_KEYS}, "pairwise_wins": 1,
        "flags": {}, "why_clip": "A concrete story with a surprising reversal.",
        "opening_strength": "Bold claim.", "viewer_payoff": "The reversal.",
        "payoff_quote": cand["strongest_quote"], "weaknesses": "Long setup.",
        "boundary_note": "",
    }
    refined = [{
        "candidate_id": cand["id"], "evaluation": evaluation, "candidate": cand,
        "start": cand["start"], "end": cand["end"], "duration": cand["duration"],
        "boundary_changed": False, "original_start": cand["start"],
        "original_end": cand["end"], "suggested": False, "note": "",
    }]
    copies = {cand["id"]: ops.fallback_copy(cand)}
    clips = ops.build_final_clips(
        refined, copies,
        discovery_model=ops.DISCOVERY_MODEL,
        review_model=ops.REVIEW_MODEL,
        copy_model=ops.COPY_MODEL,
    )
    clip = clips[0]
    assert clip["viral_score"] == 84
    assert clip["candidate_id"] == cand["id"]
    assert clip["review"]["rank"] == 1
    assert clip["two_stage"]["review_model"] == ops.REVIEW_MODEL
    assert clip["viral_reason"]
    assert clip["video_title_for_youtube_short"]


def test_parse_copy_response_fills_missing_ids_with_fallback():
    words = load_words()
    cand = candidate({"word_start_index": 0, "word_end_index": 50, "why": "x"}, words)
    evaluation = {"candidate_id": cand["id"], "quality_score": 8, "rank": 1, "scores": {}}
    refined = [{
        "candidate_id": cand["id"], "evaluation": evaluation, "candidate": cand,
        "start": cand["start"], "end": cand["end"], "duration": cand["duration"],
        "boundary_changed": False, "original_start": cand["start"],
        "original_end": cand["end"], "suggested": False, "note": "",
    }]
    copies = ops.parse_copy_response(
        "### JSON ###\n" + json.dumps({"copies": [{"candidate_id": "unknown", "video_title_for_youtube_short": "x"}]}),
        refined,
    )
    assert cand["id"] in copies
    assert copies[cand["id"]]["video_title_for_youtube_short"]


def test_compare_with_legacy_reports_overlap_and_unique_moments():
    legacy = [{"start": 0, "end": 30}, {"start": 100, "end": 130}]
    new = [{"start": 2, "end": 32}, {"start": 200, "end": 230}]
    report = ops.compare_with_legacy(legacy, new, discovery_candidates=[1, 2, 3])
    assert report["legacy_count"] == 2 and report["two_stage_count"] == 2
    assert report["overlap_pairs"] == 1
    assert report["legacy_unique"]
    assert report["two_stage_unique"]
    text = ops.format_eval_report(report)
    assert "Two-stage" in text


def test_build_discovery_prompt_has_delimiter_and_contract():
    words = load_words()
    chunk = ops.chunk_words(words, max_words=1000)[0]
    prompt = ops.build_discovery_prompt(
        chunk, words, video_duration=words[-1]["end"],
        min_duration=15, max_duration=60, target_count=8,
        instructions="focus on the money story", creator="Test Channel",
        video_title="Test video",
    )
    assert "### JSON ###" in prompt
    assert "word_start_index" in prompt
    assert "focus on the money story" in prompt
    assert "words[" in prompt


def test_build_review_prompt_does_not_receive_discovery_scores():
    words = load_words()
    cand = candidate({"word_start_index": 0, "word_end_index": 50, "why": "why"}, words)
    prompt, meta = ops.build_review_prompt(
        [cand], words, video_duration=words[-1]["end"], instructions="be strict"
    )
    assert "### JSON ###" in prompt
    assert "opening_hook" in prompt
    assert "discovery_why:" in prompt
    assert meta["truncated"] is False


def test_compare_with_legacy_accepts_discovery_count():
    report = ops.compare_with_legacy([{"start": 0, "end": 10}], [{"start": 0, "end": 10}], 6)
    assert report["discovery_candidate_count"] == 6


# --- audio cues (ElevenLabs Scribe audio_events) -----------------------------

def test_normalize_audio_events_cleans_sorts_and_dedupes():
    raw = [
        {"text": "  (laughter)  ", "start": "12.5", "end": "13.0"},
        {"text": "(music)", "start": 5, "end": 4},          # bad duration → point event
        {"text": "", "start": 1, "end": 2},                 # no label
        {"start": 1, "end": 2},                             # no label/event
        {"text": "(applause)", "start": None, "end": 9},    # missing start
        {"text": "(laughter)", "start": 12.5, "end": 13.0}, # duplicate
    ]
    out = ops.normalize_audio_events(raw)
    assert [event["text"] for event in out] == ["(music)", "(applause)", "(laughter)"]
    assert out[0]["start"] == 5.0 and out[0]["end"] == 5.0
    assert out[1]["start"] == 9.0
    assert out[2]["start"] == 12.5 and out[2]["end"] == 13.0
    assert ops.normalize_audio_events(None) == []
    assert ops.normalize_audio_events("not a list") == []


def test_discovery_prompt_lists_only_cues_inside_the_chunk():
    words = load_words()
    chunk = ops.chunk_words(words, max_words=80, overlap_words=10)[0]
    inside = {"text": "(laughter)", "start": chunk["start_time"], "end": chunk["start_time"] + 0.5}
    outside = {"text": "(thunder)", "start": chunk["end_time"] + 30, "end": chunk["end_time"] + 31}
    prompt = ops.build_discovery_prompt(
        chunk, words, video_duration=words[-1]["end"],
        min_duration=15, max_duration=60, target_count=8,
        audio_events=[inside, outside],
    )
    assert "## AUDIO CUES" in prompt
    assert "(laughter)" in prompt
    assert "(thunder)" not in prompt
    assert "Do NOT copy cue text" in prompt


def test_discovery_prompt_handles_untagged_transcripts():
    words = load_words()
    chunk = ops.chunk_words(words, max_words=1000)[0]
    prompt = ops.build_discovery_prompt(
        chunk, words, video_duration=words[-1]["end"],
        min_duration=15, max_duration=60, target_count=8,
    )
    assert "(none tagged)" in prompt


def test_review_prompt_attaches_cues_to_the_matching_candidate_only():
    words = load_words()
    cand = candidate({"word_start_index": 10, "word_end_index": 60, "why": "why"}, words)
    inside = {"text": "(crowd-cheer)", "start": words[20]["start"], "end": words[20]["end"]}
    outside = {"text": "(thunder)", "start": words[-1]["start"], "end": words[-1]["end"]}
    prompt, _ = ops.build_review_prompt(
        [cand], words, video_duration=words[-1]["end"],
        audio_events=[inside, outside],
    )
    assert "audio_cues:" in prompt
    assert "(crowd-cheer)" in prompt
    assert "(thunder)" not in prompt
    assert "AUDIO CUES" in prompt

