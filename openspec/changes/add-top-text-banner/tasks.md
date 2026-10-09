## 1. Backend top-text renderer

- [x] 1.1 Add `domain/top_text.py`: pure `top_text_plan` geometry (strip height, font px from size preset + video width, padding), Pillow `render_top_text_png` (full-width canvas, two-line wrap, centred, strip behind text), and `add_top_text_to_video` (ffprobe dims + single ffmpeg overlay pass). Reuse `hooks._resolve_hook_font_path` / the overlong-word splitter pattern; fonts safe.
- [x] 1.2 Host tests `tests/domain/test_top_text.py`: plan geometry presets, wrap-to-two-lines, default/blank-skip, colour clamp, and overlay invocation (monkeypatched ffmpeg/Pillow, mirroring `test_banner.py`).

## 2. Backend compose + schema wiring

- [x] 2.1 Add `_apply_top_text` in `compose.py` (skip on blank text or no strip; output `composed_toptext_{i}.mp4`), run it just before the banner pass; plumb `top_text_params` through `compose_layers` / `_compose_layers_impl` and the `active.get('toptext')` short-circuit handling.
- [x] 2.2 `schemas.py`: add `toptext` to `_ALLOWED_TOGGLES`; add `top_text_params` to `ComposeRequest` and `PublishRequest` overlay-validated fields.
- [x] 2.3 `publish_service.py` forwards `top_text_params` into `compose_layers`.
- [x] 2.4 Compose-order host tests (`tests/domain/test_compose.py`) assert top-text runs after logo/before banner and that `enabled`/blank text skip.

## 3. Frontend seeds, constants, shared controls

- [x] 3.1 `lib/seedClipParams.js`: `seedTopTextParams(preselections)` + `toptext` in `seedToggles` (derived from `top_text_params.enabled`, like `banner`).
- [x] 3.2 `data.js`: `TOP_TEXT_SIZES`, defaults; `presets.js` PRESET_KEYS gains the new create keys.
- [x] 3.3 New `redesign/topTextControls.jsx` (or extend `hookStyle`-style shared component): text input, size Segmented, text-colour swatches/picker, strip on/off + tint + opacity; live preview. Unit tests colocated.
- [x] 3.4 `DEFAULT_OPTS` + Create recipe drawer (`create.jsx`) + `optsToPreselections` (`realApi.js`) map the new keys into `top_text` preselection.

## 4. Frontend edit modal + bulk + publish/export

- [x] 4.1 `editTabs.jsx` TopText tab + `captions.jsx` state/apply plumbing (seeded from `initial?.topTextParams` → preselection → default).
- [x] 4.2 `bulkApply.js` copies `topTextParams` verbatim (shared config) in `clipStateToParams`/`buildClipParams`.
- [x] 4.3 `applyEdit.js` + `realApi.js` (`composeClip`/`exportClip`) + `publish.jsx` send `toggles.toptext` + `top_text_params`.
- [x] 4.4 Frontend tests: seed→payload parity, modal apply, bulkApply copy, publish body shape (mirror existing banner/bulk tests).

## 5. Verification

- [x] 5.1 Run backend host suite (`pytest -m "not integration"`) + ruff.
- [x] 5.2 Run frontend `npm test`, `npm run lint`, `npm run build`.
