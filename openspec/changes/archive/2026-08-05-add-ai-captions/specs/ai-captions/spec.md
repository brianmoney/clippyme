## Purpose

Generates optimized social publish captions per clip through a user-configured
OpenAI-compatible chat-completions endpoint, using a short series context plus
each clip's own transcript, without overwriting hand-written captions.

## ADDED Requirements

### Requirement: Configurable OpenAI-compatible endpoint
The system SHALL let the operator configure an OpenAI-compatible captions
endpoint via three settings: base URL (HTTP(S), default
`https://api.openai.com/v1`), API key, and model id (default `gpt-4o-mini`).
Private and local-network hosts MUST be allowed, and the base URL and model id
SHALL be validated before saving.

#### Scenario: Saving endpoint settings
- **WHEN** the operator saves a base URL, API key and model in the AI captions settings
- **THEN** the values persist and appear in the core config response

#### Scenario: Rejecting an invalid base URL
- **WHEN** the operator saves a non-HTTP(S) base URL
- **THEN** the save is rejected and no config change is persisted

### Requirement: Per-clip caption generation
The system SHALL generate an optimized caption per clip by sending the clip's
own transcript segments plus a shared user-provided series context to the
configured chat-completions endpoint. Captions MUST be written in the same
language as the transcript and MUST NOT invent facts about the video.

#### Scenario: Optimizing a batch of clips
- **WHEN** the operator provides a series context and requests caption generation for the clips of a job
- **THEN** each clip returns an optimized caption derived from its own transcript and the shared context

#### Scenario: Endpoint not configured
- **WHEN** caption generation is requested without a saved API key
- **THEN** the request is rejected with a 400 and no provider call is made

#### Scenario: Out-of-range clip indices
- **WHEN** the request names a clip index outside the job's clip list
- **THEN** the request is rejected with a 400 and no captions are returned

### Requirement: Hand-written captions are preserved
The system SHALL NOT overwrite a caption the operator has authored. Generated
captions fill the per-clip caption fields, but a clip flagged as hand-written
(`captionTouched`) MUST be excluded from generation, and every generated
caption MUST remain editable afterwards.

#### Scenario: Skipping a hand-written caption
- **WHEN** caption generation runs over a batch where one clip's caption was hand-written
- **THEN** the hand-written clip is not sent for generation and its caption is unchanged

#### Scenario: Generated captions stay editable
- **WHEN** a generated caption fills a clip's caption field
- **THEN** the operator can still edit that caption before publishing
