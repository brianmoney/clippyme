"""The Create recipe is persisted with each submitted job (JSON and upload)."""
import json
import os

import pytest
from fastapi.testclient import TestClient

from clippyme.api import app as app_module
from clippyme.domain.job_recipe import RECIPE_FILENAME

RECIPE = {
    "subtitles": {"mode": "karaoke", "preset": "hormozi_bold", "position": "bottom"},
    "hook": {"position": "top", "size": "L", "animate": True},
}


@pytest.fixture
def client(monkeypatch, tmp_path):
    uploads = tmp_path / "uploads"
    outputs = tmp_path / "output"
    uploads.mkdir()
    outputs.mkdir()
    monkeypatch.setattr(app_module, "UPLOAD_DIR", str(uploads))
    monkeypatch.setattr(app_module, "OUTPUT_DIR", str(outputs))

    calls = []

    async def fake_submit_job(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(app_module, "submit_job", fake_submit_job)
    tc = TestClient(app_module.app, headers={"Origin": "http://localhost:5175"})
    return tc, outputs, calls


def _recipe_on_disk(outputs):
    job_dirs = [p for p in outputs.iterdir() if p.is_dir()]
    assert len(job_dirs) == 1
    path = job_dirs[0] / RECIPE_FILENAME
    assert path.exists()
    return json.loads(path.read_text())


def test_json_submission_persists_recipe(client):
    tc, outputs, calls = client
    resp = tc.post(
        "/api/process",
        headers={"X-Gemini-Key": "k"},
        json={"url": "https://example.com/v", "preselections": RECIPE},
    )
    assert resp.status_code == 200, resp.text
    assert _recipe_on_disk(outputs) == RECIPE
    assert calls and calls[0]["preselections"] == RECIPE


def test_multipart_submission_persists_recipe(client):
    tc, outputs, calls = client
    resp = tc.post(
        "/api/process",
        headers={"X-Gemini-Key": "k"},
        files={"file": ("v.mp4", b"hello world", "video/mp4")},
        data={"preselections": json.dumps(RECIPE)},
    )
    assert resp.status_code == 200, resp.text
    assert _recipe_on_disk(outputs) == RECIPE
    assert calls and calls[0]["preselections"] == RECIPE


def test_invalid_multipart_recipe_is_rejected(client):
    tc, outputs, calls = client
    resp = tc.post(
        "/api/process",
        headers={"X-Gemini-Key": "k"},
        files={"file": ("v.mp4", b"hello world", "video/mp4")},
        data={"preselections": "{not json"},
    )
    assert resp.status_code == 400
    assert calls == []


def test_existing_job_recipe_can_be_backfilled(client):
    tc, outputs, _ = client
    job_id = "12345678-1234-4123-8123-1234567890ab"
    job_dir = outputs / job_id
    job_dir.mkdir()
    (job_dir / "vid_metadata.json").write_text('{"shorts": []}')

    resp = tc.post(
        f"/api/history/{job_id}/preselections",
        json={"preselections": RECIPE},
    )
    assert resp.status_code == 200, resp.text
    assert json.loads((job_dir / RECIPE_FILENAME).read_text()) == RECIPE


def test_backfill_rejects_missing_job(client):
    tc, _, _ = client
    resp = tc.post(
        "/api/history/12345678-1234-4123-8123-1234567890ab/preselections",
        json={"preselections": RECIPE},
    )
    assert resp.status_code == 404
