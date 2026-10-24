import json
import queue
import sys
import threading
import types
import wave

import pytest

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


def test_partial_config_merges_safe_defaults(monkeypatch, tmp_path):
    config_path = tmp_path / "DarkspiritAgent" / "companion.json"
    monkeypatch.setattr(companion, "CONFIG_PATH", config_path)
    monkeypatch.setattr(companion, "APP_DIR", config_path.parent)
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"user_name": "A"}), encoding="utf-8")

    loaded = companion.load_config()
    assert loaded["user_name"] == "A"
    assert loaded["camera_enabled"] is False
    assert loaded["fish_audio_api_key"] == ""


def test_invalid_config_fails_with_actionable_error(monkeypatch, tmp_path):
    config_path = tmp_path / "DarkspiritAgent" / "companion.json"
    monkeypatch.setattr(companion, "CONFIG_PATH", config_path)
    monkeypatch.setattr(companion, "APP_DIR", config_path.parent)
    config_path.parent.mkdir(parents=True)
    config_path.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Cannot read"):
        companion.load_config()


def test_speak_without_api_key_does_not_start_a_worker(monkeypatch, caplog):
    monkeypatch.setattr(companion, "load_config", lambda: companion.DEFAULT_CONFIG.copy())

    def unexpected_thread(*args, **kwargs):
        pytest.fail("TTS worker should not start without an API key")

    monkeypatch.setattr(companion.threading, "Thread", unexpected_thread)
    companion.speak("Hello")
    assert "fish_audio_api_key" in caplog.text


def test_background_stays_idle_when_camera_consent_is_off(monkeypatch, caplog):
    caplog.set_level("INFO")
    monkeypatch.setattr(companion, "configure_logging", lambda: None)
    monkeypatch.setattr(companion, "load_config", lambda: companion.DEFAULT_CONFIG.copy())

    companion.run_background()

    assert "camera disabled or no face enrolled" in caplog.text


def _install_fake_audio(monkeypatch, transcripts):
    class FakeRecognizer:
        def __init__(self, _model, _sample_rate):
            self._transcripts = iter(transcripts)

        def AcceptWaveform(self, _chunk):
            return True

        def Result(self):
            return json.dumps({"text": next(self._transcripts)})

    class FakeStream:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]

        def __enter__(self):
            for _ in transcripts:
                self.callback(b"\\x00" * 8, 4, None, None)
            return self

        def __exit__(self, _type, _value, _traceback):
            return False

    sounddevice = types.ModuleType("sounddevice")
    sounddevice.RawInputStream = FakeStream
    vosk = types.ModuleType("vosk")
    vosk.Model = lambda _path: object()
    vosk.KaldiRecognizer = FakeRecognizer
    monkeypatch.setitem(sys.modules, "sounddevice", sounddevice)
    monkeypatch.setitem(sys.modules, "vosk", vosk)


def test_local_hearing_handles_stop_phrase_without_network(monkeypatch, tmp_path):
    _install_fake_audio(monkeypatch, ["stop listening"])
    spoken = []
    monkeypatch.setattr(companion, "speak", spoken.append)
    stop_event = threading.Event()

    assert companion.listen_for_speech(str(tmp_path), stop_event) is True
    assert spoken == ["Okay. I'll wait until I recognize you again."]


def test_hearing_only_executes_allowlisted_calculator_action(monkeypatch, tmp_path):
    _install_fake_audio(monkeypatch, ["open calculator", "stop listening"])
    spoken = []
    launched = []
    monkeypatch.setattr(companion, "speak", spoken.append)
    monkeypatch.setattr("subprocess.Popen", lambda *args, **kwargs: launched.append((args, kwargs)))

    assert companion.listen_for_speech(str(tmp_path), threading.Event()) is True

    assert len(launched) == 1
    args, kwargs = launched[0]
    assert args[0] == ["calc.exe"]
    assert kwargs["shell"] is False
    assert "Opening Calculator." in spoken


def test_speech_skips_cleanly_if_config_is_broken(monkeypatch, caplog):
    def broken_config():
        raise RuntimeError("bad config")

    monkeypatch.setattr(companion, "load_config", broken_config)
    companion.speak("Hello")
    assert "speech was skipped" in caplog.text


def test_listener_stops_cleanly_when_stop_event_is_set(monkeypatch, tmp_path):
    _install_fake_audio(monkeypatch, [])
    stop_event = threading.Event()
    stop_event.set()

    assert companion.listen_for_speech(str(tmp_path), stop_event) is False



def test_tts_uses_bounded_fifo_queue(monkeypatch):
    class FakeThread:
        def __init__(self, target, **_kwargs):
            self.target = target

        def start(self):
            pass

        def is_alive(self):
            return True

    config = companion.DEFAULT_CONFIG.copy()
    config["fish_audio_api_key"] = "test-key"
    speech_queue = queue.Queue(maxsize=20)
    monkeypatch.setattr(companion, "_TTS_QUEUE", speech_queue)
    monkeypatch.setattr(companion, "_TTS_WORKER", None)
    monkeypatch.setattr(companion, "load_config", lambda: config)
    monkeypatch.setattr(companion.threading, "Thread", FakeThread)

    companion.speak("First reply")
    companion.speak("Second reply")

    assert [item[0] for item in speech_queue.queue] == ["First reply", "Second reply"]
    assert speech_queue.maxsize == 20



def test_generated_wav_plays_through_sounddevice(monkeypatch, tmp_path):
    wav_path = tmp_path / "voice.wav"
    with wave.open(str(wav_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"\x00\x00\xe8\x03\x18\xfc\x00\x00")

    observed = {}

    def check_output_settings(**kwargs):
        observed["settings"] = kwargs

    def play(audio, samplerate, blocking):
        observed["audio"] = audio
        observed["samplerate"] = samplerate
        observed["blocking"] = blocking

    sounddevice = types.ModuleType("sounddevice")
    sounddevice.check_output_settings = check_output_settings
    sounddevice.play = play
    monkeypatch.setitem(sys.modules, "sounddevice", sounddevice)

    companion._play_wav_with_sounddevice(wav_path)

    assert observed["samplerate"] == 16000
    assert observed["blocking"] is True
    assert observed["audio"].shape == (4, 1)
    assert observed["settings"]["dtype"] == "float32"
