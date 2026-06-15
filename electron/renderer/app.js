const API = "http://127.0.0.1:8000";
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

async function api(path, opts) {
  const r = await fetch(API + path, opts);
  return r.json();
}
function post(path, body) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
}

// ---------- view switching ----------
const titles = { devices: "Devices", transactions: "Transactions", settings: "Settings", guide: "Guide" };
function showView(name) {
  $$(".nav").forEach((n) => n.classList.toggle("active", n.dataset.view === name));
  $$(".view").forEach((v) => v.classList.add("hidden"));
  $("#view-" + name).classList.remove("hidden");
  $("#view-title").textContent = titles[name];
  if (name === "transactions") loadTransactions();
  if (name === "settings") loadSettings();
  if (name === "guide") loadGuide();
}
$$(".nav").forEach((n) => n.addEventListener("click", () => showView(n.dataset.view)));

// ---------- devices ----------
let devices = [];
function statusDot(s) {
  // green = running, red = disconnected, gray = stopped/idle
  const color = s === "monitoring" ? "#22c55e" : s === "disconnected" ? "#ef4444" : "#4a5365";
  const glow = s === "monitoring" ? "box-shadow:0 0 8px #22c55e;" : "";
  return `<span class="sdot" style="background:${color};${glow}" title="${s}"></span>`;
}
function renderDevices() {
  const grid = $("#devices-grid");
  $("#devices-empty").classList.toggle("hidden", devices.length > 0);
  const monitoring = devices.filter((d) => d.monitoring).length;
  $("#counter").textContent = `${monitoring} monitoring`;
  grid.innerHTML = devices.map((d) => {
    const running = d.monitoring;
    return `
    <div class="devcard" data-serial="${d.serial}">
      <div class="dhead">
        <div>
          <div class="name">${d.model}</div>
          <div class="serial mono">${d.serial}</div>
        </div>
        ${statusDot(d.status)}
      </div>
      <div class="meta">
        <span class="${d.has_creds ? "ok" : ""}">${d.has_creds ? "● credentials set" : "○ no credentials"}</span>
      </div>
      <div class="actions">
        ${running
          ? `<button class="btn red sm" data-act="stop">Stop</button>`
          : `<button class="btn green sm" data-act="start">Start</button>`}
        <button class="btn ghost sm" data-act="set" title="Credentials / settings">⚙</button>
        <button class="btn ghost sm" data-act="mirror">Mirror</button>
        <button class="btn ghost sm" data-act="disconnect">Disconnect</button>
      </div>
    </div>`;
  }).join("");

  grid.querySelectorAll(".devcard").forEach((card) => {
    const serial = card.dataset.serial;
    card.querySelectorAll("[data-act]").forEach((b) => {
      b.onclick = () => {
        const act = b.dataset.act;
        if (act === "start") post(`/api/devices/${encodeURIComponent(serial)}/start`);
        else if (act === "stop") post(`/api/devices/${encodeURIComponent(serial)}/stop`);
        else if (act === "set") openModal(serial);
        else if (act === "mirror") post(`/api/devices/${encodeURIComponent(serial)}/mirror`);
        else if (act === "disconnect") post(`/api/devices/${encodeURIComponent(serial)}/disconnect`).then(loadDevices);
      };
    });
  });
}
async function loadDevices() {
  devices = await api("/api/devices");
  renderDevices();
}

// ---------- transactions ----------
let txns = [];
let txQuery = "";
function fmtTime(ts) {
  return new Date(ts * 1000).toLocaleString();
}
function renderTransactions() {
  const q = txQuery.toLowerCase();
  const rows = q ? txns.filter((t) => JSON.stringify(t).toLowerCase().includes(q)) : txns;
  $("#tx-empty").classList.toggle("hidden", rows.length > 0);
  $("#tx-empty").textContent = txns.length && !rows.length
    ? "No transactions match your search." : "No synced transactions yet.";
  $("#tx-body").innerHTML = rows
    .slice().reverse()
    .map((t) => `
      <tr>
        <td class="mono">${fmtTime(t.synced_at)}</td>
        <td class="mono">${t.serial}</td>
        <td>${t.kind || ""}</td>
        <td>${t.type || ""}</td>
        <td class="mono">${t.from_account || ""}${t.from_name ? `<div class="muted">${t.from_name}</div>` : ""}</td>
        <td class="mono">${t.to_account || ""}</td>
        <td class="tx-amount">${t.amount || ""}</td>
        <td>${t.details || ""}</td>
        <td class="mono">${t.ref || ""}</td>
      </tr>`).join("");
}
async function loadTransactions() {
  txns = await api("/api/transactions");
  renderTransactions();
}
$("#tx-search").oninput = (e) => { txQuery = e.target.value.trim(); renderTransactions(); };
$("#tx-clear").onclick = async () => {
  await post("/api/transactions/clear");
  txns = [];
  renderTransactions();
};

// ---------- settings ----------
async function loadSettings() {
  const s = await api("/api/settings");
  $("#set-client").value = s.client_id || "";
  $("#set-secret").value = "";
  $("#set-secret").placeholder = s.has_secret ? "•••••• (saved — blank keeps it)" : "secret key";
  $("#sync-token").placeholder = s.ngrok_token_set ? "•••••• (saved)" : "ngrok auth token";
  refreshSync();
}
$("#set-save").onclick = async () => {
  $("#set-status").textContent = "Saving & registering…";
  const r = await post("/api/settings", {
    client_id: $("#set-client").value.trim(),
    secret_key: $("#set-secret").value.trim(),
  });
  const s = r.setup || {};
  // mirrors the Python app's "Save & Setup": save creds + call /bcel/setup
  $("#set-status").textContent = s.ok
    ? "Saved ✓ — webhook registered"
    : "Saved ✓ — " + (s.message || "setup not run");
  setTimeout(() => ($("#set-status").textContent = ""), 5000);
  loadSettings();
};

// ---------- sync (ngrok) ----------
async function refreshSync() {
  const s = await api("/api/sync/status");
  $("#sync-dot").className = "dot " + (s.running ? "on" : "off");
  $("#sync-url").value = s.url || "";
  $("#sync-url").placeholder = s.running ? "" : "(not running)";
}
$("#sync-start").onclick = async () => {
  $("#sync-url").value = "starting…";
  const r = await post("/api/sync/start", { token: $("#sync-token").value.trim() });
  if (r.ok) $("#sync-url").value = r.url || "";
  else { $("#sync-url").value = ""; logLine("⚠ Sync: " + (r.error || "failed")); }
  refreshSync();
};
$("#sync-stop").onclick = async () => { await post("/api/sync/stop"); refreshSync(); };

// ---------- QR pairing ----------
$("#pair-btn").onclick = async () => {
  $("#qr-status").textContent = "Generating QR…";
  $("#qr-img").src = "";
  $("#qr-modal").classList.remove("hidden");
  const r = await post("/api/pair/qr");
  $("#qr-img").src = r.qr_png || "";
  $("#qr-status").textContent = "Waiting for scan…";
};
$("#qr-close").onclick = () => $("#qr-modal").classList.add("hidden");

// ---------- guide ----------
let guideLoaded = false;
async function loadGuide() {
  if (guideLoaded) return;
  const { content } = await api("/api/guide");
  $("#guide-content").innerHTML = mdToHtml(content || "");
  guideLoaded = true;
}
function esc(s) { return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
function mdToHtml(md) {
  return md.split("\n").map((line) => {
    if (line.startsWith("# ")) return `<h1>${esc(line.slice(2))}</h1>`;
    if (line.startsWith("## ")) return `<h2>${esc(line.slice(3))}</h2>`;
    if (line.startsWith("### ")) return `<h2>${esc(line.slice(4))}</h2>`;
    if (line.trim() === "---") return "<hr/>";
    if (line.trim() === "") return "";
    let h = esc(line).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/`(.+?)`/g, "<code>$1</code>");
    return `<p>${h}</p>`;
  }).join("");
}

// ---------- credentials modal ----------
let modalSerial = null;
function openModal(serial) {
  modalSerial = serial;
  const d = devices.find((x) => x.serial === serial) || {};
  $("#modal-serial").textContent = `${d.model || ""}  ·  ${serial}`;
  $("#m-user").value = d.username || "";
  $("#m-pass").value = "";
  $("#m-ref").value = d.last_ref || "";
  $("#modal").classList.remove("hidden");
}
$("#m-cancel").onclick = () => $("#modal").classList.add("hidden");
$("#m-save").onclick = async () => {
  await post(`/api/devices/${encodeURIComponent(modalSerial)}/creds`, {
    username: $("#m-user").value.trim(),
    password: $("#m-pass").value,
    last_ref: $("#m-ref").value.trim(),
  });
  $("#modal").classList.add("hidden");
  loadDevices();
};

// ---------- activity log dock ----------
$("#logdock-toggle").onclick = () => {
  $("#log").classList.toggle("collapsed");
  $("#log-hint").textContent = $("#log").classList.contains("collapsed") ? "▴" : "▾";
};
function logLine(msg) {
  const el = $("#log");
  el.textContent += msg + "\n";
  el.scrollTop = el.scrollHeight;
}

// ---------- live event stream (SSE) ----------
function connectStream() {
  const es = new EventSource(API + "/api/stream");
  es.onopen = () => setBackend(true);
  es.onerror = () => { setBackend(false); };
  es.onmessage = (e) => {
    const evt = JSON.parse(e.data);
    if (evt.kind === "log") logLine(evt.data.msg);
    else if (evt.kind === "device") loadDevices();
    else if (evt.kind === "transaction") { txns.push(evt.data); renderTransactions(); logLine(`📩 synced ${evt.data.type} ${evt.data.amount}`); }
    else if (evt.kind === "pair") {
      $("#qr-status").textContent = evt.data.status || "";
      if (evt.data.done) { setTimeout(() => $("#qr-modal").classList.add("hidden"), 1500); loadDevices(); }
    }
  };
}
function setBackend(up) {
  $("#backend-dot").className = "dot " + (up ? "on" : "off");
  $("#backend-label").textContent = up ? "connected" : "offline";
}

async function loadVersion() {
  try {
    const r = await api("/api/health");
    if (r && r.version) $("#app-version").textContent = "v" + r.version;
  } catch (e) { /* leave placeholder */ }
}

// ---------- boot ----------
async function boot() {
  try {
    await loadDevices();
    setBackend(true);
  } catch (e) {
    setBackend(false);
  }
  loadVersion();
  connectStream();
  // light polling as a fallback for device hot-plug
  setInterval(loadDevices, 5000);
}
boot();
