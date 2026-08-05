# publish-scheduling Specification

## Purpose
Lets operators publish a batch of clips with per-clip captions and a controlled
schedule: how many posts land per day, how many days out the schedule starts,
and in which timezone prime-time slots are computed.
## Requirements
### Requirement: Per-clip publish captions
When publishing more than one clip, the system SHALL show one caption box per
clip and SHALL send each clip's own caption in its publish body. A single-clip
publish SHALL show one caption box and fall back to the clip title when no
caption is authored.

#### Scenario: Distinct captions per clip in a batch
- **WHEN** the operator publishes a batch of three clips and types a different caption in each box
- **THEN** each publish body carries its own caption and no clip shares another's caption

#### Scenario: Single clip caption fallback
- **WHEN** the operator publishes a single clip without typing a caption
- **THEN** the publish body uses the clip's title as the caption

### Requirement: Caption persistence across edits
The system SHALL persist per-clip caption edits back to the clip's state as they
are typed, and SHALL seed each caption box from the grid-edited caption, then
the clip's metadata caption, then the clip title.

#### Scenario: Grid caption survives a modal reopen
- **WHEN** the operator edits a caption in the publish modal, closes it, and reopens it
- **THEN** the caption box re-seeds from the persisted per-clip state

### Requirement: Batch schedule rate
When scheduling a batch, the system SHALL compute each clip's start date as
today plus the days-from-now offset plus the floor of the clip's position
divided by the posts-per-day rate, and SHALL require the backend to space
same-day posts by a minimum gap so they do not collide.

#### Scenario: Two posts per day
- **WHEN** a batch of four clips is scheduled at two posts per day
- **THEN** the first two clips share day 0's start date and the last two share day 1's, with no duplicate times within a day

#### Scenario: Delayed start
- **WHEN** the operator sets the schedule to start one day from now
- **THEN** every clip's start date is offset by one additional day

### Requirement: Per-batch timezone
The system SHALL expose a timezone selector for a scheduled batch, seeded from
the configured Zernio timezone, and SHALL send the chosen timezone with every
scheduled publish body.

#### Scenario: Overriding the schedule timezone
- **WHEN** the operator picks a timezone in the publish modal and schedules
- **THEN** every scheduled publish body carries that timezone

### Requirement: Publish now overrides scheduling
The system SHALL let the operator publish immediately regardless of the
schedule toggle: the "Publish now" action MUST send `schedule_mode: now` with
no start date.

#### Scenario: Publishing now with scheduling enabled
- **WHEN** the operator has scheduling enabled but clicks "Publish now"
- **THEN** the publish bodies carry `schedule_mode: now` and no start date

