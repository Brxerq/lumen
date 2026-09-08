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
  assert.equal(shortPath('/Users/alice/project'), '…/alice/project');
  assert.equal(shortPath('C:' + String.fromCharCode(92) + 'Users' + String.fromCharCode(92) + 'alice' + String.fromCharCode(92) + 'project'), '…' + String.fromCharCode(92) + 'alice' + String.fromCharCode(92) + 'project');
`, context);
console.log('Dashboard smoke checks passed: empty, offline, paused, quiet, needs-input, and native paths.');
