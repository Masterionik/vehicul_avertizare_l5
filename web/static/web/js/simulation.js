"use strict";
const distSlider = document.getElementById("dist-slider");
const vibSlider = document.getElementById("vib-slider");
let sendTimer = null;

async function setDistance(value) {
  const r = await api("/api/sim/distance/", "POST", { distance_cm: Number(value) });
  if (!r.ok) { toast(r.data.error || "failed", true); }
}

distSlider.addEventListener("input", function () {
  document.getElementById("dist-out").textContent = distSlider.value + " cm";
  clearTimeout(sendTimer);
  sendTimer = setTimeout(function () { setDistance(distSlider.value); }, 120);
});
document.querySelectorAll("[data-dist]").forEach(function (b) {
  b.addEventListener("click", function () {
    distSlider.value = Math.min(400, Number(b.dataset.dist));
    document.getElementById("dist-out").textContent = b.dataset.dist + " cm";
    setDistance(b.dataset.dist);
  });
});
vibSlider.addEventListener("input", function () {
  document.getElementById("vib-out").textContent = vibSlider.value + " edges";
});
document.getElementById("vib-inject").addEventListener("click", async function () {
  const r = await api("/api/sim/vibration/", "POST", { count: Number(vibSlider.value) });
  toast(r.ok ? "Injected " + vibSlider.value + " edges" : (r.data.error || "failed"), !r.ok);
});
document.querySelectorAll("[data-v]").forEach(function (b) {
  b.addEventListener("click", async function () {
    const radio = document.getElementById("radio").value;
    const r = await api("/api/sim/remote-alert/", "POST", { vehicle: b.dataset.v, script: b.dataset.s, radio: radio });
    toast(r.ok ? r.data.message : (r.data.error || "failed"), !r.ok);
  });
});

onState(function (s) {
  const simulated = s.simulation.sensors;
  document.getElementById("sensor-note").textContent = simulated
    ? "The distance sensor is simulated: the slider drives it."
    : "The distance sensor is real hardware: the simulation controls are disabled.";
  [distSlider, vibSlider, document.getElementById("vib-inject")].forEach(function (c) { c.disabled = !simulated; });
  document.querySelectorAll("[data-dist]").forEach(function (b) { b.disabled = !simulated; });
  document.getElementById("remote-note").textContent = s.mesh.simulated
    ? "The Pi's LoRa modem is simulated: scripted vehicles deliver straight into it."
    : "The Pi's LoRa modem is real: set SIM_V2_PORT / SIM_V3_PORT (or *_HOST) so the extra boards transmit over LoRa.";
  document.getElementById("s-dist").textContent = s.distance_cm === null ? "--" : s.distance_cm + " cm";
  document.getElementById("s-level").textContent = s.level;
  document.getElementById("s-buzzer").textContent = s.buzzer ? "on" : "off";
  document.getElementById("s-led").textContent = s.led ? "on" : "off";
  document.getElementById("s-impact").textContent = s.impact ? "YES" : "no";
});
