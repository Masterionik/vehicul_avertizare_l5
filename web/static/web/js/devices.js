"use strict";
function linkRow(title, info, extra) {
  const row = el("div", undefined, "row");
  row.append(el("b", title), el("span", info.connected ? "connected" : "offline",
    "badge " + (info.connected ? "badge-ok" : "badge-bad")));
  if (info.simulated) { row.append(el("span", "SIMULATED", "badge badge-sim")); }
  row.append(el("span", extra || "", "small"));
  return row;
}

async function action(id, name, confirmText) {
  const body = {};
  if (confirmText) {
    if (!window.confirm(confirmText)) { return; }
    body.confirm = true;
  }
  const r = await api("/api/devices/" + encodeURIComponent(id) + "/" + name + "/", "POST", body);
  toast(r.ok ? (r.data.message || "done") : (r.data.error || "failed"), !r.ok);
  load();
}

function deviceRow(d) {
  const row = el("div", undefined, "row");
  row.append(el("b", d.label || d.id), el("span", d.state || "", "badge"),
    el("span", (d.role || "") + "  " + (d.address || d.port || ""), "small"));
  if (d.in_use_by_pi) {
    row.append(el("span", "used by the Pi's own radio", "badge badge-warn"));
    return row;
  }
  [["Connect", "connect", ""], ["Test message", "test", ""],
   ["Disconnect", "disconnect", "Disconnect this device?"], ["Remove", "remove", "Remove this device from the list?"]]
    .forEach(function (a) {
      const b = el("button", a[0], "btn");
      b.type = "button";
      b.addEventListener("click", function () { action(d.id, a[1], a[2]); });
      row.append(b);
    });
  return row;
}

async function load() {
  const r = await api("/api/devices/");
  if (!r.ok) { return; }
  const radios = document.getElementById("radios");
  radios.replaceChildren(
    linkRow("LoRa Meshtastic modem (Heltec V3)", r.data.radios.meshtastic, r.data.radios.meshtastic.error || ""),
    linkRow("Zigbee (XBee3)", r.data.radios.zigbee, r.data.radios.zigbee.error || ""));
  const vehicles = document.getElementById("vehicles");
  vehicles.replaceChildren();
  r.data.vehicles.forEach(function (v) {
    vehicles.appendChild(linkRow(v.name + " " + v.board, v, "node " + v.node_id));
  });
  const list = document.getElementById("devices");
  list.replaceChildren();
  if (!r.data.devices.length) { list.appendChild(el("div", "No saved devices. Plug a board in and press Scan USB.", "small")); }
  r.data.devices.forEach(function (d) { list.appendChild(deviceRow(d)); });
  const log = document.getElementById("device-log");
  log.replaceChildren();
  r.data.log.slice().reverse().forEach(function (e) {
    log.appendChild(el("li", new Date(e.time * 1000).toLocaleTimeString() + "  " + e.action + " " + e.device +
      ": " + e.message + (e.ok ? "" : "  [failed]")));
  });
}

document.getElementById("scan").addEventListener("click", async function () {
  const r = await api("/api/devices/scan/", "POST", {});
  toast(r.ok ? r.data.count + " device(s) found" : "scan failed", !r.ok);
  load();
});
document.getElementById("tcp-add").addEventListener("click", async function () {
  const r = await api("/api/devices/add/", "POST", {
    host: document.getElementById("tcp-host").value, port: document.getElementById("tcp-port").value });
  toast(r.ok ? "Device saved" : (r.data.message || "failed"), !r.ok);
  load();
});
document.getElementById("mesh-test").addEventListener("click", async function () {
  const r = await api("/api/mesh/test/", "POST", {});
  toast(r.ok ? "Test message sent (" + r.data.sent_bytes + " bytes)" : (r.data.error || "failed"), !r.ok);
  load();
});
load();
setInterval(load, 3000);
