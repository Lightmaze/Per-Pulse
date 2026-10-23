import { useEffect, useId, useRef, type ReactNode } from 'react';
import { Icon } from './Icon';

export function Dialog({ title, children, onClose, className = '', initialFocus }: { title: string; children: ReactNode; onClose: () => void; className?: string; initialFocus?: string }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const element = dialog.current;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    element?.showModal();
    const target = initialFocus ? element?.querySelector<HTMLElement>(initialFocus) :
      element?.querySelector<HTMLElement>('[autofocus]') ?? element?.querySelector<HTMLElement>('input:not([type="hidden"]):not([disabled]), textarea:not([disabled]), select:not([disabled])');
    target?.focus({ preventScroll: true });
    return () => {
      const restoreFocus = document.activeElement === document.body || Boolean(element?.contains(document.activeElement));
      element?.close();
      if (restoreFocus && previousFocus?.isConnected) previousFocus.focus({ preventScroll: true });
    };
  }, [initialFocus]);
  return <dialog ref={dialog} className={`dialog ${className}`} aria-labelledby={titleId} onCancel={event => { event.preventDefault(); onClose(); }} onClick={event => { if (event.target === dialog.current) { const bounds = dialog.current.getBoundingClientRect(); if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) onClose(); } }}>
    <div className="dialog-heading"><h2 id={titleId}>{title}</h2><button className="icon-button" aria-label="Close dialog" onClick={onClose}><Icon name="close" /></button></div>
    {children}
  </dialog>;
}
