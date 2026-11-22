/* PMic — client (phone) */
"use strict";

const $ = (id) => document.getElementById(id);

const state = {
  ws: null,
  ctx: null,
  node: null,
  micGain: null,
  monitor: null,
  stream: null,
  muted: false,
  rttMs: null,
  bytesSent: 0,
  statsTimer: null,
  pingTimer: null,
  wakeLock: null,
  pcLevel: 0,
  pcActive: false,
  unanswered: 0,
  touchHooked: false,
  reconnecting: false,
  noReconnect: false,
};

/* ---------- prefs ---------- */
function prefs() {
  try { return JSON.parse(localStorage.getItem("pmic") || "{}"); } catch { return {}; }
}
function savePrefs(p) { localStorage.setItem("pmic", JSON.stringify(p)); }

/* ---------- pairing ---------- */

const params = () => new URLSearchParams(location.search);

$("pairForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const pin = $("pinInput").value.replace(/\D/g, "");
  $("pairError").hidden = true;
  $("pairBtn").disabled = true;
  $("pairBtn").textContent = "Connecting…";
  try {
    await goLive(pin);
  } catch (err) {
    $("pairError").textContent = explain(err);
    $("pairError").hidden = false;
    $("pairBtn").disabled = false;
    $("pairBtn").textContent = "Connect";
  }
});

function explain(err) {
  if (err && err.pin === false) return "That code didn’t match. Check the dashboard on your PC and try again.";
  if (err && err.mic === false) return "This page needs microphone permission. Allow it in your browser settings, then retry.";
  if (err && err.server) return "Couldn’t reach your PC. Make sure both devices are on the same Wi‑Fi and PMic is running.";
  return "Something went wrong. Try again.";
}

async function goLive(pin) {
  // 1. microphone permission + capture
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        deviceId: prefs().micId ? { exact: prefs().micId } : undefined,
        echoCancellation: false,
        noiseSuppression: prefs().ns !== undefined ? prefs().ns : true,
        autoGainControl: false,
        channelCount: 1,
      },
    });
  } catch {
    throw Object.assign(new Error("mic denied"), { mic: false });
  }
  state.stream = stream;

  // 2. socket
  const wsUrl = new URL("/audio", location.href);
  wsUrl.protocol = wsUrl.protocol.replace("http", "ws");
  wsUrl.searchParams.set("pin", pin);
  let ws;
  try {
    ws = await openSocket(wsUrl.href);
  } catch (err) {
    stream.getTracks().forEach((t) => t.stop());
    state.stream = null;
    if (String(err).includes("403")) throw Object.assign(new Error("bad pin"), { pin: false });
    throw Object.assign(new Error("server"), { server: true });
  }
  state.ws = ws;
  state.noReconnect = false;

  // 3. audio graph: source → gain → worklet tap → monitor gain → destination
  const ctx = new AudioContext({ sampleRate: 48000, latencyHint: "interactive" });
  await ctx.audioWorklet.addModule("/static/tap-worklet.js");
  const src = ctx.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(ctx, "pcm-tap");
  const micGain = ctx.createGain();
  micGain.gain.value = $("gainRange").value / 100;
  const monitor = ctx.createGain();
  monitor.gain.value = 0;                        // monitoring off by default
  src.connect(micGain);
  micGain.connect(node);
  node.connect(monitor);
  monitor.connect(ctx.destination);

  node.port.onmessage = (e) => {
    const f32 = e.data;                          // Float32Array chunk
    sendPcm(f32);
    drawMeter(levelOf(f32));
  };

  state.ctx = ctx;
  state.node = node;
  state.micGain = micGain;
  state.monitor = monitor;

  wireSocket(ws);
  const p = prefs(); p.pin = pin; savePrefs(p);   // saved for silent reconnects
  enterLive();
  keepScreenAwake();
  if (!state.touchHooked) {
    state.touchHooked = true;
    document.body.addEventListener("touchstart", keepScreenAwake, { passive: true });
  }
}

function openSocket(url) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";
    const t = setTimeout(() => reject(new Error("timeout")), 6000);
    ws.onopen = () => { clearTimeout(t); resolve(ws); };
    ws.onerror = () => { clearTimeout(t); reject(new Error("ws error")); };
    ws.onclose = (e) => { clearTimeout(t); reject(new Error("closed " + e.code)); };
  });
}

function wireSocket(ws) {
  ws.addEventListener("message", (e) => {
    if (typeof e.data !== "string") return;
    let m; try { m = JSON.parse(e.data); } catch { return; }
    if (m.type === "pong") {
      state.rttMs = Math.round(performance.now() - m.t);
      state.unanswered = 0;
    } else if (m.type === "stats") {
      // proof, not assumption: what the PC actually hears right now
      state.pcLevel = m.level;
      state.pcActive = m.active;
      drawPcDot();
    } else if (m.type === "displaced") {
      state.noReconnect = true;          // another device owns the mic now
      teardown("This PC is now using another device as its mic.");
    }
  });
  ws.addEventListener("close", () => {
    if (state.ws === ws && !state.noReconnect) reconnectLoop();
  });

  // liveness watchdog: if pings go unanswered, the link is dead even if the
  // OS hasn't noticed yet — close it now so reconnect starts immediately
  clearInterval(state.pingTimer);
  state.unanswered = 0;
  state.pingTimer = setInterval(() => {
    if (ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ type: "ping", t: performance.now() }));
    state.unanswered = (state.unanswered || 0) + 1;
    if (state.unanswered >= 4) {        // ~8 s of silence = dead link
      state.unanswered = 0;
      try { ws.close(); } catch {}
    }
  }, 2000);
}

// auto-recovery: a suspended AudioContext and dropped wake locks come back
// on their own while we're live
setInterval(() => {
  if (state.ctx && state.ctx.state !== "running") state.ctx.resume().catch(() => {});
  if (!$("liveScreen").hidden) keepScreenAwake();
}, 4000);

// proof indicator: is the voice actually reaching the PC?
let silentSeconds = 0;
function drawPcDot() {
  const dot = $("pcDot");
  if (!dot) return;
  if (!state.pcActive) {
    dot.className = "";
    $("pcHearText").textContent = "not connected";
  } else if (state.pcLevel > 0.004) {
    dot.className = "ok";
    silentSeconds = 0;
    $("pcHearText").textContent = "PC hears you";
  } else {
    silentSeconds += 1;
    if (silentSeconds >= 4) {
      dot.className = "warn";
      $("pcHearText").textContent = "no sound reaching the PC — check the phone's mic or mute";
    } else {
      dot.className = "";
      $("pcHearText").textContent = "quiet";
    }
  }
}

async function reconnectLoop() {
  const pin = prefs().pin;
  if (!pin || state.reconnecting) return;
  state.reconnecting = true;
  showNotice("Connection hiccup — reconnecting…");
  // never give up while this page is open: the phone IS the mic
  for (let attempt = 0; !state.noReconnect; attempt++) {
    await new Promise((r) => setTimeout(r, Math.min(2500 + attempt * 500, 12000)));
    if (state.noReconnect) break;
    try {
      await goLive(pin);
      state.reconnecting = false;
      showNotice("Back on air.");
      return;
    } catch (err) {
      if (err && err.pin === false) {
        // the PC restarted with a fresh code: stop hammering a dead PIN and
        // go back to pairing so the new code can be entered
        const p = prefs(); delete p.pin; savePrefs(p);
        state.reconnecting = false;
        teardown("PC restarted — enter the new pairing code.");
        $("pairError").textContent =
          "Your PC is showing a new pairing code. Enter it above.";
        $("pairError").hidden = false;
        return;
      }
    }
    if (attempt === 5) showNotice("Still reconnecting — keep this page open.");
  }
  state.reconnecting = false;
}

async function keepScreenAwake() {
  try {
    if ("wakeLock" in navigator && !state.wakeLock) {
      state.wakeLock = await navigator.wakeLock.request("screen");
      state.wakeLock.addEventListener("release", () => { state.wakeLock = null; });
    }
  } catch {}
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && !$('liveScreen').hidden) {
    keepScreenAwake();
    if (state.ctx && state.ctx.state === "suspended") state.ctx.resume().catch(() => {});
  }
});

/* ---------- live screen ---------- */

function enterLive() {
  $("pairScreen").hidden = true;
  $("liveScreen").hidden = false;
  document.body.classList.add("on-air");
  $("nsToggle").checked = prefs().ns !== undefined ? prefs().ns : true;
  populateMicList();
  startStats();
}

function teardown(message) {
  const wasLive = !$("liveScreen").hidden;
  clearInterval(state.statsTimer);
  clearInterval(state.pingTimer);
  try { state.wakeLock && state.wakeLock.release(); } catch {}
  state.wakeLock = null;
  try { state.ws && state.ws.close(); } catch {}
  try { state.stream && state.stream.getTracks().forEach((t) => t.stop()); } catch {}
  try { state.ctx && state.ctx.close(); } catch {}
  state.ws = null; state.ctx = null; state.node = null; state.stream = null;
  if (wasLive) {
    showNotice(message);
    setTimeout(() => {
      $("liveScreen").hidden = true;
      $("pairScreen").hidden = false;
      $("pairBtn").disabled = false;
      $("pairBtn").textContent = "Connect";
      $("pinInput").value = "";
      document.body.classList.remove("on-air");
    }, 2600);
  }
}

function showNotice(msg) {
  const n = document.createElement("div");
  n.className = "notice";
  n.textContent = msg;
  document.body.appendChild(n);
  setTimeout(() => n.remove(), 2600);
}

/* ---------- meter ---------- */

function levelOf(f32) {
  let sum = 0;
  for (let i = 0; i < f32.length; i += 4) sum += f32[i] * f32[i];
  return Math.sqrt(sum / (f32.length / 4 || 1));
}

let meterAvg = 0;
function drawMeter(level) {
  meterAvg = meterAvg * 0.75 + level * 0.25;
  const pct = Math.min(1, meterAvg * 6);
  const ring = $("ringLevel");
  const c = 2 * Math.PI * 54;
  ring.style.strokeDasharray = `${pct * c} ${c}`;
  ring.style.opacity = state.muted ? 0.15 : 1;
  ring.style.stroke = pct > 0.92 ? "#E5484D" : pct > 0.7 ? "#F5B841" : "#3FB6E8";
  $("meterRing").classList.toggle("pulsing", pct > 0.25 && !state.muted);
}

/* ---------- pcm plumbing ---------- */

function wsOpen() { return state.ws && state.ws.readyState === WebSocket.OPEN; }

let sendBuf = null, sendLen = 0;
const FRAME_SAMPLES = 1920; // ~40 ms @ 48 kHz mono

function sendPcm(f32) {
  if (!wsOpen()) { sendLen = 0; return; }
  // long-session hygiene: never queue stale audio behind a slow network
  if (state.ws.bufferedAmount > 262144) { sendLen = 0; return; }
  for (let i = 0; i < f32.length; i++) {
    if (sendLen === 0) sendBuf = new Int16Array(FRAME_SAMPLES);
    let s = Math.max(-1, Math.min(1, f32[i]));
    sendBuf[sendLen++] = s < 0 ? s * 32768 : s * 32767;
    if (sendLen === FRAME_SAMPLES) {
      state.ws.send(sendBuf.buffer);
      state.bytesSent += FRAME_SAMPLES * 2;
      sendLen = 0;
    }
  }
}

/* ---------- stats ---------- */

function startStats() {
  clearInterval(state.statsTimer);
  state.statsTimer = setInterval(() => {
    if ($("liveScreen").hidden) return;
    $("statLat").textContent = state.rttMs != null ? state.rttMs : "—";
    $("statRate").textContent = Math.round((state.bytesSent * 8) / 1000);
  }, 1000);
}

/* ---------- controls ---------- */

$("stopBtn").addEventListener("click", () => {
  state.noReconnect = true;
  try { state.ws && state.ws.close(); } catch {}
  teardown("Stopped. You’re off air.");
});

$("muteBtn").addEventListener("click", () => {
  state.muted = !state.muted;
  $("muteBtn").textContent = state.muted ? "Unmute" : "Mute";
  $("muteBtn").classList.toggle("is-active", state.muted);
});

$("gainRange").addEventListener("input", (e) => {
  if (state.micGain) state.micGain.gain.value = e.target.value / 100;
});

$("monitorToggle").addEventListener("change", (e) => {
  if (state.monitor) state.monitor.gain.value = e.target.checked ? 0.8 : 0;
});

$("nsToggle").addEventListener("change", (e) => {
  const p = prefs(); p.ns = e.target.checked; savePrefs(p);
  if (state.ctx) location.reload();               // reopens capture with new constraints
});

async function populateMicList() {
  try {
    const devs = await navigator.mediaDevices.enumerateDevices();
    const mics = devs.filter((d) => d.kind === "audioinput");
    const sel = $("micSel");
    sel.innerHTML = "";
    mics.forEach((d, i) => {
      const o = document.createElement("option");
      o.value = d.deviceId;
      o.textContent = d.label || `Microphone ${i + 1}`;
      if (prefs().micId === d.deviceId) o.selected = true;
      sel.appendChild(o);
    });
    sel.onchange = () => {
      const p = prefs(); p.micId = sel.value; savePrefs(p);
      if (wsOpen()) { try { state.ws.close(); } catch {} }   // reconnect path shows notice
    };
  } catch {}
}

/* ---------- boot ---------- */

if (params().get("pin")) {                        // deep link from dashboard QR
  $("pinInput").value = params().get("pin");
  $("pairForm").requestSubmit();
}
