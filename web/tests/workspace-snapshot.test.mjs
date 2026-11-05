// Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const source = await readFile(new URL('../src/workspaceSnapshot.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } });
const { mergeWorkspaceSnapshot, pageBelongsToWorkspace } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);
const snapshot = pages => ({ workspaceId: 'world', serviceInstanceId: 'instance', pages });

test('a delayed workspace read cannot undo a rename or a newly settled local result', () => {
  const current = snapshot([{ id: 'work', title: 'Revised title', status: 'idle' }, { id: 'other', status: 'working' }]);
  const observed = snapshot([{ id: 'work', title: 'Old title', status: 'working' }, { id: 'other', status: 'idle' }]);
  const next = mergeWorkspaceSnapshot(current, observed, { work: 1, other: 1 }, { work: 2, other: 1 });
  assert.deepEqual(next.pages, [current.pages[0], observed.pages[1]]);
  assert.equal(current.pages[1].status, 'working');
});

test('creating a page while a workspace read is pending does not lose its sidebar entry', () => {
  const home = { id: 'home', status: 'idle' }, created = { id: 'new-work', status: 'queued' };
  const next = mergeWorkspaceSnapshot(snapshot([home, created]), snapshot([home]), { home: 1 }, { home: 1, 'new-work': 1 });
  assert.deepEqual(next.pages, [home, created]);
});

test('Home discovery adds an already idle Work without rolling back newer local page views', () => {
  const home = { id: 'home', title: 'Home', status: 'working' };
  const renamed = { id: 'existing', title: 'Revised name', status: 'idle' };
  const discovered = { id: 'native-created', title: 'A fast finished Work', status: 'idle' };
  const current = snapshot([home, renamed]);
  const observed = snapshot([
    { ...home, status: 'idle' }, { ...renamed, title: 'Old name', status: 'working' }, discovered,
  ]);
  const merged = mergeWorkspaceSnapshot(current, observed,
    { home: 1, existing: 1 }, { home: 2, existing: 2 });
  assert.deepEqual(merged.pages, [home, renamed, discovered]);
  assert.deepEqual(current.pages, [home, renamed]);
});

test('explicit observations from a new World or service never merge old page summaries', () => {
  const current = snapshot([{ id: 'home', title: 'Private old context' }]);
  for (const identity of [{ workspaceId: 'another-world' }, { serviceInstanceId: 'restarted' }]) {
    const observed = { ...snapshot([{ id: 'home', title: 'Current context' }]), ...identity };
    assert.equal(mergeWorkspaceSnapshot(current, observed, {}, { home: 2 }), observed);
  }
});


test('page views render only in the exact native World and process that produced them', () => {
  const current = snapshot([{ id: 'home', title: 'Private old context' }]);
  const original = { workspaceId: current.workspaceId, serviceInstanceId: current.serviceInstanceId };
  assert.equal(pageBelongsToWorkspace(original, current), true);
  assert.equal(pageBelongsToWorkspace({ ...original, workspaceId: 'different-world' }, current), false);
  assert.equal(pageBelongsToWorkspace({ ...original, serviceInstanceId: 'new-process' }, current), false);
  assert.equal(pageBelongsToWorkspace(original, null), false);
  assert.equal(pageBelongsToWorkspace(null, current), false);
  assert.equal(pageBelongsToWorkspace({ ...original, serviceInstanceId: '' }, current), false);
});
