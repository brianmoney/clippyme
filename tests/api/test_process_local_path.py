"""Tests for the /api/process server-local (local_path) submission path.

The local_path mode lets a trusted operator point a job at a video already on
the backend's filesystem (NFS mount) instead of streaming it over HTTP. It is
gated by the same trusted-origin check as uploads; the source file is owned by
the operator and must never be deleted by the job lifecycle.
"""
import os

import pytest
from fastapi.testclient import TestClient

from clippyme.api import app as app_module
from clippyme.domain import job_actions


@pytest.fixture
def client(monkeypatch, tmp_path):
    uploads = tmp_path / "uploads"
    outputs = tmp_path / "output"
    videos = tmp_path / "videos"
    uploads.mkdir()
    outputs.mkdir()
    videos.mkdir()
    monkeypatch.setattr(app_module, "UPLOAD_DIR", str(uploads))
    monkeypatch.setattr(app_module, "OUTPUT_DIR", str(outputs))
    monkeypatch.setenv("LOCAL_INPUT_DIR", str(videos))
    return (
        TestClient(app_module.app, headers={"Origin": "http://localhost:5175"}),
        str(videos),
        str(outputs),
    )


def _valid_video(videos: str, name: str = "big.mp4") -> str:
    path = os.path.join(videos, name)
    with open(path, "wb") as f:
        f.write(b"fake mp4 bytes")
    return path


def _submit_local(tc, target: str):
    return tc.post(
        "/api/process",
        headers={"X-Gemini-Key": "k", "Content-Type": "application/json"},
        json={"local_path": target},
    )


def test_local_path_submits_and_sets_keep_input(client):
    tc, videos, _ = client
    target = _valid_video(videos)
    resp = _submit_local(tc, target)
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    job = app_module.jobs[job_id]
    # The job must run from the path in place and must not own the source.
    assert job["input_path"] == target
    assert job["keep_input"] is True
    assert "-i" in job["cmd"] and target in job["cmd"]
    assert os.path.exists(target)


def test_cancel_keeps_server_local_source(client):
    tc, videos, _ = client
    target = _valid_video(videos)
    job_id = _submit_local(tc, target).json()["job_id"]
    # A queued job can be cancelled; the action body must not remove the source.
    job = app_module.jobs[job_id]
    job["status"] = "processing"
    job["process"] = None
    result = tc.post(f"/api/cancel/{job_id}")
    assert result.status_code in (200, 409)
    if result.status_code == 200:
        assert os.path.exists(target)


def test_cancel_action_respects_keep_input(client):
    """Direct unit check: cancel_job_action skips deletion for keep_input jobs."""
    _, videos, outputs = client
    target = _valid_video(videos)
    job = {
        "status": "processing",
        "output_dir": os.path.join(outputs, "some-job"),
        "process": None,
        "input_path": target,
        "keep_input": True,
        "logs": [],
    }
    os.makedirs(job["output_dir"], exist_ok=True)
    import asyncio
    asyncio.run(job_actions.cancel_job_action("some-job", job))
    assert os.path.exists(target)


def test_cancel_action_removes_owned_upload(tmp_path):
    """Regression guard: owned uploads (keep_input absent) are still deleted."""
    import asyncio
    owned = tmp_path / "owned.mp4"
    owned.write_bytes(b"x")
    out = tmp_path / "out"
    out.mkdir()
    job = {
        "status": "processing",
        "output_dir": str(out),
        "process": None,
        "input_path": str(owned),
        "logs": [],
    }
    asyncio.run(job_actions.cancel_job_action("owned-job", job))
    assert not os.path.exists(owned)


def test_unsupported_local_extension_is_rejected(client):
    _, videos, _ = client
    txt = os.path.join(videos, "notes.txt")
    with open(txt, "w") as f:
        f.write("not a video")
    tc, _, _ = client
    resp = _submit_local(tc, txt)
    assert resp.status_code == 400
    assert "Unsupported file type" in resp.text


def test_local_path_outside_allowlist_rejected(client, tmp_path):
    tc, _, _ = client
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    other = outside / "x.mp4"
    other.write_bytes(b"x")
    resp = _submit_local(tc, str(other))
    assert resp.status_code == 400
    assert "inside" in resp.text


def test_url_and_local_path_are_mutually_exclusive(client):
    tc, videos, outputs = client
    target = _valid_video(videos)
    resp = tc.post(
        "/api/process",
        headers={"X-Gemini-Key": "k", "Content-Type": "application/json"},
        json={"url": "https://www.youtube.com/watch?v=abc", "local_path": target},
    )
    assert resp.status_code == 400
    assert "not both" in resp.text
    # Nothing was enqueued.
    assert not os.listdir(outputs)
