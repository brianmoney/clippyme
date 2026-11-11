"""Two-stage viral selection orchestrator (discovery → editorial review).

Stage 1 uses a high-recall model (default GLM-5.3 Flash) to propose candidates,
including chunked discovery with overlap for long transcripts. Stage 2 uses an
independent reviewer (default Claude Opus 5.5) to score and rank them before any
promotional copy is generated.

Network calls reuse :mod:`clippyme.pipeline.viral_ai` (the existing CommandCode /
OpenAI-compatible integration). Pure prompt/validation/ranking logic lives in
:mod:`clippyme.pipeline.two_stage_ops` so it is host-testable.

Every stage writes an artifact into the job output directory. A retry can reuse
discovery/review/copy artifacts instead of paying for earlier stages again.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import time
from typing import Any

from clippyme.pipeline import two_stage_ops as ops
from clippyme.pipeline import viral_ai
from clippyme.pipeline.cut_ops import flatten_words

DISCOVERY_ARTIFACT = "two_stage_discovery.json"
REVIEW_ARTIFACT = "two_stage_review.json"
COPY_ARTIFACT = "two_stage_copy.json"
REPORT_ARTIFACT = "two_stage_report.json"

DEFAULT_DISCOVERY_FALLBACKS = "z-ai/glm-5.3-flashx"
DEFAULT_REVIEW_FALLBACKS = "claude-sonnet-5-5"

_DISCOVERY_SHAPE = (
    '{"candidates": [{"word_start_index": <int>, "word_end_index": <int>, '
    '"start": <float>, "end": <float>, "excerpt": "<str>", '
    '"opening_line": "<str>", "strongest_quote": "<str>", "why": "<str>", '
    '"category": "<category>", "boundary_alternatives": []}]}'
)
_REVIEW_SHAPE = (
    '{"evaluations": [{"candidate_id": "<id>", "scores": {'
    + ", ".join(f'"{key}": <0-10>' for key in ops.RUBRIC_KEYS)
    + '}, "payoff_too_late": <bool>, "requires_background": <bool>, '
    '"better_opening_exists": <bool>, "shorter_edit_better": <bool>, '
    '"duplicate_of": <id or null>, "hook_misrepresents": <bool>, '
    '"suggested_word_start_index": <int or null>, '
    '"suggested_word_end_index": <int or null>, "boundary_note": "<str>", '
    '"selected": <bool>, "rejection_reason": "<str>", "why_clip": "<str>", '
    '"opening_strength": "<str>", "viewer_payoff": "<str>", '
    '"payoff_quote": "<str>", "weaknesses": "<str>"}], '
    '"pairwise": [{"a": "<id>", "b": "<id>", "winner": "<id>", "reason": "<str>"}], '
    '"shortlist": ["<id>"]}'
)
_COPY_SHAPE = (
    '{"copies": [{"candidate_id": "<id>", '
    '"video_title_for_youtube_short": "<max 100 chars>", '
    '"video_description_for_tiktok": "<str>", '
    '"video_description_for_instagram": "<str>", '
    '"viral_hook_text": "<3-8 words>"}]}'
)


class TwoStageError(RuntimeError):
    """A stage could not produce usable output; the caller may fall back."""


def _str_setting(name: str, default: str = "") -> str:
    return str(os.getenv(name) or default).strip()


def _int_setting(name: str, default: int, *, lo: int | None = None,
                 hi: int | None = None) -> int:
    try:
        value = int(_str_setting(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    if lo is not None:
        value = max(lo, value)
    if hi is not None:
        value = min(hi, value)
    return value


def _float_setting(name: str, default: float, *, lo: float | None = None,
                   hi: float | None = None) -> float:
    try:
        value = float(_str_setting(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    if lo is not None:
        value = max(lo, value)
    if hi is not None:
        value = min(hi, value)
    return value


def resolve_mode(provider: str) -> str:
    """Resolve VIRAL_MODE=auto to two-stage only on the CommandCode provider."""
    mode = _str_setting("VIRAL_MODE", "auto").lower()
    if mode not in ("auto", "single", "two_stage"):
        mode = "auto"
    if mode == "auto":
        return "two_stage" if provider == "openai" else "single"
    return mode


def _write_json(path: str, payload: dict) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".two-stage-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
        tmp = ""
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


def _read_json(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _transcript_hash(words: list[dict], audio_events: list[dict] | None = None) -> str:
    """Hash the evidence the discovery artifact was built from.

    Audio events are part of the selection input now, so a transcript that gains
    (or loses) provider-tagged cues must invalidate a cached discovery run.
    Untagged transcripts keep their historical hash, preserving old caches.
    """
    digest = hashlib.sha1()
    for word in words:
        digest.update(
            f"{word.get('word', '')}|{word['start']}|{word['end']};".encode("utf-8")
        )
    for event in audio_events or []:
        digest.update(
            f"#audio#{event.get('text', '')}|{event.get('start')}|{event.get('end')};"
            .encode("utf-8")
        )
    return digest.hexdigest()


def _ids_hash(values: list[str]) -> str:
    return hashlib.sha1("|".join(values).encode("utf-8")).hexdigest()


class _StageUsage:
    def __init__(self, model: str, stage: str):
        self.model = model
        self.stage = stage
        self.used_models: list[str] = []
        self.requests = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.latency_s = 0.0
        self.errors: list[str] = []

    def add(self, result: dict, latency_s: float) -> None:
        self.requests += 1
        self.input_tokens += int(result.get("input_tokens") or 0)
        self.output_tokens += int(result.get("output_tokens") or 0)
        self.latency_s += float(latency_s)
        used = result.get("model")
        if used and used not in self.used_models:
            self.used_models.append(used)

    def to_dict(self) -> dict:
        return {
            "stage": self.stage,
            "model": self.model,
            "used_models": self.used_models or [self.model],
            "requests": self.requests,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_s": round(self.latency_s, 3),
            # CommandCode does not publish per-model prices in the API, so an
            # unknown cost is None (NOT zero) and explicitly marked unknown.
            "cost_usd": None,
            "pricing_known": False,
            "errors": list(self.errors),
        }


def _call_stage(
    prompt: str,
    *,
    models: list[str],
    stage: str,
    usage: _StageUsage,
    api_key: str,
    base_url: str,
    max_attempts: int,
    log_fn=print,
) -> dict:
    started = time.monotonic()
    try:
        result = viral_ai.generate_text(
            prompt,
            api_key=api_key,
            base_url=base_url,
            models=models,
            max_attempts=max_attempts,
            log_fn=log_fn,
        )
    except Exception as exc:  # noqa: BLE001 - surface as a stage error
        usage.errors.append(str(exc)[:300])
        raise TwoStageError(
            f"{stage} model chain {', '.join(models)} failed: {exc}"
        ) from exc
    usage.add(result, time.monotonic() - started)
    used = result.get("model")
    if used and used != models[0]:
        log_fn(
            f"⚠️  {stage}: requested model {models[0]} was unavailable; "
            f"used {used} instead (recorded in the report)"
        )
    return result


def _call_with_parse(
    prompt: str,
    *,
    models: list[str],
    stage: str,
    shape_hint: str,
    usage: _StageUsage,
    api_key: str,
    base_url: str,
    max_attempts: int,
    log_fn=print,
) -> tuple[str, Any]:
    result = _call_stage(
        prompt, models=models, stage=stage, usage=usage,
        api_key=api_key, base_url=base_url, max_attempts=max_attempts,
        log_fn=log_fn,
    )
    text = result.get("text") or ""

    def retry(error: str) -> str:
        retry_prompt = ops.build_json_reformat_prompt(error, text, shape_hint)
        retry_result = _call_stage(
            retry_prompt, models=models, stage=f"{stage}-reformat", usage=usage,
            api_key=api_key, base_url=base_url, max_attempts=1, log_fn=log_fn,
        )
        return retry_result.get("text") or ""

    return text, retry


def _compute_window(min_duration: float | None, max_duration: float | None) -> tuple[float, float]:
    minimum = max(5.0, float(min_duration) if min_duration else 15.0)
    maximum = float(max_duration) if max_duration else max(60.0, minimum + 30.0)
    maximum = max(maximum, minimum + 5.0)
    return minimum, maximum


def _discovery_target_per_chunk(max_candidates: int, chunk_count: int) -> int:
    if chunk_count <= 0:
        return max_candidates
    return min(max_candidates, max(6, math.ceil(max_candidates / chunk_count) + 4))


def _run_discovery(
    words: list[dict],
    *,
    video_duration: float,
    min_duration: float,
    max_duration: float,
    max_candidates: int,
    instructions: str,
    creator: str,
    video_title: str,
    api_key: str,
    base_url: str,
    models: list[str],
    max_attempts: int,
    target_clips: int | None = None,
    audio_events: list[dict] | None = None,
    log_fn=print,
) -> tuple[list[dict], dict]:
    chunk_words = _int_setting("VIRAL_DISCOVERY_CHUNK_WORDS", 6000, lo=500, hi=40000)
    overlap_words = _int_setting("VIRAL_DISCOVERY_CHUNK_OVERLAP", 400, lo=0, hi=5000)
    overlap_words = min(overlap_words, chunk_words - 1)
    chunks = ops.chunk_words(words, max_words=chunk_words, overlap_words=overlap_words)
    per_chunk = _discovery_target_per_chunk(max_candidates, len(chunks))
    usage = _StageUsage(models[0], "discovery")

    all_candidates: list[dict] = []
    chunk_reports: list[dict] = []
    failed_chunks = 0
    for chunk in chunks:
        prompt = ops.build_discovery_prompt(
            chunk, words,
            video_duration=video_duration,
            min_duration=min_duration,
            max_duration=max_duration,
            target_count=per_chunk,
            instructions=instructions,
            creator=creator,
            video_title=video_title,
            target_clips=target_clips,
            audio_events=audio_events,
        )
        try:
            text, retry = _call_with_parse(
                prompt, models=models, stage="discovery", shape_hint=_DISCOVERY_SHAPE,
                usage=usage, api_key=api_key, base_url=base_url,
                max_attempts=max_attempts, log_fn=log_fn,
            )
            candidates, stats = ops.parse_discovery_response(
                text, words, video_duration,
                min_duration=min_duration, max_duration=max_duration,
                chunk_index=chunk["chunk_index"], retry_fn=retry,
            )
        except TwoStageError as exc:
            failed_chunks += 1
            chunk_reports.append({
                "chunk_index": chunk["chunk_index"],
                "start_index": chunk["start_index"],
                "end_index": chunk["end_index"],
                "error": str(exc),
            })
            log_fn(f"⚠️  Discovery chunk {chunk['chunk_index']} failed: {exc}")
            continue
        if not candidates and stats.get("error"):
            failed_chunks += 1
            chunk_reports.append({
                "chunk_index": chunk["chunk_index"],
                "start_index": chunk["start_index"],
                "end_index": chunk["end_index"],
                "error": stats["error"],
            })
            log_fn(
                f"⚠️  Discovery chunk {chunk['chunk_index']} produced no valid "
                f"candidates: {stats['error']}"
            )
            continue
        all_candidates.extend(candidates)
        chunk_reports.append({
            "chunk_index": chunk["chunk_index"],
            "start_index": chunk["start_index"],
            "end_index": chunk["end_index"],
            "start_time": round(chunk["start_time"], 3),
            "end_time": round(chunk["end_time"], 3),
            "target": per_chunk,
            "accepted": stats["accepted"],
            "rejected": stats["rejected"],
            "parse_path": stats["parse_path"],
            **({"error": stats["error"]} if stats.get("error") else {}),
        })
        log_fn(
            f"🔎 Discovery chunk {chunk['chunk_index'] + 1}/{len(chunks)}: "
            f"{stats['accepted']} candidates"
        )

    if failed_chunks == len(chunks):
        raise TwoStageError("all discovery chunks failed")

    consolidated, stats = ops.consolidate_candidates(all_candidates, max_candidates)
    log_fn(
        f"🧭 Discovery consolidated {stats['input']} → {stats['final']} candidates "
        f"({stats['duplicates_removed']} duplicates, {stats['capped_out']} capped)"
    )
    return consolidated, {
        "chunks": chunk_reports,
        "chunk_count": len(chunks),
        "failed_chunks": failed_chunks,
        "target_per_chunk": per_chunk,
        "consolidation": stats,
        "usage": usage.to_dict(),
    }


def _run_review(
    candidates: list[dict],
    words: list[dict],
    *,
    video_duration: float,
    instructions: str,
    min_quality: float,
    max_selected: int,
    api_key: str,
    base_url: str,
    models: list[str],
    max_attempts: int,
    target_clips: int | None = None,
    audio_events: list[dict] | None = None,
    log_fn=print,
) -> tuple[dict, dict]:
    max_words = _int_setting("VIRAL_REVIEW_MAX_WORDS", 0, lo=0)
    prompt, coverage = ops.build_review_prompt(
        candidates, words,
        video_duration=video_duration,
        instructions=instructions,
        max_words=max_words or None,
        target_clips=target_clips,
        audio_events=audio_events,
    )
    usage = _StageUsage(models[0], "review")
    text, retry = _call_with_parse(
        prompt, models=models, stage="review", shape_hint=_REVIEW_SHAPE,
        usage=usage, api_key=api_key, base_url=base_url,
        max_attempts=max_attempts, log_fn=log_fn,
    )
    result = ops.parse_review_response(
        text, candidates, min_quality=min_quality,
        max_selected=max_selected, retry_fn=retry,
    )
    log_fn(
        f"🎬 Review: {result['stats']['evaluated']} evaluated, "
        f"{result['stats']['selected']} selected, {result['stats']['rejected']} rejected"
    )
    return result, {"usage": usage.to_dict(), "coverage": coverage}


def _run_copy(
    selected: list[dict],
    *,
    instructions: str,
    video_title: str,
    api_key: str,
    base_url: str,
    models: list[str],
    max_attempts: int,
    log_fn=print,
) -> tuple[dict[str, dict], dict, bool]:
    usage = _StageUsage(models[0], "copy")
    prompt = ops.build_copy_prompt(
        selected, instructions=instructions, video_title=video_title,
    )
    try:
        text, retry = _call_with_parse(
            prompt, models=models, stage="copy", shape_hint=_COPY_SHAPE,
            usage=usage, api_key=api_key, base_url=base_url,
            max_attempts=max_attempts, log_fn=log_fn,
        )
        copies = ops.parse_copy_response(text, selected)
        # A response that parsed but supplied nothing for known ids is not a
        # failure; per-clip fallback copy keeps the metadata grounded. Flag it
        # so the report does not claim the model wrote every clip.
        copy_fallback = any(bool(copy.get("_fallback")) for copy in copies.values())
        return copies, usage.to_dict(), copy_fallback
    except TwoStageError as exc:
        log_fn(f"⚠️  Copy generation failed ({exc}); using grounded fallback copy")
        return {item["candidate_id"]: ops.fallback_copy(item["candidate"]) for item in selected}, usage.to_dict(), True


def _combined_cost(stage_metrics: list[dict]) -> dict:
    input_tokens = sum(int(item.get("input_tokens") or 0) for item in stage_metrics)
    output_tokens = sum(int(item.get("output_tokens") or 0) for item in stage_metrics)
    models = " + ".join(
        sorted({item.get("model", "") for item in stage_metrics if item.get("model")})
    )
    return {
        "provider": "openai",
        "model": models,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "input_cost": None,
        "output_cost": None,
        "total_cost": None,
        "cost_usd": None,
        "pricing_known": False,
        "two_stage": True,
        "note": "CommandCode does not publish API pricing; costs are unknown, not zero.",
    }


def run_two_stage(
    transcript_result: dict,
    video_duration: float,
    *,
    instructions: str | None = None,
    min_duration: float | None = None,
    max_duration: float | None = None,
    target_clips: int | None = None,
    video_title: str = "",
    output_dir: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    log_fn=print,
) -> dict:
    """Run discovery → review → copy and return the pipeline `clips_data` dict.

    Returns a dict with ``shorts`` plus a ``two_stage`` report. Raises
    :class:`TwoStageError` only when a stage fails; an editorial decision to
    select zero clips is a successful result with ``reviewed_empty=True``.
    """
    words = flatten_words(transcript_result)
    if not words:
        raise TwoStageError("empty transcript (no timed words)")
    # Provider-tagged non-speech cues (ElevenLabs Scribe): part of the selection
    # evidence, passed to both prompts and into the artifact hash so a cached
    # discovery run cannot survive a transcript that changed its tags.
    audio_events = ops.normalize_audio_events(transcript_result.get("audio_events"))
    if audio_events:
        log_fn(
            f"🎧 Audio cues: {len(audio_events)} provider-tagged event(s) "
            "will inform discovery and review"
        )
    else:
        log_fn("🎧 Audio cues: none tagged by the transcription provider")

    provider = _str_setting("VIRAL_PROVIDER", "gemini").lower()
    if provider != "openai":
        raise TwoStageError("two-stage mode requires VIRAL_PROVIDER=openai")

    api_key = api_key or _str_setting("VIRAL_API_KEY")
    if not api_key:
        raise TwoStageError("VIRAL_API_KEY is not configured")
    base_url = base_url or _str_setting("VIRAL_BASE_URL", viral_ai.DEFAULT_BASE_URL)

    discovery_model = _str_setting("VIRAL_DISCOVERY_MODEL", ops.DISCOVERY_MODEL)
    review_model = _str_setting("VIRAL_REVIEW_MODEL", ops.REVIEW_MODEL)
    copy_model = _str_setting("VIRAL_COPY_MODEL", ops.COPY_MODEL) or discovery_model
    discovery_chain = viral_ai.build_model_chain(
        discovery_model,
        _str_setting("VIRAL_DISCOVERY_FALLBACK_MODELS", DEFAULT_DISCOVERY_FALLBACKS),
    )
    review_chain = viral_ai.build_model_chain(
        review_model,
        _str_setting("VIRAL_REVIEW_FALLBACK_MODELS", DEFAULT_REVIEW_FALLBACKS),
    )
    copy_chain = viral_ai.build_model_chain(
        copy_model, _str_setting("VIRAL_COPY_FALLBACK_MODELS")
    )
    max_candidates = _int_setting("VIRAL_MAX_CANDIDATES", 32, lo=4, hi=60)
    max_selected = _int_setting("VIRAL_MAX_SELECTED_CLIPS", 8, lo=1, hi=20)
    try:
        target_clips = int(target_clips) if target_clips is not None else None
    except (TypeError, ValueError):
        target_clips = None
    if target_clips is not None:
        target_clips = max(1, min(20, target_clips))
        # A per-job target should steer both the review ceiling and discovery
        # cost; keep enough alternatives for the reviewer without paying for 32
        # candidates when the editor asked for 3.
        max_selected = target_clips
        max_candidates = min(max_candidates, max(8, target_clips * 3))
    min_quality = _float_setting("VIRAL_MIN_QUALITY", 6.0, lo=0.0, hi=10.0)
    max_attempts = _int_setting("VIRAL_STAGE_MAX_ATTEMPTS", 2, lo=1, hi=5)
    min_duration, max_duration = _compute_window(min_duration, max_duration)
    instructions = (instructions or "").strip()
    creator = _str_setting("CLIPPYME_CREATOR_NAME")
    transcript_hash = _transcript_hash(words, audio_events)
    force = _str_setting("CLIPPYME_FORCE_VIRAL_ANALYSIS", "").lower() in (
        "1", "true", "yes", "on",
    )

    discovery_path = os.path.join(output_dir, DISCOVERY_ARTIFACT) if output_dir else None
    review_path = os.path.join(output_dir, REVIEW_ARTIFACT) if output_dir else None
    copy_path = os.path.join(output_dir, COPY_ARTIFACT) if output_dir else None
    report_path = os.path.join(output_dir, REPORT_ARTIFACT) if output_dir else None

    discovery_artifact = None if force or not discovery_path else _read_json(discovery_path)
    reuse_discovery = bool(
        discovery_artifact
        and discovery_artifact.get("transcript_hash") == transcript_hash
        and discovery_artifact.get("discovery_model") == discovery_model
        and discovery_artifact.get("min_duration") == min_duration
        and discovery_artifact.get("max_duration") == max_duration
        and discovery_artifact.get("max_candidates") == max_candidates
        and discovery_artifact.get("target_clips") == target_clips
    )

    if reuse_discovery:
        candidates = discovery_artifact.get("candidates") or []
        discovery_metrics = discovery_artifact.get("metrics") or {}
        log_fn(f"♻️  Reusing {len(candidates)} discovered candidates from a previous attempt")
    else:
        candidates, discovery_metrics = _run_discovery(
            words,
            video_duration=video_duration,
            min_duration=min_duration,
            max_duration=max_duration,
            max_candidates=max_candidates,
            instructions=instructions,
            creator=creator,
            video_title=video_title,
            api_key=api_key,
            base_url=base_url,
            models=discovery_chain,
            max_attempts=max_attempts,
            target_clips=target_clips,
            audio_events=audio_events,
            log_fn=log_fn,
        )
        if discovery_path:
            _write_json(discovery_path, {
                "version": 1,
                "transcript_hash": transcript_hash,
                "discovery_model": discovery_model,
                "min_duration": min_duration,
                "max_duration": max_duration,
                "max_candidates": max_candidates,
                "target_clips": target_clips,
                "candidates": candidates,
                "metrics": discovery_metrics,
            })

    base_report = {
        "version": 1,
        "mode": "two_stage",
        "provider": "openai",
        "models": {
            "discovery": discovery_model,
            "review": review_model,
            "copy": copy_model,
        },
        "window": {"min_duration": min_duration, "max_duration": max_duration},
        "limits": {
            "max_candidates": max_candidates,
            "max_selected": max_selected,
            "target_clips": target_clips,
            "min_quality": min_quality,
            "stage_max_attempts": max_attempts,
        },
        "transcript": {
            "word_count": len(words),
            "duration": round(float(video_duration), 3),
            "sha1": transcript_hash,
        },
        "audio_events": {
            "count": len(audio_events),
            "tagged": bool(audio_events),
            "types": sorted({event["text"] for event in audio_events}),
        },
        "discovery": discovery_metrics,
        "artifacts": {
            "discovery": os.path.basename(discovery_path) if discovery_path else None,
            "review": os.path.basename(review_path) if review_path else None,
            "copy": os.path.basename(copy_path) if copy_path else None,
        },
        "reviewed_empty": False,
        "fallback_used": False,
        "cost_control": {
            "max_cost_usd": _float_setting("VIRAL_MAX_ANALYSIS_COST", 0.0, lo=0.0),
            "enforced": False,
            "reason": "provider pricing is unavailable; cost cap cannot be enforced",
        },
    }

    if not candidates:
        report = {
            **base_report,
            "reviewed_empty": True,
            "review": {
                "evaluated": 0, "selected": 0, "rejected": 0,
                "rejection_reasons": {}, "reason": "no valid candidates discovered",
            },
            "models_used": {
                "discovery": (discovery_metrics.get("usage") or {}).get("used_models"),
                "review": None, "copy": None,
            },
            "selections": [],
            "rejected": [],
            "usage": {"discovery": discovery_metrics.get("usage"), "review": None, "copy": None},
        }
        if report_path:
            _write_json(report_path, report)
        return {"shorts": [], "reviewed_empty": True, "two_stage": report,
                "cost_analysis": _combined_cost([discovery_metrics.get("usage") or {}])}

    candidates_hash = _ids_hash([candidate["id"] for candidate in candidates])
    audio_events_hash = _ids_hash([
        f"{event['text']}|{event['start']}|{event['end']}" for event in audio_events
    ])
    review_artifact = None if force or not review_path else _read_json(review_path)
    # Artifacts written before audio cues existed have no hash; they can only
    # ever represent an untagged transcript, so keep reusing them when the
    # current transcript has no events either.
    recorded_audio_hash = (
        review_artifact.get("audio_events_hash") if review_artifact else None
    )
    if recorded_audio_hash is None and not audio_events:
        recorded_audio_hash = audio_events_hash
    reuse_review = bool(
        review_artifact
        and review_artifact.get("candidates_hash") == candidates_hash
        and recorded_audio_hash == audio_events_hash
        and review_artifact.get("review_model") == review_model
        and review_artifact.get("min_quality") == min_quality
        and review_artifact.get("max_selected") == max_selected
    )

    if reuse_review:
        review_result = review_artifact.get("result") or {}
        review_metrics = review_artifact.get("metrics") or {}
        log_fn("♻️  Reusing previous editorial review (candidates unchanged)")
    else:
        try:
            review_result, review_metrics = _run_review(
                candidates, words,
                video_duration=video_duration,
                instructions=instructions,
                min_quality=min_quality,
                max_selected=max_selected,
                api_key=api_key,
                base_url=base_url,
                models=review_chain,
                max_attempts=max_attempts,
                target_clips=target_clips,
                audio_events=audio_events,
                log_fn=log_fn,
            )
        except TwoStageError as exc:
            base_report["review_error"] = str(exc)[:500]
            if report_path:
                _write_json(report_path, base_report)
            raise
        if review_path:
            _write_json(review_path, {
                "version": 1,
                "candidates_hash": candidates_hash,
                "audio_events_hash": audio_events_hash,
                "review_model": review_model,
                "min_quality": min_quality,
                "max_selected": max_selected,
                "result": review_result,
                "metrics": review_metrics,
            })

    evaluations = review_result.get("evaluations") or {}
    selected = review_result.get("selected") or []
    rejected = list(review_result.get("rejected") or [])

    if not selected:
        report = {
            **base_report,
            "reviewed_empty": True,
            "review": {
                **(review_result.get("stats") or {}),
                "pairwise": review_result.get("pairwise") or [],
                "coverage": review_metrics.get("coverage"),
                "usage": review_metrics.get("usage"),
            },
            "models_used": {
                "discovery": (discovery_metrics.get("usage") or {}).get("used_models"),
                "review": (review_metrics.get("usage") or {}).get("used_models"),
                "copy": None,
            },
            "selections": [],
            "rejected": rejected,
            "usage": {
                "discovery": discovery_metrics.get("usage"),
                "review": review_metrics.get("usage"),
                "copy": None,
            },
        }
        if report_path:
            _write_json(report_path, report)
        return {"shorts": [], "reviewed_empty": True, "two_stage": report,
                "cost_analysis": _combined_cost([
                    discovery_metrics.get("usage") or {},
                    review_metrics.get("usage") or {},
                ])}

    candidates_by_id = {candidate["id"]: candidate for candidate in candidates}
    refined = ops.refine_boundaries(
        selected, candidates_by_id, words,
        video_duration=video_duration,
        min_duration=min_duration,
        max_duration=max_duration,
    )
    refined, dropped_overlaps = ops.dedupe_final(refined)
    rejected.extend(dropped_overlaps)
    if not refined:
        report = {
            **base_report,
            "reviewed_empty": True,
            "review": {
                **(review_result.get("stats") or {}),
                "pairwise": review_result.get("pairwise") or [],
                "coverage": review_metrics.get("coverage"),
                "usage": review_metrics.get("usage"),
                "reason": "no selected clip had a valid boundary",
            },
            "models_used": {
                "discovery": (discovery_metrics.get("usage") or {}).get("used_models"),
                "review": (review_metrics.get("usage") or {}).get("used_models"),
                "copy": None,
            },
            "selections": [],
            "rejected": rejected,
        }
        if report_path:
            _write_json(report_path, report)
        return {"shorts": [], "reviewed_empty": True, "two_stage": report,
                "cost_analysis": _combined_cost([
                    discovery_metrics.get("usage") or {},
                    review_metrics.get("usage") or {},
                ])}
    for index, item in enumerate(refined, start=1):
        item["evaluation"]["rank"] = index

    selection_hash = _ids_hash([
        f"{item['candidate_id']}:{item['start']:.3f}:{item['end']:.3f}"
        for item in refined
    ])
    copy_artifact = None if force or not copy_path else _read_json(copy_path)
    reuse_copy = bool(
        copy_artifact
        and copy_artifact.get("selection_hash") == selection_hash
        and copy_artifact.get("copy_model") == copy_model
    )
    if reuse_copy:
        copies = copy_artifact.get("copies") or {}
        copy_metrics = copy_artifact.get("metrics") or {}
        copy_fallback = bool(copy_artifact.get("fallback"))
        log_fn("♻️  Reusing promotional copy from a previous attempt")
    else:
        copies, copy_metrics, copy_fallback = _run_copy(
            refined,
            instructions=instructions,
            video_title=video_title,
            api_key=api_key,
            base_url=base_url,
            models=copy_chain,
            max_attempts=max_attempts,
            log_fn=log_fn,
        )
        if copy_path:
            _write_json(copy_path, {
                "version": 1,
                "selection_hash": selection_hash,
                "copy_model": copy_model,
                "copies": copies,
                "metrics": copy_metrics,
                "fallback": copy_fallback,
            })

    discovery_used = ((discovery_metrics.get("usage") or {}).get("used_models") or [discovery_model])[-1]
    review_used = ((review_metrics.get("usage") or {}).get("used_models") or [review_model])[-1]
    copy_used = ((copy_metrics or {}).get("used_models") or [copy_model])[-1]
    clips = ops.build_final_clips(
        refined, copies,
        discovery_model=discovery_model,
        review_model=review_model,
        copy_model=copy_model,
        copy_fallback=copy_fallback,
        discovery_model_used=discovery_used,
        review_model_used=review_used,
        copy_model_used=copy_used,
    )

    selections = []
    for clip in clips:
        selections.append({
            "candidate_id": clip["candidate_id"],
            "rank": clip["review"]["rank"],
            "quality_score": clip["review"]["quality_score"],
            "viral_score": clip["viral_score"],
            "start": clip["start"],
            "end": clip["end"],
            "category": clip["discovery"]["category"],
            "reason": clip["viral_reason"],
            "payoff_quote": clip["review"].get("payoff_quote", ""),
            "weaknesses": clip["review"].get("weaknesses", ""),
        })

    stage_usages = [
        discovery_metrics.get("usage") or {},
        review_metrics.get("usage") or {},
        copy_metrics or {},
    ]
    report = {
        **base_report,
        "reviewed_empty": False,
        "review": {
            **(review_result.get("stats") or {}),
            "pairwise": review_result.get("pairwise") or [],
            "coverage": review_metrics.get("coverage"),
            "usage": review_metrics.get("usage"),
        },
        "copy": {"usage": copy_metrics, "fallback": copy_fallback},
        "models_used": {
            "discovery": (discovery_metrics.get("usage") or {}).get("used_models"),
            "review": (review_metrics.get("usage") or {}).get("used_models"),
            "copy": (copy_metrics or {}).get("used_models"),
        },
        "selections": selections,
        "rejected": rejected,
        "usage": {
            "discovery": discovery_metrics.get("usage"),
            "review": review_metrics.get("usage"),
            "copy": copy_metrics,
        },
        "totals": {
            "input_tokens": sum(int(u.get("input_tokens") or 0) for u in stage_usages),
            "output_tokens": sum(int(u.get("output_tokens") or 0) for u in stage_usages),
            "latency_s": round(sum(float(u.get("latency_s") or 0) for u in stage_usages), 3),
            "cost_usd": None,
            "pricing_known": False,
        },
    }
    if report_path:
        _write_json(report_path, report)
    return {
        "shorts": clips,
        "two_stage": report,
        "cost_analysis": _combined_cost(stage_usages),
    }
