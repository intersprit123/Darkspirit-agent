const BRIDGE_URL = "ws://127.0.0.1:17613";
const REQUEST_TIMEOUT_MS = 60000;

function requestBridge(payload) {
  return new Promise(function (resolve, reject) {
    chrome.storage.local.get(["bridgeToken"], function (storage) {
      const token = (storage && storage.bridgeToken || "").trim();
      if (!token) {
        reject(new Error("No pairing token saved. Open the extension popup and paste the token shown by start.bat."));
        return;
      }

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
        socket.send(JSON.stringify({ type: "authenticate", token: token }));
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
          finish(false, new Error("Bridge connection closed before replying (code " + event.code + "). Check the pairing token and bridge log."));
        }
      });
    });
  });
}

chrome.runtime.onMessage.addListener(function (message, sender, sendResponse) {
  if (!message || message.type !== "bridgeRequest") return false;
  requestBridge(message.payload || {}).then(function (result) {
    sendResponse({ ok: true, result: result });
  }).catch(function (error) {
    sendResponse({ ok: false, error: error && error.message ? error.message : String(error) });
  });
  return true;
});