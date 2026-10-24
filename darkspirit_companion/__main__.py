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
import time
from pathlib import Path
from typing import Any

APP_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DarkspiritAgent"
CONFIG_PATH = APP_DIR / "companion.json"
FACE_MODEL_PATH = APP_DIR / "face-model.yml"
FACE_LABELS_PATH = APP_DIR / "face-labels.json"
LOG_PATH = APP_DIR / "companion.log"
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
    """Speak locally through Windows SAPI without opening a visible window."""
    import subprocess

    safe_text = text.replace("'", "''")
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.Speak('{safe_text}')"
    )
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        timeout=45,
    )


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
    print("Face enrollment: look at the camera and slowly turn your head slightly.")
    print("Press Q to cancel. Samples stay on this PC and are not uploaded.")
    try:
        while len(samples) < 40:
            ok, frame = camera.read()
            if not ok:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = detector.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=5, minSize=(100, 100))
            for x, y, w, h in faces:
                cv2.rectangle(frame, (x, y), (x + w, y + h), (50, 200, 90), 2)
                face = cv2.resize(gray[y : y + h, x : x + w], (160, 160))
                if len(samples) < 40:
                    samples.append(face)
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


def listen_for_speech(model_path: str) -> None:
    """Wait for a local Vosk transcript after the enrolled face is recognized."""
    try:
        import sounddevice as sd
        from vosk import KaldiRecognizer, Model
    except ImportError as exc:
        raise RuntimeError("Voice extras are missing. Run: uv sync --extra companion") from exc
    if not model_path or not Path(model_path).is_dir():
        raise RuntimeError(
            "Set vosk_model_path in companion.json to a downloaded Vosk English model folder."
        )
    import json as json_module

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

    speak("I'm listening. What would you like me to do?")
    logging.info("Microphone listening began after face recognition.")
    with sd.RawInputStream(samplerate=16000, blocksize=8000, dtype="int16", channels=1, callback=on_audio):
        while True:
            chunk = audio_queue.get()
            if recognizer.AcceptWaveform(chunk):
                transcript = json_module.loads(recognizer.Result()).get("text", "").strip()
                if transcript:
                    logging.info("Local speech transcript received.")
                    # Do not write transcripts to disk. This MVP only confirms capture;
                    # the conversational brain/command router is a separate integration step.
                    speak(f"I heard you say: {transcript}. My conversation connection is not configured yet.")
                    if transcript.lower() in {"stop listening", "goodbye darkspirit", "go to sleep"}:
                        speak("Okay. I'll wait until I recognize you again.")
                        return


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
                            if config.get("microphone_enabled") and config.get("listen_after_greeting"):
                                try:
                                    listen_for_speech(str(config.get("vosk_model_path", "")))
                                except Exception:
                                    logging.exception("Local voice input failed.")
                                    config["microphone_enabled"] = False
                                    logging.warning("Voice input disabled for this run after startup failure.")
                                    speak("My microphone setup needs attention. I'll stay available without listening.")
                    elif greeted and last_recognized_at is not None:
                        if now - last_recognized_at > greeting_reset_seconds:
                            greeted = False
                            last_recognized_at = None
                            logging.info("Face absent for %.0f seconds; greeting re-armed.", greeting_reset_seconds)
                    time.sleep(0.08)
            finally:
                # Release the device after every short check, including errors.
                camera.release()
                logging.info("Privacy check finished; camera released.")

            time.sleep(camera_sleep_seconds)
    except KeyboardInterrupt:
        logging.info("Background companion stopped by keyboard interrupt.")
    finally:
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
