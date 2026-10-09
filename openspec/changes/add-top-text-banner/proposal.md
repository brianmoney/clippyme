## Why

Operators cutting meeting/committee/public-comment footage want a title burned
along the TOP of every clip (e.g. "St. Johns BOCC Public comments - SB 686")
so viewers immediately know which hearing a short came from. Nothing today
renders full-width top-of-frame text: the hook is short opener copy, the
attribution banner is a platform-logo + handle pill pinned low/letterbox, and
captions are bottom speech. The missing primitive is a persistent, full-width
text strip along the top edge, configurable per job (and per clip).

## What Changes

- Add a **top text banner** compose layer: an optional full-width strip along
  the top of the video, present for the whole clip, holding a single line (or
  two) of operator text.
- Styling options: **font size** (S/M/L preset scaled to video width), **text
  color** (hex), and a **background strip** on/off with a tint alpha. Reuses
  the bundled font dirs + font allow-list already used by hooks/subtitles.
- New compose toggle `toptext` with `top_text_params` (`text`, `size`,
  `text_color`, `bg_enabled`, `bg_color`, `bg_opacity`). Plumbed through the
  same channels as `banner` today: `ComposeRequest`/`PublishRequest` schemas,
  `compose_layers` layer order, per-clip edit modal, Create recipe +
  pre-selection seeds, bulk apply-to-all, per-clip + bulk publish/export.
- Full-width strip renders against a darkened translucent band so white-on-
  bright footage stays legible; text wraps to at most two centered lines.

## Capabilities

### New Capabilities
- `top-text-banner`: A compose-time layer that burns a full-width, operator-
  supplied text strip along the top edge of a clip, with font-size / color /
  background-tint controls, shared by the Create recipe, the per-clip editor
  and bulk/publish/export paths.

### Modified Capabilities
<!-- None. -->

## Impact

- Backend: new `src/clippyme/domain/top_text.py` (pure Pillow text-strip
  renderer + pure geometry helper, host-tested), `compose.py` new
  `_apply_top_text` layer + ordering, `schemas.py` toggle/params plumbing,
  `publish_service.py` forwards new params.
- Frontend: `seedClipParams.js` (toggle + params seeds), `constants`/`data.js`
  (size/color defaults + palette), a shared `TopTextControls` component, an
  EditClipModal tab + Create-recipe drawer, `bulkApply.js`/`applyEdit.js`
  propagation, and the `realApi.js`/`publish.jsx` payload builders that send
  `toggles.toptext` + `top_text_params`.
- Docs: `.env.example` / README unaffected (no new env knobs).
