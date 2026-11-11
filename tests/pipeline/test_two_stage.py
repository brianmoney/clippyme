"""Orchestration tests for two-stage discovery → review → copy."""
import json
from pathlib import Path

import pytest

from clippyme.pipeline import two_stage
from clippyme.pipeline import two_stage_ops as ops

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "viral_transcript.json"


def transcript():
    return json.loads(FIXTURE.read_text())


def json_block(payload):
    return "### JSON ###\n" + json.dumps(payload)


def candidate_payload():
    return {
        "candidates": [
            {
                "word_start_index": 10, "word_end_index": 60,
                "excerpt": "the bank account was negative forty thousand",
                "opening_line": "We had twelve thousand dollars left",
                "strongest_quote": "the number was negative forty thousand",
                "why": "A concrete number, a reversal, and a clear payoff.",
                "category": "story",
                "boundary_alternatives": [],
            },
            {
                "word_start_index": 150, "word_end_index": 210,
                "excerpt": "half the product was actually useful",
                "opening_line": "I deleted half the product",
                "strongest_quote": "Revenue went up nineteen percent",
                "why": "Counterintuitive product decision with a measurable result.",
                "category": "counterintuitive",
                "boundary_alternatives": [],
            },
        ]
    }


def review_payload(ids, qualities=(9.0, 5.0), selected=(True, False)):
    evaluations = []
    for cid, quality, keep in zip(ids, qualities, selected):
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
            "selected": keep,
            "rejection_reason": "" if keep else "competent but not compelling",
            "why_clip": "Concrete reveal with a measurable payoff.",
            "opening_strength": "A specific number in the first line.",
            "viewer_payoff": "The reversal and the result.",
            "payoff_quote": "Revenue went up nineteen percent",
            "weaknesses": "Some setup before the reveal.",
        })
    return {"evaluations": evaluations, "pairwise": [], "shortlist": [ids[0]]}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("VIRAL_PROVIDER", "openai")
    monkeypatch.setenv("VIRAL_API_KEY", "test-key")
    monkeypatch.setenv("VIRAL_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("VIRAL_MODE", "two_stage")
    monkeypatch.setenv("VIRAL_MAX_CANDIDATES", "10")
    monkeypatch.setenv("VIRAL_MAX_SELECTED_CLIPS", "5")
    monkeypatch.setenv("VIRAL_MIN_QUALITY", "6")
    monkeypatch.setenv("VIRAL_DISCOVERY_MODEL", ops.DISCOVERY_MODEL)
    monkeypatch.setenv("VIRAL_REVIEW_MODEL", ops.REVIEW_MODEL)
    monkeypatch.setenv("VIRAL_COPY_MODEL", "")
    monkeypatch.setenv("VIRAL_TWO_STAGE_FALLBACK", "single")
    monkeypatch.delenv("CLIPPYME_FORCE_VIRAL_ANALYSIS", raising=False)
    monkeypatch.delenv("CLIPPYME_CREATOR_NAME", raising=False)
    return monkeypatch


class FakeModels:
    def __init__(self):
        self.calls = []
        self.discovery_payload = candidate_payload()
        self.review_error = None
        self.selected = (True, False)
        self.qualities = (9.0, 5.0)

    def __call__(self, prompt, *, api_key, base_url, models, max_attempts, log_fn=print):
        model = models[0]
        self.calls.append({"model": model, "models": list(models), "prompt": prompt})
        if "talent scout" in prompt:
            return {"text": json_block(self.discovery_payload), "model": model,
                    "input_tokens": 100, "output_tokens": 20, "endpoint": "chat"}
        if "final gatekeeper" in prompt:
            if self.review_error:
                raise self.review_error
            ids = [c["id"] for c in self._candidates]
            return {"text": json_block(review_payload(ids, self.qualities, self.selected)),
                    "model": model, "input_tokens": 500, "output_tokens": 80,
                    "endpoint": "messages"}
        if "promotional copy" in prompt:
            ids = [c["id"] for c in self._candidates]
            return {"text": json_block({"copies": [
                {"candidate_id": cid, "video_title_for_youtube_short": "The number was negative",
                 "video_description_for_tiktok": "Would you have checked the bank account?",
                 "video_description_for_instagram": "Would you have checked the bank account?",
                 "viral_hook_text": "Negative forty thousand"} for cid in ids
            ]}), "model": model, "input_tokens": 300, "output_tokens": 50,
                "endpoint": "chat"}
        raise AssertionError(f"unexpected prompt: {prompt[:80]}")

    def install(self, monkeypatch):
        monkeypatch.setattr(two_stage.viral_ai, "generate_text", self)
        return self


def test_resolve_mode(monkeypatch):
    monkeypatch.setenv("VIRAL_MODE", "auto")
    assert two_stage.resolve_mode("openai") == "two_stage"
    assert two_stage.resolve_mode("gemini") == "single"
    monkeypatch.setenv("VIRAL_MODE", "single")
    assert two_stage.resolve_mode("openai") == "single"
    monkeypatch.setenv("VIRAL_MODE", "two_stage")
    assert two_stage.resolve_mode("gemini") == "two_stage"


def test_run_two_stage_end_to_end_and_artifacts(env, tmp_path):
    fake = FakeModels()
    # Discover the candidate ids the fake will see during review.
    from clippyme.pipeline.cut_ops import flatten_words
    words = flatten_words(transcript())
    fake._candidates = [
        ops.normalize_candidate(raw, words, words[-1]["end"], min_duration=10, max_duration=80)
        for raw in candidate_payload()["candidates"]
    ]
    fake.install(env)

    out = tmp_path / "job"
    result = two_stage.run_two_stage(
        transcript(), words[-1]["end"], output_dir=str(out),
        video_title="Test transcript", min_duration=10, max_duration=80,
    )

    assert result["two_stage"]["mode"] == "two_stage"
    assert len(result["shorts"]) == 1  # second candidate below quality floor
    clip = result["shorts"][0]
    from clippyme.schemas import ViralClip
    ViralClip.model_validate({k: v for k, v in clip.items() if k in ViralClip.model_fields})
    assert clip["candidate_id"] == fake._candidates[0]["id"]
    assert clip["review"]["quality_score"] == 9.0
    assert clip["viral_score"] == 90
    assert clip["video_title_for_youtube_short"] == "The number was negative"

    models = [call["model"] for call in fake.calls]
    assert models[0] == ops.DISCOVERY_MODEL
    assert ops.REVIEW_MODEL in models
    # Fallback chains are passed through to viral_ai (GLM flash → flashx).
    assert "z-ai/glm-5.3-flashx" in fake.calls[0]["models"]
    # Copy reuses the discovery model when VIRAL_COPY_MODEL is blank.
    assert models[-1] == ops.DISCOVERY_MODEL

    for name in ("two_stage_discovery.json", "two_stage_review.json",
                 "two_stage_copy.json", "two_stage_report.json"):
        assert (out / name).exists(), name

    report = result["two_stage"]
    assert report["review"]["evaluated"] == 2
    assert report["review"]["selected"] == 1
    assert report["review"]["rejected"] == 1
    assert report["totals"]["input_tokens"] > 0
    assert report["totals"]["cost_usd"] is None
    assert report["totals"]["pricing_known"] is False
    assert result["cost_analysis"]["total_cost"] is None
    # Review prompt must include the real transcript, not only GLM descriptions.
    review_prompt = next(c["prompt"] for c in fake.calls if "final gatekeeper" in c["prompt"])
    assert "negative forty thousand" in review_prompt


def test_run_two_stage_reuses_artifacts_without_new_calls(env, tmp_path):
    fake = FakeModels()
    from clippyme.pipeline.cut_ops import flatten_words
    words = flatten_words(transcript())
    fake._candidates = [
        ops.normalize_candidate(raw, words, words[-1]["end"], min_duration=10, max_duration=80)
        for raw in candidate_payload()["candidates"]
    ]
    fake.install(env)
    out = tmp_path / "job"
    two_stage.run_two_stage(transcript(), words[-1]["end"], output_dir=str(out),
                            min_duration=10, max_duration=80)
    first_calls = len(fake.calls)
    assert first_calls > 0

    def boom(*args, **kwargs):
        raise AssertionError("all stages should have been reused")

    env.setattr(two_stage.viral_ai, "generate_text", boom)
    result = two_stage.run_two_stage(transcript(), words[-1]["end"], output_dir=str(out),
                                     min_duration=10, max_duration=80)
    assert len(result["shorts"]) == 1


def test_review_failure_preserves_discovery_for_retry(env, tmp_path):
    fake = FakeModels()
    from clippyme.pipeline.cut_ops import flatten_words
    words = flatten_words(transcript())
    fake._candidates = [
        ops.normalize_candidate(raw, words, words[-1]["end"], min_duration=10, max_duration=80)
        for raw in candidate_payload()["candidates"]
    ]
    fake.review_error = RuntimeError("review exploded")
    fake.install(env)
    out = tmp_path / "job"
    with pytest.raises(two_stage.TwoStageError):
        two_stage.run_two_stage(transcript(), words[-1]["end"], output_dir=str(out),
                                min_duration=10, max_duration=80)
    assert (out / "two_stage_discovery.json").exists()
    assert (out / "two_stage_report.json").exists()
    assert (out / "two_stage_report.json").read_text().find("review_error") != -1
    discovery_calls = sum(1 for c in fake.calls if "talent scout" in c["prompt"])
    assert discovery_calls == 1

    fake.review_error = None
    result = two_stage.run_two_stage(transcript(), words[-1]["end"], output_dir=str(out),
                                     min_duration=10, max_duration=80)
    assert len(result["shorts"]) == 1
    assert sum(1 for c in fake.calls if "talent scout" in c["prompt"]) == 1


def test_review_can_reject_all_candidates(env, tmp_path):
    fake = FakeModels()
    from clippyme.pipeline.cut_ops import flatten_words
    words = flatten_words(transcript())
    fake._candidates = [
        ops.normalize_candidate(raw, words, words[-1]["end"], min_duration=10, max_duration=80)
        for raw in candidate_payload()["candidates"]
    ]
    fake.selected = (False, False)
    fake.install(env)
    result = two_stage.run_two_stage(transcript(), words[-1]["end"],
                                     output_dir=str(tmp_path / "job"),
                                     min_duration=10, max_duration=80)
    assert result["shorts"] == []
    assert result["reviewed_empty"] is True
    assert result["two_stage"]["reviewed_empty"] is True


def test_target_clips_caps_review_and_reaches_prompts(env, tmp_path):
    fake = FakeModels()
    from clippyme.pipeline.cut_ops import flatten_words
    words = flatten_words(transcript())
    fake._candidates = [
        ops.normalize_candidate(raw, words, words[-1]["end"], min_duration=10, max_duration=80)
        for raw in candidate_payload()["candidates"]
    ]
    fake.qualities = (9.0, 8.0)
    fake.selected = (True, True)
    fake.install(env)
    out = tmp_path / "job"
    result = two_stage.run_two_stage(
        transcript(), words[-1]["end"], output_dir=str(out),
        min_duration=10, max_duration=80, target_clips=1,
    )
    assert len(result["shorts"]) == 1
    discovery_prompt = next(c["prompt"] for c in fake.calls if "talent scout" in c["prompt"])
    review_prompt = next(c["prompt"] for c in fake.calls if "final gatekeeper" in c["prompt"])
    assert "EDITOR TARGET: about 1 final clip" in discovery_prompt
    assert "EDITOR TARGET: about 1 final clip" in review_prompt


def test_discovery_failure_raises(env, tmp_path):
    fake = FakeModels()
    fake.install(env)

    def fail(*args, **kwargs):
        raise RuntimeError("provider down")

    env.setattr(two_stage.viral_ai, "generate_text", fail)
    with pytest.raises(two_stage.TwoStageError):
        two_stage.run_two_stage(transcript(), transcript()["duration"],
                                output_dir=str(tmp_path / "job"))


# --- audio cues (ElevenLabs Scribe audio_events) -----------------------------

def test_transcript_hash_changes_with_audio_events():
    from clippyme.pipeline.cut_ops import flatten_words
    words = flatten_words(transcript())
    base = two_stage._transcript_hash(words)
    event = {"text": "(laughter)", "start": words[10]["start"], "end": words[10]["end"]}
    tagged = two_stage._transcript_hash(words, [event])
    assert tagged != base
    assert tagged == two_stage._transcript_hash(words, [dict(event)])


def test_audio_events_reach_discovery_and_review_prompts(env, tmp_path):
    fake = FakeModels()
    from clippyme.pipeline.cut_ops import flatten_words
    data = transcript()
    words = flatten_words(data)
    fake._candidates = [
        ops.normalize_candidate(raw, words, words[-1]["end"], min_duration=10, max_duration=80)
        for raw in candidate_payload()["candidates"]
    ]
    fake.install(env)
    data = {
        **data,
        "audio_events": [
            {"text": "(laughter)", "start": words[10]["start"], "end": words[10]["end"]},
            {"text": "(applause)", "start": words[-1]["start"], "end": words[-1]["end"]},
        ],
    }
    result = two_stage.run_two_stage(
        data, words[-1]["end"], output_dir=str(tmp_path / "job"),
        min_duration=10, max_duration=80,
    )
    discovery_prompt = next(c["prompt"] for c in fake.calls if "talent scout" in c["prompt"])
    review_prompt = next(c["prompt"] for c in fake.calls if "final gatekeeper" in c["prompt"])
    assert "## AUDIO CUES" in discovery_prompt
    assert "(laughter)" in discovery_prompt
    assert "audio_cues:" in review_prompt
    assert "(laughter)" in review_prompt
    # The cue is inside candidate 0, so it must not appear in candidate 1's block.
    first_block, second_block = review_prompt.split("CANDIDATE vc_", 2)[1:]
    assert "(laughter)" in first_block
    assert "(laughter)" not in second_block
    assert result["two_stage"]["audio_events"]["count"] == 2
    assert "(laughter)" in result["two_stage"]["audio_events"]["types"]
    assert (tmp_path / "job" / "two_stage_report.json").exists()


def test_audio_events_invalidate_cached_discovery(env, tmp_path):
    fake = FakeModels()
    from clippyme.pipeline.cut_ops import flatten_words
    data = transcript()
    words = flatten_words(data)
    fake._candidates = [
        ops.normalize_candidate(raw, words, words[-1]["end"], min_duration=10, max_duration=80)
        for raw in candidate_payload()["candidates"]
    ]
    fake.install(env)
    out = tmp_path / "job"
    two_stage.run_two_stage(data, words[-1]["end"], output_dir=str(out),
                            min_duration=10, max_duration=80)
    assert sum(1 for c in fake.calls if "talent scout" in c["prompt"]) == 1

    tagged = {
        **data,
        "audio_events": [
            {"text": "(laughter)", "start": words[10]["start"], "end": words[10]["end"]},
        ],
    }
    two_stage.run_two_stage(tagged, words[-1]["end"], output_dir=str(out),
                            min_duration=10, max_duration=80)
    # New evidence ⇒ the cached discovery candidates must be re-discovered,
    # not silently reused as if the transcript were unchanged.
    assert sum(1 for c in fake.calls if "talent scout" in c["prompt"]) == 2

