import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';
import { pageFromLocation, pageHref, savePosition } from './storage';
import type { Bootstrap, NavigationOrigin, PageView } from './types';

type Route = { id: string; serial: number; origin?: NavigationOrigin; targetMessageId?: string };
export function useWorkspace() {
  const [bootstrap, setBootstrap] = useState<Bootstrap | null>(null);
  const bootstrapRef = useRef<Bootstrap | null>(null);
  bootstrapRef.current = bootstrap;
  const [views, setViews] = useState<Record<string, PageView>>({});
  const [route, setRoute] = useState<Route>(() => ({ id: pageFromLocation(), serial: 0 }));
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const versions = useRef<Record<string, number>>({});
  const routeRef = useRef(route);
  routeRef.current = route;

  const applyView = useCallback((view: PageView) => {
    versions.current[view.page.id] = (versions.current[view.page.id] ?? 0) + 1;
    setViews(previous => ({ ...previous, [view.page.id]: view }));
    setBootstrap(previous => previous ? { ...previous, provider: view.provider, pages: previous.pages.some(page => page.id === view.page.id) ? previous.pages.map(page => page.id === view.page.id ? view.page : page) : [...previous.pages, view.page] } : previous);
  }, []);

  const refreshPage = useCallback(async (id: string) => {
    const version = versions.current[id] ?? 0;
    const view = await api.page(id);
    if ((versions.current[id] ?? 0) === version) applyView(view);
    return view;
  }, [applyView]);

  const refreshBootstrap = useCallback(async () => {
    const startedVersions = { ...versions.current };
    const next = await api.bootstrap();
    setBootstrap(previous => {
      if (!previous || previous.workspaceId !== next.workspaceId) return next;
      const previousPages = new Map(previous.pages.map(page => [page.id, page]));
      return { ...next, pages: next.pages.map(page =>
        (versions.current[page.id] ?? 0) !== (startedVersions[page.id] ?? 0)
          ? previousPages.get(page.id) ?? page : page) };
    });
    return next;
  }, []);

  const navigate = useCallback((id: string, origin?: NavigationOrigin, targetMessageId?: string) => {
    const workspaceId = bootstrapRef.current?.workspaceId;
    if (workspaceId) savePosition(workspaceId, routeRef.current.id, document.querySelector('.workspace-main')?.scrollTop ?? 0);
    const state = { origin, targetMessageId, workspaceId };
    if (id !== routeRef.current.id) history.pushState(state, '', pageHref(id));
    else history.replaceState(state, '', pageHref(id));
    setRoute(previous => ({ id, serial: previous.serial + 1, origin, targetMessageId }));
    setError('');
  }, []);

  useEffect(() => {
    const onPop = () => {
      const workspaceId = bootstrapRef.current?.workspaceId;
      if (workspaceId) savePosition(workspaceId, routeRef.current.id, document.querySelector('.workspace-main')?.scrollTop ?? 0);
      const sameWorkspace = workspaceId === history.state?.workspaceId;
      setRoute(previous => ({ id: pageFromLocation(), serial: previous.serial + 1, origin: sameWorkspace ? history.state?.origin : undefined, targetMessageId: sameWorkspace ? history.state?.targetMessageId : undefined }));
      setError('');
    };
    window.addEventListener('popstate', onPop);
    history.scrollRestoration = 'manual';
    return () => { window.removeEventListener('popstate', onPop); history.scrollRestoration = 'auto'; };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    api.bootstrap(controller.signal).then(setBootstrap).catch(problem => {
      if (!controller.signal.aborted) setError(problem instanceof Error ? problem.message : 'The workspace could not be loaded.');
    });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!bootstrap?.workspaceId) return;
    const sameWorkspace = history.state?.workspaceId === bootstrap.workspaceId;
    setRoute(previous => ({ ...previous, origin: sameWorkspace ? history.state?.origin : undefined, targetMessageId: sameWorkspace ? history.state?.targetMessageId : undefined }));
  }, [bootstrap?.workspaceId]);

  useEffect(() => {
    const controller = new AbortController();
    const version = versions.current[route.id] ?? 0;
    setLoading(true);
    api.page(route.id, controller.signal).then(view => {
      if ((versions.current[route.id] ?? 0) === version) applyView(view);
    }).catch(problem => {
      if (!controller.signal.aborted) setError(problem instanceof Error ? problem.message : 'This page could not be loaded.');
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [route.id, route.serial, applyView]);

  const activeView = views[route.id];
  // Background pages and Home returns can change while the current page is
  // idle. Poll the workspace as well as the visible page, with one flight at
  // a time and no requests while this browser document is hidden.
  const shouldPoll = bootstrap?.provider.configured;
  useEffect(() => {
    if (!shouldPoll) return;
    let stopped = false;
    let pending = false;
    const poll = async () => {
      if (stopped || pending || document.visibilityState === 'hidden') return;
      pending = true;
      try { await Promise.allSettled([refreshPage(route.id), refreshBootstrap()]); }
      finally { pending = false; }
    };
    const timer = window.setInterval(poll, 2000);
    document.addEventListener('visibilitychange', poll);
    return () => { stopped = true; window.clearInterval(timer); document.removeEventListener('visibilitychange', poll); };
  }, [route.id, shouldPoll, refreshPage, refreshBootstrap]);

  return { bootstrap, route, view: activeView, loading, error, setError, navigate, applyView, refreshPage, refreshBootstrap };
}
