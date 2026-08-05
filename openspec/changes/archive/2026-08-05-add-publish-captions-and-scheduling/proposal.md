## Why

Batch publishing many clips forced a single shared caption and one post per
day. Operators needed per-clip captions (each clip has its own hook) and
control over how fast a batch drains onto the platforms (posts-per-day, a
delayed start, and a per-batch timezone), without blowing past a platform's
per-day posting limit.

## What Changes

- The publish modal shows **one caption box per clip** in a batch (single-clip
  publish keeps one box). Each publish body carries that clip's own caption,
  never a shared string.
- Per-clip captions write back to per-clip clip state (`onCaptionChange`), so
  they survive a close/reopen and are seeded from the grid caption editor, then
  the clip's metadata caption, then the clip title.
- Add **batch scheduling** controls to the publish modal: posts-per-day
  (`perDay`), days-from-now start offset, and a per-batch timezone override
  (seeded from the Zernio profile/config timezone). Scheduled posts use
  `start_date = today + daysFromNow + floor(batchPos / perDay)`, and the
  backend SmartScheduler anti-collides same-day posts with a minimum gap.
- "Publish now" forces `schedule_mode: now` regardless of the schedule toggle.

## Capabilities

### New Capabilities
- `publish-scheduling`: per-clip publish captions plus batch scheduling (rate,
  start delay, timezone) for Zernio publishing.

### Modified Capabilities
<!-- None. -->

## Impact

- Frontend: `redesign/publish.jsx` (per-clip caption grid, posts-per-day and
  days-from-now steppers, timezone selector, publish-now override),
  `redesign/results.jsx` (grid caption editing feeding `clipStates.caption`).
- Backend: unchanged; the SmartScheduler already anti-collides same-day slots.
