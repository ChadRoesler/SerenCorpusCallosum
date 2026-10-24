// ── SerenCorpusCallosum viewer - leaf logic ──────────────────────────────────
// Snaps onto the SerenMeninges shell. The shell ALREADY provides api(),
// escapeHtml(), showTab(), and the bearer-token modal - this file CALLS them,
// it never redefines them. (The monolith shipped its own api()/escapeHtml() and
// wired a header that no longer exists; the shell owns the chrome now.)
//
// Header note: #storesBadge (the "fanning: x/y" pill) and the ⟳ refresh button
// live in header_aside.html, snapped into the shell's head_aside stud.
// loadStores()/boot() write the pill; the ⟳ button calls reload().

let storeTypeMap = {};   // name -> type, for hemisphere coloring

function showErr(msg) { document.getElementById("globalErr").innerHTML = `<div class="err">${escapeHtml(msg)}</div>`; }
function clearErr() { document.getElementById("globalErr").innerHTML = ""; }

// hemisphere color by store type (memory = coral / right, loci = cyan / left)
function storeClass(name) {
    const t = storeTypeMap[name] || "";
    if (t === "seren_memory") return "coral";
    if (t === "seren_loci") return "cyan";
    return "violet";
}

// ---- search -----------------------------------------------------------------
async function runSearch() {
    const q = document.getElementById("searchQ").value.trim();
    if (!q) return;
    const body = { query: q, n_results: parseInt(document.getElementById("nRes").value || "10", 10) };
    const el = document.getElementById("searchStack");
    el.innerHTML = `<div class="empty">Fanning…</div>`;
    document.getElementById("served").textContent = "";
    try {
        const data = await api("/search", { method: "POST", body: JSON.stringify(body) });
        const searched = (data.stores_searched || []).join(", ") || "none";
        let served = `fanned: ${searched}  ·  ${data.hits.length} hit${data.hits.length === 1 ? "" : "s"}`;
        document.getElementById("served").innerHTML = escapeHtml(served) +
            ((data.failed && data.failed.length)
                ? `  ·  <span class="skip" style="color:var(--bad,#e5534b)">did not answer: ${data.failed.map(s => escapeHtml(s.name + " (" + s.reason + ")")).join(", ")}</span>`
                : "") +
            ((data.skipped && data.skipped.length)
                ? `  ·  <span class="skip">skipped: ${data.skipped.map(s => escapeHtml(s.name + " (" + s.reason + ")")).join(", ")}</span>`
                : "");
        if (!data.hits.length) { el.innerHTML = `<div class="empty">No hits across the fanned stores. Try different words, or check the Stores tab.</div>`; return; }
        el.innerHTML = data.hits.map(hitRow).join("");
    } catch (e) { el.innerHTML = ""; showErr(e.message); }
}
function hitRow(h) {
    const rrf = (h.score ?? 0).toFixed(4);
    const rel = (h.base_relevance != null) ? `rel ${(h.base_relevance).toFixed(2)}` : "";
    const nat = (h.native_score != null) ? `native ${h.native_score}` : "";
    const dist = (h.raw_distance != null) ? `d=${h.raw_distance}` : "";
    const foot = [rel, nat, dist].filter(Boolean).map(x => `<span>${escapeHtml(x)}</span>`).join("");
    const m = h.metadata || {};
    const src = m.source || "";
    let edge = "";
    if (src === "satellite-edge") edge = `<span class="skip" title="a supporting episode of a core in the packet">satellite of ${escapeHtml(String(m.edge_of || "").slice(0, 8))}</span>`;
    else if (src === "supersedes-edge") edge = `<span class="skip" title="the core this packet's core replaced">superseded by ${escapeHtml(String(m.edge_of || "").slice(0, 8))}</span>`;
    else if (src === "topic-edge") edge = `<span class="skip" title="shares a topic tag with the packet">topic edge</span>`;
    else if (m.hop) edge = `<span class="skip" title="reached by a second retrieval round">hop ${escapeHtml(m.hop)}</span>`;
    const sur = m.surroundings;
    const story = (sur && (sur.satellites || sur.supersedes))
        ? `<span title="this core has a story behind it">${sur.satellites ? `${sur.satellites} satellite${sur.satellites === 1 ? "" : "s"}` : ""}${sur.satellites && sur.supersedes ? ", " : ""}${sur.supersedes ? "supersedes one" : ""}</span>` : "";
    return `<div class="card"><div class="hit">
        <div class="score">
          <span class="rank"><span class="h">${edge ? "" : "#"}</span>${edge ? "" : h.store_rank}</span>
          <span class="badge ${storeClass(h.store)}">${escapeHtml(h.store)}</span>
        </div>
        <div>
          <div class="v">${escapeHtml(h.content)}</div>
          <div class="foot">
            ${edge || `<span>rrf ${rrf}</span>`}
            ${foot}
            ${story}
            <span class="id">${escapeHtml(h.id)}</span>
          </div>
        </div>
      </div></div>`;
}

// ---- stores -----------------------------------------------------------------
async function loadStores() {
    try {
        const data = await api("/stores");
        storeTypeMap = {};
        (data.stores || []).forEach(s => storeTypeMap[s.name] = s.type);
        renderStores(data);
        const sel = document.getElementById("addType");
        if (sel && data.types) sel.innerHTML = data.types.map(t => `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`).join("");
        document.getElementById("storesBadge").textContent =
            `fanning: ${data.active}/${(data.stores || []).length}`;
    } catch (e) { showErr(e.message); }
}
async function addStore() {
    const name = document.getElementById("addName").value.trim();
    const type = document.getElementById("addType").value;
    const url = document.getElementById("addUrl").value.trim();
    const weight = parseFloat(document.getElementById("addWeight").value || "1.0");
    const floor = parseFloat(document.getElementById("addFloor").value || "0.0");
    const token = document.getElementById("addToken").value.trim();
    if (!name || !url) { showErr("name and url are required"); return; }
    const payload = { name, type, url, weight, floor };
    if (token) payload.token = token;   // omit when blank -> the store is open
    try {
        await api("/stores", { method: "POST", body: JSON.stringify(payload) });
        document.getElementById("addForm").style.display = "none";
        document.getElementById("addName").value = "";
        document.getElementById("addUrl").value = "";
        document.getElementById("addToken").value = "";   // never leave the secret in the field
        clearErr(); loadStores();
    } catch (e) { showErr(e.message); }
}
async function removeStore(name) {
    if (!confirm(`Remove "${name}" from the fan?`)) return;
    try { await api("/stores/" + encodeURIComponent(name), { method: "DELETE" }); clearErr(); loadStores(); }
    catch (e) { showErr(e.message); }
}
function renderStores(data) {
    const el = document.getElementById("storesStack");
    const stores = data.stores || [];
    if (!stores.length) { el.innerHTML = `<div class="empty">No stores configured. Add some under <code>federation.stores</code> in the config.</div>`; return; }
    el.innerHTML = stores.map(s => {
        const cls = storeClass(s.name);
        let sc = "active", dotLabel = s.status;
        if (s.status.startsWith("skipped")) sc = "skipped";
        else if (s.status === "disabled") sc = "disabled";
        const del = s.managed ? `<button class="del" data-del="${escapeHtml(s.name)}" title="Remove from the fan">✕</button>` : "";
        return `<div class="card"><div class="store-card">
          <div class="meta">
            <div class="nm"><span class="badge ${cls}">${escapeHtml(s.type)}</span>${escapeHtml(s.name)}${s.auth ? ` <span title="this store requires a bearer token" style="font-size:11px;opacity:.75;margin-left:2px;">🔒</span>` : ""}</div>
            <div class="url">${escapeHtml(s.url)}</div>
            <div class="knobs"><span>weight ${s.weight}</span><span>floor ${s.floor}</span></div>
          </div>
          <div class="right">
            <div class="status ${sc}"><span class="dot"></span>${escapeHtml(dotLabel)}</div>
            ${del}
          </div>
        </div></div>`;
    }).join("");
    el.querySelectorAll("[data-del]").forEach(b => b.onclick = () => removeStore(b.dataset.del));
}

// ---- health ----------------------------------------------------------------
async function loadHealth() {
    try {
        const data = await api("/health/stores");
        let healthy = data.healthy || 0, degraded = data.degraded || 0,
            unhealthy = data.unhealthy || 0, unknown = data.unknown || 0;
        document.getElementById("healthSummary").innerHTML =
            stat(healthy, "healthy", true) +
            stat(degraded, "degraded", false) +
            stat(unhealthy, "unhealthy", false) +
            stat(unknown, "unknown", false);
        const el = document.getElementById("healthStack");
        const stores = data.stores || [];
        if (!stores.length) { el.innerHTML = `<div class="empty">No health data yet - make a search query to start collecting metrics.</div>`; return; }
        el.innerHTML = stores.map(s => {
            const status = s.health_status || "unknown";
            let sc = status;
            if (status === "healthy") sc = "active";
            else if (status === "degraded") sc = "warn";
            else if (status === "unhealthy") sc = "error";
            else sc = "unknown";
            const lat = s.last_latency ? `${(s.last_latency * 1000).toFixed(0)}ms` : "-";
            const avg = s.avg_latency ? `${(s.avg_latency * 1000).toFixed(0)}ms` : "-";
            const mx = s.max_latency ? `${(s.max_latency * 1000).toFixed(0)}ms` : "-";
            const err = s.last_error ? escapeHtml(s.last_error) : "-";
            return `<div class="card"><div class="health-card">
                <div class="meta">
                    <div class="nm"><span class="badge">${escapeHtml(s.name)}</span></div>
                    <div class="knobs">
                        <span>calls: ${s.total_calls}</span>
                        <span>✓ ${s.successful_calls}</span>
                        <span>✗ ${s.failed_calls}</span>
                        <span>rate: ${(s.success_rate * 100).toFixed(1)}%</span>
                    </div>
                    <div class="knobs" style="margin-top:6px;">
                        <span>last: ${lat}</span>
                        <span>avg: ${avg}</span>
                        <span>max: ${mx}</span>
                    </div>
                    <div class="knobs" style="margin-top:6px;">
                        <span>errors: ${s.error_count}</span>
                        <span>consec: ${s.consecutive_failures}</span>
                    </div>
                </div>
                <div class="right">
                    <div class="status ${sc}"><span class="dot"></span>${escapeHtml(status)}</div>
                    ${err !== "-" ? `<div class="err-detail" title="${err}" style="font-size:11px;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${err}</div>` : ""}
                </div>
            </div></div>`;
        }).join("");
    } catch (e) { showErr(e.message); }
}

// ---- knobs ------------------------------------------------------------------
// Every setting read off in one place: GET /knobs. Three values per knob
// (live, yaml, default) and the sentence for what it does, so someone who is
// not the author can read the fan without opening the yaml or the code.
function knobVal(v) {
    if (v === null || v === undefined) return `<span class="knob-none">—</span>`;
    if (typeof v === "boolean") return v ? "on" : "off";
    return escapeHtml(String(v));
}
function knobRows(rows) {
    return rows.map(r => {
        const cls = (r.twiddled ? " twiddled" : "") + (!r.twiddled && r.yaml !== null && r.yaml !== undefined && !r.is_default ? " chosen" : "");
        const flag = r.twiddled ? `<span class="knob-flag twiddled">twiddled</span>`
                   : (cls.includes("chosen") ? `<span class="knob-flag chosen">set</span>` : "");
        const rt = r.runtime_settable ? `<span class="knob-rt" title="can be changed while running, with POST /configure">runtime</span>` : "";
        return `<tr class="${cls.trim()}">
            <td class="k">${escapeHtml(r.key)}${rt}</td>
            <td class="v v-live">${knobVal(r.live)}${flag}</td>
            <td class="v v-yaml">${knobVal(r.yaml)}</td>
            <td class="v v-default">${knobVal(r.default)}</td>
            <td class="w">${escapeHtml(r.what)}</td>
        </tr>`;
    }).join("");
}
function knobTable(rows) {
    return `<table class="knob-table"><thead><tr><th>knob</th><th>live</th><th>yaml</th><th>default</th><th>what it does</th></tr></thead>
        <tbody>${knobRows(rows)}</tbody></table>`;
}
async function loadKnobs() {
    const where = document.getElementById("knobsWhere");
    const el = document.getElementById("knobsStack");
    try {
        const d = await api("/knobs");
        const w = d.where || {}, sv = d.server || {};
        where.innerHTML = [
            `<span><b>yaml</b> ${escapeHtml(w.config_path || "none")}${w.config_exists ? "" : ` <span class="skip">(not on disk: defaults + env)</span>`}</span>`,
            `<span><b>overlay</b> ${escapeHtml(w.overlay_path || "none")}${w.overlay_exists ? "" : ` <span class="skip">(none yet)</span>`}</span>`,
            `<span><b>listening</b> ${escapeHtml(sv.host || "")}:${escapeHtml(sv.port || "")} · bearer ${escapeHtml(sv.auth || "open")}</span>`,
            (w.env_overrides || []).length ? `<span><b>env in effect</b> ${w.env_overrides.map(escapeHtml).join(", ")}</span>` : "",
        ].filter(Boolean).join("");
        let html = `<h3 class="group-h">The fan</h3>` + knobTable(d.federation || []);
        html += `<h3 class="group-h">The stores</h3>`;
        const stores = d.stores || [];
        if (!stores.length) html += `<div class="empty">No stores configured.</div>`;
        for (const s of stores) {
            const opts = Object.entries(s.options || {});
            html += `<div class="card knob-store">
                <div class="nm"><span class="badge ${storeClass(s.name)}">${escapeHtml(s.type)}</span>${escapeHtml(s.name)}
                    <span class="knob-meta">${escapeHtml(s.url)} · from the ${escapeHtml(s.where)} · bearer ${escapeHtml(s.auth)}</span></div>
                ${knobTable(s.knobs || [])}
                ${opts.length ? `<div class="knob-opts"><b>options</b> ${opts.map(([k, v]) => `<span>${escapeHtml(k)} = ${escapeHtml(typeof v === "string" ? v : JSON.stringify(v))}</span>`).join("")}</div>` : ""}
            </div>`;
        }
        el.innerHTML = html;
    } catch (e) { el.innerHTML = ""; showErr(e.message); }
}

// ---- overview ---------------------------------------------------------------
async function loadOverview() {
    try {
        const root = await api("/");
        let active = 0, total = 0, k = "?", nres = "?";
        try { const sd = await api("/stores"); active = sd.active; total = (sd.stores || []).length; k = sd.k; nres = sd.n_results; } catch (_) { }
        document.getElementById("stats").innerHTML =
            stat(active, "stores fanned", true) +
            stat(total, "configured", false) +
            stat(k, "RRF k", false) +
            stat(nres, "default n_results", false);
    } catch (e) { showErr(e.message); }
}
function stat(n, lbl, accent) {
    return `<div class="stat${accent ? " accent" : ""}"><div class="big">${n}</div><div class="lbl">${lbl}</div></div>`;
}

// ---- tabs -------------------------------------------------------------------
// Thin wrapper over the shell's showTab(): do the display toggle the shell way,
// then lazy-load the tab's data. (The tab buttons in tabs.html call this.)
function switchTab(tab) {
    showTab(tab);
    document.body.classList.toggle("sky-on", tab === "sky");
    if (tab !== "sky") skyPause();
    if (tab === "sky") loadSky().catch(e => { document.getElementById("sky-loading").textContent = "⚠ " + e.message; });
    if (tab === "stores") loadStores();
    if (tab === "knobs") loadKnobs();
    if (tab === "health") loadHealth();
    if (tab === "overview") loadOverview();
}

// ---- wiring -----------------------------------------------------------------
// Only the controls that exist in body.html. The tab buttons wire themselves
// via onclick in tabs.html; the 🔑 token modal is the shell's.
document.getElementById("searchBtn").onclick = runSearch;
document.getElementById("searchQ").addEventListener("keydown", e => { if (e.key === "Enter") runSearch(); });
document.getElementById("addToggle").onclick = () => { const f = document.getElementById("addForm"); f.style.display = f.style.display === "none" ? "block" : "none"; };
document.getElementById("addCancel").onclick = () => { document.getElementById("addForm").style.display = "none"; };
document.getElementById("addSave").onclick = addStore;

// ---- header refresh ---------------------------------------------------------
// Wired from the ⟳ button in header_aside.html. Reloads the active tab's data.
function reload() {
    const active = document.querySelector(".view.active");
    const id = active ? active.id : "";
    if (id === "stores") loadStores();
    else if (id === "knobs") loadKnobs();
    else if (id === "health") loadHealth();
    else if (id === "overview") loadOverview();
    else if (id === "sky") loadSky().catch(() => {});
    else boot();
}

// ---- boot -------------------------------------------------------------------
async function boot() {
    // prime the store->type map (for hemisphere coloring) + the fan-size badge
    try {
        const sd = await api("/stores");
        storeTypeMap = {};
        (sd.stores || []).forEach(s => storeTypeMap[s.name] = s.type);
        document.getElementById("storesBadge").textContent = `fanning: ${sd.active}/${(sd.stores || []).length}`;
    } catch (e) { showErr(e.message); }
}
boot();

// ---- the Sky (23 Oct 2026) ---------------------------------------------------
// Both hemispheres in one picture, the user's idea: "callosum gets the city, and
// the nebula above it". The left brain's city on the ground - a tower per
// Loci project, a floor per fact, skyways where one fact rests on another -
// and the right brain's nebula as its night sky: every Memory core and
// satellite, placed by what it means. The callosum is the bridge, so the Sky
// does what it does: ask a question and the same /search the Search tab runs
// lights the facts in the city and the memories in the sky that answer it,
// with light running between them. GET /sky brings both maps through the
// stores this callosum fans. three.js r159 is vendored and served from
// /viewer/vendor/.
const SKY = {
    lib: null, built: false, running: false, last: 0, t: 0, fitted: false,
    renderer: null, scene: null, camera: null, world: null, data: null,
    floors: [], meshes: [], byAddr: new Map(), ways: [], walkers: [], beacons: [], snow: null,
    stars: [], starById: new Map(), pickables: [], satPoints: null, skyLines: null, beams: null, beamRiders: [],
    extent: 200, height: 100, skyY: 300,
    orbit: { x: 0, y: 80, z: 0, r: 900, th: 0.75, ph: 1.22 },
    goal: { x: 0, y: 80, z: 0, r: 900, th: 0.75, ph: 1.22 },
    drift: !matchMedia("(prefers-reduced-motion: reduce)").matches, snowOn: true, waysOn: true, linesOn: true,
    selected: null, asked: null,
};
const SKY_FLOOR = 3.2, SKY_GAP = 0.55, SKY_BLOCK = 66;
const skyAddr = (p, k) => p + "\u0000" + k;

async function skyBlob(path) {
    const headers = {};
    const tok = getToken();
    if (tok) headers["Authorization"] = "Bearer " + tok;
    const res = await fetch(path, { headers });
    if (!res.ok) throw new Error(res.status + " " + res.statusText);
    return URL.createObjectURL(await res.blob());
}

function skyLib() {
    if (window.THREE) return Promise.resolve();
    if (!SKY.lib) {
        SKY.lib = skyBlob("/viewer/vendor/three.min.js").then(url => new Promise((ok, bad) => {
            const s = document.createElement("script");
            s.src = url;
            s.onload = () => ok();
            s.onerror = () => bad(new Error("the 3D library did not load"));
            document.head.appendChild(s);
        }));
        SKY.lib.catch(() => { SKY.lib = null; });
    }
    return SKY.lib;
}

function skyFrame() {
    const bar = document.querySelector(".tabbar") || document.querySelector("header");
    if (bar) document.getElementById("sky-wrap").style.top = Math.round(bar.getBoundingClientRect().bottom) + "px";
}

function skyCells(n) {
    const out = [[0, 0]];
    let x = 0, y = 0, dx = 1, dy = 0, seg = 1, steps = 0, turns = 0;
    while (out.length < n) {
        x += dx; y += dy; out.push([x, y]);
        if (++steps === seg) { steps = 0; [dx, dy] = [-dy, dx]; if (++turns % 2 === 0) seg++; }
    }
    return out;
}

const skyWidth = f => Math.min(30, 11 + 3 * Math.sqrt(f.history) + 1.6 * (f.rests_on + f.rested_on_by));

function skyWindows(seed) {
    const c = document.createElement("canvas");
    c.width = 256; c.height = 32;
    const x = c.getContext("2d");
    x.fillStyle = "#000"; x.fillRect(0, 0, 256, 32);
    let s = seed * 9301 + 49297;
    const rnd = () => ((s = (s * 9301 + 49297) % 233280) / 233280);
    for (let i = 0; i < 16; i++) {
        const lit = rnd();
        if (lit < 0.28) continue;
        const v = 0.45 + 0.55 * rnd();
        x.fillStyle = lit < 0.42 ? `rgba(160,200,255,${v})` : `rgba(255,${200 + Math.floor(40 * rnd())},150,${v})`;
        x.fillRect(i * 16 + 4, 9, 9, 15);
    }
    const t = new THREE.CanvasTexture(c);
    t.colorSpace = THREE.SRGBColorSpace;
    t.wrapS = THREE.RepeatWrapping;
    return t;
}

function skyLabel(text, hue) {
    const c = document.createElement("canvas");
    c.width = 512; c.height = 96;
    const x = c.getContext("2d");
    x.font = "600 44px ui-sans-serif, system-ui, sans-serif";
    x.textAlign = "center"; x.textBaseline = "middle";
    x.shadowColor = "rgba(0,0,0,0.9)"; x.shadowBlur = 12;
    x.fillStyle = `hsl(${hue}, 85%, 78%)`;
    x.fillText(text, 256, 48);
    const t = new THREE.CanvasTexture(c);
    t.colorSpace = THREE.SRGBColorSpace;
    const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: t, transparent: true, depthWrite: false }));
    s.scale.set(44, 8.25, 1);
    return s;
}

// a soft round glow: stars, flakes and the nebula's haze
function skyGlow(stops) {
    const c = document.createElement("canvas");
    c.width = c.height = 64;
    const x = c.getContext("2d"), g = x.createRadialGradient(32, 32, 0, 32, 32, 32);
    for (const [at, col] of stops) g.addColorStop(at, col);
    x.fillStyle = g; x.fillRect(0, 0, 64, 64);
    const t = new THREE.CanvasTexture(c);
    t.colorSpace = THREE.SRGBColorSpace;
    return t;
}

// The night behind the city and the nebula: stars thick overhead and thinning
// toward the horizon, where the city's glow is. Under the transparent scene.
function skyBackdrop(wrap) {
    const c = document.createElement('canvas');
    c.className = 'sky-stars';
    c.width = 2400; c.height = 1500;
    wrap.parentElement.insertBefore(c, wrap);
    twinkleStars(c, starField(460, c.width, c.height, () => Math.pow(Math.random(), 1.7) * c.height * 0.85));
}

// The stars far behind everything twinkle: most a little, a few a lot, each
// on its own clock, the bigger ones with a soft halo that breathes with them.
// Redrawn at ~30 frames a second, and only while the canvas is on screen and
// the tab is in front. prefers-reduced-motion gets a slower, fainter twinkle,
// not a frozen sky: nothing moves, only brightness changes, and Windows hands
// that setting to every browser on a box whose animations are turned off.
function twinkleStars(c, stars) {
    const x = c.getContext('2d');
    const calm = matchMedia('(prefers-reduced-motion: reduce)').matches ? 0.45 : 1;
    const draw = (t) => {
        x.clearRect(0, 0, c.width, c.height);
        for (const s of stars) {
            const k = 1 - s.amp * calm * (0.5 + 0.5 * Math.sin(t * s.sp * calm + s.ph));
            x.fillStyle = s.col;
            if (s.r > 1.05) {
                x.globalAlpha = s.a * k * 0.18;
                x.beginPath(); x.arc(s.x, s.y, s.r * 3.2, 0, 2 * Math.PI); x.fill();
            }
            x.globalAlpha = s.a * k;
            x.beginPath(); x.arc(s.x, s.y, s.r, 0, 2 * Math.PI); x.fill();
        }
        x.globalAlpha = 1;
    };
    draw(0);
    let last = 0;
    const tick = (now) => {
        requestAnimationFrame(tick);
        if (now - last < 33 || document.hidden || !c.getClientRects().length) return;
        last = now;
        draw(now / 1000);
    };
    requestAnimationFrame(tick);
}

function starField(n, w, h, yOf = () => Math.random() * h) {
    const out = [];
    for (let i = 0; i < n; i++) {
        const big = Math.random() < 0.06, warm = Math.random() < 0.12, lively = Math.random() < 0.15;
        out.push({ x: Math.random() * w, y: yOf(), r: big ? 1.1 + Math.random() * 0.9 : 0.3 + Math.random() * 0.7,
                   col: warm ? '#ffd6aa' : '#d2e1ff', a: warm ? 0.35 + Math.random() * 0.5 : 0.2 + Math.random() * 0.6,
                   amp: lively ? 0.55 + Math.random() * 0.4 : 0.1 + Math.random() * 0.2,
                   sp: 0.6 + Math.random() * 1.8, ph: Math.random() * 6.283 });
    }
    return out;
}

function skyBuild() {
    const wrap = document.getElementById("sky-canvas");
    skyBackdrop(wrap);
    const r = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    r.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    r.setClearColor(0x000000, 0);
    wrap.appendChild(r.domElement);
    const scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(0x0b1022, 0.0007);
    const cam = new THREE.PerspectiveCamera(45, 1, 1, 8000);
    scene.add(new THREE.HemisphereLight(0x8fa8ff, 0x0b0d18, 0.55));
    const moon = new THREE.DirectionalLight(0xcfe0ff, 0.9);
    moon.position.set(-300, 500, 200);
    scene.add(moon);
    scene.add(new THREE.AmbientLight(0x404a70, 0.35));
    SKY.starTex = skyGlow([[0, "rgba(255,255,255,1)"], [0.25, "rgba(255,255,255,0.65)"], [1, "rgba(255,255,255,0)"]]);
    SKY.hazeTex = skyGlow([[0, "rgba(255,255,255,0.55)"], [0.5, "rgba(255,255,255,0.12)"], [1, "rgba(255,255,255,0)"]]);
    Object.assign(SKY, { renderer: r, scene, camera: cam, built: true });
    skyWire(r.domElement);
    new ResizeObserver(() => {
        const w = wrap.clientWidth, h = wrap.clientHeight;
        if (!w || !h) return;
        r.setSize(w, h);
        cam.aspect = w / h;
        cam.updateProjectionMatrix();
    }).observe(wrap);
}

function skyWorld(data) {
    if (SKY.world) { SKY.scene.remove(SKY.world); SKY.world.traverse(o => { o.geometry && o.geometry.dispose(); }); }
    const world = new THREE.Group();
    Object.assign(SKY, { world, floors: [], meshes: [], byAddr: new Map(), ways: [], walkers: [], beacons: [],
                         stars: [], starById: new Map(), pickables: [], beams: null, beamRiders: [] });
    if (data.city) skyCity(data.city, world);
    if (data.nebula) skyNebula(data.nebula, world);
    SKY.scene.add(world);
}

// ---- the left brain: the city -------------------------------------------------
function skyCity(city, world) {
    const order = [...city.towers].sort((a, b) => {
        const rank = p => p.project === "*" ? 0 : p.project === "seren" ? 1 : p.project === "wren" ? 2 : 3;
        return rank(a) - rank(b) || b.floors.length - a.floors.length || a.project.localeCompare(b.project);
    });
    const cells = skyCells(order.length);
    const wins = [0, 1, 2, 3, 4, 5].map(skyWindows);
    let maxH = 0, ext = 0, k = 0;
    order.forEach((tw, i) => {
        const hue = tw.project === "*" ? 44 : tw.project === "wren" ? 345 : (190 + (k++) * 137.508) % 360;
        const [cx, cz] = cells[i].map(v => v * SKY_BLOCK);
        ext = Math.max(ext, Math.abs(cx), Math.abs(cz));
        tw.floors.forEach((f, lvl) => {
            const w = skyWidth(f);
            const y = lvl * (SKY_FLOOR + SKY_GAP) + SKY_FLOOR / 2;
            const tex = wins[(lvl * 7 + i * 3) % wins.length].clone();
            tex.repeat.set(Math.max(2, Math.round(w / 1.9)) / 16, 1);
            tex.needsUpdate = true;
            const base = new THREE.Color().setHSL(hue / 360, 0.32, 0.17);
            const glow = new THREE.Color().setHSL(hue / 360, 0.75, 0.68);
            const side = new THREE.MeshStandardMaterial({ color: base, emissive: f.recheck ? new THREE.Color(0xffb347) : glow,
                emissiveMap: tex, emissiveIntensity: 1.1, roughness: 0.5, metalness: 0.35 });
            const cap = new THREE.MeshStandardMaterial({ color: base, roughness: 0.7, metalness: 0.2 });
            const m = new THREE.Mesh(new THREE.BoxGeometry(w, SKY_FLOOR, w), [side, side, cap, cap, side, side]);
            m.position.set(cx, y, cz);
            const floor = { ...f, kind: "fact", project: tw.project, level: lvl, w, y, x: cx, z: cz, hue, mesh: m, side, glow, ways: [] };
            m.userData.thing = floor;
            world.add(m);
            SKY.meshes.push(m);
            SKY.pickables.push(m);
            SKY.floors.push(floor);
            SKY.byAddr.set(skyAddr(tw.project, f.key), floor);
        });
        const top = tw.floors.length * (SKY_FLOOR + SKY_GAP);
        maxH = Math.max(maxH, top);
        const mast = new THREE.Mesh(new THREE.CylinderGeometry(0.35, 0.5, 9, 6),
                                    new THREE.MeshStandardMaterial({ color: 0x2a3048, roughness: 0.8 }));
        mast.position.set(cx, top + 4.5, cz);
        world.add(mast);
        const warn = tw.floors.some(f => f.recheck);
        const beacon = new THREE.Mesh(new THREE.SphereGeometry(1.1, 10, 8),
                                      new THREE.MeshBasicMaterial({ color: warn ? 0xffb347 : 0xff3b3b }));
        beacon.position.set(cx, top + 9.5, cz);
        world.add(beacon);
        SKY.beacons.push({ mesh: beacon, phase: Math.random() * 6, warn });
        const label = skyLabel(tw.project === "*" ? "* fundamentals" : tw.project, hue);
        label.position.set(cx, top + 17, cz);
        world.add(label);
    });
    const size = (Math.ceil(ext / SKY_BLOCK) * 2 + 3) * SKY_BLOCK;
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(size * 14, size * 14),
                                  new THREE.MeshStandardMaterial({ color: 0x0a0d18, roughness: 1 }));
    ground.rotation.x = -Math.PI / 2;
    world.add(ground);
    const grid = new THREE.GridHelper(size, size / SKY_BLOCK, 0x2a3358, 0x1a2140);
    grid.position.set(SKY_BLOCK / 2, 0.05, SKY_BLOCK / 2);
    world.add(grid);
    skyWays(city.links, world);
    SKY.extent = ext + SKY_BLOCK / 2;
    SKY.height = maxH;
    skySnow(world, size, maxH);
}

function skyWays(links, world) {
    const walkerGeo = new THREE.SphereGeometry(0.45, 8, 6);
    for (const lk of links) {
        const a = SKY.byAddr.get(skyAddr(lk.project, lk.key)), b = SKY.byAddr.get(skyAddr(lk.on_project, lk.on_key));
        if (!a || !b) continue;
        const mat = new THREE.MeshStandardMaterial({ color: 0x1c2232, emissive: 0xffcf7a, emissiveIntensity: 0.35,
                                                     roughness: 0.3, metalness: 0.4, transparent: true, opacity: 0.7 });
        let curve, mesh;
        if (a.project === b.project) {
            const out = Math.max(a.w, b.w) / 2 + 3 + (SKY.ways.filter(w => w.a.project === a.project).length % 4) * 1.6;
            const ang = (a.level * 0.9) % (Math.PI * 2), ox = Math.cos(ang), oz = Math.sin(ang);
            const p0 = new THREE.Vector3(a.x + ox * a.w / 2, a.y, a.z + oz * a.w / 2);
            const p2 = new THREE.Vector3(b.x + ox * b.w / 2, b.y, b.z + oz * b.w / 2);
            const p1 = new THREE.Vector3(a.x + ox * out * 1.6, (a.y + b.y) / 2, a.z + oz * out * 1.6);
            curve = new THREE.QuadraticBezierCurve3(p0, p1, p2);
            mesh = new THREE.Mesh(new THREE.TubeGeometry(curve, 28, 0.5, 6, false), mat);
        } else {
            const d = new THREE.Vector3(b.x - a.x, 0, b.z - a.z).normalize();
            const p0 = new THREE.Vector3(a.x + d.x * a.w / 2, a.y, a.z + d.z * a.w / 2);
            const p1 = new THREE.Vector3(b.x - d.x * b.w / 2, b.y, b.z - d.z * b.w / 2);
            curve = new THREE.LineCurve3(p0, p1);
            mesh = new THREE.Mesh(new THREE.BoxGeometry(1.25, 1.05, p0.distanceTo(p1)), mat);
            mesh.position.copy(p0).add(p1).multiplyScalar(0.5);
            mesh.lookAt(p1);
        }
        world.add(mesh);
        const way = { a, b, curve, mesh, mat, len: curve.getLength(), on: true };
        a.ways.push(way); b.ways.push(way);
        SKY.ways.push(way);
        for (let i = 0; i < 2; i++) {
            const w = new THREE.Mesh(walkerGeo, new THREE.MeshBasicMaterial({ color: 0xfff1c9 }));
            world.add(w);
            SKY.walkers.push({ mesh: w, way, t: i / 2 + Math.random() * 0.2 });
        }
    }
}

function skySnow(world, size, h) {
    const n = 2600, pos = new Float32Array(n * 3), speed = new Float32Array(n);
    for (let i = 0; i < n; i++) {
        pos[i * 3] = (Math.random() - 0.5) * size * 1.4;
        pos[i * 3 + 1] = Math.random() * (h + 120);
        pos[i * 3 + 2] = (Math.random() - 0.5) * size * 1.4;
        speed[i] = 4 + Math.random() * 7;
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    const flakes = new THREE.Points(g, new THREE.PointsMaterial({ size: 1.6, map: SKY.starTex, transparent: true,
        opacity: 0.85, depthWrite: false, color: 0xe8f0ff }));
    flakes.visible = SKY.snowOn;
    world.add(flakes);
    SKY.snow = { points: flakes, speed, top: h + 120 };
}

// ---- the right brain: the nebula above it -------------------------------------
// Memory's own places (its embeddings by PCA, about 800 across), widened to
// cover the city and flattened into a layer above the rooftops. A core is a
// star in the hue of where it sits, as on Memory's Map; satellites are dust
// in their core's hue; a few big hazes give it its cloud.
function skyNebula(neb, world) {
    const spread = Math.max(SKY.extent * 2.2, 420) / 400;
    SKY.skyY = SKY.height + 190;
    const place = p => new THREE.Vector3(p[0] * spread, SKY.skyY + p[1] * spread * 0.32, p[2] * spread);
    const byId = new Map(neb.nodes.map(n => [n.id, n]));
    for (const n of neb.nodes) n.hue = (Math.atan2(n.pos[2], n.pos[0]) * 180 / Math.PI + 360 + n.pos[1] * 0.15) % 360;
    for (const n of neb.nodes) {
        if (n.kind !== "satellite") continue;
        const home = byId.get((n.cores || [])[0]);
        if (home) n.hue = home.hue;
    }
    const dustPos = [], dustCol = [], dust = [];
    for (const n of neb.nodes) {
        const p = place(n.pos);
        const col = n.was_core ? new THREE.Color(0x8a93a6) : n.verbatim ? new THREE.Color(0xfff3c4)
            : new THREE.Color().setHSL(n.hue / 360, n.kind === "core" ? 0.88 : 0.7, n.kind === "core" ? 0.64 : 0.76);
        const star = { ...n, kind: n.kind === "core" ? "core" : "satellite", x: p.x, y: p.y, z: p.z, col };
        if (n.kind === "core") {
            const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: SKY.starTex, color: col, transparent: true,
                depthWrite: false, blending: THREE.AdditiveBlending }));
            const s = 9 + 4 * Math.sqrt(n.evidence || 1) + (n.verbatim ? 4 : 0);
            sp.scale.set(s, s, 1);
            sp.position.copy(p);
            sp.userData.thing = star;
            star.sprite = sp; star.size = s;
            world.add(sp);
            SKY.pickables.push(sp);
        } else {
            star.dust = dust.length;
            dust.push(star);
            dustPos.push(p.x, p.y, p.z);
            dustCol.push(col.r, col.g, col.b);
        }
        SKY.stars.push(star);
        SKY.starById.set(n.id, star);
    }
    if (dust.length) {
        const g = new THREE.BufferGeometry();
        g.setAttribute("position", new THREE.Float32BufferAttribute(dustPos, 3));
        g.setAttribute("color", new THREE.Float32BufferAttribute(dustCol, 3));
        const pts = new THREE.Points(g, new THREE.PointsMaterial({ size: 4.2, map: SKY.starTex, vertexColors: true,
            transparent: true, opacity: 0.9, depthWrite: false, blending: THREE.AdditiveBlending }));
        pts.userData.dust = dust;
        world.add(pts);
        SKY.satPoints = pts;
        SKY.pickables.push(pts);
    }
    // the links, faint: a satellite to its cores, gold where one episode is two stories
    const lp = [], lc = [];
    for (const lk of neb.links) {
        const a = SKY.starById.get(lk.source), b = SKY.starById.get(lk.target);
        if (!a || !b) continue;
        const c = lk.kind === "bridge" ? [1, 0.82, 0.48] : lk.kind === "history" ? [0.4, 0.43, 0.5]
            : [b.col.r * 0.55, b.col.g * 0.55, b.col.b * 0.55];
        lp.push(a.x, a.y, a.z, b.x, b.y, b.z);
        lc.push(...c, ...c);
    }
    const lg = new THREE.BufferGeometry();
    lg.setAttribute("position", new THREE.Float32BufferAttribute(lp, 3));
    lg.setAttribute("color", new THREE.Float32BufferAttribute(lc, 3));
    SKY.skyLines = new THREE.LineSegments(lg, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true,
        opacity: 0.45, depthWrite: false, blending: THREE.AdditiveBlending }));
    SKY.skyLines.visible = SKY.linesOn;
    world.add(SKY.skyLines);
    // the cloud: a soft haze over the busiest cores
    const big = SKY.stars.filter(s => s.kind === "core").sort((a, b) => (b.evidence || 1) - (a.evidence || 1)).slice(0, 14);
    for (const s of big) {
        const h = new THREE.Sprite(new THREE.SpriteMaterial({ map: SKY.hazeTex, color: s.col, transparent: true,
            opacity: 0.16, depthWrite: false, blending: THREE.AdditiveBlending }));
        const r = 110 + Math.random() * 90;
        h.scale.set(r, r * 0.55, 1);
        h.position.set(s.x, s.y, s.z);
        world.add(h);
    }
}

// ---- looking around ------------------------------------------------------------
function skyWire(el) {
    let down = null, moved = 0;
    const pts = new Map();
    el.addEventListener("contextmenu", e => e.preventDefault());
    el.addEventListener("pointerdown", e => {
        el.setPointerCapture(e.pointerId);
        pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
        down = { pan: e.button === 2 || e.shiftKey };
        moved = 0;
        if (SKY.drift) { SKY.drift = false; document.getElementById("sky-drift").checked = false; }
    });
    el.addEventListener("pointermove", e => {
        const prev = pts.get(e.pointerId);
        if (!prev) { skyHover(e); return; }
        const dx = e.clientX - prev.x, dy = e.clientY - prev.y;
        moved += Math.abs(dx) + Math.abs(dy);
        if (pts.size === 2) {
            const [p, q] = [...pts.entries()];
            const other = p[0] === e.pointerId ? q[1] : p[1];
            const before = Math.hypot(prev.x - other.x, prev.y - other.y);
            const after = Math.hypot(e.clientX - other.x, e.clientY - other.y);
            if (before > 0) SKY.goal.r = Math.min(4000, Math.max(30, SKY.goal.r * before / after));
        } else if (down && down.pan) {
            const g = SKY.goal, s = g.r * 0.0016;
            g.x += (-Math.cos(g.th) * dx - Math.sin(g.th) * dy) * s;
            g.z += (Math.sin(g.th) * dx - Math.cos(g.th) * dy) * s;
        } else {
            SKY.goal.th -= dx * 0.005;
            SKY.goal.ph = Math.min(2.6, Math.max(0.12, SKY.goal.ph - dy * 0.004));
        }
        pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    });
    const up = e => {
        const was = pts.get(e.pointerId);
        pts.delete(e.pointerId);
        if (was && moved < 6 && pts.size === 0) skySelect(skyRay(e));
        if (!pts.size) down = null;
    };
    el.addEventListener("pointerup", up);
    el.addEventListener("pointercancel", e => { pts.delete(e.pointerId); down = null; });
    el.addEventListener("pointerleave", () => { document.getElementById("sky-tip").hidden = true; });
    el.addEventListener("wheel", e => {
        e.preventDefault();
        SKY.goal.r = Math.min(4000, Math.max(30, SKY.goal.r * Math.exp(e.deltaY * 0.0011)));
    }, { passive: false });
}

function skyRay(e) {
    const rect = SKY.renderer.domElement.getBoundingClientRect();
    const v = new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
    const ray = new THREE.Raycaster();
    ray.params.Points.threshold = 3;
    ray.setFromCamera(v, SKY.camera);
    const hit = ray.intersectObjects(SKY.pickables, false)[0];
    if (!hit) return null;
    if (hit.object === SKY.satPoints) return SKY.satPoints.userData.dust[hit.index] || null;
    return hit.object.userData.thing || null;
}

function skyAbout(t, n) {
    const s = t.kind === "fact" ? t.value : t.text;
    return (s || "").length > n ? s.slice(0, n) + "…" : (s || "");
}

function skyHover(e) {
    const tip = document.getElementById("sky-tip");
    const t = SKY.built ? skyRay(e) : null;
    SKY.renderer.domElement.style.cursor = t ? "pointer" : "";
    if (!t) { tip.hidden = true; return; }
    tip.innerHTML = (t.kind === "fact"
        ? `<div class="st-addr"><span class="st-left">fact</span> ${escapeHtml(t.project)} / <b>${escapeHtml(t.key)}</b></div>`
        : `<div class="st-addr"><span class="st-right">${t.was_core ? "older wording" : escapeHtml(t.kind)}</span> ${escapeHtml(t.topic || "")}</div>`) +
        `<div class="st-v">${escapeHtml(skyAbout(t, 160))}</div>`;
    const rect = document.getElementById("sky-wrap").getBoundingClientRect();
    tip.style.left = Math.min(e.clientX - rect.left + 14, rect.width - 320) + "px";
    tip.style.top = (e.clientY - rect.top + 14) + "px";
    tip.hidden = false;
}

// ---- how it looks: selected, asked, or neither
function skyLook() {
    const asked = SKY.asked, sel = SKY.selected;
    const near = sel && sel.kind === "fact" ? new Set([sel, ...sel.ways.map(w => w.a), ...sel.ways.map(w => w.b)]) : null;
    for (const f of SKY.floors) {
        let i = 1.1, col = f.recheck ? new THREE.Color(0xffb347) : f.glow;
        if (asked) { const on = asked.facts.has(f); i = on ? 2.1 : 0.14; if (on) col = new THREE.Color(0xd9c8ff); }
        if (near) i = near.has(f) ? (f === sel ? 2.3 : 1.6) : 0.15;
        f.side.emissive.copy(col);
        f.side.emissiveIntensity = i;
    }
    for (const w of SKY.ways) {
        const on = !near && !asked || near && (w.a === sel || w.b === sel);
        w.mesh.visible = SKY.waysOn;
        w.mat.opacity = on ? (near ? 0.9 : 0.7) : 0.06;
        w.mat.emissiveIntensity = on ? (near ? 1.2 : 0.35) : 0.04;
        w.on = on;
    }
    for (const s of SKY.stars) {
        if (!s.sprite) continue;
        const lit = asked && asked.memories.has(s), chosen = sel === s;
        const dim = asked && !lit;
        s.sprite.material.color.copy(lit || chosen ? new THREE.Color(0xffffff) : s.col);
        s.sprite.material.opacity = dim ? 0.25 : 1;
        const k = lit || chosen ? 2.4 : 1;
        s.sprite.scale.set(s.size * k, s.size * k, 1);
    }
    if (SKY.satPoints) SKY.satPoints.material.opacity = asked ? 0.3 : 0.9;
    if (SKY.skyLines) {
        SKY.skyLines.visible = SKY.linesOn;
        SKY.skyLines.material.opacity = asked ? 0.12 : 0.45;
    }
}

function skySelect(t) {
    SKY.selected = t;
    const info = document.getElementById("sky-info");
    skyLook();
    if (!t) { info.hidden = true; return; }
    const close = t.kind === "fact" ? Math.max(70, 40 + t.w * 3.2) : 120;
    Object.assign(SKY.goal, { x: t.x, y: t.y, z: t.z, r: close });
    let body;
    if (t.kind === "fact") {
        const rests = t.ways.filter(w => w.a === t).map(w => w.b), by = t.ways.filter(w => w.b === t).map(w => w.a);
        const ref = g => `<li><button class="si-ref" data-fact="${escapeHtml(skyAddr(g.project, g.key))}">` +
            `<i style="background:hsl(${g.hue},80%,66%)"></i><span><b>${escapeHtml(g.project)}</b> / ${escapeHtml(g.key)}</span></button></li>`;
        body = `<div class="si-addr"><span class="si-side left">left brain · fact</span> ${escapeHtml(t.project)} · floor ${t.level + 1}</div>
            <div class="si-key">${escapeHtml(t.key)}</div>
            <div class="si-v">${escapeHtml(t.value || "")}</div>
            ${t.why ? `<div class="si-why"><span>why</span>${escapeHtml(t.why)}</div>` : ""}
            <div class="si-meta">changed ${t.history} time${t.history === 1 ? "" : "s"}${t.recheck ? " · <b class='warn'>something under it changed</b>" : ""}</div>
            ${rests.length ? `<h4>rests on</h4><ul>${rests.map(ref).join("")}</ul>` : ""}
            ${by.length ? `<h4>what rests on it</h4><ul>${by.map(ref).join("")}</ul>` : ""}`;
    } else {
        const ref = id => {
            const m = SKY.starById.get(id);
            if (!m) return "";
            return `<li><button class="si-ref" data-star="${escapeHtml(id)}"><i style="background:#${m.col.getHexString()}"></i>` +
                `<span>${escapeHtml(skyAbout(m, 110))}</span></button></li>`;
        };
        const subs = SKY.stars.filter(s => (s.cores || []).includes(t.id));
        body = `<div class="si-addr"><span class="si-side right">right brain · ${t.was_core ? "older wording" : escapeHtml(t.kind)}</span> evidence ${t.evidence}</div>
            ${t.topic ? `<div class="si-topic">${escapeHtml(t.topic)}</div>` : ""}
            <div class="si-v">${escapeHtml(t.text || "")}</div>
            ${(t.cores || []).length ? `<h4>${t.was_core ? "replaced by" : "hangs on"}</h4><ul>${t.cores.map(ref).join("")}</ul>` : ""}
            ${subs.length ? `<h4>its episodes (${subs.length})</h4><ul>${subs.map(s => ref(s.id)).join("")}</ul>` : ""}`;
    }
    info.innerHTML = `<button class="si-close" aria-label="Close" data-close>×</button>${body}`;
    info.hidden = false;
    info.scrollTop = 0;
}

// ---- the bridge: ask, and light runs between what answers -----------------------
function skyClearBeams() {
    if (SKY.beams) { SKY.world.remove(SKY.beams); SKY.beams.geometry.dispose(); SKY.beams = null; }
    for (const r of SKY.beamRiders) SKY.world.remove(r.mesh);
    SKY.beamRiders = [];
}

async function skyAsk(q) {
    q = (q || "").trim();
    const panel = document.getElementById("sky-answer");
    skyClearBeams();
    if (!q) { SKY.asked = null; panel.hidden = true; skyLook(); return; }
    panel.hidden = false;
    panel.innerHTML = `<div class="sa-head">asking both halves…</div>`;
    const r = await api("/search", { method: "POST", body: JSON.stringify({ query: q, n_results: 12 }) });
    const facts = new Set(), memories = new Set(), rows = [];
    let unseen = 0;
    for (const h of r.hits || []) {
        const m = h.metadata || {};
        const fact = m.key != null ? SKY.byAddr.get(skyAddr(m.project, m.key)) : null;
        const star = fact ? null : SKY.starById.get(h.id);
        if (fact) facts.add(fact);
        else if (star) memories.add(star);
        else unseen++;
        rows.push({ h, thing: fact || star });
    }
    SKY.asked = { q, facts, memories };
    skySelect(null);
    // light between every fact and every memory that answered: the same
    // question, both halves of the brain
    const lp = [], lc = [], ends = [];
    for (const f of facts) for (const s of memories) {
        const a = new THREE.Vector3(f.x, f.y, f.z), b = new THREE.Vector3(s.x, s.y, s.z);
        lp.push(a.x, a.y, a.z, b.x, b.y, b.z);
        lc.push(0.36, 0.78, 0.91, 0.94, 0.45, 0.42);
        ends.push([a, b]);
    }
    if (lp.length) {
        const g = new THREE.BufferGeometry();
        g.setAttribute("position", new THREE.Float32BufferAttribute(lp, 3));
        g.setAttribute("color", new THREE.Float32BufferAttribute(lc, 3));
        SKY.beams = new THREE.LineSegments(g, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true,
            opacity: 0.75, depthWrite: false, blending: THREE.AdditiveBlending }));
        SKY.world.add(SKY.beams);
        const geo = new THREE.SphereGeometry(1.3, 8, 6);
        for (const [a, b] of ends) {
            const m = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color: 0xd9c8ff }));
            SKY.world.add(m);
            SKY.beamRiders.push({ mesh: m, a, b, t: Math.random() });
        }
    }
    skyLook();
    const side = h => h.metadata && h.metadata.key != null ? "left" : "right";
    panel.innerHTML = `
        <div class="sa-head">${facts.size} fact${facts.size === 1 ? "" : "s"} below · ${memories.size} memor${memories.size === 1 ? "y" : "ies"} above${unseen ? ` · ${unseen} from short- or near-term, not in the sky` : ""}
            <button class="sa-x" data-clear aria-label="Clear">×</button></div>
        <ol>${rows.map(({ h, thing }, idx) => `<li><button class="sa-row ${side(h)}" ${thing ? `data-go="${idx}"` : "disabled"}>
            <span class="sa-store">${escapeHtml(h.store)}</span>${escapeHtml((h.content || "").slice(0, 120))}${(h.content || "").length > 120 ? "…" : ""}</button></li>`).join("")}</ol>`;
    SKY.answerRows = rows;
    // frame the question: from the city's answers up to the sky's
    if (facts.size || memories.size) {
        const all = [...facts, ...memories];
        const c = all.reduce((acc, t) => ({ x: acc.x + t.x / all.length, y: acc.y + t.y / all.length, z: acc.z + t.z / all.length }), { x: 0, y: 0, z: 0 });
        Object.assign(SKY.goal, { x: c.x, y: c.y, z: c.z, r: Math.max(SKY.goal.r, 500) });
    }
}

function skyFit() {
    const top = SKY.stars.length ? SKY.skyY : SKY.height;
    const r = Math.max(SKY.extent * 3.4, top * 2.6, 300);
    Object.assign(SKY.goal, { x: SKY_BLOCK * 0.25, y: top * 0.45, z: SKY_BLOCK * 0.25, r, ph: 1.28 });
}

function skyTick(now) {
    if (!SKY.running) return;
    requestAnimationFrame(skyTick);
    const dt = Math.min(0.05, (now - (SKY.last || now)) / 1000);
    SKY.last = now;
    SKY.t += dt;
    const o = SKY.orbit, g = SKY.goal, a = 1 - Math.exp(-dt * 6);
    if (SKY.drift) g.th += dt * 0.05;
    for (const k of ["x", "y", "z", "r", "th", "ph"]) o[k] += (g[k] - o[k]) * a;
    const c = SKY.camera;
    c.position.set(o.x + o.r * Math.sin(o.ph) * Math.sin(o.th), o.y + o.r * Math.cos(o.ph), o.z + o.r * Math.sin(o.ph) * Math.cos(o.th));
    c.lookAt(o.x, o.y, o.z);
    for (const w of SKY.walkers) {
        const show = SKY.waysOn && w.way.on;
        w.mesh.visible = show;
        if (!show) continue;
        w.t = (w.t + dt * 9 / Math.max(10, w.way.len)) % 1;
        w.mesh.position.copy(w.way.curve.getPoint(w.t));
    }
    for (const r of SKY.beamRiders) {
        r.t = (r.t + dt * 0.35) % 1;
        r.mesh.position.lerpVectors(r.a, r.b, r.t);
    }
    if (SKY.beams) SKY.beams.material.opacity = 0.55 + 0.25 * Math.sin(SKY.t * 2.4);
    for (const b of SKY.beacons) b.mesh.visible = Math.sin(SKY.t * (b.warn ? 5 : 2.2) + b.phase) > -0.2;
    for (const s of SKY.stars) {
        if (!s.sprite || SKY.asked || SKY.selected === s) continue;
        const tw = 1 + 0.12 * Math.sin(SKY.t * 1.7 + s.x * 0.05);
        s.sprite.scale.set(s.size * tw, s.size * tw, 1);
    }
    if (SKY.snow && SKY.snowOn) {
        const p = SKY.snow.points.geometry.attributes.position, sp = SKY.snow.speed;
        for (let i = 0; i < sp.length; i++) {
            let y = p.array[i * 3 + 1] - sp[i] * dt;
            if (y < 0) y += SKY.snow.top;
            p.array[i * 3 + 1] = y;
            p.array[i * 3] += Math.sin(SKY.t * 0.7 + i) * dt * 0.8;
        }
        p.needsUpdate = true;
    }
    SKY.renderer.render(SKY.scene, c);
}

let skyWired = false;
function skyControls() {
    if (skyWired) return;
    skyWired = true;
    const on = (id, fn) => document.getElementById(id).addEventListener("change", e => fn(e.target.checked));
    on("sky-drift", v => { SKY.drift = v; });
    on("sky-snow", v => { SKY.snowOn = v; if (SKY.snow) SKY.snow.points.visible = v; });
    on("sky-ways", v => { SKY.waysOn = v; skyLook(); });
    on("sky-lines", v => { SKY.linesOn = v; skyLook(); });
    const ask = () => skyAsk(document.getElementById("sky-q").value).catch(e => {
        document.getElementById("sky-answer").innerHTML = `<div class="sa-head">⚠ ${escapeHtml(e.message)}</div>`;
    });
    document.getElementById("sky-ask").addEventListener("click", ask);
    document.getElementById("sky-q").addEventListener("keydown", e => { if (e.key === "Enter") ask(); });
    document.getElementById("sky-fit").addEventListener("click", () => { skySelect(null); skyFit(); });
    document.getElementById("sky-answer").addEventListener("click", e => {
        if (e.target.closest("[data-clear]")) { document.getElementById("sky-q").value = ""; return skyAsk(""); }
        const row = e.target.closest("[data-go]");
        if (row) skySelect(SKY.answerRows[+row.dataset.go].thing);
    });
    document.getElementById("sky-info").addEventListener("click", e => {
        if (e.target.closest("[data-close]")) return skySelect(null);
        const f = e.target.closest("[data-fact]"), s = e.target.closest("[data-star]");
        if (f) skySelect(SKY.byAddr.get(f.dataset.fact) || null);
        if (s) skySelect(SKY.starById.get(s.dataset.star) || null);
    });
    document.addEventListener("keydown", e => {
        if (e.key === "Escape" && document.body.classList.contains("sky-on")) skySelect(null);
    });
    window.addEventListener("resize", () => { if (document.body.classList.contains("sky-on")) skyFrame(); });
}

async function loadSky() {
    window.scrollTo(0, 0);
    skyFrame();
    const loading = document.getElementById("sky-loading");
    loading.hidden = false;
    loading.textContent = "raising the city, lighting the sky…";
    const [, data] = await Promise.all([skyLib(), api("/sky")]);
    if (!SKY.built) skyBuild();
    skyControls();
    document.getElementById("sky-drift").checked = SKY.drift;
    SKY.data = data;
    skyWorld(data);
    SKY.selected = null;
    const q = SKY.asked && SKY.asked.q;
    SKY.asked = null;
    const c = data.city ? data.city.counts : null, n = data.nebula ? data.nebula.counts : null;
    document.getElementById("sky-stats").innerHTML =
        (c ? `<span class="left">${c.floors} facts in ${c.towers} towers</span>` : `<span class="warn">no city: ${escapeHtml(data.failed.city || "")}</span>`) +
        " · " + (n ? `<span class="right">${n.cores + n.satellites} memories in the sky</span>` : `<span class="warn">no sky: ${escapeHtml(data.failed.nebula || "")}</span>`);
    if (q) await skyAsk(q); else skyLook();
    if (!SKY.fitted) { SKY.fitted = true; skyFit(); Object.assign(SKY.orbit, SKY.goal, { r: SKY.goal.r * 1.5 }); }
    loading.hidden = true;
    if (!SKY.running) { SKY.running = true; SKY.last = 0; requestAnimationFrame(skyTick); }
}

function skyPause() { SKY.running = false; }
