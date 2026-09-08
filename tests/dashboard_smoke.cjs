// Run with: node tests/dashboard_smoke.cjs
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const context = vm.createContext({
  window: { addEventListener() {} },
  document: { addEventListener() {} },
  addEventListener() {},
  assert,
});
const source = fs.readFileSync(path.join(__dirname, '../src/lumen/server/ui/app.js'), 'utf8');
assert.match(source, /document\.body\.append\(menu\)/);
assert.match(source, /\.menu\.portal-open/);
vm.runInContext(source.split('// ---------- boot ----------')[0], context);
vm.runInContext(`
  const sample = { devices: [], rules: [], sessions: [], activity: [], messages: [], uptime_s: 60 };
  S.state = sample;
  assert.match(renderDashboard(sample), /Ready when you are/);
  S.error = 'offline';
  assert.match(renderDashboard(sample), /Connection interrupted/);
  assert.match(renderDashboard(sample), /Showing the last received state/);
  S.error = null;
  assert.match(renderDashboard(sample), /<details[^>]+id="dashboard-activity"/);
  const dashboard = renderDashboard(sample);
  assert.equal(dashboard.match(/id="(dashboard-[^"]+)"/)[1], 'dashboard-status');
  for (const id of ['dashboard-status', 'dashboard-lights']) {
    assert.match(dashboard, new RegExp('<section[^>]+id="' + id + '"[^>]*>'));
    assert.doesNotMatch(dashboard, new RegExp('<details[^>]+id="' + id + '"'));
  }
  for (const id of ['dashboard-sessions', 'dashboard-activity']) {
    assert.match(dashboard, new RegExp('<details[^>]+id="' + id + '"[^>]*>'));
    assert.doesNotMatch(dashboard, new RegExp('<details[^>]+id="' + id + '"[^>]* open'));
  }
  assert.ok(dashboard.includes('onclick="L.forceSync()"'));
  S.sync = { busy: true, message: '', error: false };
  assert.match(renderDashboard(sample), /disabled aria-busy="true">Syncing/);
  S.sync = { busy: false, message: 'Could not sync. Try again.', error: true };
  assert.match(renderDashboard(sample), /role="status"[^>]*>Could not sync/);
  S.sync = { busy: false, message: '', error: false };
  assert.doesNotMatch(renderDashboard(sample), /overview-metrics|Your automations/);
  const settings = renderSettings({ ...sample, settings: {}, version: '0.7.6' });
  assert.match(settings, /Changes save automatically/);
  assert.match(settings, /<details[^>]+id="settings-advanced"/);
  assert.match(settings, /Check for updates/);
  const paused = renderDashboard({ ...sample, paused: true });
  assert.match(paused, /Resume automations/);
  assert.match(paused, /disabled[^>]*>Test my lights/);
  assert.match(renderDashboard({ ...sample, quiet_now: true }), /Quiet hours are active/);
  const needsInput = { ...sample, sessions: [{ id: 'one', status: 'input', agent: 'codex', cwd: '/Users/alice/project' }] };
  S.state = needsInput;
  assert.match(renderDashboard(needsInput), /1 task is waiting for you/);
  const mixed = { ...sample, sessions: [
    { id: 'done', status: 'done', agent: 'codex' },
    { id: 'input', status: 'input', agent: 'codex' },
    { id: 'working', status: 'running', agent: 'codex' },
  ] };
  S.state = mixed;
  const rows = sessionList(mixed);
  assert.ok(rows.indexOf('data-sid="working"') < rows.indexOf('data-sid="done"'));
  assert.ok(rows.indexOf('data-sid="working"') < rows.indexOf('data-sid="input"'));
  assert.equal(mixed.sessions[0].id, 'done');
  const assigned = { ...mixed,
    sessions: mixed.sessions.map(s => s.id === 'done' ? { ...s, agent: 'claude' } : s),
    devices: [{ id: 'keyboard', connected: true, zones: 2, details: {} }],
    rules: [{ enabled: true, actions: [{ device: 'keyboard', effect: 'sessions', agent: 'claude' }] }],
  };
  S.state = assigned;
  assert.ok(zoneLayout(assigned).placed.has('done'));
  assert.ok(!zoneLayout(assigned).placed.has('working'));
  const assignedRows = sessionList(assigned);
  assert.ok(assignedRows.indexOf('data-sid="working"') < assignedRows.indexOf('data-sid="done"'));
  assert.equal(shortPath('/Users/alice/project'), '…/alice/project');
  assert.equal(shortPath('C:' + String.fromCharCode(92) + 'Users' + String.fromCharCode(92) + 'alice' + String.fromCharCode(92) + 'project'), '…' + String.fromCharCode(92) + 'alice' + String.fromCharCode(92) + 'project');

  const classes = () => ({ values: new Set(), add(v) { this.values.add(v); }, remove(v) { this.values.delete(v); }, contains(v) { return this.values.has(v); } });
  const wrap = { classList: classes(), querySelector: () => menu };
  const anchor = { isConnected: true, replaceWith(node) { node.parentElement = wrap; } };
  const menu = { classList: classes(), style: {}, offsetWidth: 214, offsetHeight: 180,
    replaceWith() { this.parentElement = null; } };
  const button = { parentElement: wrap, getBoundingClientRect: () => ({ top: 100, bottom: 138, right: 468 }) };
  document.body = { append(node) { node.parentElement = this; } };
  document.createComment = () => anchor;
  document.querySelectorAll = selector => selector === '.menu-wrap.open' && wrap.classList.contains('open') ? [wrap] : [];
  this.innerWidth = 700; this.innerHeight = 600;
  L.menu(button);
  assert.equal(menu.parentElement, document.body);
  assert.equal(menu.classList.contains('portal-open'), true);
  assert.equal(menu.style.left, '254px');
  assert.equal(menu.style.top, '145px');
  closeMenus(false);
  assert.equal(menu.parentElement, wrap);
  assert.equal(menu.classList.contains('portal-open'), false);
`, context);
vm.runInContext(`
  (async () => {
    const realRefresh = refresh;
    render = () => {};
    let calls = 0, release;
    api = async (method, path) => {
      assert.equal(method, 'POST');
      assert.equal(path, '/api/sync');
      calls++;
      await new Promise(resolve => { release = resolve; });
    };
    refresh = async () => { S.error = null; return { error: null }; };
    const pending = L.forceSync();
    assert.equal(S.sync.busy, true);
    await L.forceSync();
    assert.equal(calls, 1);
    release();
    await pending;
    assert.equal(S.sync.busy, false);
    assert.equal(S.sync.error, false);
    assert.match(S.sync.message, /up to date/);
    api = async () => { throw new Error('offline'); };
    await L.forceSync();
    assert.equal(S.sync.busy, false);
    assert.equal(S.sync.error, true);
    assert.match(S.sync.message, /offline/);
    api = async () => ({});
    refresh = async () => { S.error = 'offline'; return { error: 'offline' }; };
    await L.forceSync();
    assert.equal(S.sync.error, true);
    assert.match(S.sync.message, /could not refresh/);
    refresh = async () => ({ superseded: true, error: null });
    await L.forceSync();
    assert.equal(S.sync.error, false);
    assert.match(S.sync.message, /Waiting for the latest/);
    assert.doesNotMatch(S.sync.message, /up to date/);
    renderSidebar = () => {};
    refreshBlocked = () => false;
    api = async () => S.state;
    await realRefresh();
    assert.match(S.sync.message, /up to date/);
    assert.equal(S.sync.waiting, false);
    await L.forceSync();
    api = async () => { throw new Error('offline'); };
    await realRefresh();
    assert.equal(S.sync.error, true);
    assert.equal(S.sync.waiting, false);
    assert.match(S.sync.message, /could not refresh/);
  })()
`, context).then(() => console.log('Dashboard smoke checks passed: disclosure defaults, running priority, force sync states, empty, offline, paused, quiet, and native paths.')).catch(error => {
  console.error(error);
  process.exitCode = 1;
});
