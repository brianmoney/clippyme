## Why

Deleting a history entry previously only removed the row from the list while
the job's clips and metadata stayed on disk, silently consuming storage. Each
completed job holds tens to hundreds of MB; operators need a way to actually
free that space.

## What Changes

- `DELETE /api/history/{job_id}` (trusted-origin gated) now removes the job's
  output directory and all its files (`shutil.rmtree`), removes the uploaded
  input file if any, and removes the job from the in-memory dict and the job
  journal.
- Protect active jobs: deletion returns **409** unless the job is stopped or
  cancelled first.
- Refuse to follow symlinks: a job directory that is a symbolic link returns
  **409** instead of being followed.
- A job with no directory on disk returns **404**.

## Capabilities

### New Capabilities
- `history-deletion`: removing a past job's record AND its on-disk artifacts
  so storage is actually released.

### Modified Capabilities
<!-- None. -->

## Impact

- Backend: `api/app.py` (delete handler), `domain/job_control.py`
  (`can_purge` gate), `domain/history_service.py` (scan reflects absence).
- Frontend: History view delete action.
