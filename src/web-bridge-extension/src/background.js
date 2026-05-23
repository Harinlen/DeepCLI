const PROTOCOL_VERSION = "web-bridge.v1";
const HEARTBEAT_MS = 15000;
const DISCOVERY_URLS = ["http://127.0.0.1:8200/web-bridge/status.json"];

let socket = null;
let heartbeat = null;
let discoveryTimer = null;
let manuallyDisconnected = false;
let lastDiscoveryError = "";

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || message.type !== "webbridge_command") return false;
  handlePopupCommand(message.command, message.payload || {}).then(sendResponse);
  return true;
});

chrome.runtime.onInstalled.addListener(() => {
  void discoverAndConnect();
});

chrome.runtime.onStartup.addListener(() => {
  void discoverAndConnect();
});

void discoverAndConnect();

async function handlePopupCommand(command, payload) {
  if (command === "save_and_connect") {
    manuallyDisconnected = false;
    await chrome.storage.local.set({
      bridgeWsUrl: payload.bridgeWsUrl || "",
      pairingToken: payload.pairingToken || "",
    });
    return await connectFromStorage();
  }
  if (command === "status") {
    const current = await status();
    if (!current.connected) return await discoverAndConnect();
    return current;
  }
  if (command === "disconnect") {
    closeSocket(true);
    return await status();
  }
  return { ok: false, error: "unknown_command" };
}

async function discoverAndConnect() {
  if (manuallyDisconnected) return await status();
  for (const url of DISCOVERY_URLS) {
    try {
      const response = await fetch(url, { cache: "no-store" });
      if (!response.ok) throw new Error(`status ${response.status}`);
      const data = await response.json();
      if (!data.bridgeWsUrl) throw new Error("missing bridgeWsUrl");
      await chrome.storage.local.set({
        bridgeWsUrl: data.bridgeWsUrl,
        pairingToken: data.pairingToken || "",
      });
      lastDiscoveryError = "";
      return await connectFromStorage({ allowDiscovery: false });
    } catch (error) {
      lastDiscoveryError = error instanceof Error ? error.message : String(error);
    }
  }
  scheduleDiscovery();
  return await status();
}

async function connectFromStorage(options = { allowDiscovery: true }) {
  const config = await chrome.storage.local.get(["bridgeWsUrl", "pairingToken", "secret"]);
  if (!config.bridgeWsUrl) {
    if (options.allowDiscovery) return await discoverAndConnect();
    return { ok: false, error: "missing_bridge_url" };
  }
  closeSocket();
  socket = new WebSocket(config.bridgeWsUrl);
  socket.addEventListener("open", () => {
    send({
      type: "hello",
      protocolVersion: PROTOCOL_VERSION,
      extensionId: chrome.runtime.id,
      pairingToken: config.pairingToken || null,
      secret: config.secret || null,
      browser: { name: "Chrome", version: navigator.userAgent },
    });
    heartbeat = setInterval(() => send({ type: "heartbeat" }), HEARTBEAT_MS);
  });
  socket.addEventListener("message", event => {
    void handleBridgeMessage(event.data);
  });
  socket.addEventListener("error", () => {
    lastDiscoveryError = "websocket_error";
  });
  socket.addEventListener("close", () => {
    clearInterval(heartbeat);
    heartbeat = null;
    socket = null;
    if (!manuallyDisconnected) scheduleDiscovery();
  });
  return await status();
}

async function handleBridgeMessage(raw) {
  const message = JSON.parse(raw);
  if (message.type === "hello_ack") {
    if (message.secret) await chrome.storage.local.set({ secret: message.secret, pairingToken: "" });
    if (message.ok === false) scheduleDiscovery();
    return;
  }
  if (message.type === "fetch_tab") {
    const result = await fetchTab(message);
    send({ type: "fetch_result", id: message.id, ...result });
  }
}

async function fetchTab(request) {
  let tabId = null;
  try {
    const tab = await chrome.tabs.create({ url: request.url, active: false });
    tabId = tab.id;
    if (!tabId) throw new Error("tab_create_failed");
    await waitForComplete(tabId);
    await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
    const extracted = await chrome.tabs.sendMessage(tabId, { type: "extract_page" });
    const finalTab = await chrome.tabs.get(tabId);
    const text = String(extracted.text || "");
    const max = Number(request.maxTextChars || 50000);
    return {
      ok: true,
      url: request.url,
      finalUrl: finalTab.url || request.url,
      title: extracted.title || finalTab.title || "",
      text: text.slice(0, max),
      readabilityText: String(extracted.readabilityText || text).slice(0, max),
      html: "",
      metadata: extracted.metadata || {},
      signals: { loaded: true, timedOut: false, closedTab: true, textLength: text.length },
      extractionMethod: "content_script_visible_text",
    };
  } catch (error) {
    return {
      ok: false,
      url: request.url,
      finalUrl: request.url,
      title: "",
      text: "",
      readabilityText: "",
      html: "",
      metadata: {},
      signals: { loaded: false, timedOut: false, closedTab: tabId !== null, textLength: 0 },
      extractionMethod: "content_script_visible_text",
      error: "fetch_failed",
      message: error instanceof Error ? error.message : String(error),
    };
  } finally {
    if (tabId !== null) {
      await chrome.tabs.remove(tabId).catch(() => undefined);
    }
  }
}

function waitForComplete(tabId) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      reject(new Error("load_timeout"));
    }, 30000);
    const listener = (updatedTabId, changeInfo) => {
      if (updatedTabId !== tabId || changeInfo.status !== "complete") return;
      clearTimeout(timeout);
      chrome.tabs.onUpdated.removeListener(listener);
      resolve();
    };
    chrome.tabs.onUpdated.addListener(listener);
  });
}

async function status() {
  const config = await chrome.storage.local.get(["bridgeWsUrl", "pairingToken", "secret"]);
  return {
    ok: true,
    connected: socket?.readyState === WebSocket.OPEN,
    bridgeWsUrl: config.bridgeWsUrl || "",
    hasSecret: Boolean(config.secret),
    hasPairingToken: Boolean(config.pairingToken),
    lastDiscoveryError,
  };
}

function send(payload) {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(payload));
}

function scheduleDiscovery() {
  if (discoveryTimer !== null || manuallyDisconnected) return;
  discoveryTimer = setTimeout(() => {
    discoveryTimer = null;
    void discoverAndConnect();
  }, 2000);
}

function closeSocket(manual = false) {
  manuallyDisconnected = manual;
  clearInterval(heartbeat);
  if (discoveryTimer !== null) clearTimeout(discoveryTimer);
  discoveryTimer = null;
  heartbeat = null;
  if (socket) socket.close();
  socket = null;
}
