"""Per-job Create recipe (subtitles/hook/logo/…) persistence.

The recipe used to live only in the dashboard's ``localStorage``. A job opened
from History in another browser, on another device, or after browser-data
cleanup therefore had no recipe: previews/downloads fell back to the raw
rendered clip and the chosen subtitles/hook silently disappeared.

Persisting it beside the job metadata makes the recipe part of the job. It is
never served by the static ``/videos`` mount (JSON is blocked there) and is
returned through the trusted status/history endpoints instead.
"""
import json
import os

RECIPE_FILENAME = "preselections.json"

# A recipe is a small flat options object. Bound it so a malformed client
# cannot use the field to write an arbitrarily large file.
MAX_RECIPE_BYTES = 32 * 1024


def validate_recipe(value):
    """Validate a client-supplied recipe.

    Returns the value unchanged (``None`` is allowed) or raises ``ValueError``
    for Pydantic to surface as a 422. The individual compose parameters are
    re-validated when they are used, so this only enforces shape/size.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("preselections must be a JSON object")
    try:
        encoded = json.dumps(value, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("preselections must contain only JSON values") from exc
    if len(encoded.encode("utf-8")) > MAX_RECIPE_BYTES:
        raise ValueError(f"preselections must be at most {MAX_RECIPE_BYTES} bytes")
    return value


def save_recipe(job_dir: str, recipe) -> None:
    """Atomically write the job recipe next to the other job artifacts."""
    if not recipe:
        return
    path = os.path.join(job_dir, RECIPE_FILENAME)
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(recipe, handle, separators=(",", ":"), allow_nan=False)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass


def load_recipe(job_dir: str):
    """Return the persisted recipe, or ``None`` when absent/unreadable."""
    try:
        with open(os.path.join(job_dir, RECIPE_FILENAME), encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None
