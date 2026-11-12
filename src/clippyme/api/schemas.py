"""Pydantic request schemas for the ClippyMe FastAPI app."""
from __future__ import annotations

import ipaddress
import math
import re
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator

from clippyme.domain.job_results import (
    ALLOWED_LANGUAGES,
    GEMINI_MODEL_RE,
    MAX_INSTRUCTIONS_LEN,
    MODEL_ID_RE,
)
from clippyme.domain.errors import ValidationError as ClippyMeValidationError
from clippyme.domain.job_recipe import validate_recipe
from clippyme.domain.local_input import validate_local_path
from clippyme.netutil import resolve_host_addresses
from clippyme.schemas import ViralClip, ViralClipsResponse  # noqa: F401


def _reject_internal_host(host: str) -> None:
    """Reject literal or DNS-resolved non-public hosts without unbounded DNS I/O.

    DNS failures and timeouts remain best-effort at this early API boundary; the
    downloader performs the authoritative rebinding-aware check immediately
    before the network request.
    """
    if not host:
        raise ValueError("url has no host")
    try:
        candidates = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            candidates = set(resolve_host_addresses(host, timeout=5.0))
        except (OSError, TimeoutError, UnicodeError):
            return
    for ip in candidates:
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError("url points to a non-public address")


def validate_public_url(value: str) -> str:
    """Enforce an HTTP(S) URL with a non-internal host."""
    raw = (value or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError("url must use http or https")
    _reject_internal_host((parsed.hostname or "").lower())
    return raw


def _validate_language(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized not in ALLOWED_LANGUAGES:
        raise ValueError(f"unsupported language: {value!r}")
    return normalized


def _validate_timezone(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        raise ValueError("timezone must not be blank")
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(normalized)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone: {normalized!r}") from exc
    except ImportError:  # pragma: no cover - Python 3.11 always has zoneinfo
        pass
    return normalized


_VALID_YOUTUBE_VISIBILITY = {"public", "private", "unlisted"}
YOUTUBE_CATEGORY_IDS = {
    "22": "People & Blogs",
    "1": "Film & Animation",
    "2": "Autos & Vehicles",
    "10": "Music",
    "15": "Pets & Animals",
    "17": "Sports",
    "20": "Gaming",
    "23": "Comedy",
    "24": "Entertainment",
    "25": "News & Politics",
    "26": "Howto & Style",
    "27": "Education",
    "28": "Science & Technology",
    "29": "Nonprofits & Activism",
}


def _validate_youtube_defaults(value):
    if value is None:
        return value
    if not isinstance(value, dict):
        raise ValueError("youtube defaults must be an object")
    allowed = {"category_id", "made_for_kids", "contains_synthetic_media", "visibility"}
    extra = set(value) - allowed
    if extra:
        raise ValueError(f"unknown youtube default: {sorted(extra)}")
    if value.get("category_id") is not None:
        category_id = value["category_id"]
        if not isinstance(category_id, str) or not category_id.isdigit():
            raise ValueError("category_id must be a numeric string")
        if len(category_id) > 8:
            raise ValueError("category_id too long")
    for key in ("made_for_kids", "contains_synthetic_media"):
        if value.get(key) is not None and not isinstance(value[key], bool):
            raise ValueError(f"{key} must be a boolean")
    if value.get("visibility") is not None and value["visibility"] not in _VALID_YOUTUBE_VISIBILITY:
        raise ValueError(
            f"visibility must be one of {sorted(_VALID_YOUTUBE_VISIBILITY)}"
        )
    return value


def _validate_zernio_accounts(value):
    if value is None:
        return value
    if not isinstance(value, dict):
        raise ValueError("accounts must be an object")
    if len(value) > 16:
        raise ValueError("too many account entries")
    allowed = {"tiktok", "instagram", "youtube"}
    for platform, account_id in value.items():
        if platform not in allowed:
            raise ValueError(f"unknown account platform: {platform!r}")
        if account_id is not None and (
            not isinstance(account_id, str) or len(account_id) > 256
        ):
            raise ValueError(
                f"account id for {platform!r} must be a string <= 256 chars"
            )
    return value


class ProcessRequest(BaseModel):
    url: Optional[str] = Field(None, max_length=2048)
    # Absolute path to a video already present on the backend's filesystem
    # (e.g. an NFS mount under LOCAL_INPUT_DIR). Submits the file in place —
    # no HTTP upload — and the pipeline reads it directly.
    local_path: Optional[str] = Field(None, max_length=4096)
    instructions: Optional[str] = Field(None, max_length=MAX_INSTRUCTIONS_LEN)
    reframe_mode: Optional[str] = Field(None, pattern=r"^(auto|disabled|subject|object)$")
    # Fixed zoom for the letterbox render. 0 (default) = whole frame between
    # the bars; the dashboard sends a percentage (5-15), normalized downstream.
    letterbox_zoom: Optional[float] = Field(None, ge=0, le=15)
    aspect: Optional[str] = Field(None, pattern=r"^(9:16|1:1|16:9)$")
    language: Optional[str] = Field(None, max_length=16)
    no_zoom: Optional[bool] = False
    skip_analysis: Optional[bool] = False
    model: Optional[str] = Field(
        None, max_length=128, pattern=MODEL_ID_RE.pattern
    )
    # Hard floor on every produced clip's length (seconds). 0/unset keeps the
    # default 15-60s prompt window; e.g. 60 asks for and enforces 60s+ clips.
    min_duration: Optional[float] = Field(None, gt=0, le=300)
    # Editor's requested clip count. Passed into the selection prompt (legacy)
    # and used as the review ceiling (two-stage); it is a target, not a quota.
    target_clips: Optional[int] = Field(None, ge=1, le=50)
    # The Create-tab recipe (subtitles/hook/logo/grade/…). Persisted with the
    # job so previews, downloads and publishing keep the chosen layers even
    # when the job is opened from a different browser or after a history
    # restore. Optional so API-only callers can omit it.
    preselections: Optional[dict] = None

    @field_validator("preselections")
    @classmethod
    def _validate_preselections(cls, value):
        return validate_recipe(value)

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: Optional[str]) -> Optional[str]:
        if not value:
            return value
        if value == "https://upload.invalid/local":
            return value
        return validate_public_url(value)

    @field_validator("local_path")
    @classmethod
    def _validate_local_path(cls, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        try:
            return validate_local_path(value)
        except ClippyMeValidationError as exc:
            raise ValueError(exc.detail)

    @field_validator("language")
    @classmethod
    def _bound_language(cls, value: Optional[str]) -> Optional[str]:
        return _validate_language(value)

    @model_validator(mode="after")
    def _one_source_only(self):
        has_url = bool((self.url or "").strip())
        has_local = bool(self.local_path)
        if has_url and has_local:
            raise ValueError("provide either url or local_path, not both")
        return self


class BatchRequest(BaseModel):
    urls: List[str] = Field(..., min_length=1, max_length=20)
    instructions: Optional[str] = Field(None, max_length=MAX_INSTRUCTIONS_LEN)
    reframe_mode: Optional[str] = Field(None, pattern=r"^(auto|disabled|subject|object)$")
    # Fixed zoom for the letterbox render. 0 (default) = whole frame between
    # the bars; the dashboard sends a percentage (5-15), normalized downstream.
    letterbox_zoom: Optional[float] = Field(None, ge=0, le=15)
    aspect: Optional[str] = Field(None, pattern=r"^(9:16|1:1|16:9)$")
    language: Optional[str] = Field(None, max_length=16)
    no_zoom: Optional[bool] = False
    skip_analysis: Optional[bool] = False
    model: Optional[str] = Field(
        None, max_length=128, pattern=MODEL_ID_RE.pattern
    )
    min_duration: Optional[float] = Field(None, gt=0, le=300)
    target_clips: Optional[int] = Field(None, ge=1, le=50)
    # Same Create recipe as ProcessRequest; each URL job gets its own copy so
    # History can restore the layers without the submitting browser.
    preselections: Optional[dict] = None

    @field_validator("preselections")
    @classmethod
    def _validate_preselections(cls, value):
        return validate_recipe(value)

    @field_validator("urls")
    @classmethod
    def _validate_urls(cls, values: List[str]) -> List[str]:
        cleaned = [validate_public_url(url) for url in values if (url or "").strip()]
        if not cleaned:
            raise ValueError("at least one non-blank URL is required")
        return cleaned

    @field_validator("language")
    @classmethod
    def _bound_language(cls, value: Optional[str]) -> Optional[str]:
        return _validate_language(value)


_ALLOWED_CONFIG_KEYS = frozenset({
    "GEMINI_API_KEY", "GEMINI_MODEL", "YOUTUBE_COOKIES", "HF_TOKEN",
    "HUGGINGFACE_TOKEN", "DEEPGRAM_API_KEY", "ELEVENLABS_API_KEY",
    "TRANSCRIPTION_PROVIDER", "TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET",
    "OPENAI_CAPTIONS_BASE_URL", "OPENAI_CAPTIONS_API_KEY",
    "OPENAI_CAPTIONS_MODEL",
    "VIRAL_PROVIDER", "VIRAL_BASE_URL", "VIRAL_API_KEY", "VIRAL_MODEL",
    "VIRAL_FALLBACK_MODELS",
    "VIRAL_ALLOWED_MODELS",
    "VIRAL_MODE", "VIRAL_DISCOVERY_MODEL", "VIRAL_REVIEW_MODEL",
    "VIRAL_COPY_MODEL", "VIRAL_MAX_CANDIDATES", "VIRAL_MAX_SELECTED_CLIPS",
    "VIRAL_MIN_QUALITY", "VIRAL_MAX_ANALYSIS_COST",
    "VIRAL_TWO_STAGE_FALLBACK", "VIRAL_STAGE_MAX_ATTEMPTS",
    "VIRAL_DISCOVERY_FALLBACK_MODELS", "VIRAL_REVIEW_FALLBACK_MODELS",
    "VIRAL_COPY_FALLBACK_MODELS", "VIRAL_REASONING_EFFORT",
})

# OpenAI-compatible model ids: letters/digits plus ._-/ (OpenRouter "org/model",
# Ollama "namespace/model", etc.). Keeps a config value from smuggling argv-ish
# input into the HTTP payload.
_OPENAI_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/\-]{0,127}$")


class ConfigUpdateRequest(BaseModel):
    keys: Dict[str, str]

    @field_validator("keys")
    @classmethod
    def _validate_keys(cls, values: Dict[str, str]) -> Dict[str, str]:
        unknown = set(values) - _ALLOWED_CONFIG_KEYS
        if unknown:
            raise ValueError(f"unknown config keys: {sorted(unknown)}")
        for name, value in values.items():
            if len(value) > 4096:
                raise ValueError(f"config value for {name!r} too long (max 4096)")
        provider = values.get("TRANSCRIPTION_PROVIDER")
        if provider not in (None, "", "deepgram", "elevenlabs", "whisper"):
            raise ValueError("TRANSCRIPTION_PROVIDER must be deepgram, elevenlabs or whisper")
        model = values.get("GEMINI_MODEL")
        if model and not GEMINI_MODEL_RE.fullmatch(model):
            raise ValueError("GEMINI_MODEL is not a valid Gemini model id")
        base_url = values.get("OPENAI_CAPTIONS_BASE_URL")
        if base_url and not re.match(r"^https?://\S+$", base_url):
            raise ValueError("OPENAI_CAPTIONS_BASE_URL must be an http(s) URL")
        caption_model = values.get("OPENAI_CAPTIONS_MODEL")
        if caption_model and not _OPENAI_MODEL_RE.fullmatch(caption_model):
            raise ValueError("OPENAI_CAPTIONS_MODEL is not a valid model id")
        viral_provider = values.get("VIRAL_PROVIDER")
        if viral_provider not in (None, "", "gemini", "openai"):
            raise ValueError("VIRAL_PROVIDER must be gemini or openai")
        viral_url = values.get("VIRAL_BASE_URL")
        if viral_url and not re.match(r"^https?://\S+$", viral_url):
            raise ValueError("VIRAL_BASE_URL must be an http(s) URL")
        viral_model = values.get("VIRAL_MODEL")
        if viral_model and not MODEL_ID_RE.fullmatch(viral_model):
            raise ValueError("VIRAL_MODEL is not a valid model id")
        for part in (values.get("VIRAL_FALLBACK_MODELS") or "").split(","):
            part = part.strip()
            if part and not MODEL_ID_RE.fullmatch(part):
                raise ValueError("VIRAL_FALLBACK_MODELS contains an invalid model id")
        for part in (values.get("VIRAL_ALLOWED_MODELS") or "").split(","):
            part = part.strip()
            if part and not MODEL_ID_RE.fullmatch(part):
                raise ValueError("VIRAL_ALLOWED_MODELS contains an invalid model id")
        viral_mode = values.get("VIRAL_MODE")
        if viral_mode not in (None, "", "auto", "single", "two_stage"):
            raise ValueError("VIRAL_MODE must be auto, single or two_stage")
        for key in ("VIRAL_DISCOVERY_MODEL", "VIRAL_REVIEW_MODEL", "VIRAL_COPY_MODEL"):
            model = values.get(key)
            if model and not MODEL_ID_RE.fullmatch(model):
                raise ValueError(f"{key} is not a valid model id")
        for key in ("VIRAL_DISCOVERY_FALLBACK_MODELS", "VIRAL_REVIEW_FALLBACK_MODELS",
                    "VIRAL_COPY_FALLBACK_MODELS"):
            for part in (values.get(key) or "").split(","):
                part = part.strip()
                if part and not MODEL_ID_RE.fullmatch(part):
                    raise ValueError(f"{key} contains an invalid model id")
        fallback = values.get("VIRAL_TWO_STAGE_FALLBACK")
        if fallback not in (None, "", "single", "off"):
            raise ValueError("VIRAL_TWO_STAGE_FALLBACK must be single or off")
        effort = values.get("VIRAL_REASONING_EFFORT")
        if effort not in (None, "", "off", "minimal", "low", "medium", "high", "xhigh", "max"):
            raise ValueError(
                "VIRAL_REASONING_EFFORT must be off, minimal, low, medium, high, xhigh or max"
            )

        def _bounded_int(key, lo, hi):
            raw = values.get(key)
            if raw in (None, ""):
                return
            try:
                parsed = int(raw)
            except ValueError as exc:
                raise ValueError(f"{key} must be an integer") from exc
            if not (lo <= parsed <= hi):
                raise ValueError(f"{key} must be between {lo} and {hi}")

        def _bounded_float(key, lo, hi):
            raw = values.get(key)
            if raw in (None, ""):
                return
            try:
                parsed = float(raw)
            except ValueError as exc:
                raise ValueError(f"{key} must be a number") from exc
            if not (lo <= parsed <= hi):
                raise ValueError(f"{key} must be between {lo} and {hi}")

        _bounded_int("VIRAL_MAX_CANDIDATES", 4, 60)
        _bounded_int("VIRAL_MAX_SELECTED_CLIPS", 1, 20)
        _bounded_int("VIRAL_STAGE_MAX_ATTEMPTS", 1, 5)
        _bounded_float("VIRAL_MIN_QUALITY", 0.0, 10.0)
        _bounded_float("VIRAL_MAX_ANALYSIS_COST", 0.0, 10000.0)
        return values


class ReframeRequest(BaseModel):
    reframe_mode: Optional[str] = Field(None, pattern=r"^(auto|disabled|subject|object)$")
    # Fixed zoom for the letterbox render. 0 (default) = whole frame between
    # the bars; the dashboard sends a percentage (5-15), normalized downstream.
    letterbox_zoom: Optional[float] = Field(None, ge=0, le=15)


_OVERLAY_MAX_KEYS = 40
_OVERLAY_MAX_STR = 1000
_OVERLAY_MAX_ABS_NUM = 100_000


def _validate_overlay_params(value):
    if value is None:
        return value
    if not isinstance(value, dict):
        raise ValueError("must be an object")
    if len(value) > _OVERLAY_MAX_KEYS:
        raise ValueError(f"too many keys (max {_OVERLAY_MAX_KEYS})")
    for key, item in value.items():
        if isinstance(item, str):
            if len(item) > _OVERLAY_MAX_STR:
                raise ValueError(f"value for {key!r} too long (max {_OVERLAY_MAX_STR})")
        elif isinstance(item, bool):
            continue
        elif isinstance(item, (int, float)):
            if not math.isfinite(item) or abs(item) > _OVERLAY_MAX_ABS_NUM:
                raise ValueError(f"value for {key!r} out of range")
        elif item is None:
            continue
        else:
            raise ValueError(f"value for {key!r} must be a scalar")
    return value


_DROP_MAX_RANGES = 500
_DROP_MAX_SECONDS = 100_000


def _validate_drop_ranges(value):
    if value is None:
        return value
    if not isinstance(value, list):
        raise ValueError("drop_ranges must be a list")
    if len(value) > _DROP_MAX_RANGES:
        raise ValueError(f"too many drop_ranges (max {_DROP_MAX_RANGES})")
    for item in value:
        if isinstance(item, dict):
            numbers = (item.get("start"), item.get("end"))
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            numbers = (item[0], item[1])
        else:
            raise ValueError("each drop range must be [start, end] or {start, end}")
        for number in numbers:
            if not isinstance(number, (int, float)) or isinstance(number, bool):
                raise ValueError("drop range bounds must be numbers")
            if not math.isfinite(number) or abs(number) > _DROP_MAX_SECONDS:
                raise ValueError("drop range bound out of range")
    return value


_ALLOWED_TOGGLES = frozenset({"smartcut", "hook", "subtitles", "logo", "grade", "banner", "toptext"})


def _validate_toggles(value):
    if value is None:
        return value
    if not isinstance(value, dict):
        raise ValueError("toggles must be an object")
    extra = set(value) - _ALLOWED_TOGGLES
    if extra:
        raise ValueError(f"unknown toggles: {sorted(extra)}")
    for key, enabled in value.items():
        if not isinstance(enabled, bool):
            raise ValueError(f"toggle {key!r} must be boolean")
    return value


def validate_publish_platforms(value: List[dict]) -> List[dict]:
    """Validate the small, flat Zernio target objects forwarded downstream."""
    allowed = {"tiktok", "instagram", "youtube"}
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("each platform must be an object")
        platform = item.get("platform")
        if platform not in allowed:
            raise ValueError(f"platform must be one of {sorted(allowed)}")
        account_id = item.get("accountId")
        if not isinstance(account_id, str) or not account_id or len(account_id) > 256:
            raise ValueError("accountId must be a non-empty string (max 256)")
        extra = set(item) - {"platform", "accountId", "platformSpecificData"}
        if extra:
            raise ValueError(f"unexpected platform keys: {sorted(extra)}")
        if "platformSpecificData" in item:
            _validate_overlay_params(item["platformSpecificData"])
    return value


class PreselectionsUpdateRequest(BaseModel):
    """Write-through/backfill of an existing job's Create recipe."""

    preselections: dict

    @field_validator("preselections")
    @classmethod
    def _validate_preselections(cls, value):
        return validate_recipe(value)


class ComposeRequest(BaseModel):
    toggles: dict = Field(default_factory=dict)
    hook_params: dict = Field(default_factory=dict)
    subtitle_params: dict = Field(default_factory=dict)
    logo_params: dict = Field(default_factory=dict)
    grade_params: dict = Field(default_factory=dict)
    banner_params: dict = Field(default_factory=dict)
    top_text_params: dict = Field(default_factory=dict)
    drop_ranges: list = Field(default_factory=list)

    @field_validator("toggles")
    @classmethod
    def _bound_toggles(cls, value):
        return _validate_toggles(value)

    @field_validator(
        "hook_params", "subtitle_params", "logo_params", "grade_params",
        "banner_params", "top_text_params"
    )
    @classmethod
    def _bound_overlay(cls, value):
        return _validate_overlay_params(value)

    @field_validator("drop_ranges")
    @classmethod
    def _bound_drops(cls, value):
        return _validate_drop_ranges(value)


class EditAIRequest(BaseModel):
    instruction: str = Field(..., min_length=1, max_length=1000)
    model: Optional[str] = Field(
        None, max_length=64, pattern=r"^gemini-[A-Za-z0-9.\-]{1,64}$"
    )


class HookRewriteRequest(BaseModel):
    """Regenerate a clip's hook text via Gemini in a target language.

    ``language`` is the ISO code of the language the hook must be written in
    (e.g. ``en``); ``multi``/``auto`` are meaningless as a hook target and are
    rejected. ``model`` optionally overrides the configured Gemini model.
    """
    language: str = Field("en", min_length=2, max_length=16)
    model: Optional[str] = Field(
        None, max_length=64, pattern=r"^gemini-[A-Za-z0-9.\-]{1,64}$"
    )

    @field_validator("language")
    @classmethod
    def _bound_language(cls, value: str) -> str:
        normalized = _validate_language(value)
        if not normalized or normalized in ("multi", "auto"):
            raise ValueError(
                "language must be a concrete language (e.g. 'en', 'it')"
            )
        return normalized


class CaptionOptimizeRequest(BaseModel):
    """Batch caption generation for a job's clips via an OpenAI-compatible API.

    ``context`` is the creator's series notes sent alongside each clip's own
    transcript; ``indices`` are the ABSOLUTE ``shorts`` positions to caption
    (omitted = every clip). The frontend sends only clips whose caption the
    user hasn't hand-written, so this endpoint never overwrites their work.
    """
    context: str = Field("", max_length=2000)
    indices: Optional[List[int]] = Field(None, max_length=50)

    @field_validator("indices")
    @classmethod
    def _bound_indices(cls, value):
        if value is None:
            return value
        seen, out = set(), []
        for index in value:
            if not isinstance(index, int) or isinstance(index, bool):
                raise ValueError("indices must be integers")
            if index < 0 or index > 100000:
                raise ValueError("indices out of range")
            if index not in seen:
                seen.add(index)
                out.append(index)
        return out


class PublishRequest(BaseModel):
    title: str = Field("", max_length=500)
    caption: str = Field("", max_length=2200)
    platforms: List[dict] = Field(..., min_length=1, max_length=14)
    schedule_mode: str = Field("now", pattern=r"^(now|auto|manual)$")
    scheduled_for: Optional[str] = Field(None, max_length=64)
    start_date: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    timezone: str = Field("America/New_York", max_length=64)
    profile_id: Optional[str] = Field(None, max_length=64)
    # One manual publication intent: the client reuses it for every retry of
    # the same Publish and mints a new one for a deliberate re-publish. The
    # server turns it into the Zernio Idempotency-Key, so a retry after a lost
    # response replays the original post instead of creating a second one.
    intent_id: Optional[str] = Field(None, pattern=r"^[A-Za-z0-9-]{8,64}$")
    tiktok_settings: Optional[dict] = None
    compose_first: bool = False
    toggles: Optional[dict] = None
    hook_params: Optional[dict] = None
    subtitle_params: Optional[dict] = None
    logo_params: Optional[dict] = None
    grade_params: Optional[dict] = None
    banner_params: Optional[dict] = None
    top_text_params: Optional[dict] = None
    drop_ranges: Optional[list] = None

    @field_validator("timezone")
    @classmethod
    def _validate_tz(cls, value: str) -> str:
        return _validate_timezone(value)  # type: ignore[return-value]

    @field_validator("scheduled_for")
    @classmethod
    def _validate_scheduled_for(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, AttributeError) as exc:
            raise ValueError("scheduled_for must be an ISO 8601 timestamp") from exc
        return value

    @field_validator("toggles")
    @classmethod
    def _bound_toggles(cls, value):
        return _validate_toggles(value)

    @field_validator(
        "hook_params", "subtitle_params", "logo_params", "grade_params",
        "banner_params", "top_text_params"
    )
    @classmethod
    def _bound_overlay(cls, value):
        return _validate_overlay_params(value)

    @field_validator("drop_ranges")
    @classmethod
    def _bound_drops(cls, value):
        return None if value is None else _validate_drop_ranges(value)

    @field_validator("platforms")
    @classmethod
    def _validate_platforms(cls, value: List[dict]) -> List[dict]:
        return validate_publish_platforms(value)

    @field_validator("tiktok_settings")
    @classmethod
    def _bound_tiktok_settings(cls, value):
        return _validate_overlay_params(value)


class LiveMonitorStopRequest(BaseModel):
    monitor_id: Optional[str] = Field(
        None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9:_-]+$"
    )


class LiveMonitorPublishingRequest(BaseModel):
    # Strict prevents strings such as 'false' from being coerced to True.
    enabled: bool = Field(strict=True)


class LiveMonitorStartRequest(BaseModel):
    slug: str = Field(..., min_length=1, max_length=256)
    platform: str = Field("kick", pattern=r"^(kick|twitch|youtube)$")
    mode: str = Field("live", pattern=r"^(live|vod)$")
    platforms: List[dict] = Field(..., min_length=1, max_length=14)
    segment_seconds: int = Field(1800, ge=60, le=3600)
    prelive_skip_seconds: int = Field(1800, ge=0, le=7200)
    min_gap_seconds: int = Field(900, ge=0, le=86400)
    poll_interval: Optional[int] = Field(None, ge=30, le=3600)
    loop: bool = False
    caption_template: str = Field("", max_length=2200)
    title_template: str = Field("", max_length=500)
    instructions: Optional[str] = Field(None, max_length=MAX_INSTRUCTIONS_LEN)
    timezone: Optional[str] = Field(None, max_length=64)
    banner: Optional[dict] = None
    compose: Optional[dict] = None
    catchup: str = Field("backfill", pattern=r"^(backfill|live_only)$")
    delete_after_publish: bool = True
    # 0 = no cap (publish every clip the segment yields); otherwise the top-N.
    max_clips: int = Field(5, ge=0, le=1000)
    clip_selection: str = Field("fixed", pattern=r"^(fixed|auto)$")
    min_viral_score: int = Field(70, ge=1, le=100)
    # Burn Smart Cut (silence/filler removal) into every auto-published clip.
    smart_cut: bool = False
    # Fixed zoom on the monitor's letterbox render (percent; 0 = whole frame).
    letterbox_zoom: float = Field(0, ge=0, le=15)

    @field_validator("timezone")
    @classmethod
    def _validate_tz(cls, value: Optional[str]) -> Optional[str]:
        return _validate_timezone(value)

    @field_validator("banner")
    @classmethod
    def _bound_banner(cls, value):
        return _validate_overlay_params(value)

    @field_validator("compose")
    @classmethod
    def _bound_compose(cls, value):
        if value is None:
            return value
        if not isinstance(value, dict):
            raise ValueError("compose must be an object")
        if len(value) > 8:
            raise ValueError("compose has too many keys")
        for key, item in value.items():
            if isinstance(item, dict):
                _validate_overlay_params(item)
            elif isinstance(item, (int, float)) and not isinstance(item, bool):
                if not math.isfinite(item):
                    raise ValueError(f"compose[{key!r}] must be finite")
            elif not isinstance(item, (str, int, float, bool)) and item is not None:
                raise ValueError(f"compose[{key!r}] must be a scalar or object")
        return value

    @field_validator("slug")
    @classmethod
    def _clean_slug(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or any(character.isspace() for character in normalized):
            raise ValueError("channel must not be blank or contain whitespace")
        return normalized

    @field_validator("platforms")
    @classmethod
    def _validate_platforms(cls, value: List[dict]) -> List[dict]:
        return validate_publish_platforms(value)


class ZernioProfileRequest(BaseModel):
    id: Optional[str] = Field(None, max_length=64)
    name: str = Field(..., min_length=1, max_length=80)
    api_key: Optional[str] = Field(None, max_length=512)
    is_default: bool = False
    accounts: Optional[dict] = None
    timezone: Optional[str] = Field(None, max_length=64)
    youtube: Optional[dict] = None

    @field_validator("timezone")
    @classmethod
    def _validate_tz(cls, value: Optional[str]) -> Optional[str]:
        return _validate_timezone(value)

    @field_validator("accounts")
    @classmethod
    def _validate_accounts(cls, value):
        return _validate_zernio_accounts(value)

    @field_validator("youtube")
    @classmethod
    def _validate_yt(cls, value):
        return _validate_youtube_defaults(value)


class ZernioConfigRequest(BaseModel):
    api_key: Optional[str] = Field(None, max_length=512)
    accounts: Optional[dict] = None
    timezone: Optional[str] = Field(None, max_length=64)
    profiles: Optional[List[ZernioProfileRequest]] = None

    @field_validator("timezone")
    @classmethod
    def _validate_tz(cls, value: Optional[str]) -> Optional[str]:
        return _validate_timezone(value)

    @field_validator("accounts")
    @classmethod
    def _validate_accounts(cls, value):
        return _validate_zernio_accounts(value)

    @field_validator("profiles")
    @classmethod
    def _bound_profiles(cls, value: Optional[List[ZernioProfileRequest]]):
        if value is None:
            return value
        if len(value) > 16:
            raise ValueError("too many Zernio profiles")
        return value
