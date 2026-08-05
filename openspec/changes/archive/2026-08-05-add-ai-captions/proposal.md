## Why

Gemini titles exist per clip, but publish captions (the text under a TikTok/IG
post) were left to the operator to write by hand per clip. For batch
publishing many clips, authoring scroll-stopping captions per clip is slow and
inconsistent across channels.

## What Changes

- Add an "AI captions" Settings panel configuring an OpenAI-compatible
  chat-completions endpoint: base URL (defaults to `https://api.openai.com/v1`,
  private/LAN hosts like Ollama and LM Studio allowed), API key, and model
  (default `gpt-4o-mini`). Keys are validated and persisted in core config.
- Add an expandable "AI captions" panel above the results grid: the user types
  a short series context (tone, channel, CTA), and each clip's own transcript
  segments plus that context are sent to the configured endpoint, which returns
  an optimized caption per clip.
- Add `POST /api/captions/optimize/{job_id}` (trusted-origin gated) that builds
  per-clip payloads from the transcript and returns `{captions, model, base_url}`;
  400 when no API key is configured or indices are out of range.
- Generated captions fill the per-clip caption fields (grid + publish modal).
  Hand-written captions (flagged `captionTouched`) are never overwritten, and
  every generated caption stays editable.

## Capabilities

### New Capabilities
- `ai-captions`: OpenAI-compatible per-clip caption generation for publish,
  driven by a user-provided series context.

### Modified Capabilities
<!-- None. -->

## Impact

- Backend: `domain/caption_ai.py` (pure prompt build + tolerant response parse
  + HTTP call), `api/app.py` (optimize route), `api/schemas.py` (config key
  validation, `CaptionOptimizeRequest`), `storage/config_store.py` (new
  `OPENAI_CAPTIONS_*` keys).
- Frontend: `redesign/aiCaptions.jsx` (results panel),
  `redesign/views.jsx` (Settings panel), `redesign/realApi.js`
  (`optimizeCaptions`), per-clip caption state (`captionTouched`).
- Config: new `OPENAI_CAPTIONS_BASE_URL` / `_API_KEY` / `_MODEL` keys.
