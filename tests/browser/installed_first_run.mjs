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
  await setup.locator('pre').waitFor({ state: 'visible' });
  const instructions = await setup.locator('code').innerText();
  for (const variable of ['P_PULSE_CHAT_ENDPOINT', 'P_PULSE_CHAT_MODEL', 'P_PULSE_API_KEY']) {
    assert.ok(instructions.includes(variable), variable + ' missing from first-use guidance');
  }
  assert.ok(instructions.includes('per-pulse doctor'), 'Offline provider preflight is offered before launch');
  await page.locator('#provider-terminal-shell').selectOption('powershell');
  const windowsCommand = await setup.locator('code').innerText();
  assert.ok(windowsCommand.includes("$env:P_PULSE_CHAT_ENDPOINT = '"), 'PowerShell environment syntax is usable on Windows');
  assert.ok(windowsCommand.includes('per-pulse doctor'), 'Windows setup includes a local preflight');
  assert.ok(!windowsCommand.includes('export P_PULSE_CHAT_'), 'Windows must not show POSIX shell commands');
  await page.locator('#provider-connection-mode').selectOption('loopback');
  const localWindows = await setup.locator('code').innerText();
  assert.ok(localWindows.includes('127.0.0.1:11434/v1/chat/completions'));
  assert.ok(localWindows.includes('$env:P_PULSE_LOCAL_NO_AUTH'));
  assert.ok(localWindows.includes('$env:P_PULSE_ALLOW_LOCAL_HTTP'));
  assert.ok(!localWindows.includes('P_PULSE_API_KEY'), 'Local model setup never asks for a key');
  await page.locator('#provider-terminal-shell').selectOption('posix');
  const localPosix = await setup.locator('code').innerText();
  assert.ok(localPosix.includes('export P_PULSE_LOCAL_NO_AUTH'));
  assert.ok(!localPosix.includes('$env:'));
  await page.locator('#interface-language').selectOption('zh-CN');
  assert.equal(await page.locator('#provider-connection-mode option:checked').innerText(),
    '本机模型服务（无需 API 密钥）');
  await page.locator('#provider-connection-mode').selectOption('keyed');
  await page.locator('#provider-terminal-shell').selectOption('posix');
  assert.ok((await setup.locator('code').innerText()).startsWith('export P_PULSE_CHAT_ENDPOINT='));
  await page.locator('#provider-terminal-shell').selectOption('powershell');
  assert.ok((await setup.locator('code').innerText()).startsWith('$env:P_PULSE_CHAT_ENDPOINT'));
  assert.equal(await page.getByRole('button', { name: '复制终端配置命令' }).count(), 1);
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

  // The first Work's save can be pending while the person visits another Work.
  // This is a real wheel-installed browser and persistent World, with the
  // response held at the browser/network boundary (no model and no mock store).
  const originalWorkId = new URL(page.url()).pathname.split('/').pop();
  const delayedPattern = '**/api/pages/' + encodeURIComponent(originalWorkId) + '/messages';
  let releaseOriginal;
  const delayedResponse = new Promise(resolve => { releaseOriginal = resolve; });
  let signalHeld;
  const heldRequest = new Promise(resolve => { signalHeld = resolve; });
  let signalForwarded;
  const forwardedRequest = new Promise(resolve => { signalForwarded = resolve; });
  await page.route(delayedPattern, async route => {
    signalHeld();
    await delayedResponse;
    await route.continue();
    signalForwarded();
  });
  await page.locator('.composer textarea').fill('Saved in the first Work after visiting another Work.');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await heldRequest;
  await page.getByLabel('Workspace navigation').getByRole('link', { name: 'Home', exact: true }).click();
  await page.getByRole('button', { name: 'Create a page', exact: true }).click();
  await page.getByRole('textbox', { name: 'Page name', exact: true }).fill('Second independent Work');
  await page.getByRole('textbox', { name: 'Where should we begin?', exact: true })
    .fill('Continue here while the first Work saves its message.');
  await page.getByRole('button', { name: 'Create page', exact: true }).click();
  await page.getByRole('heading', { name: 'Second independent Work', exact: true }).waitFor();
  await page.getByRole('textbox', { name: /^Message / })
    .fill('The second Work is independently writable.');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await page.getByText('The second Work is independently writable.', { exact: true }).waitFor();
  releaseOriginal();
  // Do not unroute while Playwright still owns the paused route. Otherwise
  // the handler can race teardown and throw "Route is already handled".
  await forwardedRequest;
  await page.unroute(delayedPattern);
  await page.getByRole('link', { name: 'A real disconnected Work', exact: true }).click();
  await page.getByText('Saved in the first Work after visiting another Work.', { exact: true }).waitFor();
  assert.equal(await page.locator('.composer textarea').inputValue(), '',
    'Late origin save clears only its own unchanged draft');

  // A transport failure on Work A must be discoverable from Work B without
  // showing A's error in B or discarding either page's draft. An uncertain
  // result is not automatically retried: the user returns to its source.
  const failedText = 'Check this message before deciding whether to resend.';
  let releaseFailure;
  const failureGate = new Promise(resolve => { releaseFailure = resolve; });
  let signalFailureHeld;
  const failureHeld = new Promise(resolve => { signalFailureHeld = resolve; });
  let signalFailureHandled;
  const failureHandled = new Promise(resolve => { signalFailureHandled = resolve; });
  await page.route(delayedPattern, async route => {
    signalFailureHeld();
    await failureGate;
    await route.abort('failed');
    signalFailureHandled();
  });
  await page.locator('.composer textarea').fill(failedText);
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await failureHeld;
  const navigation = page.getByLabel('Workspace navigation');
  await navigation.locator('.page-nav a').filter({ hasText: 'Second independent Work' }).click();
  await page.getByRole('heading', { name: 'Second independent Work', exact: true }).waitFor();
  const secondDraft = 'A separate draft survives the other Work failing.';
  await page.locator('.composer textarea').fill(secondDraft);
  releaseFailure();
  await failureHandled;
  await navigation.locator('.page-nav a').filter({ hasText: 'A real disconnected Work' })
    .locator('[aria-label="Check last message"]').waitFor();
  assert.equal(await page.locator('.composer textarea').inputValue(), secondDraft,
    'The other Work keeps its unsent draft');
  assert.equal(await page.locator('.page-column .error-notice').count(), 0,
    'The failed send does not display an unrelated error in the current Work');
  await page.unroute(delayedPattern);
  await navigation.locator('.page-nav a').filter({ hasText: 'A real disconnected Work' }).click();
  await page.getByRole('alert').getByText('Your workspace could not be reached. Please try again.').waitFor();
  assert.equal(await page.locator('.composer textarea').inputValue(), failedText,
    'The origin Work keeps its draft after uncertain save');
  // The controlled abort occurred before the request reached the native API.
  // Resending here is explicit and the actual World persists the content.
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await page.getByText(failedText, { exact: true }).waitFor();
  assert.equal(await navigation.locator('.page-nav a').filter({ hasText: 'A real disconnected Work' })
    .locator('[aria-label="Check last message"]').count(), 0,
    'A successful origin save clears the check-status marker');

  const bootstrap = await (await fetch(base + '/api/bootstrap')).json();
  assert.equal(bootstrap.provider.configured, false);
  assert.equal(bootstrap.pages.filter(item => item.kind === 'work').length, 2);
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
    passed: 7,
    checks: ['isolated wheel starts without credentials', 'browser offers local provider setup',
      'English/Chinese first-use instructions work', 'real disconnected Work accepts queued input',
      'two Works stay writable across a delayed origin request without losing its result or draft',
      'late origin save failures are visible at their source without hijacking another Work',
      'original World, Work and note persist after clean restart'],
    provider: 'none; no synthetic model replies',
  }, null, 2));
} finally {
  if (browser) await browser.close();
  await stop(server);
  await rm(directory, { recursive: true, force: true });
}
