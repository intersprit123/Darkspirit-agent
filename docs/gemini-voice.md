# Darkspirit Gemini Voice (Windows)

## Start

Run `start_gemini_voice.bat` from the project folder. Approve the session prompt.
The companion starts in the background, ensures the local browser bridge is running,
and opens Gemini in Chrome.

## Controls

- **Shift+S**: start microphone capture.
- **Shift+S while listening**: stop capture and submit the recognized text immediately.
- **Five seconds without new recognized words**: automatically submit and close the microphone.
- **Shift+Esc**: stop capture and exit the companion.

The microphone opens only after consent and an explicit Shift+S press. Vosk performs speech
recognition locally. The recognized text is submitted to the authenticated localhost bridge,
then the browser extension inserts it into Gemini. The extension speaks Gemini's reply through
Chrome's default text-to-speech voice. This voice path does not enable PC-control actions.

## First-time browser setup

1. Open `chrome://extensions`.
2. Ensure **Darkspirit Agent — Browser AI** is enabled and loaded from this repository's
   `browser_extension` folder.
3. After updating extension files, click **Reload** on the extension once.
4. Open Gemini and sign in if needed.
5. Run `start_gemini_voice.bat`, approve the session, and press Shift+S.

## Troubleshooting

- Check `%LOCALAPPDATA%\DarkspiritAgent\gemini-voice.log` for shortcut and speech status.
- Check `%LOCALAPPDATA%\DarkspiritAgent\bridge.log` if the local bridge fails to start.
- If another bridge window is already running, close duplicate Darkspirit bridge windows and retry.
- If no microphone is detected, check Windows **Settings → Privacy & security → Microphone**.
- If transcripts submit but replies are silent, reload the browser extension and use its **Test default voice** button.
