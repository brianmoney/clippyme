"""POST /api/hook/rewrite returns a Gemini-rewritten hook for a clip.

The Gemini call (rewrite_hook) is mocked — this guards the endpoint wiring:
metadata load, clip lookup, transcript→segments, key resolution, metadata
persistence and the response shape. TestClient is used WITHOUT its context
manager so the FastAPI lifespan never starts.
"""
import json

import pytest
from fastapi.testclient import TestClient

from clippyme.api import app as app_module
import clippyme.domain.hook_rewrite as hook_rewrite

JOB_ID = "55555555-5555-4555-8555-555555555555"
ORIGIN = {"Origin": "http://localhost:5175", "X-Gemini-Key": "dummy-test-key-not-real"}


def _make_client(monkeypatch, tmp_path, hook_text="Stavo sbagliando tutto"):
    outputs = tmp_path / "output"
    job_dir = outputs / JOB_ID
    job_dir.mkdir(parents=True)
    monkeypatch.setattr(app_module, "OUTPUT_DIR", str(outputs))
    meta = {
        "transcript": {
            "language": "it",
            "segments": [{"words": [
                {"start": 0.0, "end": 1.0, "word": "stavo"},
                {"start": 3.0, "end": 4.0, "word": "sbagliando"},
            ]}],
        },
        "shorts": [{"start": 0.0, "end": 5.0, "viral_hook_text": hook_text}],
    }
    with open(job_dir / "vid_metadata.json", "w") as f:
        json.dump(meta, f)
    return TestClient(app_module.app, headers=ORIGIN)


def teardown_function():
    app_module.jobs.pop(JOB_ID, None)


def test_rewrite_returns_and_persists_hook_text(monkeypatch, tmp_path):
    captured = {}

    def fake_rewrite(*, api_key, model, segments, current_hook, target_language,
                     clip_duration):
        captured.update(model=model, lang=target_language, duration=clip_duration,
                        current=current_hook)
        return {"hook_text": "I was doing it all wrong", "error": ""}

    monkeypatch.setattr(hook_rewrite, "rewrite_hook", fake_rewrite)
    client = _make_client(monkeypatch, tmp_path)
    r = client.post(f"/api/hook/rewrite/{JOB_ID}/0", json={"language": "en"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["hook_text"] == "I was doing it all wrong"
    assert body["error"] == ""
    assert body["language"] == "en"
    assert captured["model"] == "gemini-3.5-flash"
    assert captured["lang"] == "en"
    assert captured["duration"] == 5.0
    assert captured["current"] == "Stavo sbagliando tutto"
    # Persisted atomically into the clip metadata (single source of truth).
    meta_path = tmp_path / "output" / JOB_ID / "vid_metadata.json"
    data = json.loads(meta_path.read_text())
    assert data["shorts"][0]["viral_hook_text"] == "I was doing it all wrong"


def test_rewrite_syncs_in_memory_job(monkeypatch, tmp_path):
    monkeypatch.setattr(
        hook_rewrite, "rewrite_hook",
        lambda **k: {"hook_text": "Now in English", "error": ""})
    app_module.jobs[JOB_ID] = {
        "status": "completed",
        "result": {"clips": [{"original_index": 0,
                              "viral_hook_text": "Vecchio testo"}]},
    }
    client = _make_client(monkeypatch, tmp_path)
    r = client.post(f"/api/hook/rewrite/{JOB_ID}/0", json={"language": "en"})
    assert r.status_code == 200, r.text
    assert app_module.jobs[JOB_ID]["result"]["clips"][0]["viral_hook_text"] \
        == "Now in English"


def test_rewrite_error_surfaces_without_persisting(monkeypatch, tmp_path):
    monkeypatch.setattr(
        hook_rewrite, "rewrite_hook",
        lambda **k: {"hook_text": "", "error": "AI hook rewrite failed: boom"})
    client = _make_client(monkeypatch, tmp_path)
    r = client.post(f"/api/hook/rewrite/{JOB_ID}/0", json={"language": "en"})
    assert r.status_code == 200, r.text
    assert r.json()["hook_text"] == ""
    assert "boom" in r.json()["error"]
    data = json.loads((tmp_path / "output" / JOB_ID / "vid_metadata.json").read_text())
    assert data["shorts"][0]["viral_hook_text"] == "Stavo sbagliando tutto"


def test_rewrite_404_for_bad_clip(monkeypatch, tmp_path):
    monkeypatch.setattr(
        hook_rewrite, "rewrite_hook",
        lambda **k: {"hook_text": "", "error": ""})
    client = _make_client(monkeypatch, tmp_path)
    r = client.post(f"/api/hook/rewrite/{JOB_ID}/99", json={"language": "en"})
    assert r.status_code == 404


def test_rewrite_rejects_multi_language(monkeypatch, tmp_path):
    client = _make_client(monkeypatch, tmp_path)
    r = client.post(f"/api/hook/rewrite/{JOB_ID}/0", json={"language": "multi"})
    assert r.status_code == 422  # schema rejects multi/auto as a hook target


@pytest.mark.parametrize("language", ["", "xx", "toolonglanguagecode"])
def test_rewrite_rejects_bad_language(monkeypatch, tmp_path, language):
    client = _make_client(monkeypatch, tmp_path)
    r = client.post(f"/api/hook/rewrite/{JOB_ID}/0", json={"language": language})
    assert r.status_code == 422
