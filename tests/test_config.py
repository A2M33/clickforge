from clickforge_core import DEFAULTS, load_config, save_config


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
