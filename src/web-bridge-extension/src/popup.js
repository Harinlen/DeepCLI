const bridgeWsUrl = document.getElementById("bridgeWsUrl");
const pairingToken = document.getElementById("pairingToken");
const statusEl = document.getElementById("status");

document.getElementById("connect").addEventListener("click", async () => {
  await send("save_and_connect", {
    bridgeWsUrl: bridgeWsUrl.value.trim(),
    pairingToken: pairingToken.value.trim(),
  });
  await refresh();
});

document.getElementById("disconnect").addEventListener("click", async () => {
  await send("disconnect", {});
  await refresh();
});

async function refresh() {
  const status = await send("status", {});
  bridgeWsUrl.value = status.bridgeWsUrl || bridgeWsUrl.value;
  statusEl.textContent = JSON.stringify(status, null, 2);
}

function send(command, payload) {
  return chrome.runtime.sendMessage({ type: "webbridge_command", command, payload });
}

void refresh();
