# Browser AI integration (experimental)

Darkspirit Agent can use the AI chat page already open in Microsoft Edge or Google Chrome. This approach does not require an OpenAI or Gemini API key: prompts are sent through the website UI in your signed-in browser session. The website's account limits, service rules, and page redesigns still apply.

## What is included

- A Manifest V3 extension for ChatGPT and Gemini.
- A localhost WebSocket bridge at 127.0.0.1:17613.
- A pairing token stored under the current Windows user's home directory, not in Git.
- A limited Windows-Use tool catalogue: application/window actions, mouse/keyboard actions, scrolling, waiting, and a read-only desktop summary.
- Per-action confirmation enabled by default.
- A 30-step limit per task.

The browser bridge does not expose Windows-Use shell, arbitrary-code, direct file-management, or memory tools. Because UI automation can still be misused (for example, by opening a terminal in the UI), inspect each action preview before approving it. Do not approve an action you do not understand.

## Install dependencies

1. Open the repository folder in Windows.
2. Double-click **install.bat**, or run it from PowerShell.
3. It runs **uv sync --extra dev** from the existing pyproject.toml and uv.lock, then performs basic import and Python compilation checks. The WebSocket dependency is already declared in the project.
4. If uv is missing, the installer tries Windows Package Manager (winget). If that isn't available, follow the official uv installation instructions shown by the installer.

This does not install a browser, alter browser profiles, download an AI model, or install Ollama. Ollama is optional and is used only by the separate local-agent mode.

## Load the extension

1. In Edge, open **edge://extensions**; in Chrome, open **chrome://extensions**.
2. Turn on **Developer mode**.
3. Choose **Load unpacked**.
4. Select the repository's **browser_extension** directory.
5. Open the extension's toolbar popup.
6. Start the bridge using **start.bat** and choose option 2. Keep its terminal window open.
7. Copy the pairing token printed in that terminal into the extension popup and choose **Save token**.
8. Choose **Test bridge**. It should report that the bridge is connected and show the number of available tools.

Never commit the pairing token or share it in screenshots, chat messages, or issues. If the token is exposed, delete the local token file at %USERPROFILE%\.darkspirit-agent\bridge.token and restart the bridge to generate a new one, then save the new token in the extension popup.

## Run a first task

1. Open https://chatgpt.com or https://gemini.google.com and sign in normally.
2. The Darkspirit Agent panel appears near the top-right of the page.
3. Enter a simple task, such as asking it to inspect the current desktop and explain which window is active.
4. Check the desktop-sharing consent box. Window titles and a sanitized accessibility summary are sent to the AI website as part of the conversation. Text entered in edit/text fields is hidden from the initial desktop summary; email addresses and long number sequences are redacted. This is not a guarantee that all sensitive content on screen will be detected.
5. For any task that changes the computer, check **Enable computer actions for this task**. Leave **Ask me before each... action** checked, especially for early tests.
6. Choose **Start task**. Review every pending action's tool name and parameters, then approve or deny it.
7. Choose **Stop** to stop the next steps. An action already in progress may finish.

Start with a harmless task, such as launching Notepad. Do not test on banking, school portals with personal information, admin tools, or unsaved documents.

## Launcher options

**start.bat** offers:
1. Install/update project dependencies.
2. Start the browser bridge. The bridge listens only on localhost and starts with computer actions disabled.
3. Start the separate Ollama-backed Windows-Use CLI with a 10-step limit. That mode is full computer control and is not the browser AI path.
4. Exit.

The bridge does not kill programs if the port is occupied. Close the actual program holding port 17613 yourself only after identifying it.

## Limitations and troubleshooting

- This is an early integration. ChatGPT or Gemini can change their composer or response DOM and break prompt sending or response parsing. The two providers are supported by best-effort page selectors, not an official API.
- The AI website may not follow the required JSON command protocol. If it repeatedly fails, stop the task and use the page normally.
- If the extension says the bridge is unreachable, keep the bridge terminal open and use **Test bridge** in the extension popup.
- If authentication fails, copy the entire current token from the bridge terminal again. Never publish it.
- If start.bat reports that port 17613 is already in use, it will not terminate the other process.
- The browser extension itself is loaded unpacked, so no Node.js build step or extension package manager is required.

## Privacy and safety

This mode sends the task and initial desktop accessibility summary to the AI website in the active tab. The AI website is a third-party service; its normal account, retention, and policy conditions apply. Do not use this mode with sensitive screens.

The bridge binds to 127.0.0.1, validates a random token, rejects other browser origins, and only dispatches an explicit allow-list. It is not a security boundary against malware running under your Windows account. Keep the extension and token private, and stop the bridge when you are finished.
