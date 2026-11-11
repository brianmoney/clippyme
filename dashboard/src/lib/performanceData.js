// Optional future performance-data interface.
//
// The two-stage pipeline records editorial signals (candidate id, review score,
// rank, models, acceptance/edits) in taste events. When real platform analytics
// become available, normalize them with this module and join on `cid`. Nothing
// here makes an analytics integration a prerequisite: metrics are treated as
// noisy observations, never as proof that a model decision was right or wrong.

export const PERFORMANCE_FIELDS = Object.freeze([
  'platform',
  'published_at',
  'views',
  'impressions',
  'avg_watch_seconds',
  'three_second_retention',
  'completion_rate',
  'shares_per_1k_views',
  'saves_per_1k_views',
  'engagement_vs_channel',
]);

const NUMERIC_FIELDS = new Set([
  'views', 'impressions', 'avg_watch_seconds', 'three_second_retention',
  'completion_rate', 'shares_per_1k_views', 'saves_per_1k_views',
  'engagement_vs_channel',
]);

/** Normalize one raw analytics record into the stable shape (or null). */
export function normalizePerformanceRecord(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const out = {};
  for (const field of PERFORMANCE_FIELDS) {
    if (!(field in raw)) continue;
    const value = raw[field];
    if (NUMERIC_FIELDS.has(field)) {
      const number = Number(value);
      if (Number.isFinite(number)) out[field] = number;
    } else if (value != null && String(value).trim()) {
      out[field] = String(value).trim();
    }
  }
  return Object.keys(out).length ? out : null;
}

/**
 * Join performance records onto taste events by candidate id.
 * Records may be `{candidate_id, ...metrics}` or `{[candidateId]: metrics}`.
 * Returns a new array; events without a match are left untouched.
 */
export function attachPerformance(events, records) {
  const byId = new Map();
  if (Array.isArray(records)) {
    for (const record of records) {
      const id = record?.candidate_id || record?.cid;
      const normalized = normalizePerformanceRecord(record);
      if (id && normalized) byId.set(String(id), normalized);
    }
  } else if (records && typeof records === 'object') {
    for (const [id, record] of Object.entries(records)) {
      const normalized = normalizePerformanceRecord(record);
      if (normalized) byId.set(String(id), normalized);
    }
  }
  return (events || []).map((event) => {
    const cid = event?.cid ? String(event.cid) : '';
    const performance = cid ? byId.get(cid) : undefined;
    return performance ? { ...event, perf: performance } : event;
  });
}

/**
 * Summarize available performance observations. Explicitly reports coverage so
 * a small/noisy sample cannot be mistaken for a reliable model evaluation.
 */
export function performanceSummary(events) {
  const observed = (events || []).filter((event) => event?.perf);
  const withMetric = (field) => observed
    .map((event) => Number(event.perf[field]))
    .filter((value) => Number.isFinite(value));
  const median = (values) => {
    if (!values.length) return null;
    const sorted = [...values].sort((a, b) => a - b);
    const middle = Math.floor(sorted.length / 2);
    return sorted.length % 2
      ? sorted[middle]
      : (sorted[middle - 1] + sorted[middle]) / 2;
  };
  return {
    observations: observed.length,
    total_events: (events || []).length,
    median_views: median(withMetric('views')),
    median_three_second_retention: median(withMetric('three_second_retention')),
    median_completion_rate: median(withMetric('completion_rate')),
    median_shares_per_1k: median(withMetric('shares_per_1k_views')),
    median_saves_per_1k: median(withMetric('saves_per_1k_views')),
    caveat: 'Engagement metrics are noisy observations, not proof of clip quality.',
  };
}
