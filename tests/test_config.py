import pytest

import clickforge_core
from clickforge_core import DEFAULTS, load_config, sanitize_config, save_config


def test_missing_file_gives_defaults(tmp_path):
    assert load_config(tmp_path / "nope.json") == DEFAULTS


def test_round_trip_keeps_keys_we_dont_manage(tmp_path):
    path = tmp_path / "app" / "config.json"
    save_config({**DEFAULTS, "multiplier": 5, "exe_path": "C:\\x.exe"}, str(path))
    cfg = load_config(path)
    assert cfg["multiplier"] == 5 and cfg["exe_path"] == "C:\\x.exe"


def test_invalid_json_gives_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_config(path) == DEFAULTS


@pytest.mark.parametrize("body", ["[1, 2]", "5", "null", '"text"'])
def test_a_file_that_isnt_an_object_gives_defaults(tmp_path, body):
    path = tmp_path / "config.json"
    path.write_text(body, encoding="utf-8")
    assert load_config(path) == DEFAULTS  # `[1, 2]` used to crash at startup


@pytest.mark.parametrize("bad", [
    {"multiplier": "abc"},  # used to stop the app starting
    {"multiplier": True},
    {"delay_ms": None},
    {"buttons": "middle"},  # used to kill the mouse listener on the first click
    {"led": "caps"},
    {"sound": "no"},
    {"hotkey": []},
    {"hotkey": 5},
    {"hotkey": ["ctrl"]},
    {"hotkey": ["f6", "f7"]},
    {"hotkey": ["mouse_left"]},  # would swallow every left click on the whole PC
    {"hotkey": ["mouse_right"]},
])
def test_bad_values_fall_back_to_the_default(bad):
    key = next(iter(bad))
    assert sanitize_config(bad)[key] == DEFAULTS[key]


def test_numbers_are_clamped_and_rounded():
    cfg = sanitize_config({"multiplier": 5000, "delay_ms": 0.4})
    assert cfg["multiplier"] == 100 and cfg["delay_ms"] == 1
    assert sanitize_config({"multiplier": 7.6})["multiplier"] == 8


def test_infinity_and_nan_are_not_numbers_we_can_use(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"multiplier": NaN, "delay_ms": Infinity}', encoding="utf-8")
    cfg = load_config(path)
    assert cfg["multiplier"] == DEFAULTS["multiplier"] and cfg["delay_ms"] == DEFAULTS["delay_ms"]


def test_good_values_and_unknown_keys_survive():
    raw = {"multiplier": 3, "buttons": "both", "led": "off", "sound": False,
           "hotkey": ["ctrl", "mouse_left"], "exe_path": "C:\\x.exe"}
    assert sanitize_config(raw) == {**DEFAULTS, **raw}


def test_a_damaged_file_is_kept_for_the_user(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    load_config(str(path))
    assert (tmp_path / "config.json.bad").read_text(encoding="utf-8") == "{not json"


def test_a_crash_while_saving_leaves_the_old_file_whole(tmp_path, monkeypatch):
    path = str(tmp_path / "config.json")
    assert save_config({**DEFAULTS, "multiplier": 5}, path) is True

    def boom(_src, _dst):
        raise OSError("power cut")

    monkeypatch.setattr(clickforge_core.os, "replace", boom)
    assert save_config({**DEFAULTS, "multiplier": 6}, path) is False
    monkeypatch.undo()
    assert load_config(path)["multiplier"] == 5


def test_an_unwritable_folder_is_reported_not_raised(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("", encoding="utf-8")
    assert save_config(DEFAULTS, str(blocker / "sub" / "config.json")) is False
