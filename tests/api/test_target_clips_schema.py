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
