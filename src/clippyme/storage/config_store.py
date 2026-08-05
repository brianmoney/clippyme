"""Persistent configuration loader/saver for ClippyMe."""
import contextlib
import json
import logging
import os
import tempfile
import threading
import uuid

logger = logging.getLogger("clippyme")

DATA_DIR = "data"
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
VALID_CONFIG_KEYS = (
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "YOUTUBE_COOKIES",
    "HF_TOKEN",
    "DEEPGRAM_API_KEY",
    "ELEVENLABS_API_KEY",
    "TRANSCRIPTION_PROVIDER",
    "TWITCH_CLIENT_ID",
    "TWITCH_CLIENT_SECRET",
    "OPENAI_CAPTIONS_BASE_URL",
    "OPENAI_CAPTIONS_API_KEY",
    "OPENAI_CAPTIONS_MODEL",
)
ZERNIO_CONFIG_NAMESPACE = "zernio"
ZERNIO_DEFAULT_TIMEZONE = os.environ.get("ZERNIO_DEFAULT_TZ", "America/New_York")
ZERNIO_MAX_PROFILES = 16
_CONFIG_LOCK = threading.RLock()


def _read_raw_config() -> dict:
    with _CONFIG_LOCK:
        if not os.path.exists(CONFIG_FILE):
            return {}
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as file:
                data = json.load(file) or {}
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            logger.warning("Error reading config.json: %s", exc)
            return {}


def _write_raw_config(data: dict) -> bool:
    """Atomically replace config.json with owner-only permissions.

    Writing to a sibling temporary file and then ``os.replace`` prevents a
    crash, disk-full condition, or concurrent reader from observing a
    half-truncated JSON document containing secrets.
    """
    with _CONFIG_LOCK:
        tmp_path = None
        try:
            os.makedirs(DATA_DIR, mode=0o700, exist_ok=True)
            with contextlib.suppress(OSError):
                os.chmod(DATA_DIR, 0o700)

            fd, tmp_path = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=DATA_DIR)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as file:
                    json.dump(data, file, indent=4)
                    file.flush()
                    os.fsync(file.fileno())
                with contextlib.suppress(OSError):
                    os.chmod(tmp_path, 0o600)
                os.replace(tmp_path, CONFIG_FILE)
                tmp_path = None
                with contextlib.suppress(OSError):
                    os.chmod(CONFIG_FILE, 0o600)
                # Persist the rename itself on POSIX filesystems when possible.
                try:
                    dir_fd = os.open(DATA_DIR, os.O_RDONLY)
                except OSError:
                    dir_fd = None
                if dir_fd is not None:
                    try:
                        os.fsync(dir_fd)
                    except OSError:
                        pass
                    finally:
                        os.close(dir_fd)
                return True
            except Exception:
                # fdopen owns/closes fd once entered; close only if creation
                # failed before that ownership transfer.
                with contextlib.suppress(OSError):
                    os.close(fd)
                raise
        except (OSError, TypeError, ValueError) as exc:
            logger.error("Error writing config.json: %s", exc)
            return False
        finally:
            if tmp_path:
                with contextlib.suppress(OSError):
                    os.remove(tmp_path)


def _normalize_profiles(raw) -> list:
    """Coerce stored profiles into clean profile dicts (read side).

    Malformed entries are dropped and missing fields filled with defaults.
    Only used by loaders — writes go through :func:`_validate_profiles_for_save`.
    """
    if not isinstance(raw, list):
        return []
    profiles = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        profile = {
            "id": str(entry.get("id") or ""),
            "name": str(entry.get("name") or ""),
            "api_key": str(entry.get("api_key") or ""),
            "is_default": bool(entry.get("is_default")),
            "accounts": entry.get("accounts") if isinstance(entry.get("accounts"), dict) else {},
            "timezone": entry.get("timezone") or ZERNIO_DEFAULT_TIMEZONE,
        }
        if profile["id"] and profile["name"]:
            profiles.append(profile)
    return profiles


def _mask_key(api_key: str) -> str:
    if api_key and len(api_key) > 10:
        return f"{api_key[:6]}...{api_key[-4:]}"
    return ""


def load_zernio_config() -> dict:
    """Load the Zernio namespace, exposing both profiles and legacy fields.

    Returned keys:
      - ``profiles``: list of ``{id, name, api_key, is_default, accounts, timezone}``
      - ``default_profile``: the profile to publish to (first ``is_default``,
        else the first profile, else ``None``)
      - ``api_key`` / ``accounts`` / ``timezone``: derived from the default
        profile so legacy consumers (live-monitor auto-publish, publish flow)
        keep working unchanged. A pre-profiles layout (top-level api_key only)
        is surfaced as a single synthesized "default" profile.
    """
    raw = _read_raw_config()
    zernio = raw.get(ZERNIO_CONFIG_NAMESPACE) or {}
    if not isinstance(zernio, dict):
        zernio = {}
    accounts = zernio.get("accounts", {})
    legacy = {
        "api_key": zernio.get("api_key", ""),
        "accounts": accounts if isinstance(accounts, dict) else {},
        "timezone": zernio.get("timezone", ZERNIO_DEFAULT_TIMEZONE),
    }
    profiles = _normalize_profiles(zernio.get("profiles"))
    if not profiles and legacy["api_key"]:
        # Back-compat with the pre-profiles layout: expose the stored key as a
        # single default profile so every consumer treats config as a list.
        profiles = [{
            "id": "default",
            "name": "Default",
            "api_key": legacy["api_key"],
            "is_default": True,
            "accounts": legacy["accounts"],
            "timezone": legacy["timezone"],
        }]
    default_profile = next(
        (p for p in profiles if p.get("is_default")),
        profiles[0] if profiles else None,
    )
    return {
        "profiles": profiles,
        "default_profile": default_profile,
        "api_key": (default_profile or {}).get("api_key") or legacy["api_key"],
        "accounts": (default_profile or {}).get("accounts") or legacy["accounts"],
        "timezone": (default_profile or {}).get("timezone") or legacy["timezone"],
    }


def get_zernio_profile(profile_id: str = None) -> dict:
    """Resolve a stored Zernio profile by id, falling back to the default."""
    cfg = load_zernio_config()
    profiles = cfg.get("profiles") or []
    if profile_id:
        for profile in profiles:
            if profile.get("id") == profile_id:
                return profile
    return cfg.get("default_profile")


def _validate_profiles_for_save(profiles, existing_by_id: dict):
    """Validate + normalise an incoming profile list. ``None`` on invalid input.

    Profiles that come back with a blank ``api_key`` inherit the stored key of
    the same ``id`` (the editor sends masked/blank keys for existing profiles).
    Enforces unique ids/names and at most one ``is_default``.
    """
    if not isinstance(profiles, list):
        return None
    if len(profiles) > ZERNIO_MAX_PROFILES:
        return None
    cleaned = []
    seen_ids = set()
    seen_names = set()
    default_seen = False
    for entry in profiles:
        if not isinstance(entry, dict):
            return None
        pid = str(entry.get("id") or "").strip()
        name = str(entry.get("name") or "").strip()
        api_key = str(entry.get("api_key") or "").strip()
        if not name or len(name) > 80:
            return None
        if len(api_key) > 512:
            return None
        if not pid:
            pid = f"p{uuid.uuid4().hex[:12]}"
        if pid in seen_ids:
            return None
        seen_ids.add(pid)
        if name in seen_names:
            return None
        seen_names.add(name)
        if not api_key:
            api_key = (existing_by_id.get(pid) or {}).get("api_key", "")
        accounts = entry.get("accounts")
        if accounts is not None and not isinstance(accounts, dict):
            return None
        is_default = bool(entry.get("is_default"))
        if is_default and default_seen:
            return None
        if is_default:
            default_seen = True
        cleaned.append({
            "id": pid,
            "name": name,
            "api_key": api_key,
            "is_default": is_default,
            "accounts": accounts if isinstance(accounts, dict) else {},
            "timezone": entry.get("timezone") or ZERNIO_DEFAULT_TIMEZONE,
        })
    return cleaned


def save_zernio_config(api_key: str = None, accounts: dict = None, timezone: str = None,
                       profiles: list = None) -> bool:
    """Merge-update Zernio settings as one locked read-modify-write.

    When ``profiles`` is given it REPLACES the whole profile list (the dashboard
    editor owns the list). The legacy top-level ``api_key``/``accounts``/
    ``timezone`` keys are kept in sync with the default profile for back-compat
    consumers. Without ``profiles`` the legacy single-key merge is preserved.
    """
    with _CONFIG_LOCK:
        raw = _read_raw_config()
        current = raw.get(ZERNIO_CONFIG_NAMESPACE) or {}
        if not isinstance(current, dict):
            current = {}
        if profiles is not None:
            existing = {p["id"]: p for p in _normalize_profiles(current.get("profiles"))}
            # Legacy layout: the synthesized "default" profile (blank key on
            # save) must inherit the stored top-level api_key, or a migration
            # save would silently wipe the user's key.
            if "default" not in existing and current.get("api_key"):
                existing["default"] = {
                    "id": "default",
                    "name": "Default",
                    "api_key": current.get("api_key", ""),
                    "is_default": True,
                    "accounts": current.get("accounts") if isinstance(current.get("accounts"), dict) else {},
                    "timezone": current.get("timezone") or ZERNIO_DEFAULT_TIMEZONE,
                }
            cleaned = _validate_profiles_for_save(profiles, existing)
            if cleaned is None:
                return False
            if cleaned:
                default = next((p for p in cleaned if p.get("is_default")), cleaned[0])
                current["profiles"] = cleaned
                current["api_key"] = default["api_key"]
                current["accounts"] = default["accounts"]
                current["timezone"] = default["timezone"]
            else:
                current.pop("profiles", None)
                current.pop("api_key", None)
                current.pop("accounts", None)
                current.pop("timezone", None)
        else:
            if api_key is not None:
                if api_key == "":
                    current.pop("api_key", None)
                else:
                    current["api_key"] = api_key
            if accounts is not None:
                merged = current.get("accounts") or {}
                if not isinstance(merged, dict):
                    merged = {}
                for key, value in accounts.items():
                    if value in (None, ""):
                        merged.pop(key, None)
                    else:
                        merged[key] = value
                current["accounts"] = merged
            if timezone is not None:
                current["timezone"] = timezone
        raw[ZERNIO_CONFIG_NAMESPACE] = current
        return _write_raw_config(raw)


def zernio_config_status() -> dict:
    cfg = load_zernio_config()
    default = cfg.get("default_profile")
    profiles = []
    for profile in cfg.get("profiles") or []:
        profiles.append({
            "id": profile.get("id"),
            "name": profile.get("name"),
            "api_key_masked": _mask_key(profile.get("api_key", "")),
            "is_default": bool(profile.get("is_default")),
            "accounts": profile.get("accounts", {}),
            "timezone": profile.get("timezone", ZERNIO_DEFAULT_TIMEZONE),
        })
    default_key = (default or {}).get("api_key", "")
    return {
        "configured": bool(default_key),
        "default_profile_id": (default or {}).get("id"),
        "profiles": profiles,
        "api_key_masked": _mask_key(default_key),
        "accounts": (default or {}).get("accounts", {}),
        "timezone": (default or {}).get("timezone", ZERNIO_DEFAULT_TIMEZONE),
    }


def _normalize_incoming_keys(data: dict) -> dict:
    if not data:
        return {}
    out = dict(data)
    if "HUGGINGFACE_TOKEN" in out and not out.get("HF_TOKEN"):
        out["HF_TOKEN"] = out.pop("HUGGINGFACE_TOKEN")
    else:
        out.pop("HUGGINGFACE_TOKEN", None)
    return out


def load_persistent_config() -> dict:
    config = {
        "GEMINI_API_KEY": os.environ.get("GEMINI_API_KEY", ""),
        "GEMINI_MODEL": os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"),
        "YOUTUBE_COOKIES": os.environ.get("YOUTUBE_COOKIES", ""),
        "HF_TOKEN": os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN") or "",
        "DEEPGRAM_API_KEY": os.environ.get("DEEPGRAM_API_KEY", ""),
        "ELEVENLABS_API_KEY": os.environ.get("ELEVENLABS_API_KEY", ""),
        "TRANSCRIPTION_PROVIDER": os.environ.get("TRANSCRIPTION_PROVIDER", "deepgram"),
        "TWITCH_CLIENT_ID": os.environ.get("TWITCH_CLIENT_ID", ""),
        "TWITCH_CLIENT_SECRET": os.environ.get("TWITCH_CLIENT_SECRET", ""),
        "OPENAI_CAPTIONS_BASE_URL": os.environ.get("OPENAI_CAPTIONS_BASE_URL", "https://api.openai.com/v1"),
        "OPENAI_CAPTIONS_API_KEY": os.environ.get("OPENAI_CAPTIONS_API_KEY", ""),
        "OPENAI_CAPTIONS_MODEL": os.environ.get("OPENAI_CAPTIONS_MODEL", "gpt-4o-mini"),
    }
    raw = _read_raw_config()
    config.update({key: value for key, value in raw.items() if key in VALID_CONFIG_KEYS})
    return config


def save_persistent_config(new_config: dict) -> bool:
    """Persist core keys without racing the separate Zernio namespace."""
    with _CONFIG_LOCK:
        raw = _read_raw_config()
        normalized = _normalize_incoming_keys(new_config)
        sanitized = {
            key: normalized.get(key) for key in VALID_CONFIG_KEYS if key in normalized
        }
        for key, value in sanitized.items():
            if value in (None, ""):
                raw.pop(key, None)
            else:
                raw[key] = value
        if not _write_raw_config(raw):
            return False
        for key, value in sanitized.items():
            if value in (None, ""):
                os.environ.pop(key, None)
                if key == "HF_TOKEN":
                    os.environ.pop("HUGGINGFACE_TOKEN", None)
            else:
                os.environ[key] = str(value)
                if key == "HF_TOKEN":
                    os.environ["HUGGINGFACE_TOKEN"] = str(value)
        return True
