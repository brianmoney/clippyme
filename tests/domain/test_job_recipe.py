"""The Create recipe must travel with the job, not only in browser storage."""
import json
import os

import pytest

from clippyme.domain import job_recipe
from clippyme.domain.job_recipe import (
    MAX_RECIPE_BYTES,
    load_recipe,
    save_recipe,
    validate_recipe,
)


def test_validate_recipe_accepts_none_and_dicts():
    assert validate_recipe(None) is None
    recipe = {"subtitles": {"mode": "karaoke", "preset": "hormozi_bold"}}
    assert validate_recipe(recipe) == recipe


@pytest.mark.parametrize("value", ["text", [], 3])
def test_validate_recipe_rejects_non_objects(value):
    with pytest.raises(ValueError):
        validate_recipe(value)


def test_validate_recipe_bounds_payload_size():
    with pytest.raises(ValueError):
        validate_recipe({"top_text": {"text": "x" * (MAX_RECIPE_BYTES + 1)}})


def test_save_and_load_recipe_round_trip(tmp_path):
    recipe = {
        "subtitles": {"mode": "classic", "font_color": "#FF0000"},
        "hook": {"position": "top", "animate": True},
    }
    save_recipe(str(tmp_path), recipe)
    assert load_recipe(str(tmp_path)) == recipe
    path = os.path.join(str(tmp_path), job_recipe.RECIPE_FILENAME)
    assert os.path.exists(path)
    assert (os.stat(path).st_mode & 0o777) == 0o600


def test_save_recipe_ignores_empty_recipe(tmp_path):
    save_recipe(str(tmp_path), None)
    save_recipe(str(tmp_path), {})
    assert load_recipe(str(tmp_path)) is None


def test_load_recipe_ignores_corrupt_or_non_object_files(tmp_path):
    path = os.path.join(str(tmp_path), job_recipe.RECIPE_FILENAME)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{not json")
    assert load_recipe(str(tmp_path)) is None
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(["not", "an", "object"], handle)
    assert load_recipe(str(tmp_path)) is None
