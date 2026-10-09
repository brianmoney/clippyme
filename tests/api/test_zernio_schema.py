"""Bounds tests for ZernioConfigRequest + PublishRequest.scheduled_for.

The Zernio config endpoint and publish schedule accept user-supplied strings
that land in data/config.json and the scheduler. These pin the size/shape
guards: unbounded api_key, oversized/odd accounts maps, and a giant
scheduled_for string are all rejected at the Pydantic boundary; normal
payloads pass.
"""
import pytest
from pydantic import ValidationError

from clippyme.api.schemas import PublishRequest, ZernioConfigRequest


def test_zernio_accepts_normal_config():
    req = ZernioConfigRequest(
        api_key="sk_live_abc123",
        accounts={"tiktok": "acc1", "youtube": "acc2", "instagram": None},
        timezone="Europe/Rome",
    )
    assert req.accounts["tiktok"] == "acc1"


def test_zernio_accepts_empty():
    req = ZernioConfigRequest()
    assert req.api_key is None


def test_zernio_rejects_oversized_api_key():
    with pytest.raises(ValidationError):
        ZernioConfigRequest(api_key="x" * 513)


def test_zernio_rejects_oversized_timezone():
    with pytest.raises(ValidationError):
        ZernioConfigRequest(timezone="z" * 65)


def test_zernio_rejects_too_many_accounts():
    with pytest.raises(ValidationError):
        ZernioConfigRequest(accounts={f"k{i}": "v" for i in range(17)})


def test_zernio_rejects_unknown_platform():
    with pytest.raises(ValidationError):
        ZernioConfigRequest(accounts={"myspace": "a"})


def test_zernio_rejects_oversized_account_id():
    with pytest.raises(ValidationError):
        ZernioConfigRequest(accounts={"tiktok": "a" * 257})


def test_zernio_rejects_nonstring_account_id():
    with pytest.raises(ValidationError):
        ZernioConfigRequest(accounts={"tiktok": {"nested": 1}})


def test_publish_accepts_normal_scheduled_for():
    req = PublishRequest(
        platforms=[{"platform": "tiktok", "accountId": "a"}],
        scheduled_for="2026-04-08T12:00:00",
    )
    assert req.scheduled_for.startswith("2026")


def test_publish_rejects_oversized_scheduled_for():
    with pytest.raises(ValidationError):
        PublishRequest(
            platforms=[{"platform": "tiktok", "accountId": "a"}],
            scheduled_for="9" * 65,
        )


def test_zernio_profile_accepts_youtube_defaults():
    req = ZernioConfigRequest(
        profiles=[{
            "id": "p1",
            "name": "Test",
            "api_key": "sk_x",
            "is_default": True,
            "accounts": {"youtube": "yt1"},
            "youtube": {
                "category_id": "25",
                "made_for_kids": False,
                "contains_synthetic_media": False,
                "visibility": "unlisted",
            },
        }]
    )
    assert req.profiles[0].youtube["category_id"] == "25"
    assert req.profiles[0].youtube["visibility"] == "unlisted"


def test_zernio_profile_accepts_partial_youtube_defaults():
    req = ZernioConfigRequest(
        profiles=[{
            "id": "p1",
            "name": "Test",
            "api_key": "sk_x",
            "is_default": True,
            "accounts": {},
            "youtube": {"category_id": "27"},
        }]
    )
    assert req.profiles[0].youtube == {"category_id": "27"}


def test_zernio_profile_rejects_unknown_youtube_default():
    with pytest.raises(ValidationError):
        ZernioConfigRequest(
            profiles=[{
                "id": "p1",
                "name": "Test",
                "api_key": "sk_x",
                "is_default": True,
                "accounts": {},
                "youtube": {"hashtags": ["x"]},
            }]
        )


def test_zernio_profile_accepts_any_numeric_youtube_category():
    # Off-list ids are legal — YouTube has more categories than the dropdown's
    # curated shortlist, so a stored value must never be rejected by the API.
    req = ZernioConfigRequest(
        profiles=[{
            "id": "p1",
            "name": "Test",
            "api_key": "sk_x",
            "is_default": True,
            "accounts": {},
            "youtube": {"category_id": "999"},
        }]
    )
    assert req.profiles[0].youtube["category_id"] == "999"


def test_zernio_profile_rejects_non_numeric_youtube_category():
    with pytest.raises(ValidationError):
        ZernioConfigRequest(
            profiles=[{
                "id": "p1",
                "name": "Test",
                "api_key": "sk_x",
                "is_default": True,
                "accounts": {},
                "youtube": {"category_id": "abc"},
            }]
        )


def test_zernio_profile_rejects_bad_youtube_visibility():
    with pytest.raises(ValidationError):
        ZernioConfigRequest(
            profiles=[{
                "id": "p1",
                "name": "Test",
                "api_key": "sk_x",
                "is_default": True,
                "accounts": {},
                "youtube": {"visibility": "hidden"},
            }]
        )
