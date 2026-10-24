# Background companion prototype

This is an opt-in prototype for a quiet Windows companion. It does not replace the browser AI bridge; that remains available separately.

## Current scope

- No startup window when launched with pythonw.
- Local Windows SAPI greeting after an enrolled face is recognized.
- Optional local face enrollment with OpenCV LBPH; the model stays in %LOCALAPPDATA%\DarkspiritAgent.
- Optional offline speech capture using Vosk and sounddevice after the face greeting.
- Camera and microphone settings default to OFF.
- Config and operational logs stay in %LOCALAPPDATA%\DarkspiritAgent.
- A startup shortcut is installed only if the user explicitly runs install_companion_startup.bat; remove it with remove_companion_startup.bat.

## Setup

1. Run install.bat and choose dependency installation.
2. Run uv sync --extra companion in PowerShell to install the optional face and offline voice packages.
3. Run setup_companion.bat. Explicitly choose whether to enable the camera, microphone, and local memory. If camera is enabled, the face-enrollment window captures samples locally.
4. To use speech recognition, download a Vosk model from the official Vosk website yourself, extract it locally, and set vosk_model_path in %LOCALAPPDATA%\DarkspiritAgent\companion.json to that folder. This prototype does not download models automatically.
5. Run start.bat and choose option 4 to launch the background companion without a console window.
6. To launch at Windows sign-in, run install_companion_startup.bat. Run remove_companion_startup.bat to remove that shortcut.

## Important limitations

This is a foundation, not the complete conversational assistant. The current offline speech prototype repeats the recognized phrase and explicitly says the conversation connection is not configured. It does not yet connect spoken transcripts to Gemini/ChatGPT, run a wake-word detector, store conversational memories, or implement a tray icon. The existing browser AI extension is still the conversational task loop and currently injects its panel into AI webpages.

Face matching uses a simple local OpenCV LBPH model, which can make false matches and is not secure authentication. Do not use face recognition to authorize sensitive operations. A detected face is not proof of identity. The webcam is opened only when the saved camera setting is enabled and a face model exists; microphone capture starts only when enabled and after the face greeting.

No administrator privileges are requested by this prototype. When an operation needs elevation, the eventual implementation must launch a specific trusted action and rely on Windows UAC; it must not silently elevate the whole agent. The prototype does not request UAC.

Use this prototype only on your own PC with people who consent to being recorded or recognized. Check Windows Settings > Privacy & security > Camera / Microphone to manage OS permissions. Logs do not intentionally contain raw audio, transcripts, or camera frames.
