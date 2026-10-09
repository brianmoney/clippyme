
import { useCallback, useEffect, useState } from 'react';
import { HISTORY_KEY, HISTORY_MAX_ITEMS } from '../lib/constants';
import { readStoredJson, removeStoredValue, subscribeStoredJson, writeStoredJson } from '../lib/storage';

const validHistory = (value) => Array.isArray(value);
const normalize = (value) => (Array.isArray(value) ? value.filter((entry) => entry && typeof entry.jobId === 'string') : []);

export function useHistory() {
  const [history, setHistoryState] = useState(() => normalize(readStoredJson(HISTORY_KEY, [], { validate: validHistory })));

  useEffect(() => subscribeStoredJson(HISTORY_KEY, (value) => setHistoryState(normalize(value)), { validate: validHistory }), []);

  const replaceHistory = useCallback((next) => {
    const value = normalize(typeof next === 'function' ? next(history) : next).slice(0, HISTORY_MAX_ITEMS);
    setHistoryState(value);
    writeStoredJson(HISTORY_KEY, value);
  }, [history]);

  // Merge the backend's full job scan into the localStorage list so jobs that
  // exist on disk stay openable even when this browser never ran them (fresh
  // profile, cleared storage, or a job processed earlier). Local entries win
  // for the same jobId (they carry this-browser state); backend-only jobs are
  // appended as 'complete' rows the user can open.
  const seedFromBackend = useCallback((backendJobs) => {
    if (!Array.isArray(backendJobs) || !backendJobs.length) return;
    const local = normalize(history);
    const byId = new Map(local.map((e) => [e.jobId, e]));
    let added = 0;
    for (const j of backendJobs) {
      if (!j.jobId || byId.has(j.jobId)) continue;
      const source = j.source || j.title || j.jobId;
      byId.set(j.jobId, {
        jobId: j.jobId,
        status: 'complete',
        timestamp: Number(j.timestamp) || Date.now(),
        source,
        sourceType: /^https?:\/\//i.test(source) ? 'url' : 'file',
        title: source,
        clipCount: j.clipCount || 0,
        cost: j.cost != null ? Number(j.cost) : null,
        publishedCount: j.publishedCount || 0,
      });
      added++;
    }
    if (!added) return;
    const merged = [...byId.values()]
      .sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0))
      .slice(0, HISTORY_MAX_ITEMS);
    setHistoryState(merged);
    writeStoredJson(HISTORY_KEY, merged);
  }, [history]);

  const saveToHistory = useCallback((entry) => {
    if (!entry?.jobId) return;
    setHistoryState((previous) => {
      const updated = [entry, ...previous.filter((item) => item.jobId !== entry.jobId)].slice(0, HISTORY_MAX_ITEMS);
      writeStoredJson(HISTORY_KEY, updated);
      return updated;
    });
  }, []);

  const purgeJobStorage = useCallback((jobId) => {
    removeStoredValue(`clippyme_clip_states_${jobId}`);
    removeStoredValue(`clippyme_preselections_job_${jobId}`);
  }, []);

  const deleteFromHistory = useCallback((jobId) => {
    setHistoryState((previous) => {
      const updated = previous.filter((entry) => entry.jobId !== jobId);
      writeStoredJson(HISTORY_KEY, updated);
      return updated;
    });
    purgeJobStorage(jobId);
  }, [purgeJobStorage]);

  const clearHistory = useCallback(() => {
    setHistoryState((previous) => {
      previous.forEach((entry) => entry?.jobId && purgeJobStorage(entry.jobId));
      return [];
    });
    removeStoredValue(HISTORY_KEY);
  }, [purgeJobStorage]);

  return { history, setHistory: replaceHistory, saveToHistory, seedFromBackend, deleteFromHistory, clearHistory };
}
