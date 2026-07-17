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
	// green = running, red = disconnected, amber = unauthorized/reconnecting, gray = idle
	const color = s === "monitoring" ? "#22c55e"
		: s === "disconnected" ? "#ef4444"
		: (s === "unauthorized" || s === "reconnecting") ? "#f59e0b" : "#4a5365";
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
		const selectedSet = sets.find((s) => s.id === d.set);
		const selectedSetName = selectedSet ? (selectedSet.name || "Untitled") : "Set";
		const setOptions = [
			`<button class="set-option ${!d.set ? "active" : ""}" data-set-option="">— none —</button>`,
			...sets.map((s) =>
				`<button class="set-option ${d.set === s.id ? "active" : ""}" data-set-option="${esc(s.id)}">${esc(s.name || "Untitled")}</button>`
			)
		].join("");
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
	        ${d.connection_message
				? `<span class="warn connection-warning">⚠ ${esc(d.connection_message)}</span>`
				: (d.state && d.state !== "device" ? `<span class="warn">⚠ ${esc(d.state)}</span>` : "")}
	      </div>
	      <div class="actions">
	        ${running
					? `<button class="btn red sm" data-act="stop">Stop</button>`
					: `<button class="btn green sm" data-act="start">Start</button>`}
	        <div class="set-picker">
	          <button class="set-trigger" type="button">
	            <span>${esc(selectedSetName)}</span>
	            <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 7.5l5 5 5-5"/></svg>
	          </button>
	          <div class="set-menu hidden">${setOptions}</div>
	        </div>
	        <button class="btn ghost sm" data-act="set" title="Credentials / settings">⚙</button>
	        <button class="btn ghost sm" data-act="disconnect">Disconnect</button>
	      </div>
    </div>`;
	}).join("");

	grid.querySelectorAll(".devcard").forEach((card) => {
		const serial = card.dataset.serial;
		const picker = card.querySelector(".set-picker");
		if (picker) {
			const trigger = picker.querySelector(".set-trigger");
			const menu = picker.querySelector(".set-menu");
			trigger.onclick = (event) => {
				event.stopPropagation();
				closeSetMenus(menu);
				menu.classList.toggle("hidden");
			};
			menu.querySelectorAll("[data-set-option]").forEach((option) => {
				option.onclick = async (event) => {
					event.stopPropagation();
					await post(`/api/devices/${encodeURIComponent(serial)}/set`, { set_id: option.dataset.setOption });
					menu.classList.add("hidden");
					await loadDevices();
				};
			});
		}
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

function closeSetMenus(except) {
	$$(".set-menu").forEach((menu) => {
		if (menu !== except) menu.classList.add("hidden");
	});
}
document.addEventListener("click", () => closeSetMenus());

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
// Prefer the bank message's own date/time; fall back to when we synced it.
function txTime(t) {
	return t.time || (t.synced_at ? fmtTime(t.synced_at) : "");
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
        <td class="mono">${txTime(t)}</td>
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

// ---------- settings: delivery sets ----------
let sets = [];
let activeSetId = "sync";          // set id, "new", "logs", or "sync"
let defaultApi = "https://paymentgateway.108pay.co";
let logRetention = "7_days";
const logRetentionDays = {
	"7_days": 7, "15_days": 15, "1_month": 30,
	"2_months": 60, "5_months": 150, "1_year": 365,
};

async function loadSettings() {
	const s = await api("/api/settings");
	defaultApi = s.default_api_url || defaultApi;
	logRetention = s.log_retention || "7_days";
	$("#log-retention").value = logRetention;
	$("#sync-token").placeholder = s.ngrok_token_set ? "•••••• (saved)" : "ngrok auth token";
	await loadSets();
}

async function loadSets() {
	sets = await api("/api/sets");
	if (!["sync", "logs", "new"].includes(activeSetId) && !sets.find((x) => x.id === activeSetId))
		activeSetId = sets.length ? sets[0].id : "new";
	renderSetTabs();
	renderSetEditor();
	renderDevices();   // device dropdowns depend on the set list
}

function renderSetTabs() {
	const bar = $("#settabs");
	let html = sets.map((s) =>
		`<button class="settab ${activeSetId === s.id ? "active" : ""}" data-set="${s.id}">${esc(s.name || "Untitled")}</button>`
	).join("");
	html += `<button class="settab add ${activeSetId === "new" ? "active" : ""}" data-set="new">＋ Add set</button>`;
	html += `<button class="settab ${activeSetId === "logs" ? "active" : ""}" data-set="logs" style="margin-left:auto">Logs</button>`;
	html += `<button class="settab ${activeSetId === "sync" ? "active" : ""}" data-set="sync">Sync</button>`;
	bar.innerHTML = html;
	bar.querySelectorAll(".settab").forEach((b) => {
		b.onclick = () => { activeSetId = b.dataset.set; renderSetTabs(); renderSetEditor(); };
	});
}

function renderSetEditor() {
	const isSync = activeSetId === "sync";
	const isLogs = activeSetId === "logs";
	$("#set-editor").classList.toggle("hidden", isSync || isLogs);
	$("#sync-editor").classList.toggle("hidden", !isSync);
	$("#logs-editor").classList.toggle("hidden", !isLogs);
	if (isSync) { refreshSync(); return; }
	if (isLogs) { $("#log-retention").value = logRetention; return; }
	const s = sets.find((x) => x.id === activeSetId);
	const isNew = !s;
	const setType = s ? (s.type || "gateway") : "gateway";
	const isCustom = setType === "custom";
	$("#set-editor-title").textContent = isNew
		? (isCustom ? "New custom set" : "New gateway set")
		: (s.name || (isCustom ? "Custom set" : "Gateway set"));
	$("#set-description").textContent = isCustom
		? "Forward transactions directly to your callback with the configured API-key header."
		: "A named gateway profile. Assign devices to it on the Devices page.";
	$("#set-name").value = isNew ? "" : (s.name || "");
	$("#gateway-set-fields").classList.toggle("hidden", isCustom);
	$("#custom-set-fields").classList.toggle("hidden", !isCustom);
	$("#set-client").value = isNew ? "" : (s.client_id || "");
	$("#set-secret").value = "";
	$("#set-secret").placeholder = (!isNew && s.has_secret) ? "•••••• (saved — blank keeps it)" : "secret key";
	$("#set-header").value = isNew ? "" : (s.header || "");
	$("#set-api-key").value = "";
	$("#set-api-key").placeholder = (!isNew && s.has_secret) ? "•••••• (saved — blank keeps it)" : "API key";
	$("#set-callback").value = isNew ? "" : (s.callback_url || "");
	$("#set-delete").style.display = isNew ? "none" : "";
	$("#set-register").classList.toggle("hidden", isCustom);
	$("#set-status").textContent = "";
}

$("#log-retention").onchange = async (event) => {
	const value = event.target.value;
	$("#log-retention-status").textContent = "Saving…";
	const result = await post("/api/settings/log-retention", { value });
	logRetention = result.log_retention || "7_days";
	event.target.value = logRetention;
	pruneActivityLog();
	await loadTransactions();
	const removed = Number(result.removed) || 0;
	$("#log-retention-status").textContent = removed
		? `Saved — removed ${removed} expired log${removed === 1 ? "" : "s"}`
		: "Saved ✓";
	setTimeout(() => ($("#log-retention-status").textContent = ""), 5000);
};

$("#set-save").onclick = async () => {
	$("#set-status").textContent = "Saving…";
	const existing = sets.find((x) => x.id === activeSetId);
	const isNew = !existing;
	const setType = existing ? (existing.type || "gateway") : "gateway";
	const body = {
		id: isNew ? "" : activeSetId,
		type: setType,
		name: $("#set-name").value.trim(),
	};
	if (setType === "custom") {
		body.header = $("#set-header").value.trim();
		body.secret_key = $("#set-api-key").value.trim();
		body.callback_url = $("#set-callback").value.trim();
	} else {
		body.client_id = $("#set-client").value.trim();
		body.secret_key = $("#set-secret").value.trim();
	}
	const r = await post("/api/sets", body);
	if (!r.ok) {
		$("#set-status").textContent = "⚠ " + (r.message || "Could not save set");
		return;
	}
	if (r.id) activeSetId = r.id;
	if (setType === "custom") {
		await loadSets();
		$("#set-status").textContent = "Saved ✓";
		setTimeout(() => ($("#set-status").textContent = ""), 5000);
		return;
	}
	// verify the credentials against the gateway (POST /bcel/setup)
	$("#set-status").textContent = "Saved — verifying credentials…";
	const v = await post(`/api/sets/${activeSetId}/webhook`);
	await loadSets();   // refresh list (renderSetEditor clears the status line)
	$("#set-status").textContent = v.ok
		? "Saved ✓ — credentials verified"
		: "Saved, but " + (v.message || "verification failed");
	setTimeout(() => ($("#set-status").textContent = ""), 8000);
};

$("#set-delete").onclick = async () => {
	if (!sets.find((x) => x.id === activeSetId)) return;
	await post(`/api/sets/${activeSetId}/delete`);
	activeSetId = null;
	await loadSets();
};

$("#set-register").onclick = async () => {
	if (!sets.find((x) => x.id === activeSetId)) { $("#set-status").textContent = "Save the set first"; return; }
	$("#set-status").textContent = "Registering webhook…";
	const r = await post(`/api/sets/${activeSetId}/webhook`);
	$("#set-status").textContent = r.ok ? "Webhook registered ✓" : "⚠ " + (r.message || "failed");
	setTimeout(() => ($("#set-status").textContent = ""), 6000);
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
	// the plaintext password is never sent to the UI (it'd leak over Sync); show
	// whether one is saved and keep it unless a new value is typed.
	$("#m-pass").placeholder = d.has_creds ? "•••••• (saved — blank keeps it)" : "device password";
	$("#m-ref").value = d.last_ref || "";
	$("#modal").classList.remove("hidden");
}
$("#m-cancel").onclick = () => $("#modal").classList.add("hidden");
$("#m-save").onclick = async () => {
	const body = {
		username: $("#m-user").value.trim(),
		last_ref: $("#m-ref").value.trim(),
	};
	// only send the password if the user actually typed one, so saving other
	// fields can't wipe the stored password
	const pass = $("#m-pass").value;
	if (pass) body.password = pass;
	await post(`/api/devices/${encodeURIComponent(modalSerial)}/creds`, body);
	$("#modal").classList.add("hidden");
	loadDevices();
};

// ---------- activity log dock ----------
let activityLog = [];
$("#logdock-toggle").onclick = () => {
	$("#log").classList.toggle("collapsed");
	$("#log-hint").textContent = $("#log").classList.contains("collapsed") ? "▴" : "▾";
};
function renderActivityLog() {
	const el = $("#log");
	el.textContent = activityLog.map((entry) => entry.msg).join("\n") + (activityLog.length ? "\n" : "");
	el.scrollTop = el.scrollHeight;
}
function pruneActivityLog() {
	const days = logRetentionDays[logRetention];
	if (days !== null && days !== undefined) {
		const cutoff = Date.now() / 1000 - days * 86400;
		activityLog = activityLog.filter((entry) => entry.ts >= cutoff);
	}
	renderActivityLog();
}
function logLine(msg, ts = Date.now() / 1000) {
	activityLog.push({ msg, ts });
	pruneActivityLog();
}

// ---------- live event stream (SSE) ----------
function connectStream() {
	const es = new EventSource(API + "/api/stream");
	es.onopen = () => setBackend(true);
	es.onerror = () => { setBackend(false); };
	es.onmessage = (e) => {
		const evt = JSON.parse(e.data);
		if (evt.kind === "log") logLine(evt.data.msg, evt.ts);
		else if (evt.kind === "device") loadDevices();
		else if (evt.kind === "transaction") { txns.push(evt.data); renderTransactions(); logLine(`📩 synced ${evt.data.type} ${evt.data.amount}`, evt.ts); }
		else if (evt.kind === "transactions_pruned") loadTransactions();
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
		await loadSets();        // populate device-card set dropdowns up front
		await loadDevices();
		setBackend(true);
	} catch (e) {
		setBackend(false);
	}
	loadVersion();
	connectStream();
	// light polling as a fallback for device hot-plug
	setInterval(loadDevices, 5000);
	setInterval(pruneActivityLog, 60000);
}
boot();
