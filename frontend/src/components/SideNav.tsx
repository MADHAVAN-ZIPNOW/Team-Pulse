import { useState, type ReactNode } from "react";

export type PageId = "assistant" | "trends" | "meetings" | "radar";

const ICON_PROPS = { width: 20, height: 20, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true } as const;

const ITEMS: { id: PageId; label: string; hint: string; icon: ReactNode }[] = [
  {
    id: "assistant", label: "PR Assistant", hint: "Ask Claude about Azure DevOps",
    icon: <svg {...ICON_PROPS}><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12Z" /></svg>,
  },
  {
    id: "meetings", label: "Meetings", hint: "Claude listens and creates work items",
    icon: <svg {...ICON_PROPS}><path d="M17 20v-2a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v2" /><circle cx="10" cy="7" r="3.5" /><path d="M21 20v-2a4 4 0 0 0-3-3.9M15 3.6a3.5 3.5 0 0 1 0 6.8" /></svg>,
  },
  {
    id: "radar", label: "Problem Radar", hint: "Today's real tech problems and fixes",
    icon: <svg {...ICON_PROPS}><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="4.5" /><path d="M12 12l6-6" /></svg>,
  },
  {
    id: "trends", label: "Google Trends", hint: "What the world is searching right now",
    icon: <svg {...ICON_PROPS}><path d="M3 17l6-6 4 4 8-8" /><path d="M15 7h6v6" /></svg>,
  },
];

/** Microsoft-style left navigation: icon rail that expands to show names. */
export function SideNav({ page, onSelect }: { page: PageId; onSelect: (p: PageId) => void }) {
  const [open, setOpen] = useState(() => {
    try { return localStorage.getItem("pra_nav_open") !== "0"; } catch { return true; }
  });
  const toggle = () => setOpen((o) => {
    try { localStorage.setItem("pra_nav_open", o ? "0" : "1"); } catch { /* storage may be blocked */ }
    return !o;
  });
  return (
    <nav aria-label="Apps" className={`${open ? "w-56" : "w-12"} flex shrink-0 flex-col border-r border-slate-800 bg-white transition-[width] duration-150`}>
      <button
        onClick={toggle}
        aria-label={open ? "Collapse navigation" : "Expand navigation"}
        aria-expanded={open}
        className="flex h-12 w-12 items-center justify-center text-slate-300 hover:bg-slate-950"
      >
        <svg {...ICON_PROPS}><path d="M4 6h16M4 12h16M4 18h16" /></svg>
      </button>
      <ul className="flex flex-col">
        {ITEMS.map((it) => {
          const active = page === it.id;
          return (
            <li key={it.id}>
              <button
                onClick={() => onSelect(it.id)}
                title={open ? it.hint : it.label}
                aria-current={active ? "page" : undefined}
                className={`relative flex h-11 w-full items-center gap-3 pl-3.5 pr-3 text-left text-sm ${
                  active ? "bg-sky-950 font-semibold text-sky-600" : "text-slate-300 hover:bg-slate-950"
                }`}
              >
                {active && <span className="absolute inset-y-2 left-0 w-[3px] rounded-r bg-sky-600" aria-hidden />}
                <span className="shrink-0">{it.icon}</span>
                {open && <span className="truncate">{it.label}</span>}
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
