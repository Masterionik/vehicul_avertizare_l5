"use strict";
/* Shared helpers: API calls with CSRF, state polling every 500 ms, top bar chips, toast. */
const CSRF = document.querySelector('meta[name="csrf-token"]').content;
const stateListeners = [];

async function api(path, method, body) {
  const options = { method: method || "GET", headers: { "X-CSRFToken": CSRF } };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  let data = {};
  try { data = await response.json(); } catch (e) { /* empty body */ }
  return { ok: response.ok, status: response.status, data: data };
}

function toast(message, bad) {
  const box = document.getElementById("toast");
  box.textContent = message;
  box.className = "toast" + (bad ? " bad" : "");
  box.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(function () { box.hidden = true; }, 3500);
}

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) { node.textContent = text; }
  if (className) { node.className = className; }
  return node;
}

function onState(fn) { stateListeners.push(fn); }

function setChip(id, label, connected, simulated) {
  const chip = document.getElementById(id);
  chip.textContent = label + ": " + (connected ? "connected" : "offline") + (simulated ? " (SIMULATED)" : "");
  chip.className = "chip " + (connected ? (simulated ? "chip-sim" : "chip-ok") : "chip-bad");
}

async function poll() {
  try {
    const result = await api("/api/state/");
    if (result.ok) {
      const s = result.data;
      document.getElementById("badge-sim").hidden = !s.simulation.any;
      setChip("chip-mesh", "LoRa Meshtastic", s.mesh.connected, s.mesh.simulated);
      setChip("chip-zigbee", "Zigbee", s.zigbee.connected, s.zigbee.simulated);
      stateListeners.forEach(function (fn) { fn(s); });
    }
  } catch (e) {
    document.getElementById("chip-mesh").className = "chip chip-bad";
  }
  setTimeout(poll, 500);
}

document.querySelectorAll("[data-nav]").forEach(function (a) {
  if (a.dataset.nav === document.body.dataset.page) { a.classList.add("active"); }
});
document.getElementById("stop-all").addEventListener("click", async function () {
  await api("/api/outputs/off/", "POST", {});
  toast("Buzzer and LED switched off");
});
poll();
