## 1. Backend deletion

- [x] 1.1 `DELETE /api/history/{job_id}` removes the output dir via `rmtree`, removes the uploaded input, and drops the job from the in-memory dict + journal.
- [x] 1.2 Gate on `job_control.can_purge` so active jobs return 409.
- [x] 1.3 Refuse symbolic-link job dirs (409) and return 404 when the dir is missing.

## 2. Frontend + tests

- [x] 2.1 Wire the History view delete action to the new endpoint.
- [x] 2.2 Tests for active-job 409, symlink 409, missing 404, and storage-released delete.
