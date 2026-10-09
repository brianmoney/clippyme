"""Server-local input validation for /api/process ``local_path`` submissions.

Uploading multi-GB VODs over HTTP is wasteful and flaky. ``local_path`` lets a
trusted operator point a job at a video already on the backend's filesystem
(an NFS mount, a ``docker cp``'d file, …). The path is read in place and is
**never** deleted by job lifecycle cleanup, because ClippyMe does not own it.

Security model: only an absolute path whose realpath stays inside a single
allow-listed directory tree (``LOCAL_INPUT_DIR``) is accepted. The route that
accepts this is already gated by ``require_trusted_config_request`` (trusted
origin or private-network client), same as every config/state endpoint.
"""
import os

from clippyme.domain.errors import ValidationError

# Media containers accepted for both multipart uploads and local paths.
MEDIA_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}

# Backend working dir inside the container is /app, so a default of ./videos
# lets operators `cp` a file into <repo>/videos without any compose change.
DEFAULT_LOCAL_INPUT_DIR = "videos"


def configured_local_input_dir(env=None) -> str:
    """Return the allow-listed root for server-local submissions.

    An explicitly set LOCAL_INPUT_DIR is honoured verbatim; otherwise fall
    back to the repo-local ``videos/`` dir (created on demand by the route).
    """
    source = os.environ if env is None else env
    raw = (source.get("LOCAL_INPUT_DIR") or "").strip()
    if raw:
        return os.path.abspath(os.path.expanduser(raw))
    return os.path.abspath(DEFAULT_LOCAL_INPUT_DIR)


def resolve_local_input_dir(env=None) -> str:
    """Resolve + create the configured server-local input directory."""
    root = configured_local_input_dir(env)
    try:
        os.makedirs(root, exist_ok=True)
    except OSError as exc:
        raise ValidationError(f"cannot create local input directory {root!r}: {exc}")
    return root


def validate_local_path(value: str, root: str | None = None) -> str:
    """Validate an absolute media path inside ``root`` and return its realpath.

    Raises :class:`ValidationError` (400) for anything that is not a regular,
    non-empty, media-suffixed file whose real location stays under ``root``.
    """
    raw = (value or "").strip()
    if not raw:
        raise ValidationError("local_path must not be blank")
    if raw.startswith("-"):
        raise ValidationError("local_path must not start with '-'")
    base = os.path.abspath(root or configured_local_input_dir())
    candidate = os.path.abspath(os.path.expanduser(raw))

    # Realpath resolves symlinks on both sides so a link inside the root that
    # points elsewhere is caught here, not just by the lexical prefix check.
    try:
        real_candidate = os.path.realpath(candidate)
        real_base = os.path.realpath(base)
    except OSError as exc:  # pragma: no cover - realpath rarely raises
        raise ValidationError(f"could not resolve local_path: {exc}")
    if not (real_candidate == real_base or real_candidate.startswith(real_base + os.sep)):
        raise ValidationError(
            f"local_path must be inside {base!r} (configure LOCAL_INPUT_DIR to change it)"
        )
    try:
        if not os.path.isfile(real_candidate):
            raise ValidationError(f"local file not found: {candidate}")
        if os.path.islink(candidate) and not os.path.isfile(candidate):
            raise ValidationError(f"local_path must be a regular file: {candidate}")
        if os.path.getsize(real_candidate) == 0:
            raise ValidationError("local file is empty")
    except OSError as exc:
        raise ValidationError(f"could not read local file {candidate!r}: {exc}") from exc

    ext = os.path.splitext(real_candidate)[1].lower()
    if ext not in MEDIA_EXTENSIONS:
        raise ValidationError(
            "Unsupported file type. Allowed: " + ", ".join(sorted(MEDIA_EXTENSIONS))
        )
    return real_candidate
