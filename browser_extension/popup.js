const statusBox = document.getElementById("status");
const testButton = document.getElementById("test");

testButton.addEventListener("click", async function () {
  statusBox.textContent = "Connecting to local bridge…";
  try {
    const response = await chrome.runtime.sendMessage({
      type: "bridgeRequest",
      payload: { type: "list_tools" }
    });
    if (!response || !response.ok) throw new Error(response && response.error || "Bridge request failed.");
    const result = response.result;
    statusBox.textContent = "Connected. " + ((result.tools || []).length) + " tools found. Actions are " +
      (result.actions_enabled ? "enabled" : "disabled") + ".";
  } catch (error) {
    statusBox.textContent = "Connection failed: " + (error.message || String(error));
  }
});


const testVoiceButton = document.getElementById("test-voice");
const stopVoiceButton = document.getElementById("stop-voice");

function sendVoiceRequest(message, acceptedMessage) {
  statusBox.textContent = "Requesting the browser's default voice…";
  chrome.runtime.sendMessage(message, function (response) {
    const runtimeError = chrome.runtime.lastError;
    if (runtimeError) {
      statusBox.textContent = "Voice request failed: " + runtimeError.message;
      return;
    }
    if (!response || !response.ok) {
      statusBox.textContent = "Voice request failed: " +
        (response && response.error ? response.error : "No response from the speech service.");
      return;
    }
    statusBox.textContent = acceptedMessage || response.message || "Voice request accepted.";
  });
}

testVoiceButton.addEventListener("click", function () {
  sendVoiceRequest({ type: "ttsTest" }, "Test sent to the browser's default voice. Listen for the spoken sentence.");
});

stopVoiceButton.addEventListener("click", function () {
  sendVoiceRequest({ type: "ttsStop" }, "Speech stopped.");
});

chrome.runtime.onMessage.addListener(function (message) {
  if (!message || message.type !== "ttsStatus" || message.target !== "popup") return false;
  if (message.status === "error") statusBox.textContent = "Browser voice failed: " + (message.error || "unknown speech error");
  else if (message.status === "started") statusBox.textContent = "Speaking with the browser's default voice…";
  else if (message.status === "finished") statusBox.textContent = "Voice playback finished.";
  return false;
});
