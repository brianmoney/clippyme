"""Pure logic for two-stage viral clip selection.

Stage 1 (discovery) proposes broad, recall-oriented candidates. Stage 2
(review) independently scores and ranks them, then we refine boundaries and
generate promotional copy only for the survivors.

Everything in this module is string/dict math over the transcript word list —
no network, no cv2, no ffmpeg — so it runs in the host test tier. The model
calls live in :mod:`clippyme.pipeline.two_stage`.
"""
from __future__ import annotations

import bisect
import hashlib
import re
from typing import Any

from clippyme.pipeline.cut_ops import snap_clip_to_words
from clippyme.pipeline.gemini_parser import parse_gemini_response

DISCOVERY_MODEL = "z-ai/glm-5.3-flash"
REVIEW_MODEL = "claude-opus-5-5"
COPY_MODEL = DISCOVERY_MODEL

JSON_HINT = "### JSON ###"

CANDIDATE_CATEGORIES = (
    "surprise",
    "emotion",
    "quote",
    "controversy",
    "counterintuitive",
    "story",
    "useful",
    "narrative",
    "comparison",
    "self_contained",
    "other",
)

RUBRIC_KEYS = (
    "opening_hook",
    "emotional_impact",
    "surprise_novelty",
    "shareability",
    "self_contained_clarity",
    "narrative_payoff",
    "retention_potential",
    "quotability",
    "information_value",
    "audience_relevance",
)

RUBRIC_LABELS = {
    "opening_hook": "Will the first 1-3 seconds attract attention?",
    "emotional_impact": "Does the moment provoke a genuine reaction?",
    "surprise_novelty": "Is the statement unexpected or memorable?",
    "shareability": "Would someone plausibly send this to another person?",
    "self_contained_clarity": "Does it make sense without the original video?",
    "narrative_payoff": "Does the clip deliver a satisfying conclusion or reveal?",
    "retention_potential": "Is the segment likely to maintain attention throughout?",
    "quotability": "Does it contain a line people might repeat or discuss?",
    "information_value": "Does it teach, explain, or reveal something useful?",
    "audience_relevance": "Is the subject likely to interest the intended audience?",
}

# Prompt-injection hygiene: strip the delimiter a model could echo to forge the
# JSON section, bound the length and fence user text as data.
def _fenced(text: str | None, limit: int = 2000) -> str:
    safe = str(text or "").replace(JSON_HINT, "").strip()[:limit]
    if not safe:
        return ""
    return f"<user_instructions>\n{safe}\n</user_instructions>"


def _truncate(text: str | None, limit: int) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def coerce_seconds(value: Any) -> float | None:
    """Best-effort seconds coercion (floats, dotted and colon time strings)."""
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        pass
    if ":" in raw:
        try:
            parts = raw.split(":")
            if len(parts) == 2:
                return float(parts[0]) * 60.0 + float(parts[1])
            if len(parts) == 3:
                return (
                    float(parts[0]) * 3600.0
                    + float(parts[1]) * 60.0
                    + float(parts[2])
                )
        except ValueError:
            return None
    if raw.count(".") >= 2:
        parts = raw.split(".")
        try:
            if len(parts) == 3:
                return float(parts[0]) * 60.0 + float(parts[1]) + float("0." + parts[2])
            if len(parts) == 4:
                return (
                    float(parts[0]) * 3600.0
                    + float(parts[1]) * 60.0
                    + float(parts[2])
                    + float("0." + parts[3])
                )
        except ValueError:
            return None
    return None


AUDIO_EVENT_LIMIT = 500
AUDIO_EVENT_TEXT_LIMIT = 48


def normalize_audio_events(raw_events: Any) -> list[dict]:
    """Normalise a transcript's provider-tagged non-speech events.

    ElevenLabs Scribe returns ``audio_events`` as ``{text, start, end}``
    dicts (e.g. ``(laughter)``, ``(applause)``, ``(music)``). The two-stage
    prompts need a small, deterministic timeline: drop malformed rows, clamp
    durations, strip prompt delimiters, dedupe and sort by time. This is a
    pure helper so both selection paths and the host tests share one shape.
    """
    if not isinstance(raw_events, list):
        return []
    cleaned: list[dict] = []
    seen: set[tuple[str, float, float]] = set()
    for raw in raw_events:
        if not isinstance(raw, dict):
            continue
        text = " ".join(
            str(raw.get("text") or raw.get("label") or raw.get("event") or "").split()
        ).replace(JSON_HINT, "").strip()
        if not text:
            continue
        start = coerce_seconds(raw.get("start"))
        end = coerce_seconds(raw.get("end"))
        if start is None and end is None:
            continue
        if start is None:
            start = end
        if end is None or end < start:
            end = start
        start = round(max(0.0, float(start)), 3)
        end = round(max(start, float(end)), 3)
        text = _truncate(text, AUDIO_EVENT_TEXT_LIMIT)
        key = (text, start, end)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append({"text": text, "start": start, "end": end})
        if len(cleaned) >= AUDIO_EVENT_LIMIT:
            break
    cleaned.sort(key=lambda event: (event["start"], event["end"], event["text"]))
    return cleaned


def audio_events_in_window(
    events: list[dict] | None,
    start: float,
    end: float,
    *,
    pad: float = 0.0,
) -> list[dict]:
    """Events whose span intersects ``[start, end]`` (optionally padded)."""
    if not events:
        return []
    low = float(start) - max(0.0, float(pad))
    high = float(end) + max(0.0, float(pad))
    return [
        event for event in events
        if float(event["end"]) >= low and float(event["start"]) <= high
    ]


def format_audio_events(events: list[dict] | None, *, limit: int = 24) -> str:
    """Render an event timeline as compact prompt lines.

    Always returns something so the prompt template has a stable section even
    when a provider (Deepgram/Whisper) didn't tag any events.
    """
    events = list(events or [])
    if not events:
        return "  (none tagged)"
    lines = [
        f"  - [{event['start']:.3f}-{event['end']:.3f}] {event['text']}"
        for event in events[: max(1, int(limit))]
    ]
    if len(events) > limit:
        lines.append(f"  - … {len(events) - limit} more events omitted")
    return "\n".join(lines)


def candidate_id(start: float, end: float, excerpt: str) -> str:
    """Stable, content-addressed id for a candidate moment."""
    seed = f"{float(start):.3f}|{float(end):.3f}|{_truncate(excerpt, 120)}"
    return "vc_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]


def chunk_words(
    words: list[dict],
    max_words: int = 6000,
    overlap_words: int = 400,
) -> list[dict]:
    """Split the word list into overlapping chunks that cover every word.

    Overlap keeps a promising moment from being hidden by a chunk boundary and
    gives the discovery model enough surrounding context. Chunk indices are
    global word indices so timestamps can be anchored exactly.
    """
    total = len(words)
    if total == 0:
        return []
    max_words = max(1, int(max_words))
    overlap_words = max(0, min(int(overlap_words), max_words - 1))
    if total <= max_words:
        return [{
            "chunk_index": 0,
            "start_index": 0,
            "end_index": total,
            "start_time": float(words[0]["start"]),
            "end_time": float(words[-1]["end"]),
        }]

    step = max_words - overlap_words
    chunks = []
    start = 0
    index = 0
    while start < total:
        end = min(start + max_words, total)
        chunks.append({
            "chunk_index": index,
            "start_index": start,
            "end_index": end,
            "start_time": float(words[start]["start"]),
            "end_time": float(words[end - 1]["end"]),
        })
        if end >= total:
            break
        start += step
        index += 1
    return chunks


def _word_text(word: dict) -> str:
    return str(word.get("word") or "")


def _span_text(words: list[dict], start_index: int, end_index: int) -> str:
    if start_index < 0 or end_index >= len(words) or end_index < start_index:
        return ""
    return " ".join(_word_text(w) for w in words[start_index:end_index + 1]).strip()


def _quote_word(value: str) -> str:
    from clippyme.pipeline.gemini_request import _toon_quote_word

    return _toon_quote_word(value)


def encode_indexed_words(words: list[dict], start_index: int, end_index: int) -> str:
    """TOON tabular words block with GLOBAL word indices (i,w,s,e)."""
    rows = [f"words[{end_index - start_index}]{{i,w,s,e}}:"]
    for index in range(start_index, end_index):
        word = words[index]
        rows.append(
            f"  {index},{_quote_word(_word_text(word))},"
            f"{word['start']},{word['end']}"
        )
    return "\n".join(rows)


def _nearest_word_start(target: float, words: list[dict], max_snap: float = 1.0):
    starts = [float(word["start"]) for word in words]
    pos = bisect.bisect_left(starts, target)
    candidates = [i for i in (pos - 1, pos) if 0 <= i < len(words)]
    best = None
    best_distance = max_snap
    for index in candidates:
        distance = abs(starts[index] - target)
        if distance <= best_distance:
            best_distance = distance
            best = index
    return best


def _nearest_word_end(target: float, words: list[dict], max_snap: float = 1.0):
    ends = [float(word["end"]) for word in words]
    pos = bisect.bisect_left(ends, target)
    candidates = [i for i in (pos - 1, pos) if 0 <= i < len(words)]
    best = None
    best_distance = max_snap
    for index in candidates:
        distance = abs(ends[index] - target)
        if distance <= best_distance:
            best_distance = distance
            best = index
    return best


def _first_sentence(text: str) -> str:
    match = re.search(r"^.{0,220}?[.!?](?:\s|$)", text)
    if match:
        return match.group(0).strip()
    return _truncate(text, 160)


def _longest_sentence(text: str) -> str:
    parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    return max(parts, key=len) if parts else _truncate(text, 180)


def _contains_fragment(haystack: str, needle: str) -> bool:
    def normalize(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()

    needle_norm = normalize(needle)
    return bool(needle_norm) and needle_norm in normalize(haystack)


def _normalize_alt(raw: Any, words: list[dict], video_duration: float | None):
    if not isinstance(raw, dict):
        return None
    start = coerce_seconds(raw.get("start"))
    end = coerce_seconds(raw.get("end"))
    si = raw.get("word_start_index")
    ei = raw.get("word_end_index")
    if isinstance(si, int) and isinstance(ei, int) and 0 <= si < ei < len(words):
        start, end = float(words[si]["start"]), float(words[ei]["end"])
    if start is None or end is None:
        return None
    if video_duration is not None:
        start = max(0.0, min(start, video_duration))
        end = max(0.0, min(end, video_duration))
    if end <= start:
        return None
    note = _truncate(raw.get("note") or raw.get("why") or "", 180)
    return {"start": round(start, 3), "end": round(end, 3), "note": note}


def normalize_candidate(
    raw: Any,
    words: list[dict],
    video_duration: float | None,
    *,
    min_duration: float = 15.0,
    max_duration: float = 60.0,
    chunk_index: int = 0,
    discovery_order: int = 0,
) -> dict | None:
    """Validate + anchor one discovery candidate to real transcript words.

    The model may propose indices or timestamps, but the application owns the
    final numbers: indices are authoritative, and timestamps are snapped to the
    nearest word boundary. Invented or wordless moments are rejected.
    """
    if not isinstance(raw, dict) or not words:
        return None

    start = coerce_seconds(raw.get("start"))
    end = coerce_seconds(raw.get("end"))
    si = raw.get("word_start_index")
    ei = raw.get("word_end_index")

    if isinstance(si, int) and isinstance(ei, int) and 0 <= si < len(words) and 0 <= ei < len(words):
        if si > ei:
            si, ei = ei, si
        start, end = float(words[si]["start"]), float(words[ei]["end"])
    else:
        if start is None or end is None:
            return None
        if video_duration is not None:
            start = max(0.0, min(start, video_duration))
            end = max(0.0, min(end, video_duration))
        if end <= start:
            return None
        si = _nearest_word_start(start, words)
        ei = _nearest_word_end(end, words)
        if si is None or ei is None:
            return None
        if si > ei:
            si, ei = ei, si
        start, end = float(words[si]["start"]), float(words[ei]["end"])

    if video_duration is not None and start >= video_duration:
        return None
    if end <= start:
        return None
    duration = end - start
    if duration < 4.0:
        return None
    # Absurdly long proposals are still reviewable but capped so they cannot
    # dominate the review context/cost.
    if duration > max(180.0, max_duration * 2.0):
        return None

    excerpt = _span_text(words, si, ei)
    if not excerpt:
        return None
    context_start = max(0, si - 45)
    context_end = min(len(words) - 1, ei + 45)
    context_before = _span_text(words, context_start, si - 1) if si > context_start else ""
    context_after = _span_text(words, ei + 1, context_end) if ei < context_end else ""

    raw_excerpt = str(raw.get("excerpt") or "").strip()
    excerpt_verified = bool(raw_excerpt) and _contains_fragment(excerpt, raw_excerpt)

    opening_line = str(raw.get("opening_line") or "").strip()
    if not opening_line or not _contains_fragment(excerpt, opening_line):
        opening_line = _first_sentence(excerpt)

    strongest = str(raw.get("strongest_quote") or "").strip()
    if not strongest or not _contains_fragment(excerpt, strongest):
        strongest = _longest_sentence(excerpt)

    category = str(raw.get("category") or "other").strip().lower()
    if category not in CANDIDATE_CATEGORIES:
        category = "other"

    alternatives = []
    for alt in (raw.get("boundary_alternatives") or [])[:3]:
        normalized = _normalize_alt(alt, words, video_duration)
        if normalized:
            alternatives.append(normalized)

    start = round(start, 3)
    end = round(end, 3)
    excerpt = _truncate(excerpt, 1200)
    return {
        "id": candidate_id(start, end, excerpt),
        "start": start,
        "end": end,
        "duration": round(end - start, 3),
        "word_start_index": si,
        "word_end_index": ei,
        "excerpt": excerpt,
        "excerpt_verified": excerpt_verified,
        "context_before": _truncate(context_before, 700),
        "context_after": _truncate(context_after, 700),
        "opening_line": _truncate(opening_line, 240),
        "strongest_quote": _truncate(strongest, 320),
        "why": _truncate(raw.get("why") or raw.get("rationale") or "", 600),
        "category": category,
        "boundary_alternatives": alternatives,
        "duration_valid": min_duration <= (end - start) <= max_duration,
        "chunk_index": chunk_index,
        "discovery_order": discovery_order,
    }


DISCOVERY_PROMPT = """You are a senior short-form video talent scout. Your job is RECALL: find every moment in this transcript that could genuinely work as a standalone TikTok / Reels / Shorts clip. A later senior editor will reject weak candidates, so a missed exceptional moment is worse than including a moderately promising one.

This is DISCOVERY ONLY. Do NOT write titles, captions, overlay hooks, or promotional copy. Do NOT assign a virality score.

## WHAT MAKES A CANDIDATE (look for at least one, strongly)
1. Surprising information, a revelation, or an unexpected turn.
2. Strong emotion: laughter, frustration, disbelief, excitement, indignation.
3. A memorable quote or an unusually clear explanation.
4. A controversial or strongly debatable opinion.
5. A counterintuitive claim that challenges an assumption.
6. A personal story with real stakes.
7. Useful information a viewer would save or share.
8. A narrative setup with a satisfying payoff.
9. An unusual comparison, statistic, or specific example.
10. A moment that makes sense without the original long video.

Do NOT equate outrage, profanity, or provocative wording with quality. The clip must give the audience genuine value.

## DISCOVERY RULES (hard)
- Candidate duration must be {min_duration}-{max_duration} seconds.
- Anchor every candidate to the transcript WORDS table:
  * set `word_start_index` to the `i` of the FIRST word of the opening sentence;
  * set `word_end_index` to the `i` of the LAST word of the closing sentence;
  * `start` must equal that first word's `s`, `end` must equal that last word's `e`.
- Never invent timestamps. Never round to whole seconds.
- Open on a natural sentence boundary and close after the payoff / reaction.
- The strongest line may sit in the middle of a longer discussion: you may start earlier for context or later to skip a slow intro.
- Overlapping candidates are allowed when they are meaningfully different editorial choices (different hook, different payoff, shorter vs fuller context).
- Prefer candidates that can be understood cold. Avoid generic discussion or filler.
- If the material does not support {target_count} strong candidates, return fewer. Do not pad.

## VIDEO CONTEXT
VIDEO_DURATION_SECONDS: {video_duration}
TOTAL_TRANSCRIPT_WORDS: {total_words}
CHUNK_WORD_RANGE: {start_index}..{end_index_exclusive}
{target_clips_block}
{creator_block}
{video_title_block}
{instructions_block}

## TRANSCRIPT TEXT (chunk)
{transcript_text}

## AUDIO CUES (provider-tagged non-speech events in this chunk)
Bracketed tags such as ``(laughter)``, ``(applause)``, ``(cheering)`` and
``(music)`` are real audio events, not spoken words. Timestamps are relative to
the source video.
- A candidate that ends just after a cue is stronger evidence of emotional
  payoff / a reaction beat — prefer including that reaction.
- Only use a cue when it coincides with the spoken excerpt. Ignore continuous
  background music that has no editorial meaning.
- Do NOT copy cue text into excerpt, opening_line, strongest_quote, why or
  category; cues are signal only.
{audio_events_block}

## WORDS (TOON, global indices `words[N]{{i,w,s,e}}:`, s/e seconds)
{words_block}

## OUTPUT CONTRACT
Think internally, then emit the literal delimiter `### JSON ###` on its own line, followed by ONLY this JSON object (no markdown, no prose):
{{
  "candidates": [
    {{
      "word_start_index": <int>,
      "word_end_index": <int>,
      "start": <float seconds>,
      "end": <float seconds>,
      "excerpt": "<exact words spoken in this range>",
      "opening_line": "<first spoken line>",
      "strongest_quote": "<the strongest exact quote from the range>",
      "why": "<specific reason this could perform>",
      "category": "<one of: surprise|emotion|quote|controversy|counterintuitive|story|useful|narrative|comparison|self_contained|other>",
      "boundary_alternatives": [
        {{"word_start_index": <int>, "word_end_index": <int>, "start": <float>, "end": <float>, "note": "<why this alternative>"}}
      ]
    }}
  ]
}}
"""


def build_discovery_prompt(
    chunk: dict,
    words: list[dict],
    *,
    video_duration: float,
    min_duration: float,
    max_duration: float,
    target_count: int,
    instructions: str = "",
    creator: str = "",
    video_title: str = "",
    target_clips: int | None = None,
    audio_events: list[dict] | None = None,
) -> str:
    start_index = chunk["start_index"]
    end_index = chunk["end_index"]
    audio_events = normalize_audio_events(audio_events)
    creator_block = (
        f"CHANNEL OWNER: {_truncate(creator, 64)} — this clip comes from their stream. "
        "It may be the SUBJECT of a later title, never evidence of who is speaking."
        if creator else ""
    )
    title_block = f"SOURCE TITLE: {_truncate(video_title, 160)}" if video_title else ""
    target_block = ""
    if target_clips:
        noun = "clip" if int(target_clips) == 1 else "clips"
        target_block = (
            f"EDITOR TARGET: about {int(target_clips)} final {noun}. Propose "
            "enough candidates (including meaningfully different alternatives) "
            "to support that target, but do not pad with filler."
        )
    return DISCOVERY_PROMPT.format(
        target_clips_block=target_block,
        min_duration=int(min_duration) if float(min_duration).is_integer() else min_duration,
        max_duration=int(max_duration) if float(max_duration).is_integer() else max_duration,
        target_count=target_count,
        video_duration=round(float(video_duration), 3),
        total_words=len(words),
        start_index=start_index,
        end_index_exclusive=end_index,
        creator_block=creator_block,
        video_title_block=title_block,
        instructions_block=_fenced(instructions),
        transcript_text=_span_text(words, start_index, end_index - 1),
        audio_events_block=format_audio_events(
            audio_events_in_window(
                audio_events,
                chunk.get("start_time", words[start_index]["start"]),
                chunk.get("end_time", words[end_index - 1]["end"]),
            )
        ),
        words_block=encode_indexed_words(words, start_index, end_index),
    )


def parse_discovery_response(
    text: str,
    words: list[dict],
    video_duration: float | None,
    *,
    min_duration: float = 15.0,
    max_duration: float = 60.0,
    chunk_index: int = 0,
    retry_fn=None,
) -> tuple[list[dict], dict]:
    """Parse and validate a discovery response. Returns (candidates, stats)."""
    result = parse_gemini_response(text, retry_fn=retry_fn, request_id="discovery")
    data = result.data
    stats = {
        "parse_path": result.parse_path,
        "raw_count": 0,
        "accepted": 0,
        "rejected": 0,
    }
    if not isinstance(data, dict):
        stats["error"] = result.error or "response was not a JSON object"
        return [], stats
    raw = data.get("candidates")
    if raw is None:
        raw = data.get("shorts")
    if not isinstance(raw, list):
        stats["error"] = "response had no candidates list"
        return [], stats
    stats["raw_count"] = len(raw)

    candidates = []
    for order, item in enumerate(raw):
        normalized = normalize_candidate(
            item, words, video_duration,
            min_duration=min_duration, max_duration=max_duration,
            chunk_index=chunk_index, discovery_order=order,
        )
        if normalized is None:
            stats["rejected"] += 1
        else:
            candidates.append(normalized)
    stats["accepted"] = len(candidates)
    return candidates, stats


def _overlap_ratio(a: dict, b: dict) -> float:
    inter = max(0.0, min(a["end"], b["end"]) - max(a["start"], b["start"]))
    union = max(a["end"], b["end"]) - min(a["start"], b["start"])
    return inter / union if union > 0 else 0.0


def consolidate_candidates(
    candidates: list[dict],
    max_candidates: int,
) -> tuple[list[dict], dict]:
    """Merge near-identical candidates, then fairly cap the global set.

    Candidates are grouped by chunk and selected round-robin so one long chunk
    cannot crowd out the rest of the video. Meaningfully different overlapping
    proposals are deliberately preserved for review.
    """
    kept: list[dict] = []
    duplicates = 0
    for candidate in sorted(candidates, key=lambda c: (c["start"], c["end"])):
        duplicate = False
        for existing in kept:
            same_start = abs(candidate["start"] - existing["start"]) <= 0.75
            same_end = abs(candidate["end"] - existing["end"]) <= 0.75
            if (same_start and same_end) or _overlap_ratio(candidate, existing) >= 0.92:
                duplicate = True
                break
        if duplicate:
            duplicates += 1
        else:
            kept.append(candidate)

    groups: dict[int, list[dict]] = {}
    for candidate in kept:
        groups.setdefault(int(candidate.get("chunk_index") or 0), []).append(candidate)
    for group in groups.values():
        group.sort(key=lambda c: c.get("discovery_order", 0))

    capped_out = 0
    if max_candidates and max_candidates > 0 and len(kept) > max_candidates:
        picked: list[dict] = []
        chunk_ids = sorted(groups)
        cursor = 0
        while len(picked) < max_candidates:
            progressed = False
            for chunk_id in chunk_ids:
                group = groups[chunk_id]
                if cursor < len(group):
                    picked.append(group[cursor])
                    progressed = True
                    if len(picked) >= max_candidates:
                        break
            if not progressed:
                break
            cursor += 1
        capped_out = len(kept) - len(picked)
        kept = picked

    kept.sort(key=lambda c: (c["start"], c["end"]))
    for index, candidate in enumerate(kept):
        candidate["discovery_rank"] = index + 1

    return kept, {
        "input": len(candidates),
        "duplicates_removed": duplicates,
        "after_dedupe": len(candidates) - duplicates,
        "capped_out": capped_out,
        "final": len(kept),
    }


REVIEW_PROMPT = """You are a senior short-form video editor and the final gatekeeper. A discovery model proposed the candidates below. Evaluate each one INDEPENDENTLY against the rubric and decide which clips deserve to be made. The discovery model's descriptions are not evidence: the exact transcript excerpt and surrounding context are.

Do NOT anchor on the discovery model's opinions. Do NOT reward dramatic wording by itself. A routine but competent explanation is not an exceptional clip.

## RUBRIC — score every candidate on each dimension, 0-10
{scores_block}

Also judge these flags:
- payoff_too_late: the payoff arrives too late for short-form.
- requires_background: the clip needs too much outside explanation.
- better_opening_exists: a stronger word/sentence boundary is available.
- shorter_edit_better: trimming would improve it.
- duplicate_of: another candidate id if this is essentially the same moment.
- hook_misrepresents: the apparent hook would misrepresent the actual content.

## RANKING PROCESS
1. Score every candidate independently. Do not inflate scores.
2. Reject weak candidates even if that leaves fewer clips. Zero selections is a valid, correct answer.
3. For close calls among the top ~15 only, make bounded pairwise comparisons (at most 20) and record them in `pairwise`.
4. Prefer the candidate that is more compelling to someone who has never seen the source video.
5. Return an ordered `shortlist` of the candidates that should be clipped, best first.
6. For each selected clip, name the exact spoken line that provides the payoff.

## BOUNDARIES
You may propose better word boundaries using the global word indices in the WORDS table. Only recommend a change when it measurably improves the hook or preserves a payoff/reaction. The application validates and snaps every boundary; invalid proposals are discarded.

## AUDIO CUES
Candidates may list provider-tagged non-speech audio events (``(laughter)``,
``(applause)``, ``(cheering)``, ``(music)``) with timestamps. Treat a cue that
lands inside the excerpt or just after the payoff as evidence for
emotional_impact and narrative_payoff. Do not reward continuous background
music by itself, and never quote cue text in payoff_quote or any copy field.

## VIDEO CONTEXT
VIDEO_DURATION_SECONDS: {video_duration}
{target_clips_block}
{instructions_block}
{coverage_note}

## CANDIDATES (chronological; evaluate every id)
{candidates_block}

## TRANSCRIPT WORDS (TOON, global indices `words[N]{{i,w,s,e}}:`)
{words_block}

## OUTPUT CONTRACT
Think internally, then emit `### JSON ###` on its own line and ONLY this JSON object:
{{
  "evaluations": [
    {{
      "candidate_id": "<id from the list>",
      "scores": {{
        "opening_hook": <0-10>,
        "emotional_impact": <0-10>,
        "surprise_novelty": <0-10>,
        "shareability": <0-10>,
        "self_contained_clarity": <0-10>,
        "narrative_payoff": <0-10>,
        "retention_potential": <0-10>,
        "quotability": <0-10>,
        "information_value": <0-10>,
        "audience_relevance": <0-10>
      }},
      "payoff_too_late": <bool>,
      "requires_background": <bool>,
      "better_opening_exists": <bool>,
      "shorter_edit_better": <bool>,
      "duplicate_of": <candidate id or null>,
      "hook_misrepresents": <bool>,
      "suggested_word_start_index": <int or null>,
      "suggested_word_end_index": <int or null>,
      "boundary_note": "<short explanation or empty>",
      "selected": <bool>,
      "rejection_reason": "<short reason when selected=false, else empty>",
      "why_clip": "<specific editorial reason this deserves to exist>",
      "opening_strength": "<what makes the first 1-3s work>",
      "viewer_payoff": "<what the viewer gains by watching to the end>",
      "payoff_quote": "<exact spoken line of the payoff>",
      "weaknesses": "<what could limit performance>"
    }}
  ],
  "pairwise": [
    {{"a": "<candidate id>", "b": "<candidate id>", "winner": "<candidate id>", "reason": "<short>"}}
  ],
  "shortlist": ["<candidate id>", "..."]
}}
"""


def build_review_prompt(
    candidates: list[dict],
    words: list[dict],
    *,
    video_duration: float,
    instructions: str = "",
    max_words: int | None = None,
    target_clips: int | None = None,
    audio_events: list[dict] | None = None,
) -> tuple[str, dict]:
    """Build the independent review prompt. Returns (prompt, coverage_meta)."""
    audio_events = normalize_audio_events(audio_events)
    scores_block = "\n".join(
        f"- {key}: {label}" for key, label in RUBRIC_LABELS.items()
    )
    blocks = []
    for candidate in candidates:
        alternatives = candidate.get("boundary_alternatives") or []
        alt_text = "\n".join(
            f"    - [{alt['start']:.3f}, {alt['end']:.3f}] {alt.get('note', '')}"
            for alt in alternatives
        ) or "    (none)"
        # Include cues inside the candidate plus a small tail (a reaction beat
        # often lands a second after the last word) so the reviewer can see the
        # evidence without a second global timeline.
        cues = format_audio_events(
            audio_events_in_window(
                audio_events,
                candidate["start"],
                candidate["end"],
                pad=1.5,
            ),
            limit=8,
        )
        blocks.append(
            f"CANDIDATE {candidate['id']}\n"
            f"  start={candidate['start']:.3f} end={candidate['end']:.3f} "
            f"duration={candidate['duration']:.1f}s category={candidate['category']}\n"
            f"  word_indices={candidate['word_start_index']}..{candidate['word_end_index']}\n"
            f"  opening_line: {candidate.get('opening_line', '')}\n"
            f"  strongest_quote: {candidate.get('strongest_quote', '')}\n"
            f"  discovery_why: {candidate.get('why', '')}\n"
            f"  excerpt: {candidate.get('excerpt', '')}\n"
            f"  context_before: {candidate.get('context_before', '')}\n"
            f"  context_after: {candidate.get('context_after', '')}\n"
            f"  audio_cues:\n{cues}\n"
            f"  boundary_alternatives:\n{alt_text}"
        )

    coverage_meta = {"truncated": False, "words_included": len(words)}
    end_index = len(words)
    coverage_note = ""
    if max_words and len(words) > max_words:
        end_index = max_words
        coverage_meta = {"truncated": True, "words_included": end_index,
                         "total_words": len(words)}
        coverage_note = (
            f"NOTE: the transcript table below is TRUNCATED to the first {end_index} "
            f"of {len(words)} words due to a configured context limit. Candidate "
            "excerpts/context above are still complete. Account for the reduced "
            "global coverage when comparing candidates."
        )

    target_block = ""
    if target_clips:
        noun = "clip" if int(target_clips) == 1 else "clips"
        target_block = (
            f"EDITOR TARGET: about {int(target_clips)} final {noun}. Select at "
            "most that many, fewer when the material is weak, and zero if "
            "nothing qualifies. The target is a request, not a quota."
        )
    return REVIEW_PROMPT.format(
        target_clips_block=target_block,
        scores_block=scores_block,
        video_duration=round(float(video_duration), 3),
        instructions_block=_fenced(instructions),
        coverage_note=coverage_note,
        candidates_block="\n\n".join(blocks),
        words_block=encode_indexed_words(words, 0, end_index),
    ), coverage_meta


def _clamp_score(value: Any) -> float | None:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(10.0, score))


def _score_evaluation(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    scores_raw = raw.get("scores")
    if not isinstance(scores_raw, dict):
        return None
    scores = {}
    for key in RUBRIC_KEYS:
        score = _clamp_score(scores_raw.get(key))
        if score is None:
            return None
        scores[key] = score
    return scores


def parse_review_response(
    text: str,
    candidates: list[dict],
    *,
    min_quality: float = 6.0,
    max_selected: int = 8,
    retry_fn=None,
) -> dict:
    """Parse the review JSON and produce the final deterministic ranking."""
    result = parse_gemini_response(text, retry_fn=retry_fn, request_id="review")
    data = result.data or {}
    by_id = {candidate["id"]: candidate for candidate in candidates}
    evaluations: dict[str, dict] = {}
    rejected: list[dict] = []

    raw_evals = data.get("evaluations") if isinstance(data, dict) else None
    if not isinstance(raw_evals, list):
        raw_evals = []
    for raw in raw_evals:
        if not isinstance(raw, dict):
            continue
        cid = str(raw.get("candidate_id") or "").strip()
        if cid not in by_id or cid in evaluations:
            continue
        scores = _score_evaluation(raw)
        if scores is None:
            rejected.append({"candidate_id": cid, "reason": "invalid or incomplete scores"})
            continue
        quality = round(sum(scores.values()) / len(scores), 2)
        duplicate_of = raw.get("duplicate_of")
        if duplicate_of not in by_id or duplicate_of == cid:
            duplicate_of = None
        flags = {
            "payoff_too_late": bool(raw.get("payoff_too_late")),
            "requires_background": bool(raw.get("requires_background")),
            "better_opening_exists": bool(raw.get("better_opening_exists")),
            "shorter_edit_better": bool(raw.get("shorter_edit_better")),
            "hook_misrepresents": bool(raw.get("hook_misrepresents")),
        }
        selected_flag = bool(raw.get("selected"))
        reasons = []
        if not selected_flag:
            reasons.append(str(raw.get("rejection_reason") or "reviewer did not select it"))
        if quality < min_quality:
            reasons.append(f"quality {quality:.2f} below threshold {min_quality:.2f}")
        if duplicate_of:
            reasons.append(f"duplicate of {duplicate_of}")
        if flags["hook_misrepresents"]:
            reasons.append("hook would misrepresent the content")
        eligible = not reasons

        evaluations[cid] = {
            "candidate_id": cid,
            "scores": scores,
            "quality_score": quality,
            "selected": selected_flag,
            "eligible": eligible,
            "rejection_reason": "; ".join(dict.fromkeys(reasons)),
            "duplicate_of": duplicate_of,
            "flags": flags,
            "suggested_word_start_index": raw.get("suggested_word_start_index"),
            "suggested_word_end_index": raw.get("suggested_word_end_index"),
            "boundary_note": str(raw.get("boundary_note") or "").strip()[:400],
            "why_clip": str(raw.get("why_clip") or "").strip()[:800],
            "opening_strength": str(raw.get("opening_strength") or "").strip()[:400],
            "viewer_payoff": str(raw.get("viewer_payoff") or "").strip()[:500],
            "payoff_quote": str(raw.get("payoff_quote") or "").strip()[:400],
            "weaknesses": str(raw.get("weaknesses") or "").strip()[:500],
        }
        if not eligible:
            rejected.append({
                "candidate_id": cid,
                "quality_score": quality,
                "reason": evaluations[cid]["rejection_reason"],
            })

    # Every candidate must be evaluated; missing ones are rejected explicitly.
    for cid in by_id:
        if cid not in evaluations:
            rejected.append({"candidate_id": cid, "reason": "missing from review response"})

    shortlist = data.get("shortlist") if isinstance(data, dict) else None
    if not isinstance(shortlist, list):
        shortlist = []
    shortlist = [str(cid) for cid in shortlist if isinstance(cid, str) and cid in evaluations]

    pairwise = []
    raw_pairs = data.get("pairwise") if isinstance(data, dict) else None
    if not isinstance(raw_pairs, list):
        raw_pairs = []
    for pair in raw_pairs[:20]:
        if not isinstance(pair, dict):
            continue
        a, b, winner = pair.get("a"), pair.get("b"), pair.get("winner")
        if a in evaluations and b in evaluations and winner in (a, b) and a != b:
            pairwise.append({
                "a": a, "b": b, "winner": winner,
                "reason": str(pair.get("reason") or "").strip()[:240],
            })

    eligible = {cid for cid, ev in evaluations.items() if ev["eligible"]}
    ordered = [cid for cid in shortlist if cid in eligible]
    ordered.extend(
        cid for cid in sorted(
            eligible, key=lambda cid: (-evaluations[cid]["quality_score"], cid)
        ) if cid not in ordered
    )

    # Bounded pairwise tie-breaks: only override a close score gap.
    for pair in pairwise:
        winner, loser = pair["winner"], pair["b"] if pair["winner"] == pair["a"] else pair["a"]
        if winner in ordered and loser in ordered:
            wi, li = ordered.index(winner), ordered.index(loser)
            gap = abs(evaluations[winner]["quality_score"] - evaluations[loser]["quality_score"])
            if li < wi and gap <= 0.75:
                ordered.pop(wi)
                ordered.insert(li, winner)

    max_selected = max(0, int(max_selected or 0))
    selected_ids = ordered[:max_selected] if max_selected else []
    for rank, cid in enumerate(selected_ids, start=1):
        evaluations[cid]["rank"] = rank
        evaluations[cid]["pairwise_wins"] = sum(
            1 for pair in pairwise if pair["winner"] == cid
        )

    selected = [evaluations[cid] for cid in selected_ids]
    rejection_reasons: dict[str, int] = {}
    for item in rejected:
        reason = item.get("reason") or "rejected"
        rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1

    return {
        "parse_path": result.parse_path,
        "error": result.error,
        "evaluations": evaluations,
        "selected": selected,
        "rejected": rejected,
        "pairwise": pairwise,
        "shortlist": selected_ids,
        "stats": {
            "evaluated": len(evaluations),
            "selected": len(selected),
            "rejected": len(rejected),
            "rejection_reasons": rejection_reasons,
        },
    }


def refine_boundaries(
    selected: list[dict],
    candidates_by_id: dict[str, dict],
    words: list[dict],
    *,
    video_duration: float,
    min_duration: float,
    max_duration: float,
) -> list[dict]:
    """Anchor selected clips to valid, word-aligned boundaries.

    Review may suggest an editorial boundary; the application decides whether it
    is usable. Deterministic and verifiable: every returned timestamp maps to a
    real transcript word start/end.
    """
    refined = []
    for evaluation in selected:
        candidate = candidates_by_id.get(evaluation["candidate_id"])
        if not candidate:
            continue
        start = candidate["start"]
        end = candidate["end"]
        si = evaluation.get("suggested_word_start_index")
        ei = evaluation.get("suggested_word_end_index")
        if isinstance(si, int) and isinstance(ei, int) and 0 <= si < ei < len(words):
            start = float(words[si]["start"])
            end = float(words[ei]["end"])

        start = max(0.0, min(float(start), float(video_duration)))
        end = max(0.0, min(float(end), float(video_duration)))
        if words:
            start, end = snap_clip_to_words(
                start, end, words, pre_pad=0.0, post_pad=0.0,
                max_snap=1.0, source_duration=video_duration or None,
            )
        if end <= start:
            continue

        # Enforce the duration window deterministically: trim the intro first
        # (preserve the payoff), then extend the tail for too-short clips.
        if end - start > max_duration:
            target = end - max_duration
            index = bisect.bisect_left([w["start"] for w in words], target)
            index = min(index, len(words) - 1) if words else 0
            if words:
                start = float(words[index]["start"])
        if end - start < min_duration and words:
            target = start + min_duration
            index = bisect.bisect_left([w["end"] for w in words], target)
            if index < len(words):
                end = min(float(words[index]["end"]), float(video_duration))
            else:
                end = min(float(video_duration), start + min_duration)

        if end <= start:
            continue
        original_start = candidate["start"]
        original_end = candidate["end"]
        refined.append({
            "candidate_id": candidate["id"],
            "evaluation": evaluation,
            "candidate": candidate,
            "start": round(float(start), 3),
            "end": round(float(end), 3),
            "duration": round(float(end) - float(start), 3),
            "boundary_changed": (
                abs(start - original_start) > 0.001 or abs(end - original_end) > 0.001
            ),
            "original_start": original_start,
            "original_end": original_end,
            "suggested": isinstance(si, int) and isinstance(ei, int),
            "note": evaluation.get("boundary_note", ""),
        })
    return refined


def dedupe_final(refined: list[dict], similarity_threshold: float = 0.8) -> tuple[list[dict], list[dict]]:
    """Final overlap resolution after boundary refinement."""
    kept: list[dict] = []
    dropped: list[dict] = []
    for item in refined:
        duplicate = None
        for existing in kept:
            if _overlap_ratio(item, existing) >= similarity_threshold:
                duplicate = existing
                break
        if duplicate is None:
            kept.append(item)
        else:
            dropped.append({
                "candidate_id": item["candidate_id"],
                "reason": f"overlaps stronger clip {duplicate['candidate_id']}",
            })
    return kept, dropped


COPY_PROMPT = """You write the promotional copy for clips that have ALREADY been selected by a senior editor. Do not re-judge the selection and do not invent facts.

Every factual claim must be traceable to the clip's own excerpt or surrounding context. Never fabricate a quote, name, number, outcome, or speaker attribution. If the clip does not say who is speaking, use a generic reference. Leave the loop open: name the object/number/reaction, not the outcome.

## TITLE & CAPTION RULES
- The title is bait, not a summary. Its job is to make someone stop, watch, and comment.
- Play up the stakes using what actually happens. Speculation is allowed only as a conditional/question, never as a stated fact.
- At least one of the three text fields should give the viewer something genuine to reply to (an opinion, a debatable valuation, a real question). NEVER use mechanical engagement bait ("comment X and I'll send…", "like if…", "follow me").
- Concrete numbers/objects beat adjectives. If a number is said in the clip, use it.
- Stack about two triggers per title. One is flat; three reads as spam.
- Same language as the transcript. Spoken, informal register. No ALL CAPS, at most one emoji, no hashtag spam.
- video_title_for_youtube_short: max 100 characters.
- viral_hook_text: 3-8 words, a scroll-stopping overlay, NOT a transcript quote.
- The description ends with a genuine question or debatable opinion.

{title_block}{instructions_block}

## SELECTED CLIPS
{clips_block}

## OUTPUT CONTRACT
Think internally, then emit `### JSON ###` on its own line and ONLY this JSON object:
{{
  "copies": [
    {{
      "candidate_id": "<id>",
      "video_title_for_youtube_short": "<max 100 chars>",
      "video_description_for_tiktok": "<ends with a question/opinion>",
      "video_description_for_instagram": "<ends with a question/opinion>",
      "viral_hook_text": "<3-8 word overlay>"
    }}
  ]
}}
"""


def build_copy_prompt(selected: list[dict], *, instructions: str = "", video_title: str = "") -> str:
    blocks = []
    for item in selected:
        candidate = item["candidate"]
        blocks.append(
            f"CLIP {candidate['id']}\n"
            f"  start={item['start']:.3f} end={item['end']:.3f}\n"
            f"  category: {candidate.get('category', 'other')}\n"
            f"  excerpt: {candidate.get('excerpt', '')}\n"
            f"  context_before: {candidate.get('context_before', '')}\n"
            f"  context_after: {candidate.get('context_after', '')}\n"
            f"  opening_line: {candidate.get('opening_line', '')}\n"
            f"  payoff_quote: {item['evaluation'].get('payoff_quote', '')}\n"
            f"  editorial_why: {item['evaluation'].get('why_clip', '')}\n"
            f"  reviewer_weaknesses: {item['evaluation'].get('weaknesses', '')}"
        )
    title_block = f"SOURCE TITLE (context only): {_truncate(video_title, 160)}\n" if video_title else ""
    instructions_block = _fenced(instructions)
    return COPY_PROMPT.format(
        clips_block="\n\n".join(blocks),
        instructions_block=instructions_block,
        title_block=title_block,
    )


def _clean_copy(value: Any, limit: int) -> str:
    return _truncate(value, limit)


def fallback_copy(candidate: dict) -> dict:
    """Grounded, deterministic copy used when the copy model fails."""
    excerpt = candidate.get("excerpt") or ""
    opening = candidate.get("opening_line") or excerpt
    quote = candidate.get("strongest_quote") or opening
    title = _clean_copy(opening, 95) or _clean_copy(excerpt, 95) or "Watch this moment"
    hook = " ".join(str(quote).split()[:8]) or " ".join(str(opening).split()[:8]) or "Watch this"
    description = _clean_copy(excerpt, 180) or title
    return {
        "video_title_for_youtube_short": title,
        "video_description_for_tiktok": description,
        "video_description_for_instagram": description,
        "viral_hook_text": _clean_copy(hook, 120),
    }


def parse_copy_response(text: str, selected: list[dict]) -> dict[str, dict]:
    """Parse per-clip copy; missing/invalid entries fall back to grounded copy."""
    result = parse_gemini_response(text, request_id="copy")
    data = result.data or {}
    raw_copies = data.get("copies") if isinstance(data, dict) else None
    if not isinstance(raw_copies, list):
        raw_copies = []
    by_id = {}
    for raw in raw_copies:
        if not isinstance(raw, dict):
            continue
        cid = raw.get("candidate_id")
        if not cid:
            continue
        by_id[cid] = {
            "video_title_for_youtube_short": _clean_copy(raw.get("video_title_for_youtube_short"), 100),
            "video_description_for_tiktok": _clean_copy(raw.get("video_description_for_tiktok"), 600),
            "video_description_for_instagram": _clean_copy(raw.get("video_description_for_instagram"), 600),
            "viral_hook_text": _clean_copy(raw.get("viral_hook_text"), 160),
        }

    copies = {}
    for item in selected:
        cid = item["candidate_id"]
        parsed = by_id.get(cid)
        fallback = fallback_copy(item["candidate"])
        if not parsed:
            fallback["_fallback"] = True
            copies[cid] = fallback
            continue
        merged = dict(fallback)
        for key, value in parsed.items():
            if value:
                merged[key] = value
        copies[cid] = merged
    return copies


def _reason_text(evaluation: dict, candidate: dict) -> str:
    reason = str(evaluation.get("why_clip") or "").strip()
    if len(reason) < 20:
        reason = str(candidate.get("why") or "").strip()
    if len(reason) < 20:
        reason = f"Selected by editorial review: {_truncate(candidate.get('excerpt'), 160)}"
    return _truncate(reason, 900)


def build_final_clips(
    refined: list[dict],
    copies: dict[str, dict],
    *,
    discovery_model: str,
    review_model: str,
    copy_model: str,
    copy_fallback: bool = False,
    discovery_model_used: str | None = None,
    review_model_used: str | None = None,
    copy_model_used: str | None = None,
) -> list[dict]:
    """Build the final `shorts` payload while preserving existing fields."""
    clips = []
    for item in refined:
        candidate = item["candidate"]
        evaluation = item["evaluation"]
        cid = item["candidate_id"]
        copy = copies.get(cid) or fallback_copy(candidate)
        quality = float(evaluation.get("quality_score") or 0.0)
        clips.append({
            "start": item["start"],
            "end": item["end"],
            "viral_score": max(1, min(100, int(round(quality * 10)))),
            "viral_reason": _reason_text(evaluation, candidate),
            "video_description_for_tiktok": copy.get("video_description_for_tiktok", ""),
            "video_description_for_instagram": copy.get("video_description_for_instagram", ""),
            "video_title_for_youtube_short": copy.get("video_title_for_youtube_short", ""),
            "viral_hook_text": copy.get("viral_hook_text", ""),
            "candidate_id": cid,
            "review": {
                "rank": evaluation.get("rank"),
                "quality_score": quality,
                "scores": evaluation.get("scores"),
                "pairwise_wins": evaluation.get("pairwise_wins", 0),
                "flags": evaluation.get("flags"),
                "why_clip": evaluation.get("why_clip", ""),
                "opening_strength": evaluation.get("opening_strength", ""),
                "viewer_payoff": evaluation.get("viewer_payoff", ""),
                "payoff_quote": evaluation.get("payoff_quote", ""),
                "weaknesses": evaluation.get("weaknesses", ""),
                "boundary_note": evaluation.get("boundary_note", ""),
                "boundary_changed": item.get("boundary_changed", False),
                "original_start": item.get("original_start"),
                "original_end": item.get("original_end"),
            },
            "discovery": {
                "category": candidate.get("category", "other"),
                "why": candidate.get("why", ""),
                "opening_line": candidate.get("opening_line", ""),
                "strongest_quote": candidate.get("strongest_quote", ""),
                "duration_valid": candidate.get("duration_valid"),
                "boundary_alternatives": candidate.get("boundary_alternatives", []),
                "discovery_rank": candidate.get("discovery_rank"),
                "word_start_index": candidate.get("word_start_index"),
                "word_end_index": candidate.get("word_end_index"),
            },
            "two_stage": {
                "discovery_model": discovery_model,
                "review_model": review_model,
                "copy_model": copy_model,
                "discovery_model_used": discovery_model_used or discovery_model,
                "review_model_used": review_model_used or review_model,
                "copy_model_used": copy_model_used or copy_model,
                "copy_fallback": bool(copy_fallback),
            },
        })
    return clips


def build_json_reformat_prompt(err_msg: str, broken_text: str, shape_hint: str) -> str:
    return (
        "You are a JSON reformatter. The previous response was not valid JSON.\n\n"
        f"ERROR: {_truncate(err_msg, 500)}\n\n"
        "PREVIOUS_BROKEN_OUTPUT:\n"
        f"{str(broken_text or '')[:12000]}\n\n"
        "Return ONLY valid JSON matching this shape (no markdown, no prose, "
        "straight double quotes, no trailing commas):\n"
        f"{shape_hint}\n"
    )


def compare_with_legacy(
    legacy_shorts: list[dict],
    two_stage_shorts: list[dict],
    discovery_candidates: list[dict] | None = None,
    reference_ranges: list[list[float]] | None = None,
) -> dict:
    """Diagnostic comparison between the legacy and two-stage selections."""
    def as_range(item):
        return {"start": float(item["start"]), "end": float(item["end"])}

    legacy = [as_range(item) for item in legacy_shorts or []]
    new = [as_range(item) for item in two_stage_shorts or []]

    pairs = []
    for legacy_item in legacy:
        for new_item in new:
            iou = _overlap_ratio(legacy_item, new_item)
            if iou >= 0.5:
                pairs.append({
                    "legacy": [legacy_item["start"], legacy_item["end"]],
                    "two_stage": [new_item["start"], new_item["end"]],
                    "iou": round(iou, 3),
                })

    matched_legacy = {tuple(p["legacy"]) for p in pairs}
    matched_new = {tuple(p["two_stage"]) for p in pairs}
    legacy_unique = [r for r in legacy if (r["start"], r["end"]) not in matched_legacy]
    new_unique = [r for r in new if (r["start"], r["end"]) not in matched_new]

    if isinstance(discovery_candidates, int):
        discovery_count = discovery_candidates
    else:
        discovery_count = len(discovery_candidates or [])
    report = {
        "legacy_count": len(legacy),
        "two_stage_count": len(new),
        "discovery_candidate_count": discovery_count,
        "overlap_pairs": len(pairs),
        "overlaps": pairs,
        "legacy_unique": legacy_unique,
        "two_stage_unique": new_unique,
    }

    if reference_ranges:
        reference = [{"start": float(r[0]), "end": float(r[1])} for r in reference_ranges]
        def covers(candidate, reference_item):
            return _overlap_ratio(candidate, reference_item) >= 0.5

        top5 = new[:5]
        report["reference_count"] = len(reference)
        report["top5_precision"] = round(
            sum(1 for item in top5 if any(covers(item, ref) for ref in reference))
            / len(top5), 3,
        ) if top5 else None
        report["reference_recall"] = round(
            sum(1 for ref in reference if any(covers(item, ref) for item in new))
            / len(reference), 3,
        ) if reference else None
    return report


def format_eval_report(report: dict) -> str:
    """Render a plain-text diagnostic report for a comparison run."""
    lines = [
        "Two-stage viral selection — comparison report",
        "=" * 48,
        f"Legacy single-stage clips : {report.get('legacy_count', 0)}",
        f"Two-stage clips           : {report.get('two_stage_count', 0)}",
        f"Discovery candidates      : {report.get('discovery_candidate_count', 0)}",
        f"Overlapping pairs (IoU≥.5): {report.get('overlap_pairs', 0)}",
    ]
    if report.get("top5_precision") is not None:
        lines.append(f"Top-5 precision           : {report['top5_precision']}")
    if report.get("reference_recall") is not None:
        lines.append(f"Reference recall          : {report['reference_recall']}")
    if report.get("legacy_model"):
        lines.append(f"Legacy model              : {report['legacy_model']}")
    used = report.get("two_stage_models_used") or {}
    if used:
        lines.append(f"Two-stage models used     : {used}")
    totals = report.get("two_stage_totals") or {}
    if totals:
        lines.append(
            f"Two-stage tokens          : {totals.get('input_tokens')} in / "
            f"{totals.get('output_tokens')} out · "
            f"cost {totals.get('cost_usd') if totals.get('pricing_known') else 'unknown'}"
        )
    wall = report.get("wall_seconds") or {}
    if wall:
        lines.append(
            f"Wall time                 : legacy {wall.get('legacy')}s · "
            f"two-stage {wall.get('two_stage')}s"
        )
    if report.get("legacy_unique"):
        lines.append("\nLegacy-only moments:")
        for item in report["legacy_unique"]:
            lines.append(f"  [{item['start']:.1f}-{item['end']:.1f}]")
    if report.get("two_stage_unique"):
        lines.append("\nTwo-stage-only moments:")
        for item in report["two_stage_unique"]:
            lines.append(f"  [{item['start']:.1f}-{item['end']:.1f}]")
    return "\n".join(lines)
