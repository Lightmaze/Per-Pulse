/** Browser acceptance for a genuinely installed wheel with no model.
 * This is a local, disconnected first-use flow, not a scripted provider.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const python = process.env.PER_PULSE_INSTALLED_PYTHON;
if (!python) throw new Error('PER_PULSE_INSTALLED_PYTHON must point to the isolated wheel installation.');

const directory = await mkdtemp(join(tmpdir(), 'per-pulse-installed-browser-'));
const listener = createServer();
await new Promise(done => listener.listen(0, '127.0.0.1', done));
const port = listener.address().port;
await new Promise(done => listener.close(done));
const base = 'http://127.0.0.1:' + port;
const env = { ...process.env };
for (const key of ['P_PULSE_CHAT_ENDPOINT', 'P_PULSE_CHAT_MODEL', 'P_PULSE_API_KEY', 'P_PULSE_CREDENTIAL_ENV']) delete env[key];
let serverLog = '';
let server;
let browser;

function start() {
  const child = spawn(python, ['-m', 'per_pulse.cli', '--data-dir', directory, '--port', String(port)],
    { cwd: directory, env, stdio: ['ignore', 'pipe', 'pipe'] });
  child.stdout.on('data', data => { serverLog += data; });
  child.stderr.on('data', data => { serverLog += data; });
  return child;
}
async function stop(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  child.kill('SIGTERM');
  const force = setTimeout(() => child.kill('SIGKILL'), 9000);
  await new Promise(done => child.once('exit', done));
  clearTimeout(force);
}
async function ready() {
  const deadline = Date.now() + 25000;
  while (Date.now() < deadline) {
    if (server.exitCode !== null) throw new Error('Installed service exited: ' + serverLog);
    try {
      const response = await fetch(base + '/api/health');
      if (response.ok) {
        const health = await response.json();
        assert.equal(health.providerConfigured, false, 'No model is configured');
        return;
      }
    } catch { /* Server still starting. */ }
    await new Promise(done => setTimeout(done, 100));
  }
  throw new Error('Installed service did not start: ' + serverLog);
}

try {
  server = start();
  await ready();
  browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1100, height: 800 } });
  await page.goto(base);
  await page.getByRole('textbox', { name: 'Message Home', exact: true }).waitFor();
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  const setup = page.locator('details.provider-setup');
  assert.equal(await setup.count(), 1, 'Disconnected ordinary user must be able to find setup');
  await setup.locator('summary').click();
  await setup.getByText('Terminal setup example').waitFor({ state: 'visible' }).catch(() => {});
  const instructions = await setup.locator('code').innerText();
  for (const variable of ['P_PULSE_CHAT_ENDPOINT', 'P_PULSE_CHAT_MODEL', 'P_PULSE_API_KEY']) {
    assert.ok(instructions.includes(variable), variable + ' missing from first-use guidance');
  }
  await page.locator('#interface-language').selectOption('zh-CN');
  await page.getByText('如何连接模型', { exact: true }).waitFor();
  await page.locator('#interface-language').selectOption('en');
  await page.getByRole('button', { name: 'Close dialog', exact: true }).click();

  await page.getByRole('button', { name: 'Create a page', exact: true }).click();
  await page.getByRole('textbox', { name: 'Page name', exact: true }).fill('A real disconnected Work');
  await page.getByRole('textbox', { name: 'Where should we begin?', exact: true })
    .fill('Keep the explanation in this Work.');
  await page.getByRole('button', { name: 'Create page', exact: true }).click();
  await page.getByRole('heading', { name: 'A real disconnected Work', exact: true }).waitFor();
  await page.getByRole('textbox', { name: /^Message / }).fill('A note to continue after restart.');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await page.getByText('A note to continue after restart.', { exact: true }).waitFor();

  const bootstrap = await (await fetch(base + '/api/bootstrap')).json();
  assert.equal(bootstrap.provider.configured, false);
  assert.equal(bootstrap.pages.filter(item => item.kind === 'work').length, 1);
  await stop(server);
  server = start();
  await ready();
  await page.reload();
  await page.getByRole('link', { name: 'A real disconnected Work', exact: true }).click();
  await page.getByText('A note to continue after restart.', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  assert.equal(await page.locator('details.provider-setup').count(), 1);
  const restored = await (await fetch(base + '/api/bootstrap')).json();
  assert.equal(restored.workspaceId, bootstrap.workspaceId);
  assert.equal(restored.provider.configured, false);

  console.log(JSON.stringify({
    passed: 5,
    checks: ['isolated wheel starts without credentials', 'browser offers local provider setup',
      'English/Chinese first-use instructions work', 'real disconnected Work accepts queued input',
      'original World, Work and note persist after clean restart'],
    provider: 'none; no synthetic model replies',
  }, null, 2));
} finally {
  if (browser) await browser.close();
  await stop(server);
  await rm(directory, { recursive: true, force: true });
}
