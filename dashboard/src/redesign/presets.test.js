// Presets must capture EVERY Create-recipe control. A key missing from
// PRESET_KEYS is silently dropped by "Save current" and by a default preset on
// reload — which changed the effective home-page recipe (and therefore every
// generated clip) between sessions.
import { test, expect } from 'vitest';
import { captureOpts } from './presets.js';

test('captureOpts keeps subtitle, hook, grade and banner recipe options', () => {
  const opts = {
    subtitles: true, subMode: 'classic', subPreset: 'classic_white',
    subPosition: 'top', subAlign: 'center', subOffsetY: -12,
    subFont: 'Anton-Regular', subColor: '#FDE700', subStroke: '#111111',
    subFontSize: 42, subOutlineW: 4, subBg: true,
    hooks: true, hookPos: 'bottom', hookSize: 'L',
    hookStyle: { animate: true, text_color: '#FFFFFF' },
    gradePreset: 'warm_cinematic',
    banner: true, bannerPlatform: 'kick', bannerHandle: 'clippyme', bannerYPct: 0.8,
    minDuration: 45,
  };
  expect(captureOpts(opts)).toMatchObject(opts);
});
