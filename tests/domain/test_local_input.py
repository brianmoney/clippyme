"""Unit tests for clippyme.domain.local_input server-local path validation."""
import os

import pytest

from clippyme.domain.errors import ValidationError
from clippyme.domain.local_input import (
    configured_local_input_dir,
    resolve_local_input_dir,
    validate_local_path,
)


def _make_videos(tmp_path) -> str:
    videos = tmp_path / "videos"
    videos.mkdir()
    return str(videos)


def test_accepts_a_regular_media_file_inside_root(tmp_path):
    root = _make_videos(tmp_path)
    target = tmp_path / "videos" / "recording.mp4"
    target.write_bytes(b"x" * 100)
    assert validate_local_path(str(target), root=root) == str(target.resolve())


@pytest.mark.parametrize("ext", [".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"])
def test_accepts_every_media_extension(tmp_path, ext):
    root = _make_videos(tmp_path)
    target = tmp_path / "videos" / f"clip{ext}"
    target.write_bytes(b"x")
    assert validate_local_path(str(target), root=root) == str(target.resolve())


def test_rejects_path_outside_root(tmp_path):
    root = _make_videos(tmp_path)
    outside = tmp_path / "other"
    outside.mkdir()
    target = outside / "secret.mp4"
    target.write_bytes(b"x")
    with pytest.raises(ValidationError, match="inside"):
        validate_local_path(str(target), root=root)


def test_rejects_traversal_that_lexically_looks_inside(tmp_path):
    root = _make_videos(tmp_path)
    outside = tmp_path / "other"
    outside.mkdir()
    (outside / "secret.mp4").write_bytes(b"x")
    sneaky = os.path.join(root, "..", "other", "secret.mp4")
    with pytest.raises(ValidationError, match="inside"):
        validate_local_path(sneaky, root=root)


def test_rejects_symlink_escaping_the_root(tmp_path):
    root = _make_videos(tmp_path)
    outside = tmp_path / "other"
    outside.mkdir()
    secret = outside / "secret.mp4"
    secret.write_bytes(b"x")
    link = os.path.join(root, "esc.mp4")
    os.symlink(str(secret), link)
    with pytest.raises(ValidationError, match="inside"):
        validate_local_path(link, root=root)


def test_rejects_missing_empty_and_non_file(tmp_path):
    root = _make_videos(tmp_path)
    with pytest.raises(ValidationError, match="not found"):
        validate_local_path(os.path.join(root, "ghost.mp4"), root=root)
    empty = tmp_path / "videos" / "empty.mp4"
    empty.touch()
    with pytest.raises(ValidationError, match="empty"):
        validate_local_path(str(empty), root=root)
    sub = tmp_path / "videos" / "sub"
    sub.mkdir()
    with pytest.raises(ValidationError, match="not found|regular"):
        validate_local_path(str(sub / "dir.mp4"), root=root)


def test_rejects_unsupported_extension_and_blank(tmp_path):
    root = _make_videos(tmp_path)
    txt = tmp_path / "videos" / "notes.txt"
    txt.write_bytes(b"x")
    with pytest.raises(ValidationError, match="Unsupported file type"):
        validate_local_path(str(txt), root=root)
    with pytest.raises(ValidationError, match="blank"):
        validate_local_path("", root=root)
    with pytest.raises(ValidationError, match="blank"):
        validate_local_path("   ", root=root)


def test_rejects_argv_like_path(tmp_path):
    root = _make_videos(tmp_path)
    with pytest.raises(ValidationError, match="must not start with"):
        validate_local_path("-i/etc/passwd", root=root)


def test_configured_dir_honours_env_and_default(monkeypatch):
    monkeypatch.delenv("LOCAL_INPUT_DIR", raising=False)
    assert configured_local_input_dir() == os.path.abspath("videos")
    monkeypatch.setenv("LOCAL_INPUT_DIR", "/srv/ingest")
    assert configured_local_input_dir() == "/srv/ingest"
    # ~ expansion
    monkeypatch.setenv("LOCAL_INPUT_DIR", "~/ingest")
    assert configured_local_input_dir() == os.path.abspath(
        os.path.expanduser("~/ingest")
    )


def test_resolve_creates_dir(tmp_path, monkeypatch):
    root = os.path.join(str(tmp_path), "nested", "videos")
    monkeypatch.setenv("LOCAL_INPUT_DIR", root)
    assert resolve_local_input_dir() == os.path.realpath(root)
    assert os.path.isdir(root)
