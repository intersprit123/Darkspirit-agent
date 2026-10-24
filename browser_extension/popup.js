const tokenInput = document.getElementById("token");
const statusBox = document.getElementById("status");
const saveButton = document.getElementById("save");
const testButton = document.getElementById("test");

chrome.storage.local.get(["bridgeToken"], function (result) {
  tokenInput.value = result.bridgeToken || "";
  statusBox.textContent = tokenInput.value ? "Pairing token saved." : "Paste the token printed by start.bat.";
});

saveButton.addEventListener("click", function () {
  const token = tokenInput.value.trim();
  if (token.length < 32) {
    statusBox.textContent = "That token looks too short. Copy the entire token from the bridge terminal.";
    return;
  }
  chrome.storage.local.set({ bridgeToken: token }, function () {
    statusBox.textContent = "Token saved in this browser profile.";
  });
});

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