import assert from 'node:assert/strict';
import test from 'node:test';
import { compactTokens, exactTokens, hasUsage, totalTokens } from '../src/pageUsage.ts';
import { workspaceEnglish, workspaceChinese } from '../src/locales/workspace.ts';

const usage = (input, output, unreportedRounds = 0) => ({ inputTokens: input, outputTokens: output, cachedTokens: 0, unreportedRounds, lastTurn: null });

test('usage appears only after the model service has reported some', () => {
  assert.equal(hasUsage(undefined), false);
  assert.equal(hasUsage(null), false);
  assert.equal(hasUsage(usage(0, 0, 3)), false);
  assert.equal(hasUsage(usage(1200, 300)), true);
  assert.equal(totalTokens(usage(1200, 300)), 1500);
});

test('totals are compact in the summary and exact in the details, in both languages', () => {
  assert.equal(compactTokens(12345, 'en'), '12.3K');
  assert.equal(compactTokens(12345, 'zh-CN'), '1.2万');
  assert.equal(exactTokens(12345, 'en'), '12,345');
  assert.equal(compactTokens(640, 'en'), '640');
});

test('English and Chinese copy say the numbers are reported, and when they are a lower bound', () => {
  for (const table of [workspaceEnglish, workspaceChinese]) {
    for (const key of ['usage.summary', 'usage.summaryAtLeast']) assert.match(table[key], /\{total\}/);
    assert.match(table['usage.unreported'], /\{count\}/);
  }
  assert.match(workspaceEnglish['usage.summaryAtLeast'], /^At least/);
  assert.match(workspaceEnglish['usage.source'], /reported by the model service/);
  assert.match(workspaceChinese['usage.summaryAtLeast'], /至少/);
});
