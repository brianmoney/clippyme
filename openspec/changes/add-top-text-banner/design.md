## Context

ClippyMe's compose pipeline already has a banner primitive (`domain/banner.py`)
that renders an attribution "pill" (platform logo + channel handle) as a
transparent PNG via Pillow, then overlays it with one ffmpeg pass
(`add_banner_to_video`). Compose ordering is **Grade → Subtitles → Smart Cut →
Hook → Logo → Banner** (banner topmost) in `compose_layers`, toggled via the
`ComposeRequest.toggles` dict and parameterised via a `*_params` overlay dict
that goes through `_validate_overlay_params` (generic scalar bounds).

The frontend threads each layer through four channels that must stay in sync:
`seedClipParams` (toggle + params seeds), the Create recipe drawer, the per-clip
Edit modal tab, and the payload builders (`realApi.js`, `publish.jsx`,
`bulkApply.js`, `applyEdit.js`) that send `toggles.<x>` + `<x>_params` to
`/api/compose` and `/api/publish`.

## Goals / Non-Goals

**Goals:**
- A full-width, top-edge text strip rendered for the clip's whole duration.
- Style controls: S/M/L font size (video-width-proportional), hex text colour,
  optional background strip with tint + opacity.
- Same UX surface as existing overlays: Create recipe + per-clip edit + bulk
  apply-to-all + multi-select bulk + publish/export compose-first.
- Follows the existing `banner` plumbing shape so review and future maintenance
  mirror a known pattern.

**Non-Goals:**
- No animation, per-line timing, multi-page text, or emoji rendering beyond what
  the bundled fonts already support.
- No new env/config knobs; text is per-clip UI state only, not global config.
- No new live-monitor integration (monitors keep their own banner/compose
  recipe). It is deliberately not added to `live_monitor` compose construction.

## Decisions

### 1. New `domain/top_text.py` — a sibling of `domain/banner.py`
Implements `render_top_text_png(text, video_width, params, out_path)` (pure
Pillow, host-testable; no cv2) plus `add_top_text_to_video(video_path, params,
out_path)` which probes dimensions then overlays via one ffmpeg pass —
mirroring `render_banner_png` / `add_banner_to_video`. A pure geometry helper
(`top_text_plan`) keeps the strip height / font px mapping unit-testable
without Pillow/ffmpeg. Fonts resolve through `hooks._resolve_hook_font_path`
(reuses the allow-listed bundled/user font dirs + `_FONT_NAME_RE`).

### 2. Full-width strip, not a pill
Rendering: a full-width `RGBA` canvas of the video's width; strip height =
`text height + vertical padding`, tightly sized to the (max two) wrapped lines.
Text is horizontally centred, vertically centred in the strip. Strip placed at
`y=0`. Strip fill = `bg_color` at `bg_opacity` (default 0.55 black), giving
legibility on bright meeting footage; the strip is always drawn behind text,
and when `bg_enabled` is false the layer is skipped (text with no band on
uncontrolled meeting footage is not useful and the request default keeps the
strip on). Text colour defaults `#FFFFFF`. Font size presets scale to video
width: `{S: 0.035, M: 0.045, L: 0.055} * width` px, clamped into Pillow's safe
range — two lines wrap via the same `_split_overlong_word` width-measuring loop
as `hooks.create_hook_image`.

### 3. Naming: toggle `toptext`, params `top_text_params`
Matches the existing `<thing>_params` convention (`banner_params`,
`subtitle_params`) and avoids the collision with the existing `banner` layer.
`_ALLOWED_TOGGLES` gains `toptext`; both `ComposeRequest` and `PublishRequest`
gain `top_text_params: dict`, validated by the existing `_validate_overlay_params`.

### 4. Compose ordering: after Logo, before/with the attribution Banner
The top text is title chrome and should sit above every content layer. It runs
immediately before the existing `_apply_banner` pass. Both are separate
single-overlay ffmpeg passes (matching today's banner; no filter fusion — the
extra encode generation is acceptable and keeps each overlay independently
reviewable/testable).

### 5. Text is SHARED config across bulk paths
Unlike hook text (which `bulkApply` preserves per clip), top text is meeting/
series chrome — copied verbatim by apply-to-all, multi-select bulk edit, and
Create-recipe seeding. This matches how `banner_params` / `logo_params`
propagate.

### 6. Params vocabulary (frontend `topTextParams`, backend keys)
`{ enabled, text, size: 'S'|'M'|'L', text_color, bg_enabled, bg_color,
bg_opacity }`. `enabled` is UI-side (mirrors `bannerParams.enabled`) and the
toggle (`toggles.toptext`) is derived from it in `seedToggles`, exactly like
`banner`. Defaults: `size M`, `text_color #FFFFFF`, `bg_enabled true`,
`bg_color #000000`, `bg_opacity 0.55`.

## Risks / Trade-offs

- **Two full-width rows could cover the subject in a tight face-crop.** Mitigated
  by defaulting to S/M and allowing the strip to be disabled; the strip is
  translucent so content shows through.
- **Another ffmpeg re-encode pass** for clips that enable both banner and top
  text (2 extra passes today vs 0 when neither is used). Acceptable — compose
  is download/publish-time, not per-frame; correctness and independent
  testability are worth one generation.
- **Long unbroken tokens** are split by the shared overlong-word splitter so a
  giant no-space string cannot create an oversized canvas or overflow the strip.
- Frontend/backend key drift is the main risk of adding a sixth layer; the
  change reuses the single `seedClipParams`/seed vocabulary and updates the
  payload builders in the same commit, with frontend tests guarding the
  seed→compose→publish payloads.
