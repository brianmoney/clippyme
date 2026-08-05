# history-deletion Specification

## Purpose
Lets operators delete a past job from history such that its on-disk artifacts
(clips, metadata, uploaded input) are removed and the storage is actually
freed, while protecting active jobs and symlinks.
## Requirements
### Requirement: Delete releases storage
The system SHALL, on a history-delete request for a job, remove the job's
output directory with all its files, remove the uploaded input file when one
exists, and remove the job from the in-memory job dict and the persisted
journal, so the disk usage is released.

#### Scenario: Deleting a completed job
- **WHEN** the operator deletes a completed job from history
- **THEN** the job's output directory is removed from disk, the job is gone from the journal and from history, and a follow-up status lookup fails

#### Scenario: Deleting a job with an uploaded input
- **WHEN** the deleted job originated from a local upload
- **THEN** the uploaded source file is removed as well

### Requirement: Active jobs are protected
The system SHALL reject deletion of a job that is not in a terminal state
(stopped or cancelled): an active job MUST return a 409 instead of deleting.

#### Scenario: Deleting a running job
- **WHEN** the operator deletes a job that is currently processing or queued
- **THEN** the request is rejected with a 409 and the job's files stay on disk

### Requirement: Symlink deletion is refused
The system SHALL refuse to delete a job directory that is a symbolic link,
returning a 409, so the delete path can never follow a link outside the output
tree.

#### Scenario: Deleting a symlinked job directory
- **WHEN** the job's output path is a symbolic link
- **THEN** the request is rejected with a 409 and the link is left in place

### Requirement: Missing job returns not found
The system SHALL return a 404 when a delete request targets a job whose output
directory does not exist on disk.

#### Scenario: Deleting an already-removed job
- **WHEN** the operator deletes a job whose directory is already gone
- **THEN** the request returns a 404

