## 1. Backend caption generation

- [x] 1.1 Add `domain/caption_ai.py`: prompt build, clip→payload mapping, tolerant chat-completions parsing, minimal HTTP call (pure, host-tested).
- [x] 1.2 Add `POST /api/captions/optimize/{job_id}` (trusted-origin gated) with no-key/out-of-range 400s.
- [x] 1.3 Add `OPENAI_CAPTIONS_BASE_URL`/`_API_KEY`/`_MODEL` config keys with validation (base URL scheme, model id regex).

## 2. Frontend

- [x] 2.1 Add the AI captions Settings panel (base URL, key, model).
- [x] 2.2 Add the results-grid AI captions panel: series context input + per-clip caption generation.
- [x] 2.3 Fill generated captions into per-clip caption state and honor `captionTouched` (never overwrite hand-written).

## 3. Tests

- [x] 3.1 Host tests for `caption_ai` prompt/parsing and the optimize route.
- [x] 3.2 Frontend tests for the AI captions panel and settings.
