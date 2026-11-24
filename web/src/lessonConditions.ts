// Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
import type { LessonCondition } from './types';

export type ConditionPath = number[];
export type ConditionIssue = 'empty' | 'long' | 'complex' | null;

/** The native journal remains authoritative; these checks explain draft errors. */
export function conditionIssue(value: LessonCondition): ConditionIssue {
  let nodes = 0;
  const bytes = (text: string) => new TextEncoder().encode(text).length;
  const visit = (item: LessonCondition, depth: number): ConditionIssue => {
    if (++nodes > 32 || depth > 6) return 'complex';
    if (typeof item === 'string') return !item.trim() ? 'empty' : bytes(item) > 1000 ? 'long' : null;
    const children = 'not' in item ? [item.not] : 'all' in item ? item.all : item.any;
    if (!children.length || children.length > 12) return 'complex';
    for (const child of children) {
      const issue = visit(child, depth + 1);
      if (issue) return issue;
    }
    return null;
  };
  return visit(value, 0) ?? (bytes(JSON.stringify(value)) > 8192 ? 'long' : null);
}

/** Copy only the edited branch; never mutate the experience or another draft. */
export function replaceCondition(value: LessonCondition, path: ConditionPath, next: LessonCondition): LessonCondition {
  if (!path.length) return next;
  if (typeof value === 'string') throw new Error('A condition passage has no children.');
  const [index, ...rest] = path;
  if ('not' in value) {
    if (index !== 0) throw new Error('An exclusion has exactly one condition.');
    return { not: replaceCondition(value.not, rest, next) };
  }
  const children = 'all' in value ? value.all : value.any;
  if (index < 0 || index >= children.length) throw new Error('Unknown condition.');
  const updated = children.map((child, offset) => offset === index ? replaceCondition(child, rest, next) : child);
  return 'all' in value ? { all: updated } : { any: updated };
}
