# zernio-publishing Specification

## Purpose
Enables publishing to multiple client Zernio workspaces, each configured as a
named profile with its own API key, platform account IDs and timezone, and a
single default account that publish flows use unless another is chosen.
## Requirements
### Requirement: Multiple named profiles
The system SHALL store a configurable list of named Zernio profiles, each with
its own name, API key, per-platform account IDs (tiktok/instagram/youtube) and
timezone. Profile names MUST be unique and a profile list MUST contain at most
16 entries.

#### Scenario: Saving a profile list
- **WHEN** the operator saves a profile list with two profiles
- **THEN** both profiles persist with their ids, names, keys, accounts and timezones and are returned by the config status endpoint

#### Scenario: Rejecting duplicate names
- **WHEN** a profile list with two profiles sharing a name is saved
- **THEN** the save is rejected and no config change is persisted

### Requirement: Single default profile
The system SHALL mark exactly one profile as the default publish target.
Selecting a profile as default MUST clear the default flag from every other
profile. When no profile is explicitly marked, the first profile acts as
default.

#### Scenario: Switching the default
- **WHEN** the operator marks profile B as default while profile A was default
- **THEN** only profile B has the default flag and publish flows resolve to B

#### Scenario: No default marked
- **WHEN** a saved profile list has no profile marked default
- **THEN** the first profile in the list is used as the default publish target

### Requirement: Legacy single-key migration
The system SHALL treat a pre-profiles Zernio config (top-level api_key only) as
a single synthesized "Default" profile so existing keys remain usable, and a
subsequent save that returns that profile with a blank key MUST keep the stored
key rather than wiping it.

#### Scenario: Loading a legacy config
- **WHEN** the stored Zernio config has an api_key but no profiles list
- **THEN** the config status exposes one "Default" profile backed by that key

#### Scenario: Saving over a legacy config
- **WHEN** the operator saves the synthesized "Default" profile with a blank key
- **THEN** the stored api_key is preserved and the profile list is persisted

### Requirement: Secret masking
The system SHALL never echo a full Zernio API key. Config status and profile
status MUST return masked key previews (first 6 and last 4 characters) instead
of the raw key.

#### Scenario: Masked keys in status
- **WHEN** the config status endpoint is queried with profiles configured
- **THEN** each profile returns an api_key_masked field and no response contains a full key verbatim

### Requirement: Per-publish profile selection
A publish request MAY carry a `profile_id`. The system SHALL resolve the named
profile when provided and the default profile otherwise, and upload the clip
using that profile's API key, account IDs and timezone. The publish record MUST
store which profile published the clip.

#### Scenario: Publishing to the selected profile
- **WHEN** a publish request specifies profile_id for profile B
- **THEN** the clip uploads with profile B's API key and platform account IDs and the publish history records profile B

#### Scenario: Publishing to the default
- **WHEN** a publish request omits profile_id
- **THEN** the clip uploads with the default profile's API key and platform account IDs

### Requirement: Publish account confirmation
The publish surface SHALL display the resolved publishing account (name and
masked key) before a publish is submitted and SHALL submit the selected
profile's id with the request.

#### Scenario: Confirming the account in the publish modal
- **WHEN** the operator opens the publish modal with multiple profiles configured
- **THEN** the modal shows a "Publishing account" selector defaulted to the default profile with a confirmation line naming that account

### Requirement: Per-profile account discovery
Account discovery SHALL accept an optional `profile_id` and resolve accounts
using that profile's API key, defaulting to the default profile when omitted.

#### Scenario: Discovering accounts for a non-default profile
- **WHEN** discovery is requested for profile B's id
- **THEN** the Zernio API is queried with profile B's key and the response identifies profile B

