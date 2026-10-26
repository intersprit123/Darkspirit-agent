# Darkspirit Gemini Voice (Windows)

## Start

Run `start_gemini_voice.bat` from the project folder. Approve the session prompt.
The companion starts in the background and ensures the local browser bridge is running. Voice transcripts are sent to the ChatGPT or Gemini tab you are currently using; if neither AI site is open, it opens ChatGPT.

## Controls

- **Shift+S**: start microphone capture.
- **Shift+S while listening**: stop capture and submit the recognized text immediately.
- **Two seconds without new recognized words**: automatically close the microphone and submit the transcript.
- **Shift+Esc**: stop capture and exit the companion.

The microphone opens only after consent and an explicit Shift+S press. Vosk performs speech
recognition locally. The recognized text is submitted to the authenticated localhost bridge,
then the browser extension inserts it into the active ChatGPT or Gemini conversation. The extension speaks the AI's reply through Chrome's default text-to-speech voice.

Voice input normally goes to the active AI chat. To run a spoken request through the local PC task agent instead, enable **Route voice prompts through Darkspirit PC task mode** in the page panel. This is off by default. You must also check desktop-sharing consent and, for computer control, enable computer actions. Keep **Ask me before each... action** enabled; each requested computer action will still need approval.

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
- If the extension Console reports `Voice bridge is not running`, first check that the bridge is running and that `http://127.0.0.1:17614/token` is reachable from the extension. Version 0.3.3 handles opaque service-worker origins for this localhost token flow.
- If it reports `Could not establish connection. Receiving end does not exist`, the selected AI tab may have a stale/missing content-script listener after an extension reload. Version 0.3.3 checks the recipient and attempts to inject the current extension-owned content script into the selected supported AI tab. If the page remains hidden, bring that AI tab to the front. Voice prompts still expire after 60 seconds so stale commands are not sent later.
- If the extension reports `This Gemini/ChatGPT tab is not visible yet`, confirm that the intended tab is selected; direct diagnostic messages sent to a background/inactive tab can be rejected by the visibility guard.
- A raw WebSocket `opening handshake failed` traceback can be caused by an older companion checking port 17613 with a bare TCP connection. The updated companion uses a proper WebSocket handshake for readiness checks; this warning alone does not prove voice delivery failed.
- If transcripts submit but replies are silent, use the extension's **Test voice** button to check browser text-to-speech separately.


## Microphone selection and troubleshooting

The assistant logs the microphone it selects. It uses the Windows/PortAudio default input unless a specific input is configured. To list available input devices, open PowerShell in the project folder and run:

\`\`\`powershell
@'
import sounddevice as sd
print("Default input index:", sd.default.device[0])
for i, device in enumerate(sd.query_devices()):
    if device["max_input_channels"] > 0:
        print(f"{i}: {device['name']}")
'@ | .\\.venv\\Scripts\\python.exe -
\`\`\`

To choose a microphone, open %LOCALAPPDATA%\\DarkspiritAgent\\companion.json and add a **microphone_device** field using an input's exact name or index, while keeping the other existing settings. For example: **microphone_device: "Microphone (Realtek Audio)"**. Restart the voice companion after saving. If no speech is recognized for 15 seconds, the microphone now closes and the log advises checking the input device.

Voice queue entries older than 60 seconds are discarded so an old test phrase cannot unexpectedly run later. Restart the local bridge after updating to clear any transcripts already sitting in the old in-memory queue.
