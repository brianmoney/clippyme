## 1. Backend profile storage

- [x] 1.1 Add `profiles` support to `config_store.py`: normalize/validate load, save (replace list, single default, blank-key inheritance), `get_zernio_profile`, status with masked per-profile keys.
- [x] 1.2 Migrate legacy single-key layout to a synthesized "Default" profile and preserve the key on the first profile save.

## 2. API surface

- [x] 2.1 Add `ZernioProfileRequest` + `profiles` field to `ZernioConfigRequest`; add `profile_id` to `PublishRequest`.
- [x] 2.2 Update `update_zernio_config` to persist the profile list and `list_zernio_accounts` to accept `profile_id`.
- [x] 2.3 Resolve the publish profile in the `/api/publish` endpoint and pass it through `publish_clip_flow`; record `profile_id`/`profile_name` in publish history.

## 3. Frontend

- [x] 3.1 Rework the Settings Publishing panel into a profile list editor (name, masked key, accounts, Default radio, Discover, Add/Delete).
- [x] 3.2 Add the "Publishing account" selector + confirmation line to the publish modal and send `profile_id`.
- [x] 3.3 Thread `profile_id` through `discoverZernioAccounts` in `realApi.js`.

## 4. Tests

- [x] 4.1 Cover profile save/load/default/legacy-migration/invalid-input in `test_config_store.py`.
- [x] 4.2 Cover profile roundtrip + per-profile discovery in `test_config_routes.py`.
- [x] 4.3 Cover the profile editor and per-profile publish body in frontend tests.
