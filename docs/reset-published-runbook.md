# Resetting the "published" state on clips

Operational runbook for clearing the *published* indication after a clip batch
was posted to the wrong profile, needs to be re-posted elsewhere, or was only
scheduled (then cancelled) and never actually went live.

## How "published" is represented

There are **two independent** pieces of state that together form what a user
sees as "this clip is published". They live in different places and must be
cleared separately:

| Surface | Where it lives | Purpose |
|---|---|---|
| Per-clip `published` record | Job metadata JSON on the backend (`output/<job_id>/*_metadata.json`, each `shorts[i].published` array) | Append-only history of every Zernio post for the clip (platforms, post id, schedule, profile). Feeds the History tab's per-job "N published" badge (`history_service.scan_history` → `publishedCount`). It does **not** block re-publishing. |
| Per-clip `publishedAt` timestamp | Browser `localStorage`, key `clippyme_clip_states_<job_id>` | Drives the little "published" chip on each clip card in the results grid (`results.jsx` renders it when `state?.publishedAt` is set). Purely cosmetic; also records a "kept" taste event once. |

Notes:

- Neither flag gates re-publishing — the `PublishModal` is always available for
  every clip. Clearing them is **cosmetic / history hygiene**, not a
  permission gate.
- If clips were published with the live-monitor's `delete_after_publish`
  option (default on), their artifacts are removed and the metadata entry is
  marked `deleted_after_publish` after a *confirmed* publish — such clips are
  filtered out of the results (`job_results._build_clips`) and cannot be
  re-published without re-processing. The reset procedure below does not
  resurrect them.
- The durable per-clip `published` record is written best-effort by
  `record_clip_publish` (`domain/job_artifacts.py`); a hiccup there can leave
  a published clip without a record, and cancelling a *scheduled* Zernio post
  does **not** remove the record that was already appended.

## Before you start

1. Identify the job. In the app, the job id is the URL/history entry; on disk
   it is the directory name under `output/` (e.g.
   `output/dc4cd89e-7d96-4bcf-b670-2b12353be054/`). Confirm it contains the
   expected `*_metadata.json` and count the clips you intend to reset.
2. **Cancel the Zernio posts first.** Resetting the metadata flags does not
   touch Zernio. If the clips were *scheduled* (not yet published) and you
   clear the record, the scheduled post will still fire at its appointed time
   and you will have a duplicate after re-publishing. Delete/cancel the
   pending posts in Zernio before republishing.
3. Back up the metadata file before editing it (see below).

## Part A — clear the backend per-clip `published` records

Run inside the backend container (or on the host if the `output/` directory is
writable by your user). The file is owned by the container user and mode
`0600`, so on the host this needs `sudo`. It uses the same atomic-write pattern
as the app (`tmp` + `os.replace`) and preserves ownership/mode.

Replace the `<job_id>` and `<metadata>.json` below:

```bash
sudo cp "output/<job_id>/<metadata>.json" /tmp/<job_id>-metadata-backup.json

sudo python3 - <<'EOF'
import json, os, tempfile

path = "output/<job_id>/<metadata>.json"
with open(path) as f:
    data = json.load(f)

removed = 0
for clip in data.get("shorts", []):
    if "published" in clip:
        del clip["published"]
        removed += 1

st = os.stat(path)
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".meta_", suffix=".tmp")
try:
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, ensure_ascii=False)
    os.chmod(tmp, st.st_mode)
    os.chown(tmp, st.st_uid, st.st_gid)
    os.replace(tmp, path)
finally:
    if os.path.exists(tmp):
        os.unlink(tmp)

print(f"cleared published on {removed}/{len(data.get('shorts', []))} clips")
EOF
```

Verify — every clip's `published` key is gone and the file is valid JSON:

```bash
sudo python3 -c "
import json
d = json.load(open('output/<job_id>/<metadata>.json'))
print('clips:', len(d['shorts']))
print('any published left:', any('published' in c for c in d['shorts']))
"
```

The History tab's per-job published badge (a count derived from these records)
updates on next load/refresh.

## Part B — clear the browser per-clip `publishedAt` chip

The results grid's "published" chip on each clip card comes from browser
`localStorage`, not the backend, so Part A alone will not remove it. The chip
is shown when the per-clip state object has a `publishedAt` field.

Targeted (recommended — keeps captions, toggles, drop ranges and all other
per-clip state):

1. Open the app in Chrome and open DevTools (**F12** → **Console**).
2. Paste and run:

```js
(() => {
  const key = 'clippyme_clip_states_<job_id>';
  const raw = localStorage.getItem(key);
  if (!raw) { console.log('No stored per-clip state for this job.'); return; }
  const states = JSON.parse(raw);
  let removed = 0;
  for (const st of Object.values(states)) {
    if (st && typeof st === 'object' && 'publishedAt' in st) {
      delete st.publishedAt;
      removed++;
    }
  }
  localStorage.setItem(key, JSON.stringify(states));
  console.log(`Removed publishedAt from ${removed} clip(s). Reload to see the change.`);
})();
```

3. Reload the page (**F5**). The chips disappear.

Alternative — wipe the whole per-clip state for the job (also erases any
hand-edited captions/settings/trims for that job, which then re-seed from clip
metadata / preselections):

```js
localStorage.removeItem('clippyme_clip_states_<job_id>');
```

Then reload.

## Why the chip sometimes returns

The `publishedAt` is set in `RedesignApp.jsx` `onPublished` whenever the
`PublishModal` reports a clip as published, even if the underlying Zernio post
was only *scheduled* (and later cancelled) or subsequently failed. There is no
UI to unset it — that is what the console snippet above is for.

## Setting the YouTube upload defaults (category, visibility, …)

Every Zernio publish to YouTube now carries per-video metadata in the target's
`platformSpecificData`: `title`, `categoryId`, `madeForKids`,
`containsSyntheticMedia`, and `visibility`. These come from the Zernio
**profile** that publishes (manual modal → the profile dropdown; live monitor →
the default profile), falling back to Zernio's own defaults (`categoryId: "22"`
People & Blogs, `visibility: public`, `madeForKids: false`,
`containsSyntheticMedia: false`) when a profile has no custom values.

Set them per profile in **Settings → Publishing → the profile card →
"YouTube upload defaults"**: a category dropdown, visibility dropdown, and
"Made for kids (COPPA)" / "Contains AI-generated content" checkboxes. Save with
the profile's Save button.

Notes:

- The live monitor auto-publishes with the **default** profile, so its YouTube
  defaults come from the default profile's block.
- Backend plumbing: `social_publisher._with_youtube_defaults` (builds the
  platform payload), `publish_service.publish_clip_flow` + `live_monitor` (pass
  the profile's snake_case block, translated by
  `social_publisher._camel_youtube_defaults`), and
  `storage.config_store` normalizes/validates the stored per-profile block.

## History is empty even though jobs exist on disk

The History tab's list is stored per-browser in `localStorage`
(`clippyme_history`) and is populated **as jobs complete in that browser** —
the backend `/api/history` scan is not read to seed the list. So on a fresh
browser/profile, or after cleared storage, History reads "Nothing here yet"
even when `output/` still holds finished jobs (and the backend
`/api/history` returns them). This also means the per-clip state key from
Part B simply does not exist yet for those jobs in that browser — the
Part B snippet will report "No stored per-clip state".

The frontend now self-heals: opening the History tab fetches the backend scan
and merges any on-disk jobs into the local list (local entries win for a
matching `jobId`; backend-only jobs appear as openable `complete` rows). If
you are on an older build, or the merge did not happen, re-seed manually from
the DevTools console:

```js
// Rebuild clippyme_history from the backend scan, then reload the History tab.
fetch('/api/history').then(r => r.json()).then((d) => {
  const backend = d.jobs || [];
  const local = JSON.parse(localStorage.getItem('clippyme_history') || '[]');
  const byId = new Map(local.map((e) => [e.jobId, e]));
  for (const j of backend) {
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
  }
  localStorage.setItem('clippyme_history', JSON.stringify([...byId.values()]));
  console.log(`Seeded ${backend.length} backend job(s) into History — reload to view.`);
});
```

Once the job is openable, the per-clip state key (`clippyme_clip_states_<job_id>`)
is created on first open of the results grid; at that point Part B's targeted
snippet is the way to clear any `publishedAt` chips for that job in this
browser.

