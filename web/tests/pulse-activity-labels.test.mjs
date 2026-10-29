import assert from 'node:assert/strict';
import test from 'node:test';
import { pulseActivityLabel } from '../src/pulseActivityLabels.ts';
import { continuityEnglish, continuityChinese } from '../src/locales/continuity.ts';

const en = key => continuityEnglish[key];
const zh = key => continuityChinese[key];

test('native local activation state, type and deferral are readable in both languages', () => {
  for (const [field, value, expectedEn, expectedZh] of [
    ['status','queued','Queued','排队中'],
    ['status','uncertain','Outcome uncertain','结果待确认'],
    ['kind','propagation','Connected influence','连接传来的影响'],
    ['kind','spontaneous','Local reentry','局部再次激活'],
    ['reason','attention_held','Local attention held','此处的局部关注已暂停'],
    ['reason','operator_paused','Background paused by you','你已暂停后台计算'],
    ['reason','connection_revoked','Connection removed','计算连接已撤销'],
  ]) {
    assert.equal(pulseActivityLabel(en, field, value), expectedEn);
    assert.equal(pulseActivityLabel(zh, field, value), expectedZh);
  }
});

test('unrecognized native states and prototype-like values remain evidence, never fabricated translations', () => {
  for (const [field, value] of [
    ['kind','future_kind'], ['status','superseded'], ['reason','future_gate'],
    ['reason','constructor'], ['status','toString'],
  ]) {
    assert.equal(pulseActivityLabel(zh, field, value), value);
  }
});
