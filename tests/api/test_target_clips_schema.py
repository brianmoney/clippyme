"""The Create-tab clip target must be a validated request field, not free text."""
import pytest
from pydantic import ValidationError

from clippyme.api.schemas import BatchRequest, ProcessRequest


def test_process_request_accepts_target_clips():
    req = ProcessRequest(url="https://example.com/v", target_clips=7)
    assert req.target_clips == 7
    assert ProcessRequest(url="https://example.com/v").target_clips is None


@pytest.mark.parametrize("value", [0, 51, -1])
def test_process_request_rejects_out_of_range_target(value):
    with pytest.raises(ValidationError):
        ProcessRequest(url="https://example.com/v", target_clips=value)


def test_batch_request_accepts_target_clips():
    req = BatchRequest(urls=["https://example.com/v"], target_clips=3)
    assert req.target_clips == 3


def test_requests_accept_the_create_recipe():
    recipe = {
        "subtitles": {"mode": "karaoke", "preset": "hormozi_bold", "position": "bottom"},
        "hook": {"position": "top", "size": "L"},
        "logo": {"position": "top-right", "size": "M"},
    }
    assert ProcessRequest(url="https://example.com/v", preselections=recipe).preselections == recipe
    assert BatchRequest(urls=["https://example.com/v"], preselections=recipe).preselections == recipe
    assert ProcessRequest(url="https://example.com/v").preselections is None


@pytest.mark.parametrize("recipe", ["text", [], 3])
def test_requests_reject_non_object_recipe(recipe):
    with pytest.raises(ValidationError):
        ProcessRequest(url="https://example.com/v", preselections=recipe)
    with pytest.raises(ValidationError):
        BatchRequest(urls=["https://example.com/v"], preselections=recipe)


def test_requests_reject_oversize_recipe():
    recipe = {"top_text": {"text": "x" * (32 * 1024 + 1)}}
    with pytest.raises(ValidationError):
        ProcessRequest(url="https://example.com/v", preselections=recipe)
