## 1. Per-clip captions

- [x] 1.1 Render one caption box per clip in the batch publish modal; single clip keeps one box.
- [x] 1.2 Send each clip's own caption in its publish body (fall back to title).
- [x] 1.3 Write caption edits back to per-clip state via `onCaptionChange` and seed boxes from grid state → metadata caption → title.

## 2. Batch scheduling

- [x] 2.1 Add posts-per-day and days-from-now steppers (visible for multi-clip batches).
- [x] 2.2 Add a timezone selector seeded from the Zernio config, sent with every scheduled body.
- [x] 2.3 Compute `start_date = today + daysFromNow + floor(batchPos / perDay)` and rely on SmartScheduler anti-collision for same-day posts.
- [x] 2.4 "Publish now" forces `schedule_mode: now` with no start date.

## 3. Tests

- [x] 3.1 Frontend tests: distinct captions per clip, seed-from-state, title fallback, perDay spread, days-from-now offset, timezone, publish-now override.
