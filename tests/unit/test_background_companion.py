from darkspirit_companion import __main__ as companion


def test_default_configuration_keeps_sensors_off():
    assert companion.DEFAULT_CONFIG["camera_enabled"] is False
    assert companion.DEFAULT_CONFIG["microphone_enabled"] is False
    assert companion.DEFAULT_CONFIG["face_enrolled"] is False


def test_load_and_save_config_uses_user_local_directory(monkeypatch, tmp_path):
    config_path = tmp_path / "DarkspiritAgent" / "companion.json"
    monkeypatch.setattr(companion, "CONFIG_PATH", config_path)
    monkeypatch.setattr(companion, "APP_DIR", config_path.parent)

    saved = companion.DEFAULT_CONFIG.copy()
    saved["user_name"] = "Test User"
    companion.save_config(saved)

    loaded = companion.load_config()
    assert loaded["user_name"] == "Test User"
    assert loaded["camera_enabled"] is False
    assert loaded["microphone_enabled"] is False
