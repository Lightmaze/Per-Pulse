import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError } from './api';
import { pageFromLocation, pageHref, savePosition } from './storage';
import type { Bootstrap, NavigationOrigin, PageView, WorkspaceSettings } from './types';

type Route = { id: string; serial: number; origin?: NavigationOrigin; targetMessageId?: string };
export function useWorkspace() {
  const [bootstrap, setBootstrap] = useState<Bootstrap | null>(null);
  const bootstrapRef = useRef<Bootstrap | null>(null);
  bootstrapRef.current = bootstrap;
  const [views, setViews] = useState<Record<string, PageView>>({});
  const [route, setRoute] = useState<Route>(() => ({ id: pageFromLocation(), serial: 0 }));
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const versions = useRef<Record<string, number>>({});
  const settingsVersion = useRef(0);
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
    const version = settingsVersion.current;
    const next = await api.bootstrap();
    setBootstrap(previous => previous && version !== settingsVersion.current ? { ...next, settings: previous.settings } : next);
    return next;
  }, []);

  const applySettings = useCallback((settings: WorkspaceSettings) => {
    settingsVersion.current += 1;
    setBootstrap(previous => previous ? { ...previous, settings } : previous);
  }, []);

  const refreshSettings = useCallback(async () => {
    const version = settingsVersion.current;
    const next = await api.settings();
    if (version === settingsVersion.current) applySettings(next);
  }, [applySettings]);

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
      if (!controller.signal.aborted) setError(problem instanceof Error ? problem : new ApiError('The workspace could not be loaded.', 'workspace_load_failed', 0));
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
      if (!controller.signal.aborted) setError(problem instanceof Error ? problem : new ApiError('This page could not be loaded.', 'page_load_failed', 0));
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [route.id, route.serial, applyView]);

  const activeView = views[route.id];
  const shouldPoll = activeView?.provider.configured && ['queued', 'working'].includes(activeView.page.status);
  useEffect(() => {
    if (!shouldPoll) return;
    const timer = window.setInterval(() => { refreshPage(route.id).catch(() => { /* A temporary polling failure does not replace saved work. */ }); }, 2000);
    return () => window.clearInterval(timer);
  }, [route.id, shouldPoll, refreshPage]);

  return { bootstrap, route, view: activeView, loading, error, setError, navigate, applyView, refreshPage, refreshBootstrap, applySettings, refreshSettings };
}
