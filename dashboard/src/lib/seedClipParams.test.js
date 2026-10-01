import { test } from 'vitest';
import assert from 'node:assert/strict';
import {
  seedToggles, seedGradeParams, seedLogoParams, seedHookParams, seedSubtitleParams, seedBannerParams, seedTopTextParams,
} from './seedClipParams.js';

// seedClipParams is the single seam that keeps the Create pre-selection panel,
// ClipCard export, EditClipModal and PublishModal all producing the SAME param
// shape compose.py reads — its own header documents a past preview/output
// drift (camelCase fontSize silently dropped). These tests pin the contract.

test('seedToggles defaults everything off', () => {
  assert.deepEqual(seedToggles(undefined), {
    smartcut: false, hook: false, subtitles: false, logo: false, grade: false, banner: false, toptext: false,
  });
});

test('seedToggles turns toptext on only when a real pre-selection enables it', () => {
  assert.equal(seedToggles({ top_text: { enabled: true, text: 'SB 686' } }).toptext, true);
  assert.equal(seedToggles({ top_text: { enabled: false, text: 'SB 686' } }).toptext, false);
  assert.equal(seedToggles({ top_text: false }).toptext, false);
  assert.equal(seedToggles(undefined).toptext, false);
});

test('seedTopTextParams prefers an explicit pre-selection', () => {
  const pre = {
    top_text: {
      enabled: true, text: 'St. Johns BOCC', size: 'L',
      text_color: '#FFE000', bg_enabled: true, bg_color: '#000000', bg_opacity: 0.8,
    },
  };
  assert.deepEqual(seedTopTextParams(pre), {
    enabled: true, text: 'St. Johns BOCC', size: 'L',
    text_color: '#FFE000', bg_enabled: true, bg_color: '#000000', bg_opacity: 0.8,
  });
});

test('seedTopTextParams defaults disabled with backend-matching style', () => {
  const out = seedTopTextParams(undefined);
  assert.equal(out.enabled, false);
  assert.equal(out.text, 'St. Johns BOCC Public comments — SB 686'); // example pre-filled
  assert.equal(out.size, 'M');
  assert.equal(out.text_color, '#FFFFFF');
  assert.equal(out.bg_enabled, true);
  assert.equal(out.bg_color, '#000000');
  assert.equal(out.bg_opacity, 0.55);
});

test('seedTopTextParams falls back to the example when the pre-selection has no text', () => {
  const out = seedTopTextParams({ top_text: { enabled: true, size: 'S' } });
  assert.equal(out.enabled, true);
  assert.equal(out.text, 'St. Johns BOCC Public comments — SB 686');
  assert.equal(out.size, 'S');
});

test('seedBannerParams prefers an explicit pre-selection over the source suggestion', () => {
  const pre = { banner: { enabled: true, platform: 'twitch', handle: 'xqc', y_pct: 0.7 } };
  assert.deepEqual(seedBannerParams(pre, { platform: 'kick', handle: 'other' }),
    { enabled: true, platform: 'twitch', handle: 'xqc', y_pct: 0.7 });
});

test('seedBannerParams falls back to the job source_info auto-suggestion', () => {
  assert.deepEqual(seedBannerParams(undefined, { platform: 'youtube', handle: 'GrenBaudLounge' }),
    { enabled: true, platform: 'youtube', handle: 'GrenBaudLounge', y_pct: 0.85 });
});

test('seedBannerParams defaults disabled when neither is present', () => {
  assert.deepEqual(seedBannerParams(undefined, null),
    { enabled: false, platform: 'kick', handle: '', y_pct: 0.85 });
  assert.equal(seedBannerParams({ banner: false }, null).enabled, false);
});

test('seedToggles turns grade on only for a real preset', () => {
  assert.equal(seedToggles({ grade: { preset: 'warm_cinematic' } }).grade, true);
  assert.equal(seedToggles({ grade: { preset: 'none' } }).grade, false);
  assert.equal(seedToggles({ grade: {} }).grade, false);
  assert.equal(seedToggles({ grade: false }).grade, false);
});

test('seedGradeParams and seedLogoParams fall back to backend defaults', () => {
  assert.deepEqual(seedGradeParams(undefined), { preset: 'none' });
  assert.deepEqual(seedLogoParams(undefined), { position: 'top-right', size: 'M' });
  assert.deepEqual(
    seedLogoParams({ logo: { position: 'bottom-left', size: 'L' } }),
    { position: 'bottom-left', size: 'L' },
  );
});

test('seedHookParams prefers viral_hook_text and carries style keys', () => {
  const clip = { viral_hook_text: 'WATCH THIS', hook_text: 'ignored' };
  const out = seedHookParams(clip, { hook: { position: 'bottom', size: 'M', text_color: '#FF0000' } });
  assert.equal(out.text, 'WATCH THIS');
  assert.equal(out.position, 'bottom');
  assert.equal(out.size, 'M');
  assert.equal(out.text_color, '#FF0000');
  assert.equal(out.offset_y, 0);
});

test('seedHookParams omits style keys the user never set', () => {
  const out = seedHookParams({}, { hook: {} });
  for (const k of ['bg_enabled', 'bg_color', 'text_color', 'outline_width', 'font']) {
    assert.equal(k in out, false, `unset style key ${k} must not be forwarded`);
  }
});

test('seedSubtitleParams defaults match the backend contract', () => {
  const out = seedSubtitleParams(undefined);
  assert.equal(out.preset, 'classic_white');
  assert.equal(out.mode, 'karaoke');
  assert.equal(out.position, 'bottom');
  assert.equal(out.align, 'left');
  assert.equal(out.font_color, '#FFFFFF');
  assert.equal(out.outline_color, '#000000');
  assert.equal(out.offset_y, 0);
  assert.equal(out.bg_opacity, 0);
});

test('seedSubtitleParams only forwards explicit overrides', () => {
  // uppercase/font_size/outline_width/words_per_group omitted → the preset's
  // own value applies (a hard-coded true used to force lower-case presets
  // upper — the exact regression this contract prevents).
  const bare = seedSubtitleParams({ subtitles: {} });
  for (const k of ['uppercase', 'font_size', 'outline_width', 'words_per_group']) {
    assert.equal(k in bare, false, `${k} must be omitted unless explicitly set`);
  }
  const set = seedSubtitleParams({
    subtitles: { uppercase: false, font_size: 48, outline_width: 3, words_per_group: 4 },
  });
  assert.equal(set.uppercase, false);
  assert.equal(set.font_size, 48);
  assert.equal(set.outline_width, 3);
  assert.equal(set.words_per_group, 4);
});

test('seedSubtitleParams sends a font only for classic', () => {
  // Karaoke has no font picker; a `font` key would override the preset font.
  assert.equal('font' in seedSubtitleParams(undefined), false);
  assert.equal('font' in seedSubtitleParams({ subtitles: { mode: 'karaoke', preset: 'hormozi_bold' } }), false);
  assert.equal('font' in seedSubtitleParams({ subtitles: { mode: 'karaoke', font: 'Montserrat-Black' } }), false);
  assert.equal(seedSubtitleParams({ subtitles: { mode: 'classic' } }).font, 'Montserrat-Black');
  assert.equal(seedSubtitleParams({ subtitles: { mode: 'classic', font: 'Bangers-Regular' } }).font, 'Bangers-Regular');
});

test('seedSubtitleParams uses canonical snake_case font_size (not fontSize)', () => {
  const out = seedSubtitleParams({ subtitles: { font_size: 40 } });
  assert.equal(out.font_size, 40);
  assert.equal('fontSize' in out, false);
});
