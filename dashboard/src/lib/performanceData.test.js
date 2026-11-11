import { test } from 'vitest';
import assert from 'node:assert/strict';

import {
  normalizePerformanceRecord, attachPerformance, performanceSummary,
  PERFORMANCE_FIELDS,
} from './performanceData.js';

test('normalizePerformanceRecord keeps known fields and coerces numerics', () => {
  const out = normalizePerformanceRecord({
    platform: 'tiktok', published_at: '2026-10-01', views: '12000',
    completion_rate: '0.42', three_second_retention: '0.81',
    unknown: 'ignored',
  });
  assert.equal(out.platform, 'tiktok');
  assert.equal(out.views, 12000);
  assert.equal(out.completion_rate, 0.42);
  assert.equal(out.unknown, undefined);
  assert.ok(PERFORMANCE_FIELDS.includes('shares_per_1k_views'));
});

test('normalizePerformanceRecord rejects empty/garbage records', () => {
  assert.equal(normalizePerformanceRecord(null), null);
  assert.equal(normalizePerformanceRecord({ unknown: 1 }), null);
});

test('attachPerformance joins by candidate id without mutating events', () => {
  const events = [
    { a: 'kept', cid: 'vc_1', s: 90, d: 20 },
    { a: 'kept', cid: 'vc_2', s: 80, d: 20 },
  ];
  const out = attachPerformance(events, [{ candidate_id: 'vc_1', views: 1000 }]);
  assert.equal(out[0].perf.views, 1000);
  assert.equal(out[1].perf, undefined);
  assert.equal(events[0].perf, undefined);
});

test('performanceSummary reports coverage and a caveat', () => {
  const events = [
    { cid: 'a', perf: { views: 100, completion_rate: 0.4 } },
    { cid: 'b', perf: { views: 300, completion_rate: 0.6 } },
    { cid: 'c' },
  ];
  const summary = performanceSummary(events);
  assert.equal(summary.observations, 2);
  assert.equal(summary.total_events, 3);
  assert.equal(summary.median_views, 200);
  assert.match(summary.caveat, /noisy/);
});
