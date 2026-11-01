import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';
import { messageFromLocation, pageFromLocation, pageHref, readNavigationState, savePosition } from './storage';
import { mergeWorkspaceSnapshot, pageBelongsToWorkspace } from './workspaceSnapshot';
import type { Bootstrap, NavigationOrigin, PageView } from './types';

type Route = { id: string; serial: number; origin?: NavigationOrigin; samePageReturn?: NavigationOrigin; targetMessageId?: string; restoreFocus?: NavigationOrigin; canGoBack: boolean };
export function useWorkspace() {
  const [bootstrap, setBootstrap] = useState<Bootstrap | null>(null);
  const bootstrapRef = useRef<Bootstrap | null>(null);
  bootstrapRef.current = bootstrap;
  const [views, setViews] = useState<Record<string, PageView>>({});
  const [route, setRoute] = useState<Route>(() => ({ id: pageFromLocation(), serial: 0, targetMessageId: messageFromLocation(), canGoBack: false }));
  const [error, setError] = useState('');
  const [syncError, setSyncError] = useState('');
  const [isRefreshing, setIsRefreshing] = useState(false);
  const refreshRequest = useRef<Promise<void> | null>(null);
  const [loadedRouteSerial, setLoadedRouteSerial] = useState<number | null>(null);
  // A new route is loading in the very render that changes it. Waiting for
  // an effect to set a boolean lets source restoration run on the old view.
  const loading = loadedRouteSerial !== route.serial;
  const versions = useRef<Record<string, number>>({});
  const requests = useRef<Record<string, number>>({});
  const bootstrapRequests = useRef(0);
  const routeRef = useRef(route);
  routeRef.current = route;

  const applyView = useCallback((view: PageView) => {
    // An earlier fetch may finish after the local service switched Worlds or
    // restarted. Never mix that page's private messages into another identity.
    const current = bootstrapRef.current;
    if (!pageBelongsToWorkspace(view, current)) {
      if (current) {
        setViews({});
        setSyncError('The workspace restarted or changed. Refresh updates to reconnect.');
      }
      return;
    }
    versions.current[view.page.id] = (versions.current[view.page.id] ?? 0) + 1;
    setViews(previous => ({ ...previous, [view.page.id]: view }));
    setBootstrap(previous => previous && pageBelongsToWorkspace(view, previous)
      ? { ...previous, provider: view.provider, pages: previous.pages.some(page => page.id === view.page.id)
        ? previous.pages.map(page => page.id === view.page.id ? view.page : page)
        : [...previous.pages, view.page] } : previous);
    setSyncError('');
  }, []);

  const refreshPage = useCallback(async (id: string, signal?: AbortSignal) => {
    const version = versions.current[id] ?? 0;
    const request = (requests.current[id] ?? 0) + 1;
    requests.current[id] = request;
    // Preserve the selected source during route changes and subsequent polls.
    const sourceMessageId = id === routeRef.current.id ? routeRef.current.targetMessageId : undefined;
    const view = await api.page(id, signal, sourceMessageId);
    if (!signal?.aborted && requests.current[id] === request && (versions.current[id] ?? 0) === version) applyView(view);
    return view;
  }, [applyView]);

  const refreshBootstrap = useCallback(async (signal?: AbortSignal, expected?: Pick<Bootstrap, 'workspaceId' | 'serviceInstanceId'>) => {
    const request = ++bootstrapRequests.current;
    const before = { ...versions.current };
    const next = await api.bootstrap(signal);
    if (!signal?.aborted && bootstrapRequests.current === request) {
      if (expected && (next.workspaceId !== expected.workspaceId || next.serviceInstanceId !== expected.serviceInstanceId)) {
        throw new Error('The workspace restarted or changed. Refresh updates to reconnect.');
      }
      setBootstrap(previous => mergeWorkspaceSnapshot(previous, next, before, versions.current));
    }
    return next;
  }, []);

  const refreshWorkspace = useCallback(() => {
    if (refreshRequest.current) return refreshRequest.current;
    setIsRefreshing(true);
    const request = Promise.allSettled([refreshBootstrap(), refreshPage(routeRef.current.id)])
      .then(results => {
        const failure = results.find(result => result.status === 'rejected');
        if (failure?.status === 'rejected') throw failure.reason;
        setSyncError('');
      })
      .catch(problem => {
        setSyncError(problem instanceof Error ? problem.message : 'Updates could not be checked. Please try again.');
        throw problem;
      })
      .finally(() => { refreshRequest.current = null; setIsRefreshing(false); });
    refreshRequest.current = request;
    return request;
  }, [refreshBootstrap, refreshPage]);

  const navigate = useCallback((id: string, origin?: NavigationOrigin, targetMessageId?: string) => {
    const workspaceId = bootstrapRef.current?.workspaceId;
    if (workspaceId) savePosition(workspaceId, routeRef.current.id, document.querySelector('.workspace-main')?.scrollTop ?? 0);
    const previous = readNavigationState(history.state, workspaceId, routeRef.current.id);
    const changedPage = id !== routeRef.current.id;
    // The incoming Work and a same-Work source detour are independent
    // return paths. Opening another Activity anchor must not erase either.
    const returnOrigin = changedPage ? origin
      : previous?.origin ?? (origin?.pageId !== id ? origin : undefined);
    const requestedSamePageReturn = !changedPage && origin?.pageId === id ? origin : undefined;
    const samePageReturn = changedPage ? undefined : requestedSamePageReturn
      ?? (targetMessageId && previous?.samePageReturn?.messageId === targetMessageId
        ? undefined : previous?.samePageReturn);
    const canGoBack = changedPage ? origin?.pageId === routeRef.current.id : previous?.canGoBack ?? false;
    const state = { origin: returnOrigin, samePageReturn, targetMessageId, workspaceId, pageId: id, canGoBack };
    if (changedPage) {
      history.replaceState({ ...previous, workspaceId, pageId: routeRef.current.id, restoreFocus: canGoBack ? origin : undefined }, '');
      history.pushState(state, '', pageHref(id, targetMessageId));
    } else history.replaceState(state, '', pageHref(id, targetMessageId));
    setRoute(previous => ({ id, serial: previous.serial + 1, origin: returnOrigin, samePageReturn, targetMessageId, canGoBack }));
    setError('');
  }, []);

  useEffect(() => {
    const onPop = () => {
      const workspaceId = bootstrapRef.current?.workspaceId;
      if (workspaceId) savePosition(workspaceId, routeRef.current.id, document.querySelector('.workspace-main')?.scrollTop ?? 0);
      const id = pageFromLocation();
      const saved = readNavigationState(history.state, workspaceId, id);
      setRoute(previous => ({ id, serial: previous.serial + 1, origin: saved?.origin, samePageReturn: saved?.samePageReturn, targetMessageId: messageFromLocation() ?? saved?.targetMessageId, restoreFocus: saved?.restoreFocus, canGoBack: saved?.canGoBack ?? false }));
      setError('');
    };
    window.addEventListener('popstate', onPop);
    history.scrollRestoration = 'manual';
    return () => { window.removeEventListener('popstate', onPop); history.scrollRestoration = 'auto'; };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    refreshBootstrap(controller.signal).catch(problem => {
      if (!controller.signal.aborted) setError(problem instanceof Error ? problem.message : 'The workspace could not be loaded.');
    });
    return () => controller.abort();
  }, [refreshBootstrap]);

  useEffect(() => {
    if (!bootstrap?.workspaceId) return;
    const saved = readNavigationState(history.state, bootstrap.workspaceId, routeRef.current.id);
    setRoute(previous => ({ ...previous, origin: saved?.origin, samePageReturn: saved?.samePageReturn, targetMessageId: messageFromLocation() ?? saved?.targetMessageId, restoreFocus: saved?.restoreFocus, canGoBack: saved?.canGoBack ?? false }));
  }, [bootstrap?.workspaceId]);

  useEffect(() => {
    // A new bootstrap invalidates cached pages from the previous service.
    // Fetch only after the current World identity is known.
    if (!bootstrap?.workspaceId || !bootstrap?.serviceInstanceId) return;
    const controller = new AbortController();
    refreshPage(route.id, controller.signal).catch(problem => {
      if (!controller.signal.aborted) setError(problem instanceof Error ? problem.message : 'This page could not be loaded.');
    }).finally(() => { if (!controller.signal.aborted) setLoadedRouteSerial(route.serial); });
    return () => controller.abort();
  }, [route.id, route.serial, refreshPage, bootstrap?.workspaceId, bootstrap?.serviceInstanceId]);

  const cachedView = views[route.id];
  const activeView = pageBelongsToWorkspace(cachedView, bootstrap)
    ? { ...cachedView!, provider: bootstrap!.provider } : undefined;
  const shouldPoll = activeView?.provider.configured && ['queued', 'working', 'needs_attention'].includes(activeView.page.status);
  const shouldPollWorkspace = bootstrap?.provider.configured && bootstrap.pages.some(page =>
    page.id !== route.id && ['queued', 'working', 'needs_attention'].includes(page.status));
  useEffect(() => {
    if (!shouldPoll && !shouldPollWorkspace) return;
    let pending = false;
    let cancelled = false;
    const controller = new AbortController();
    const timer = window.setInterval(() => {
      if (pending) return;
      pending = true;
      // Unopened Work still finishes. Observe summaries without loading its
      // conversation, changing the current route, or admitting model work.
      Promise.allSettled([
        ...(shouldPoll ? [refreshPage(route.id, controller.signal)] : []),
        ...(shouldPollWorkspace ? [refreshBootstrap(controller.signal, bootstrapRef.current ?? undefined)] : []),
      ]).then(results => {
        if (cancelled) return;
        const failure = results.find(result => result.status === 'rejected');
        setSyncError(failure?.status === 'rejected'
          ? failure.reason instanceof Error ? failure.reason.message : 'Updates could not be checked. Please try again.'
          : '');
      }).finally(() => { pending = false; });
    }, 2000);
    return () => { cancelled = true; controller.abort(); window.clearInterval(timer); };
  }, [route.id, shouldPoll, shouldPollWorkspace, refreshPage, refreshBootstrap]);

  return { bootstrap, route, view: activeView, loading, error, setError, syncError, isRefreshing, navigate, applyView, refreshPage, refreshBootstrap, refreshWorkspace };
}
