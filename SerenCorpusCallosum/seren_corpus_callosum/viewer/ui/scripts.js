// ── SerenCorpusCallosum viewer — leaf logic ──────────────────────────────────
// Snaps onto the SerenMeninges shell. The shell ALREADY provides api(),
// escapeHtml(), showTab(), and the bearer-token modal — this file CALLS them,
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
    return `<div class="card"><div class="hit">
        <div class="score">
          <span class="rank"><span class="h">#</span>${h.store_rank}</span>
          <span class="badge ${storeClass(h.store)}">${escapeHtml(h.store)}</span>
        </div>
        <div>
          <div class="v">${escapeHtml(h.content)}</div>
          <div class="foot">
            <span>rrf ${rrf}</span>
            ${foot}
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
    if (!name || !url) { showErr("name and url are required"); return; }
    try {
        await api("/stores", { method: "POST", body: JSON.stringify({ name, type, url, weight, floor }) });
        document.getElementById("addForm").style.display = "none";
        document.getElementById("addName").value = "";
        document.getElementById("addUrl").value = "";
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
            <div class="nm"><span class="badge ${cls}">${escapeHtml(s.type)}</span>${escapeHtml(s.name)}</div>
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
    if (tab === "stores") loadStores();
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
    else if (id === "overview") loadOverview();
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
