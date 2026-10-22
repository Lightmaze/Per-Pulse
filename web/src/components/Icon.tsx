import type { CSSProperties, ReactNode } from 'react';

type IconName = 'pulse' | 'home' | 'search' | 'page' | 'menu' | 'close' | 'link' | 'send' | 'settings' | 'plus' | 'chevron' | 'back' | 'return' | 'check' | 'edit';
const paths: Record<IconName, ReactNode> = {
  pulse: <path d="M2 13h4l3-10 4 19 3-13 2 4h4" />,
  home: <><path d="m3 10 9-7 9 7v11H3Z" /><path d="M9 21v-8h6v8" /></>,
  search: <><circle cx="10.5" cy="10.5" r="7.5" /><path d="m16 16 5 5" /></>,
  page: <><path d="M6 2.5h8l5 5V21H6Z" /><path d="M14 2.5v5h5" /></>,
  menu: <path d="M5 6h14M5 12h14M5 18h14" />,
  close: <path d="m6 6 12 12M6 18 18 6" />,
  link: <><path d="m10 7 2-2a5 5 0 0 1 7 7l-2 2M14 17l-2 2a5 5 0 0 1-7-7l2-2" /><path d="m8 16 8-8" /></>,
  send: <path d="M12 20V4m-7 7 7-7 7 7" />,
  settings: <><path d="m10 2-.6 3-2 .9-2.7-.9L2 9l2.3 2v2L2 15l2.7 4 2.7-.9 2 .9.6 3h4l.6-3 2-.9 2.7.9L22 15l-2.3-2v-2L22 9l-2.7-4-2.7.9-2-.9L14 2Z" /><circle cx="12" cy="12.5" r="3" /></>,
  plus: <path d="M12 5v14M5 12h14" />,
  chevron: <path d="m9 5 7 7-7 7" />,
  back: <path d="M20 12H4m7-7-7 7 7 7" />,
  return: <><path d="M20 5v7a4 4 0 0 1-4 4H4" /><path d="m9 11-5 5 5 5" /></>,
  check: <path d="m5 12 4 4L19 6" />,
  edit: <><path d="m15 4 5 5-11 11H4v-5Z" /><path d="m12 7 5 5" /></>,
};

export function Icon({ name, size = 20, style, className = '' }: { name: IconName; size?: number; style?: CSSProperties; className?: string }) {
  return <svg className={`icon ${className}`} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" style={style} aria-hidden="true">{paths[name]}</svg>;
}
