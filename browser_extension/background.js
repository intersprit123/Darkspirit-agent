const BRIDGE_URL = "ws://127.0.0.1:17613";
const REQUEST_TIMEOUT_MS = 60000;

function requestBridge(payload) {
  return new Promise(function (resolve, reject) {
    let socket;
      let settled = false;
      let sentRequest = false;
      const requestId = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + "-" + Math.random());
      const timer = setTimeout(function () {
        finish(false, new Error("Timed out waiting for the local bridge. Check that start.bat is still running."));
      }, REQUEST_TIMEOUT_MS);

      function finish(ok, value) {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        try { if (socket && socket.readyState < WebSocket.CLOSING) socket.close(); } catch (_) {}
        if (ok) resolve(value);
        else reject(value instanceof Error ? value : new Error(String(value)));
      }

      try {
        socket = new WebSocket(BRIDGE_URL);
      } catch (error) {
        finish(false, error);
        return;
      }

      socket.addEventListener("open", function () {
        socket.send(JSON.stringify({ type: "authenticate" }));
      });

      socket.addEventListener("message", function (event) {
        let message;
        try { message = JSON.parse(event.data); }
        catch (_) { finish(false, new Error("The bridge sent an invalid response.")); return; }

        if (!sentRequest && message.type === "ready") {
          sentRequest = true;
          socket.send(JSON.stringify(Object.assign({}, payload, { id: requestId })));
          return;
        }
        if (message.type === "error" && (message.id === requestId || message.id == null)) {
          finish(false, new Error(message.error || "Bridge request failed."));
          return;
        }
        if (sentRequest && message.id === requestId) finish(true, message);
      });

      socket.addEventListener("error", function () {
        finish(false, new Error("Cannot connect to ws://127.0.0.1:17613. Start the Darkspirit bridge first."));
      });
      socket.addEventListener("close", function (event) {
        if (!settled) {
          finish(false, new Error("Bridge connection closed before replying (code " + event.code + "). Check that the local bridge is running."));
        }
      });
  });
}

function sendVoiceStatus(sender, status, error) {
  const message = {
    type: "ttsStatus",
    status: status,
    error: error || "",
    target: sender && sender.tab && typeof sender.tab.id === "number" ? "tab" : "popup"
  };
  if (message.target === "tab") {
    chrome.tabs.sendMessage(sender.tab.id, message, function () {
      // A tab may have navigated away before the speech event arrives.
      void chrome.runtime.lastError;
    });
  } else {
    chrome.runtime.sendMessage(message, function () {
      // The popup may have closed before the speech event arrives.
      void chrome.runtime.lastError;
    });
  }
}

function speakWithBrowserVoice(text, sender, sendResponse, enqueue) {
  if (!chrome.tts || typeof chrome.tts.speak !== "function") {
    sendResponse({ ok: false, error: "This browser does not expose the extension TTS API. Use current Edge or Chrome on desktop." });
    return false;
  }
  const utterance = String(text || "").trim().slice(0, 6000);
  if (!utterance) {
    sendResponse({ ok: false, error: "There is no text to speak." });
    return false;
  }

  try {
    chrome.tts.speak(utterance, {
      lang: "en-US",
      rate: 1.0,
      pitch: 1.0,
      enqueue: Boolean(enqueue),
      onEvent: function (event) {
        if (!event) return;
        if (event.type === "start") sendVoiceStatus(sender, "started");
        else if (event.type === "end") sendVoiceStatus(sender, "finished");
        else if (event.type === "error") {
          const detail = event.errorMessage || "The browser speech engine could not speak this text.";
          console.warn("Darkspirit browser TTS error:", detail);
          sendVoiceStatus(sender, "error", detail);
        }
      }
    }, function () {
      const error = chrome.runtime.lastError;
      if (error) {
        sendResponse({ ok: false, error: error.message || "The browser speech request failed." });
      } else {
        sendResponse({ ok: true, message: "Speech passed to the browser's default voice." });
      }
    });
  } catch (error) {
    sendResponse({
      ok: false,
      error: error && error.message ? error.message : String(error)
    });
  }
  return true;
}


let voicePollTimer = null;
let voiceBridgeToken = null;
async function pollVoiceQueue() {
  try {
    if (!voiceBridgeToken) {
      const tokenResponse = await fetch("http://127.0.0.1:17614/token", { cache: "no-store" });
      if (!tokenResponse.ok) throw new Error("Voice bridge is not running");
      const tokenData = await tokenResponse.json();
      voiceBridgeToken = tokenData.token;
    }
    const response = await fetch("http://127.0.0.1:17614/next", {
      headers: { "X-Darkspirit-Token": voiceBridgeToken },
      cache: "no-store"
    });
    if (!response.ok) throw new Error("Voice queue request failed");
    const data = await response.json();
    if (data.text) {
      const tabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
      const tab = tabs.find(function (candidate) {
        return candidate.url && /^https:\/\/(chatgpt\.com|chat\.openai\.com|gemini\.google\.com)\//i.test(candidate.url);
      });
      if (tab && tab.id) {
        await chrome.tabs.sendMessage(tab.id, { type: "darkspiritVoiceSubmit", text: data.text });
      }
    }
  } catch (error) {
    voiceBridgeToken = null;
  }
}
function startVoiceQueuePolling() {
  if (voicePollTimer) return;
  pollVoiceQueue();
  voicePollTimer = setInterval(pollVoiceQueue, 500);
}
chrome.runtime.onStartup.addListener(startVoiceQueuePolling);
chrome.runtime.onInstalled.addListener(startVoiceQueuePolling);
startVoiceQueuePolling();

chrome.runtime.onMessage.addListener(function (message, sender, sendResponse) {
  if (!message || typeof message.type !== "string") return false;

  if (message.type === "ttsSpeak") {
    return speakWithBrowserVoice(message.text, sender, sendResponse, true);
  }

  if (message.type === "ttsTest") {
    return speakWithBrowserVoice(
      "Hello! This is Darkspirit using your browser's default voice.",
      sender,
      sendResponse,
      false
    );
  }

  if (message.type === "ttsStop") {
    try {
      if (chrome.tts && typeof chrome.tts.stop === "function") {
        chrome.tts.stop();
        sendResponse({ ok: true });
      } else {
        sendResponse({ ok: false, error: "The browser speech API is unavailable." });
      }
    } catch (error) {
      sendResponse({ ok: false, error: error && error.message ? error.message : String(error) });
    }
    return false;
  }

  if (message.type !== "bridgeRequest") return false;
  requestBridge(message.payload || {}).then(function (result) {
    sendResponse({ ok: true, result: result });
  }).catch(function (error) {
    sendResponse({ ok: false, error: error && error.message ? error.message : String(error) });
  });
  return true;
});
