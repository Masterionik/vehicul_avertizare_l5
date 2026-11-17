"use strict";
function setInd(id, on) { document.getElementById(id).classList.toggle("on", !!on); }

function radioLine(link) {
  const st = link.stats || {};
  return "TX " + (st.tx || 0) + "  RX " + (st.rx_ok || 0) +
    "  bad signature " + (st.rx_bad_signature || 0) + "  replay " + (st.rx_replay || 0);
}

onState(function (s) {
  document.getElementById("distance").textContent = s.distance_cm === null ? "--" : s.distance_cm;
  document.getElementById("fault").hidden = !s.sensor_fault;
  document.querySelectorAll("#levels span").forEach(function (item) {
    item.classList.toggle("active", Number(item.dataset.level) === s.level);
  });
  setInd("ind-buzzer", s.buzzer);
  setInd("ind-led", s.led);
  setInd("ind-impact", s.impact);

  [["mesh", s.mesh], ["zigbee", s.zigbee]].forEach(function (pair) {
    const state = document.getElementById(pair[0] + "-state");
    state.textContent = pair[1].connected ? "connected" : "offline";
    state.className = "badge " + (pair[1].connected ? "badge-ok" : "badge-bad");
    document.getElementById(pair[0] + "-sim").hidden = !pair[1].simulated;
    document.getElementById(pair[0] + "-stats").textContent = radioLine(pair[1]);
  });

  const remote = document.getElementById("remote");
  const r = s.last_remote_alert;
  remote.textContent = r ? ("node " + r.node + " via " + r.radio + ": " + (r.kind === 2 ? "impact" : "proximity") +
    ", level " + r.level + ", " + r.distance_cm + " cm, " + r.age_s + " s ago") : "None received";
  remote.classList.toggle("alert", s.remote_level > 0);

  const list = document.getElementById("events");
  list.replaceChildren();
  s.events.slice().reverse().forEach(function (e) {
    list.appendChild(el("li", new Date(e.time * 1000).toLocaleTimeString() + "  " + e.text));
  });
});
