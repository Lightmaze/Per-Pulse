/** Bounded table reading of actual uploaded files, with no connected provider.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-tables-'));
const screenshots = process.env.PER_PULSE_TABLE_SCREENSHOTS;
const listener = createServer();
await new Promise(done => listener.listen(0, '127.0.0.1', done));
const port = listener.address().port;
await new Promise(done => listener.close(done));
const base = `http://127.0.0.1:${port}`;
const env = { ...process.env };
for (const key of ['P_PULSE_CHAT_ENDPOINT', 'P_PULSE_CHAT_MODEL', 'P_PULSE_API_KEY', 'P_PULSE_CREDENTIAL_ENV']) delete env[key];
let output = '', browser;
const checks = [], errors = [];
const server = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [
  '-m', 'per_pulse.cli', '--data-dir', directory, '--port', String(port),
], { cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'] });
server.stdout.on('data', data => { output += data; });
server.stderr.on('data', data => { output += data; });
async function wait(fn, label) {
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    try { if (await fn()) return; } catch {}
    await new Promise(done => setTimeout(done, 80));
  }
  throw new Error(`Timed out: ${label}\n${output}`);
}
try {
  await wait(async () => (await fetch(base + '/api/health')).ok, 'service');
  browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, locale: 'en-US' });
  const page = await context.newPage();
  page.setDefaultTimeout(12000);
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base);
  await page.locator('.composer textarea').fill('Keep this question while inspecting the source.');
  await page.getByRole('button', { name: 'Materials', exact: true }).click();
  await wait(async () => !(await page.getByRole('button', { name: 'Refresh', exact: true }).isDisabled()), 'listing');
  const upload = async (name, text) => {
    await page.getByLabel('Choose a text file', { exact: true }).setInputFiles({ name, mimeType: 'text/plain', buffer: Buffer.from(text) });
    await page.getByRole('button', { name: 'Upload file', exact: true }).click();
    await page.getByRole('button', { name: 'Add to draft', exact: true }).waitFor();
  };
  const shot = async name => {
    if (screenshots) { await mkdir(screenshots, { recursive: true }); await page.screenshot({ path: join(screenshots, name + '.png'), fullPage: true }); }
  };
  const csv = '\uFEFFname,value,note,meaning,source,missing\r\n"A, B",001.00,"line 1\r\nline ""2""",=1+1,https://example.invalid/source,\r\nC,0,<script>window.csvRan=true</script>,literal,text,\r\n';
  await upload('Observation table.csv', csv);
  await page.getByText('2 data rows · 6 columns', { exact: true }).waitFor();
  assert.equal(await page.getByRole('columnheader', { name: 'value', exact: true }).getAttribute('scope'), 'col');
  const cells = await page.locator('.delimited-scroll tbody tr').first().locator('td').allTextContents();
  assert.deepEqual(cells, ['A, B', '001.00', 'line 1\r\nline "2"', '=1+1', 'https://example.invalid/source', '']);
  assert.equal(await page.locator('.delimited-scroll a, .delimited-scroll script, .delimited-scroll img').count(), 0);
  assert.equal(await page.evaluate(() => window.csvRan), undefined);
  await shot('tables-desktop');
  await page.locator('.table-columns summary').click();
  await page.getByRole('button', { name: 'Hide all', exact: true }).click();
  await page.getByText('Choose at least one column to read the table.', { exact: true }).waitFor();
  assert.equal(await page.locator('.delimited-scroll table').count(), 0);
  await page.getByRole('checkbox', { name: '2. value', exact: true }).check();
  await page.getByRole('checkbox', { name: '4. meaning', exact: true }).check();
  assert.deepEqual(await page.locator('.delimited-scroll th').allTextContents(), ['value', 'meaning']);
  assert.deepEqual(await page.locator('.delimited-scroll tbody tr').first().locator('td').allTextContents(), ['001.00', '=1+1']);
  await page.getByRole('button', { name: 'Source', exact: true }).click();
  assert.equal(await page.locator('.document-source').textContent(), csv);
  await page.getByRole('button', { name: 'Read', exact: true }).click();
  await page.getByText('Columns: 2 of 6', { exact: true }).waitFor();
  assert.deepEqual(await page.locator('.delimited-scroll th').allTextContents(), ['value', 'meaning']);
  await shot('tables-selected-columns');
  await page.locator('.table-columns summary').focus();
  await page.keyboard.press('Enter');
  await page.getByRole('button', { name: 'Show all', exact: true }).click();
  await page.locator('.table-columns summary').click();
  assert.deepEqual(await page.locator('.delimited-scroll tbody tr').first().locator('td').allTextContents(), cells);
  checks.push('Column selection preserves original positions, exact values and full source; empty selection is recoverable and choices survive Read/Source');
  await page.getByLabel('First row contains headings', { exact: true }).uncheck();
  assert.equal(await page.getByRole('columnheader', { name: 'Column 1', exact: true }).count(), 1);
  assert.equal(await page.locator('.delimited-scroll tbody tr').count(), 3);
  await page.getByRole('button', { name: 'Source', exact: true }).click();
  assert.equal(await page.locator('.document-source').textContent(), csv);
  await page.getByRole('button', { name: 'Read', exact: true }).click();
  assert.equal(await page.getByLabel('First row contains headings', { exact: true }).isChecked(), false);
  checks.push('CSV keeps exact string values, quoted newlines and empty cells; headings are an explicit, retained reading choice');

  await page.setViewportSize({ width: 375, height: 812 });
  const region = page.getByRole('region', { name: 'Table in document', exact: true });
  await region.focus();
  await page.keyboard.press('ArrowRight');
  await wait(async () => region.evaluate(el => el.scrollLeft > 0), 'keyboard table scroll');
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  const footer = await page.getByRole('button', { name: 'Add to draft', exact: true }).boundingBox();
  assert.ok(footer.y >= 0 && footer.y + footer.height <= 812);
  await shot('tables-mobile');
  await page.getByRole('button', { name: 'Add to draft', exact: true }).click();
  assert.ok((await page.locator('.composer textarea').inputValue()).startsWith('Keep this question while inspecting the source.'));
  await page.getByRole('button', { name: 'Remove Observation table.csv from this message', exact: true }).waitFor();
  checks.push('A phone can scroll the table by keyboard and add the native source without losing the draft');

  await page.setViewportSize({ width: 1280, height: 900 });
  await page.getByRole('button', { name: 'Materials', exact: true }).click();
  await wait(async () => !(await page.getByRole('button', { name: 'Refresh', exact: true }).isDisabled()), 'listing again');
  await upload('Repeated observations.tsv', 'name\tvalue\n' + Array.from({ length: 225 }, (_, i) => `row${i}\t${i}`).join('\n'));
  await page.getByText('Partial preview: 200 complete data rows · 2 columns. More content is in the file.', { exact: true }).waitFor();
  assert.equal(await page.locator('.delimited-scroll tbody tr').count(), 200);
  assert.equal(await page.locator('.delimited-scroll tbody tr').last().locator('td').first().textContent(), 'row199');
  const large = 'name,value\n' + Array.from({ length: 120 }, (_, i) => `row${i},${'x'.repeat(800)}\n`).join('');
  await upload('Large source.csv', large);
  const completeRows = large.slice(0, 64000).split('\n').length - 2;
  await page.locator('.table-summary').filter({ hasText: 'Partial preview:' }).waitFor();
  assert.equal(await page.locator('.delimited-scroll tbody tr').count(), completeRows);
  assert.ok((await page.locator('.delimited-scroll tbody tr').last().locator('td').last().textContent()).length === 800);
  checks.push('Row limits and the server preview boundary remain visible; a cut-off value never becomes a complete table row');

  await upload('Unclosed quote.csv', 'name,value\nA,"unfinished');
  await page.getByText('The rows or quotes do not form a regular table. The source text is shown below.', { exact: true }).waitFor();
  assert.equal(await page.locator('.document-source').textContent(), 'name,value\nA,"unfinished');
  await upload('Repeated headings.csv', 'Repeat,,Repeat\n001,missing,003\n');
  await page.getByText('Columns: 3 of 3', { exact: true }).waitFor();
  await page.locator('.table-columns summary').click();
  await page.getByRole('button', { name: 'Hide all', exact: true }).click();
  await page.getByRole('checkbox', { name: '3. Repeat', exact: true }).check();
  assert.deepEqual(await page.locator('.delimited-scroll tbody tr').first().locator('td').allTextContents(), ['003']);
  await page.getByRole('checkbox', { name: '2. Column 2', exact: true }).check();
  assert.deepEqual(await page.locator('.delimited-scroll tbody tr').first().locator('td').allTextContents(), ['missing', '003']);
  checks.push('Repeated and empty headings keep distinct column positions; a new file starts with all columns visible');
  await upload('Too many columns.csv', Array.from({ length: 65 }, () => 'value').join(','));
  await page.getByText('This table has more than 64 columns. The source text is shown below.', { exact: true }).waitFor();
  checks.push('Malformed and over-wide files remain available as original text');

  await page.keyboard.press('Escape');
  // Fill the native attachment selection without changing the authored draft.
  for (const name of ['Repeated observations.tsv', 'Large source.csv', 'Unclosed quote.csv', 'Too many columns.csv']) {
    await page.getByRole('button', { name: 'Materials', exact: true }).click();
    await wait(async () => !(await page.getByRole('button', { name: 'Refresh', exact: true }).isDisabled()), 'attachment listing');
    await page.locator('.material-list').getByRole('button', { name: 'uploads/', exact: true }).click();
    await page.locator('.material-list button').filter({ hasText: name }).click();
    await page.getByRole('button', { name: 'Add to draft', exact: true }).click();
    await page.getByRole('button', { name: `Remove ${name} from this message`, exact: true }).waitFor();
  }
  const fullDraft = 'Keep this long draft intact. ' + 'x'.repeat(31900);
  await page.locator('.composer textarea').fill(fullDraft);
  await page.getByRole('button', { name: 'Materials', exact: true }).click();
  await wait(async () => !(await page.getByRole('button', { name: 'Refresh', exact: true }).isDisabled()), 'listing for full draft');
  await page.locator('.material-list').getByRole('button', { name: 'uploads/', exact: true }).click();
  await page.locator('.material-list button').filter({ hasText: 'Observation table.csv' }).click();
  await page.getByRole('button', { name: 'Add to draft', exact: true }).click();
  const alert = page.locator('.material-footer').getByRole('alert');
  await alert.waitFor();
  const alertBounds = await alert.boundingBox();
  assert.ok(alertBounds.y >= 0 && alertBounds.y + alertBounds.height <= 900);
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('.composer textarea').inputValue(), fullDraft);
  checks.push('A full attachment selection reports the problem beside the fixed action and preserves existing draft text');
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.locator('#interface-language').selectOption('zh-CN');
  await page.getByRole('button', { name: '关闭对话框', exact: true }).click();
  await page.getByRole('button', { name: '材料', exact: true }).click();
  await wait(async () => !(await page.getByRole('button', { name: '刷新', exact: true }).isDisabled()), 'translated listing');
  await page.locator('.material-list').getByRole('button', { name: 'uploads/', exact: true }).click();
  await page.locator('.material-list button').filter({ hasText: 'Observation table.csv' }).click();
  await page.getByText('2 行数据 · 6 列', { exact: true }).waitFor();
  await page.getByLabel('首行是列名', { exact: true }).waitFor();
  await page.getByText('显示列：6 / 6', { exact: true }).click();
  await page.getByRole('button', { name: '全部隐藏', exact: true }).click();
  await page.getByText('请至少选择一列以阅读表格。', { exact: true }).waitFor();
  await page.getByRole('button', { name: '全部显示', exact: true }).click();
  assert.equal(await page.locator('.delimited-scroll th').count(), 6);
  assert.deepEqual(errors, []);
  assert.equal((await fetch(base + '/api/health').then(r => r.json())).providerConfigured, false);
  checks.push('Bilingual table controls use the same saved file without a provider call');
  console.log(JSON.stringify({ ok: true, checks, browserErrors: errors, provider: 'disconnected', source: 'explicit synthetic uploaded text' }, null, 2));
} catch (error) {
  console.error(output.slice(-7000));
  throw error;
} finally {
  if (browser) await browser.close();
  if (server.exitCode === null && server.signalCode === null) {
    server.kill('SIGTERM');
    const force = setTimeout(() => server.kill('SIGKILL'), 9000);
    await new Promise(done => server.once('exit', done));
    clearTimeout(force);
  }
  await rm(directory, { recursive: true, force: true });
}
