
import { useCallback, useEffect, useState } from 'react';
import { readStoredJson, removeStoredValue, subscribeStoredJson, writeStoredJson } from '../lib/storage';

const validStates = (value) => value && typeof value === 'object' && !Array.isArray(value);

// A compose can take a couple of minutes. Keep the "processing" overlay across
// a reload during that window (clearing it made the card show the raw clip as if
// the render had finished); stale flags from a crashed render expire.
const PROCESSING_STALE_MS = 15 * 60 * 1000;
const RECIPE_LABELS = { subtitles: 'Applying subtitles…', hook: 'Applying hook…' };

function keyFor(jobId) { return `clippyme_clip_states_${jobId}`; }

function normalize(value) {
  if (!validStates(value)) return {};
  const now = Date.now();
  const next = {};
  for (const [key, state] of Object.entries(value)) {
    if (!/^\d+$/.test(key) || !state || typeof state !== 'object') continue;
    let clean = { ...state };
    if (clean.processing) {
      const since = Number(clean.processingSince);
      if (!Number.isFinite(since) || now - since > PROCESSING_STALE_MS) {
        clean = { ...clean, processing: false, processingSince: null };
      }
    }
    // Older builds saved a `font` with every karaoke edit although karaoke has
    // no font picker; dropping it lets the preset's font apply again.
    const sp = clean.subtitleParams;
    if (sp && sp.mode !== 'classic' && 'font' in sp) {
      const { font: _legacy, ...rest } = sp;
      clean = { ...clean, subtitleParams: rest };
    }
    next[key] = clean;
  }
  return next;
}

export function processingLabel(state) {
  const toggles = state?.toggles || {};
  if (toggles.subtitles) return RECIPE_LABELS.subtitles;
  if (toggles.hook) return RECIPE_LABELS.hook;
  return 'Reprocessing…';
}

export function useClipStates(jobId) {
  const [states, setStates] = useState({});

  useEffect(() => {
    if (!jobId) { setStates({}); return undefined; }
    const key = keyFor(jobId);
    setStates(normalize(readStoredJson(key, {}, { validate: validStates })));
    return subscribeStoredJson(key, (value) => setStates(normalize(value)), { validate: validStates });
  }, [jobId]);

  const updateClip = useCallback((index, patch) => {
    if (!Number.isInteger(Number(index)) || !patch || typeof patch !== 'object') return;
    setStates((previous) => {
      const next = { ...previous, [index]: { ...(previous[index] || {}), ...patch } };
      if (jobId) writeStoredJson(keyFor(jobId), next);
      return next;
    });
  }, [jobId]);

  const getClipState = useCallback((index) => states[index] || {}, [states]);
  const reset = useCallback(() => {
    setStates({});
    if (jobId) removeStoredValue(keyFor(jobId));
  }, [jobId]);

  return { states, updateClip, getClipState, reset };
}
