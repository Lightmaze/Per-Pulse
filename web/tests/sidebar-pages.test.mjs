/** Long-lived Work sidebar navigation against a realistic large page list.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { SIDEBAR_PAGE_BATCH, sidebarPageWindow } from '../src/sidebarPages.ts';

const pages = Array.from({ length: 135 }, (_, index) => ({
  id: `work-${index + 1}`, title: `Work ${index + 1}`,
  description: index === 134 ? 'The oldest source to recover' : 'Ordinary persistent work',
}));

test('bounded initial sidebar still exposes the selected deep Work and original identities', () => {
  const view = sidebarPageWindow(pages, '', 'work-135', SIDEBAR_PAGE_BATCH);
  assert.equal(view.total, 135);
  assert.equal(view.shown, 48);
  assert.equal(view.hasMore, true);
  assert.deepEqual(view.visible.map(page => page.id).slice(0, 3), ['work-135', 'work-1', 'work-2']);
  assert.equal(new Set(view.visible.map(page => page.id)).size, view.visible.length);
  assert.equal(view.visible.length, 49);
  assert.deepEqual(pages.map(page => page.id).slice(0, 2), ['work-1', 'work-2'], 'source array must be unchanged');
});

test('showing more eventually reveals every Work with no duplicates', () => {
  const first = sidebarPageWindow(pages, '', 'work-1', SIDEBAR_PAGE_BATCH);
  assert.equal(first.visible.length, 48);
  const second = sidebarPageWindow(pages, '', 'work-1', SIDEBAR_PAGE_BATCH * 2);
  assert.equal(second.visible.length, 96);
  const final = sidebarPageWindow(pages, '', 'work-1', SIDEBAR_PAGE_BATCH * 3);
  assert.equal(final.visible.length, pages.length);
  assert.equal(final.shown, 135);
  assert.equal(final.hasMore, false);
});

test('filter finds old Work by title or description before any list expansion', () => {
  const byTitle = sidebarPageWindow(pages, 'WORK 135', 'home', SIDEBAR_PAGE_BATCH);
  const byDescription = sidebarPageWindow(pages, 'oldest source', 'home', SIDEBAR_PAGE_BATCH);
  assert.deepEqual(byTitle.visible.map(page => page.id), ['work-135']);
  assert.deepEqual(byDescription.visible.map(page => page.id), ['work-135']);
  assert.equal(byTitle.hasMore, false);
  assert.equal(sidebarPageWindow(pages, 'not present', 'work-135', SIDEBAR_PAGE_BATCH).visible.length, 0);
});

test('empty and small workspaces preserve their original behavior', () => {
  assert.deepEqual(sidebarPageWindow([], '', 'home', 48).visible, []);
  const small = sidebarPageWindow(pages.slice(0, 2), '', 'work-2', 48);
  assert.deepEqual(small.visible.map(page => page.id), ['work-1', 'work-2']);
  assert.equal(small.hasMore, false);
});
