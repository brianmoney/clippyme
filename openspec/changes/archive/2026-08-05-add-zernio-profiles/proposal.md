## Why

Clipping is done for multiple client accounts, each with its own Zernio
workspace (API key + social account IDs). The previous single-key Zernio
config meant publishing always went to one account, forcing an operator to
rewrite config between clients. There was no way to confirm, before hitting
"Publish", which account a batch would land on.

## What Changes

- Introduce multiple named **Zernio profiles**, each carrying its own name,
  Zernio API key, per-platform account IDs (tiktok/instagram/youtube) and
  timezone, with exactly one profile marked **default**.
- Persist profiles in the existing `zernio` config namespace
  (`data/config.json`) as a `profiles` list; keep the legacy top-level
  `api_key`/`accounts`/`timezone` keys in sync with the default profile so
  existing consumers (live-monitor auto-publish) keep working unchanged.
- Migrate a pre-profiles layout (top-level api_key only) into a synthesized
  "Default" profile so existing keys survive the first save.
- `PublishRequest` gains `profile_id`; the publish endpoint resolves the
  selected profile (or the default) and uploads with that profile's API key,
  account IDs and timezone. The publish history record stores which profile
  published.
- The Settings "Publishing" panel becomes a profile list editor (name, masked
  API key, per-platform account IDs, Default radio, Discover, Add/Delete).
- The publish modal shows a "Publishing account" selector (defaulted to the
  default profile) with a confirmation line naming the account before
  publishing.
- Account discovery (`GET /api/zernio/accounts`) accepts an optional
  `profile_id` so each profile discovers its own accounts.

## Capabilities

### New Capabilities
- `zernio-publishing`: Zernio publishing configuration — named profiles with a
  single default, per-profile accounts/timezone, and per-publish account
  selection with confirmation.

### Modified Capabilities
<!-- None: this is the first spec for the Zernio publishing surface. -->

## Impact

- Backend: `storage/config_store.py` (profiles load/save/status + resolution),
  `api/schemas.py` (`ZernioProfileRequest`, `PublishRequest.profile_id`),
  `api/config_routes.py` (profile-aware config + discovery),
  `api/app.py` + `domain/publish_service.py` (profile-resolved publish flow).
- Frontend: `redesign/views.jsx` (Settings profile editor),
  `redesign/publish.jsx` (account selector + confirmation),
  `redesign/realApi.js` (discover with profile_id).
- Data: `data/config.json` `zernio` namespace gains `profiles`.
