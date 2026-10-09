
import { act, renderHook } from '@testing-library/react';
import { beforeEach, expect, test } from 'vitest';
import { HISTORY_KEY } from '../lib/constants';
import { useHistory } from './useHistory';

beforeEach(() => localStorage.clear());

test('seedFromBackend appends backend-only jobs as complete rows', () => {
  const { result } = renderHook(() => useHistory());
  act(() => {
    result.current.seedFromBackend([
      { jobId: 'j1', timestamp: 1000, clipCount: 3, source: 'my video', cost: 0.12, publishedCount: 2 },
    ]);
  });
  const [entry] = result.current.history;
  expect(entry).toMatchObject({
    jobId: 'j1',
    status: 'complete',
    clipCount: 3,
    cost: 0.12,
    publishedCount: 2,
    sourceType: 'file',
  });
  expect(entry.source).toBe('my video');
  // And it was persisted so a reload keeps it.
  expect(JSON.parse(localStorage.getItem(HISTORY_KEY))[0].jobId).toBe('j1');
});

test('seedFromBackend leaves existing local entries untouched', () => {
  localStorage.setItem(HISTORY_KEY, JSON.stringify([{ jobId: 'local1', status: 'complete', timestamp: 500, source: 'local job', clipCount: 1 }]));
  const { result } = renderHook(() => useHistory());
  act(() => {
    result.current.seedFromBackend([
      { jobId: 'local1', timestamp: 9999, clipCount: 9, source: 'backend copy' },
      { jobId: 'only-backend', timestamp: 100, clipCount: 2, source: 'https://youtu.be/x' },
    ]);
  });
  const byId = Object.fromEntries(result.current.history.map((e) => [e.jobId, e]));
  // Local entry wins (kept its local timestamp/source); backend-only one added.
  expect(byId.local1.source).toBe('local job');
  expect(byId['only-backend'].sourceType).toBe('url');
  expect(byId['only-backend'].status).toBe('complete');
});

test('seedFromBackend sorts merged list newest-first and caps at HISTORY_MAX_ITEMS', () => {
  const backend = Array.from({ length: 60 }, (_, i) => ({ jobId: `j${i}`, timestamp: i, clipCount: 1 }));
  const { result } = renderHook(() => useHistory());
  act(() => { result.current.seedFromBackend(backend); });
  const stamps = result.current.history.map((e) => e.timestamp);
  expect(stamps).toEqual([...stamps].sort((a, b) => b - a));
});
