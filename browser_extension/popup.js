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
