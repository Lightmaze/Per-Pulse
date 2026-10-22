import type { MouseEvent } from 'react';
import type { NavigationOrigin } from '../types';
import { pageHref } from '../storage';
import { Icon } from './Icon';

export type Navigate = (id: string, origin?: NavigationOrigin, targetMessageId?: string) => void;

export function interceptNavigation(event: MouseEvent<HTMLAnchorElement>, onNavigate: () => void) {
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return;
  event.preventDefault();
  onNavigate();
}

export function SessionLink({ pageId, label, referenceId, origin, navigate, targetMessageId }: {
  pageId: string; label: string; referenceId?: string; origin?: NavigationOrigin; navigate: Navigate; targetMessageId?: string;
}) {
  return <a className="session-link" href={pageHref(pageId)} data-reference-id={referenceId} onClick={event => interceptNavigation(event, () => navigate(pageId, origin, targetMessageId))}>
    <Icon name="page" size={17} /><span>{label}</span>
  </a>;
}
