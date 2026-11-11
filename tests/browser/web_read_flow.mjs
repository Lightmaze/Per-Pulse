/** Native SELF -> web observation -> file/report -> source -> restart/recall.
 * Model and website HTTP are explicit synthetic fixtures. TLS is tested separately.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-web-reading-'));
const output = process.env.PER_PULSE_WEB_SCREENSHOTS || await mkdtemp(join(tmpdir(), 'per-pulse-web-screenshots-'));
await mkdir(output, { recursive: true });
const socket = createServer();
await new Promise(done => socket.listen(0, '127.0.0.1', done));
const port = socket.address().port;
await new Promise(done => socket.close(done));
const base = `http://127.0.0.1:${port}`, sourceUrl = 'https://reading.example/field-notes';
let serverLog = '', browser, page;
const checks = [], errors = [];
const installedPython = process.env.PER_PULSE_INSTALLED_PYTHON;
const start = () => {
  const environment = { ...process.env };
  if (installedPython) {
    delete environment.PYTHONPATH;
    delete environment.PYTHONHOME;
    for (const key of Object.keys(environment)) if (key.startsWith('P_PULSE_')) delete environment[key];
  } else environment.PYTHONPATH = join(root, 'src');
  const child = spawn(installedPython || process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [
    join(root, 'tests/browser/web_read_server.py'), '--data-dir', directory, '--port', String(port),
    ...(installedPython ? ['--installed'] : []),
  ], { cwd: installedPython ? directory : root, env: environment, stdio: ['ignore', 'pipe', 'pipe'] });
  child.stderr.on('data', data => { serverLog += data; });
  return child;
};
const stop = async child => {
  if (child.exitCode !== null || child.signalCode !== null) return;
  const exited = new Promise(done => child.once('exit', done));
  child.kill('SIGTERM');
  const force = setTimeout(() => child.kill('SIGKILL'), 10000);
  await exited; clearTimeout(force);
};
const get = async path => { const response = await fetch(base + path); assert.ok(response.ok); return response.json(); };
const wait = async (read, ready, label) => {
  const end = Date.now() + 30000;
  while (Date.now() < end) {
    try { const value = await read(); if (ready(value)) return value; } catch {}
    await new Promise(done => setTimeout(done, 80));
  }
  throw new Error(`Timed out: ${label}\n${serverLog}`);
};
let server = start();
try {
  await wait(() => fetch(base + '/api/health'), value => value.ok, 'native server');
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
    args: process.env.PLAYWRIGHT_CHROMIUM_ARGS ? JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS) : undefined });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  await context.route(sourceUrl, route => route.fulfill({ contentType: 'text/html',
    body: '<h1>Explicit synthetic website fixture</h1><p>Original field observation: café 山川 🌍.</p>' }));
  page = await context.newPage(); page.on('pageerror', error => errors.push(error.message));
  await page.goto(base);
  await page.getByRole('button', { name: 'Activity', exact: true }).click();
  await page.getByRole('spinbutton', { name: 'Requests per week', exact: true }).fill('20');
  await page.getByRole('button', { name: 'Start autonomous practice', exact: true }).click();
  const bootstrap = await wait(() => get('/api/bootstrap'), value => value.pages.some(p => p.title === 'Reading expedition'), 'native Work');
  const work = bootstrap.pages.find(p => p.title === 'Reading expedition').id;
  const view = await wait(() => get('/api/pages/' + work), value => value.page.status === 'idle' && value.artifacts.length === 1, 'saved sourced file');
  const source = view.execution.webSources.items[0];
  assert.equal(source.url, sourceUrl); assert.equal(source.truncated, true);
  const home = await wait(() => get('/api/pages/home'), value => value.messages.some(m => m.sourcePageId === work), 'Home report');
  const status = await get('/__web/status');
  assert.equal(status.installed, Boolean(installedPython));
  const usage = await wait(() => get('/api/pulse/weekly-usage'), value => value.usage.consumed === status.requests
    && value.usage.states.completed === status.requests, 'all model requests accounted');
  assert.equal(status.webRequests, 1);
  checks.push('one global enable reaches native SELF, Work, a partial web observation, a real file and a sourced Home report under weekly accounting');
  await page.getByRole('button', { name: 'Pause autonomous practice', exact: true }).click();
  // Closing Activity must not trap the user behind a pending native weekly update.
  // The native operation continues, so verify its outcome before navigating.
  await page.getByRole('button', { name: 'Close dialog', exact: true }).click();
  await page.locator('.pulse-activity-dialog').waitFor({ state: 'detached' });
  await wait(() => get('/api/pulse/weekly-usage'),
    value => value.usage.enabled === false, 'autonomous practice paused');
  const report = home.messages.find(m => m.sourcePageId === work);
  await page.locator('#message-' + report.id).locator('.source-line a.session-link').click();
  await page.getByRole('heading', { name: 'Reading expedition', exact: true }).waitFor();
  await page.locator('.web-sources summary').click();
  await page.getByText("Only part of this page's text was read.", { exact: true }).waitFor();
  const link = page.locator('.web-sources a');
  assert.equal(await link.getAttribute('href'), sourceUrl);
  assert.equal(await link.getAttribute('rel'), 'noopener noreferrer');
  const popupPromise = context.waitForEvent('page'); await link.click();
  const popup = await popupPromise; await popup.waitForLoadState();
  assert.equal(popup.url(), sourceUrl); await popup.close();
  await page.screenshot({ path: join(output, 'web-source-desktop.png') });
  checks.push('the original Work renders the verified source with text-only coverage and opens the exact URL through the existing safe link renderer');
  const draft = 'Keep this reading draft.';
  await page.getByRole('textbox', { name: /^Message / }).fill(draft);
  await page.goto('about:blank'); await stop(server); server = start();
  await wait(() => fetch(base + '/api/health'), value => value.ok, 'restart');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(base + '/page/' + work);
  const composer = page.getByRole('textbox', { name: /^Message / }); await composer.waitFor();
  assert.equal(await composer.inputValue(), draft);
  assert.deepEqual((await get('/api/pages/' + work)).execution.webSources.items, [source]);
  assert.equal((await get('/__web/status')).requests, 0);
  assert.equal((await get('/__web/status')).webRequests, 0);
  await composer.fill('Recall the original webpage from saved history.');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await wait(() => get('/api/pages/' + work), value => value.messages.at(-1).content === 'Recovered the original saved webpage observation.', 'history recall');
  await page.getByText('Recovered the original saved webpage observation.', { exact: true }).waitFor();
  assert.equal((await get('/__web/status')).webRequests, 0);
  assert.equal((await get('/api/pulse/weekly-usage')).usage.consumed, usage.usage.consumed);
  await page.locator('.web-sources summary').click();
  await page.locator('.web-sources').scrollIntoViewIfNeeded();
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({ path: join(output, 'web-source-mobile.png') });
  checks.push('restart preserves the source and unsent draft; mobile recall reads the original history without refetching or autonomous spending');
  await composer.fill('Read an unsupported webpage.');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await page.getByText('This URL uses HTTP. Try a public HTTPS text page.', { exact: true }).waitFor();
  assert.equal((await get('/__web/status')).webRequests, 0);
  assert.deepEqual((await get('/api/pages/' + work)).execution.webSources.items, [source]);
  await composer.fill('Recall the original webpage from saved history.');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await wait(() => get('/api/pages/' + work), value => value.page.status === 'idle'
    && value.messages.filter(m => m.content === 'Recovered the original saved webpage observation.').length === 2, 'continue after unreadable URL');
  checks.push('an unreadable URL has a useful visible reason; the existing source survives and the next message can continue');
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: checks.length, checks, installed: Boolean(installedPython), provider: 'synthetic model and website HTTP', output }));
} catch (error) {
  await writeFile(join(output, 'web-server.log'), serverLog);
  if (page) await page.screenshot({ path: join(output, 'web-failure.png') }).catch(() => {});
  throw error;
} finally {
  await browser?.close(); await stop(server); await rm(directory, { recursive: true, force: true });
}
