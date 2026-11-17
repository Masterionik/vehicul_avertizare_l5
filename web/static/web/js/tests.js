"use strict";
const TEST_NAMES = { buzzer_led: "Buzzer + LED pulse", ultrasonic: "Ultrasonic single read",
  vibration: "Vibration count", meshtastic: "LoRa Meshtastic send", zigbee: "Zigbee send", rte: "RTE self-test" };
const rows = {};

function showResult(res) {
  const row = rows[res.name];
  if (!row) { return; }
  row.badge.textContent = res.status;
  row.badge.className = "badge badge-" + (res.status === "PASS" ? "ok" : res.status === "FAIL" ? "bad" : "warn");
  row.detail.textContent = res.detail;
}

async function run(name, button) {
  if (button) { button.disabled = true; }
  const r = await api("/api/tests/run/", "POST", { name: name });
  if (r.ok) { r.data.results.forEach(showResult); } else { toast(r.data.error || "failed", true); }
  if (button) { button.disabled = false; }
}

const container = document.getElementById("tests");
Object.keys(TEST_NAMES).forEach(function (name) {
  const row = el("div", undefined, "row");
  const badge = el("span", "--", "badge");
  const detail = el("span", "", "small");
  const button = el("button", "Run", "btn");
  button.type = "button";
  button.addEventListener("click", function () { run(name, button); });
  row.append(el("b", TEST_NAMES[name]), badge, detail, button);
  container.appendChild(row);
  rows[name] = { badge: badge, detail: detail };
});
document.getElementById("run-all").addEventListener("click", function (e) { run("all", e.target); });
