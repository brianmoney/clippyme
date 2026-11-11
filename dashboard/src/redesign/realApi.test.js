import { test } from 'vitest';
import assert from 'node:assert/strict';

// Vitest runs with a jsdom environment, so window.location.origin is real —
// the old plain-Node `globalThis.window` stub is gone.
import { optsToPreselections, clipVideoSrc, clipPreviewSrc, fmtDuration, exportClip, deleteHistoryJob } from './realApi.js';

// --- optsToPreselections: the Create-tab → backend translation layer --------

test('reframe mode: legacy object alias normalizes to subject', () => {
  assert.equal(optsToPreselections({ reframeMode: 'object' }).reframe_mode, 'subject');
  assert.equal(optsToPreselections({ reframeMode: 'subject' }).reframe_mode, 'subject');
  assert.equal(optsToPreselections({ reframeMode: 'disabled' }).reframe_mode, 'disabled');
});

test('reframe mode: legacy boolean fallback, default auto', () => {
  assert.equal(optsToPreselections({}).reframe_mode, 'auto');
  assert.equal(optsToPreselections({ reframe: false }).reframe_mode, 'disabled');
});

test('model override: blank is omitted, value passes through trimmed', () => {
  assert.equal(optsToPreselections({}).model, undefined);
  assert.equal(optsToPreselections({ model: '  ' }).model, undefined);
  assert.equal(optsToPreselections({ model: ' gemini-2.5-pro ' }).model, 'gemini-2.5-pro');
});

test('karaoke subtitles carry colours but not classic typography', () => {
  const p = optsToPreselections({
    subtitles: true, subMode: 'karaoke', subPreset: 'hormozi_bold',
    subColor: '#FDE700', subStroke: '#111111', subFontSize: 48,
  });
  assert.equal(p.subtitles.preset, 'hormozi_bold');
  assert.equal(p.subtitles.font_color, '#FDE700');
  assert.equal(p.subtitles.outline_color, '#111111');
  assert.equal(p.subtitles.font_size, 48);
  assert.equal('font' in p.subtitles, false, 'classic-only font key leaked into karaoke');
});

test('karaoke font_size 0 means Auto and is omitted', () => {
  const p = optsToPreselections({ subtitles: true, subMode: 'karaoke', subFontSize: 0 });
  assert.equal('font_size' in p.subtitles, false);
});

test('classic subtitles carry font/border/background', () => {
  const p = optsToPreselections({
    subtitles: true, subMode: 'classic', subFont: 'Anton-Regular',
    subColor: '#581BBA', subOutlineW: 3, subBg: true,
  });
  assert.equal(p.subtitles.font, 'Anton-Regular');
  assert.equal(p.subtitles.font_color, '#581BBA');
  assert.equal(p.subtitles.border_width, 3);
  assert.equal(p.subtitles.bg_opacity, 0.6);
  assert.equal(p.subtitles.bg_color, '#000000');
});

test('subtitles off → false; grade none → false; logo off → false', () => {
  const p = optsToPreselections({ subtitles: false, gradePreset: 'none', logo: false });
  assert.equal(p.subtitles, false);
  assert.equal(p.grade, false);
  assert.equal(p.logo, false);
});

test('grade preset flows through when set', () => {
  assert.deepEqual(optsToPreselections({ gradePreset: 'vivid_pop' }).grade, { preset: 'vivid_pop' });
});

test('top_text preselection carries text + style when enabled', () => {
  const p = optsToPreselections({
    topText: true,
    topTextStyle: { text: 'SB 686', size: 'L', text_color: '#FFE000', bg_enabled: true, bg_color: '#000000', bg_opacity: 0.8 },
  });
  assert.equal(p.top_text.enabled, true);
  assert.equal(p.top_text.text, 'SB 686');
  assert.equal(p.top_text.size, 'L');
  assert.equal(p.top_text.text_color, '#FFE000');
});

test('top_text is false when disabled or text blank', () => {
  assert.equal(optsToPreselections({ topText: true, topTextStyle: {} }).top_text, false);
  assert.equal(optsToPreselections({ topText: false }).top_text, false);
});

test('exportClip sends top_text_params when the toptext toggle is on', async () => {
  const bodies = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (url, opts) => {
    bodies.push(JSON.parse(opts.body));
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ composed_url: '/videos/j/composed.mp4' }) });
  };
  try {
    const clip = { original_index: 0, video_url: '/videos/j/clip_1.mp4' };
    const state = {
      toggles: { toptext: true, subtitles: false, hook: false, logo: false, grade: false, banner: false, smartcut: false },
      topTextParams: { enabled: true, text: 'St. Johns BOCC', size: 'M', text_color: '#FFFFFF', bg_enabled: true, bg_color: '#000000', bg_opacity: 0.55 },
    };
    await exportClip('job-1', 0, clip, state, {});
    assert.equal(bodies.length, 1);
    assert.equal(bodies[0].toggles.toptext, true);
    assert.equal(bodies[0].top_text_params.text, 'St. Johns BOCC');
  } finally {
    globalThis.fetch = originalFetch;
  }
});

// --- URL safety: a malicious API response must never become an executable src

test('clipVideoSrc neutralizes javascript: and data: schemes', () => {
  for (const evil of ['javascript:alert(1)', 'data:text/html,<script>x</script>']) {
    const src = clipVideoSrc({ video_url: evil });
    assert.equal(src.startsWith('javascript:'), false);
    assert.equal(src.startsWith('data:'), false);
    assert.equal(src.startsWith('/'), true, `expected inert relative path, got ${src}`);
  }
});

test('clipVideoSrc appends the cache-buster correctly', () => {
  assert.equal(clipVideoSrc({ video_url: '/videos/j/clip_1.mp4' }, 99).endsWith('?v=99'), true);
  assert.equal(clipVideoSrc({ video_url: '/videos/j/c.mp4?x=1' }, 99).endsWith('&v=99'), true);
});

test('clipPreviewSrc prefers the composed previewUrl over the raw clip', () => {
  const clip = { video_url: '/videos/j/clip_1.mp4' };
  const raw = clipPreviewSrc(clip, {});
  assert.equal(raw.includes('clip_1.mp4'), true);
  const composed = clipPreviewSrc(clip, { previewUrl: '/videos/j/composed_clip_0.mp4', previewBust: 7 });
  assert.equal(composed.includes('composed_clip_0.mp4'), true);
  assert.equal(composed.endsWith('?v=7'), true);
});

test('fmtDuration renders m:ss with zero-padded seconds', () => {
  assert.equal(fmtDuration(0, 65), '1:05');
  assert.equal(fmtDuration(10, 10), '0:00');
  assert.equal(fmtDuration(0, 599.6), '10:00');
});

// --- I-1 regression: per-clip endpoints must resolve by the backend's
// ABSOLUTE `shorts` position (`original_index`), not the frontend array
// position — they diverge once a manual-publish gap skips a
// deleted_after_publish clip (job_results._build_clips).

test('exportClip composes against clip.original_index, not the array position', async () => {
  const calls = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (url) => {
    calls.push(url);
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ composed_url: '/videos/j/composed.mp4' }) });
  };
  try {
    // Clip B sits at array position 1 but is absolute shorts position 2 (a
    // deleted_after_publish clip at position 1 was skipped upstream).
    const clip = { original_index: 2, video_url: '/videos/j/clip_3.mp4' };
    const state = { toggles: { subtitles: true } };
    await exportClip('job-1', 1, clip, state, {});
    assert.equal(calls.length, 1);
    assert.match(String(calls[0]), /\/api\/compose\/job-1\/2$/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

// --- history delete must hit the backend (files off disk), not just the
// localStorage entry — a row dismissal that left the output/ dir behind was
// silently leaking storage.

test('deleteHistoryJob issues a DELETE and resolves on 200', async () => {
  const calls = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (url, init = {}) => {
    calls.push({ url, method: init.method });
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ success: true }) });
  };
  try {
    const body = await deleteHistoryJob('abc-123');
    assert.equal(body.success, true);
    assert.equal(calls.length, 1);
    assert.match(String(calls[0].url), /\/api\/history\/abc-123$/);
    assert.equal(calls[0].method, 'DELETE');
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('deleteHistoryJob surfaces non-OK status so callers can handle 404/409', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = () => Promise.resolve({ ok: false, status: 409 });
  try {
    let caught = null;
    try { await deleteHistoryJob('abc-123'); } catch (e) { caught = e; }
    assert.ok(caught, 'expected deleteHistoryJob to throw on non-OK');
    assert.equal(caught.status, 409);
  } finally {
    globalThis.fetch = originalFetch;
  }
});


test('target_clips: omitted in Auto mode, forwarded in custom mode', () => {
  assert.equal(optsToPreselections({ clipsAuto: true, clips: 7 }).target_clips, undefined);
  assert.equal(optsToPreselections({ clipsAuto: false, clips: 7 }).target_clips, 7);
  assert.equal(optsToPreselections({ clipsAuto: false, clips: 0 }).target_clips, undefined);
});
