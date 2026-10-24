"""Background-first Darkspirit companion (Windows).

The companion is opt-in: camera and microphone remain off unless the user explicitly
enables them in the local configuration. No images or audio are uploaded by this module.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import tempfile
import threading
import time
import urllib.error
import urllib.request
import winsound
from pathlib import Path
from typing import Any

APP_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DarkspiritAgent"
CONFIG_PATH = APP_DIR / "companion.json"
FACE_MODEL_PATH = APP_DIR / "face-model.yml"
FACE_LABELS_PATH = APP_DIR / "face-labels.json"
LOG_PATH = APP_DIR / "companion.log"
_SPEECH_LOCK = threading.Lock()  # Prevent overlapping TTS playback.
DEFAULT_CONFIG: dict[str, Any] = {
    "camera_enabled": False,
    "microphone_enabled": False,
    "face_enrolled": False,
    "greet_on_recognition": True,
    "listen_after_greeting": True,
    "memory_enabled": False,
    "user_name": "",
    "voice_language": "en-US",
    "vosk_model_path": "",
    "fish_audio_api_key": "",
    "fish_audio_voice_id": "b3c51bf6029f4201a342b40827250784",
    "fish_audio_model": "s2.1-pro-free",
    "fish_audio_speed": 1.0,
}


def ensure_app_dir() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict[str, Any]:
    ensure_app_dir()
    if not CONFIG_PATH.exists():
        return DEFAULT_CONFIG.copy()
    try:
        saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(saved, dict):
            raise ValueError("Configuration must be a JSON object.")
        return {**DEFAULT_CONFIG, **saved}
    except (OSError, json.JSONDecodeError, ValueError):
        raise RuntimeError(
            f"Cannot read {CONFIG_PATH}. Back it up and repair or remove the invalid file."
        ) from None


def save_config(config: dict[str, Any]) -> None:
    ensure_app_dir()
    temporary = CONFIG_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, indent=2), encoding="utf-8")
    temporary.replace(CONFIG_PATH)
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def configure_logging() -> None:
    ensure_app_dir()
    logging.basicConfig(
        filename=LOG_PATH,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        encoding="utf-8",
    )


def speak(text: str) -> None:
    """Generate Verity-style speech through Fish Audio without blocking camera checks.

    Fish Audio credentials are supplied by the user in the local config. Speech is
    synthesized on a worker thread and played from a temporary WAV file; Windows SAPI
    is deliberately not used as a fallback.
    """
    try:
        config = load_config()
    except Exception:
        logging.exception("Cannot load companion settings; speech was skipped.")
        return
    api_key = str(config.get("fish_audio_api_key", "")).strip()
    if not api_key:
        logging.warning("Fish Audio voice is not configured; set fish_audio_api_key in companion.json.")
        return

    def synthesize_and_play() -> None:
        # Serialize synthesis and playback so replies never overlap or play out of order.
        with _SPEECH_LOCK:
            wav_path: Path | None = None
            try:
                speed = float(config.get("fish_audio_speed", 1.0))
                if not 0.5 <= speed <= 2.0:
                    logging.warning("Fish Audio speed must be between 0.5 and 2.0; using 1.0.")
                    speed = 1.0
                payload = {
                    "text": text[:500],
                    "reference_id": str(config.get("fish_audio_voice_id") or DEFAULT_CONFIG["fish_audio_voice_id"]),
                    "format": "wav",
                    "speed": speed,
                    "normalize": True,
                }
                request = urllib.request.Request(
                    "https://api.fish.audio/v1/tts",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        "model": str(config.get("fish_audio_model") or "s2.1-pro-free"),
                    },
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=30) as response:
                    audio = response.read(20 * 1024 * 1024 + 1)
                if len(audio) > 20 * 1024 * 1024:
                    raise ValueError("Fish Audio response exceeded the 20 MiB safety limit.")
                if len(audio) < 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
                    raise ValueError("Fish Audio returned data that is not a WAV file.")
                with tempfile.NamedTemporaryFile(prefix="darkspirit_voice_", suffix=".wav", delete=False) as handle:
                    wav_path = Path(handle.name)
                    handle.write(audio)
                winsound.PlaySound(str(wav_path), winsound.SND_FILENAME)
            except urllib.error.HTTPError as exc:
                logging.error("Fish Audio TTS request failed with HTTP %s; check API key, quota, and voice ID.", exc.code)
            except Exception:
                logging.exception("Fish Audio speech generation or playback failed.")
            finally:
                if wav_path is not None:
                    try:
                        wav_path.unlink(missing_ok=True)
                    except OSError:
                        logging.warning("Could not remove temporary speech audio file.")

    threading.Thread(target=synthesize_and_play, name="Darkspirit-FishAudio-TTS", daemon=True).start()


def enroll_face() -> None:
    """Capture several face samples locally and train OpenCV's LBPH recognizer."""
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "Face recognition extras are missing. Run: uv sync --extra companion"
        ) from exc
    if not hasattr(cv2, "face") or not hasattr(cv2.face, "LBPHFaceRecognizer_create"):
        raise RuntimeError("OpenCV's LBPH recognizer is unavailable. Reinstall companion extras.")

    ensure_app_dir()
    camera = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    if not camera.isOpened():
        camera.release()
        raise RuntimeError("Could not open the webcam. Check Windows camera permissions.")
    detector = cv2.CascadeClassifier(
        str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
    )
    samples = []
    last_sample_at = 0.0
    print("Face enrollment: look at the camera and slowly turn your head slightly.")
    print("Enrollment takes about 6 seconds. Samples stay on this PC and are not uploaded.")
    try:
        while len(samples) < 40:
            ok, frame = camera.read()
            if not ok:
                time.sleep(0.05)
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = detector.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=5, minSize=(100, 100))
            now = time.monotonic()
            for x, y, w, h in faces:
                cv2.rectangle(frame, (x, y), (x + w, y + h), (50, 200, 90), 2)
                if now - last_sample_at >= 0.15:
                    face = cv2.resize(gray[y : y + h, x : x + w], (160, 160))
                    samples.append(face)
                    last_sample_at = now
                break
            cv2.putText(frame, f"Samples: {len(samples)}/40 - Q cancels", (12, 28),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
            cv2.imshow("Darkspirit - Face enrollment (local)", frame)
            if cv2.waitKey(80) & 0xFF in (ord("q"), ord("Q")):
                raise RuntimeError("Face enrollment cancelled.")
    finally:
        camera.release()
        cv2.destroyAllWindows()

    recognizer = cv2.face.LBPHFaceRecognizer_create()
    recognizer.train(samples, np.zeros(len(samples), dtype=np.int32))
    recognizer.write(str(FACE_MODEL_PATH))
    FACE_LABELS_PATH.write_text(json.dumps({"0": "enrolled_user"}), encoding="utf-8")
    config = load_config()
    config["face_enrolled"] = True
    save_config(config)
    print(f"Enrolled locally. Model saved to {FACE_MODEL_PATH}")


def configure() -> None:
    config = load_config()
    print("Darkspirit Agent — private background companion setup")
    print("Camera and microphone default to OFF. Nothing is recorded during setup.")
    config["user_name"] = input("What should Darkspirit call you? ").strip()[:80]
    config["camera_enabled"] = input("Enable webcam face recognition? [y/N] ").strip().lower() == "y"
    # Persist consent before enrollment: enroll_face() loads and updates the saved config.
    save_config(config)
    if config["camera_enabled"]:
        enroll_face()
        config = load_config()
        # Enrollment may update face_enrolled; preserve the explicit camera consent.
        config["camera_enabled"] = True
        save_config(config)
    config["microphone_enabled"] = (
        input("Enable local microphone listening after face recognition? [y/N] ").strip().lower() == "y"
    )
    config["memory_enabled"] = input("Enable local memory? You must still explicitly ask it to remember each item. [y/N] ").strip().lower() == "y"
    config["greet_on_recognition"] = True
    config["listen_after_greeting"] = True
    save_config(config)
    print(f"Settings saved to {CONFIG_PATH}")
    print("To run quietly in the background, use start_companion.bat.")


def listen_for_speech(model_path: str, stop_event: threading.Event) -> bool:
    """Continuously transcribe locally; run this on its own worker thread."""
    try:
        import sounddevice as sd
        from vosk import KaldiRecognizer, Model
    except ImportError as exc:
        raise RuntimeError("Voice extras are missing. Run: uv sync --extra companion") from exc
    if not model_path or not Path(model_path).is_dir():
        raise RuntimeError("Set vosk_model_path in companion.json to a downloaded Vosk English model folder.")

    recognizer = KaldiRecognizer(Model(model_path), 16000)
    audio_queue: queue.Queue[bytes] = queue.Queue(maxsize=30)

    def on_audio(indata, frames, timing, status):
        del frames, timing
        if status:
            logging.warning("Audio input status: %s", status)
        try:
            audio_queue.put_nowait(bytes(indata))
        except queue.Full:
            pass

    logging.info("Microphone listening started on its own worker; camera checks remain independent.")
    with sd.RawInputStream(samplerate=16000, blocksize=4000, dtype="int16", channels=1, callback=on_audio):
        while not stop_event.is_set():
            try:
                chunk = audio_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if not recognizer.AcceptWaveform(chunk):
                continue
            transcript = json.loads(recognizer.Result()).get("text", "").strip()
            if not transcript:
                continue

            # Do not store raw transcripts. Only fixed, allowlisted commands can act.
            command = " ".join(transcript.lower().strip().replace(".", "").replace(",", "").split())
            if command in {"stop listening", "goodbye darkspirit", "go to sleep"}:
                logging.info("Voice listener paused by a recognized stop phrase.")
                speak("Okay. I'll wait until I recognize you again.")
                return True

            if command in {"open calculator", "launch calculator", "start calculator", "calculator"}:
                try:
                    import subprocess
                    subprocess.Popen(
                        ["calc.exe"],
                        shell=False,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    logging.info("Allowlisted action executed: open calculator.")
                    speak("Opening Calculator.")
                except OSError:
                    logging.exception("Could not open Calculator.")
                    speak("Sorry, I couldn't open Calculator.")
                continue

            logging.info("Speech recognized; no allowlisted action matched.")
            speak("I heard you. I can open Calculator so far, but my AI conversation connection isn't configured yet.")

    # False means capture ended because its stop event was set, not by a voice command.
    return False


def run_background() -> None:
    """Check for the enrolled face periodically, releasing the camera between checks."""
    configure_logging()
    config = load_config()
    if not config.get("camera_enabled") or not config.get("face_enrolled"):
        logging.info("Background companion idle: camera disabled or no face enrolled.")
        return
    if not FACE_MODEL_PATH.exists():
        logging.error("Face model missing; camera remains off.")
        return
    try:
        import cv2
    except ImportError:
        logging.error("OpenCV missing; camera remains off.")
        return

    detector = cv2.CascadeClassifier(
        str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
    )
    recognizer = cv2.face.LBPHFaceRecognizer_create()
    recognizer.read(str(FACE_MODEL_PATH))

    # Privacy mode: the webcam is opened only for a short check, then released.
    # These values can be adjusted later if detection is too slow or too frequent.
    camera_active_seconds = 2.0
    camera_sleep_seconds = 5.0
    greeting_reset_seconds = 30.0
    greeted = False
    last_recognized_at: float | None = None
    voice_stop_event = threading.Event()
    voice_thread: threading.Thread | None = None
    voice_paused_event = threading.Event()
    voice_retry_after = 0.0

    def start_voice_listener(voice_config: dict[str, Any]) -> None:
        nonlocal voice_stop_event, voice_thread, voice_retry_after
        worker_stop_event = threading.Event()
        voice_stop_event = worker_stop_event
        voice_retry_after = time.monotonic() + 10.0

        def voice_worker() -> None:
            try:
                paused_by_command = listen_for_speech(
                    str(voice_config.get("vosk_model_path", "")), worker_stop_event
                )
                if paused_by_command:
                    voice_paused_event.set()
            except Exception:
                logging.exception("Local voice input failed; will retry after a pause.")
                speak("My microphone needs attention. I'll try listening again shortly.")

        voice_thread = threading.Thread(
            target=voice_worker, name="Darkspirit-Voice-Listener", daemon=True
        )
        voice_thread.start()

    logging.info(
        "Background companion started in privacy mode: camera active for %.1fs, "
        "then released for %.1fs.",
        camera_active_seconds,
        camera_sleep_seconds,
    )

    try:
        while True:
            # Re-check saved consent each cycle so turning the camera off in the
            # config stops further camera access without needing to restart.
            config = load_config()
            if not config.get("camera_enabled") or not config.get("face_enrolled"):
                logging.info("Camera consent disabled or face enrollment removed; stopping camera checks.")
                break

            camera = cv2.VideoCapture(0, cv2.CAP_DSHOW)
            if not camera.isOpened():
                logging.warning("Camera unavailable during privacy check; retrying after pause.")
                camera.release()
                time.sleep(camera_sleep_seconds)
                continue

            check_started = time.monotonic()
            try:
                while time.monotonic() - check_started < camera_active_seconds:
                    ok, frame = camera.read()
                    if not ok:
                        time.sleep(0.05)
                        continue
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    faces = detector.detectMultiScale(
                        gray, scaleFactor=1.2, minNeighbors=5, minSize=(100, 100)
                    )
                    recognized = False
                    for x, y, w, h in faces:
                        face = cv2.resize(gray[y : y + h, x : x + w], (160, 160))
                        label, confidence = recognizer.predict(face)
                        if label == 0 and confidence < 65:
                            recognized = True
                            break

                    now = time.monotonic()
                    if recognized:
                        last_recognized_at = now
                        if not greeted:
                            name = str(config.get("user_name") or "there").replace("'", "")
                            logging.info("Enrolled face recognized; greeting played.")
                            if config.get("greet_on_recognition", True):
                                speak(f"Hello {name}. I'm Darkspirit. I'm here when you need me.")
                            greeted = True
                    elif greeted and last_recognized_at is not None:
                        if now - last_recognized_at > greeting_reset_seconds:
                            greeted = False
                            last_recognized_at = None
                            # Stop microphone capture when the user has been away.
                            voice_stop_event.set()
                            voice_paused_event.clear()
                            voice_retry_after = 0.0
                            logging.info(
                                "Face absent for %.0f seconds; microphone listener stopping and greeting re-armed.",
                                greeting_reset_seconds,
                            )
                    # Supervise the mic independently: recover from transient worker failures
                    # without repeating the greeting. A voice stop phrase stays paused until
                    # the face has been absent long enough to re-arm the companion.
                    if (
                        greeted
                        and config.get("microphone_enabled")
                        and config.get("listen_after_greeting")
                        and not voice_paused_event.is_set()
                        and (voice_thread is None or not voice_thread.is_alive())
                        and time.monotonic() >= voice_retry_after
                    ):
                        start_voice_listener(config.copy())
                    time.sleep(0.08)
            finally:
                # Release the device after every short check, including errors.
                camera.release()
                logging.info("Privacy check finished; camera released.")

            time.sleep(camera_sleep_seconds)
    except KeyboardInterrupt:
        logging.info("Background companion received Ctrl+C in its attached console.")
    finally:
        voice_stop_event.set()
        logging.info("Background companion stopped.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Darkspirit background companion")
    parser.add_argument("--setup", action="store_true", help="Open the explicit first-time setup")
    parser.add_argument("--enroll-face", action="store_true", help="Enroll or replace the local face model")
    parser.add_argument("--background", action="store_true", help="Run without opening a UI window")
    args = parser.parse_args()
    if args.setup:
        configure()
    elif args.enroll_face:
        enroll_face()
    elif args.background:
        run_background()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
