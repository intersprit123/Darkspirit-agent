"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function loadBackground(overrides = {}) {
  let messageListener;
  const spoken = [];
  const sentTabMessages = [];
  let stopped = false;
  const chrome = {
    runtime: {
      lastError: null,
      onMessage: {
        addListener(listener) {
          messageListener = listener;
        }
      },
      onStartup: {
        addListener() {}
      },
      onInstalled: {
        addListener() {}
      }
    },
    alarms: {
      onAlarm: {
        addListener() {}
      },
      create() {}
    },
    storage: {
      session: {
        async get() { return {}; },
        async set() {},
        async remove() {}
      }
    },
    tts: {
      speak(text, options, callback) {
        spoken.push({ text, options });
        callback();
      },
      stop() {
        stopped = true;
      }
    },
    tabs: {
      sendMessage(tabId, message, callback) {
        sentTabMessages.push({ tabId, message });
        if (callback) callback();
      }
    },
    ...overrides
  };
  const source = fs.readFileSync(
    path.join(__dirname, "../../browser_extension/background.js"),
    "utf8"
  );
  vm.runInNewContext(source, {
    chrome,
    WebSocket: function WebSocket() {},
    crypto: { randomUUID: () => "test-request-id" },
    setTimeout,
    clearTimeout,
    Promise,
    Error,
    String,
    Math,
    Date,
    JSON,
    console
  });
  assert.equal(typeof messageListener, "function");
  return { listener: messageListener, spoken, sentTabMessages, getStopped: () => stopped };
}

test("voice test uses the browser default English voice", () => {
  const harness = loadBackground();
  let response;
  const keepChannelOpen = harness.listener(
    { type: "ttsTest" },
    { tab: { id: 42 } },
    (value) => { response = value; }
  );

  assert.equal(keepChannelOpen, true);
  assert.match(harness.spoken[0].text, /browser's default voice/i);
  assert.equal(harness.spoken[0].options.lang, "en-US");
  assert.equal(harness.spoken[0].options.voiceName, undefined);
  assert.equal(response.ok, true);
});

test("automatic replies are queued for the browser default voice", () => {
  const harness = loadBackground();
  let response;
  harness.listener(
    { type: "ttsSpeak", text: "Hello from Darkspirit" },
    { tab: { id: 3 } },
    (value) => { response = value; }
  );

  assert.equal(harness.spoken[0].text, "Hello from Darkspirit");
  assert.equal(harness.spoken[0].options.enqueue, true);
  assert.equal(response.ok, true);
});

test("voice events return to the tab that requested speech", () => {
  const harness = loadBackground();
  harness.listener(
    { type: "ttsTest" },
    { tab: { id: 17 } },
    () => {}
  );
  harness.spoken[0].options.onEvent({ type: "start" });
  assert.equal(harness.sentTabMessages.length, 1);
  assert.equal(harness.sentTabMessages[0].tabId, 17);
  assert.equal(harness.sentTabMessages[0].message.type, "ttsStatus");
  assert.equal(harness.sentTabMessages[0].message.status, "started");
});

test("stop message stops browser speech", () => {
  const harness = loadBackground();
  let response;
  const keepsChannelOpen = harness.listener(
    { type: "ttsStop" },
    {},
    (value) => { response = value; }
  );
  assert.equal(keepsChannelOpen, false);
  assert.equal(harness.getStopped(), true);
  assert.equal(response.ok, true);
});

test("speech rejects empty input cleanly", () => {
  const harness = loadBackground();
  let response;
  const keepsChannelOpen = harness.listener(
    { type: "ttsSpeak", text: "   " },
    {},
    (value) => { response = value; }
  );
  assert.equal(keepsChannelOpen, false);
  assert.equal(response.ok, false);
  assert.match(response.error, /no text/i);
});
