const shapes = {
  chart: <><path d="M4 3v17h17M8 15v-4m5 4V6m5 9V9"/></>,
  settings: <><path d="M3 7h10m4 0h4M3 17h3m4 0h11"/><circle cx="15" cy="7" r="2"/><circle cx="8" cy="17" r="2"/></>,
  logout: <><path d="M10 4H4v16h6m-1-8h12m-4-4 4 4-4 4"/></>,
  chevron: <path d="m6 9 6 6 6-6"/>,
  user: <><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></>,
  layers: <><path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5M3 16l9 5 9-5"/></>,
  messages: <><path d="M21 11a7 7 0 0 1-7 7H9l-4 3v-4a7 7 0 0 1-2-5V9a6 6 0 0 1 6-6h6a6 6 0 0 1 6 6Z"/><path d="M8 10h8m-8 4h5"/></>,
  files: <><path d="M14 2H6a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V6Z"/><path d="M14 2v5h4M8 11h6m-6 4h6m7-5v10a2 2 0 0 1-2 2H8"/></>,
  plus: <><path d="M12 5v14M5 12h14"/></>,
  sparkles: <><path d="M12 2 9.6 8.6 3 11l6.6 2.4L12 20l2.4-6.6L21 11l-6.6-2.4L12 2ZM5 3v5M2.5 5.5h5M19 16v5m-2.5-2.5h5"/></>,
  book: <><path d="M12 6C9 4 5 4 3 5v14c3-1 6-1 9 1 3-2 6-2 9-1V5c-2-1-6-1-9 1Zm0 0v14"/></>,
  search: <><circle cx="11" cy="11" r="6"/><path d="m16 16 4 4"/></>,
  copy: <><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 4H6a2 2 0 0 0-2 2v10"/></>,
  more: <><circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></>,
  menu: <><path d="M4 6h16M4 12h16M4 18h16"/></>,
  upload: <><path d="M12 16V3m-5 5 5-5 5 5M4 15v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4"/></>,
  check: <><path d="m5 12 4 4L19 6"/></>,
  clock: <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
}

export function Icon({ name }: { name: keyof typeof shapes }) {
  return <span data-icon={name} aria-hidden="true">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      {shapes[name]}
    </svg>
  </span>
}
