"""Background Gemini voice companion with a Windows-wide Shift+S hotkey.

Microphone capture starts only after the user approves the session and presses Shift+S.
Speech is transcribed locally with Vosk and only the resulting text is sent to Gemini
through the authenticated localhost Darkspirit bridge. Shift+S while recording submits
early; two seconds without new recognized speech submits automatically after closing
the microphone. Shift+Esc exits.
"""
from __future__ import annotations

import ctypes
import json
import logging
import os
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

APP_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DarkspiritAgent"
TOKEN_PATH = APP_DIR / "voice-bridge.token"
LOG_PATH = APP_DIR / "gemini-voice.log"
BRIDGE_LOG_PATH = APP_DIR / "bridge.log"
GEMINI_URL = "https://gemini.google.com/app"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_WS_PORT = 17613
BRIDGE_HTTP_PORT = 17614
SILENCE_SECONDS = 2.0
NO_SPEECH_TIMEOUT_SECONDS = 15.0
SAMPLE_RATE = 16000
VK_SHIFT = 0x10
VK_ESCAPE = 0x1B
VK_S = 0x53
MB_YESNO = 0x00000004
MB_ICONQUESTION = 0x00000020
MB_ICONERROR = 0x00000010
MB_TOPMOST = 0x00040000
IDYES = 6
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def configure_logging() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(LOG_PATH),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        encoding="utf-8",
        force=True,
    )


def show_message(message: str, title: str = "Darkspirit Gemini Voice", flags: int | None = None) -> int:
    if os.name != "nt":
        return 0
    actual_flags = flags if flags is not None else (MB_YESNO | MB_ICONQUESTION | MB_TOPMOST)
    return int(ctypes.windll.user32.MessageBoxW(None, message, title, actual_flags))


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            return True
    except OSError:
        return False


def find_chrome() -> str | None:
    candidates = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    found = shutil.which("chrome.exe") or shutil.which("chrome")
    if found:
        return found
    return next((str(path) for path in candidates if path.is_file()), None)


def open_gemini() -> None:
    chrome = find_chrome()
    if chrome:
        subprocess.Popen(
            [chrome, GEMINI_URL],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
            close_fds=True,
        )
        logging.info("Opened Gemini in Chrome.")
    else:
        os.startfile(GEMINI_URL)  # type: ignore[attr-defined]
        logging.info("Opened Gemini in the default browser; Chrome executable was not found.")


def ensure_bridge() -> subprocess.Popen[Any] | None:
    ws_open = port_open(BRIDGE_WS_PORT)
    http_open = port_open(BRIDGE_HTTP_PORT)
    if ws_open and http_open and TOKEN_PATH.is_file():
        token = TOKEN_PATH.read_text(encoding="utf-8").strip()
        if token:
            logging.info("Reusing an existing Darkspirit bridge.")
            return None
    if ws_open or http_open:
        raise RuntimeError(
            "A partial/conflicting bridge is already listening on localhost ports "
            "17613/17614. Close the old Darkspirit bridge windows and start this companion again."
        )

    bridge_log = BRIDGE_LOG_PATH.open("a", encoding="utf-8", errors="replace")
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "darkspirit_bridge"],
            cwd=str(PROJECT_ROOT),
            stdin=subprocess.DEVNULL,
            stdout=bridge_log,
            stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW,
            close_fds=True,
        )
    finally:
        bridge_log.close()

    deadline = time.monotonic() + 18.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        if port_open(BRIDGE_WS_PORT) and port_open(BRIDGE_HTTP_PORT) and TOKEN_PATH.is_file():
            if TOKEN_PATH.read_text(encoding="utf-8").strip():
                logging.info("Started the Darkspirit browser bridge (PID %s).", proc.pid)
                return proc
        time.sleep(0.25)

    raise RuntimeError(
        "Could not start the local browser bridge. Check the bridge log at "
        f"{BRIDGE_LOG_PATH}. If another bridge is open, close it and retry."
    )


def post_transcript(text: str, token: str) -> None:
    request = urllib.request.Request(
        f"http://127.0.0.1:{BRIDGE_HTTP_PORT}/submit",
        data=json.dumps({"text": text}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Darkspirit-Token": token,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        if response.status != 200:
            raise RuntimeError(f"Voice bridge returned HTTP {response.status}.")


def play_beep(starting: bool) -> None:
    try:
        # Distinct, unobtrusive Windows system sounds; no text is spoken locally.
        ctypes.windll.user32.MessageBeep(0x00000040 if starting else 0x00000030)
    except Exception:
        pass


_INSTANCE_MUTEX: int | None = None


def acquire_single_instance() -> bool:
    """Use a named Windows mutex so repeated launches cannot duplicate microphone listeners."""
    global _INSTANCE_MUTEX
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.CreateMutexW(None, False, r"Local\DarkspiritGeminiVoice")
    if not handle:
        raise OSError("Windows could not create the Darkspirit single-instance mutex.")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return False
    _INSTANCE_MUTEX = handle
    return True


def run() -> None:
    if os.name != "nt":
        raise RuntimeError("The global Shift+S companion currently supports Windows only.")
    configure_logging()
    if not acquire_single_instance():
        logging.info("A Darkspirit Gemini Voice instance is already running; ignoring duplicate launch.")
        return
    consent = show_message(
        "Start Darkspirit Gemini Voice in the background?\n\n"
        "• Shift+S starts microphone listening.\n"
        "• Two seconds after your last recognized words, your microphone closes and the transcript is sent to the active ChatGPT/Gemini tab.\n"
        "• Press Shift+S again to submit early and stop the microphone.\n"
        "• Gemini's reply is spoken by the Darkspirit browser extension.\n"
        "• Shift+Esc exits this background companion.\n\n"
        "Audio is transcribed on this PC. The recognized text is sent to Gemini. "
        "Computer-control actions are not enabled by this voice shortcut.\n\n"
        "Allow this session?",
        flags=MB_YESNO | MB_ICONQUESTION | MB_TOPMOST,
    )
    if consent != IDYES:
        logging.info("User declined Gemini voice session.")
        return

    try:
        import sounddevice as sd
        from vosk import KaldiRecognizer, Model
    except ImportError as exc:
        show_message(
            "Voice dependencies are missing. Run this from the project folder:\n\n"
            "uv sync --extra companion",
            flags=MB_ICONERROR | MB_TOPMOST,
        )
        raise RuntimeError("Missing voice dependencies.") from exc

    microphone_device: int | None = None
    try:
        ensure_bridge()
        logging.info(
            "Voice routing will use the active ChatGPT/Gemini tab; leaving the current browser page untouched."
        )
        token = TOKEN_PATH.read_text(encoding="utf-8").strip()
        config_path = APP_DIR / "companion.json"
        config: dict[str, Any] = {}
        if config_path.is_file():
            try:
                loaded = json.loads(config_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    config = loaded
            except (OSError, json.JSONDecodeError):
                logging.warning("Could not parse companion settings; using the bundled Vosk model.")
        # Prefer an explicitly configured microphone name/index. Otherwise use
        # sounddevice's current Windows default input device and log its identity.
        configured_microphone = str(
            config.get("microphone_device") or os.environ.get("DARKSPIRIT_MIC_DEVICE") or ""
        ).strip()
        if configured_microphone:
            devices = list(sd.query_devices())
            if configured_microphone.isdigit():
                candidate_index = int(configured_microphone)
                try:
                    candidate = sd.query_devices(candidate_index)
                except Exception as exc:
                    raise RuntimeError(
                        f"Configured microphone index {candidate_index} does not exist. "
                        "Set microphone_device to an input device name in companion.json."
                    ) from exc
                if int(candidate.get("max_input_channels", 0)) < 1:
                    raise RuntimeError(
                        f"Configured device {candidate_index} is not an input microphone: {candidate.get('name', 'unknown')}."
                    )
                microphone_device = candidate_index
            else:
                matches = [
                    (index, device)
                    for index, device in enumerate(devices)
                    if int(device.get("max_input_channels", 0)) > 0
                    and configured_microphone.casefold() in str(device.get("name", "")).casefold()
                ]
                if not matches:
                    available = sorted({
                        str(device.get("name", "unknown"))
                        for device in devices
                        if int(device.get("max_input_channels", 0)) > 0
                    })
                    raise RuntimeError(
                        f"No input microphone matched '{configured_microphone}'. Available inputs: "
                        + "; ".join(available[:20])
                    )
                exact = [item for item in matches if str(item[1].get("name", "")).casefold() == configured_microphone.casefold()]
                microphone_device = (exact or matches)[0][0]
            selected_microphone = sd.query_devices(microphone_device)
            logging.info(
                "Using configured microphone index=%s name=%s",
                microphone_device,
                selected_microphone.get("name", "unknown"),
            )
        else:
            default_input = sd.default.device[0]
            try:
                default_index = int(default_input) if default_input is not None else -1
            except (TypeError, ValueError):
                default_index = -1
            if default_index >= 0:
                selected_microphone = sd.query_devices(default_index)
                logging.info(
                    "Using default microphone index=%s name=%s",
                    default_index,
                    selected_microphone.get("name", "unknown"),
                )
            else:
                logging.warning("No explicit default microphone index; using PortAudio's default input.")

        configured_model = str(config.get("vosk_model_path") or "").strip()
        model_path = Path(configured_model) if configured_model else PROJECT_ROOT / "vosk-small-en-us-0.15"
        if not model_path.is_dir():
            model_path = PROJECT_ROOT / "vosk-small-en-us-0.15"
        if not model_path.is_dir():
            raise RuntimeError(
                "The Vosk model folder was not found. Expected vosk-small-en-us-0.15 "
                "inside the project or a valid vosk_model_path in companion settings."
            )
        model = Model(str(model_path))
    except Exception as exc:
        logging.exception("Gemini voice startup failed.")
        show_message(str(exc), flags=MB_ICONERROR | MB_TOPMOST)
        return

    user32 = ctypes.windll.user32
    audio_chunks: queue.Queue[bytes] = queue.Queue(maxsize=64)
    stream = None
    recognizer = None
    segments: list[str] = []
    last_partial = ""
    last_speech_update: float | None = None
    listening_started_at: float | None = None
    listening = False
    combo_was_down = False
    shift_esc_was_down = False
    last_submit = 0.0

    def audio_callback(indata, frames, timing, status) -> None:
        del frames, timing
        if status:
            logging.debug("Microphone status: %s", status)
        try:
            audio_chunks.put_nowait(bytes(indata))
        except queue.Full:
            logging.warning("Audio queue full; dropping an audio chunk.")

    def drain_audio_queue() -> None:
        while True:
            try:
                audio_chunks.get_nowait()
            except queue.Empty:
                break

    def start_listening() -> None:
        nonlocal stream, recognizer, segments, last_partial, last_speech_update, listening_started_at, listening
        drain_audio_queue()
        recognizer = KaldiRecognizer(model, SAMPLE_RATE)
        segments = []
        last_partial = ""
        last_speech_update = None
        stream = sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            blocksize=4000,
            dtype="int16",
            channels=1,
            device=microphone_device,
            callback=audio_callback,
        )
        try:
            stream.start()
        except Exception:
            stream.close()
            stream = None
            raise
        listening = True
        listening_started_at = time.monotonic()
        logging.info("Microphone listening started by Shift+S.")
        play_beep(True)

    def finish_listening(submit: bool = True) -> None:
        nonlocal stream, recognizer, segments, last_partial, last_speech_update, listening_started_at, listening, last_submit
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                logging.debug("Audio stream stop raised.", exc_info=True)
            try:
                stream.close()
            except Exception:
                logging.debug("Audio stream close raised.", exc_info=True)
            stream = None
        if recognizer is not None:
            try:
                final_text = json.loads(recognizer.FinalResult()).get("text", "").strip()
                if final_text:
                    segments.append(final_text)
                elif last_partial:
                    # Vosk can expose a useful final partial without emitting a final result.
                    segments.append(last_partial)
            except Exception:
                logging.debug("Could not flush final speech segment.", exc_info=True)
                if last_partial:
                    segments.append(last_partial)
        recognizer = None
        listening = False
        transcript = " ".join(part.strip() for part in segments if part.strip()).strip()
        segments = []
        last_partial = ""
        last_speech_update = None
        listening_started_at = None
        if not submit or not transcript:
            if not transcript:
                logging.info("Listening stopped without recognized speech.")
            play_beep(False)
            return
        # Guard against a double hotkey/auto-silence race producing duplicate requests.
        now = time.monotonic()
        if now - last_submit < 1.5:
            logging.info("Ignored a duplicate voice submit within the debounce interval.")
            return
        try:
            post_transcript(transcript, token)
            last_submit = now
            logging.info("Transcript queued for the active AI tab (%d characters).", len(transcript))
            play_beep(False)
        except Exception:
            logging.exception("Could not queue the transcript for Gemini.")
            show_message(
                "I couldn't send your transcript to Gemini. Check that the browser bridge is running.\n\n"
                f"Log: {LOG_PATH}",
                flags=MB_ICONERROR | MB_TOPMOST,
            )

    logging.info("Voice companion ready. Shift+S starts; Shift+Esc exits.")
    # A small loop monitors system-wide keys. The microphone itself opens only after Shift+S.
    try:
        while True:
            shift_down = bool(user32.GetAsyncKeyState(VK_SHIFT) & 0x8000)
            s_down = bool(user32.GetAsyncKeyState(VK_S) & 0x8000)
            esc_down = bool(user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000)
            combo_down = shift_down and s_down
            exit_down = shift_down and esc_down

            if exit_down and not shift_esc_was_down:
                finish_listening(submit=True)
                logging.info("Gemini voice companion exited by Shift+Esc.")
                break
            shift_esc_was_down = exit_down

            if combo_down and not combo_was_down:
                if listening:
                    finish_listening(submit=True)
                else:
                    try:
                        start_listening()
                    except Exception as exc:
                        logging.exception("Could not start microphone.")
                        show_message(
                            "Could not open the microphone.\n\n"
                            f"{type(exc).__name__}: {exc}\n\n"
                            "Check Windows Settings → Privacy & security → Microphone.",
                            flags=MB_ICONERROR | MB_TOPMOST,
                        )
                time.sleep(0.3)  # debounce key repeats
            combo_was_down = combo_down

            if listening:
                try:
                    chunk = audio_chunks.get(timeout=0.02)
                except queue.Empty:
                    chunk = None
                if chunk is not None and recognizer is not None:
                    now = time.monotonic()
                    try:
                        if recognizer.AcceptWaveform(chunk):
                            result_text = json.loads(recognizer.Result()).get("text", "").strip()
                            if result_text:
                                segments.append(result_text)
                                last_speech_update = now
                                last_partial = ""
                                logging.debug("Recognized speech segment (%d characters).", len(result_text))
                        else:
                            partial = json.loads(recognizer.PartialResult()).get("partial", "").strip()
                            if partial and partial != last_partial:
                                last_partial = partial
                                last_speech_update = now
                    except Exception:
                        logging.exception("Speech recognition error.")
                # If nothing is recognized, stop after a bounded period so a wrong or
                # silent input device cannot leave the microphone open indefinitely.
                if (
                    last_speech_update is None
                    and listening_started_at is not None
                    and time.monotonic() - listening_started_at >= NO_SPEECH_TIMEOUT_SECONDS
                ):
                    logging.warning(
                        "No speech recognized after %.0f seconds. Closing microphone; check the selected input device.",
                        NO_SPEECH_TIMEOUT_SECONDS,
                    )
                    finish_listening(submit=True)

                # Use recognition changes rather than raw microphone energy, so fan noise
                # cannot keep the microphone open forever.
                if (
                    listening
                    and last_speech_update is not None
                    and time.monotonic() - last_speech_update >= SILENCE_SECONDS
                    and (segments or last_partial)
                ):
                    finish_listening(submit=True)

            time.sleep(0.025)
    except Exception:
        logging.exception("Gemini voice companion crashed.")
        show_message(
            "Darkspirit Gemini Voice stopped unexpectedly.\n\n"
            f"Log: {LOG_PATH}",
            flags=MB_ICONERROR | MB_TOPMOST,
        )
        if listening:
            finish_listening(submit=False)
        raise
    finally:
        if listening:
            finish_listening(submit=False)
        logging.info("Gemini voice companion stopped.")


if __name__ == "__main__":
    try:
        run()
    except Exception:
        logging.exception("Uncaught Gemini voice companion error.")
