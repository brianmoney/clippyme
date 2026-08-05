"""POST /api/captions/optimize returns per-clip AI captions for a job.

The OpenAI-compatible HTTP call (caption_ai.optimize_captions) is mocked — this
guards the endpoint wiring: config resolution, metadata load, shorts-position
mapping, transcript→segments, error mapping. TestClient is used WITHOUT its
context manager so the FastAPI lifespan never starts.
"""
import json

from fastapi.testclient import TestClient

from clippyme.api import app as app_module
from clippyme.domain import caption_ai as caption_ai_module

JOB_ID = "44444444-4444-4444-8444-444444444444"
ORIGIN = {"Origin": "http://localhost:5175"}
META = {
    "transcript": {
        "language": "en",
        "segments": [{"words": [
            {"start": 0.0, "end": 1.0, "word": "intro"},
            {"start": 3.0, "end": 4.0, "word": "body"},
        ]}],
    },
    "shorts": [
        {"start": 0.0, "end": 5.0, "video_title_for_youtube_short": "First"},
        {"start": 5.0, "end": 9.0, "video_title_for_youtube_short": "Second"},
    ],
}


def _make_client(monkeypatch, tmp_path, cfg):
    outputs = tmp_path / "output"
    job_dir = outputs / JOB_ID
    job_dir.mkdir(parents=True)
    monkeypatch.setattr(app_module, "OUTPUT_DIR", str(outputs))
    with open(job_dir / "vid_metadata.json", "w") as f:
        json.dump(META, f)
    monkeypatch.setattr(app_module, "load_persistent_config", lambda: cfg)
    return TestClient(app_module.app, headers=ORIGIN)


def teardown_function():
    app_module.jobs.pop(JOB_ID, None)


def test_captions_optimize_returns_per_clip_captions(monkeypatch, tmp_path):
    captured = {}

    def fake_optimize(*, base_url, api_key, model, clips):
        captured.update(base_url=base_url, api_key=api_key, model=model,
                        indices=[c["index"] for c in clips], contexts={c["index"]: c["context"] for c in clips})
        return [{"index": c["index"], "caption": f"caption {c['index']}"} for c in clips]

    monkeypatch.setattr(caption_ai_module, "optimize_captions", fake_optimize)
    client = _make_client(monkeypatch, tmp_path, {
        "OPENAI_CAPTIONS_API_KEY": "sk-test",
        "OPENAI_CAPTIONS_BASE_URL": "http://localhost:11434/v1",
        "OPENAI_CAPTIONS_MODEL": "llama3",
    })

    r = client.post(f"/api/captions/optimize/{JOB_ID}", json={
        "context": "fitness channel, hype tone",
        "indices": [0, 1],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert [c["caption"] for c in body["captions"]] == ["caption 0", "caption 1"]
    assert body["model"] == "llama3"
    assert body["base_url"] == "http://localhost:11434/v1"
    assert captured["api_key"] == "sk-test"
    assert captured["indices"] == [0, 1]
    assert captured["contexts"][0] == "fitness channel, hype tone"


def test_captions_optimize_defaults_indices_and_config(monkeypatch, tmp_path):
    def fake_optimize(*, base_url, api_key, model, clips):
        return [{"index": c["index"], "caption": "x"} for c in clips]

    monkeypatch.setattr(caption_ai_module, "optimize_captions", fake_optimize)
    client = _make_client(monkeypatch, tmp_path, {"OPENAI_CAPTIONS_API_KEY": "sk-test"})
    r = client.post(f"/api/captions/optimize/{JOB_ID}", json={"context": ""})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model"] == "gpt-4o-mini"
    assert body["base_url"] == "https://api.openai.com/v1"
    assert len(body["captions"]) == 2


def test_captions_optimize_400_when_key_missing(monkeypatch, tmp_path):
    client = _make_client(monkeypatch, tmp_path, {})
    r = client.post(f"/api/captions/optimize/{JOB_ID}", json={"context": "x"})
    assert r.status_code == 400
    assert "API key not configured" in r.json()["detail"]


def test_captions_optimize_400_for_out_of_range_index(monkeypatch, tmp_path):
    def fake_optimize(**kwargs):
        return []
    monkeypatch.setattr(caption_ai_module, "optimize_captions", fake_optimize)
    client = _make_client(monkeypatch, tmp_path, {"OPENAI_CAPTIONS_API_KEY": "sk-test"})
    r = client.post(f"/api/captions/optimize/{JOB_ID}", json={"indices": [99]})
    assert r.status_code == 400
    assert "out of range" in r.json()["detail"]


def test_captions_optimize_404_for_missing_job(monkeypatch, tmp_path):
    client = _make_client(monkeypatch, tmp_path, {"OPENAI_CAPTIONS_API_KEY": "sk-test"})
    r = client.post("/api/captions/optimize/00000000-0000-4000-8000-000000000000", json={"context": "x"})
    assert r.status_code == 404


def test_captions_optimize_maps_provider_error_to_502(monkeypatch, tmp_path):
    def boom(**kwargs):
        raise caption_ai_module.CaptionAIError("Caption AI returned HTTP 429: rate", status_code=429, body="rate")
    monkeypatch.setattr(caption_ai_module, "optimize_captions", boom)
    client = _make_client(monkeypatch, tmp_path, {"OPENAI_CAPTIONS_API_KEY": "sk-test"})
    r = client.post(f"/api/captions/optimize/{JOB_ID}", json={"context": "x"})
    assert r.status_code == 429
    assert "429" in r.json()["detail"]
