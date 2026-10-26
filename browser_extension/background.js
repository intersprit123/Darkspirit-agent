const BRIDGE_URL = "ws://127.0.0.1:17613";
const REQUEST_TIMEOUT_MS = 60000;
const VOICE_PENDING_TTL_MS = 60000;

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
let pendingVoiceText = null;
let pendingVoiceQueuedAt = 0;
let voicePollInFlight = false;
let voiceDeliveryRetryAfter = 0;
let lastVoicePollError = "";
let lastVoicePollErrorAt = 0;

async function ensureAITab() {
  const allTabs = await chrome.tabs.query({});
  const activeTabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  const isChatGPTTab = function (candidate) {
    return candidate.url &&
      (candidate.url.indexOf("https://chatgpt.com/") === 0 ||
       candidate.url.indexOf("https://chat.openai.com/") === 0);
  };
  const isGeminiTab = function (candidate) {
    return candidate.url && candidate.url.indexOf("https://gemini.google.com/") === 0;
  };
  const isAITab = function (candidate) {
    return isChatGPTTab(candidate) || isGeminiTab(candidate);
  };

  // Prefer the AI page the user is currently looking at. This lets voice prompts
  // land in the active ChatGPT conversation instead of always switching to Gemini.
  let tab = activeTabs.find(isAITab);

  if (!tab) {
    const chatGPTMatches = allTabs.filter(isChatGPTTab);
    const geminiMatches = allTabs.filter(isGeminiTab);
    chatGPTMatches.sort(function (a, b) {
      return (b.lastAccessed || 0) - (a.lastAccessed || 0);
    });
    geminiMatches.sort(function (a, b) {
      return (b.lastAccessed || 0) - (a.lastAccessed || 0);
    });
    // Prefer a signed-in ChatGPT tab when the user is not actively on either AI site.
    tab = chatGPTMatches[0] || geminiMatches[0];
  }

  if (!tab) {
    tab = await chrome.tabs.create({ url: "https://chatgpt.com/", active: true });
  } else {
    await chrome.tabs.update(tab.id, { active: true });
  }
  try {
    if (typeof tab.windowId === "number") {
      await chrome.windows.update(tab.windowId, { focused: true });
    }
  } catch (_) {
    // Some managed browser contexts do not allow programmatic window focusing.
  }
  return tab;
}

async function clearPendingVoice(reason) {
  if (reason) console.warn("Darkspirit voice prompt discarded:", reason);
  pendingVoiceText = null;
  pendingVoiceQueuedAt = 0;
  await chrome.storage.session.remove("darkspiritPendingVoiceText");
}

async function deliverPendingVoice() {
  if (!pendingVoiceText) return false;
  if (pendingVoiceQueuedAt && Date.now() - pendingVoiceQueuedAt > VOICE_PENDING_TTL_MS) {
    await clearPendingVoice("it was older than 60 seconds, so an old prompt cannot unexpectedly run later.");
    return true;
  }
  const tab = await ensureAITab();
  try {
    const response = await chrome.tabs.sendMessage(tab.id, {
      type: "darkspiritVoiceSubmit",
      text: pendingVoiceText
    });
    if (!response || response.ok !== true) {
      throw new Error(response && response.error ? response.error : "The AI page did not confirm prompt submission.");
    }
    pendingVoiceText = null;
    pendingVoiceQueuedAt = 0;
    await chrome.storage.session.remove("darkspiritPendingVoiceText");
    console.info("Darkspirit voice transcript submitted to the active AI page.");
    return true;
  } catch (error) {
    // Keep the transcript and retry until the page confirms that submission
    // succeeded. A delivered extension message alone is not a successful send.
    const detail = error && error.message ? error.message : String(error);
    voiceDeliveryRetryAfter = Date.now() + 2500;
    console.warn("Darkspirit voice transcript is still pending:", detail);
    return false;
  }
}

async function pollVoiceQueue() {
  if (voicePollInFlight) return;
  if (pendingVoiceText && Date.now() < voiceDeliveryRetryAfter) return;
  voicePollInFlight = true;
  try {
    if (!pendingVoiceText) {
      const stored = await chrome.storage.session.get("darkspiritPendingVoiceText");
      const saved = stored.darkspiritPendingVoiceText;
      if (saved && typeof saved === "object" && typeof saved.text === "string") {
        pendingVoiceText = saved.text.trim();
        pendingVoiceQueuedAt = Number(saved.createdAt) || 0;
        if (!pendingVoiceText || !pendingVoiceQueuedAt || Date.now() - pendingVoiceQueuedAt > VOICE_PENDING_TTL_MS) {
          await clearPendingVoice("the saved prompt was empty, too old, or from an incomplete previous session.");
        }
      } else if (typeof saved === "string") {
        // Old versions saved the transcript without a timestamp. Do not replay it
        // into a live conversation after an extension update.
        await clearPendingVoice("a legacy pending prompt had no timestamp.");
      }
    }
    if (pendingVoiceText) {
      await deliverPendingVoice();
      if (pendingVoiceText) return;
    }
    if (!voiceBridgeToken) {
      const tokenResponse = await fetch("http://127.0.0.1:17614/token", { cache: "no-store" });
      if (!tokenResponse.ok) throw new Error("Voice bridge is not running");
      const tokenData = await tokenResponse.json();
      if (!tokenData || typeof tokenData.token !== "string" || !tokenData.token) {
        throw new Error("Voice bridge returned an invalid token");
      }
      voiceBridgeToken = tokenData.token;
    }
    const response = await fetch("http://127.0.0.1:17614/next", {
      headers: { "X-Darkspirit-Token": voiceBridgeToken },
      cache: "no-store"
    });
    if (!response.ok) throw new Error("Voice queue request failed");
    const data = await response.json();
    if (typeof data.text === "string" && data.text.trim()) {
      const serverQueuedAt = Number(data.queued_at);
      if (!Number.isFinite(serverQueuedAt) || serverQueuedAt <= 0) {
        console.warn("Darkspirit discarded an untimestamped voice prompt. Restart the local bridge to enable safe queue timestamps.");
        return;
      }
      pendingVoiceQueuedAt = serverQueuedAt * 1000;
      if (Date.now() - pendingVoiceQueuedAt > VOICE_PENDING_TTL_MS) {
        console.warn("Darkspirit discarded an old voice prompt before delivery.");
        pendingVoiceText = null;
        pendingVoiceQueuedAt = 0;
        return;
      }
      pendingVoiceText = data.text.trim();
      await chrome.storage.session.set({
        darkspiritPendingVoiceText: {
          text: pendingVoiceText,
          createdAt: pendingVoiceQueuedAt
        }
      });
      await deliverPendingVoice();
    }
  } catch (error) {
    voiceBridgeToken = null;
    const detail = error && error.message ? error.message : String(error);
    const now = Date.now();
    if (detail !== lastVoicePollError || now - lastVoicePollErrorAt >= 15000) {
      console.warn("Darkspirit voice queue polling failed:", detail);
      lastVoicePollError = detail;
      lastVoicePollErrorAt = now;
    }
  } finally {
    voicePollInFlight = false;
  }
}

function startVoiceQueuePolling() {
  if (voicePollTimer) clearInterval(voicePollTimer);
  pollVoiceQueue();
  // Fast polling while Chrome keeps this worker awake. The periodic alarm is a
  // recovery path if Manifest V3 suspends the service worker.
  voicePollTimer = setInterval(pollVoiceQueue, 750);
  chrome.alarms.create("darkspiritVoiceQueue", { periodInMinutes: 1 });
}

chrome.alarms.onAlarm.addListener(function (alarm) {
  if (alarm && alarm.name === "darkspiritVoiceQueue") pollVoiceQueue();
});
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
