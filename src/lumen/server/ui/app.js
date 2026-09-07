/* Lumen dashboard — vanilla JS, no build step. Talks to the local JSON API. */
"use strict";

const S = { state: null, page: "dashboard", draft: null, wizard: null, error: null, testColor: {}, quiet: 0, update: null, feed: "", drag: null };
const DEF_PALETTE = { running: [255, 180, 0], input: [255, 0, 0], done: [0, 255, 0] };
const STATUS_LABEL = { running: "working", input: "needs you", done: "done" };
const basename = p => String(p || "").replace(/[\\/]+$/, "").split(/[\\/]/).pop();
// Long working directories are noise; the last two segments say where you are,
// and the full path is still on the title attribute.
const shortPath = p => { const parts = String(p || "").replace(/[\\/]+$/, "").split(/[\\/]/); return parts.length > 2 ? "…\\" + parts.slice(-2).join("\\") : p; };
const PRESETS = ["#5fe36a", "#ffc23d", "#ff5d5d", "#5b9dff", "#4dd0e1", "#c084fc", "#ffffff"];
// Feed filters. Each is a prefix test on the event type, so a new event family
// falls into "Everything else" instead of disappearing.
const FEED_FILTERS = [["", "All"], ["agent", "Agents"], ["build,deploy", "Builds"], ["command,timer", "Commands"], ["other", "Everything else"]];
const feedMatch = (type, filter) => {
  if (!filter) return true;
  if (filter === "other") return !FEED_FILTERS.slice(1, -1).some(([f]) => feedMatch(type, f));
  return filter.split(",").some(p => type === p || type.startsWith(p + "."));
};
const THEMES = [["auto", "Match my system"], ["dark", "Always dark"], ["light", "Always light"]];
// The agents Lumen can tell apart. A device can be pointed at one of them
// ("Codex on the keyboard, Claude on the light bar") or at all of them.
const AGENTS = [["claude", "Claude"], ["codex", "Codex"]];
const agentName = a => (AGENTS.find(([id]) => id === a) || [a, "every agent"])[1];
function applyTheme(name) {
  try { localStorage.setItem("lumen-theme", name); } catch (e) { /* private window */ }
  document.documentElement.dataset.theme = name === "auto" ? "" : name;
}
function currentTheme() {
  try { return localStorage.getItem("lumen-theme") || "auto"; } catch (e) { return "auto"; }
}
const KIND_ICON = {
  keyboard: '<rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M8 14h8"/>',
  lightbar: '<rect x="2" y="9" width="20" height="6" rx="3"/><path d="M6 5v-1M12 5v-1M18 5v-1M6 20v-1M12 20v-1M18 20v-1"/>',
  strip: '<path d="M3 12c3-4 6-4 9 0s6 4 9 0"/><path d="M6 8v1M12 6v1M18 8v1M6 16v1M12 18v1M18 16v1"/>',
  light: '<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-4 10.5c.8.8 1 1.5 1 2.5h6c0-1 .2-1.7 1-2.5A6 6 0 0 0 12 3z"/>',
  mouse: '<rect x="6" y="3" width="12" height="18" rx="6"/><path d="M12 7v3"/>',
  headset: '<path d="M4 14v-2a8 8 0 0 1 16 0v2"/><rect x="3" y="13" width="4" height="7" rx="1.5"/><rect x="17" y="13" width="4" height="7" rx="1.5"/>',
  screen: '<rect x="2" y="4" width="20" height="13" rx="2"/><path d="M8 21h8M12 17v4"/>',
  notification: '<path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4z"/><path d="M10 21h4"/>',
  sound: '<path d="M4 9v6h4l5 4V5L8 9z"/><path d="M16 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11"/>',
  other: '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/>',
};
const TYPE_TONE = t => /fail|error|needs_input/.test(t) ? "err" : /success|succeeded|finished|done/.test(t) ? "ok" : /running|status/.test(t) ? "warn" : "";

// ---------- helpers ----------
const h = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const $ = sel => document.querySelector(sel);
// A value dropped inside a JS string literal inside an onclick="" attribute is
// parsed twice: the HTML parser decodes entities, then JS reads the literal. So
// escape for JS first and for HTML second — h() alone turns ' into &#39;, which
// the HTML parser hands back to JS as a quote that ends the string.
const js = v => h(String(v ?? "").replace(/[\\'"]/g, c => "\\" + c).replace(/\r?\n/g, "\\n"));
const hex = rgb => rgb ? "#" + rgb.map(c => Math.max(0, Math.min(255, c | 0)).toString(16).padStart(2, "0")).join("") : "#000000";
const rgb = hx => [1, 3, 5].map(i => parseInt(hx.slice(i, i + 2), 16));
const ago = ts => { const s = Math.max(0, (Date.now() / 1000 - ts) | 0); return s < 60 ? `${s}s` : s < 3600 ? `${(s / 60) | 0}m` : `${(s / 3600) | 0}h`; };
const clock = ts => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const uptime = s => s < 3600 ? `${(s / 60) | 0}m` : s < 86400 ? `${(s / 3600) | 0}h ${((s % 3600) / 60) | 0}m` : `${(s / 86400) | 0}d ${((s % 86400) / 3600) | 0}h`;
const label = type => (S.state?.catalog?.[type]?.label) || type;

async function api(method, path, body) {
  // A stalled request must not wedge the poll loop, so every call has a deadline.
  const ctl = new AbortController();
  const deadline = setTimeout(() => ctl.abort(), 8000);
  try {
    const r = await fetch(path, { method, signal: ctl.signal, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || r.statusText);
    return data;
  } finally {
    clearTimeout(deadline);
  }
}
function closeMenus() { document.querySelectorAll(".menu-wrap.open").forEach(m => m.classList.remove("open")); }
function toast(text, err) {
  const el = document.createElement("div");
  el.className = "toast" + (err ? " err" : "");
  el.textContent = text;
  $("#toast-root").appendChild(el);
  setTimeout(() => el.remove(), err ? 6000 : 3200);
}
async function act(fn, ok) {
  try { const r = await fn(); if (ok) toast(typeof ok === "function" ? ok(r) : ok); await refresh(); pollSoon(); return r; }
  catch (e) { toast(e.message, true); }
}

// ---------- state ----------
async function refresh() {
  try {
    S.state = await api("GET", "/api/state");
    S.error = null;
  } catch (e) {
    S.error = e.message;
  }
  // Re-render only when something visible changed, and never while a menu is open
  // (a rebuild would close it under the cursor). The sidebar clock updates regardless.
  const st = S.state;
  const sig = st && JSON.stringify([S.page, S.feed, st.devices, st.integrations, st.rules, st.presets, st.activity, st.messages, st.paused, st.scanning, st.settings, st.onboarded, st.sessions, st.forgotten_devices, st.quiet_now, S.error]);
  if (sig === S.sig || document.querySelector(".menu-wrap.open")) { S.quiet++; renderSidebar(); return; }
  S.quiet = 0;
  S.sig = sig;
  render();
}
// A switch that says what it is to a screen reader, not just a coloured pill.
function toggleBtn(on, onclick, label) {
  return `<button class="toggle ${on ? "on" : ""}" role="switch" aria-checked="${on}" aria-label="${h(label)}" title="${h(label)}" onclick="${onclick}"></button>`;
}

function renderSidebar() {
  const st = S.state, ds = $("#daemon-status");
  if (S.error || !st) {
    ds.innerHTML = `<span class="dot off"></span><span>Not connected</span>`;
    return;
  }
  ds.innerHTML = `<span class="dot ${st.paused ? "warn" : "on"}"></span><span>${st.paused ? "Paused" : "Running"} · ${uptime(st.uptime_s)}</span>`;
  $("#version").textContent = "v" + st.version;
}
function effectsFor(device) {
  const E = S.state.effects, caps = new Set(device.capabilities);
  return Object.entries(E).filter(([k, e]) => caps.has(e.needs) || (e.needs === "color" && caps.has("brightness") && k !== "wave"))
    .filter(([k]) => !(k === "wave" && !caps.has("zones")))
    .map(([k, e]) => ({ id: k, ...e }));
}
function deviceById(id) { return S.state.devices.find(d => d.id === id); }

// ---------- rendering ----------
function render() {
  const st = S.state;
  document.querySelectorAll("#nav a").forEach(a => a.classList.toggle("active", a.dataset.page === S.page));
  renderSidebar();
  if (!st) { $("#main").innerHTML = `<div class="card" style="margin-top:60px"><div class="empty"><b>Can't reach Lumen</b>The background app doesn't seem to be running. Start it from the tray icon, or run <span class="mono">lumen start</span>.<br><span class="mono dim">${h(S.error || "")}</span></div></div>`; return; }
  if (document.activeElement && $("#main").contains(document.activeElement) && ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)) return;
  const pages = { dashboard: renderDashboard, devices: renderDevices, automations: renderAutomations, integrations: renderIntegrations, effects: renderEffects, settings: renderSettings };
  $("#main").innerHTML = (pages[S.page] || renderDashboard)(st);
  if (!st.onboarded && !S.wizard && !$("#modal-root").children.length) openWizard();
}

function swatch(d) {
  const zones = (d.zone_colors || []).filter(Boolean);
  if (zones.length > 1) return `<div class="swatch zoned ${zones.some(z => z.some(c => c > 0)) ? "lit" : ""}" title="${h(d.kind)} · ${zones.length} zones">${zones.map(z => `<i style="background:${hex(z)}"></i>`).join("")}</div>`;
  const color = d.color && d.color.some(c => c > 0) ? hex(d.color) : null;
  return `<div class="swatch ${color ? "lit" : ""}" title="${h(d.kind)}">${color ? `<div class="glow" style="background:${color}"></div>` : ""}<svg viewBox="0 0 24 24">${KIND_ICON[d.kind] || KIND_ICON.other}</svg></div>`;
}
function testMenu(d) {
  const effs = effectsFor(d);
  if (!effs.length) return "";
  return `<div class="menu-wrap"><button class="btn sm" onclick="L.menu(this)">Test <svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg></button>
    <div class="menu"><label class="menu-color"><input type="color" class="color-input" value="${S.testColor[d.id] || "#5fe36a"}" oninput="L.testColor('${js(d.id)}', this.value)"><span>Test color</span></label><div class="sep"></div>${effs.filter(e => e.id !== "sessions" && e.id !== "sound").map(e => `<button onclick="L.test('${js(d.id)}','${js(e.id)}')">${h(e.label)}</button>`).join("")}${effs.some(e => e.id === "sound")
      ? soundChoices().map(([n, label]) => `<button onclick="L.test('${js(d.id)}','sound','${js(n)}')">${h(label)}</button>`).join("") : ""}</div></div>`;
}

function renderDashboard(st) {
  const on = st.devices.filter(d => d.connected && d.details.enabled !== false);
  const rules = st.rules.filter(r => r.enabled);
  const sess = st.sessions;
  const busy = sess.filter(s => s.status === "running").length;
  const needs = sess.filter(s => s.status === "input").length;

  // One plain sentence at the top: what is Lumen doing for you right now?
  let tone = "", headline = "Everything is running";
  if (st.paused) { tone = "paused"; headline = "Lumen is paused"; }
  else if (needs) { tone = "attn"; headline = `${needs} ${needs === 1 ? "task is" : "tasks are"} waiting for you`; }
  else if (busy) { tone = "busy"; headline = `${busy} ${busy === 1 ? "task is" : "tasks are"} working`; }
  const sub = st.paused
    ? "Your devices are back under their own control."
    : `${on.length} of ${st.devices.length} devices connected · ${rules.length} automation${rules.length === 1 ? "" : "s"} on · running for ${uptime(st.uptime_s)}`;

  return `
  <div class="page-head"><div><h1>Dashboard</h1><p>What your devices are doing right now.</p></div></div>

  <div class="card section"><div class="hero ${tone}">
    <div class="hero-orb"><i></i></div>
    <div class="hero-text"><b>${h(headline)}</b><span>${h(sub)}</span></div>
    <div class="hero-actions">
      <button class="btn" onclick="L.emit('agent.finished', {agent: 'claude'})" title="Fires an agent.finished event">Test my lights</button>
      <button class="btn ${st.paused ? "primary" : ""}" onclick="L.pause(${!st.paused})">${st.paused ? "Resume" : "Pause"}</button>
    </div>
  </div></div>

  ${st.devices.length ? "" : `<div class="card section"><div class="empty"><b>No lights found yet</b>
    Plug in supported hardware, or install OpenRGB for gaming peripherals, then scan.<br>
    <a class="btn primary mt" href="#devices" style="display:inline-flex">Go to Devices</a></div></div>`}

  ${liveBoard(st)}

  <div class="two-col">
    <div>
      ${sessionList(st)}
      ${deviceStrip(st)}
    </div>
    <div class="rail">
      <div class="section"><div class="section-head"><h2>Activity</h2>
        <span class="spacer"></span>
        ${st.activity.length ? `<button class="btn sm ghost" onclick="L.clearFeed()">Clear</button>` : ""}</div>
        ${st.activity.length ? `<div class="filters" role="group" aria-label="Filter the feed">${FEED_FILTERS.map(([f, name]) => `<button class="chip-btn ${S.feed === f ? "on" : ""}" aria-pressed="${S.feed === f}" onclick="L.feed('${f}')">${h(name)}</button>`).join("")}</div>` : ""}
        ${(() => {
          const shown = collapse(st.activity.filter(a => feedMatch(a.type, S.feed))).slice(0, 12);
          return `<div class="card">${shown.length ? `<div class="feed">${shown.map(a => feedItem(a)).join("")}</div>`
            : `<div class="empty"><b>${st.activity.length ? "Nothing here" : "Nothing yet"}</b>${st.activity.length ? "No recent events match this filter." : "Events from your agents, builds and scripts show up here."}</div>`}</div>`;
        })()}
      </div>
      <div class="section"><div class="section-head"><h2>Your automations</h2><span class="count">${rules.length}/${st.rules.length}</span><span class="spacer"></span><a class="more" href="#automations">Manage →</a></div>
        <div class="card">${st.rules.length ? `<div class="rows">${st.rules.slice(0, 6).map(r => `<div class="row compact">
          <div class="row-main"><div class="row-title">${h(r.name || "Untitled")}</div>
          <div class="row-sub">${ruleSentence(r)}</div></div>
          ${toggleBtn(r.enabled, `L.toggleRule('${js(r.id)}')`, `${r.enabled ? "On" : "Off"}: ${r.name || "automation"}`)}</div>`).join("")}</div>`
          : `<div class="empty"><b>No automations yet</b><a href="#automations">Create one →</a></div>`}</div>
      </div>
      ${st.messages.length ? `<div class="section"><div class="section-head"><h2>Notes</h2></div><div class="card"><div class="feed">${st.messages.map(m => `<div class="feed-item"><span class="feed-time">${clock(m.ts)}</span><span class="feed-body small muted">${h(m.text)}</span></div>`).join("")}</div></div></div>` : ""}
    </div>
  </div>`;
}

// Mirrors effects.spread_zones on the daemon side: the tabs that are open widen
// until they cover the device, so one tab lights the whole keyboard, two split
// it in half, and three across four zones take 2 / 1 / 1. A lit zone beside a
// dark one reads as broken hardware rather than as a free seat.
function spreadSeats(list, n) {
  const used = list.filter(z => z.tab);
  if (!used.length || used.length === n) return list;
  const out = [];
  used.forEach((seat, i) => out.push(...Array((n / used.length | 0) + (i < n % used.length ? 1 : 0)).fill(seat)));
  return out;
}

// Which slot each zone of each device is showing, worked out from the enabled
// `sessions` actions rather than guessed. It has to be read off the rules: a
// rule can point the light bar at slot 5 so it carries on where a 4-zone
// keyboard stops, and a tab in a slot no rule covers reaches no hardware at
// all — the one thing this page must not hide.
function zoneLayout(st) {
  const zones = new Map();     // device id -> [session or null shown by zone 0, zone 1, ...]
  const agentOf = new Map();   // device id -> the agent it is filtered to ("" = all of them)
  const folded = new Map();    // device id -> agent, for devices showing one colour for all its tabs
  // A device pointed at one agent counts that agent's tabs from its own zone 1,
  // exactly as effects.agent_sessions re-ranks them before painting.
  const ranked = agent => {
    const mine = st.sessions.filter(s => !agent || s.agent === agent).sort((a, b) => (a.slot || 0) - (b.slot || 0));
    return agent ? mine.map((s, i) => ({ ...s, slot: i })) : mine;
  };
  for (const r of st.rules) {
    if (!r.enabled) continue;
    for (const a of r.actions) {
      if (a.effect !== "sessions") continue;
      for (const d of st.devices) {
        if (a.device !== "*" && a.device !== d.id) continue;
        if (!d.connected || d.details.enabled === false) continue;
        agentOf.set(d.id, a.agent || "");
        // Whole-device layout, or nowhere to put a second tab: one colour for the lot.
        if (a.per_zone === false || d.zones < 2) { folded.set(d.id, a.agent || ""); zones.delete(d.id); continue; }
        folded.delete(d.id);
        const tabs = ranked(a.agent || ""), off = a.offset || 0;
        const no = new Map(tabs.map((t, i) => [t.id, i + 1]));   // the tab's place in the list, for the label
        const list = Array.from({ length: d.zones }, (_, z) => ({ tab: tabs.find(t => (t.slot || 0) - off === z) || null }));
        // Past the last zone, a busy tab borrows a free zone, else the last idle one (effects.session_zones).
        const busy = t => t && (t.status === "input" || t.status === "running");
        for (const t of tabs.filter(t => (t.slot || 0) - off >= d.zones).filter(busy).sort((x, y) => (x.status === "input" ? 0 : 1) - (y.status === "input" ? 0 : 1))) {
          const spare = list.map((z, i) => busy(z.tab) ? -1 : i).filter(i => i >= 0);
          if (!spare.length) break;
          const free = spare.filter(i => !list[i].tab);
          list[(free.length ? free : spare).pop()].tab = t;
        }
        zones.set(d.id, spreadSeats(list, d.zones).map(seat => ({ ...seat, n: seat.tab ? no.get(seat.tab.id) : 0 })));
      }
    }
  }
  const placed = new Set();    // sessions that reach a zone of their own somewhere
  zones.forEach(list => list.forEach(s => s.tab && placed.add(s.tab.id)));
  // Tabs on a device that shows them all in one colour: lit, but with no zone to arrange.
  const foldedIds = new Map(); // session id -> the device showing it that way
  folded.forEach((agent, deviceId) => st.sessions.forEach(s => {
    if ((!agent || s.agent === agent) && !placed.has(s.id)) foldedIds.set(s.id, deviceId);
  }));
  return { zones, agentOf, folded, placed, foldedIds };
}
// Which agent's tabs a device is showing, read off the enabled `sessions`
// actions the same way zoneLayout does. "" = every agent, "off" = no automation
// puts agent tabs on this device at all.
function deviceAgent(st, id) {
  const act = deviceSessionAction(st, id);
  return act ? (act.agent || "") : "off";
}
function deviceSessionAction(st, id) {
  const acts = pick => st.rules.filter(r => r.enabled).flatMap(r => r.actions).filter(a => a.effect === "sessions" && pick(a.device));
  return acts(d => d === id)[0] || acts(d => d === "*" || d === "all" || d === "")[0] || null;
}
// A device with zones can give each tab one, or show a single colour for all of
// them. Without this on the Devices page the choice is buried in the automation
// editor, which is where the light bar looked like it had no settings at all.
function deviceLayout(st, id) {
  const act = deviceSessionAction(st, id);
  return act ? act.per_zone !== false : true;
}
const sessionName = s => s.label || basename(s.cwd) || ((s.agent === "codex" ? "Codex" : "Claude") + " tab");

// The live board is the point of the whole app: what colour is on the hardware
// this second. Big, on its own, above everything else.
function liveBoard(st) {
  const lit = st.devices.filter(d => d.details.enabled !== false && d.connected && (d.color || (d.zone_colors || []).length));
  if (!lit.length) return "";
  const { zones, agentOf } = zoneLayout(st);
  return `<div class="section"><div class="section-head"><h2>Lights right now</h2></div>
    <div class="card"><div class="board">
      ${lit.map(d => {
        const zs = (d.zone_colors || []).length ? d.zone_colors : [d.color || [0, 0, 0]];
        const seats = zones.get(d.id);
        const whose = agentOf.get(d.id) ? `${agentName(agentOf.get(d.id))} tabs` : "agent tabs";
        const shape = zs.length < 2 ? " solid" : d.kind === "keyboard" ? " keys" : " bar";
        const owners = seats ? new Set(seats.filter(s => s.tab).map(s => s.tab.id)).size : 0;
        const sub = zs.length < 2 ? "single colour"
          : owners ? `${zs.length} zones · ${owners === 1 ? `one ${whose.slice(0, -1)} across all of them` : `${owners} ${whose}, sharing them`}`
          : `${zs.length} zones`;
        // Zones the same tab owns are drawn as one block, because that is what the
        // hardware shows: two halves of a keyboard, not four squares of two colours.
        const cells = [];
        zs.forEach((z, i) => {
          const seat = seats ? seats[i] : null, last = cells[cells.length - 1];
          if (last && seat && last.seat?.tab && seat.tab && last.seat.tab.id === seat.tab.id) { last.span++; return; }
          cells.push({ z, i, seat, span: 1 });
        });
        return `<div class="board-dev">
          <div class="board-label"><b>${h(d.name)}</b><span>${h(sub)}</span></div>
          <div class="zones${shape}">${cells.map(({ z, i, seat, span }) => {
            const dark = !z || !z.some(c => c > 24);
            const owner = seat && seat.tab;
            const where = span > 1 ? `Zones ${i + 1}–${i + span}` : `Zone ${i + 1}`;
            const title = !seat ? where
              : owner ? `${where} · ${whose.slice(0, -1)} ${seat.n}: ${sessionName(owner)} (${STATUS_LABEL[owner.status] || owner.status})`
              : `${where} · nothing open`;
            return `<i class="${dark ? "dark" : ""}" style="background:${dark ? "var(--panel-2)" : hex(z)};flex:${span}" title="${h(title)}">${zs.length > 1
              ? `<b>${owner ? seat.n : i + 1}</b>${owner ? `<em>${h(sessionName(owner))}</em>` : ""}` : ""}</i>`;
          }).join("")}</div></div>`;
      }).join("")}
      <div class="board-legend">
        <span><i class="swatch-sm" style="background:#fbbf24"></i>working</span>
        <span><i class="swatch-sm" style="background:#f87171"></i>needs you</span>
        <span><i class="swatch-sm" style="background:#4ade80"></i>done</span>
        <span class="dim">Every block is one agent tab. Open tabs share the device between them.</span>
      </div>
    </div></div></div>`;
}

// Tabs are listed in zone order and can be dragged into the order you think in
// ("zone 1 is the API refactor"). The colour on the slot number is the colour
// that zone is actually showing, so you can see what you are moving. Tabs past
// the last zone are listed too, under the reason they are dark: a tab that
// silently reaches no hardware is what makes this page feel broken.
function sessionList(st) {
  const sess = st.sessions;
  const { zones, agentOf, placed: onZoneIds, foldedIds } = zoneLayout(st);
  const onHardware = new Set([...onZoneIds, ...foldedIds.keys()]);
  const onZone = sess.filter(s => onZoneIds.has(s.id));
  const foldedTabs = sess.filter(s => foldedIds.has(s.id));
  const offZone = sess.filter(s => !onHardware.has(s.id));
  // Only zones a homeless tab could actually land on: a keyboard filtered to
  // Claude is no help to a Codex tab, and counting it makes the advice a lie.
  const seats = [...zones].reduce((n, [id, list]) =>
    n + (offZone.some(s => !agentOf.get(id) || s.agent === agentOf.get(id)) ? list.length : 0), 0);
  // The colour of the zone this tab is actually showing on, wherever that is.
  const zoneColor = id => {
    for (const [deviceId, list] of zones) {
      const at = list.findIndex(seat => seat.tab && seat.tab.id === id);
      const z = at < 0 ? null : (st.devices.find(d => d.id === deviceId)?.zone_colors || [])[at];
      if (z && z.some(c => c > 0)) return hex(z);
    }
    return "";
  };
  const row = s => {
    // Position in the whole list, not in its group: the arrows move a tab across
    // the on-zone / off-zone boundary, so only the real ends are dead ends.
    const i = sess.indexOf(s);
    const name = sessionName(s);
    const lit = zoneColor(s.id);
    const placed = onZoneIds.has(s.id);
    const sharing = foldedIds.get(s.id);
    return `<div class="row session${placed || sharing ? "" : " off-zone"}" draggable="true" data-sid="${h(s.id)}" role="listitem"
      ondragstart="L.dragStart(event,'${js(s.id)}')" ondragover="L.dragOver(event)" ondrop="L.drop(event,'${js(s.id)}')" ondragend="L.dragEnd()">
      <span class="grip" aria-hidden="true" title="Drag to reorder">⠿</span>
      <span class="slot ${placed || sharing ? h(s.status) : "unplaced"}" title="${placed ? `Tab ${i + 1}, left to right on your devices`
      : sharing ? `Shown on ${deviceById(sharing)?.name || sharing}, in one colour with the other tabs` : `Tab ${i + 1}. Every zone is taken by a tab further up`}" ${lit ? `style="box-shadow:0 0 0 2px ${lit} inset"` : ""}>${i + 1}</span>
      <div class="row-main">
        <div class="row-title"><input class="name-edit" value="${h(name)}" aria-label="Name for this tab"
          title="Rename this tab" onchange="L.labelSession('${js(s.id)}', this.value)"></div>
        <div class="row-sub">${h(s.agent)} · ${s.started ? "open for " + ago(s.started) : "tracked from the agent's own record"}${s.ts ? " · updated " + ago(s.ts) + " ago" : ""}</div>
        ${s.cwd ? `<div class="row-sub tech" title="${h(s.cwd)}">${h(shortPath(s.cwd))}</div>` : ""}</div>
      <div class="row-actions"><span class="pill ${h(s.status)}">${STATUS_LABEL[s.status] || h(s.status)}</span>
        <button class="btn sm ghost icon" aria-label="Move ${h(name)} earlier" title="Move earlier" ${i ? "" : "disabled"} onclick="L.moveSession('${js(s.id)}', -1)">▲</button>
        <button class="btn sm ghost icon" aria-label="Move ${h(name)} later" title="Move later" ${i === sess.length - 1 ? "disabled" : ""} onclick="L.moveSession('${js(s.id)}', 1)">▼</button>
        <button class="btn sm ghost icon" aria-label="Forget ${h(name)}" title="Forget this tab. It comes back if the tab is still open." onclick="L.forget('${js(s.id)}')">✕</button></div></div>`;
  };
  const group = list => `<div class="rows" role="list">${list.map(row).join("")}</div>`;
  return `<div class="section"><div class="section-head"><h2>Open agent tabs</h2><span class="count">${sess.length}</span>${sess.length > 1
    ? `<span class="spacer"></span><span class="more dim">drag to reorder</span>` : ""}</div>
    <div class="card">${sess.length ? `${group(onZone)}${foldedTabs.length ? `<div class="rows-note">${(() => {
      const dev = deviceById([...foldedIds.values()][0]);
      return `<b>${foldedTabs.length === 1 ? "This tab shares" : `These ${foldedTabs.length} tabs share`} ${dev ? h(dev.name) : "one device"}.</b>
      It shows a single colour for all of them, so there is nothing to arrange here.${dev && dev.zones > 1
        ? ` Switch it to one zone per tab on the <a href="#devices">Devices page</a> to give each its own colour.`
        : ` It has one zone, so it can only ever show the busiest of them.`}`;
    })()}</div>${group(foldedTabs)}` : ""}${offZone.length ? `<div class="rows-note">${seats ? `<b>${offZone.length} more ${offZone.length === 1 ? "tab is" : "tabs are"} open with no zone left.</b>
      Your devices cover ${seats} ${seats === 1 ? "zone" : "zones"}, and the tabs above have taken all of them. Drag one of these up to swap it in, or close the tabs you are done with.`
      : `<b>No automation puts these tabs on a device.</b>
      They are still tracked, but nothing lights up per tab until an automation shows agent status on a device with more than one zone. <a href="#automations">Set one up →</a>`}</div>${group(offZone)}` : ""}`
      : `<div class="empty"><b>No agent tabs open</b>Open a Claude Code or Codex tab. One tab lights the whole keyboard; open a second and they take half each.</div>`}</div></div>`;
}

// A glance, not the Devices page: one line per device with its live colour.
function deviceStrip(st) {
  if (!st.devices.length) return "";
  return `<div class="section"><div class="section-head"><h2>Devices</h2><span class="count">${st.devices.length}</span>${st.scanning ? '<span class="spinner"></span>' : ""}<span class="spacer"></span><a class="more" href="#devices">Manage →</a></div>
    <div class="card"><div class="rows">${st.devices.map(d => `<div class="row compact ${d.details.enabled === false ? "disabled" : ""}">${swatch(d)}
      <div class="row-main"><div class="row-title">${h(d.name)}</div>
      <div class="row-sub">${d.details.enabled === false ? "Switched off" : d.connected ? "Connected" : "Not responding"}${d.zones > 1 ? ` · ${d.zones} zones` : ""}</div></div>
      <div class="row-actions"><span class="dot ${d.details.enabled === false ? "" : d.connected ? "on" : "off"}"></span>${testMenu(d)}</div></div>`).join("")}</div></div></div>`;
}

// The same event can fire many times in a row (a busy agent reports every turn).
// Show it once with a count instead of ten identical lines.
function collapse(activity) {
  const out = [];
  for (const a of activity) {
    const prev = out[out.length - 1];
    if (prev && prev.type === a.type && JSON.stringify(prev.rules) === JSON.stringify(a.rules)) { prev.repeats = (prev.repeats || 1) + 1; continue; }
    out.push({ ...a });
  }
  return out;
}

function feedItem(a) {
  const data = Object.entries(a.data || {}).filter(([k]) => k !== "agents").map(([k, v]) => k === "sessions" ? `${v.length} tab${v.length === 1 ? "" : "s"}` : `${k}: ${v}`).join(" · ");
  return `<div class="feed-item"><span class="feed-icon ${TYPE_TONE(a.type)}"><i></i></span><div class="feed-body">
    <span class="feed-title" title="${h(a.type)}">${h(label(a.type))}${a.repeats ? ` <span class="dim small">×${a.repeats}</span>` : ""}</span>
    <span class="feed-rules">${a.rules?.length ? "ran <b>" + a.rules.map(h).join("</b>, <b>") + "</b>" : "no automation matched"}${data ? " · " + h(data) : ""}</span>
    </div><span class="feed-time" title="${clock(a.ts)}">${ago(a.ts)}</span></div>`;
}

const CAP_LABEL = { color: "colour", zones: "separate zones", brightness: "brightness", notify: "notifications", sound: "sound" };
// The sound adapter publishes its tones in details.sounds, so adding one there is
// enough to make it selectable here. The fallback covers a daemon older than this UI.
const soundChoices = () => {
  const d = (S.state?.devices || []).find(x => x.details?.sounds?.length);
  const labels = d?.details?.sound_labels || {};
  return (d?.details?.sounds || ["default"]).map(n => [n, labels[n] || n]);
};
// A rule saved before the tones existed holds a name like "default". The daemon still
// resolves it, so keep it in the list rather than silently showing the wrong option.
const soundOptions = sel => {
  const opts = soundChoices();
  if (sel && !opts.some(([n]) => n === sel)) opts.unshift([sel, `${sel} (from an older version)`]);
  return opts.map(([n, label]) => `<option value="${h(n)}" ${sel === n ? "selected" : ""}>${h(label)}</option>`).join("");
};
// Prefill the device-request issue with what we detected, so a report is one click.
const deviceIssueUrl = d => "https://github.com/Brxerq/lumen/issues/new?template=device_request.yml&title="
  + encodeURIComponent(`[device] ${d.vendor ? d.vendor + " " : ""}${d.name}`)
  + "&labels=device&body=" + encodeURIComponent(`Detected as \`${d.id}\` (${d.kind}).\n\n\`\`\`\n${JSON.stringify(d.details, null, 2)}\n\`\`\`\n\nWhat worked, and what didn't:\n`);

function renderDevices(st) {
  return `
  <div class="page-head"><div><h1>Devices</h1><p>Everything Lumen can light up on this machine and your network.</p></div>
    <div class="page-actions"><button class="btn" onclick="L.scan()" ${st.scanning ? "disabled" : ""}>${st.scanning ? '<span class="spinner"></span> Scanning' : "Scan again"}</button></div></div>
  <div class="card">${st.devices.length ? `<div class="rows">${st.devices.map(d => {
    // Scalars only: an adapter is free to publish a list or a map here (the sound
    // adapter publishes its tone labels), and those stringify to "[object Object]".
    const tech = Object.entries(d.details)
      .filter(([k, v]) => !["enabled", "setup", "hint"].includes(k) && v !== null && typeof v !== "object")
      .map(([k, v]) => `${h(k)} ${h(v)}`).join(" · ");
    const lights = d.ambient !== false && d.capabilities.some(c => c === "color" || c === "zones" || c === "brightness");
    const sounds = d.capabilities.includes("sound");
    const shows = lights ? deviceAgent(st, d.id) : "off";
    return `<div class="row ${d.details.enabled === false ? "disabled" : ""}">${swatch(d)}
    <div class="row-main">
      <div class="row-title"><input class="name-edit" value="${h(d.name)}" onchange="L.rename('${js(d.id)}', this.value)" title="Click to rename"></div>
      <div class="row-sub">${d.details.enabled === false ? "Switched off" : d.connected ? "Connected" : "Not responding"}${d.vendor ? " · " + h(d.vendor) : ""} · ${h(d.kind)}${d.zones > 1 ? ` · ${d.zones} zones` : ""}${d.capabilities.length ? " · can do " + d.capabilities.map(c => CAP_LABEL[c] || c).join(", ") : " · needs setup"}</div>
      ${d.details.hint ? `<div class="row-sub" style="color:var(--amber)">${h(d.details.hint)}</div>` : ""}
      ${d.details.last_error ? `<div class="row-sub" style="color:var(--red)">Last error: ${h(d.details.last_error)}</div>` : ""}
      ${d.details.unverified ? `<div class="row-sub" style="color:var(--amber)">Unverified model. Test each zone, then <a href="${h(deviceIssueUrl(d))}" target="_blank" rel="noopener" style="color:var(--blue)">report what worked</a> so it can ship as verified.</div>` : ""}
      ${tech ? `<div class="row-sub tech">${tech}</div>` : ""}
    </div>
    <div class="row-actions">
      ${lights || sounds ? `<label class="dimmer" title="${sounds ? "How loud this device plays" : "How bright this device may go"}"><span class="sr-only">${sounds ? "Volume" : "Brightness"} for ${h(d.name)}</span>
        <input type="range" min="0.05" max="1" step="0.05" value="${d.brightness ?? 1}" aria-label="${sounds ? "Volume" : "Brightness"} for ${h(d.name)}" onchange="L.dim('${js(d.id)}', +this.value)">
        <span class="dim small">${Math.round((d.brightness ?? 1) * 100)}%</span></label>` : ""}
      ${lights ? `<label title="Whose agent tabs light up this device"><span class="sr-only">Agent tabs shown on ${h(d.name)}</span>
        <select class="input sm" onchange="L.deviceAgent('${js(d.id)}', this.value)">
          <option value="" ${shows === "" ? "selected" : ""}>All agent tabs</option>
          ${AGENTS.map(([id, name]) => `<option value="${id}" ${shows === id ? "selected" : ""}>${h(name)} tabs only</option>`).join("")}
          <option value="off" ${shows === "off" ? "selected" : ""}>No agent tabs</option>
        </select></label>
      ${d.zones > 1 && shows !== "off" ? `<label title="Share the zones between the open tabs, or one colour for all of them"><span class="sr-only">Tab layout on ${h(d.name)}</span>
        <select class="input sm" onchange="L.deviceLayout('${js(d.id)}', this.value === '1')">
          <option value="1" ${deviceLayout(st, d.id) ? "selected" : ""}>Split between tabs</option>
          <option value="0" ${deviceLayout(st, d.id) ? "" : "selected"}>One colour for all</option>
        </select></label>` : ""}` : ""}
      <span class="dot ${d.details.enabled === false ? "" : d.connected ? "on" : "off"}" title="${d.connected ? "Connected" : "Not responding"}"></span>
      ${d.details.setup ? `<button class="btn sm primary" onclick="L.adapter('${js(d.details.setup)}','pair',{bridge:'${js(d.details.bridge || "")}'})">Pair</button>` : ""}
      ${testMenu(d)}
      ${toggleBtn(d.details.enabled !== false, `L.enable('${js(d.id)}', ${d.details.enabled === false})`, `${d.details.enabled === false ? "Switched off" : "On"}: ${d.name}`)}
    </div></div>`;
  }).join("")}</div>`
    : `<div class="empty"><b>${st.scanning ? "Scanning…" : "No devices found"}</b>${st.scanning ? "Checking USB, OpenRGB, your network and this system." : "Plug in supported hardware, install OpenRGB for gaming peripherals, or turn on LAN control for Govee lights, then scan again."}</div>`}</div>
  ${(st.forgotten_devices || []).length ? `<div class="section mt"><div class="section-head"><h2>Not connected right now</h2></div>
    <div class="card"><div class="rows">${st.forgotten_devices.map(id => `<div class="row"><div class="row-main"><div class="row-title mono">${h(id)}</div>
      <div class="row-sub">Lumen still has a name, a dimmer and an on/off switch saved for this one. Plug it back in and they apply again.</div></div>
      <div class="row-actions"><button class="btn sm ghost danger" onclick="L.forgetDevice('${js(id)}')">Forget</button></div></div>`).join("")}</div></div></div>` : ""}
  <div class="section mt"><div class="section-head"><h2>What Lumen supports</h2></div>
    <div class="card"><div class="card-pad row-sub" style="line-height:1.75">ASUS Aura laptops (ROG, TUF, Zephyrus): keyboard zones and light bar · anything OpenRGB drives (Razer, Corsair, Logitech, SteelSeries, MSI, HyperX, strips, RAM…) · Linux keyboard backlights · Philips Hue · Govee (LAN) · screen edge glow · system notifications · system sounds.<br>Missing yours? Adapters are ~100 lines: see <span class="mono">docs/PLUGINS.md</span>.</div></div></div>`;
}

function renderAutomations(st) {
  return `
  <div class="page-head"><div><h1>Automations</h1><p>When something happens, your devices react. Automations run top to bottom, and a colour stays until another one changes it.</p></div>
    <div class="page-actions">
      <button class="btn" onclick="L.exportRules()" ${st.rules.length ? "" : "disabled"}>Export</button>
      <button class="btn" onclick="L.importRules()">Import</button>
      <button class="btn primary" onclick="L.editRule()">+ New automation</button></div></div>
  <div class="card">${st.rules.length ? `<div class="rows">${st.rules.map((r, i) => ruleCard(r, i, st.rules.length)).join("")}</div>`
    : `<div class="empty"><b>No automations yet</b>Create one, or run the setup again from Settings to get the defaults back.</div>`}</div>
  ${st.presets?.length ? `<div class="section mt"><div class="section-head"><h2>Saved effects</h2><span class="count">${st.presets.length}</span></div>
    <div class="card"><div class="rows">${st.presets.map(p => `<div class="row"><div class="row-main"><div class="row-title">${h(p.name)}</div>
      <div class="row-sub">${p.actions.map(a => h((S.state.effects[a.effect]?.label) || a.effect)).join(", ")}. Pick it from the Start from menu when editing an automation.</div></div>
      <div class="row-actions"><button class="btn sm ghost danger" onclick="L.deletePreset('${js(p.id)}')">Delete</button></div></div>`).join("")}</div></div></div>` : ""}`;
}

// Plain English first; the event/effect machinery is folded away behind "details".
function ruleSentence(r) {
  const filters = Object.entries(r.match || {}).filter(([, v]) => v !== "" && v != null).map(([k, v]) => `${k} is ${v}`).join(" and ");
  const when = `<b>${h(label(r.when))}</b>${filters ? ` (only when ${h(filters)})` : ""}`;
  const acts = r.actions.map(a => {
    const dev = a.device === "*" ? "every compatible device" : (deviceById(a.device)?.name || a.device);
    const eff = S.state.effects[a.effect] || { label: a.effect, params: [] };
    const P = p => eff.params.includes(p);
    if (a.effect === "sessions") return `show each ${a.agent ? h(agentName(a.agent)) + " " : "agent "}tab's status on <b>${h(dev)}</b>`;
    if (a.effect === "notify") return `pop up a notification${a.message ? ` saying “${h(a.message)}”` : ""}`;
    if (a.effect === "sound") return `play the <b>${h(a.sound || "default")}</b> sound`;
    const dot = P("color") ? `<span class="swatch-sm" style="background:${hex(a.color)}"></span> ` : "";
    const extra = [P("count") && a.count > 1 ? `${a.count} times` : "", P("duration") ? `for ${a.duration}s` : ""].filter(Boolean).join(", ");
    return `${dot}<b>${h(eff.label.toLowerCase())}</b> ${h(dev)}${extra ? " " + extra : ""}`;
  });
  return `When ${when}, ${acts.join(", and ")}.`;
}

function ruleCard(r, index = 0, total = 1) {
  const filters = Object.entries(r.match || {}).filter(([, v]) => v !== "" && v != null).map(([k, v]) => `<span class="chip">${h(k)} = ${h(v)}</span>`).join("");
  return `<div class="rule ${r.enabled ? "" : "off"}">
    <div><div class="rule-name">${h(r.name || "Untitled")}${r.enabled ? "" : '<span class="chip">off</span>'}</div>
      <div class="rule-plain">${ruleSentence(r)}</div>
      <details class="rule-tech"><summary>Details</summary>
        <div class="ladder">
          <div class="step"><span class="kw when">WHEN</span><span class="chip ev">${h(r.when)}</span>${filters}</div>
          ${r.actions.map((a, i) => `<div class="step"><span class="kw ${i ? "and" : "then"}">${i ? "AND" : "THEN"}</span>${actionChips(a)}</div>`).join("")}
        </div>
      </details></div>
    <div class="row-actions">
      ${total > 1 ? `<div class="reorder">
        <button class="btn sm ghost icon" aria-label="Move ${h(r.name || "this automation")} earlier" title="Run this one earlier" ${index ? "" : "disabled"} onclick="L.moveRule('${js(r.id)}', -1)">▲</button>
        <button class="btn sm ghost icon" aria-label="Move ${h(r.name || "this automation")} later" title="Run this one later" ${index === total - 1 ? "disabled" : ""} onclick="L.moveRule('${js(r.id)}', 1)">▼</button></div>` : ""}
      <button class="btn sm" onclick="L.preview('${js(r.id)}')" title="Run it now on your devices">Try it</button>
      <button class="btn sm" onclick="L.editRule('${js(r.id)}')">Edit</button>
      <button class="btn sm ghost icon" aria-label="Duplicate ${h(r.name || "this automation")}" title="Duplicate" onclick="L.duplicateRule('${js(r.id)}')">⧉</button>
      <button class="btn sm ghost danger icon" aria-label="Delete ${h(r.name || "this automation")}" title="Delete" onclick="L.deleteRule('${js(r.id)}')">✕</button>
      ${toggleBtn(r.enabled, `L.toggleRule('${js(r.id)}')`, `${r.enabled ? "On" : "Off"}: ${r.name || "automation"}`)}
    </div></div>`;
}
function actionChips(a) {
  const dev = a.device === "*" ? "All compatible devices" : (deviceById(a.device)?.name || a.device);
  const eff = S.state.effects[a.effect] || { label: a.effect, params: [] };
  const hasColor = eff.params.includes("color");
  if (a.effect === "sessions") {
    const pal = a.palette || DEF_PALETTE;
    const who = a.agent ? a.agent[0].toUpperCase() + a.agent.slice(1) : "any agent";
    return `<span class="chip dev">${h(dev)}</span><span class="arrow">→</span><span class="chip">${["running", "input", "done"].map(k => `<span class="swatch-sm" style="background:${hex(pal[k])}" title="${STATUS_LABEL[k]}"></span>`).join("")}${h(eff.label)}</span><span class="muted small">${h(who)} · ${a.per_zone === false ? "whole device" : `split between the tabs, from tab ${(a.offset || 0) + 1}`}</span>`;
  }
  const extra = [eff.params.includes("count") ? `×${a.count}` : "", eff.params.includes("duration") ? `${a.duration}s` : "", a.effect === "notify" && a.message ? `“${a.message}”` : "", a.effect === "sound" ? a.sound : ""].filter(Boolean).join(" · ");
  return `<span class="chip dev">${h(dev)}</span><span class="arrow">→</span><span class="chip">${hasColor ? `<span class="swatch-sm" style="background:${hex(a.color)}"></span>` : ""}${h(eff.label)}</span>${extra ? `<span class="muted small">${h(extra)}</span>` : ""}`;
}

function renderIntegrations(st) {
  return `
  <div class="page-head"><div><h1>Integrations</h1><p>Where events come from. Connect your coding agents, or send events over the webhook or the command line.</p></div></div>
  <div class="card"><div class="rows">${st.integrations.map(i => `<div class="integ">
    <div>
      <div class="row-title"><span class="dot ${i.connected ? "on" : ""}"></span>${h(i.name)}<small>${h(i.detail || "")}</small></div>
      <div class="row-sub mt" style="margin-top:6px">${h(i.description)}</div>
      ${usageMeters(i.usage)}
      <div class="chips mt">${i.events.map(e => `<span class="chip ev">${h(e)}</span>`).join("")}</div>
      ${i.docs ? `<div class="integ-doc">${md(i.docs)}</div>` : ""}
      ${Object.keys(i.option_fields || {}).length ? `<div class="integ-opts">${Object.entries(i.option_fields).map(([k, f]) => `<div class="field"><label>${h(f.label)}</label><input class="input" type="${f.type || "text"}" placeholder="${h(f.placeholder || "")}" value="${h(i.options?.[k] ?? "")}" id="opt-${i.id}-${k}" style="min-width:${f.type === "number" ? 90 : 320}px"></div>`).join("")}
        <button class="btn sm" onclick="L.saveOptions('${js(i.id)}', ${JSON.stringify(Object.keys(i.option_fields)).replace(/"/g, "&quot;")})">Save</button></div>` : ""}
      <div class="msg" id="msg-${i.id}"></div>
    </div>
    <div class="row-actions">${i.can_connect ? (i.connected ? `<button class="btn sm" onclick="L.connect('${js(i.id)}', true)">Disconnect</button>` : `<button class="btn sm primary" onclick="L.connect('${js(i.id)}')">Connect</button>`) : ""}</div>
  </div>`).join("")}</div></div>`;
}
// The 5-hour and 7-day limits an agent integration read with its own login,
// as two thin meters. Grey until it matters, amber past 70 %, red past 90 %.
function usageMeters(u) {
  if (!u || (!u.five_hour && !u.seven_day)) return "";
  const tone = p => p >= 90 ? "var(--red)" : p >= 70 ? "var(--amber)" : "var(--dim)";
  const left = ts => { if (!ts) return ""; const s = Math.max(0, ts - Date.now() / 1000), d = Math.floor(s / 86400), hh = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60); return d ? `${d}d ${hh}h` : hh ? `${hh}h ${String(m).padStart(2, "0")}m` : `${m}m`; };
  const eta = u.eta_full_s ? ` · at this pace, full in ${left(Date.now() / 1000 + u.eta_full_s)}` : "";
  return `<div class="meters">${[["five_hour", "Session · 5 hours", eta], ["seven_day", "Week · 7 days", ""]].filter(([k]) => u[k]).map(([k, name, extra]) => `
    <div class="meter-row"><div class="meter-head"><span>${name}</span><span>${u[k].used}%${u[k].resets_at ? ` · resets in ${left(u[k].resets_at)}` : ""}${h(extra)}</span></div>
    <div class="meter"><div class="meter-fill" style="width:${Math.max(2, Math.min(100, u[k].used))}%;background:${tone(u[k].used)}"></div></div></div>`).join("")}</div>`;
}
function md(text) {
  // minimal markdown: fenced code, inline code, links, paragraphs
  let out = h(text);
  out = out.replace(/```(\w*)\n([\s\S]*?)```/g, (_, l, c) => `<pre>${c}</pre>`);
  out = out.replace(/`([^`]+)`/g, "<code>$1</code>");
  out = out.replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  return out.split(/\n\n+/).map(p => p.startsWith("<pre>") ? p : `<p>${p.replace(/\n/g, "<br>")}</p>`).join("");
}

function renderEffects(st) {
  const devs = st.devices.filter(d => d.capabilities.length && d.details.enabled !== false);
  const d = S.bench || (S.bench = { devices: devs.map(x => x.id), effect: "flash", color: "#5fe36a", count: 2, duration: 1.5, message: "Hello from Lumen", sound: soundChoices()[0][0] });
  return `
  <div class="page-head"><div><h1>Playground</h1><p>Pick devices, an effect and a colour, then play it on the hardware. Nothing here is saved.</p></div></div>
  <div class="two-col">
    <div class="section">
      <div class="section-head"><h2>Play an effect</h2></div>
      <div class="card"><div class="card-pad form">
        <div class="field"><label>On which devices?</label><div class="pickbox">${devs.map(x => `<label class="pick ${d.devices.includes(x.id) ? "on" : ""}"><input type="checkbox" ${d.devices.includes(x.id) ? "checked" : ""} onchange="L.bench('devices', this.checked ? [...L.b().devices, '${js(x.id)}'] : L.b().devices.filter(i => i !== '${js(x.id)}'))"> ${h(x.name)}</label>`).join("") || '<span class="dim">No devices available.</span>'}</div></div>
        <div class="inline">
          <div class="field"><label>Effect</label><select class="input" onchange="L.bench('effect', this.value)">${Object.entries(st.effects).filter(([k]) => !["set", "off", "sessions"].includes(k)).map(([k, e]) => `<option value="${k}" ${d.effect === k ? "selected" : ""}>${h(e.label)}</option>`).join("")}</select></div>
          <div class="field"><label>Colour</label><div class="inline"><input type="color" class="color-input" id="bench-color" value="${d.color}" oninput="L.bench('color', this.value)"><div class="presets">${PRESETS.map(p => `<span class="preset" style="background:${p}" onclick="L.bench('color','${p}')"></span>`).join("")}</div></div></div>
          <div class="field"><label>Repeat</label><input type="number" class="input num" min="1" max="20" value="${d.count}" onchange="L.bench('count', +this.value)"></div>
          <div class="field"><label>Seconds</label><input type="number" class="input num" min="0.2" step="0.1" value="${d.duration}" onchange="L.bench('duration', +this.value)"></div>
        </div>
        <div class="field"><label>Notification text</label><input class="input" value="${h(d.message)}" onchange="L.bench('message', this.value)"><span class="hint">Only used if a chosen device shows notifications.</span></div>
        <div class="field"><label>Sound</label><select class="input" onchange="L.bench('sound', this.value)">${soundOptions(d.sound)}</select><span class="hint">Play it here, then assign it in an automation.</span></div>
        <div class="inline"><button class="btn primary" onclick="L.play()">▶ Play it</button><button class="btn" onclick="L.playSet()">Hold this colour</button><button class="btn ghost" onclick="L.playOff()">Turn off</button></div>
      </div></div>
    </div>
    <div class="section">
      <div class="section-head"><h2>Send a test event</h2></div>
      <p class="section-sub">Fire a fake event to check that your automations react the way you expect.</p>
      <div class="card"><div class="card-pad form">
        <div class="field"><label>Event</label><select class="input" id="fire-type">${Object.entries(st.catalog).map(([k, c]) => `<option value="${k}">${h(c.label)}</option>`).join("")}</select></div>
        <div class="field"><label>Extra details (optional)</label><textarea class="input" id="fire-data" rows="3" placeholder="agent=claude"></textarea><span class="hint">One <span class="mono">key=value</span> per line.</span></div>
        <div><button class="btn primary" onclick="L.fire()">Fire event</button></div>
        <div class="hint">Same as <span class="mono">lumen emit &lt;type&gt; --data k=v</span>, or a POST to <span class="mono">/api/events</span>.</div>
      </div></div>
    </div>
  </div>`;
}

function renderSettings(st) {
  const s = st.settings;
  const q = s.quiet_hours || { enabled: false, from: "23:00", to: "07:00", mode: "no_flash" };
  const row = (title, desc, ctl) => `<div class="setting"><div class="desc"><b>${title}</b><span>${desc}</span></div><div class="ctl">${ctl}</div></div>`;
  // `inv` flips only what the switch shows ("open the dashboard" is stored as
  // start_minimized); the click still writes the opposite of what is stored.
  const tog = (key, inv) => toggleBtn(inv ? !s[key] : s[key], `L.setting('${key}', ${!s[key]})`, key.replace(/_/g, " "));
  return `
  <div class="page-head"><div><h1>Settings</h1><p>Lumen runs in the tray. Everything it stores lives in one folder you can delete at any time.</p></div></div>

  <div class="section"><div class="section-head"><h2>General</h2></div><div class="card"><div class="rows">
    ${row("Start at login", "Launch Lumen automatically when you sign in.", tog("autostart"))}
    ${row("Open this dashboard on start", "Otherwise Lumen starts in the tray.", tog("start_minimized", true))}
    ${row("Reduce flashing", "Turns flashes into pulses, never faster than twice a second.", tog("reduce_flashing"))}
    ${row("Keep the lights on when paused", "Pausing stops Lumen reacting but leaves your devices lit as they are, instead of handing them back to their own lighting.", tog("keep_lit"))}
    ${row("Appearance", "This dashboard's colours.", `<select class="input" aria-label="Appearance" onchange="L.theme(this.value)">${THEMES.map(([v, name]) => `<option value="${v}" ${currentTheme() === v ? "selected" : ""}>${name}</option>`).join("")}</select>`)}
    ${row("Run the setup again", "Finds your devices and connects your agents again.", `<button class="btn" onclick="L.openWizard()">Start setup</button>`)}
  </div></div></div>

  <div class="section"><div class="section-head"><h2>Quiet hours</h2>${st.quiet_now ? `<span class="chip">active now</span>` : ""}</div><div class="card"><div class="rows">
    ${row("Quiet hours", "Stop your devices reacting at night. Notifications and sounds stay with your operating system's do-not-disturb.", toggleBtn(q.enabled, `L.quiet({enabled: ${!q.enabled}})`, "Quiet hours"))}
    ${row("Between", "Local time. A window that runs past midnight is fine.", `<span class="inline"><input type="time" class="input" value="${h(q.from)}" aria-label="Quiet hours start" onchange="L.quiet({from: this.value})"><span class="dim">and</span><input type="time" class="input" value="${h(q.to)}" aria-label="Quiet hours end" onchange="L.quiet({to: this.value})"></span>`)}
    ${row("During those hours", "Keep the status colours but stop the flashing, or go completely dark.", `<select class="input" aria-label="What happens during quiet hours" onchange="L.quiet({mode: this.value})"><option value="no_flash" ${q.mode === "no_flash" ? "selected" : ""}>Colours stay, nothing flashes</option><option value="dark" ${q.mode === "dark" ? "selected" : ""}>Everything goes dark</option></select>`)}
  </div></div></div>

  <div class="section"><div class="section-head"><h2>Devices &amp; engine</h2></div><div class="card"><div class="rows">
    ${row("Status tab on the screen edge", "A small dark tab: one lit bar per open agent tab, your Claude limits, hover for details, click a row to jump to that terminal.", tog("notch"))}
    ${s.notch ? row("Where it sits", "Bottom keeps it clear of a MacBook's notch and menu bar.", `<select class="input" aria-label="Status tab position" onchange="L.setting('notch_position', this.value)">${[["top", "Top centre"], ["top-left", "Top left"], ["top-right", "Top right"], ["bottom", "Bottom centre"]].map(([v, name]) => `<option value="${v}" ${s.notch_position === v ? "selected" : ""}>${name}</option>`).join("")}</select>`) : ""}
    ${s.notch ? row("Hide during full-screen apps", "Games, films and presentations keep the whole screen.", tog("notch_hide_fullscreen")) : ""}
    ${s.notch ? row("Whose tabs", "Show every agent's sessions, or just one agent's.", `<select class="input" aria-label="Which agents the status tab shows" onchange="L.setting('notch_agents', this.value)">${[["all", "Claude and Codex"], ["claude", "Claude only"], ["codex", "Codex only"]].map(([v, name]) => `<option value="${v}" ${s.notch_agents === v ? "selected" : ""}>${name}</option>`).join("")}</select>`) : ""}
    ${s.notch ? row("What it shows", "Untick anything you don't want. Just the Claude limits and the live tabs is a popular pick.", `<div class="checks">${[["notch_show_sessions", "Live tabs"], ["notch_show_activity", "What each tab is doing"], ["notch_show_context", "Context window"], ["notch_show_cost", "Session cost"], ["notch_show_claude_usage", "Claude 5-hour / 7-day limits"], ["notch_show_codex_usage", "Codex 5-hour / 7-day limits"]].map(([k, name]) => `<label class="check"><input type="checkbox" ${s[k] !== false ? "checked" : ""} onchange="L.setting('${k}', this.checked)"> ${name}</label>`).join("")}</div>`) : ""}
    ${row("Start OpenRGB automatically", "Launches the OpenRGB server when it is installed but not running.", tog("launch_openrgb"))}
    ${row("Look for new devices every", "Seconds between background scans. Set to 0 to scan only when you press the button.", `<input type="number" class="input num" min="0" value="${s.rescan_interval_s}" onchange="L.setting('rescan_interval_s', +this.value)">`)}
    ${row(st.paused ? "Lumen is paused" : "Lumen is running", st.paused ? "Your devices are back under their own control." : "Automations are reacting to events.", `<button class="btn ${st.paused ? "primary" : ""}" onclick="L.pause(${!st.paused})">${st.paused ? "Resume" : "Pause"}</button>`)}
  </div></div></div>

  <div class="section"><div class="section-head"><h2>Advanced</h2></div><div class="card"><div class="rows">
    ${row("Write events to the log file", "Records each event and the automations it triggered.", tog("log_events"))}
    ${row("Webhook token", "If set, anything posting to /api/events must send it as a bearer token.", `<input class="input" style="width:240px" placeholder="none" value="${h(s.webhook_token)}" onchange="L.setting('webhook_token', this.value)">`)}
    ${row("Log", "The last lines Lumen wrote. Start here when a device or an agent misbehaves.", `<button class="btn sm" onclick="L.loadLog()">${S.log ? "Refresh" : "Show"}</button>`)}
  </div>${S.log ? `<pre class="log" id="log-view">${h(S.log.join("\n") || "(the log is empty)")}</pre>` : ""}<div class="rows">
    ${row("Port", "Where this dashboard and the API listen on 127.0.0.1. Takes effect after a restart.", `<input type="number" class="input num" value="${s.port}" onchange="L.setting('port', +this.value)">`)}
  </div></div></div>

  <div class="section"><div class="section-head"><h2>About</h2></div><div class="card"><div class="rows">
    ${row(`Lumen v${h(st.version)}`, `<span id="update-msg">${S.update ? (S.update.available ? `Version ${h(S.update.latest)} is available.` : S.update.error ? h(S.update.error) : "You're on the latest version.") : "Checks GitHub releases only when you press the button."}</span>`,
      S.update?.available ? `<button class="btn primary" id="update-btn" onclick="L.applyUpdate()">${S.update.frozen ? `Update to v${h(S.update.latest)}` : "How to update"}</button>` : `<button class="btn" id="update-btn" onclick="L.checkUpdate()">Check for updates</button>`)}
    <div class="card-pad row-sub" style="line-height:1.8"><a href="https://github.com/Brxerq/lumen" target="_blank" rel="noopener" style="color:var(--blue)">source &amp; issues</a> · MIT licensed.<br>API: <span class="mono">GET /api/state</span>, <span class="mono">POST /api/events</span>, <span class="mono">POST /api/scan</span>. See <span class="mono">docs/API.md</span>.</div>
  </div></div></div>`;
}

// ---------- rule editor ----------
function ruleEditor() {
  const r = S.draft, st = S.state;
  const cat = st.catalog[r.when];
  const fields = cat ? Object.entries(cat.fields) : [];
  const devs = st.devices.filter(d => d.capabilities.length);
  const problem = ruleProblem(r);
  return `<div class="modal-bg" onclick="if(event.target===this)L.closeModal()"><div class="modal" role="dialog" aria-modal="true" aria-label="${r.id ? "Edit automation" : "New automation"}">
    <div class="modal-head"><h2>${r.id ? "Edit automation" : "New automation"}</h2><button class="btn ghost sm" aria-label="Close" onclick="L.closeModal()">✕</button></div>
    <div class="modal-body form">
      <div class="field"><label>Give it a name</label><input class="input" value="${h(r.name)}" placeholder="e.g. Flash green when Claude finishes" oninput="L.draft('name', this.value)"></div>
      ${st.presets?.length ? `<div class="field"><label>Start from a saved effect</label><select class="input" aria-label="Start from a saved effect" onchange="L.usePreset(this.value); this.value=''"><option value="">Keep what is below…</option>${st.presets.map(p => `<option value="${h(p.id)}">${h(p.name)}</option>`).join("")}</select></div>` : ""}
      <div class="field"><label>When this happens…</label>
        <div class="inline">
          <select class="input" style="min-width:320px" onchange="L.draft('when', this.value)">
            ${Object.entries(st.catalog).map(([k, c]) => `<option value="${k}" ${r.when === k ? "selected" : ""}>${h(c.label)}</option>`).join("")}
            <option value="__custom" ${cat ? "" : "selected"}>Something else (type it yourself)…</option>
          </select>
          ${cat ? `<span class="chip ev">${h(r.when)}</span>` : `<input class="input mono" value="${h(r.when)}" placeholder="build.*" onchange="L.draft('when', this.value)">`}
        </div>
        ${fields.length ? `<div class="inline mt">${fields.map(([k, opts]) => `<div class="field"><label>…but only when ${h(k)} is</label>${opts.length ? `<select class="input" onchange="L.match('${k}', this.value)"><option value="">anything</option>${opts.map(o => `<option ${r.match[k] === o ? "selected" : ""}>${h(o)}</option>`).join("")}</select>` : `<input class="input" placeholder="anything" value="${h(r.match[k] || "")}" onchange="L.match('${k}', this.value)">`}</div>`).join("")}</div>` : ""}
      </div>
      <div class="field"><label>…do this</label></div>
      ${r.actions.map((a, i) => actionEditor(a, i, devs)).join("")}
      <div><button class="btn" onclick="L.addAction()">+ And do something else too</button></div>
    </div>
    <div class="modal-foot"><button class="btn" onclick="L.previewDraft()">Try it now</button>
      <button class="btn" onclick="L.savePreset()" title="Reuse these effects in other automations">Save as effect</button>
      ${problem ? `<span class="msg warn" role="status">${h(problem)}</span>` : ""}
      <div class="right"><button class="btn ghost" onclick="L.closeModal()">Cancel</button>
        <button class="btn primary" onclick="L.saveRule()" ${problem ? "disabled" : ""}>Save automation</button></div></div>
  </div></div>`;
}
// What stops this rule from being saved, in one sentence, or "" when it is fine.
function ruleProblem(r) {
  if (!String(r.when || "").trim()) return "Choose what this automation reacts to.";
  if (!r.actions.length) return "Add at least one thing to do.";
  const missing = r.actions.map(a => a.device).filter(id => id && id !== "*" && !deviceById(id));
  if (missing.length) return `${missing[0]} is not connected. Pick another device, or plug it back in.`;
  return "";
}

function actionEditor(a, i, devs) {
  const dev = a.device === "*" ? null : deviceById(a.device);
  const effs = dev ? effectsFor(dev) : Object.entries(S.state.effects).map(([k, e]) => ({ id: k, ...e }));
  const eff = S.state.effects[a.effect] || { params: [] };
  const P = p => eff.params.includes(p);
  return `<div class="action-editor">
    <div class="head"><span class="kw ${i ? "and" : "then"}" style="width:auto;text-align:left">${i ? "AND ALSO" : "DO THIS"}</span>${S.draft.actions.length > 1 ? `<button class="btn ghost sm" onclick="L.removeAction(${i})">Remove</button>` : ""}</div>
    <div class="inline">
      <div class="field"><label>On</label><select class="input" aria-label="Device" onchange="L.action(${i}, 'device', this.value)"><option value="*" ${a.device === "*" ? "selected" : ""}>All compatible devices</option>${devs.map(d => `<option value="${h(d.id)}" ${a.device === d.id ? "selected" : ""}>${h(d.name)}</option>`).join("")}${dev || a.device === "*" ? "" : `<option value="${h(a.device)}" selected>${h(a.device)} (not connected)</option>`}</select></div>
      <div class="field"><label>Do</label><select class="input" onchange="L.action(${i}, 'effect', this.value)">${effs.map(e => `<option value="${h(e.id)}" ${a.effect === e.id ? "selected" : ""}>${h(e.label)}</option>`).join("")}</select></div>
      ${P("color") ? `<div class="field"><label>Colour</label><div class="inline"><input type="color" class="color-input" id="color-${i}" value="${hex(a.color)}" oninput="L.action(${i}, 'color', this.value)"><div class="presets">${PRESETS.map(p => `<span class="preset" style="background:${p}" onclick="L.action(${i}, 'color', '${p}')"></span>`).join("")}</div></div></div>` : ""}
    </div>
    ${P("agent") ? `<div class="inline">
      <div class="field"><label>Agent</label><select class="input" onchange="L.action(${i}, 'agent', this.value)"><option value="" ${!a.agent ? "selected" : ""}>Any agent</option>${AGENTS.map(([x, name]) => `<option value="${x}" ${a.agent === x ? "selected" : ""}>${h(name)}</option>`).join("")}</select></div>
      <div class="field"><label>Layout</label><select class="input" onchange="L.action(${i}, 'per_zone', this.value === '1')"><option value="1" ${a.per_zone !== false ? "selected" : ""}>Split the zones between the tabs</option><option value="0" ${a.per_zone === false ? "selected" : ""}>Whole device, overall status</option></select></div>
    </div>` : ""}
    ${P("palette") ? `<div class="inline palette">${["running", "input", "done"].map(k => `<div class="field"><label>${STATUS_LABEL[k]}</label><div class="inline"><input type="color" class="color-input" id="pal-${i}-${k}" value="${hex((a.palette || DEF_PALETTE)[k])}" oninput="L.palette(${i}, '${k}', this.value)"><div class="presets">${PRESETS.slice(0, 5).map(p => `<span class="preset" style="background:${p}" onclick="L.palette(${i}, '${k}', '${p}')"></span>`).join("")}</div></div></div>`).join("")}
      <div class="field"><label>First slot</label><select class="input" onchange="L.action(${i}, 'offset', +this.value)">${[0, 1, 2, 3, 4, 5, 6, 7].map(o => `<option value="${o}" ${(a.offset || 0) === o ? "selected" : ""}>${o + 1}</option>`).join("")}</select></div></div>
      <div class="hint dim small">Zone 1 shows the session in this slot, zone 2 the next, and so on. Put the light bar on slot 5 to continue where a 4-zone keyboard stops. "Whole device" and devices without zones show one color: red if any session needs you, amber if any is working, else green.</div>` : ""}
    <div class="inline">
      ${P("count") ? `<div class="field"><label>Times</label><input type="number" class="input num" min="1" max="20" value="${a.count}" onchange="L.action(${i}, 'count', +this.value)"></div>` : ""}
      ${P("duration") ? `<div class="field"><label>Seconds</label><input type="number" class="input num" min="0.2" step="0.1" value="${a.duration}" onchange="L.action(${i}, 'duration', +this.value)"></div>` : ""}
      ${P("brightness") ? `<div class="field"><label>Brightness</label><input type="range" min="0.05" max="1" step="0.05" value="${a.brightness}" onchange="L.action(${i}, 'brightness', +this.value)"></div>` : ""}
      ${P("message") ? `<div class="field grow"><label>Message</label><input class="input" value="${h(a.message)}" placeholder="Task finished: {agent}" onchange="L.action(${i}, 'message', this.value)"></div>` : ""}
      ${P("sound") ? `<div class="field grow"><label>Sound</label><select class="input" onchange="L.action(${i}, 'sound', this.value)">${soundOptions(a.sound)}</select></div>` : ""}
    </div>
    ${a.device === "*" ? `<div class="hint dim small">Runs on every device that supports it; backlights without color pulse their brightness instead.</div>` : ""}
  </div>`;
}
function newAction() { return { device: "*", effect: "flash", color: [95, 227, 106], duration: 1.5, count: 2, brightness: 1, message: "", sound: soundChoices()[0][0], palette: { ...DEF_PALETTE }, offset: 0, agent: "", per_zone: true }; }
function openModal(html) { $("#modal-root").innerHTML = html; }

// ---------- onboarding wizard ----------
function openWizard() {
  S.wizard = { step: 0, scanned: false, choice: { effect: "flash", color: "#5fe36a", devices: "*", notify: true } };
  renderWizard();
  act(() => api("POST", "/api/scan")).then(() => { S.wizard && (S.wizard.scanned = true); renderWizard(); });
}
// A machine with no RGB is a supported machine, not a failed scan.
function noDevicesHint() {
  const platform = navigator.userAgent.includes("Mac") ? "mac" : navigator.userAgent.includes("Windows") ? "win" : "linux";
  const hint = {
    mac: "MacBooks have no addressable keyboard lighting, so Lumen uses the screen edge glow, notifications and sounds. Philips Hue and Govee lights on your network are found here too.",
    win: "For gaming peripherals, install OpenRGB and leave it running; ASUS Aura laptops (ROG, TUF, Zephyrus) are driven directly, so close Armoury Crate first. Meanwhile the screen glow, notifications and sounds work already.",
    linux: "Install OpenRGB for peripherals, or check /sys/class/leds for a keyboard backlight. The screen glow, notify-send and sounds work already.",
  }[platform];
  return `<div class="empty"><b>No lights found. Lumen still works</b>${hint}<br>You can scan again any time from the Devices page.</div>`;
}

function renderWizard() {
  const w = S.wizard; if (!w) return;
  const st = S.state;
  const steps = 4;
  const bar = `<div class="wizard-steps">${Array.from({ length: steps }, (_, i) => `<i class="${i <= w.step ? "done" : ""}"></i>`).join("")}</div>`;
  let body = "", foot = "";
  const devs = st.devices.filter(d => d.capabilities.length);
  if (w.step === 0) {
    body = `<h2>${w.scanned ? `Found ${devs.length} compatible device${devs.length === 1 ? "" : "s"}` : "Looking for hardware"}</h2>
      <p class="lead">${w.scanned ? "Test each one." : "Checking USB, OpenRGB, the network and this system."}</p>
      ${w.scanned ? `<div class="rows">${devs.map(d => `<div class="row">${swatch(d)}<div class="row-main"><div class="row-title">${h(d.name)}<small>${h(d.vendor)} · ${h(d.kind)}</small></div></div><div class="row-actions">${testMenu(d)}</div></div>`).join("") || noDevicesHint()}</div>` : '<div style="padding:30px 0;text-align:center"><span class="spinner"></span></div>'}`;
    foot = `<button class="btn ghost" onclick="L.finishWizard()">Skip setup</button><div class="right"><button class="btn primary" onclick="L.wizardStep(1)" ${w.scanned ? "" : "disabled"}>Next</button></div>`;
  } else if (w.step === 1) {
    const agents = st.integrations.filter(i => i.can_connect);
    body = `<h2>Connect your coding agents</h2><p class="lead">Connecting installs a hook, so Lumen knows when an agent starts, needs you or finishes. Webhook, terminal and GitHub are on the Integrations page.</p>
      <div class="rows">${agents.map(i => `<div class="row"><span class="dot ${i.connected ? "on" : ""}"></span><div class="row-main"><div class="row-title">${h(i.name)}<small>${h(i.detail || "")}</small></div><div class="row-sub">${h(i.description)}</div></div>
        <div class="row-actions">${i.connected ? '<span class="chip">connected</span>' : `<button class="btn sm primary" onclick="L.connect('${js(i.id)}')">Connect</button>`}</div></div>`).join("")}</div>`;
    foot = `<button class="btn ghost" onclick="L.wizardStep(0)">Back</button><div class="right"><button class="btn ghost" onclick="L.finishWizard()">Skip setup</button><button class="btn primary" onclick="L.wizardStep(2)">Next</button></div>`;
  } else if (w.step === 2) {
    const c = w.choice;
    body = `<h2>When an agent finishes</h2><p class="lead">Choose what happens. You can add more automations later.</p>
      <div class="form">
        <div class="inline">
          <div class="field"><label>Devices</label><select class="input" onchange="L.wizardChoice('devices', this.value)"><option value="*">All compatible devices</option>${devs.map(d => `<option value="${h(d.id)}" ${c.devices === d.id ? "selected" : ""}>${h(d.name)}</option>`).join("")}</select></div>
          <div class="field"><label>Effect</label><select class="input" onchange="L.wizardChoice('effect', this.value)">${["flash", "pulse", "wave"].map(e => `<option value="${e}" ${c.effect === e ? "selected" : ""}>${h(st.effects[e].label)}</option>`).join("")}</select></div>
          <div class="field"><label>Color</label><div class="inline"><input type="color" class="color-input" id="wiz-color" value="${c.color}" oninput="L.wizardChoice('color', this.value)"><div class="presets">${PRESETS.map(p => `<span class="preset" style="background:${p}" onclick="L.wizardChoice('color','${p}')"></span>`).join("")}</div></div></div>
        </div>
        <label class="inline" style="cursor:pointer"><input type="checkbox" ${c.notify ? "checked" : ""} onchange="L.wizardChoice('notify', this.checked)"> Also show a system notification</label>
      </div>`;
    foot = `<button class="btn ghost" onclick="L.wizardStep(1)">Back</button><div class="right"><button class="btn ghost" onclick="L.finishWizard()">Skip setup</button><button class="btn primary" onclick="L.wizardSave()">Save &amp; test</button></div>`;
  } else {
    body = `<h2>Setup complete</h2><p class="lead">A test event was fired, so your devices should have reacted. Fire another from the dashboard whenever you want.</p>
      <div class="row-sub" style="line-height:1.8">• <b>Automations</b> holds the rules for builds, deploys, timers and anything else.<br>• <b>Integrations</b> has the webhook URL and the CLI (<span class="mono">lumen emit</span>, <span class="mono">lumen exec -- …</span>).<br>• Lumen keeps running in the tray. Turn on <b>Start at login</b> in Settings.</div>`;
    foot = `<div class="right"><button class="btn" onclick="L.emit('agent.finished', {agent:'claude'})">Test again</button><button class="btn primary" onclick="L.finishWizard()">Open dashboard</button></div>`;
  }
  openModal(`<div class="modal-bg"><div class="modal wizard" role="dialog" aria-modal="true" aria-label="Setup"><div class="modal-body">${bar}${body}</div><div class="modal-foot">${foot}</div></div></div>`);
}

// ---------- actions (window.L) ----------
const L = window.L = {
  b: () => S.bench,
  // The menu is positioned in viewport coordinates rather than inside its row:
  // it has to escape the card, the modal and the wizard's row list, all of which
  // clip their overflow, and it flips above the button when there is no room
  // below — which is what left the last device's Test menu a sliver in setup.
  menu(btn) {
    const wrap = btn.parentElement, open = wrap.classList.contains("open");
    closeMenus();
    if (open) return;
    wrap.classList.add("open");
    const menu = wrap.querySelector(".menu"), at = btn.getBoundingClientRect();
    const room = innerHeight - at.bottom - 16 >= menu.offsetHeight;
    menu.style.left = `${Math.max(8, Math.min(at.right - menu.offsetWidth, innerWidth - menu.offsetWidth - 8))}px`;
    menu.style.top = room ? `${at.bottom + 7}px` : `${Math.max(8, at.top - menu.offsetHeight - 7)}px`;
  },
  testColor(id, v) { S.testColor[id] = v; },
  forget: id => act(() => api("DELETE", `/api/sessions/${id}`), r => r.forgotten ? "session forgotten" : "no hook file for that session (it is tracked from the agent's own record)"),
  test: (id, effect, sound) => act(() => api("POST", `/api/devices/${id}/test`, { effect, sound, color: rgb(S.testColor[id] || "#5fe36a"), count: 2, duration: 1.5 }),
    r => r.touched.length ? `Playing ${sound || effect} on ${deviceById(id)?.name || id}` : `${deviceById(id)?.name || id} is disabled or can't do ${effect}`),
  scan: () => act(() => api("POST", "/api/scan"), r => `${r.devices.length} device(s) found`),
  pause: v => act(() => api("POST", "/api/pause", { paused: v })),
  rename: (id, name) => act(() => api("PATCH", `/api/devices/${id}`, { name })),
  enable: (id, enabled) => act(() => api("PATCH", `/api/devices/${id}`, { enabled })),
  dim: (id, brightness) => act(() => api("PATCH", `/api/devices/${id}`, { brightness })),
  // Point one device at one agent. The shipped automation paints every device
  // from a single wildcard action, so the first time a device is given its own
  // agent that action is expanded into one per device — otherwise "Codex here"
  // would quietly mean "Codex everywhere".
  deviceAgent(id, value) {
    return act(() => L.patchDeviceSessions(id, value === "off" ? null : { agent: value }),
      () => value === "off" ? `${deviceById(id)?.name || id} no longer shows agent tabs`
        : `${deviceById(id)?.name || id} now shows ${agentName(value)}${value ? " tabs" : ""}`);
  },
  deviceLayout(id, perZone) {
    return act(() => L.patchDeviceSessions(id, { per_zone: perZone }),
      () => `${deviceById(id)?.name || id} shows ${perZone ? "one zone per tab" : "one colour for every tab"}`);
  },
  // Rewrite the one `sessions` action that paints this device. Passing null
  // takes the device out of the automation entirely.
  patchDeviceSessions(id, patch) {
    const st = S.state;
    const carries = pick => st.rules.find(r => r.actions.some(a => a.effect === "sessions" && pick(a.device)));
    // The rule that already paints THIS device, not merely the first one with a
    // session action: a setup with "Claude on the keyboard" and "Codex on the
    // light bar" is two rules, and editing the wrong one adds a second action
    // for the same device instead of changing it — the light bar then ignores
    // everything this page does.
    const source = carries(d => d === id) || carries(d => ["*", "all", ""].includes(d));
    const rule = source ? JSON.parse(JSON.stringify(source))
      : { name: "Agent status", enabled: true, when: "agents.sessions", match: {}, actions: [] };
    // Ambient devices only: a wildcard action never reaches the screen glow, so
    // expanding it must not hand the glow a permanent per-session border either.
    const lit = st.devices.filter(d => d.details.enabled !== false && d.ambient !== false
      && d.capabilities.some(c => c === "color" || c === "zones" || c === "brightness"));
    const wild = rule.actions.find(a => a.effect === "sessions" && ["*", "all", ""].includes(a.device));
    if (wild) rule.actions = rule.actions.filter(a => a !== wild).concat(lit.map(d => ({ ...wild, device: d.id })));
    const mine = rule.actions.find(a => a.effect === "sessions" && a.device === id);
    const from = mine || rule.actions.find(a => a.effect === "sessions") || newAction();
    rule.actions = rule.actions.filter(a => a !== mine);
    // A device showing one agent starts that agent's tabs at its own first zone;
    // an offset only means something when devices share one run of tabs.
    if (patch) {
      const agent = "agent" in patch ? patch.agent : (from.agent || "");
      rule.actions.push({ ...from, effect: "sessions", device: id, ...patch,
                          offset: agent ? 0 : (from.offset || 0) });
    }
    rule.enabled = true;
    return api("POST", "/api/rules", rule);
  },
  forgetDevice: id => confirm(`Forget the saved settings for ${id}?`) && act(() => api("DELETE", `/api/devices/${id}`), "forgotten"),
  feed(filter) { S.feed = filter; render(); },
  clearFeed: () => act(() => api("DELETE", "/api/activity"), "feed cleared"),
  theme(name) { applyTheme(name); render(); },
  quiet(patch) {
    const q = { ...(S.state.settings.quiet_hours || {}), ...patch };
    return act(() => api("PUT", "/api/settings", { quiet_hours: q }));
  },

  // --- agent tabs: which zone is which -------------------------------------
  labelSession: (id, label) => act(() => api("PATCH", `/api/sessions/${id}`, { label }), "renamed"),
  moveSession(id, delta) {
    const list = S.state.sessions;
    const at = list.findIndex(s => s.id === id);
    const to = list[at + delta];
    if (!to) return;
    return act(() => api("PATCH", `/api/sessions/${id}`, { slot: to.slot }));
  },
  dragStart(ev, id) {
    S.drag = id;
    ev.dataTransfer.effectAllowed = "move";
    ev.dataTransfer.setData("text/plain", id);   // Firefox needs a payload to start a drag
    ev.currentTarget.classList.add("dragging");
  },
  dragOver(ev) { if (S.drag) { ev.preventDefault(); ev.dataTransfer.dropEffect = "move"; } },
  dragEnd() { S.drag = null; document.querySelectorAll(".row.session.dragging").forEach(r => r.classList.remove("dragging")); },
  drop(ev, targetId) {
    ev.preventDefault();
    const moved = S.drag || ev.dataTransfer.getData("text/plain");
    L.dragEnd();
    if (!moved || moved === targetId) return;
    const target = S.state.sessions.find(s => s.id === targetId);
    if (target) return act(() => api("PATCH", `/api/sessions/${moved}`, { slot: target.slot }));
  },

  // --- automations ----------------------------------------------------------
  moveRule(id, delta) {
    const rules = S.state.rules.slice();
    const at = rules.findIndex(r => r.id === id);
    if (at < 0 || at + delta < 0 || at + delta >= rules.length) return;
    rules.splice(at + delta, 0, rules.splice(at, 1)[0]);
    return act(() => api("PUT", "/api/rules", rules));
  },
  duplicateRule(id) {
    const r = S.state.rules.find(x => x.id === id);
    return act(() => api("POST", "/api/rules", { ...r, id: undefined, name: `${r.name || "Automation"} copy` }), "duplicated");
  },
  exportRules() {
    const blob = new Blob([JSON.stringify(S.state.rules, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "lumen-automations.json";
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  },
  importRules() {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "application/json,.json";
    input.onchange = async () => {
      const file = input.files[0];
      if (!file) return;
      let rules;
      try {
        rules = JSON.parse(await file.text());
        if (!Array.isArray(rules) || !rules.every(r => r && r.when && Array.isArray(r.actions))) throw new Error("that file is not a list of automations");
      } catch (e) { return toast(e.message, true); }
      if (!confirm(`Replace your ${S.state.rules.length} automation(s) with the ${rules.length} in this file?`)) return;
      act(() => api("PUT", "/api/rules", rules), `imported ${rules.length}`);
    };
    input.click();
  },

  // --- saved effects --------------------------------------------------------
  savePreset() {
    const name = prompt("Name this set of effects (e.g. “Success”)", S.draft.name || "");
    if (!name) return;
    return act(() => api("POST", "/api/presets", { name, actions: S.draft.actions }), "saved");
  },
  usePreset(id) {
    const preset = S.state.presets.find(p => p.id === id);
    if (!preset) return;
    S.draft.actions = JSON.parse(JSON.stringify(preset.actions));
    openModal(ruleEditor());
  },
  deletePreset: id => confirm("Delete this saved effect?") && act(() => api("DELETE", `/api/presets/${id}`), "deleted"),
  adapter: (name, action, params) => act(() => api("POST", `/api/adapters/${name}/${action}`, params), r => r.message),
  emit: (type, data) => act(() => api("POST", "/api/events", { type, data: data || {}, source: "dashboard" }), `fired ${type}`),
  fire() {
    const data = Object.fromEntries($("#fire-data").value.split("\n").filter(l => l.includes("=")).map(l => l.split("=", 2).map(s => s.trim())));
    return L.emit($("#fire-type").value, data);
  },
  connect: (id, off) => act(() => api("POST", `/api/integrations/${id}/${off ? "disconnect" : "connect"}`), r => r.message).then(r => { const m = $(`#msg-${id}`); if (m && r) m.textContent = r.message; if (S.wizard) renderWizard(); }),
  saveOptions: (id, keys) => act(() => api("PATCH", `/api/integrations/${id}`, Object.fromEntries(keys.map(k => [k, $(`#opt-${id}-${k}`).value]))), "saved"),
  setting: (key, value) => act(() => api("PUT", "/api/settings", { [key]: value })),
  async checkUpdate() {
    const b = $("#update-btn"); if (b) { b.disabled = true; b.textContent = "Checking…"; }
    try { S.update = await api("GET", "/api/update"); } catch (e) { S.update = { error: e.message }; }
    render();
  },
  async applyUpdate() {
    if (!S.update?.frozen) { window.open(S.update.url, "_blank"); return; }
    const b = $("#update-btn"); if (b) { b.disabled = true; b.textContent = "Downloading…"; }
    try {
      const r = await api("POST", "/api/update");
      toast(r.message);
      if (/restarts/.test(r.message)) { S.update = null; setTimeout(() => location.reload(), 8000); }
    } catch (e) { toast(e.message, true); render(); }
  },
  toggleRule(id) { const r = S.state.rules.find(x => x.id === id); return act(() => api("POST", "/api/rules", { ...r, enabled: !r.enabled })); },
  deleteRule: id => confirm("Delete this automation?") && act(() => api("DELETE", `/api/rules/${id}`), "deleted"),
  preview(id) { const r = S.state.rules.find(x => x.id === id); return act(() => api("POST", "/api/rules/preview", r.actions)); },
  editRule(id) {
    const r = id ? S.state.rules.find(x => x.id === id) : { name: "", enabled: true, when: "agent.finished", match: {}, actions: [newAction()] };
    S.draft = JSON.parse(JSON.stringify(r));
    openModal(ruleEditor());
  },
  draft(k, v) { if (k === "when" && v === "__custom") v = "custom.event"; S.draft[k] = v; if (k === "when") { S.draft.match = {}; openModal(ruleEditor()); } },
  palette(i, k, v) { const a = S.draft.actions[i]; a.palette = { ...DEF_PALETTE, ...(a.palette || {}), [k]: rgb(v) }; const el = $(`#pal-${i}-${k}`); if (el) el.value = v; },
  match(k, v) { S.draft.match[k] = v; },
  async loadLog() {
    try { S.log = (await api("GET", "/api/log")).lines; } catch (e) { S.log = [`could not read the log: ${e.message || e}`]; }
    render(); requestAnimationFrame(() => { const el = document.getElementById("log-view"); if (el) el.scrollTop = el.scrollHeight; });
  },
  action(i, k, v) {
    const a = S.draft.actions[i];
    if (k === "color") { const el = $(`#color-${i}`); if (el) el.value = v; v = rgb(v); }
    a[k] = v;
    if (k === "device" || k === "effect") {
      const dev = a.device === "*" ? null : deviceById(a.device);
      const allowed = (dev ? effectsFor(dev) : Object.keys(S.state.effects).map(id => ({ id }))).map(e => e.id);
      if (!allowed.includes(a.effect)) a.effect = allowed[0] || "flash";
      openModal(ruleEditor());
    }
  },
  addAction() { S.draft.actions.push(newAction()); openModal(ruleEditor()); },
  removeAction(i) { S.draft.actions.splice(i, 1); openModal(ruleEditor()); },
  previewDraft: () => act(() => api("POST", "/api/rules/preview", S.draft.actions)),
  saveRule() {
    const problem = ruleProblem(S.draft);
    if (problem) return toast(problem, true);
    return act(() => api("POST", "/api/rules", S.draft), "saved").then(() => L.closeModal());
  },
  closeModal() { $("#modal-root").innerHTML = ""; S.draft = null; },
  openWizard,
  wizardStep(n) { S.wizard.step = n; renderWizard(); },
  wizardChoice(k, v) { S.wizard.choice[k] = v; if (k === "color") { const el = $("#wiz-color"); if (el) el.value = v; } },
  async wizardSave() {
    const c = S.wizard.choice;
    const actions = [{ device: c.devices, effect: c.effect, color: rgb(c.color), count: 2, duration: 1.5 }];
    if (c.notify && S.state.devices.some(d => d.id === "notification")) actions.push({ device: "notification", effect: "notify", message: "Your task is complete." });
    const existing = S.state.rules.find(r => r.when === "agent.finished" && !Object.keys(r.match || {}).length);
    await act(() => api("POST", "/api/rules", { ...(existing || {}), name: existing?.name || "Task finished", when: "agent.finished", match: {}, enabled: true, actions }));
    await act(() => api("POST", "/api/events", { type: "agent.finished", data: { agent: "claude" }, source: "onboarding" }));
    S.wizard.step = 3; renderWizard();
  },
  finishWizard() { S.wizard = null; $("#modal-root").innerHTML = ""; return act(() => api("POST", "/api/onboarded")); },
  bench(k, v) { S.bench[k] = v; if (k === "color") { const el = $("#bench-color"); if (el) el.value = v; } if (k === "effect" || k === "devices") render(); },
  play() { const b = S.bench; return act(() => api("POST", "/api/rules/preview", b.devices.map(d => ({ device: d, effect: b.effect, color: rgb(b.color), count: b.count, duration: b.duration, message: b.message, sound: b.sound || "default" })))); },
  playSet() { const b = S.bench; return act(() => api("POST", "/api/rules/preview", b.devices.map(d => ({ device: d, effect: "set", color: rgb(b.color) })))); },
  playOff() { const b = S.bench; return act(() => api("POST", "/api/rules/preview", b.devices.map(d => ({ device: d, effect: "off" })))); },
};

// ---------- staying up to date ----------
// The daemon pushes a revision number over server-sent events, so the dashboard
// refetches when something actually changed instead of every two seconds. The
// old poll stays as the fallback: if the stream cannot connect (a proxy, an old
// browser), the page still works, just less promptly. Either way nothing runs
// while the tab is hidden — a tray app's dashboard is in the background most of
// its life — and a setTimeout chain means a slow response can't stack up.
const POLL_FAST = 2000, POLL_SLOW = 10000, QUIET_POLLS = 8;
let pollTimer = null, stream = null;

function schedulePoll() {
  clearTimeout(pollTimer);
  if (document.hidden || stream?.readyState === EventSource.OPEN) return;
  pollTimer = setTimeout(poll, S.quiet >= QUIET_POLLS ? POLL_SLOW : POLL_FAST);
}
async function poll() { await refresh(); schedulePoll(); }
function pollSoon() { S.quiet = 0; schedulePoll(); }

function openStream() {
  if (stream || document.hidden || !window.EventSource) return;
  stream = new EventSource("/api/stream");
  stream.onmessage = () => { S.quiet = 0; refresh(); };
  stream.onopen = () => clearTimeout(pollTimer);
  stream.onerror = () => {           // daemon restarting, or no stream support
    stream.close();
    stream = null;
    schedulePoll();
    setTimeout(openStream, 5000);
  };
}
function closeStream() { stream?.close(); stream = null; }

document.addEventListener("visibilitychange", () => {
  if (document.hidden) { clearTimeout(pollTimer); closeStream(); }
  else { S.quiet = 0; clearTimeout(pollTimer); poll(); openStream(); }   // catch up the moment you look
});
window.addEventListener("pagehide", closeStream);

// Esc closes whatever is on top: a menu, then the modal.
document.addEventListener("keydown", ev => {
  if (ev.key !== "Escape") return;
  if (document.querySelector(".menu-wrap.open")) return closeMenus();
  if ($("#modal-root").children.length && !S.wizard) L.closeModal();
});
// An open menu is pinned to the viewport, so anything that scrolls under it
// would leave it hanging over the wrong row. Close it instead of chasing it.
addEventListener("scroll", closeMenus, true);
addEventListener("resize", closeMenus);

// ---------- boot ----------
// Blur first: render() refuses to rebuild while a field inside the page has
// focus, so that a background refresh cannot yank what you are typing. Clicking
// a nav link straight after editing a name is a navigation, not a refresh, and
// used to leave you on the same page.
window.addEventListener("hashchange", () => { S.page = location.hash.slice(1) || "dashboard"; document.activeElement?.blur?.(); if (S.draft) L.closeModal(); render(); pollSoon(); });
document.addEventListener("click", e => { if (!e.target.closest(".menu-wrap")) closeMenus(); });
applyTheme(currentTheme());
S.page = location.hash.slice(1) || "dashboard";
poll();
openStream();
