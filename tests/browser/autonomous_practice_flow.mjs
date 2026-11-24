/** Synthetic model decisions on the actual World, Kernel, files and restart path.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, readFile, rm, stat } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-autonomous-'));
const output = process.env.PER_PULSE_AUTONOMOUS_SCREENSHOTS || await mkdtemp(join(tmpdir(), 'per-pulse-autonomous-browser-'));
await mkdir(output, { recursive: true });
const socket = createServer();
await new Promise(done => socket.listen(0, '127.0.0.1', done));
const port = socket.address().port;
await new Promise(done => socket.close(done));
const base = `http://127.0.0.1:${port}`;
let serverLog = '';
const start = (dataDir = directory, purposeRevision = false) => {
  const child = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [
    join(root, 'tests/browser/autonomous_practice_server.py'), '--data-dir', dataDir, '--port', String(port),
    ...(purposeRevision ? ['--purpose-revision'] : []),
  ], { cwd: root, env: { ...process.env, PYTHONPATH: join(root, 'src') }, stdio: ['ignore', 'pipe', 'pipe'] });
  child.stderr.on('data', data => { serverLog += data; });
  return child;
};
const stop = async child => {
  if (child.exitCode !== null || child.signalCode !== null) return;
  await new Promise(done => { child.once('exit', done); child.kill('SIGTERM'); });
};
const get = async path => (await fetch(base + path)).json();
const wait = async (read, ready, label) => {
  const end = Date.now() + 20000;
  while (Date.now() < end) {
    try { const value = await read(); if (ready(value)) return value; } catch {}
    await new Promise(done => setTimeout(done, 80));
  }
  throw new Error(`Timed out: ${label}\n${serverLog}`);
};
const checks = [];
const errors = [];
let server = start();
let browser;
try {
  await wait(() => fetch(base + '/api/health'), value => value.ok, 'native server');
  const fresh = await get('/__autonomous/status');
  assert.equal(fresh.requests, 0);
  assert.deepEqual(fresh.departures, []);
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
    args: process.env.PLAYWRIGHT_CHROMIUM_ARGS ? JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS) : undefined });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base);
  await page.getByRole('button', { name: 'Activity', exact: true }).click();
  await page.getByRole('spinbutton', { name: 'Requests per week', exact: true }).fill('1');
  await page.getByRole('button', { name: 'Start autonomous practice', exact: true }).click();
  await wait(() => get('/api/pulse/weekly-usage'), value => value.usage.consumed === 1 && value.usage.remaining === 0, 'first request exhausts allowance');
  await wait(() => get('/__autonomous/status'), value => value.departures[0]?.status === 'uncertain', 'original turn retains its partial outcome');
  await page.getByText('Waiting for autonomous allowance', { exact: true }).waitFor();
  await page.getByRole('spinbutton', { name: 'Requests per week', exact: true }).fill('20');
  await page.getByRole('button', { name: 'Save limit', exact: true }).click();
  const pages = await wait(() => get('/api/bootstrap'), value => value.pages.some(p => p.title === 'A small practice'), 'model-created Work');
  const work = pages.pages.find(p => p.title === 'A small practice').id;
  await wait(() => get('/api/pages/' + work), value => value.artifacts.length === 1 && value.page.status === 'idle', 'native file and completed Work');
  const home = await wait(() => get('/api/pages/home'), value => value.messages.filter(m => m.role === 'assistant').length >= 2, 'native report and Home response');
  const complete = await get('/__autonomous/status');
  const usage = await wait(() => get('/api/pulse/weekly-usage'), value => value.usage.consumed === complete.requests
    && value.usage.states.completed === complete.requests, 'unified physical request accounting');
  assert.equal(complete.departures.length, 1);
  assert.equal(complete.departures[0].source, 'self');
  assert.equal(complete.departures[0].parent, null);
  assert.deepEqual(complete.userRoots, []);
  assert.ok(usage.usage.consumed >= 6 && usage.usage.consumed <= 20);
  checks.push('one explicit enable starts from SELF without a user message and reaches a native Work, file and report within one allowance');
  await page.getByRole('button', { name: 'Close dialog', exact: true }).click();
  assert.match(await page.locator('#message-' + complete.departures[0].id).getAttribute('class'), /message-system/);
  const report = home.messages.find(message => message.activation === 'work_report');
  assert.ok(report?.sourceMessageId && report.sourcePageId === work);
  await page.locator('#message-' + report.id).locator('.source-line a.session-link').click();
  await page.getByRole('heading', { name: 'A small practice', exact: true }).waitFor();
  assert.equal(await page.locator('.message-user').count(), 0);
  await page.locator('.message-system').filter({ hasText: 'AUTONOMOUS_PRACTICE_FIXTURE' }).waitFor();
  await page.getByRole('button', { name: /practice\.txt/ }).click();
  await page.getByText('A persisted result from explicitly synthetic autonomous practice.', { exact: true }).waitFor();
  await page.screenshot({ path: join(output, 'autonomous-file-desktop.png') });
  await page.getByRole('button', { name: /practice\.txt/ }).click();
  checks.push('Home shows the SELF origin as workspace activity and the actual report opens its producing Work and exact file');
  const draft = 'Keep this practice draft across pause and restart.';
  await page.getByRole('textbox', { name: /^Message / }).fill(draft);
  await page.getByRole('button', { name: 'Activity', exact: true }).click();
  // Editing the next allowance must not lower it on Pause. A different tab
  // may also save a *newer* World limit while this Activity view is open.
  await page.getByRole('spinbutton', { name: 'Requests per week', exact: true }).fill('2');
  const observation = await get('/api/pulse/observation');
  const changedElsewhere = await fetch(base + '/api/pulse/weekly-usage', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json',
      'X-Per-Pulse-World': observation.workspaceId,
      'X-Per-Pulse-Service-Instance': observation.serviceInstanceId },
    body: JSON.stringify({ enabled: true, weeklyLimit: 23 }),
  });
  assert.equal(changedElsewhere.status, 200, await changedElsewhere.text());
  const pauseRequest = page.waitForRequest(request => request.url().endsWith('/api/pulse/weekly-usage')
    && request.method() === 'PATCH' && request.postDataJSON()?.enabled === false);
  await page.getByRole('button', { name: 'Pause autonomous practice', exact: true }).click();
  assert.deepEqual((await pauseRequest).postDataJSON(), { enabled: false },
    'Pause must not send an allowance from an outdated Activity view');
  await page.getByText('Autonomous model requests are disabled.', { exact: true }).waitFor();
  const paused = await get('/api/pulse/weekly-usage');
  assert.equal(paused.usage.enabled, false);
  assert.equal(paused.usage.weekly_limit, 23);
  assert.equal(await page.getByRole('spinbutton', { name: 'Requests per week', exact: true }).inputValue(), '23');
  await page.getByRole('button', { name: 'Close dialog', exact: true }).click();
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.locator('#interface-language').selectOption('zh-CN');
  await page.getByRole('button', { name: '关闭对话框', exact: true }).click();
  await page.getByRole('button', { name: '活动', exact: true }).click();
  await page.getByRole('button', { name: '开始自主实践', exact: true }).click();
  await wait(() => get('/api/pulse/weekly-usage'), value => value.usage.enabled, 'Chinese resume');
  assert.equal((await get('/__autonomous/status')).requests, complete.requests);
  assert.equal((await get('/__autonomous/status')).departures[0].id, complete.departures[0].id);
  // An unfinished empty/invalid edit must never make Pause unavailable.
  await page.locator('.pulse-activity-section input[type="number"]').first().fill('');
  await page.getByRole('button', { name: '暂停自主实践', exact: true }).click();
  await page.getByText('自主模型请求目前未启用。', { exact: true }).waitFor();
  const pausedAgain = await get('/api/pulse/weekly-usage');
  assert.equal(pausedAgain.usage.enabled, false);
  assert.equal(pausedAgain.usage.weekly_limit, 23);
  assert.equal(await page.locator('.pulse-activity-section input[type="number"]').first().inputValue(), '23');
  checks.push('English and Chinese Pause omit allowance updates, preserve a newer policy saved from another view, and resume the same SELF departure');
  await stop(server);
  server = start();
  await wait(() => fetch(base + '/api/health'), value => value.ok, 'restarted native server');
  await page.reload();
  await page.locator('.composer textarea').waitFor();
  assert.equal(await page.locator('.composer textarea').inputValue(), draft);
  const retained = await get('/api/pulse/weekly-usage');
  assert.equal(retained.usage.enabled, false);
  assert.equal(retained.usage.consumed, usage.usage.consumed);
  const restarted = await get('/__autonomous/status');
  assert.equal(restarted.requests, 0);
  assert.equal(restarted.departures[0].id, complete.departures[0].id);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: /practice\.txt/ }).click();
  await page.getByText('A persisted result from explicitly synthetic autonomous practice.', { exact: true }).waitFor();
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
  await page.screenshot({ path: join(output, 'autonomous-file-mobile.png') });
  checks.push('SQLite restart preserves pause, usage, Work draft, SELF identity and the real file with no replay on mobile');
  // A second isolated journey follows one Work through a retained SELF
  // action, a user revision, an allowance hold and delivery after restart.
  await page.close();
  await stop(server);
  const revisionDirectory = join(directory, 'purpose-revision');
  await mkdir(revisionDirectory);
  server = start(revisionDirectory, true);
  await wait(() => fetch(base + '/api/health'), value => value.ok, 'purpose-revision server');
  const revisionPage = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  revisionPage.on('pageerror', error => errors.push(error.message));
  await revisionPage.goto(base);
  await revisionPage.getByRole('button', { name: 'Create a page', exact: true }).click();
  const title = 'Retained work';
  await revisionPage.getByRole('textbox', { name: 'Page name', exact: true }).fill(title);
  await revisionPage.getByRole('textbox', { name: 'Where should we begin?', exact: true }).fill('Initial context.');
  await revisionPage.getByRole('button', { name: 'Create page', exact: true }).click();
  await revisionPage.getByRole('heading', { name: title, exact: true }).waitFor();
  const revisedWork = decodeURIComponent(new URL(revisionPage.url()).pathname.split('/').pop());
  const workView = () => get('/api/pages/' + revisedWork);
  await wait(workView, value => value.page.status === 'idle' && value.messages.some(m => m.role === 'assistant'), 'initial Work');
  // Seed the same host-owned SELF activation as the native regression.
  // Actual work and subsequent user interaction use production paths.
  const world = await get('/api/pulse/observation');
  const configured = await fetch(base + '/api/pulse/weekly-usage', {
    method: 'PATCH', headers: { 'Content-Type': 'application/json',
      'X-Per-Pulse-World': world.workspaceId, 'X-Per-Pulse-Service-Instance': world.serviceInstanceId },
    body: JSON.stringify({ enabled: true, weeklyLimit: 1 }),
  });
  assert.equal(configured.status, 200, await configured.text());
  const queued = await fetch(base + '/__autonomous/reentry/' + revisedWork, { method: 'POST' });
  assert.equal(queued.status, 200, await queued.text());
  const held = await wait(workView, value => value.execution.providerWaiting && value.artifacts.length === 1, 'retained file and allowance hold');
  assert.equal(held.execution.recoveryRequired, false);
  const originalPath = join(revisionDirectory, 'workspace', 'retained.txt');
  assert.equal(await readFile(originalPath, 'utf8'), 'Keep this exact effect.\n');
  const originalMtime = (await stat(originalPath, { bigint: true })).mtimeNs;
  const receipt = await wait(() => get('/__autonomous/status'), value => value.continuations.some(c => c.status === 'queued'), 'durable continuation');
  const continuation = receipt.continuations.find(c => c.status === 'queued').id;
  const revisionTask = 'Change the purpose: produce a planning brief instead.';
  const composer = revisionPage.getByRole('textbox', { name: 'Message ' + title, exact: true });
  await composer.fill(revisionTask);
  await revisionPage.getByRole('button', { name: 'Send message', exact: true }).click();
  await revisionPage.getByText('Revision accepted. [Synthetic fixture]', { exact: true }).waitFor();
  await wait(workView, value => value.page.status !== 'working' && value.messages.some(m => m.role === 'user' && m.content === revisionTask), 'actual user revision');
  assert.equal((await get('/__autonomous/status')).continuations.find(c => c.id === continuation).status, 'queued');
  assert.equal((await get('/api/pulse/weekly-usage')).usage.consumed, 1);
  assert.equal((await stat(originalPath, { bigint: true })).mtimeNs, originalMtime);
  checks.push('a real user revises the same held Work while its confirmed file and pending SELF continuation remain intact');

  await stop(server);
  server = start(revisionDirectory, true);
  await wait(() => fetch(base + '/api/health'), value => value.ok, 'purpose-revision restart');
  await revisionPage.reload();
  await composer.waitFor();
  assert.equal((await get('/__autonomous/status')).requests, 0);
  assert.equal((await get('/__autonomous/status')).continuations.find(c => c.id === continuation).status, 'queued');
  await revisionPage.getByRole('button', { name: 'Activity', exact: true }).click();
  await revisionPage.getByRole('spinbutton', { name: 'Requests per week', exact: true }).fill('20');
  await revisionPage.getByRole('button', { name: 'Save limit', exact: true }).click();
  await wait(() => get('/__autonomous/status'), value => value.continuations.some(c => c.id === continuation && c.status === 'settled'), 'same retained continuation settles');
  const revisedHome = await wait(() => get('/api/pages/home'), value => value.page.status === 'idle'
    && value.messages.some(m => m.activation === 'work_report' && m.sourcePageId === revisedWork && m.reportedArtifact), 'revised result reaches Home');
  const revisedReport = revisedHome.messages.find(m => m.activation === 'work_report' && m.sourcePageId === revisedWork && m.reportedArtifact);
  assert.ok(revisedReport.sourceMessageId);
  const inputs = (await get('/__autonomous/status')).resumeInputs.filter(r => r.context.startsWith('work:'));
  assert.ok(inputs.length > 0 && inputs.every(r => r.latestUser === revisionTask));
  assert.equal(await readFile(originalPath, 'utf8'), 'Keep this exact effect.\n');
  assert.equal((await stat(originalPath, { bigint: true })).mtimeNs, originalMtime);
  const brief = '# Planning brief\nVerify the assumptions before release.\n';
  assert.equal(await readFile(join(revisionDirectory, 'workspace', 'planning-brief.md'), 'utf8'), brief);
  const resumedUsage = await wait(() => get('/api/pulse/weekly-usage'), value =>
    value.usage.consumed === value.usage.states.completed && value.usage.consumed > 1, 'settled allowance accounting');
  assert.ok(resumedUsage.usage.consumed <= 20);
  assert.equal(resumedUsage.usage.consumed, 1 + (await get('/__autonomous/status')).requests);
  await revisionPage.getByRole('button', { name: 'Pause autonomous practice', exact: true }).click();
  await revisionPage.getByText('Autonomous model requests are disabled.', { exact: true }).waitFor();
  await revisionPage.getByRole('button', { name: 'Close dialog', exact: true }).click();
  await revisionPage.goto(base);
  const reportArticle = revisionPage.locator('#message-' + revisedReport.id);
  assert.equal(revisedReport.reportedArtifact.path, 'planning-brief.md');
  assert.equal(revisedReport.reportedArtifact.pageId, revisedWork);
  const downloadBrief = async (screenshot) => {
    // The report starts collapsed. Open its version-checked original before
    // downloading, just as the person must do on Home after each restart.
    await reportArticle.getByRole('button', { name: 'Open verified file', exact: true }).click();
    await reportArticle.getByText('Verify the assumptions before release.', { exact: true }).waitFor();
    const button = reportArticle.getByRole('button', { name: 'Download file', exact: true });
    await button.waitFor({ state: 'visible' });
    const [download] = await Promise.all([
      revisionPage.waitForEvent('download'), button.click(),
    ]);
    assert.equal(download.suggestedFilename(), 'planning-brief.md');
    const bytes = await readFile(await download.path());
    assert.equal(bytes.toString('utf8'), brief);
    assert.equal(createHash('sha256').update(bytes).digest('hex'), revisedReport.reportedArtifact.sha256);
    await revisionPage.screenshot({ path: join(output, screenshot) });
    await reportArticle.getByRole('button', { name: 'Close file preview', exact: true }).click();
  };
  await downloadBrief('revised-home-delivery.png');
  await reportArticle.locator('.source-line a.session-link').click();
  await revisionPage.getByRole('heading', { name: title, exact: true }).waitFor();
  await revisionPage.getByRole('button', { name: /planning-brief\.md/ }).click();
  await revisionPage.getByText('Verify the assumptions before release.', { exact: true }).waitFor();
  checks.push('restoring allowance after restart uses the actual revised user message, produces a different file without replay and delivers exact bytes to Home');

  await stop(server);
  server = start(revisionDirectory, true);
  await wait(() => fetch(base + '/api/health'), value => value.ok, 'paused result restart');
  await revisionPage.goto(base);
  await downloadBrief('revised-home-restart.png');
  assert.equal((await get('/__autonomous/status')).requests, 0);
  assert.equal((await get('/api/pulse/weekly-usage')).usage.consumed, resumedUsage.usage.consumed);
  assert.equal((await stat(originalPath, { bigint: true })).mtimeNs, originalMtime);
  checks.push('paused restart preserves the revised Home deliverable, original effect and allowance without further provider requests');
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: checks.length, checks, output,
    modelEvidence: 'explicit synthetic HTTP decisions; production World/Kernel/SQLite/files/accounting' }));
} finally {
  await browser?.close();
  await stop(server);
  await rm(directory, { recursive: true, force: true });
}
