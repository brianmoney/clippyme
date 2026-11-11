// Cross-job taste memory (#8). ClippyMe never learned from which clips a user
// actually kept vs threw away — every job started cold. This records a small
// rolling signal (clip viral_score + duration + whether kept/published or
// discarded) and distils it into a one-line natural-language hint that is
// appended to the Gemini `instructions` on the NEXT job. The hint rides the
// EXISTING instructions channel, so no backend change is needed: it reaches
// `get_viral_clips` exactly like a user-typed directive. A preferred LENGTH
// above the prompt's default 60s ceiling also raises the `min_duration` floor
// (see `withTaste`), because the hint alone cannot override that hard window.
//
// Pure functions (summarizeTaste) are unit-tested via `node --test`; the
// localStorage I/O is thin and guarded.

const KEY = 'clippyme_taste_v1';
const MAX_EVENTS = 120;        // rolling window — old taste decays out
const MIN_EVENTS = 6;          // below this we don't have enough signal to hint

export function loadTasteEvents() {
  try {
    const raw = localStorage.getItem(KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr : [];
  } catch {
    return [];
  }
}

// action: 'kept' (published) | 'discarded' (removed/disabled) | 'edited'
// (boundaries / smart-cut changed after selection). Only kept/discarded feed
// the hint; the extra fields are retained so the two-stage editorial signals
// can later be joined with real-world performance data.
export function recordTasteEvent({
  viralScore, duration, action,
  originalDuration, editedDuration,
  candidateId, discoveryModel, reviewModel, reviewScore, reviewRank,
  rejectionReason, performance,
}) {
  if (action !== 'kept' && action !== 'discarded' && action !== 'edited') return;
  const ev = {
    s: Number.isFinite(viralScore) ? Math.round(viralScore) : null,
    d: Number.isFinite(duration) ? Math.round(duration) : null,
    a: action,
  };
  if (Number.isFinite(originalDuration)) ev.od = Math.round(originalDuration);
  if (Number.isFinite(editedDuration)) ev.ed = Math.round(editedDuration);
  if (candidateId) ev.cid = String(candidateId);
  if (discoveryModel) ev.dm = String(discoveryModel);
  if (reviewModel) ev.rm = String(reviewModel);
  if (Number.isFinite(reviewScore)) ev.rs = Math.round(reviewScore * 100) / 100;
  if (Number.isFinite(reviewRank)) ev.rr = Math.round(reviewRank);
  if (rejectionReason) ev.rej = String(rejectionReason).slice(0, 240);
  if (performance && typeof performance === 'object') ev.perf = performance;
  try {
    const events = loadTasteEvents();
    events.push(ev);
    const trimmed = events.slice(-MAX_EVENTS);
    localStorage.setItem(KEY, JSON.stringify(trimmed));
  } catch {
    /* localStorage unavailable — taste memory is best-effort */
  }
}

function median(nums) {
  const xs = nums.filter((n) => Number.isFinite(n)).sort((a, b) => a - b);
  if (!xs.length) return null;
  const m = Math.floor(xs.length / 2);
  return xs.length % 2 ? xs[m] : Math.round((xs[m - 1] + xs[m]) / 2);
}

// Pure: events → distilled taste signal. `hint` is the natural-language
// suffix appended to a job's Gemini `instructions`; `minDuration` is the
// numeric per-clip floor that MUST also ride `min_duration`. Reason: the
// prompt enforces a hard {min}–{max}s window (default 15–60s) that outranks
// the free-text hint, so a preference longer than 60s is silently ignored
// unless the floor is raised. `min_duration=M` yields an [M, M+30s] window.
export function tasteSignal(events) {
  const evs = (events || []).filter((e) => e && (e.a === 'kept' || e.a === 'discarded'));
  if (evs.length < MIN_EVENTS) return { hint: '', minDuration: null };

  const kept = evs.filter((e) => e.a === 'kept');
  const discarded = evs.filter((e) => e.a === 'discarded');
  const parts = [];
  let minDuration = null;

  // Preferred length from clips the user kept.
  const keptDur = median(kept.map((e) => e.d));
  if (keptDur) {
    const lo = Math.max(5, keptDur - 6);
    const hi = keptDur + 6;
    parts.push(`prefer clips roughly ${lo}-${hi}s long`);
    minDuration = lo;
  }

  // Score band the user tends to discard: if discarded clips skew low-score.
  const discMedScore = median(discarded.map((e) => e.s));
  const keptMedScore = median(kept.map((e) => e.s));
  if (discMedScore != null && keptMedScore != null && keptMedScore - discMedScore >= 8) {
    parts.push(`avoid weak moments — the user discards clips scoring below about ${keptMedScore}`);
  }

  if (!parts.length) return { hint: '', minDuration: null };
  return { hint: `Based on the user's past edits, ${parts.join(' and ')}.`, minDuration };
}

// Pure: the natural-language hint only (back-compat for callers that just
// append text).
export function summarizeTaste(events) {
  return tasteSignal(events).hint;
}

// Convenience: the suffix to append to a job's AI instructions.
export function tasteInstructionSuffix() {
  return tasteSignal(loadTasteEvents()).hint;
}

// Convenience: the numeric per-clip floor to send as `min_duration` (null when
// there is no duration signal).
export function tasteMinDuration() {
  return tasteSignal(loadTasteEvents()).minDuration;
}
