/* PMic — PC dashboard */
"use strict";

const $ = (id) => document.getElementById(id);
let lastStatus = null;

function fmtName(d) { return d.name.length > 46 ? d.name.slice(0, 44) + "…" : d.name; }

function renderStatus(s) {
  lastStatus = s;

  $("ver").textContent = s.version;
  $("uptime").textContent = `up ${Math.floor(s.uptime_s / 60)}m ${s.uptime_s % 60}s`;
  $("connectUrl").textContent = s.connect_url;
  $("pinDigits").textContent = s.pin_required ? s.pin : "not required";
  $("pinToggle").checked = s.pin_required;

  // QR
  const slot = $("qrSlot");
  if (s.qr_svg && !slot.dataset.done) {
    slot.innerHTML = s.qr_svg;
    slot.dataset.done = "1";
  }
  slot.style.visibility = slot.dataset.done ? "visible" : "hidden";

  // first-time https hint (self-signed cert on a home network)
  if (s.https_available && !sessionStorage.getItem("pmic-tls-hint")) {
    sessionStorage.setItem("pmic-tls-hint", "1");
    const hint = document.createElement("p");
    hint.className = "device-note";
    hint.style.margin = "8px 0 0";
    hint.textContent = "First visit from a phone: the browser shows a certificate warning \u2014 that's normal on a home network. Choose \u201cAdvanced \u2192 proceed\u201d (Android/Chrome) and the mic permission prompt will appear.";
    document.querySelector(".connect-strip").after(hint);
  }

  // feedback warning: phone mic + PC speakers = screech loop
  const fb = $("feedbackNote");
  const dev = s.devices.find((d) => d.id === s.device);
  if (b.active && dev && !dev.virtual) {
    fb.hidden = false;
    fb.textContent = "Screeching or ringing? Your PC speakers are looping back into the phone mic. Use headphones on the PC, lower the PC volume, or keep the phone away from the speakers.";
  } else {
    fb.hidden = true;
  }

  // live badge
  const b = s.bridge;
  const badge = $("liveBadge");
  badge.dataset.state = b.active ? (b.level > 0.02 ? "hot" : "live") : "off";
  badge.textContent = b.active ? (b.client_name || "On air") : "Offline";

  // signal column
  $("sClient").textContent = b.active ? (b.client_name || "connected") : "none";
  $("sBuffer").textContent = Math.round(b.buffer_ms) + " / " + b.target_buffer_ms + " ms"
    + (b.effective_buffer_ms > b.target_buffer_ms + 1 ? " (auto " + Math.round(b.effective_buffer_ms) + " ms)" : "");
  $("sLat").textContent = b.active ? `~${Math.round(b.est_latency_ms)} ms` : "—";
  $("sKbps").textContent = b.active ? `${Math.round(b.kbps)} kb/s` : "—";
  $("sUnderruns").textContent = b.underruns;
  $("sDrift").textContent = (b.drift_ppm > 0 ? "+" : "") + Math.round(b.drift_ppm || 0) + " ppm";
  const silent = b.active && (b.silent_s || 0) > 10;
  const note = $("feedbackNote");
  note.hidden = !silent;
  if (silent) note.textContent =
    `Playback has been silent for ${Math.round(b.silent_s)} s while the phone is connected — the audio output may be stuck. Try Dashboard → Output device, then reselect.`;
  drawLevel(b.level, b.active);

  // device list
  const sel = $("deviceSel");
  if (document.activeElement !== sel) {
    sel.innerHTML = "";
    s.devices.forEach((d) => {
      const o = document.createElement("option");
      o.value = d.id;
      o.textContent = fmtName(d) + (d.virtual ? "  —  virtual mic" : d.default ? "  —  default" : "");
      o.selected = s.device === d.id;
      sel.appendChild(o);
    });
  }

  // virtual mic guidance (install card / default-mic card, rebuilt on state change)
  const card = $("virtualNote").dataset.card || "none";
  if (!s.virtual_device_present && card !== "install") {
    $("virtualNote").innerHTML =
      "No virtual microphone found — without one your voice goes to an output device, " +
      "not into games. Install the bundled VB‑CABLE driver (offline, nothing to download): " +
      "<button id='installCableBtn' class='btn btn-primary btn-sm' style='margin-top:10px'>Install virtual mic</button>" +
      " <span id='installCableMsg' class='hint'></span>";
    const btn = document.getElementById("installCableBtn");
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      btn.textContent = "Installing…";
      await fetch("/api/install-cable", { method: "POST" });
      const poll = setInterval(async () => {
        const st = await (await fetch("/api/install-cable/status")).json();
        document.getElementById("installCableMsg").textContent = st.message || "";
        if (st.status === "launched") {
          clearInterval(poll);
          btn.textContent = "Install launched — finish it, then restart PMic";
          document.getElementById("installCableMsg").textContent =
            "Complete the installer window, then restart PMic (tray → Quit, run again).";
        } else if (st.status === "error") {
          clearInterval(poll);
          btn.disabled = false;
          btn.textContent = "Install virtual mic";
        }
      }, 700);
    });
    $("virtualNote").dataset.card = "install";
  } else if (s.virtual_device_present && card !== "default") {
    $("virtualNote").innerHTML =
      "Virtual microphone detected. Games that use the system default mic will find it automatically. " +
      "<button id='setDefaultMicBtn' class='btn btn-ghost btn-sm' style='margin-top:10px'>Set as system default mic</button>" +
      (s.default_mic_journaled
        ? " <button id='restoreMicBtn' class='btn btn-ghost btn-sm' style='margin-top:10px'>Restore my real mic</button>"
        : "") +
      " <button id='repairAudioBtn' class='btn btn-ghost btn-sm' style='margin-top:10px' " +
      "title='Rebuild the Windows audio routing table if sound stops working'>Repair audio</button>" +
      " <span id='setDefaultMicMsg' class='hint'></span>";
    document.getElementById("setDefaultMicBtn").addEventListener("click", async () => {
      const msg = document.getElementById("setDefaultMicMsg");
      msg.textContent = "Setting…";
      try {
        const r = await (await fetch("/api/set-default-mic", { method: "POST" })).json();
        if (r.mode === "direct") { msg.textContent = "Done — this PC's default mic is now PMic."; }
        else if (r.mode === "elevated") { msg.textContent = "Confirm the admin prompt that just appeared."; }
        else { msg.textContent = "Couldn't set it automatically — pick CABLE Output in Windows sound settings."; }
      } catch { msg.textContent = "Failed — pick CABLE Output in Windows sound settings."; }
    });
    const restoreBtn = document.getElementById("restoreMicBtn");
    if (restoreBtn) restoreBtn.addEventListener("click", async () => {
      const msg = document.getElementById("setDefaultMicMsg");
      msg.textContent = "Restoring…";
      try {
        const r = await (await fetch("/api/restore-default-mic", { method: "POST" })).json();
        msg.textContent = r.ok
          ? "Done — your previous mic is the system default again."
          : "Nothing to restore (PMic hadn't changed it).";
      } catch { msg.textContent = "Failed — change it in Windows sound settings."; }
    });
    $("virtualNote").dataset.card = "default";
    const repairBtn = document.getElementById("repairAudioBtn");
    if (repairBtn) repairBtn.addEventListener("click", async () => {
      const msg = document.getElementById("setDefaultMicMsg");
      msg.textContent = "Repairing audio…";
      try {
        const r = await (await fetch("/api/repair-audio", { method: "POST" })).json();
        msg.textContent = r.ok
          ? "Confirm the admin prompt, then wait ~5 seconds."
          : (r.message || "Couldn't repair automatically.");
      } catch { msg.textContent = "Couldn't repair automatically."; }
    });
  } else if (s.virtual_device_present === (card === "default") ||
             (!s.virtual_device_present && card === "install")) {
    // card already correct; nothing to do
  } else {
    $("virtualNote").textContent = "";
  }

  $("muteBtn").textContent = s.mute ? "Unmute output" : "Mute output";
  $("muteBtn").classList.toggle("is-active", s.mute);
  if (document.activeElement !== $("cleanupSel")) $("cleanupSel").value = s.cleanup;
  if (document.activeElement !== $("gateRange")) {
    $("gateRange").value = Math.round(s.gate_db);
    $("gateVal").textContent = `${Math.round(s.gate_db)} dB`;
  }
  if (document.activeElement !== $("gainRange")) {
    $("gainRange").value = Math.round(s.gain * 100);
    $("gainVal").textContent = `${Math.round(s.gain * 100)}%`;
  }
  if (document.activeElement !== $("bufferRange")) {
    $("bufferRange").value = b.target_buffer_ms;
    $("bufferVal").textContent = `${b.target_buffer_ms} ms`;
  }
}

function drawLevel(level, active) {
  const pct = Math.min(1, level * 6) * 100;
  const fill = $("pcLevel");
  fill.style.width = pct + "%";
  fill.style.background = pct > 92 ? "#E5484D" : pct > 70 ? "#F5B841" : "#3FB6E8";
  fill.style.opacity = active ? 1 : 0.25;
}

async function refresh() {
  try {
    const r = await fetch("/api/status");
    renderStatus(await r.json());
  } catch {}
}

/* ---------- actions ---------- */

$("copyLinkBtn").addEventListener("click", async () => {
  const url = lastStatus ? lastStatus.connect_url + "/?pin=" + lastStatus.pin : "";
  try { await navigator.clipboard.writeText(url); } catch {}
  $("copyLinkBtn").textContent = "Copied";
  setTimeout(() => ($("copyLinkBtn").textContent = "Copy link"), 1400);
});

$("deviceSel").addEventListener("change", async (e) => {
  const id = e.target.value === "" ? null : parseInt(e.target.value, 10);
  await fetch("/api/select", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id }),
  });
  refresh();
});

$("gainRange").addEventListener("input", async (e) => {
  const g = e.target.value / 100;
  $("gainVal").textContent = `${e.target.value}%`;
  await fetch("/api/gain", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ gain: g }),
  });
});

$("bufferRange").addEventListener("input", async (e) => {
  $("bufferVal").textContent = `${e.target.value} ms`;
  await fetch("/api/buffer", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ms: parseInt(e.target.value, 10) }),
  });
});

$("cleanupSel").addEventListener("change", async (e) => {
  await fetch("/api/cleanup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode: e.target.value }),
  });
  refresh();
});

$("gateRange").addEventListener("input", async (e) => {
  $("gateVal").textContent = `${e.target.value} dB`;
  await fetch("/api/cleanup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ gate_db: parseInt(e.target.value, 10) }),
  });
});

$("muteBtn").addEventListener("click", async () => {
  const mute = !(lastStatus && lastStatus.mute);
  await fetch("/api/mute", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mute }),
  });
  refresh();
});

$("disconnectBtn").addEventListener("click", async () => {
  await fetch("/api/disconnect", { method: "POST" });
  refresh();
});

$("pinToggle").addEventListener("change", async (e) => {
  await fetch("/api/pin", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ required: e.target.checked }),
  });
  refresh();
});

refresh();
setInterval(refresh, 500);
